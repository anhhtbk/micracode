"""Apply :class:`PatchBundle` ops to project files.

Pure functions, no LLM calls. File I/O is delegated to a ``ProjectContext``
object so tests can substitute an in-memory store. The router ultimately
persists the results via ``storage.write_file`` / ``storage.delete_file``;
this module just produces the sanitized list of results to stream.

Patch rule: each ``search`` must appear EXACTLY ONCE in the current buffer.
This is Aider's rule and it's what makes search/replace reliable — failed
matches are preferable to silent wrong edits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from ..schemas.codegen import (
    MAX_FILE_CONTENT_CHARS,
    PatchBundle,
    PatchFile,
    SearchReplace,
)
from ..storage import safe_join

_FORBIDDEN_SEGMENTS = frozenset({".micracode", "node_modules", ".git"})
_VALIDATION_ROOT = Path("/tmp/.micracode-codegen-path-validation").resolve()

# Suffixes we'll consider for the client-directive safety net. Excludes
# `.d.ts` (type-only) and config files like `next.config.mjs`.
_CLIENT_DIRECTIVE_SUFFIXES = (".tsx", ".jsx", ".ts", ".js")

# Files inside `app/` that MUST stay server components — adding "use client"
# to them is a bug, not a fix. Layouts can technically be client too, but
# the metadata export pattern in our starter requires server.
_SERVER_ONLY_APP_FILES = ("app/layout.tsx", "app/layout.jsx")

# Client-only library imports. If a file imports any of these, it must
# start with the "use client" directive or Next's RSC bundler will throw
# "Could not find the module ... in the React Client Manifest".
_CLIENT_ONLY_IMPORT_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"""from\s+['"]framer-motion['"]"""),
    re.compile(r"""from\s+['"]@?react-spring(?:/[\w-]+)?['"]"""),
)

# React hooks that only work in client components. Matched as `<hook>(`
# to avoid catching identifiers that merely contain the word.
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
    """True if a file should carry the ``"use client"`` directive but doesn't.

    Conservative: only flips when we see an unambiguous client-only signal
    (framer-motion import or a React client-only hook call). Skips files
    that already have the directive and any path on the server-only list.
    """
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
    """Prepend ``"use client";`` when :func:`_needs_use_client` says so."""
    if not _needs_use_client(path, content):
        return content
    return '"use client";\n\n' + content.lstrip("\n")


class PatchError(Exception):
    """Raised when a search/replace op cannot be applied unambiguously."""


class FileLoader(Protocol):
    """Minimal protocol for reading current file contents by relative path.

    Returns ``None`` if the file does not exist or cannot be decoded as text.
    Concrete implementations are in :mod:`agents.context`.
    """

    def __call__(self, path: str) -> str | None: ...


@dataclass
class ProjectContext:
    """Snapshot of a project's files plus a lazy loader for anything extra.

    ``files`` is pre-populated by :mod:`agents.context` with the files we
    think are relevant to the current prompt; anything the model touches
    that isn't already in ``files`` is fetched on demand via ``loader``.

    ``placeholder_files`` lists any paths in ``files`` whose contents still
    match the starter scaffold byte-for-byte. These are safe (and usually
    correct) to ``replace`` wholesale rather than ``edit`` — trying to
    search/replace against a tiny placeholder almost always causes the
    model to hallucinate search strings that do not exist in the buffer.
    """

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
    """One applied (or failed) file op, ready to emit as a stream event."""

    path: str
    kind: Literal["write", "delete", "error"]
    content: str = ""
    error: str = ""


def _normalize_path(raw: str) -> str | None:
    """Trim, convert to POSIX, strip leading slash. Returns ``None`` if empty."""
    cleaned = raw.strip().replace("\\", "/").lstrip("/")
    return cleaned or None


def _path_is_safe(rel: str) -> bool:
    """Block forbidden segments and traversal via a placeholder-root check."""
    parts = Path(rel).parts
    if not parts or any(seg in _FORBIDDEN_SEGMENTS for seg in parts):
        return False
    try:
        safe_join(_VALIDATION_ROOT, rel)
    except ValueError:
        return False
    return True


def _line_trimmed_match(buffer: str, search: str) -> tuple[int, int] | None:
    """Find ``search`` in ``buffer`` ignoring per-line trailing whitespace.

    Splits both sides into lines, compares each line with ``rstrip()``.
    Returns ``(start, end)`` byte offsets in ``buffer`` if exactly one
    contiguous block of buffer lines matches the sequence of search
    lines; otherwise ``None``.

    This is the conservative Aider-style fuzzy match: indentation must
    still agree (no leading-whitespace stripping), but stray trailing
    spaces or CR characters from the model won't break the edit.
    """
    buf_lines = buffer.splitlines(keepends=True)
    raw_search_lines = search.splitlines(keepends=True)
    if not raw_search_lines:
        return None

    search_norm = [ln.rstrip() for ln in raw_search_lines]
    n = len(search_norm)

    matches: list[int] = []
    for i in range(len(buf_lines) - n + 1):
        if all(buf_lines[i + j].rstrip() == search_norm[j] for j in range(n)):
            matches.append(i)
            if len(matches) > 1:
                return None
    if len(matches) != 1:
        return None

    start_line = matches[0]
    start = sum(len(buf_lines[k]) for k in range(start_line))
    end = start + sum(len(buf_lines[start_line + k]) for k in range(n))

    # If the search didn't end with a newline, the model intended to
    # match up to (but not including) the last line's terminator. Keep
    # that newline outside the replaced span so the file ending stays.
    last_search_line = raw_search_lines[-1]
    last_buf_line = buf_lines[start_line + n - 1]
    if not last_search_line.endswith(("\n", "\r")) and last_buf_line.endswith("\n"):
        trailing = 2 if last_buf_line.endswith("\r\n") else 1
        end -= trailing
    return start, end


def _apply_one_op(buffer: str, op: SearchReplace, idx: int) -> str:
    """Apply one search/replace, with CRLF + line-trimmed fallbacks.

    Strategy, in order: exact match (the contract); CRLF-normalized exact
    match (LLMs sometimes emit ``\\n`` against a ``\\r\\n`` buffer or vice
    versa); line-trimmed match (per-line ``rstrip``). Any fallback that
    succeeds with exactly one match is accepted; otherwise raise.
    """
    count = buffer.count(op.search)
    if count == 1:
        return buffer.replace(op.search, op.replace, 1)
    if count > 1:
        raise PatchError(
            f"edit #{idx + 1}: search string matches {count} times, expected 1"
        )

    # CRLF normalization fallback (both directions).
    nl_buffer = buffer.replace("\r\n", "\n")
    nl_search = op.search.replace("\r\n", "\n")
    if op.search != nl_search or buffer != nl_buffer:
        nl_count = nl_buffer.count(nl_search)
        if nl_count == 1:
            nl_replace = op.replace.replace("\r\n", "\n")
            return nl_buffer.replace(nl_search, nl_replace, 1)
        if nl_count > 1:
            raise PatchError(
                f"edit #{idx + 1}: search string matches {nl_count} times "
                "after CRLF normalization, expected 1"
            )

    # Line-trimmed fallback (operates on the normalized buffer so a
    # successful replace returns LF-only text — consistent with the
    # CRLF branch above).
    span = _line_trimmed_match(nl_buffer, nl_search)
    if span is not None:
        start, end = span
        nl_replace = op.replace.replace("\r\n", "\n")
        return nl_buffer[:start] + nl_replace + nl_buffer[end:]

    raise PatchError(f"edit #{idx + 1}: search string not found in file")


def apply_patch(original: str, ops: list[SearchReplace]) -> str:
    """Apply ``ops`` sequentially to ``original``.

    Each op's ``search`` should occur exactly once in the *current* buffer
    (i.e. after previous ops in the same list were applied). Exact match
    is tried first; if it fails we try CRLF normalization and a
    per-line-trimmed match. On 0 or >1 matches after all fallbacks,
    :class:`PatchError` is raised and the caller should skip the whole
    file — partial edits are worse than no edits for the agent.
    """
    buffer = original
    for idx, op in enumerate(ops):
        buffer = _apply_one_op(buffer, op, idx)
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
    """Produce one :class:`PatchResult` per file in ``bundle`` order.

    Never raises: individual failures are reported as ``kind='error'`` so the
    stream can continue and the model can fix the mistake on the next turn.
    """
    return [_apply_one(f, context) for f in bundle.files]
