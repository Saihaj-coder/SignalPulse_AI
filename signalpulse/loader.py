"""Idempotent Neo4j loader for SignalPulse AI (the "Load" stage).

Takes the outputs of the earlier stages and writes them into the graph:

    RawDocument            -> (:Document)
    Chunk (+ embedding)    -> (:Chunk)                 (:Document)-[:HAS_CHUNK]->(:Chunk)
    Extraction.entities    -> (:Entity)                (:Chunk)-[:MENTIONS]->(:Entity)
    Extraction.relations   -> (:Entity)-[:RELATED_TO {type}]->(:Entity)

Idempotency
-----------
Every write uses ``MERGE`` on the unique keys defined by our constraints
(``Document.id``, ``Chunk.id``, ``Entity.name``). ``MERGE`` = "match if it
exists, otherwise create". Running the pipeline twice therefore updates existing
nodes/relationships in place instead of creating duplicates — essential for a
scheduled pipeline that re-sees the same documents.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from signalpulse.graph import run_query

if TYPE_CHECKING:
    from signalpulse.connectors import RawDocument
    from signalpulse.extraction import Extraction
    from signalpulse.processing import Chunk


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

_UPSERT_DOCUMENT = """
MERGE (d:Document {id: $id})
SET d.title          = $title,
    d.url            = $url,
    d.agency         = $agency,
    d.domain         = $domain,
    d.source_format  = $source_format,
    d.connector      = $connector,
    d.published_date = $published_date,
    d.content_hash   = $content_hash,
    d.updated_at     = timestamp()
"""


def upsert_document(doc: "RawDocument") -> None:
    """Create or update a Document node (keyed on its stable source id)."""
    run_query(
        _UPSERT_DOCUMENT,
        {
            "id": doc.source_id,
            "title": doc.title,
            "url": doc.url,
            "agency": doc.agency,
            "domain": doc.domain,
            "source_format": doc.source_format,
            "connector": doc.connector,
            "published_date": doc.published_date,
            "content_hash": doc.content_hash,
        },
    )


# ---------------------------------------------------------------------------
# Chunks (+ embeddings) and their link to the document
# ---------------------------------------------------------------------------

_UPSERT_CHUNKS = """
MATCH (d:Document {id: $doc_id})
UNWIND $chunks AS ch
MERGE (c:Chunk {id: ch.id})
SET c.text        = ch.text,
    c.chunk_index = ch.chunk_index,
    c.document_id = $doc_id,
    c.embedding   = ch.embedding
MERGE (d)-[:HAS_CHUNK]->(c)
"""


def upsert_chunks(doc_id: str, chunks: list["Chunk"]) -> None:
    """Create/update Chunk nodes with embeddings and link them to the document."""
    if not chunks:
        return
    payload = [
        {
            "id": c.id,
            "text": c.text,
            "chunk_index": c.chunk_index,
            "embedding": c.embedding,
        }
        for c in chunks
    ]
    run_query(_UPSERT_CHUNKS, {"doc_id": doc_id, "chunks": payload})


# ---------------------------------------------------------------------------
# Entities + MENTIONS (chunk -> entity)
# ---------------------------------------------------------------------------

_UPSERT_MENTIONS = """
UNWIND $rows AS row
MATCH (c:Chunk {id: row.chunk_id})
UNWIND row.entities AS ent
MERGE (e:Entity {name: ent.name})
ON CREATE SET e.type = ent.type
SET e.type = coalesce(e.type, ent.type)
MERGE (c)-[:MENTIONS]->(e)
"""


def upsert_mentions(rows: list[dict[str, Any]]) -> None:
    """Upsert entities and link the chunks that mention them.

    ``rows`` = ``[{"chunk_id": str, "entities": [{"name": str, "type": str}]}]``.
    Chunks with no entities contribute nothing (harmless).
    """
    rows = [r for r in rows if r.get("entities")]
    if not rows:
        return
    run_query(_UPSERT_MENTIONS, {"rows": rows})


# ---------------------------------------------------------------------------
# Entity -> Entity relationships
# ---------------------------------------------------------------------------

_UPSERT_RELATIONSHIPS = """
UNWIND $rels AS rel
MERGE (a:Entity {name: rel.source})
MERGE (b:Entity {name: rel.target})
MERGE (a)-[r:RELATED_TO {type: rel.relation}]->(b)
"""


def upsert_relationships(rels: list[dict[str, str]]) -> None:
    """Upsert typed (:Entity)-[:RELATED_TO {type}]->(:Entity) relationships."""
    if not rels:
        return
    run_query(_UPSERT_RELATIONSHIPS, {"rels": rels})


# ---------------------------------------------------------------------------
# Incremental helpers
# ---------------------------------------------------------------------------


def existing_content_hash(doc_id: str) -> str | None:
    """Return the stored content_hash for a document, or None if not present.

    Used by the pipeline to skip documents whose content hasn't changed.
    """
    rows = run_query(
        "MATCH (d:Document {id: $id}) RETURN d.content_hash AS h",
        {"id": doc_id},
    )
    return rows[0]["h"] if rows else None


def delete_document_chunks(doc_id: str) -> None:
    """Delete a document's existing chunks (and their MENTIONS edges).

    Called before re-loading a *changed* document so stale chunks don't linger.
    Entity nodes are shared and intentionally left in place.
    """
    run_query(
        "MATCH (:Document {id: $id})-[:HAS_CHUNK]->(c:Chunk) DETACH DELETE c",
        {"id": doc_id},
    )


# ---------------------------------------------------------------------------
# High-level: load one document end-to-end
# ---------------------------------------------------------------------------


def load_document(
    doc: "RawDocument",
    chunks: list["Chunk"],
    extractions: list["Extraction"],
) -> dict[str, int]:
    """Load a document, its chunks, and their extracted knowledge into Neo4j.

    ``extractions[i]`` corresponds to ``chunks[i]``. Fully idempotent.
    Returns a small summary of how much was written for this document.
    """
    if len(chunks) != len(extractions):
        raise ValueError("chunks and extractions must be the same length")

    upsert_document(doc)
    upsert_chunks(doc.source_id, chunks)

    rows: list[dict[str, Any]] = []
    rels: list[dict[str, str]] = []
    n_entities = n_rels = 0
    for chunk, ex in zip(chunks, extractions):
        entities = [{"name": e.name, "type": e.type} for e in ex.entities]
        rows.append({"chunk_id": chunk.id, "entities": entities})
        n_entities += len(entities)
        for r in ex.relationships:
            rels.append({"source": r.source, "relation": r.relation, "target": r.target})
        n_rels += len(ex.relationships)

    upsert_mentions(rows)
    upsert_relationships(rels)

    return {"chunks": len(chunks), "entity_mentions": n_entities, "relationships": n_rels}


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def graph_summary() -> dict[str, int]:
    """Return node counts per label and relationship counts per type."""
    summary: dict[str, int] = {}
    for label in ("Document", "Chunk", "Entity"):
        rows = run_query(f"MATCH (n:{label}) RETURN count(n) AS c")
        summary[f"{label} nodes"] = rows[0]["c"] if rows else 0
    for rel in ("HAS_CHUNK", "MENTIONS", "RELATED_TO"):
        rows = run_query(f"MATCH ()-[r:{rel}]->() RETURN count(r) AS c")
        summary[f"{rel} rels"] = rows[0]["c"] if rows else 0
    return summary
