"""Chat model factory for Ollama Cloud."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from langchain_ollama import ChatOllama

# backend/.env (this file is backend/app/runtime/llm.py)
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(ENV_PATH)

DEFAULT_BASE_URL = "https://ollama.com"
DEFAULT_MODEL = "deepseek-v4.1-flash"


class LLMConfigError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def get_chat_model() -> ChatOllama:
    api_key = os.environ.get("OLLAMA_API_KEY", "").strip()
    if not api_key:
        raise LLMConfigError("OLLAMA_API_KEY is not set (see backend/.env.example)")
    return ChatOllama(
        model=os.environ.get("OLLAMA_MODEL", "").strip() or DEFAULT_MODEL,
        base_url=os.environ.get("OLLAMA_BASE_URL", "").strip() or DEFAULT_BASE_URL,
        client_kwargs={"headers": {"Authorization": f"Bearer {api_key}"}},
        temperature=0.2,
    )
