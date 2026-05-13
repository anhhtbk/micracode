from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from micracode_core.config import CoreConfig


class MicracodeSettings(CoreConfig):
    """Runtime settings for the Micracode web server."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_web_origin: str = Field(default="http://localhost:3000")

    @property
    def cors_allow_origins(self) -> list[str]:
        return [o.strip() for o in self.app_web_origin.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> MicracodeSettings:
    return MicracodeSettings()


settings = get_settings()
