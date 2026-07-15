"""Retrieval tools for SignalPulse AI (the "read" path over Neo4j).

Three tools the agentic RAG chatbot (Step 8) will call:

* ``vector_search``   — semantic similarity over chunk embeddings
* ``fulltext_search`` — keyword / Lucene search over chunk text
* ``graph_search``    — entity + relationship lookup, with provenance chunks

Every hit carries **evidence** (passage text) and **source metadata**
(document title, agency, domain, URL) so answers can be cited.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from signalpulse.config import settings
from signalpulse.graph import run_query
from signalpulse.processing import embed_query


# ---------------------------------------------------------------------------
# Shared result shape
# ---------------------------------------------------------------------------


@dataclass
class Evidence:
    """One retrieved passage plus the document it came from (for citations)."""

    chunk_id: str
    text: str
    score: float
    document_id: str
    title: str
    agency: str
    domain: str
    url: str
    tool: str = ""  # which retrieval tool produced this hit

    def preview(self, n: int = 280) -> str:
        snippet = " ".join(self.text.split())
        if len(snippet) > n:
            snippet = snippet[:n] + "..."
        return (
            f"[{self.tool}] score={self.score:.3f}\n"
            f"  agency: {self.agency}\n"
            f"  title: {self.title[:80]}\n"
            f"  cite: {self.url}\n"
            f"  text: {snippet}"
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GraphHit:
    """Result of an entity/graph lookup: the entity, its neighbors, and evidence."""

    entity: str
    entity_type: str
    relationships: list[dict[str, str]] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)

    def preview(self) -> str:
        lines = [f"Entity: {self.entity} [{self.entity_type}]"]
        for rel in self.relationships[:8]:
            lines.append(
                f"  ({rel['source']}) -{rel['relation']}-> ({rel['target']})"
            )
        lines.append(f"  provenance chunks: {len(self.evidence)}")
        for ev in self.evidence[:3]:
            lines.append("  " + ev.preview(120).replace("\n", "\n  "))
        return "\n".join(lines)


def _rows_to_evidence(rows: list[dict[str, Any]], tool: str) -> list[Evidence]:
    out: list[Evidence] = []
    for r in rows:
        out.append(
            Evidence(
                chunk_id=r.get("chunk_id") or "",
                text=r.get("text") or "",
                score=float(r.get("score") or 0.0),
                document_id=r.get("document_id") or "",
                title=r.get("title") or "",
                agency=r.get("agency") or "",
                domain=r.get("domain") or "",
                url=r.get("url") or "",
                tool=tool,
            )
        )
    return out


# ---------------------------------------------------------------------------
# 1. Vector (semantic) search
# ---------------------------------------------------------------------------


def vector_search(
    query: str,
    *,
    top_k: int | None = None,
    threshold: float | None = None,
    domain: str | None = None,
) -> list[Evidence]:
    """Find chunks whose *meaning* is closest to the query.

    Embeds the query with the same local model used at ingest time, then
    queries the ``chunk_embedding`` cosine vector index in Neo4j.
    """
    k = top_k or settings.VECTOR_TOP_K
    thr = settings.SIMILARITY_THRESHOLD if threshold is None else threshold
    embedding = embed_query(query)

    cypher = """
    CALL db.index.vector.queryNodes('chunk_embedding', $k, $embedding)
    YIELD node AS c, score
    WHERE score >= $threshold
    MATCH (d:Document)-[:HAS_CHUNK]->(c)
    """
    if domain:
        cypher += "\n    WHERE d.domain = $domain"
    cypher += """
    RETURN c.id AS chunk_id, c.text AS text, score,
           d.id AS document_id, d.title AS title, d.agency AS agency,
           d.domain AS domain, d.url AS url
    ORDER BY score DESC
    """
    rows = run_query(
        cypher,
        {"k": k, "embedding": embedding, "threshold": thr, "domain": domain},
    )
    return _rows_to_evidence(rows, tool="vector_search")


# ---------------------------------------------------------------------------
# 2. Full-text (keyword) search
# ---------------------------------------------------------------------------


def _fulltext_query(query: str) -> str:
    """Turn an identifier-like query into a stricter Lucene expression.

    Lucene splits on hyphens, so ``CVE-2026-56291`` would otherwise match any
    chunk mentioning ``CVE`` or ``2026``. Joining tokens with AND keeps
    keyword search precise for CVE / docket / control ids.
    """
    q = query.strip()
    if not q:
        return q
    if " " in q or ":" in q or '"' in q:
        return q  # caller already wrote a Lucene expression
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", q) if p]
    if len(parts) >= 2:
        return " AND ".join(parts)
    return q


def fulltext_search(
    query: str,
    *,
    top_k: int | None = None,
    domain: str | None = None,
) -> list[Evidence]:
    """Find chunks that contain the given keywords (Lucene full-text index).

    Best for exact identifiers (CVE ids, docket numbers, agency acronyms).
    Also matches ``Document.id`` / ``Document.title`` so identifiers that live
    in metadata (but not always in chunk prose) still resolve.
    """
    k = top_k or settings.VECTOR_TOP_K
    lucene_q = _fulltext_query(query)

    cypher = """
    CALL db.index.fulltext.queryNodes('chunk_fulltext', $query)
    YIELD node AS c, score
    MATCH (d:Document)-[:HAS_CHUNK]->(c)
    """
    if domain:
        cypher += "\n    WHERE d.domain = $domain"
    cypher += """
    RETURN c.id AS chunk_id, c.text AS text, score,
           d.id AS document_id, d.title AS title, d.agency AS agency,
           d.domain AS domain, d.url AS url
    ORDER BY score DESC
    LIMIT $k
    """
    rows = run_query(cypher, {"query": lucene_q, "k": k, "domain": domain})
    hits = _rows_to_evidence(rows, tool="fulltext_search")
    seen = {h.chunk_id for h in hits}

    # Metadata fallback: document id / title contains the raw query string.
    meta = """
    MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)
    WHERE toLower(d.id) CONTAINS toLower($q)
       OR toLower(d.title) CONTAINS toLower($q)
    """
    if domain:
        meta += "\n    AND d.domain = $domain"
    meta += """
    RETURN c.id AS chunk_id, c.text AS text, 1.5 AS score,
           d.id AS document_id, d.title AS title, d.agency AS agency,
           d.domain AS domain, d.url AS url
    LIMIT $k
    """
    for ev in _rows_to_evidence(
        run_query(meta, {"q": query.strip(), "k": k, "domain": domain}),
        tool="fulltext_search",
    ):
        if ev.chunk_id not in seen:
            hits.append(ev)
            seen.add(ev.chunk_id)

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:k]


# ---------------------------------------------------------------------------
# 3. Graph (entity / relationship) search
# ---------------------------------------------------------------------------


def graph_search(
    entity_name: str,
    *,
    top_k: int | None = None,
) -> list[GraphHit]:
    """Look up entities by name and return their graph neighborhood + evidence.

    Matches entities whose name contains ``entity_name`` (case-insensitive),
    collects ``RELATED_TO`` neighbors, and pulls the chunks (with source docs)
    that ``MENTIONS`` each matched entity — the citation trail.
    """
    k = top_k or settings.VECTOR_TOP_K
    # Find matching entities first.
    entities = run_query(
        """
        MATCH (e:Entity)
        WHERE toLower(e.name) CONTAINS toLower($name)
        RETURN e.name AS name, e.type AS type
        ORDER BY size(e.name) ASC
        LIMIT $k
        """,
        {"name": entity_name, "k": k},
    )
    hits: list[GraphHit] = []
    for ent in entities:
        name = ent["name"]
        relationships = [
            {"source": r["source"], "relation": r["relation"], "target": r["target"]}
            for r in run_query(
                """
                MATCH (a:Entity {name: $name})-[r:RELATED_TO]->(b:Entity)
                RETURN a.name AS source, r.type AS relation, b.name AS target
                UNION
                MATCH (a:Entity)-[r:RELATED_TO]->(b:Entity {name: $name})
                RETURN a.name AS source, r.type AS relation, b.name AS target
                """,
                {"name": name},
            )
        ]

        evidence_rows = run_query(
            """
            MATCH (c:Chunk)-[:MENTIONS]->(e:Entity {name: $name})
            MATCH (d:Document)-[:HAS_CHUNK]->(c)
            RETURN c.id AS chunk_id, c.text AS text, 1.0 AS score,
                   d.id AS document_id, d.title AS title, d.agency AS agency,
                   d.domain AS domain, d.url AS url
            LIMIT $k
            """,
            {"name": name, "k": k},
        )
        hits.append(
            GraphHit(
                entity=name,
                entity_type=ent.get("type") or "Other",
                relationships=relationships,
                evidence=_rows_to_evidence(evidence_rows, tool="graph_search"),
            )
        )
    return hits


# ---------------------------------------------------------------------------
# Convenience: format for the agent / notebooks
# ---------------------------------------------------------------------------


def format_evidence(hits: list[Evidence]) -> str:
    """Human-readable dump of evidence list (for notebooks / debugging)."""
    if not hits:
        return "(no results)"
    return "\n\n".join(h.preview() for h in hits)
