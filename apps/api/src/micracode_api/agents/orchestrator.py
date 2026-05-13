"""Plain-Python orchestrator for the codegen loop (no LangGraph).

One async generator, two LLM calls (plan, codegen), one patch-apply pass.
State flows as function arguments; events are ``yield``-ed to the SSE
router. File writes and deletes are persisted to storage here, before
the matching event is yielded, so storage and the client tree stay in sync.

History threading (``_history_to_messages``) and ``CodegenError`` are
preserved here so router code and tests can keep importing them through
``agents.orchestrator`` without churn.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator

import httpx

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import ValidationError

from ..config import get_settings
from ..schemas.codegen import PatchBundle
from ..schemas.project import PromptRecord
from ..schemas.stream import (
    ErrorEvent,
    FileDeleteEvent,
    FileWriteEvent,
    MessageDeltaEvent,
    StatusEvent,
    StreamEvent,
)
from ..storage import Storage, get_storage
from . import model_catalog
from .context import load_context
from .llm import LLMFactory
from .patcher import ProjectContext, apply_bundle
from .prompts import CODEGEN_SYSTEM_PROMPT, PLANNER_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


def _missing_api_key_message(provider: str | None = None) -> str:
    """Build a provider-aware error message for a missing API key."""
    resolved = provider or get_settings().llm_provider
    env_var = "OPENAI_API_KEY" if resolved == "openai" else "GOOGLE_API_KEY"
    return f"Server is not configured with a {env_var}; cannot generate code."


def build_llm(provider: str, model: str) -> BaseChatModel:
    """Seam used by ``_plan`` / ``_codegen``; tests monkeypatch this."""
    return LLMFactory.build(provider=provider, model=model)


# Bounds for prior-turn context. Mirrors the previous graph-based limits.
HISTORY_TURN_CAP = 20
HISTORY_CHAR_CAP = 12_000

# Truncate per-file bodies we send back to the model for edit ops so a
# single huge file cannot blow the context window.
CONTEXT_FILE_DISPLAY_CAP = 12_000


class CodegenError(RuntimeError):
    """Raised when the LLM cannot produce a usable code bundle."""


def _history_to_messages(
    records: list[PromptRecord] | None,
) -> list[BaseMessage]:
    """Convert persisted prompts into LangChain messages, bounded in size.

    Keeps only ``user``/``assistant`` turns (drops ``system``/``tool``).
    Iterates from the tail so the most recent context is preserved, then
    reverses back into chronological order. Stops when either
    :data:`HISTORY_TURN_CAP` or :data:`HISTORY_CHAR_CAP` is hit.
    """
    if not records:
        return []

    selected: list[BaseMessage] = []
    total_chars = 0
    for rec in reversed(records):
        if rec.role == "user":
            msg: BaseMessage = HumanMessage(content=rec.content)
        elif rec.role == "assistant":
            msg = AIMessage(content=rec.content)
        else:
            continue
        next_chars = total_chars + len(rec.content)
        if selected and (len(selected) >= HISTORY_TURN_CAP or next_chars > HISTORY_CHAR_CAP):
            break
        selected.append(msg)
        total_chars = next_chars

    selected.reverse()
    return selected


def _render_context_block(context: ProjectContext) -> str:
    """Format the project snapshot for inclusion in the user message."""
    if not context.tree_summary and not context.files:
        return "Current project: (empty — this is the first turn)."

    parts: list[str] = []
    parts.append("Current project files (path (size in chars)):")
    parts.append(context.tree_summary or "(no files yet)")

    if context.placeholder_files:
        listed = ", ".join(sorted(context.placeholder_files))
        parts.append("")
        parts.append(
            "These files still hold unmodified starter-scaffold placeholder "
            f"content and should be overwritten with `replace` (not `edit`) "
            f"when the user asks for any substantive change: {listed}."
        )

    if context.files:
        parts.append("")
        parts.append("Contents of the most relevant files:")
        for path, body in context.files.items():
            display = body
            if len(display) > CONTEXT_FILE_DISPLAY_CAP:
                display = display[:CONTEXT_FILE_DISPLAY_CAP] + "\n/* ... truncated ... */"
            marker = " (placeholder scaffold)" if path in context.placeholder_files else ""
            parts.append(f"\n----- {path}{marker} -----\n{display}")

    return "\n".join(parts)


async def _plan(
    prompt: str,
    history: list[BaseMessage],
    context: ProjectContext,
    *,
    provider: str,
    model: str,
) -> str:
    """Run the planner LLM call and return the plan text."""
    try:
        llm = build_llm(provider, model)
        msg = await llm.ainvoke(
            [
                SystemMessage(content=PLANNER_SYSTEM_PROMPT),
                *history,
                HumanMessage(
                    content=(
                        f"{_render_context_block(context)}\n\nUser request:\n{prompt or '(empty)'}"
                    )
                ),
            ]
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("planner LLM call failed")
        raise CodegenError(f"planner failed: {exc}") from exc

    plan_text = msg.content.strip() if isinstance(msg.content, str) else ""
    if not plan_text:
        raise CodegenError("planner returned empty response")
    return plan_text


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def _extract_json_object(text: str) -> str:
    """Pull a JSON object out of an LLM response.

    Handles three shapes: bare JSON, ```json fenced JSON, and prose with a
    JSON object embedded. Needed because OpenAI-compatible shims for Claude
    or local models often ignore tool-calling and return text instead, which
    breaks ``with_structured_output``.
    """
    s = (text or "").strip()
    if not s:
        raise ValueError("empty model response")

    if s.startswith("{") and s.endswith("}"):
        return s

    m = _JSON_FENCE_RE.search(s)
    if m:
        return m.group(1).strip()

    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end > start:
        return s[start : end + 1]

    raise ValueError("no JSON object found in model response")


async def _codegen(
    prompt: str,
    plan: str,
    history: list[BaseMessage],
    context: ProjectContext,
    *,
    provider: str,
    model: str,
) -> PatchBundle:
    """Run the codegen LLM call and parse a PatchBundle from the response.

    Uses plain JSON-output prompting rather than ``with_structured_output``
    so the path works against OpenAI-compatible shims (Anthropic, OpenRouter,
    LM Studio) that don't reliably honor function-calling.
    """
    human = (
        f"{_render_context_block(context)}\n\n"
        f"User request:\n{prompt or '(empty)'}\n\n"
        f"Plan:\n{plan or '(none)'}\n\n"
        "Respond with a single JSON object and nothing else (no prose, no\n"
        "markdown fences). Shape:\n"
        '{\n'
        '  "files": [\n'
        '    {"path": "<relative posix path>", "operation": "create"|"replace", "content": "<full file body>"},\n'
        '    {"path": "...", "operation": "edit", "edits": [{"search": "<exact substring>", "replace": "<new text>"}]},\n'
        '    {"path": "...", "operation": "delete"}\n'
        '  ]\n'
        '}\n'
        "Rules: 'create' for new files; 'replace' to rewrite placeholder\n"
        "scaffolds or short files wholesale; 'edit' only when each 'search'\n"
        "string appears verbatim and exactly once in the file body shown\n"
        "above; 'delete' carries no content/edits. Max 10 files."
    )

    try:
        llm = build_llm(provider, model)
        msg = await llm.ainvoke(
            [
                SystemMessage(content=CODEGEN_SYSTEM_PROMPT),
                *history,
                HumanMessage(content=human),
            ]
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("codegen LLM call failed")
        raise CodegenError(f"codegen failed: {exc}") from exc

    raw = msg.content if isinstance(msg.content, str) else ""
    try:
        payload = _extract_json_object(raw)
        result = PatchBundle.model_validate_json(payload)
    except (ValueError, json.JSONDecodeError, ValidationError) as exc:
        snippet = (raw or "").strip()[:400]
        logger.warning("codegen JSON parse failed: %s; raw=%r", exc, snippet)
        raise CodegenError(f"codegen returned invalid JSON: {exc}") from exc

    if not result.files:
        raise CodegenError("codegen returned no file operations")
    return result


async def _repair_edits(
    failed_paths: list[tuple[str, str]],
    *,
    context: ProjectContext,
    user_prompt: str,
    plan: str,
    history: list[BaseMessage],
    provider: str,
    model: str,
) -> PatchBundle | None:
    """Ask the codegen LLM to redo just the files whose edits failed.

    ``failed_paths`` is a list of ``(path, error_message)``. The repair
    prompt steers the model toward whole-file ``replace`` ops (and away
    from another round of search/replace, which is what just failed).
    Returns ``None`` if the LLM call or JSON parse fails — callers should
    fall back to surfacing the original errors.
    """
    if not failed_paths:
        return None

    file_blocks: list[str] = []
    for path, err in failed_paths:
        body = context.get_file(path) or ""
        display = body
        if len(display) > CONTEXT_FILE_DISPLAY_CAP:
            display = display[:CONTEXT_FILE_DISPLAY_CAP] + "\n/* ... truncated ... */"
        file_blocks.append(
            f"----- {path} -----\nFailure: {err}\nCurrent file body:\n{display}"
        )

    human = (
        "Your previous patch had failed edits. Redo ONLY the files below.\n"
        "Prefer the `replace` operation (full file body) over another\n"
        "round of search/replace. Do not touch any other files.\n\n"
        f"Original request:\n{user_prompt or '(empty)'}\n\n"
        f"Plan:\n{plan or '(none)'}\n\n"
        + "\n\n".join(file_blocks)
        + "\n\nRespond with a single JSON object: "
        '{"files":[{"path":"...","operation":"replace","content":"..."}]}'
    )

    try:
        llm = build_llm(provider, model)
        msg = await llm.ainvoke(
            [
                SystemMessage(content=CODEGEN_SYSTEM_PROMPT),
                *history,
                HumanMessage(content=human),
            ]
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("repair LLM call failed")
        return None

    raw = msg.content if isinstance(msg.content, str) else ""
    try:
        payload = _extract_json_object(raw)
        return PatchBundle.model_validate_json(payload)
    except (ValueError, json.JSONDecodeError, ValidationError) as exc:
        logger.warning("repair JSON parse failed: %s", exc)
        return None


async def run_codegen_stream(
    *,
    project_id: str,
    prompt: str,
    history: list[PromptRecord] | None = None,
    storage: Storage | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> AsyncIterator[StreamEvent]:
    """Execute the codegen loop and yield :class:`StreamEvent` items.

    ``history`` should be the prior conversation on this project **excluding**
    the current prompt. ``storage`` defaults to the process-wide instance
    and is used to read the current project state for context selection.

    ``provider`` and ``model`` are the client's per-request selection. When
    either is omitted the server-side default (from env / catalog) is used.

    On any :class:`CodegenError` a single ``ErrorEvent`` frame is emitted
    and the stream ends (no ``status: done``). Individual patch failures
    surface as *recoverable* error events so the stream continues.
    """
    current = get_settings()

    yield StatusEvent(stage="planning", note="Reading project")

    try:
        resolved_provider, resolved_model = model_catalog.resolve(
            provider, model, current
        )
    except ValueError as exc:
        yield ErrorEvent(message=str(exc), recoverable=False)
        return

    if resolved_provider == "ollama":
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                await client.get(f"{current.ollama_base_url}/api/tags")
        except Exception:
            yield ErrorEvent(
                message=(
                    f"Ollama is not running at {current.ollama_base_url}. "
                    "Start Ollama and try again."
                ),
                recoverable=False,
            )
            return
    if resolved_provider == "openai" and not current.openai_api_key:
        yield ErrorEvent(
            message=_missing_api_key_message("openai"), recoverable=False
        )
        return
    if resolved_provider == "gemini" and not current.google_api_key:
        yield ErrorEvent(
            message=_missing_api_key_message("gemini"), recoverable=False
        )
        return

    store = storage or get_storage()

    try:
        context = load_context(store, project_id, prompt)
    except Exception as exc:
        logger.exception("failed to load project context")
        yield ErrorEvent(message=f"context load failed: {exc}", recoverable=False)
        return

    history_msgs = _history_to_messages(history)

    try:
        plan_text = await _plan(
            prompt,
            history_msgs,
            context,
            provider=resolved_provider,
            model=resolved_model,
        )
    except CodegenError as exc:
        logger.warning("codegen plan failed: %s", exc)
        yield ErrorEvent(message=str(exc), recoverable=False)
        return
    except Exception as exc:
        logger.exception("planner crashed")
        yield ErrorEvent(message=f"planner crashed: {exc}", recoverable=False)
        return

    yield MessageDeltaEvent(content=plan_text + "\n")
    yield StatusEvent(stage="generating", note="Generating code")

    codegen_task = asyncio.create_task(
        _codegen(
            prompt,
            plan_text,
            history_msgs,
            context,
            provider=resolved_provider,
            model=resolved_model,
        )
    )
    # Heartbeat so proxies (Vercel/Cloudflare/nginx) don't drop the SSE
    # connection while the upstream LLM is still composing its JSON reply.
    elapsed = 0
    try:
        while not codegen_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(codegen_task), timeout=10)
            except asyncio.TimeoutError:
                elapsed += 10
                yield StatusEvent(
                    stage="generating", note=f"Still generating ({elapsed}s)"
                )
        bundle = codegen_task.result()
    except CodegenError as exc:
        logger.warning("codegen failed: %s", exc)
        yield ErrorEvent(message=str(exc), recoverable=False)
        return
    except asyncio.CancelledError:
        codegen_task.cancel()
        raise
    except Exception as exc:
        logger.exception("codegen crashed")
        yield ErrorEvent(message=f"codegen crashed: {exc}", recoverable=False)
        return

    # Snapshot the project *after* codegen returned a usable bundle but
    # *before* applying any file ops. That way a failed LLM call or a
    # cancel during planning doesn't leave an empty snapshot behind, but
    # the user can still roll back past any writes that follow.
    snapshot_id: str | None = None
    try:
        record = store.create_snapshot(project_id, user_prompt=prompt)
        snapshot_id = record.id
    except Exception:
        logger.exception("failed to create pre-turn snapshot for %s", project_id)

    yield StatusEvent(stage="generating", note="Writing files", snapshot_id=snapshot_id)

    results = apply_bundle(bundle, context)

    # If any `edit` ops failed (typically because the model produced a
    # search string that no longer matches the buffer), give the LLM one
    # chance to redo just those files with a `replace` op. The fuzzy
    # fallback in apply_patch catches the easy cases; this catches the
    # harder ones where the model genuinely got the surrounding text wrong.
    edit_targets = {f.path.strip().replace("\\", "/").lstrip("/"): f for f in bundle.files if f.operation == "edit"}
    failed_edits = [
        (r.path, r.error) for r in results
        if r.kind == "error" and r.path in edit_targets
    ]
    if failed_edits:
        yield StatusEvent(stage="generating", note=f"Repairing {len(failed_edits)} file(s)")
        repaired = await _repair_edits(
            failed_edits,
            context=context,
            user_prompt=prompt,
            plan=plan_text,
            history=history_msgs,
            provider=resolved_provider,
            model=resolved_model,
        )
        if repaired is not None:
            repair_results = apply_bundle(repaired, context)
            repair_by_path = {r.path: r for r in repair_results}
            results = [
                repair_by_path.get(r.path, r) if r.kind == "error" else r
                for r in results
            ]

    for result in results:
        if result.kind == "error":
            yield ErrorEvent(
                message=f"{result.path}: {result.error}",
                recoverable=True,
            )
        elif result.kind == "delete":
            try:
                store.delete_file(project_id, result.path)
            except ValueError as exc:
                yield ErrorEvent(
                    message=f"rejected file.delete {result.path}: {exc}",
                    recoverable=True,
                )
                continue
            except OSError:
                logger.exception("file.delete failed path=%s", result.path)
            yield FileDeleteEvent(path=result.path)
        else:
            try:
                store.write_file(project_id, result.path, result.content)
            except ValueError as exc:
                yield ErrorEvent(
                    message=f"rejected file.write {result.path}: {exc}",
                    recoverable=True,
                )
                continue
            except OSError:
                logger.exception("file.write failed path=%s", result.path)
            yield FileWriteEvent(path=result.path, content=result.content)

    yield StatusEvent(stage="done")
