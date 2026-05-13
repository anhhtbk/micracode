"""Project-related request/response schemas (local filesystem build)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PromptRole = Literal["user", "assistant", "system", "tool"]


class DeploymentRecord(BaseModel):
    """One Vercel deployment for a project.

    Vercel keeps every deployment immutable + addressable forever at its
    own permalink (``url``). The shared production alias (``alias_url``)
    only ever points at one deployment at a time; ``is_current_production``
    is our local cache of which row that currently is.
    """

    id: str = Field(description="Vercel deployment id (e.g. dpl_xxx).")
    url: str = Field(description="Immutable permalink for this version.")
    alias_url: str | None = Field(
        default=None,
        description="Shared production alias (only meaningful when promoted).",
    )
    target: Literal["production", "preview"] = "production"
    created_at: datetime
    is_current_production: bool = False


class ProjectRecord(BaseModel):
    """Metadata describing a single project folder under ``opener-apps/``."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(
        description="URL-safe slug; also the on-disk folder name.",
        pattern=r"^[a-z0-9][a-z0-9-]{0,62}$",
    )
    name: str = Field(min_length=1, max_length=120)
    template: str = "next"
    created_at: datetime
    updated_at: datetime
    # Vercel state. Lazily filled on first deploy; once set it pins the
    # project to a single Vercel project so re-deploys reuse the same
    # production URL. Listed in chronological order (oldest first).
    vercel_project_name: str | None = None
    deployments: list[DeploymentRecord] = Field(default_factory=list)


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    template: str = "next"


class UpdateProjectFileRequest(BaseModel):
    """Body for ``PUT /v1/projects/{id}/files`` — upserts one text file."""

    path: str = Field(min_length=1, max_length=1024)
    content: str = ""


class PromptRecord(BaseModel):
    id: str
    role: PromptRole
    content: str
    created_at: datetime
    # Snapshot captured immediately before the turn that produced this
    # record ran; lets the UI offer "revert to before this message".
    # Optional so historical ``prompts.jsonl`` rows remain valid.
    snapshot_id: str | None = None


class SnapshotRecord(BaseModel):
    """Metadata for a single on-disk project snapshot.

    Snapshots are filesystem copies of the project tree (excluding the
    sidecar + build output) captured just before a codegen turn starts,
    so the user can roll back a bad generation.
    """

    id: str = Field(
        description="Snapshot id; also the on-disk folder name under .micracode/snapshots/.",
        pattern=r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{4}$",
    )
    created_at: datetime
    user_prompt: str = ""
    kind: Literal["pre-turn"] = "pre-turn"
