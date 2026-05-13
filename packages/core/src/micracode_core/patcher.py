"""Apply :class:`PatchBundle` ops to project files."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from .schemas.codegen import (
    MAX_FILE_CONTENT_CHARS,
    PatchBundle,
    PatchFile,
    SearchReplace,
)
from .storage import safe_join

_FORBIDDEN_SEGMENTS = frozenset({".micracode", "node_modules", ".git"})
_VALIDATION_ROOT = Path("/tmp/.micracode-codegen-path-validation").resolve()

_CLIENT_DIRECTIVE_SUFFIXES = (".tsx", ".jsx", ".ts", ".js")
_SERVER_ONLY_APP_FILES = ("app/layout.tsx", "app/layout.jsx")

_CLIENT_ONLY_IMPORT_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"""from\s+['"]framer-motion['"]"""),
    re.compile(r"""from\s+['"]@?react-spring(?:/[\w-]+)?['"]"""),
)

_CLIENT_ONLY_HOOKS = (
    "useState",
    "useEffect",
    "useLayoutEffect",
    "useReducer",
    "useRef",
    "useContext",
    "useCallback",
    "useMemo",
    "useTransition",
    "useDeferredValue",
    "useSyncExternalStore",
    "useImperativeHandle",
)
_CLIENT_HOOK_RE = re.compile(
    r"\b(?:" + "|".join(_CLIENT_ONLY_HOOKS) + r")\s*\("
)

_USE_CLIENT_RE = re.compile(r"""^\s*['"]use client['"]\s*;?""")


def _needs_use_client(path: str, content: str) -> bool:
    if not path.endswith(_CLIENT_DIRECTIVE_SUFFIXES):
        return False
    if path.endswith(".d.ts"):
        return False
    if path in _SERVER_ONLY_APP_FILES:
        return False
    if _USE_CLIENT_RE.match(content):
        return False
    if any(rx.search(content) for rx in _CLIENT_ONLY_IMPORT_RES):
        return True
    if _CLIENT_HOOK_RE.search(content):
        return True
    return False


def _ensure_use_client(path: str, content: str) -> str:
    if not _needs_use_client(path, content):
        return content
    return '"use client";\n\n' + content.lstrip("\n")


class PatchError(Exception):
    """Raised when a search/replace op cannot be applied unambiguously."""


class FileLoader(Protocol):
    def __call__(self, path: str) -> str | None: ...


@dataclass
class ProjectContext:
    project_id: str
    tree_summary: str
    files: dict[str, str] = field(default_factory=dict)
    loader: FileLoader | None = None
    placeholder_files: frozenset[str] = field(default_factory=frozenset)

    def get_file(self, path: str) -> str | None:
        if path in self.files:
            return self.files[path]
        if self.loader is None:
            return None
        content = self.loader(path)
        if content is not None:
            self.files[path] = content
        return content


@dataclass(frozen=True)
class PatchResult:
    path: str
    kind: Literal["write", "delete", "error"]
    content: str = ""
    error: str = ""


def _normalize_path(raw: str) -> str | None:
    cleaned = raw.strip().replace("\\", "/").lstrip("/")
    return cleaned or None


def _path_is_safe(rel: str) -> bool:
    parts = Path(rel).parts
    if not parts or any(seg in _FORBIDDEN_SEGMENTS for seg in parts):
        return False
    try:
        safe_join(_VALIDATION_ROOT, rel)
    except ValueError:
        return False
    return True


def apply_patch(original: str, ops: list[SearchReplace]) -> str:
    buffer = original
    for idx, op in enumerate(ops):
        count = buffer.count(op.search)
        if count == 0:
            raise PatchError(f"edit #{idx + 1}: search string not found in file")
        if count > 1:
            raise PatchError(
                f"edit #{idx + 1}: search string matches {count} times, expected 1"
            )
        buffer = buffer.replace(op.search, op.replace, 1)
    return buffer


def _truncate(content: str) -> str:
    if len(content) > MAX_FILE_CONTENT_CHARS:
        return content[:MAX_FILE_CONTENT_CHARS]
    return content


def _apply_one(file: PatchFile, context: ProjectContext) -> PatchResult:
    rel = _normalize_path(file.path)
    if rel is None:
        return PatchResult(path=file.path, kind="error", error="empty path")
    if not _path_is_safe(rel):
        return PatchResult(path=rel, kind="error", error=f"unsafe path: {rel}")

    op = file.operation
    if op in ("create", "replace"):
        if file.content is None:
            return PatchResult(path=rel, kind="error", error=f"{op}: missing content")
        final = _ensure_use_client(rel, file.content)
        return PatchResult(path=rel, kind="write", content=_truncate(final))

    if op == "edit":
        current = context.get_file(rel)
        if current is None:
            return PatchResult(
                path=rel,
                kind="error",
                error=f"edit: file not found on disk: {rel}",
            )
        try:
            patched = apply_patch(current, file.edits or [])
        except PatchError as exc:
            return PatchResult(path=rel, kind="error", error=f"edit: {exc}")
        patched = _ensure_use_client(rel, patched)
        context.files[rel] = patched
        return PatchResult(path=rel, kind="write", content=_truncate(patched))

    if op == "delete":
        return PatchResult(path=rel, kind="delete")

    return PatchResult(path=rel, kind="error", error=f"unknown operation: {op}")


def apply_bundle(bundle: PatchBundle, context: ProjectContext) -> list[PatchResult]:
    return [_apply_one(f, context) for f in bundle.files]
