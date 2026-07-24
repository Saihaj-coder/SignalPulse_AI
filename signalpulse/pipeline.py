"""The SignalPulse AI ingestion pipeline — the schedulable "data factory".

Ties Steps 2-5 into one incremental flow, per document:

    fetch (Extract)  ->  clean + chunk + embed  ->  extract entities  ->  load

Incremental by design
----------------------
Each ``RawDocument`` carries a ``content_hash`` (a fingerprint of its text).
Before doing any expensive work we compare that hash to what's already stored in
Neo4j:

    * hash missing   -> brand-new document        -> process + load  ("new")
    * hash differs   -> document changed           -> refresh + load  ("updated")
    * hash identical -> unchanged since last run   -> skip            ("skipped")

So a scheduled run only spends LLM/embedding effort on genuinely new or changed
content. ``force=True`` reprocesses everything regardless.

Usage
-----
    from signalpulse.pipeline import run_pipeline
    report = run_pipeline(limit=5)          # all default sources
    report = run_pipeline(limit=3, max_chunks_per_doc=4)   # smaller/cheaper
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from signalpulse import connectors as C
from signalpulse import digest as D
from signalpulse import extraction as X
from signalpulse import graph
from signalpulse import loader as L
from signalpulse import processing as P
from signalpulse.connectors import Connector, RawDocument


@dataclass
class PipelineReport:
    """A running tally of what a pipeline run did."""

    new: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    chunks: int = 0
    entity_mentions: int = 0
    relationships: int = 0
    errors: list[str] = field(default_factory=list)
    seconds: float = 0.0
    # One record per new/updated document — feeds the ingest digest & watchlist.
    changes: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "new": self.new,
            "updated": self.updated,
            "skipped": self.skipped,
            "failed": self.failed,
            "chunks": self.chunks,
            "entity_mentions": self.entity_mentions,
            "relationships": self.relationships,
            "seconds": round(self.seconds, 1),
        }


def process_one(
    doc: RawDocument,
    *,
    max_chunks_per_doc: int | None = None,
    force: bool = False,
) -> tuple[str, dict[str, int]]:
    """Process and load a single document. Returns (status, stats).

    status is one of "new", "updated", "skipped".
    """
    prior_hash = L.existing_content_hash(doc.source_id)

    if prior_hash is not None and prior_hash == doc.content_hash and not force:
        return "skipped", {"chunks": 0, "entity_mentions": 0, "relationships": 0}

    # clean + chunk + embed
    chunks = P.process_document(doc)
    if max_chunks_per_doc is not None:
        chunks = chunks[:max_chunks_per_doc]

    # extract entities/relationships per chunk (LLM)
    extractions = [X.extract_from_chunk(c, title=doc.title) for c in chunks]

    # if this is an update, clear stale chunks before writing the new ones
    if prior_hash is not None:
        L.delete_document_chunks(doc.source_id)

    stats = L.load_document(doc, chunks, extractions)
    status = "updated" if prior_hash is not None else "new"
    return status, stats


def run_pipeline(
    connectors: list[Connector] | None = None,
    *,
    limit: int = 5,
    max_chunks_per_doc: int | None = None,
    force: bool = False,
    verbose: bool = True,
    connector_limits: dict[str, int] | None = None,
) -> PipelineReport:
    """Run the full ingestion pipeline across the given (or default) sources."""
    start = time.time()
    graph.verify_connectivity()
    graph.create_schema()  # idempotent

    connectors = connectors or C.default_connectors()
    report = PipelineReport()
    watchlist = D.load_watchlist()

    # Extract stage: gather documents from every source (failures isolated).
    docs = C.fetch_all(connectors, limit=limit, connector_limits=connector_limits)
    if verbose:
        print(f"\nFetched {len(docs)} documents. Processing incrementally...\n")

    for doc in docs:
        try:
            status, stats = process_one(
                doc, max_chunks_per_doc=max_chunks_per_doc, force=force
            )
            setattr(report, status, getattr(report, status) + 1)
            report.chunks += stats["chunks"]
            report.entity_mentions += stats["entity_mentions"]
            report.relationships += stats["relationships"]
            if status in ("new", "updated"):
                report.changes.append(
                    {
                        "status": status,
                        "source_id": doc.source_id,
                        "title": doc.title,
                        "url": doc.url,
                        "agency": doc.agency,
                        "domain": doc.domain,
                        "connector": doc.connector,
                        "published_date": doc.published_date,
                        "watchlist_hits": D.keyword_hits(
                            f"{doc.title}\n{doc.raw_text}", watchlist
                        ),
                    }
                )
            if verbose:
                tag = {"new": "NEW ", "updated": "UPD ", "skipped": "skip"}[status]
                print(
                    f"  [{tag}] {doc.connector:<14} {doc.source_id[:40]:<40} "
                    f"chunks={stats['chunks']}"
                )
        except Exception as exc:  # noqa: BLE001 - isolate per-document failures
            report.failed += 1
            msg = f"{doc.connector}:{doc.source_id} -> {type(exc).__name__}: {exc}"
            report.errors.append(msg)
            if verbose:
                print(f"  [FAIL] {msg}")

    report.seconds = time.time() - start
    if verbose:
        print(f"\nDone in {report.seconds:.1f}s: {report.as_dict()}")
    return report
