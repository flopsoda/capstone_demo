"""Chat model factory: Ollama Cloud (default) or an OpenAI-compatible API.

LLM_PROVIDER=ollama  → ChatOllama, OLLAMA_* variables
LLM_PROVIDER=openai  → ChatOpenAI, OPENAI_* variables; OPENAI_BASE_URL points it at any
                       OpenAI-compatible server (e.g. Groq https://api.groq.com/openai/v1)
LLM_TIMEOUT_SECONDS  → how long one call may wait on the provider (default 120). Without it a call the provider
                       never finishes holds the run: on Ollama Cloud an empty-turn retry has waited about 8 minutes.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

# backend/.env (this file is backend/app/runtime/llm.py)
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(ENV_PATH)

DEFAULT_BASE_URL = "https://ollama.com"
DEFAULT_MODEL = "deepseek-v4.1-flash"
# Explicit, because the openai client reads an empty OPENAI_BASE_URL from the environment as the URL.
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
# Free tiers answer bursts with 429 + retry-after; a Super Agent run makes many calls in a row.
OPENAI_MAX_RETRIES = 6
# Seconds one call may wait on the provider (httpx read timeout: the longest gap without data). A normal turn,
# even a long tool call, comes back in well under a minute; agent_core treats a timed-out turn as an empty one.
DEFAULT_TIMEOUT_SECONDS = 120.0


class LLMConfigError(RuntimeError):
    pass


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def timeout_seconds() -> float:
    try:
        value = float(_env("LLM_TIMEOUT_SECONDS") or DEFAULT_TIMEOUT_SECONDS)
    except ValueError:
        raise LLMConfigError("LLM_TIMEOUT_SECONDS must be a number of seconds") from None
    if value <= 0:
        raise LLMConfigError("LLM_TIMEOUT_SECONDS must be greater than 0")
    return value


@lru_cache(maxsize=1)
def get_chat_model() -> BaseChatModel:
    provider = _env("LLM_PROVIDER").lower() or "ollama"
    if provider == "openai":
        return _openai_chat_model()
    if provider != "ollama":
        raise LLMConfigError(f"LLM_PROVIDER must be 'ollama' or 'openai', got {provider!r}")
    api_key = _env("OLLAMA_API_KEY")
    if not api_key:
        raise LLMConfigError("OLLAMA_API_KEY is not set (see backend/.env.example)")
    return ChatOllama(
        model=_env("OLLAMA_MODEL") or DEFAULT_MODEL,
        base_url=_env("OLLAMA_BASE_URL") or DEFAULT_BASE_URL,
        client_kwargs={"headers": {"Authorization": f"Bearer {api_key}"}, "timeout": timeout_seconds()},
        temperature=0.2,
    )


def _openai_chat_model() -> ChatOpenAI:
    api_key, model = _env("OPENAI_API_KEY"), _env("OPENAI_MODEL")
    if not api_key or not model:
        raise LLMConfigError("LLM_PROVIDER=openai needs OPENAI_API_KEY and OPENAI_MODEL (see backend/.env.example)")
    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=_env("OPENAI_BASE_URL") or DEFAULT_OPENAI_BASE_URL,
        temperature=0.2,
        max_retries=OPENAI_MAX_RETRIES,
        timeout=timeout_seconds(),
    )
