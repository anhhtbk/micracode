"""MicracodeEngine — stateful core engine, one instance per running app."""

from __future__ import annotations

from collections.abc import AsyncIterator

from .config import CoreConfig
from .model_catalog import list_catalog
from .orchestrator import run_codegen_stream
from .schemas.project import PromptRecord
from .schemas.stream import StreamEvent
from .storage import Storage


class MicracodeEngine:
    """Single entry-point for all Micracode core operations.

    Instantiate once at app startup with a :class:`CoreConfig`.  All
    downstream operations (codegen, model listing, storage) flow through this
    object so config is injected once and never re-read from env vars mid-run.
    """

    def __init__(self, config: CoreConfig) -> None:
        self.config = config
        self.storage = Storage(config.opener_apps_dir)

    def ensure_root(self) -> None:
        self.storage.ensure_root()

    def run_codegen(
        self,
        *,
        project_id: str,
        prompt: str,
        history: list[PromptRecord] | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        return run_codegen_stream(
            project_id=project_id,
            prompt=prompt,
            history=history,
            storage=self.storage,
            config=self.config,
            provider=provider,
            model=model,
        )

    async def list_catalog(self) -> dict:
        return await list_catalog(self.config)
