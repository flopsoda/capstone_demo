"""LLM provider selection. Building a chat model does not call the API, so no key is needed."""

import pytest
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from app.runtime import llm

PROVIDER_VARS = (
    "LLM_PROVIDER",
    "OLLAMA_API_KEY",
    "OLLAMA_MODEL",
    "OLLAMA_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "OPENAI_BASE_URL",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in PROVIDER_VARS:
        monkeypatch.delenv(name, raising=False)
    llm.get_chat_model.cache_clear()
    yield
    llm.get_chat_model.cache_clear()


def test_defaults_to_ollama(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "key")
    model = llm.get_chat_model()
    assert isinstance(model, ChatOllama)
    assert model.model == llm.DEFAULT_MODEL


def test_openai_compatible_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("OPENAI_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.groq.com/openai/v1")
    model = llm.get_chat_model()
    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "openai/gpt-oss-120b"
    assert model.openai_api_base == "https://api.groq.com/openai/v1"


@pytest.mark.parametrize(
    "env",
    [
        {},  # ollama without OLLAMA_API_KEY
        {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "key"},  # no OPENAI_MODEL
        {"LLM_PROVIDER": "claude"},
    ],
)
def test_misconfiguration_is_reported(monkeypatch, env):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(llm.LLMConfigError):
        llm.get_chat_model()
