"""Dashboard / corpus aggregations for the SignalPulse web console.

Reads Neo4j + last_ingest.json so the UI can show real ingested data —
not placeholder marketing metrics.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from signalpulse.ui_helpers import load_last_ingest


def _safe_connectivity() -> bool:
    try:
        from signalpulse import graph

        return bool(graph.verify_connectivity())
    except Exception:  # noqa: BLE001
        return False


def _query(cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    from signalpulse import graph

    return graph.run_query(cypher, params or {})


def focus_agency_predicate(focus: str) -> str:
    """Cypher boolean expression over Document alias ``d``."""
    mapping = {
        "cyber": (
            "toLower(coalesce(d.agency,'')) CONTAINS 'cisa' OR "
            "toLower(coalesce(d.agency,'')) CONTAINS 'nvd' OR "
            "toLower(coalesce(d.domain,'')) CONTAINS 'cyber'"
        ),
        "nist": "toLower(coalesce(d.agency,'')) CONTAINS 'nist' AND NOT "
        "toLower(coalesce(d.agency,'')) CONTAINS 'nvd'",
        "health": (
            "toLower(coalesce(d.agency,'')) CONTAINS 'health' OR "
            "toLower(coalesce(d.agency,'')) CONTAINS 'hhs' OR "
            "toLower(coalesce(d.agency,'')) CONTAINS 'cms' OR "
            "toLower(coalesce(d.agency,'')) CONTAINS 'onc' OR "
            "toLower(coalesce(d.agency,'')) CONTAINS 'fda' OR "
            "toLower(coalesce(d.domain,'')) CONTAINS 'health'"
        ),
        "state": (
            "toLower(coalesce(d.agency,'')) CONTAINS 'nascio' OR "
            "toLower(coalesce(d.domain,'')) CONTAINS 'state'"
        ),
        "defense": (
            "toLower(coalesce(d.agency,'')) CONTAINS 'defense' OR "
            "toLower(coalesce(d.agency,'')) CONTAINS 'dod' OR "
            "toLower(coalesce(d.agency,'')) CONTAINS 'omb'"
        ),
    }
    return mapping.get(focus, "true")


def corpus_overview() -> dict[str, Any]:
    """KPIs + charts + last ingest for the Intelligence Hub."""
    online = _safe_connectivity()
    empty = {
        "online": online,
        "kpis": {
            "documents": 0,
            "chunks": 0,
            "entities": 0,
            "mentions": 0,
            "relationships": 0,
            "feeds": 0,
        },
        "by_agency": [],
        "by_domain": [],
        "entity_types": [],
        "rel_types": [],
        "ingest": None,
        "sync_label": "Corpus offline",
    }
    if not online:
        return empty

    from signalpulse import loader as L

    summary = L.graph_summary()
    stamp = load_last_ingest()

    by_agency = _query(
        """
        MATCH (d:Document)
        RETURN coalesce(d.agency, '(unknown)') AS label, count(*) AS value
        ORDER BY value DESC
        LIMIT 12
        """
    )
    by_domain = _query(
        """
        MATCH (d:Document)
        RETURN coalesce(d.domain, '(unknown)') AS label, count(*) AS value
        ORDER BY value DESC
        """
    )
    entity_types = _query(
        """
        MATCH (e:Entity)
        RETURN coalesce(e.type, '(unknown)') AS label, count(*) AS value
        ORDER BY value DESC
        LIMIT 10
        """
    )
    rel_types = _query(
        """
        MATCH ()-[r:RELATED_TO]->()
        RETURN coalesce(r.type, 'RELATED_TO') AS label, count(*) AS value
        ORDER BY value DESC
        LIMIT 10
        """
    )

    feeds = len((stamp or {}).get("sources") or [])
    sync_label = _sync_label(stamp, int(summary.get("Document nodes", 0)))

    return {
        "online": True,
        "kpis": {
            "documents": int(summary.get("Document nodes", 0)),
            "chunks": int(summary.get("Chunk nodes", 0)),
            "entities": int(summary.get("Entity nodes", 0)),
            "mentions": int(summary.get("MENTIONS rels", 0)),
            "relationships": int(summary.get("RELATED_TO rels", 0)),
            "feeds": feeds,
        },
        "by_agency": by_agency,
        "by_domain": by_domain,
        "entity_types": entity_types,
        "rel_types": rel_types,
        "ingest": _normalize_ingest(stamp),
        "sync_label": sync_label,
    }


def list_documents(
    *,
    focus: str = "all",
    q: str = "",
    limit: int = 80,
) -> list[dict[str, Any]]:
    if not _safe_connectivity():
        return []

    pred = focus_agency_predicate(focus)
    where_parts = [f"({pred})"]
    params: dict[str, Any] = {"limit": limit}
    if q.strip():
        where_parts.append(
            "(toLower(coalesce(d.title,'')) CONTAINS toLower($q) OR "
            "toLower(coalesce(d.agency,'')) CONTAINS toLower($q))"
        )
        params["q"] = q.strip()
    where = " AND ".join(where_parts)

    rows = _query(
        f"""
        MATCH (d:Document)
        WHERE {where}
        OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c)
        RETURN d.id AS id,
               d.title AS title,
               d.agency AS agency,
               d.domain AS domain,
               d.url AS url,
               d.published_date AS published,
               count(c) AS chunks
        ORDER BY coalesce(d.published_date, '') DESC, d.title
        LIMIT $limit
        """,
        params,
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        url = r.get("url") or ""
        out.append(
            {
                "id": r.get("id") or "",
                "title": (r.get("title") or "Untitled").replace("\n", " ").strip(),
                "agency": r.get("agency") or "—",
                "domain": r.get("domain") or "—",
                "url": url,
                "host": urlparse(url).netloc if url else "",
                "published": _pretty_date(r.get("published")),
                "chunks": int(r.get("chunks") or 0),
            }
        )
    return out


def source_catalog() -> list[dict[str, Any]]:
    """Known connectors with live doc counts when possible."""
    catalog = [
        {
            "id": "cisa_kev",
            "name": "CISA Known Exploited Vulnerabilities",
            "tier": 1,
            "format": "JSON API",
            "domain": "Cybersecurity & Defense",
            "match": "cisa",
        },
        {
            "id": "nvd",
            "name": "NIST NVD (CVEs)",
            "tier": 1,
            "format": "JSON API",
            "domain": "Cybersecurity & Defense",
            "match": "nvd",
        },
        {
            "id": "fr_cms",
            "name": "Federal Register — CMS",
            "tier": 1,
            "format": "JSON API",
            "domain": "Health IT & Civilian",
            "match": "health and human",
        },
        {
            "id": "fr_dod",
            "name": "Federal Register — Defense",
            "tier": 1,
            "format": "JSON API",
            "domain": "Cybersecurity & Defense",
            "match": "defense",
        },
        {
            "id": "fr_hhs_onc",
            "name": "Federal Register — HHS / ONC",
            "tier": 1,
            "format": "JSON API",
            "domain": "Health IT & Civilian",
            "match": "health",
        },
        {
            "id": "nist_news",
            "name": "NIST News",
            "tier": 2,
            "format": "RSS",
            "domain": "Tech Standards & Safety",
            "match": "nist",
        },
        {
            "id": "nist_csf",
            "name": "NIST Cybersecurity Framework",
            "tier": 3,
            "format": "HTML/PDF",
            "domain": "Tech Standards & Safety",
            "match": "nist",
        },
        {
            "id": "nist_80053_oscal",
            "name": "NIST SP 800-53 (OSCAL)",
            "tier": 3,
            "format": "JSON/OSCAL",
            "domain": "Tech Standards & Safety",
            "match": "nist",
        },
        {
            "id": "nist_rmf",
            "name": "NIST Risk Management Framework",
            "tier": 3,
            "format": "HTML",
            "domain": "Tech Standards & Safety",
            "match": "nist",
        },
        {
            "id": "healthit_newsroom",
            "name": "HealthIT.gov / ONC newsroom",
            "tier": 2,
            "format": "HTML",
            "domain": "Health IT & Civilian",
            "match": "onc",
        },
        {
            "id": "nascio_priorities",
            "name": "NASCIO State CIO Priorities",
            "tier": 3,
            "format": "PDF / seed",
            "domain": "State & Local Gov",
            "match": "nascio",
        },
        {
            "id": "agency_glossary",
            "name": "Agency acronym glossary (CMS/ONC/NIST/…)",
            "tier": 3,
            "format": "seed text",
            "domain": "Reference",
            "match": "signalpulse",
        },
    ]

    stamp = load_last_ingest()
    active = set((stamp or {}).get("sources") or [])
    agency_counts: dict[str, int] = {}
    if _safe_connectivity():
        for row in _query(
            """
            MATCH (d:Document)
            RETURN toLower(coalesce(d.agency,'')) AS agency, count(*) AS n
            """
        ):
            agency_counts[row["agency"]] = int(row["n"])

    out = []
    for item in catalog:
        needle = item["match"]
        docs = sum(n for a, n in agency_counts.items() if needle in a)
        out.append(
            {
                "id": item["id"],
                "name": item["name"],
                "tier": item["tier"],
                "format": item["format"],
                "domain": item["domain"],
                "in_last_ingest": item["id"] in active,
                "approx_docs": docs,
            }
        )
    return out


def pipeline_story() -> dict[str, Any]:
    stamp = load_last_ingest()
    return {
        "stages": [
            {
                "n": 1,
                "name": "Extract",
                "detail": "Tiered connectors: JSON APIs → RSS → HTML/PDF scrape",
            },
            {
                "n": 2,
                "name": "Clean",
                "detail": "Strip chrome; keep body text (trafilatura / PDF tools)",
            },
            {
                "n": 3,
                "name": "Deduplicate",
                "detail": "Content-hash skip so scheduled runs stay incremental",
            },
            {
                "n": 4,
                "name": "Chunk",
                "detail": "Deterministic overlapping passages for retrieval",
            },
            {
                "n": 5,
                "name": "Embed",
                "detail": "Local BGE embeddings — vectors never leave the machine",
            },
            {
                "n": 6,
                "name": "Extract graph",
                "detail": "LLM summaries, entities, typed relationships",
            },
            {
                "n": 7,
                "name": "Load Neo4j",
                "detail": "Document / Chunk / Entity + vector & fulltext indexes",
            },
            {
                "n": 8,
                "name": "Schedule",
                "detail": "On-demand now; Windows Task Scheduler for recurring runs",
            },
        ],
        "ingest": _normalize_ingest(stamp),
        "command": ".\\run_demo_ingest.ps1",
    }


def _normalize_ingest(stamp: dict[str, Any] | None) -> dict[str, Any] | None:
    if not stamp:
        return None
    finished = stamp.get("finished_at", "")
    try:
        dt = datetime.fromisoformat(finished.replace("Z", "+00:00"))
        finished_fmt = dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:  # noqa: BLE001
        finished_fmt = finished or "unknown"
    report = stamp.get("report") or {}
    graph = stamp.get("graph") or {}
    return {
        "finished_at": finished_fmt,
        "profile": stamp.get("profile", "?"),
        "sources": stamp.get("sources") or [],
        "report": {
            "new": int(report.get("new") or 0),
            "updated": int(report.get("updated") or 0),
            "skipped": int(report.get("skipped") or 0),
            "failed": int(report.get("failed") or 0),
            "chunks": int(report.get("chunks") or 0),
            "entity_mentions": int(report.get("entity_mentions") or 0),
            "relationships": int(report.get("relationships") or 0),
            "seconds": float(report.get("seconds") or 0),
        },
        "graph": graph,
    }


def _sync_label(stamp: dict[str, Any] | None, docs: int) -> str:
    if not docs:
        return "Corpus empty — run demo ingest"
    if not stamp:
        return f"Live · {docs} documents"
    finished = stamp.get("finished_at", "")
    try:
        dt = datetime.fromisoformat(finished.replace("Z", "+00:00"))
        when = dt.astimezone(timezone.utc).strftime("%b %d %H:%M UTC")
    except Exception:  # noqa: BLE001
        when = "recently"
    return f"Last ingest {when} · {docs} documents"


def _pretty_date(raw: Any) -> str:
    if raw is None:
        return "—"
    s = str(raw).strip()
    if not s:
        return "—"
    # ISO-ish
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    # RFC-ish from RSS
    try:
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(s).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return s[:16]
