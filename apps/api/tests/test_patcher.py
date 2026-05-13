"""Tests for the pure patcher module (no LLM, no I/O)."""

from __future__ import annotations

import pytest

from micracode_api.agents.patcher import (
    PatchError,
    ProjectContext,
    apply_bundle,
    apply_patch,
)
from micracode_api.schemas.codegen import (
    MAX_FILE_CONTENT_CHARS,
    PatchBundle,
    PatchFile,
    SearchReplace,
)

# ---------------------------------------------------------------------------
# apply_patch — exact, single-occurrence semantics
# ---------------------------------------------------------------------------


def test_apply_patch_single_match() -> None:
    result = apply_patch(
        "hello world",
        [SearchReplace(search="world", replace="there")],
    )
    assert result == "hello there"


def test_apply_patch_zero_matches_raises() -> None:
    with pytest.raises(PatchError, match="not found"):
        apply_patch(
            "hello world",
            [SearchReplace(search="planet", replace="there")],
        )


def test_apply_patch_multi_match_raises() -> None:
    with pytest.raises(PatchError, match="matches 2 times"):
        apply_patch(
            "abc abc",
            [SearchReplace(search="abc", replace="xyz")],
        )


def test_apply_patch_applies_ops_sequentially() -> None:
    # The second op searches in the buffer already transformed by the first,
    # so "foo bar" becomes "xxx bar" becomes "xxx yyy".
    result = apply_patch(
        "foo bar",
        [
            SearchReplace(search="foo", replace="xxx"),
            SearchReplace(search="bar", replace="yyy"),
        ],
    )
    assert result == "xxx yyy"


def test_apply_patch_indentation_significant() -> None:
    # Indentation still must agree — line-trimmed fallback only forgives
    # trailing whitespace, not leading. A search with extra leading
    # indentation against a less-indented buffer is genuinely a miss.
    with pytest.raises(PatchError, match="not found"):
        apply_patch(
            "function f() {\n  return 1;\n}\n",
            [SearchReplace(search="    return 1;", replace="    return 2;")],
        )


def test_apply_patch_trailing_whitespace_tolerated() -> None:
    # Buffer has trailing spaces on an inner line; LLM-emitted search
    # omits them. Exact match fails (the newline boundary doesn't line up),
    # so the line-trimmed fallback should kick in.
    result = apply_patch(
        "let x = 1;   \nlet y = 2;\n",
        [
            SearchReplace(
                search="let x = 1;\nlet y = 2;",
                replace="let x = 10;\nlet y = 20;",
            )
        ],
    )
    assert result == "let x = 10;\nlet y = 20;\n"


def test_apply_patch_crlf_normalized() -> None:
    # Buffer uses CRLF line endings; LLM emits LF. The CRLF fallback
    # should normalize both sides and apply the edit.
    result = apply_patch(
        "line1\r\nline2\r\nline3\r\n",
        [SearchReplace(search="line2\n", replace="LINE2\n")],
    )
    assert result == "line1\nLINE2\nline3\n"


# ---------------------------------------------------------------------------
# apply_bundle — per-operation dispatch
# ---------------------------------------------------------------------------


def _ctx(files: dict[str, str] | None = None) -> ProjectContext:
    return ProjectContext(
        project_id="p",
        tree_summary="",
        files=dict(files or {}),
        loader=None,
    )


def test_apply_bundle_create_returns_write() -> None:
    bundle = PatchBundle(
        files=[PatchFile(path="app/page.tsx", operation="create", content="ok")]
    )
    results = apply_bundle(bundle, _ctx())
    assert len(results) == 1
    assert results[0].kind == "write"
    assert results[0].path == "app/page.tsx"
    assert results[0].content == "ok"


def test_apply_bundle_replace_returns_write() -> None:
    bundle = PatchBundle(
        files=[PatchFile(path="app/page.tsx", operation="replace", content="new")]
    )
    results = apply_bundle(bundle, _ctx({"app/page.tsx": "old"}))
    assert results[0].kind == "write"
    assert results[0].content == "new"


def test_apply_bundle_edit_applies_patch() -> None:
    bundle = PatchBundle(
        files=[
            PatchFile(
                path="app/page.tsx",
                operation="edit",
                edits=[SearchReplace(search="old", replace="new")],
            )
        ]
    )
    ctx = _ctx({"app/page.tsx": "hello old world"})
    results = apply_bundle(bundle, ctx)
    assert results[0].kind == "write"
    assert results[0].content == "hello new world"
    # Edit must also update the in-memory context so subsequent edits see it.
    assert ctx.files["app/page.tsx"] == "hello new world"


def test_apply_bundle_delete_returns_delete() -> None:
    bundle = PatchBundle(files=[PatchFile(path="obsolete.ts", operation="delete")])
    results = apply_bundle(bundle, _ctx({"obsolete.ts": "x"}))
    assert results[0].kind == "delete"
    assert results[0].path == "obsolete.ts"
    assert results[0].content == ""


def test_apply_bundle_edit_missing_file_is_error() -> None:
    bundle = PatchBundle(
        files=[
            PatchFile(
                path="nope.tsx",
                operation="edit",
                edits=[SearchReplace(search="x", replace="y")],
            )
        ]
    )
    results = apply_bundle(bundle, _ctx())
    assert results[0].kind == "error"
    assert "not found" in results[0].error


def test_apply_bundle_edit_patch_mismatch_is_error() -> None:
    bundle = PatchBundle(
        files=[
            PatchFile(
                path="app/page.tsx",
                operation="edit",
                edits=[SearchReplace(search="does-not-exist", replace="y")],
            )
        ]
    )
    results = apply_bundle(bundle, _ctx({"app/page.tsx": "something else"}))
    assert results[0].kind == "error"
    assert "edit:" in results[0].error


def test_apply_bundle_continues_after_failure() -> None:
    bundle = PatchBundle(
        files=[
            PatchFile(
                path="a.tsx",
                operation="edit",
                edits=[SearchReplace(search="missing", replace="y")],
            ),
            PatchFile(path="b.tsx", operation="create", content="ok"),
        ]
    )
    results = apply_bundle(bundle, _ctx({"a.tsx": "unrelated"}))
    assert [r.kind for r in results] == ["error", "write"]
    assert results[1].content == "ok"


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_path",
    [
        "node_modules/x.js",
        ".git/config",
        ".micracode/secret",
        "app/node_modules/nested.js",
        "../escape.tsx",
        "",
        "   ",
    ],
)
def test_apply_bundle_rejects_unsafe_paths(bad_path: str) -> None:
    bundle = PatchBundle.model_construct(
        files=[
            PatchFile.model_construct(
                path=bad_path, operation="create", content="x"
            )
        ]
    )
    results = apply_bundle(bundle, _ctx())
    assert results[0].kind == "error"


def test_apply_bundle_accepts_posix_paths() -> None:
    bundle = PatchBundle(
        files=[PatchFile(path="app/components/Nav.tsx", operation="create", content="x")]
    )
    results = apply_bundle(bundle, _ctx())
    assert results[0].kind == "write"
    assert results[0].path == "app/components/Nav.tsx"


def test_apply_bundle_normalizes_leading_slash() -> None:
    bundle = PatchBundle(
        files=[PatchFile(path="/app/page.tsx", operation="create", content="x")]
    )
    results = apply_bundle(bundle, _ctx())
    assert results[0].kind == "write"
    assert results[0].path == "app/page.tsx"


# ---------------------------------------------------------------------------
# Content size cap
# ---------------------------------------------------------------------------


def test_apply_bundle_truncates_oversized_content() -> None:
    big = "a" * (MAX_FILE_CONTENT_CHARS + 1_000)
    bundle = PatchBundle.model_construct(
        files=[
            PatchFile.model_construct(
                path="app/big.tsx", operation="create", content=big
            )
        ]
    )
    results = apply_bundle(bundle, _ctx())
    assert results[0].kind == "write"
    assert len(results[0].content) == MAX_FILE_CONTENT_CHARS


# ---------------------------------------------------------------------------
# Lazy loader fallback when a file is missing from context.files
# ---------------------------------------------------------------------------


def test_project_context_loader_is_used_when_file_missing() -> None:
    calls: list[str] = []

    def loader(path: str) -> str | None:
        calls.append(path)
        if path == "app/page.tsx":
            return "hello old"
        return None

    ctx = ProjectContext(
        project_id="p", tree_summary="", files={}, loader=loader
    )
    bundle = PatchBundle(
        files=[
            PatchFile(
                path="app/page.tsx",
                operation="edit",
                edits=[SearchReplace(search="old", replace="new")],
            )
        ]
    )
    results = apply_bundle(bundle, ctx)
    assert results[0].kind == "write"
    assert results[0].content == "hello new"
    assert calls == ["app/page.tsx"]
