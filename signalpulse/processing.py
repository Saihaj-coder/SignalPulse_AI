"""Text processing for SignalPulse AI (the "Transform" stage).

Turns a ``RawDocument`` into embedding-ready ``Chunk`` objects in three steps:

    1. clean   -> normalize whitespace / strip junk (deterministic)
    2. chunk   -> split into overlapping passages (deterministic, no LLM)
    3. embed   -> convert each chunk to a vector with a free local model

Design choices
--------------
* Chunking is **deterministic** (a rule-based splitter), not LLM-based. It is
  cheaper, faster, and produces identical output every run — which matters for
  a scheduled, idempotent pipeline.
* Embeddings are produced **locally** by ``sentence-transformers`` using
  ``BAAI/bge-small-en-v1.5`` (384-dim). Free, no API limits, no data leaves the
  machine. Embeddings are L2-normalized so cosine similarity works cleanly with
  the Neo4j vector index we created in Step 1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING

from langchain_text_splitters import RecursiveCharacterTextSplitter

from signalpulse.config import settings

if TYPE_CHECKING:  # avoid importing heavy deps at module import time
    from sentence_transformers import SentenceTransformer

    from signalpulse.connectors import RawDocument


# ---------------------------------------------------------------------------
# Chunk object
# ---------------------------------------------------------------------------


@dataclass
class Chunk:
    """A passage of a document, plus its embedding once computed."""

    id: str                     # e.g. "CVE-2026-1234::0"
    document_id: str            # the parent document's source_id
    chunk_index: int            # 0-based position within the document
    text: str
    embedding: list[float] | None = field(default=None, repr=False)

    def preview(self, n: int = 160) -> str:
        text = " ".join(self.text.split())
        snippet = text[:n] + ("..." if len(text) > n else "")
        dim = len(self.embedding) if self.embedding is not None else 0
        return f"  [{self.id}] (emb dim={dim}) {snippet}"


# ---------------------------------------------------------------------------
# 1. Cleaning
# ---------------------------------------------------------------------------

_WS_RUN = re.compile(r"[ \t]+")
_NL_RUN = re.compile(r"\n{3,}")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def clean_text(text: str) -> str:
    """Normalize text: strip control chars, collapse runs of spaces/newlines."""
    if not text:
        return ""
    text = _CTRL.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS_RUN.sub(" ", text)          # collapse spaces/tabs
    text = _NL_RUN.sub("\n\n", text)        # cap blank-line runs at one
    lines = [ln.strip() for ln in text.split("\n")]
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# 2. Chunking (deterministic)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_splitter() -> RecursiveCharacterTextSplitter:
    """Build the recursive character splitter from config.

    ``RecursiveCharacterTextSplitter`` tries to split on paragraph breaks first,
    then lines, then spaces — keeping semantically related text together. Sizes
    are in **characters** (see the notebook for why this stays safely under the
    embedding model's 512-token limit).
    """
    return RecursiveCharacterTextSplitter(
        chunk_size=settings.CHUNK_SIZE,
        chunk_overlap=settings.CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
        length_function=len,
    )


def split_text(text: str) -> list[str]:
    """Split cleaned text into overlapping chunks."""
    if not text.strip():
        return []
    return get_splitter().split_text(text)


def split_document(doc: "RawDocument") -> list[Chunk]:
    """Clean and split a RawDocument into Chunk objects (no embeddings yet)."""
    cleaned = clean_text(doc.raw_text)
    pieces = split_text(cleaned)
    return [
        Chunk(
            id=f"{doc.source_id}::{i}",
            document_id=doc.source_id,
            chunk_index=i,
            text=piece,
        )
        for i, piece in enumerate(pieces)
    ]


# ---------------------------------------------------------------------------
# 3. Embeddings (local, free)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_embedder() -> "SentenceTransformer":
    """Load the local embedding model once and cache it.

    Downloaded automatically on first use (~130 MB) and cached by Hugging Face.
    """
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(settings.EMBEDDING_MODEL)


def count_tokens(text: str) -> int:
    """Number of tokens the embedding model would see (to check the 512 limit)."""
    tok = get_embedder().tokenizer
    return len(tok(text)["input_ids"])


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of passages -> list of normalized 384-dim vectors."""
    if not texts:
        return []
    model = get_embedder()
    vectors = model.encode(
        texts,
        normalize_embeddings=True,       # L2-normalize -> cosine-ready
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return [v.tolist() for v in vectors]


def embed_query(query: str) -> list[float]:
    """Embed a search query. bge-v1.5 recommends a short instruction prefix for
    queries (not for passages), which improves retrieval quality."""
    instruction = "Represent this sentence for searching relevant passages: "
    model = get_embedder()
    vec = model.encode(
        instruction + query,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return vec.tolist()


def embed_chunks(chunks: list[Chunk]) -> list[Chunk]:
    """Attach embeddings to each chunk (mutates and returns the same list)."""
    vectors = embed_texts([c.text for c in chunks])
    for chunk, vector in zip(chunks, vectors):
        chunk.embedding = vector
    return chunks


# ---------------------------------------------------------------------------
# Convenience: full transform for one document
# ---------------------------------------------------------------------------


def process_document(doc: "RawDocument") -> list[Chunk]:
    """clean -> chunk -> embed for a single document. Returns embedded chunks."""
    chunks = split_document(doc)
    return embed_chunks(chunks)
