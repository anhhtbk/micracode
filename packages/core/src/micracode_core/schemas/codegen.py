"""Structured LLM output for the codegen orchestrator."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_CODEGEN_FILES = 10
MAX_FILE_CONTENT_CHARS = 80_000
MAX_PATH_CHARS = 512
MAX_SEARCH_REPLACE_CHARS = 8_000
MAX_EDITS_PER_FILE = 20

Operation = Literal["create", "replace", "edit", "delete"]


class SearchReplace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search: str = Field(
        min_length=1,
        max_length=MAX_SEARCH_REPLACE_CHARS,
        description=(
            "Exact substring (whitespace-significant) of the existing file. "
            "Must occur exactly once; otherwise the edit is rejected."
        ),
    )
    replace: str = Field(
        max_length=MAX_SEARCH_REPLACE_CHARS,
        description="Text that replaces ``search``. May be empty to delete.",
    )


class PatchFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        max_length=MAX_PATH_CHARS,
        description="Relative path under the project root (POSIX, no '..').",
    )
    operation: Operation = Field(
        description="File-level op. Use 'edit' for small changes to existing files.",
    )
    content: str | None = Field(
        default=None,
        max_length=MAX_FILE_CONTENT_CHARS,
        description="Full file contents for 'create' / 'replace'. Forbidden otherwise.",
    )
    edits: list[SearchReplace] | None = Field(
        default=None,
        max_length=MAX_EDITS_PER_FILE,
        description="Sequential search/replace ops for 'edit'. Forbidden otherwise.",
    )

    @model_validator(mode="after")
    def _check_operation_fields(self) -> PatchFile:
        op = self.operation
        if op in ("create", "replace"):
            if self.content is None:
                raise ValueError(f"operation={op!r} requires 'content'")
            if self.edits is not None:
                raise ValueError(f"operation={op!r} forbids 'edits'")
        elif op == "edit":
            if not self.edits:
                raise ValueError("operation='edit' requires non-empty 'edits'")
            if self.content is not None:
                raise ValueError("operation='edit' forbids 'content'")
        elif op == "delete":
            if self.content is not None or self.edits is not None:
                raise ValueError("operation='delete' forbids 'content' and 'edits'")
        return self


class PatchBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[PatchFile] = Field(
        max_length=MAX_CODEGEN_FILES,
        description=(
            "Files to create, replace, edit, or delete. Use 'replace' when "
            "rewriting a placeholder scaffold or most of a file; use 'edit' "
            "only for surgical tweaks whose search strings exist verbatim in "
            "the file body shown in context."
        ),
    )
