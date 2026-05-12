"""Provider-agnostic LLM factory.

Supports Gemini and OpenAI. The active provider is selected via
``settings.llm_provider`` (``gemini`` by default). New providers can be
added as additional branches without touching the orchestrator.
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from ..config import get_settings


class LLMFactory:
    """Build a ``BaseChatModel`` by logical name."""

    @staticmethod
    def build(
        provider: str | None = None,
        model: str | None = None,
        *,
        temperature: float = 0.2,
        streaming: bool = True,
        **kwargs: Any,
    ) -> BaseChatModel:
        settings = get_settings()
        resolved_provider = provider or settings.llm_provider

        if resolved_provider == "gemini":
            return ChatGoogleGenerativeAI(
                model=model or settings.gemini_model,
                google_api_key=settings.google_api_key or None,
                temperature=temperature,
                # ChatGoogleGenerativeAI streams by default when used via
                # ``astream`` / ``astream_events``; ``streaming`` is kept for
                # parity with other LangChain providers.
                **kwargs,
            )

        if resolved_provider == "openai":
            resolved_model = model or settings.openai_model
            if not resolved_model:
                raise ValueError("OPENAI_MODEL is not set; required when LLM_PROVIDER=openai.")
            openai_kwargs: dict[str, Any] = {
                "model": resolved_model,
                "api_key": settings.openai_api_key or None,
                "streaming": streaming,
                **kwargs,
            }
            if settings.openai_base_url:
                openai_kwargs["base_url"] = settings.openai_base_url
            # GPT-5 reasoning family rejects any temperature other than 1
            # (400 "Unsupported value: 'temperature'..."). Omit the param
            # so the API uses its default; non-reasoning models keep the
            # caller-supplied value.
            if not resolved_model.startswith("gpt-5"):
                openai_kwargs["temperature"] = temperature
            return ChatOpenAI(**openai_kwargs)

        if resolved_provider == "ollama":
            resolved_model = model or settings.ollama_model
            if not resolved_model:
                raise ValueError("OLLAMA_MODEL is not set; required when LLM_PROVIDER=ollama.")
            return ChatOllama(
                model=resolved_model,
                base_url=settings.ollama_base_url,
                temperature=temperature,
                **kwargs,
            )

        raise ValueError(f"Unsupported LLM provider: {resolved_provider!r}")


def build_default_llm() -> BaseChatModel:
    """Shortcut used by the codegen graph."""
    return LLMFactory.build()
