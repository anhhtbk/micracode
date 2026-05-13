"""Pydantic mirror of ``packages/shared/src/stream-events.ts``.

Any change here MUST be mirrored in the TypeScript source of truth to keep
the SSE contract in sync.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MessageDeltaEvent(_Event):
    type: Literal["message.delta"] = "message.delta"
    content: str


class FileWriteEvent(_Event):
    type: Literal["file.write"] = "file.write"
    path: str
    content: str


class FileDeleteEvent(_Event):
    type: Literal["file.delete"] = "file.delete"
    path: str


class ShellExecEvent(_Event):
    type: Literal["shell.exec"] = "shell.exec"
    command: str
    cwd: str | None = None


class StatusEvent(_Event):
    type: Literal["status"] = "status"
    stage: Literal["planning", "generating", "done", "cancelled"]
    note: str | None = None
    snapshot_id: str | None = None


class ErrorEvent(_Event):
    type: Literal["error"] = "error"
    message: str
    recoverable: bool = False


StreamEvent = Annotated[
    MessageDeltaEvent
    | FileWriteEvent
    | FileDeleteEvent
    | ShellExecEvent
    | StatusEvent
    | ErrorEvent,
    Field(discriminator="type"),
]


class GenerateRequest(BaseModel):
    """Request body for ``POST /v1/generate``."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=16000)
    history: list[dict[str, str]] | None = None
    retry: bool = False
    provider: Literal["openai", "gemini"] | None = None
    model: str | None = Field(default=None, max_length=128)
