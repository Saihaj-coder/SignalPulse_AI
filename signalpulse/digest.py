"""Ingest digest + watchlist alerts for SignalPulse AI.

After every pipeline run we summarize *what changed* (new / updated documents)
and flag any that mention a watchlist keyword. This turns the tool from
pull-only ("ask a question") into a light form of monitoring ("here is what
landed this week, and which items you said you care about").

Artifacts (under ``data/processed/``):

* ``digest_latest.json``  — machine-readable digest for the web console
* ``digest_latest.md``    — human-readable version (open in any editor)

Watchlist keywords live in ``data/seeds/watchlist.txt`` (one per line,
``#`` comments allowed). Edit that file to follow the topics you care about.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from signalpulse.config import DATA_DIR, PROCESSED_DIR

WATCHLIST_PATH = DATA_DIR / "seeds" / "watchlist.txt"
DIGEST_JSON = PROCESSED_DIR / "digest_latest.json"
DIGEST_MD = PROCESSED_DIR / "digest_latest.md"


def load_watchlist() -> list[str]:
    """Read watchlist keywords (lowercased, deduped, comments stripped)."""
    if not WATCHLIST_PATH.exists():
        return []
    keywords: list[str] = []
    for line in WATCHLIST_PATH.read_text(encoding="utf-8").splitlines():
        term = line.split("#", 1)[0].strip().lower()
        if term and term not in keywords:
            keywords.append(term)
    return keywords


def keyword_hits(text: str, keywords: list[str]) -> list[str]:
    """Return the watchlist keywords that appear in ``text`` (word-ish match)."""
    if not keywords or not text:
        return []
    low = text.lower()
    hits = []
    for kw in keywords:
        # Substring match is fine for ids (cve-2026) and multiword phrases;
        # require word boundaries for short single words to avoid noise.
        if len(kw) <= 4 and " " not in kw:
            if re.search(rf"\b{re.escape(kw)}\b", low):
                hits.append(kw)
        elif kw in low:
            hits.append(kw)
    return hits


def build_digest(
    changes: list[dict[str, Any]],
    *,
    profile: str,
) -> dict[str, Any]:
    """Assemble the digest payload from pipeline change records."""
    watchlist = load_watchlist()
    alerts = [c for c in changes if c.get("watchlist_hits")]

    by_domain: dict[str, list[dict[str, Any]]] = {}
    for c in changes:
        by_domain.setdefault(c.get("domain") or "Other", []).append(c)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profile": profile,
        "total_changes": len(changes),
        "new": sum(1 for c in changes if c.get("status") == "new"),
        "updated": sum(1 for c in changes if c.get("status") == "updated"),
        "watchlist": watchlist,
        "alerts": alerts,
        "by_domain": [
            {"domain": dom, "items": items}
            for dom, items in sorted(by_domain.items())
        ],
    }


def _digest_markdown(digest: dict[str, Any]) -> str:
    lines = [
        "# SignalPulse ingest digest",
        "",
        f"Generated: {digest['generated_at']}  |  profile: {digest['profile']}",
        f"Changes: {digest['total_changes']} "
        f"({digest['new']} new, {digest['updated']} updated)",
        "",
    ]
    if digest["alerts"]:
        lines.append("## Watchlist alerts")
        for a in digest["alerts"]:
            kws = ", ".join(a.get("watchlist_hits") or [])
            lines.append(f"- **{a.get('title', '?')}** — matched: {kws}")
            lines.append(f"  {a.get('url', '')}")
        lines.append("")
    elif digest["watchlist"]:
        lines.append("## Watchlist alerts")
        lines.append("_No new documents matched the watchlist this run._")
        lines.append("")

    for group in digest["by_domain"]:
        lines.append(f"## {group['domain']}")
        for c in group["items"]:
            tag = "NEW" if c.get("status") == "new" else "UPDATED"
            lines.append(f"- [{tag}] {c.get('title', '?')} ({c.get('agency', '')})")
            lines.append(f"  {c.get('url', '')}")
        lines.append("")
    if digest["total_changes"] == 0:
        lines.append("_No new or updated documents this run (corpus unchanged)._")
    return "\n".join(lines)


def write_digest(digest: dict[str, Any]) -> None:
    """Persist the digest as JSON (for the UI) and Markdown (for humans)."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    DIGEST_JSON.write_text(json.dumps(digest, indent=2), encoding="utf-8")
    DIGEST_MD.write_text(_digest_markdown(digest), encoding="utf-8")


def load_latest_digest() -> dict[str, Any] | None:
    """Latest digest for the web console, or None if no run has produced one."""
    if not DIGEST_JSON.exists():
        return None
    try:
        return json.loads(DIGEST_JSON.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
