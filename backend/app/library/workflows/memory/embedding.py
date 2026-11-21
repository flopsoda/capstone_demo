"""Embedding providers of the conversation memory — chosen by configuration, so the model can change.

    MEMORY_EMBED_PROVIDER   openai (default) | ollama | none
    MEMORY_EMBED_MODEL      openai: text-embedding-3-small (default) · ollama: embeddinggemma (default)
    MEMORY_EMBED_API_KEY    openai: the API key (kept in backend/.env, never in code or logs)
    MEMORY_EMBED_URL        openai: https://api.openai.com/v1 (default; any OpenAI-compatible server) ·
                            ollama: http://localhost:11434 (default; the server's own Ollama)

Every provider answers embed_query(text) / embed_documents(texts) and has a `model` name; the store keeps the vector
under "<provider>:<model>", so after a change the old vectors are not compared with new ones and
`python -m app.library.workflows.memory.reembed` adds the new model's vectors to what is already kept.
The Ollama provider is the news search's (news/embed.py), with its search prompts.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx

from ..news.embed import EmbedUnavailable, Embedder as OllamaEmbedder

DEFAULT_OPENAI_MODEL = "text-embedding-3-small"
DEFAULT_OPENAI_URL = "https://api.openai.com/v1"
DEFAULT_OLLAMA_MODEL = "embeddinggemma"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
QUERY_TIMEOUT = 10.0
BATCH_TIMEOUT = 60.0


class MemoryEmbedder(Protocol):
    name: str  # what the store keeps the vectors under: provider:model

    def embed_query(self, text: str) -> list[float]: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...


@dataclass
class OpenAIEmbedder:
    model: str
    api_key: str
    url: str = DEFAULT_OPENAI_URL

    @property
    def name(self) -> str:
        return f"openai:{self.model}"

    def _embed(self, texts: list[str], timeout: float) -> list[list[float]]:
        try:
            res = httpx.post(f"{self.url.rstrip('/')}/embeddings", json={"model": self.model, "input": texts},
                             headers={"Authorization": f"Bearer {self.api_key}"}, timeout=timeout)
            res.raise_for_status()
            data = sorted(res.json()["data"], key=lambda d: d["index"])
        except httpx.HTTPStatusError as e:  # the status only: the request carries the key
            raise EmbedUnavailable(f"{self.name}: HTTP {e.response.status_code}") from None
        except (httpx.HTTPError, KeyError, ValueError, OSError) as e:
            raise EmbedUnavailable(f"{self.name}: {type(e).__name__}") from None
        if len(data) != len(texts):
            raise EmbedUnavailable(f"{self.name}: {len(data)} vectors for {len(texts)} texts")
        return [d["embedding"] for d in data]

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], QUERY_TIMEOUT)[0]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed(list(texts), BATCH_TIMEOUT)


class OllamaMemoryEmbedder(OllamaEmbedder):
    @property
    def name(self) -> str:
        return f"ollama:{self.model}"


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def memory_embedder() -> MemoryEmbedder | None:
    """The configured provider, or None (turned off, or OpenAI without a key): the memory then searches by words."""
    provider = (_env("MEMORY_EMBED_PROVIDER") or "openai").lower()
    if provider == "none":
        return None
    if provider == "ollama":
        return OllamaMemoryEmbedder(_env("MEMORY_EMBED_MODEL") or DEFAULT_OLLAMA_MODEL, _env("MEMORY_EMBED_URL") or DEFAULT_OLLAMA_URL)
    if provider != "openai":
        raise ValueError(f"MEMORY_EMBED_PROVIDER must be openai, ollama or none, got {provider!r}")
    key = _env("MEMORY_EMBED_API_KEY")
    if not key:
        return None
    return OpenAIEmbedder(_env("MEMORY_EMBED_MODEL") or DEFAULT_OPENAI_MODEL, key, _env("MEMORY_EMBED_URL") or DEFAULT_OPENAI_URL)
