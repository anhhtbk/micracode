"""Tests for the custom codegen orchestrator (formerly the LangGraph graph)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from micracode_api.agents.context import load_context
from micracode_api.agents.patcher import ProjectContext
from micracode_api.config import get_settings
from micracode_api.schemas.codegen import (
    PatchBundle,
    PatchFile,
    SearchReplace,
)
from micracode_api.schemas.project import PromptRecord
from micracode_api.starter.next_default import NEXT_STARTER_FILES
from micracode_api.storage import Storage


def _pr(role: str, content: str, *, idx: int = 0) -> PromptRecord:
    """Build a PromptRecord for tests with a deterministic timestamp."""
    return PromptRecord(
        id=f"id-{idx}",
        role=role,  # type: ignore[arg-type]
        content=content,
        created_at=datetime(2026, 1, 1, 0, 0, idx, tzinfo=UTC),
    )


def _make_mock_llm(plan_text: str, bundle: PatchBundle) -> MagicMock:
    """Mock LLM whose ``ainvoke`` returns plan text first, then bundle JSON.

    The orchestrator parses JSON out of the second ``ainvoke`` response, so we
    serialize ``bundle`` to its JSON form instead of relying on
    ``with_structured_output`` (which the orchestrator no longer uses).
    """
    bundle_json = bundle.model_dump_json()
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        side_effect=[
            AIMessage(content=plan_text),
            AIMessage(content=bundle_json),
        ]
    )
    return mock_llm


# ---------------------------------------------------------------------------
# History threading
# ---------------------------------------------------------------------------


def test_history_to_messages_maps_roles_in_order() -> None:
    from micracode_api.agents.orchestrator import _history_to_messages

    records = [
        _pr("user", "build a todo app", idx=1),
        _pr("assistant", "built it", idx=2),
        _pr("user", "make bg white", idx=3),
    ]
    msgs = _history_to_messages(records)
    assert [type(m).__name__ for m in msgs] == [
        "HumanMessage",
        "AIMessage",
        "HumanMessage",
    ]
    assert [m.content for m in msgs] == [
        "build a todo app",
        "built it",
        "make bg white",
    ]


def test_history_to_messages_drops_non_chat_roles() -> None:
    from micracode_api.agents.orchestrator import _history_to_messages

    records = [
        _pr("user", "hi", idx=1),
        _pr("system", "ignored", idx=2),
        _pr("tool", "ignored", idx=3),
        _pr("assistant", "hello", idx=4),
    ]
    msgs = _history_to_messages(records)
    assert len(msgs) == 2
    assert [m.content for m in msgs] == ["hi", "hello"]


def test_history_to_messages_caps_by_turn_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from micracode_api.agents import orchestrator as orch

    monkeypatch.setattr(orch, "HISTORY_TURN_CAP", 3)
    monkeypatch.setattr(orch, "HISTORY_CHAR_CAP", 10_000)
    records = [_pr("user", f"msg{i}", idx=i) for i in range(10)]
    msgs = orch._history_to_messages(records)
    assert [m.content for m in msgs] == ["msg7", "msg8", "msg9"]


def test_history_to_messages_caps_by_char_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from micracode_api.agents import orchestrator as orch

    monkeypatch.setattr(orch, "HISTORY_TURN_CAP", 1000)
    monkeypatch.setattr(orch, "HISTORY_CHAR_CAP", 10)
    records = [
        _pr("user", "aaaaa", idx=1),
        _pr("user", "bbbbb", idx=2),
        _pr("user", "ccccc", idx=3),
    ]
    msgs = orch._history_to_messages(records)
    assert [m.content for m in msgs] == ["bbbbb", "ccccc"]


def test_history_to_messages_handles_empty() -> None:
    from micracode_api.agents.orchestrator import _history_to_messages

    assert _history_to_messages(None) == []
    assert _history_to_messages([]) == []


# ---------------------------------------------------------------------------
# No API key -> single ErrorEvent, no crash
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_emits_error_event_when_no_api_key(
    monkeypatch: pytest.MonkeyPatch, storage: Storage
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GOOGLE_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()

    from micracode_api.agents import orchestrator as orch

    storage.create_project("p5")
    try:
        events = [
            evt
            async for evt in orch.run_codegen_stream(project_id="p5", prompt="hi", storage=storage)
        ]
    finally:
        get_settings.cache_clear()

    types = [e.type for e in events]
    assert "error" in types
    assert types[0] == "status"
    error = next(e for e in events if e.type == "error")
    assert "API_KEY" in error.message
    # Must not emit a done status after an error.
    assert not any(e.type == "status" and getattr(e, "stage", None) == "done" for e in events)


# ---------------------------------------------------------------------------
# Planner failure -> wrapped CodegenError -> non-recoverable ErrorEvent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_planner_exception_surfaced(
    monkeypatch: pytest.MonkeyPatch, storage: Storage
) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    get_settings.cache_clear()

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=RuntimeError("boom"))

    from micracode_api.agents import orchestrator as orch

    monkeypatch.setattr(orch, "build_llm", lambda provider, model: mock_llm)

    storage.create_project("p4")
    try:
        events = [
            evt
            async for evt in orch.run_codegen_stream(project_id="p4", prompt="x", storage=storage)
        ]
    finally:
        get_settings.cache_clear()

    errors = [e for e in events if e.type == "error"]
    assert errors, "expected at least one ErrorEvent"
    assert "planner failed" in errors[0].message
    assert errors[0].recoverable is False


# ---------------------------------------------------------------------------
# Per-request model override: explicit provider/model is threaded into build_llm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_threads_explicit_provider_and_model(
    monkeypatch: pytest.MonkeyPatch, storage: Storage
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("GOOGLE_API_KEY", "")
    get_settings.cache_clear()

    storage.create_project("p-override")

    bundle = PatchBundle(
        files=[PatchFile(path="app/page.tsx", operation="create", content="// ok\n")]
    )
    mock_llm = _make_mock_llm("plan", bundle)

    from micracode_api.agents import orchestrator as orch

    calls: list[tuple[str, str]] = []

    def fake_build(provider: str, model: str):
        calls.append((provider, model))
        return mock_llm

    monkeypatch.setattr(orch, "build_llm", fake_build)

    try:
        events = [
            evt
            async for evt in orch.run_codegen_stream(
                project_id="p-override",
                prompt="hello",
                storage=storage,
                provider="openai",
                model="gpt-4.1",
            )
        ]
    finally:
        get_settings.cache_clear()

    # Both planner and codegen phases built an LLM with the same override pair.
    assert calls and all(c == ("openai", "gpt-4.1") for c in calls)
    assert any(e.type == "file.write" for e in events)


@pytest.mark.asyncio
async def test_stream_rejects_unknown_model(
    monkeypatch: pytest.MonkeyPatch, storage: Storage
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    get_settings.cache_clear()

    storage.create_project("p-unknown")

    from micracode_api.agents import orchestrator as orch

    try:
        events = [
            evt
            async for evt in orch.run_codegen_stream(
                project_id="p-unknown",
                prompt="hi",
                storage=storage,
                provider="openai",
                model="gpt-9000",
            )
        ]
    finally:
        get_settings.cache_clear()

    errors = [e for e in events if e.type == "error"]
    assert errors and "Unknown model" in errors[0].message
    assert errors[0].recoverable is False


# ---------------------------------------------------------------------------
# End-to-end: create op against a fresh project
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_end_to_end_create(monkeypatch: pytest.MonkeyPatch, storage: Storage) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    get_settings.cache_clear()

    storage.create_project("p-create")

    bundle = PatchBundle(
        files=[
            PatchFile(
                path="app/page.tsx",
                operation="create",
                content="export default function Page() { return null; }\n",
            )
        ]
    )
    mock_llm = _make_mock_llm("1) Create app/page.tsx.", bundle)

    from micracode_api.agents import orchestrator as orch

    monkeypatch.setattr(orch, "build_llm", lambda provider, model: mock_llm)

    try:
        events = [
            evt
            async for evt in orch.run_codegen_stream(
                project_id="p-create", prompt="empty page", storage=storage
            )
        ]
    finally:
        get_settings.cache_clear()

    stages = [getattr(e, "stage", None) for e in events if e.type == "status"]
    assert "planning" in stages
    assert "generating" in stages
    assert "done" in stages

    writes = [e for e in events if e.type == "file.write"]
    assert len(writes) == 1
    assert writes[0].path == "app/page.tsx"
    assert "export default" in writes[0].content

    deltas = [e for e in events if e.type == "message.delta"]
    assert deltas and "Create" in deltas[0].content


# ---------------------------------------------------------------------------
# End-to-end: edit op against a seeded project applies search/replace server-side
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_end_to_end_edit_applies_patch(
    monkeypatch: pytest.MonkeyPatch, storage: Storage
) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    get_settings.cache_clear()

    storage.create_project("p-edit")
    # Seed an existing file we'll edit. Overwrites whatever the starter wrote.
    storage.write_file(
        "p-edit",
        "app/page.tsx",
        'export default function Page() { return <div className="bg-black">hi</div>; }\n',
    )

    bundle = PatchBundle(
        files=[
            PatchFile(
                path="app/page.tsx",
                operation="edit",
                edits=[SearchReplace(search="bg-black", replace="bg-white")],
            )
        ]
    )
    mock_llm = _make_mock_llm("1) Swap bg-black for bg-white.", bundle)

    from micracode_api.agents import orchestrator as orch

    monkeypatch.setattr(orch, "build_llm", lambda provider, model: mock_llm)

    try:
        events = [
            evt
            async for evt in orch.run_codegen_stream(
                project_id="p-edit",
                prompt="change the background to white",
                storage=storage,
            )
        ]
    finally:
        get_settings.cache_clear()

    writes = [e for e in events if e.type == "file.write"]
    assert len(writes) == 1
    assert writes[0].path == "app/page.tsx"
    assert "bg-white" in writes[0].content
    assert "bg-black" not in writes[0].content


# ---------------------------------------------------------------------------
# Edit whose search string does not match -> recoverable error, no crash
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_patch_mismatch_is_recoverable(
    monkeypatch: pytest.MonkeyPatch, storage: Storage
) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    get_settings.cache_clear()

    storage.create_project("p-mismatch")
    storage.write_file("p-mismatch", "app/page.tsx", "hello world\n")

    bundle = PatchBundle(
        files=[
            PatchFile(
                path="app/page.tsx",
                operation="edit",
                edits=[SearchReplace(search="does-not-exist", replace="x")],
            ),
            PatchFile(path="app/extra.tsx", operation="create", content="export const x = 1;"),
        ]
    )
    mock_llm = _make_mock_llm("edit + add", bundle)

    from micracode_api.agents import orchestrator as orch

    monkeypatch.setattr(orch, "build_llm", lambda provider, model: mock_llm)

    try:
        events = [
            evt
            async for evt in orch.run_codegen_stream(
                project_id="p-mismatch", prompt="x", storage=storage
            )
        ]
    finally:
        get_settings.cache_clear()

    errors = [e for e in events if e.type == "error"]
    writes = [e for e in events if e.type == "file.write"]
    # The mismatched edit becomes a recoverable error; the other op still runs.
    assert any(e.recoverable for e in errors)
    assert any(w.path == "app/extra.tsx" for w in writes)
    # Stream still completes with a done status.
    assert any(e.type == "status" and getattr(e, "stage", None) == "done" for e in events)


# ---------------------------------------------------------------------------
# History threading: PromptRecord -> LangChain messages reaches the LLM calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_threads_history_to_planner_and_codegen(
    monkeypatch: pytest.MonkeyPatch, storage: Storage
) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    get_settings.cache_clear()

    storage.create_project("p-hist")

    bundle = PatchBundle(
        files=[PatchFile(path="app/page.tsx", operation="create", content="// ok\n")]
    )
    mock_llm = _make_mock_llm("plan", bundle)

    from micracode_api.agents import orchestrator as orch

    monkeypatch.setattr(orch, "build_llm", lambda provider, model: mock_llm)

    history = [
        _pr("user", "earlier turn", idx=1),
        _pr("assistant", "earlier reply", idx=2),
    ]

    try:
        events = [
            evt
            async for evt in orch.run_codegen_stream(
                project_id="p-hist",
                prompt="now do this",
                history=history,
                storage=storage,
            )
        ]
    finally:
        get_settings.cache_clear()

    # Both LLM calls received the system prompt, then the history turns, then
    # a HumanMessage with the current prompt.
    assert mock_llm.ainvoke.await_count == 2
    planner_messages = mock_llm.ainvoke.await_args_list[0][0][0]
    assert isinstance(planner_messages[0], SystemMessage)
    assert [type(m).__name__ for m in planner_messages[1:3]] == [
        "HumanMessage",
        "AIMessage",
    ]
    assert planner_messages[1].content == "earlier turn"
    assert planner_messages[2].content == "earlier reply"
    assert isinstance(planner_messages[-1], HumanMessage)
    assert "now do this" in planner_messages[-1].content

    codegen_messages = mock_llm.ainvoke.await_args_list[1][0][0]
    assert isinstance(codegen_messages[0], SystemMessage)
    assert [type(m).__name__ for m in codegen_messages[1:3]] == [
        "HumanMessage",
        "AIMessage",
    ]

    # Confirm the stream actually produced a file write too.
    assert any(e.type == "file.write" for e in events)


# ---------------------------------------------------------------------------
# Placeholder detection on the default Next starter
# ---------------------------------------------------------------------------


def test_load_context_flags_unmodified_starter_as_placeholder(
    storage: Storage,
) -> None:
    """A freshly-created Next project should mark its placeholder pages."""
    storage.create_project("p-placeholder")

    ctx = load_context(storage, "p-placeholder", prompt="create a landing page")

    assert "app/page.tsx" in ctx.placeholder_files
    assert "app/layout.tsx" in ctx.placeholder_files
    # The loaded body matches the starter byte-for-byte.
    assert ctx.files["app/page.tsx"] == NEXT_STARTER_FILES["app/page.tsx"]


def test_load_context_does_not_flag_modified_files(storage: Storage) -> None:
    """Once the user edits a starter file, it is no longer a placeholder."""
    storage.create_project("p-modified")
    storage.write_file("p-modified", "app/page.tsx", "export default () => null;\n")

    ctx = load_context(storage, "p-modified", prompt="tweak the page")

    assert "app/page.tsx" not in ctx.placeholder_files
    # layout.tsx is still untouched, so it still counts as placeholder.
    assert "app/layout.tsx" in ctx.placeholder_files


def test_render_context_block_surfaces_placeholder_hint() -> None:
    """Placeholder files should be called out so the model picks `replace`."""
    from micracode_api.agents.orchestrator import _render_context_block

    ctx = ProjectContext(
        project_id="p",
        tree_summary="app/page.tsx (42)",
        files={"app/page.tsx": NEXT_STARTER_FILES["app/page.tsx"]},
        placeholder_files=frozenset({"app/page.tsx"}),
    )

    rendered = _render_context_block(ctx)

    assert "placeholder" in rendered.lower()
    assert "replace" in rendered.lower()
    assert "app/page.tsx" in rendered
    # The per-file marker is also present.
    assert "app/page.tsx (placeholder scaffold)" in rendered


def test_render_context_block_omits_hint_when_no_placeholders() -> None:
    """Without placeholders, the hint line must not be emitted."""
    from micracode_api.agents.orchestrator import _render_context_block

    ctx = ProjectContext(
        project_id="p",
        tree_summary="app/page.tsx (99)",
        files={"app/page.tsx": "export default () => null;\n"},
        placeholder_files=frozenset(),
    )

    rendered = _render_context_block(ctx)

    assert "placeholder" not in rendered.lower()
    assert "(placeholder scaffold)" not in rendered
