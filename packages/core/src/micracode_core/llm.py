"""Provider-agnostic LLM factory."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from .config import CoreConfig


class LLMFactory:
    """Build a ``BaseChatModel`` by logical name."""

    @staticmethod
    def build(
        config: CoreConfig | None = None,
        provider: str | None = None,
        model: str | None = None,
        *,
        temperature: float = 0.2,
        streaming: bool = True,
        **kwargs: Any,
    ) -> BaseChatModel:
        cfg = config or CoreConfig()
        resolved_provider = provider or cfg.llm_provider

        if resolved_provider == "gemini":
            return ChatGoogleGenerativeAI(
                model=model or cfg.gemini_model,
                google_api_key=cfg.google_api_key or None,
                temperature=temperature,
                **kwargs,
            )

        if resolved_provider == "openai":
            resolved_model = model or cfg.openai_model
            if not resolved_model:
                raise ValueError("OPENAI_MODEL is not set; required when LLM_PROVIDER=openai.")
            openai_kwargs: dict[str, Any] = {
                "model": resolved_model,
                "api_key": cfg.openai_api_key or None,
                "streaming": streaming,
                **kwargs,
            }
            # GPT-5 reasoning family rejects any temperature other than 1.
            if not resolved_model.startswith("gpt-5"):
                openai_kwargs["temperature"] = temperature
            return ChatOpenAI(**openai_kwargs)

        if resolved_provider == "ollama":
            resolved_model = model or cfg.ollama_model
            if not resolved_model:
                raise ValueError("OLLAMA_MODEL is not set; required when LLM_PROVIDER=ollama.")
            return ChatOllama(
                model=resolved_model,
                base_url=cfg.ollama_base_url,
                temperature=temperature,
                **kwargs,
            )

        raise ValueError(f"Unsupported LLM provider: {resolved_provider!r}")


def build_default_llm(config: CoreConfig | None = None) -> BaseChatModel:
    return LLMFactory.build(config)
