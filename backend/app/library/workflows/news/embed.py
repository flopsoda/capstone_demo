"""Text embeddings for news search: what an article says, as a vector the store can compare with a
request's (pgvector cosine distance). Used by every request and scheduled collection the same way.

    model   NEWS_EMBED_MODEL (default embeddinggemma; "none" turns semantic search off),
            served by a local Ollama (NEWS_EMBED_URL, default http://localhost:11434) — the chat
            models' cloud does not serve embeddings
    text    an article's headline and the start of its text (EMBED_CHARS of the body; under an
            "AI 학습·활용 금지" notice only the lead, as for the LLM; else the description)

When the model cannot be reached the caller searches by keywords only; after a failure the model
is not asked again for RETRY_AFTER seconds, so a request is never slowed down twice.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_MODEL = "embeddinggemma"
DEFAULT_URL = "http://localhost:11434"
EMBED_CHARS = 500
LEAD_CHARS = 200  # under a usage notice
BATCH = 32
QUERY_TIMEOUT = 5.0
BATCH_TIMEOUT = 60.0
RETRY_AFTER = 60.0

# The prompts a model was trained with for search (embeddinggemma's model card); others take none.
PROMPTS = {"embeddinggemma": ("task: search result | query: ", "title: none | text: ")}
# Which semantic hits count (cosine similarity): at least FLOOR, and within MARGIN of the best hit.
# Calibrated on stored Korean news for embeddinggemma; other models use the default until measured.
CUTOFFS = {"embeddinggemma": (0.35, 0.08)}
DEFAULT_CUTOFF = (0.0, 0.1)


class EmbedUnavailable(RuntimeError):
    """The embedding model cannot answer now."""


def article_text(row: dict[str, Any]) -> str:
    body = row.get("body") or ""
    if body:
        text = body[:LEAD_CHARS] if row.get("usage_notice") else body[:EMBED_CHARS]
    else:
        text = (row.get("summary") or "")[:EMBED_CHARS]
    return f"{row.get('title') or ''}\n{text}".strip()


@dataclass
class Embedder:
    model: str = DEFAULT_MODEL
    url: str = DEFAULT_URL

    def __post_init__(self) -> None:
        self._down_until = 0.0
        self._lock = threading.Lock()

    @property
    def cutoff(self) -> tuple[float, float]:
        return CUTOFFS.get(self.model.split(":")[0], DEFAULT_CUTOFF)

    def _prompt(self, kind: int) -> str:
        return PROMPTS.get(self.model.split(":")[0], ("", ""))[kind]

    def _embed(self, texts: list[str], timeout: float) -> list[list[float]]:
        if time.monotonic() < self._down_until:
            raise EmbedUnavailable(f"{self.model}: unavailable (retrying later)")
        try:
            res = httpx.post(f"{self.url.rstrip('/')}/api/embed", json={"model": self.model, "input": texts}, timeout=timeout)
            res.raise_for_status()
            vectors = res.json()["embeddings"]
        except (httpx.HTTPError, KeyError, ValueError, OSError) as e:
            with self._lock:
                self._down_until = time.monotonic() + RETRY_AFTER
            raise EmbedUnavailable(f"{self.model}: {type(e).__name__}") from e
        if len(vectors) != len(texts):
            raise EmbedUnavailable(f"{self.model}: {len(vectors)} vectors for {len(texts)} texts")
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._embed([self._prompt(0) + text], QUERY_TIMEOUT)[0]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed([self._prompt(1) + t for t in texts], BATCH_TIMEOUT)


_DEFAULT: dict[tuple[str, str], Embedder] = {}


def default_embedder() -> Embedder | None:
    """The configured embedder (one per model and URL, so its back-off is shared), or None when turned off."""
    model = os.environ.get("NEWS_EMBED_MODEL", "").strip() or DEFAULT_MODEL
    if model.lower() == "none":
        return None
    url = os.environ.get("NEWS_EMBED_URL", "").strip() or DEFAULT_URL
    return _DEFAULT.setdefault((model, url), Embedder(model, url))
