"""Project-related schemas (local filesystem build)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PromptRole = Literal["user", "assistant", "system", "tool"]


class ProjectRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str = Field(
        description="URL-safe slug; also the on-disk folder name.",
        pattern=r"^[a-z0-9][a-z0-9-]{0,62}$",
    )
    name: str = Field(min_length=1, max_length=120)
    template: str = "next"
    created_at: datetime
    updated_at: datetime


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    template: str = "next"


class UpdateProjectFileRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    content: str = ""


class PromptRecord(BaseModel):
    id: str
    role: PromptRole
    content: str
    created_at: datetime
    snapshot_id: str | None = None


class SnapshotRecord(BaseModel):
    id: str = Field(
        description="Snapshot id; also the on-disk folder name under .micracode/snapshots/.",
        pattern=r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{4}$",
    )
    created_at: datetime
    user_prompt: str = ""
    kind: Literal["pre-turn"] = "pre-turn"
