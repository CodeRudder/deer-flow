"""Stateless runs endpoints -- stream and wait without a pre-existing thread.

These endpoints auto-create a temporary thread when no ``thread_id`` is
supplied in the request body.  When a ``thread_id`` **is** provided, it
is reused so that conversation history is preserved across calls.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.gateway.deps import get_checkpointer, get_run_manager, get_stream_bridge
from app.gateway.routers.thread_runs import RunCreateRequest, RunResponse, _record_to_response
from app.gateway.services import sse_consumer, start_run
from deerflow.runtime import serialize_channel_values

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/runs", tags=["runs"])


def _resolve_thread_id(body: RunCreateRequest) -> str:
    """Return the thread_id from the request body, or generate a new one."""
    thread_id = (body.config or {}).get("configurable", {}).get("thread_id")
    if thread_id:
        return str(thread_id)
    return str(uuid.uuid4())


@router.post("/stream")
async def stateless_stream(body: RunCreateRequest, request: Request) -> StreamingResponse:
    """Create a run and stream events via SSE.

    If ``config.configurable.thread_id`` is provided, the run is created
    on the given thread so that conversation history is preserved.
    Otherwise a new temporary thread is created.
    """
    thread_id = _resolve_thread_id(body)
    bridge = get_stream_bridge(request)
    run_mgr = get_run_manager(request)
    record = await start_run(body, thread_id, request)

    return StreamingResponse(
        sse_consumer(bridge, record, request, run_mgr),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Content-Location": f"/api/threads/{thread_id}/runs/{record.run_id}",
        },
    )


@router.post("/wait", response_model=dict)
async def stateless_wait(body: RunCreateRequest, request: Request) -> dict:
    """Create a run and block until completion.

    If ``config.configurable.thread_id`` is provided, the run is created
    on the given thread so that conversation history is preserved.
    Otherwise a new temporary thread is created.
    """
    thread_id = _resolve_thread_id(body)
    record = await start_run(body, thread_id, request)

    if record.task is not None:
        try:
            await record.task
        except asyncio.CancelledError:
            pass

    checkpointer = get_checkpointer(request)
    config = {"configurable": {"thread_id": thread_id}}
    try:
        checkpoint_tuple = await checkpointer.aget_tuple(config)
        if checkpoint_tuple is not None:
            checkpoint = getattr(checkpoint_tuple, "checkpoint", {}) or {}
            channel_values = checkpoint.get("channel_values", {})
            return serialize_channel_values(channel_values)
    except Exception:
        logger.exception("Failed to fetch final state for run %s", record.run_id)

    return {"status": record.status.value, "error": record.error}


# ---------------------------------------------------------------------------
# Active runs management (cross-thread)
# ---------------------------------------------------------------------------


@router.get("/active", response_model=list[RunResponse])
async def list_active_runs(request: Request) -> list[RunResponse]:
    """List all pending or running runs across all threads."""
    run_mgr = get_run_manager(request)
    records = await run_mgr.list_active()
    return [_record_to_response(r) for r in records]


class CancelAllResponse(BaseModel):
    cancelled: list[str] = Field(default_factory=list, description="IDs of successfully cancelled runs")
    failed: list[str] = Field(default_factory=list, description="IDs that could not be cancelled")
    total: int = 0


@router.post("/cancel-all", response_model=CancelAllResponse)
async def cancel_all_runs(
    request: Request,
    action: Literal["interrupt", "rollback"] = Query(default="interrupt", description="Cancel action"),
) -> CancelAllResponse:
    """Cancel all pending or running runs across all threads."""
    run_mgr = get_run_manager(request)
    active = await run_mgr.list_active()

    cancelled: list[str] = []
    failed: list[str] = []

    for record in active:
        ok = await run_mgr.cancel(record.run_id, action=action)
        if ok:
            cancelled.append(record.run_id)
        else:
            failed.append(record.run_id)

    if cancelled:
        logger.info("Cancelled %d run(s), %d failed", len(cancelled), len(failed))

    return CancelAllResponse(cancelled=cancelled, failed=failed, total=len(cancelled) + len(failed))


class CancelSubtaskResponse(BaseModel):
    task_id: str
    cancelled: bool
    error: str | None = None


@router.post("/subtasks/{task_id}/cancel", response_model=CancelSubtaskResponse)
async def cancel_subtask(task_id: str, request: Request) -> CancelSubtaskResponse:
    """Cancel a running sub-agent task by task_id.

    Cancellation strategy (in order of preference):
    1. Same process (Gateway mode): directly call request_cancel_background_task
    2. Cross-process (standard mode): cancel the LangGraph run hosting the task_tool,
       which triggers asyncio.CancelledError → request_cancel_background_task
    3. Fallback: update on-disk session status to "cancelled"
    """
    from deerflow.subagents.executor import get_background_task_result, request_cancel_background_task

    # ── Path 1: same process (Gateway mode) ──────────────────────────────
    result = get_background_task_result(task_id)
    if result is not None:
        if result.status.value not in ("running", "pending"):
            return CancelSubtaskResponse(task_id=task_id, cancelled=False, error=f"Task is {result.status.value}")

        request_cancel_background_task(task_id)
        logger.info("Cancelled subtask %s via API (in-memory, same process)", task_id)
        return CancelSubtaskResponse(task_id=task_id, cancelled=True)

    # ── Path 2: cross-process via cancel marker file ──────────────────────
    thread_id = _find_thread_id_for_task(task_id)
    if thread_id:
        try:
            from deerflow.subagents.session import SubagentSession

            session = SubagentSession(
                thread_id=thread_id, task_id=task_id,
                subagent_name="", description="",
            )
            session.request_cancel()
            logger.info("Wrote cancel marker for task %s (thread %s)", task_id, thread_id)
            # Also update summary.json in case the process is already dead (zombie)
            _mark_summary_cancelled(thread_id, task_id)
            return CancelSubtaskResponse(task_id=task_id, cancelled=True)
        except Exception:
            logger.exception("Failed to write cancel marker for task %s", task_id)

    # ── Path 3: fallback — update on-disk session ────────────────────────
    return await _cancel_subtask_on_disk(task_id)


def _mark_summary_cancelled(thread_id: str, task_id: str) -> None:
    """Update summary.json to cancelled (guards against zombie tasks)."""
    import json

    from deerflow.config.paths import get_paths

    summary_path = get_paths().base_dir / "threads" / thread_id / "subagents" / f"{task_id}.summary.json"
    if not summary_path.exists():
        return
    try:
        with open(summary_path, encoding="utf-8") as f:
            summary = json.load(f)
        if summary.get("status") in ("running", "pending", "unknown", "interrupted"):
            summary["status"] = "cancelled"
            with open(summary_path, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)
            logger.info("Marked zombie task %s as cancelled in summary.json", task_id)
    except Exception:
        logger.debug("Failed to update summary for task %s", task_id, exc_info=True)


def _find_thread_id_for_task(task_id: str) -> str | None:
    """Find thread_id by scanning subagent session files."""
    try:
        from deerflow.config.paths import get_paths

        threads_dir = get_paths().base_dir / "threads"
        if not threads_dir.exists():
            return None
        for thread_dir in threads_dir.iterdir():
            if not thread_dir.is_dir():
                continue
            subagents_dir = thread_dir / "subagents"
            if subagents_dir.exists():
                summary = subagents_dir / f"{task_id}.summary.json"
                if summary.exists():
                    return thread_dir.name
    except Exception:
        logger.debug("Failed to find thread_id for task %s", task_id, exc_info=True)
    return None


async def _cancel_subtask_on_disk(task_id: str) -> CancelSubtaskResponse:
    """Fallback: mark subtask as cancelled on disk when no running process found."""
    import json

    from deerflow.config.paths import get_paths

    try:
        threads_dir = get_paths().base_dir / "threads"
        if threads_dir.exists():
            for thread_dir in threads_dir.iterdir():
                if not thread_dir.is_dir():
                    continue
                summary_path = thread_dir / "subagents" / f"{task_id}.summary.json"
                if not summary_path.exists():
                    continue

                with open(summary_path, encoding="utf-8") as f:
                    summary = json.load(f)
                if summary.get("status") in ("running", "pending", "unknown", "interrupted"):
                    summary["status"] = "cancelled"
                    with open(summary_path, "w", encoding="utf-8") as f:
                        json.dump(summary, f, indent=2, ensure_ascii=False)
                    logger.info("Marked subtask %s as cancelled on disk (thread %s)", task_id, thread_dir.name)
                    return CancelSubtaskResponse(task_id=task_id, cancelled=True)
    except Exception:
        logger.exception("Failed to mark subtask %s as cancelled on disk", task_id)

    return CancelSubtaskResponse(task_id=task_id, cancelled=False, error="Task not found")
