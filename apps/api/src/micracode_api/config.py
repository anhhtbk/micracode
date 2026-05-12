"""Application configuration loaded via pydantic-settings.

Values are read from (in order of precedence):
  1. Process environment
  2. `apps/api/.env`
  3. Repo-root `.env`

Local-filesystem build: no database credentials. Generated code lives
under ``opener_apps_dir`` (default ``~/opener-apps/``).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_API_DIR = Path(__file__).resolve().parents[2]
_REPO_ROOT = _API_DIR.parent.parent


def _default_data_dir() -> Path:
    return Path.home() / "opener-apps"


class Settings(BaseSettings):
    """Runtime settings for the FastAPI service."""

    model_config = SettingsConfigDict(
        env_file=(_REPO_ROOT / ".env", _API_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App ---------------------------------------------------------------
    app_name: str = "micracode-api"
    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")

    # --- CORS --------------------------------------------------------------
    app_web_origin: str = Field(default="http://localhost:3000")

    @property
    def cors_allow_origins(self) -> list[str]:
        return [o.strip() for o in self.app_web_origin.split(",") if o.strip()]

    # --- LLM ---------------------------------------------------------------
    llm_provider: Literal["gemini", "openai", "ollama"] = Field(default="gemini")

    google_api_key: str = Field(default="")
    gemini_model: str = Field(default="gemini-2.5-flash")

    openai_api_key: str = Field(default="")
    # No hard-coded default — callers must set OPENAI_MODEL when provider=openai.
    openai_model: str = Field(default="")
    # Optional override for OpenAI-compatible endpoints (Azure proxy,
    # OpenRouter, LiteLLM, vLLM, LM Studio, Together, Groq, …). Empty
    # string means "use the OpenAI SDK default".
    openai_base_url: str = Field(default="")

    ollama_base_url: str = Field(default="http://localhost:11434")
    # No hard-coded default — callers must set OLLAMA_MODEL when provider=ollama.
    ollama_model: str = Field(default="")

    @property
    def active_model(self) -> str:
        if self.llm_provider == "openai":
            return self.openai_model
        if self.llm_provider == "ollama":
            return self.ollama_model
        return self.gemini_model

    @property
    def active_api_key(self) -> str:
        if self.llm_provider == "openai":
            return self.openai_api_key
        if self.llm_provider == "ollama":
            return ""
        return self.google_api_key

    @property
    def active_api_key_env_var(self) -> str:
        """Name of the env var the user must set for the active provider."""
        if self.llm_provider == "openai":
            return "OPENAI_API_KEY"
        if self.llm_provider == "ollama":
            return ""
        return "GOOGLE_API_KEY"

    # --- Local storage -----------------------------------------------------
    opener_apps_dir: Path = Field(default_factory=_default_data_dir)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
