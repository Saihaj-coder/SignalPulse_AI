"""SignalPulse AI — ingestion pipeline runner (CLI entry point).

The schedulable "data factory". Runs the full
Extract -> Clean -> Chunk -> Embed -> Extract-entities -> Load flow,
incrementally (only new/changed documents do real work).

Profiles
--------
    demo    Deeper pull on overlapping cyber / NIST / CMS sources (company demo)
    weekly  Same as demo — use for a weekly refresh habit
    full    All default sources at moderate depth
    smoke   Tiny plumbing check

Examples
--------
    python run_pipeline.py --profile demo
    python run_pipeline.py --profile weekly
    python run_pipeline.py --profile full
    python run_pipeline.py --limit 3 --source cisa_kev
    python run_pipeline.py --force               # reprocess even unchanged docs
    python run_pipeline.py --list-sources
    python run_pipeline.py --list-profiles

Windows weekly (Task Scheduler) or manual:
    .\\run_demo_ingest.ps1
    # or:  python run_pipeline.py --profile weekly
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from signalpulse import connectors as C
from signalpulse import graph
from signalpulse import loader as L
from signalpulse.config import PROCESSED_DIR, settings
from signalpulse.pipeline import run_pipeline


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run the SignalPulse ingestion pipeline.")
    p.add_argument(
        "--profile",
        choices=sorted(C.INGEST_PROFILES.keys()),
        help="Named ingest profile (demo / weekly / full / smoke).",
    )
    p.add_argument("--source", help="Only run the connector with this name.")
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max docs per source (overrides profile default).",
    )
    p.add_argument(
        "--max-chunks",
        type=int,
        default=None,
        help="Cap chunks per document (overrides profile; limits LLM use).",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Reprocess documents even if unchanged.",
    )
    p.add_argument(
        "--list-sources",
        action="store_true",
        help="List available source names and exit.",
    )
    p.add_argument(
        "--list-profiles",
        action="store_true",
        help="List ingest profiles and exit.",
    )
    return p


def _write_ingest_stamp(profile: str, report: dict, sources: list[str]) -> Path:
    """Persist last-ingest metadata for the UI / operators."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    path = PROCESSED_DIR / "last_ingest.json"
    payload = {
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "profile": profile,
        "sources": sources,
        "report": report,
        "graph": L.graph_summary(),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.list_profiles:
        print("Ingest profiles:")
        for name, cfg in C.INGEST_PROFILES.items():
            print(f"  - {name:8s} limit={cfg['limit']:<3} max_chunks={cfg['max_chunks']}")
            print(f"             {cfg['description']}")
        return 0

    if args.profile:
        profile_name = args.profile
        profile = C.INGEST_PROFILES[profile_name]
        all_connectors = profile["connectors"]()
        limit = args.limit if args.limit is not None else int(profile["limit"])
        max_chunks = (
            args.max_chunks
            if args.max_chunks is not None
            else int(profile["max_chunks"])
        )
        connector_limits = dict(profile.get("connector_limits") or {})
        profile_blurb = profile["description"]
    else:
        profile_name = "custom"
        all_connectors = C.default_connectors()
        limit = args.limit if args.limit is not None else 5
        max_chunks = args.max_chunks  # may be None = no cap
        connector_limits: dict[str, int] = {}
        profile_blurb = "all default sources (pass --profile demo for the company-demo corpus)"

    if args.list_sources:
        label = args.profile or "default"
        print(f"Sources ({label}):")
        for c in (C.INGEST_PROFILES[args.profile]["connectors"]() if args.profile else C.default_connectors()):
            print(f"  - {c.name:<22} [{getattr(c, 'domain', '')}] ({c.tier})")
        return 0

    connectors = all_connectors
    if args.source:
        connectors = [c for c in all_connectors if c.name == args.source]
        if not connectors:
            connectors = [c for c in C.default_connectors() if c.name == args.source]
        if not connectors:
            print(f"No source named {args.source!r}. Use --list-sources.")
            return 2

    print("SignalPulse AI ingestion pipeline")
    print(f"  Profile      : {profile_name} — {profile_blurb}")
    print(f"  Neo4j target : {settings.NEO4J_URI}")
    print(f"  LLM provider : {settings.LLM_PROVIDER} ({settings.GROQ_MODEL})")
    print(f"  Limit/source : {limit}  |  max_chunks/doc: {max_chunks}")
    if connector_limits:
        caps = ", ".join(f"{k}={v}" for k, v in sorted(connector_limits.items()))
        print(f"  Source caps  : {caps}")
    print(f"  Sources      : {', '.join(c.name for c in connectors)}")

    report = run_pipeline(
        connectors,
        limit=limit,
        max_chunks_per_doc=max_chunks,
        force=args.force,
        connector_limits=connector_limits or None,
    )

    stamp = _write_ingest_stamp(
        profile_name,
        report.as_dict(),
        [c.name for c in connectors],
    )
    print(f"Ingest stamp  : {stamp}")
    print("Graph summary :", L.graph_summary())

    processed = report.new + report.updated + report.skipped
    return 1 if (report.failed and processed == 0) else 0


if __name__ == "__main__":
    sys.exit(main())
