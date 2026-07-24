"""SignalPulse AI — enterprise web console (FastAPI).

Serves a custom HTML/CSS/JS dashboard + Agentic RAG chat over the same
``ask()`` engine used in notebooks.

    .\\start_neo4j.ps1
    .\\run_chat.ps1
    # → http://localhost:8501
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WEB = ROOT / "web"

from signalpulse.agent import ask  # noqa: E402
from signalpulse.dashboard import (  # noqa: E402
    corpus_overview,
    list_documents,
    pipeline_story,
    source_catalog,
)
from signalpulse.digest import load_latest_digest, load_watchlist  # noqa: E402
from signalpulse.ui_helpers import compose_answer_message, extract_urls  # noqa: E402

app = FastAPI(title="SignalPulse AI", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(WEB / "static")), name="static")

FOCUS_HINTS = {
    "all": "",
    "cyber": "Focus on CISA, NVD, and KEV/BOD cyber sources. ",
    "nist": "Focus on NIST guidance (CSF, RMF, SP 800-53). ",
    "health": "Focus on CMS, HHS/ONC, and HealthIT sources. ",
    "state": "Focus on NASCIO and state CIO priorities. ",
    "defense": "Focus on Defense / DoD Federal Register sources. ",
}


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1)
    focus: str = "all"


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    overview = corpus_overview()
    return {
        "ok": True,
        "online": overview["online"],
        "sync_label": overview["sync_label"],
    }


@app.get("/api/overview")
def api_overview() -> dict[str, Any]:
    return corpus_overview()


@app.get("/api/documents")
def api_documents(
    focus: str = "all",
    q: str = "",
    limit: int = 80,
) -> dict[str, Any]:
    docs = list_documents(focus=focus, q=q, limit=min(limit, 200))
    return {"count": len(docs), "documents": docs}


@app.get("/api/sources")
def api_sources() -> dict[str, Any]:
    return {"sources": source_catalog()}


@app.get("/api/pipeline")
def api_pipeline() -> dict[str, Any]:
    return pipeline_story()


@app.get("/api/digest")
def api_digest() -> dict[str, Any]:
    digest = load_latest_digest()
    return {
        "available": digest is not None,
        "watchlist": load_watchlist(),
        "digest": digest,
    }


@app.post("/api/ask")
def api_ask(body: AskRequest) -> dict[str, Any]:
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Empty question")
    hint = FOCUS_HINTS.get(body.focus, "")
    try:
        reply = ask(f"{hint}{question}")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc

    body_md = compose_answer_message(reply.answer, refused=reply.refused)
    urls = extract_urls(reply.answer)
    tools = [
        {"name": t.get("name"), "args": t.get("args") or {}}
        for t in (reply.tool_calls or [])
        if t.get("name")
    ]
    return {
        "answer": body_md,
        "raw_answer": reply.answer,
        "refused": reply.refused,
        "urls": urls,
        "tools": tools,
        "provider": getattr(reply, "provider", None),
    }
