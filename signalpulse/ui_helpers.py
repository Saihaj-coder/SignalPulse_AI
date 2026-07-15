"""Helpers for the SignalPulse Chainlit chat UI."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from signalpulse.config import PROCESSED_DIR
from signalpulse.llm import available_providers

# URLs that look like citations in model answers.
_URL_RE = re.compile(r"https?://[^\s\]\)\"'<>]+", re.IGNORECASE)


def extract_urls(text: str) -> list[str]:
    """Deduplicate http(s) URLs found in an answer, preserving order."""
    seen: set[str] = set()
    out: list[str] = []
    for match in _URL_RE.findall(text or ""):
        url = match.rstrip(".,);]")
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def format_tools(tool_calls: list[dict[str, Any]]) -> str:
    """Human-readable tool trace for a Step panel."""
    if not tool_calls:
        return "_No retrieval tools were called._"
    lines: list[str] = []
    for i, tc in enumerate(tool_calls, 1):
        name = tc.get("name") or "tool"
        args = tc.get("args") or {}
        pretty = ", ".join(f"{k}={v!r}" for k, v in args.items() if v not in ("", None))
        lines.append(f"{i}. **`{name}`**" + (f" — {pretty}" if pretty else ""))
    return "\n".join(lines)


def format_sources(urls: list[str]) -> str:
    if not urls:
        return ""
    lines = ["**Sources**"]
    for url in urls:
        host = urlparse(url).netloc or url
        lines.append(f"- [{host}]({url})")
    return "\n".join(lines)


def load_last_ingest() -> dict[str, Any] | None:
    path = PROCESSED_DIR / "last_ingest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def system_status_markdown() -> str:
    """Sidebar / welcome status block (Neo4j + ingest + LLM chain)."""
    lines = [
        "### System status",
        "",
    ]

    # Neo4j
    try:
        from signalpulse import graph
        from signalpulse import loader as L

        ok = graph.verify_connectivity()
        if ok:
            summary = L.graph_summary()
            lines.append("- **Neo4j:** connected")
            lines.append(
                f"- **Corpus:** {summary.get('Document nodes', 0)} docs · "
                f"{summary.get('Chunk nodes', 0)} chunks · "
                f"{summary.get('Entity nodes', 0)} entities"
            )
        else:
            lines.append("- **Neo4j:** unreachable — run `start_neo4j.ps1`")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"- **Neo4j:** error (`{type(exc).__name__}`)")

    # Last ingest
    stamp = load_last_ingest()
    if stamp:
        finished = stamp.get("finished_at", "")
        try:
            dt = datetime.fromisoformat(finished.replace("Z", "+00:00"))
            finished_fmt = dt.strftime("%Y-%m-%d %H:%M UTC")
        except Exception:  # noqa: BLE001
            finished_fmt = finished or "unknown"
        profile = stamp.get("profile", "?")
        lines.append(f"- **Last ingest:** {finished_fmt} (`{profile}`)")
    else:
        lines.append("- **Last ingest:** no stamp yet")

    providers = available_providers()
    lines.append(
        "- **LLM chain:** " + (" → ".join(providers) if providers else "_(none configured)_")
    )
    lines.append("")
    lines.append(
        "_Public U.S. government sources only. Verify critical decisions against "
        "the original .gov links._"
    )
    return "\n".join(lines)


def compose_answer_message(
    answer: str, *, refused: bool, provider: str | None = None
) -> str:
    """Final assistant markdown: answer + sources (provider optional)."""
    text = (answer or "").strip()
    parts: list[str] = []
    if refused:
        # Keep a single clean refuse block (agent already uses the fixed phrase).
        if "not covered in the current sources" not in text.lower():
            parts.append("**Not covered in the current sources.**")
            parts.append("")
            parts.append(text)
        else:
            parts.append("**Not covered in the current sources.**")
        parts.append("")
        parts.append(
            "_Try a cyber, NIST, CMS, health, or NASCIO question grounded in ingested documents._"
        )
    else:
        parts.append(text)
        urls = extract_urls(text)
        sources = format_sources(urls)
        if sources:
            parts.append("")
            parts.append(sources)
    if provider:
        parts.append("")
        parts.append(f"_Answered via **{provider}**_")
    return "\n".join(parts).strip()


def corpus_counts() -> tuple[int, int, int]:
    """Return (documents, chunks, entities) or zeros if Neo4j is down."""
    try:
        from signalpulse import graph
        from signalpulse import loader as L

        if not graph.verify_connectivity():
            return (0, 0, 0)
        s = L.graph_summary()
        return (
            int(s.get("Document nodes", 0)),
            int(s.get("Chunk nodes", 0)),
            int(s.get("Entity nodes", 0)),
        )
    except Exception:  # noqa: BLE001
        return (0, 0, 0)


def list_recent_documents(
    *, agency_focus: str = "all", limit: int = 40
) -> list[dict[str, Any]]:
    """Recent corpus docs for the Sources workspace (title, agency, url)."""
    try:
        from signalpulse import graph

        graph.verify_connectivity()
    except Exception:  # noqa: BLE001
        return []

    focus_filter = {
        "cyber": "toLower(coalesce(d.agency,'')) CONTAINS 'cisa' OR "
        "toLower(coalesce(d.agency,'')) CONTAINS 'nvd' OR "
        "toLower(coalesce(d.title,'')) CONTAINS 'cve' OR "
        "toLower(coalesce(d.title,'')) CONTAINS 'kev'",
        "nist": "toLower(coalesce(d.agency,'')) CONTAINS 'nist'",
        "cms": "toLower(coalesce(d.agency,'')) CONTAINS 'cms' OR "
        "toLower(coalesce(d.agency,'')) CONTAINS 'hhs' OR "
        "toLower(coalesce(d.agency,'')) CONTAINS 'onc' OR "
        "toLower(coalesce(d.agency,'')) CONTAINS 'health' OR "
        "toLower(coalesce(d.agency,'')) CONTAINS 'fda'",
        "state": "toLower(coalesce(d.agency,'')) CONTAINS 'nascio' OR "
        "toLower(coalesce(d.agency,'')) CONTAINS 'state'",
    }.get(agency_focus)

    where = f"WHERE ({focus_filter})" if focus_filter else ""
    cypher = f"""
    MATCH (d:Document)
    {where}
    RETURN d.title AS title,
           d.agency AS agency,
           d.url AS url,
           coalesce(d.id, '') AS id
    ORDER BY coalesce(d.published_date, d.title) DESC
    LIMIT $limit
    """
    try:
        from signalpulse import graph

        return graph.run_query(cypher, {"limit": limit})
    except Exception:  # noqa: BLE001
        return []


def sync_caption() -> str:
    """Short sidebar sync line (no LLM pipeline dump)."""
    stamp = load_last_ingest()
    docs, _chunks, _ents = corpus_counts()
    if not docs:
        return "Corpus offline · start Neo4j"
    if not stamp:
        return f"Live · {docs} documents"
    finished = stamp.get("finished_at", "")
    try:
        dt = datetime.fromisoformat(finished.replace("Z", "+00:00"))
        when = dt.strftime("%b %d %H:%M UTC")
    except Exception:  # noqa: BLE001
        when = "recently"
    sources = stamp.get("sources") or []
    short = " · ".join(str(s).replace("_", " ") for s in sources[:3])
    if len(sources) > 3:
        short += " +"
    return f"Synced {when}" + (f" · {short}" if short else "")
