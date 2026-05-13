"""Unit tests for the provider-agnostic LLM factory."""

from __future__ import annotations

import pytest
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI

from micracode_api.config import get_settings
from micracode_core.llm import LLMFactory


def test_factory_builds_gemini_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    get_settings.cache_clear()
    try:
        llm = LLMFactory.build()
    finally:
        get_settings.cache_clear()

    assert isinstance(llm, ChatGoogleGenerativeAI)


def test_factory_builds_openai_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")
    get_settings.cache_clear()
    try:
        llm = LLMFactory.build()
    finally:
        get_settings.cache_clear()

    assert isinstance(llm, ChatOpenAI)
    assert llm.model_name == "gpt-test"


def test_factory_openai_without_model_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValueError, match="OPENAI_MODEL"):
            LLMFactory.build()
    finally:
        get_settings.cache_clear()


def test_factory_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="Unsupported LLM provider"):
        LLMFactory.build(provider="anthropic")
