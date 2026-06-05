"""Stateless runs endpoints -- stream and wait without a pre-existing thread.

These endpoints auto-create a temporary thread when no ``thread_id`` is
supplied in the request body.  When a ``thread_id`` **is** provided, it
is reused so that conversation history is preserved across calls.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.gateway.authz import require_permission
from app.gateway.deps import get_checkpointer, get_feedback_repo, get_run_event_store, get_run_manager, get_run_store, get_stream_bridge
from app.gateway.pagination import trim_run_message_page
from app.gateway.routers.thread_runs import RunCreateRequest
from app.gateway.services import sse_consumer, start_run, wait_for_run_completion
from deerflow.runtime import serialize_channel_values
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/runs", tags=["runs"])


async def _find_thread_id_for_task_async(task_id: str, *, user_id: str | None = None) -> str | None:
    def _find() -> str | None:
        from deerflow.config.paths import get_paths

        if user_id is None:
            return None
        threads_dir = get_paths().user_dir(user_id) / "threads"
        if not threads_dir.exists():
            return None
        for thread_dir in threads_dir.iterdir():
            if not thread_dir.is_dir():
                continue
            summary = thread_dir / "subagents" / f"{task_id}.summary.json"
            if summary.exists():
                return thread_dir.name
        return None

    return await asyncio.to_thread(_find)


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
    bridge = get_stream_bridge(request)
    run_mgr = get_run_manager(request)
    record = await start_run(body, thread_id, request)

    completed = True
    if record.task is not None:
        completed = await wait_for_run_completion(bridge, record, request, run_mgr)

    if completed:
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
# Active runs and subtask cancellation
# ---------------------------------------------------------------------------


class RunResponse(BaseModel):
    run_id: str
    thread_id: str
    status: str
    created_at: str
    updated_at: str
    error: str | None = None


def _record_to_response(record) -> RunResponse:
    return RunResponse(
        run_id=record.run_id,
        thread_id=record.thread_id,
        status=record.status.value,
        created_at=record.created_at.isoformat(),
        updated_at=record.updated_at.isoformat(),
        error=record.error,
    )


@router.get("/active", response_model=list[RunResponse])
async def list_active_runs(request: Request) -> list[RunResponse]:
    """List all pending or running runs across all threads."""
    run_mgr = get_run_manager(request)
    records = await run_mgr.list_active()
    return [_record_to_response(record) for record in records]


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
    """Cancel a running sub-agent task by task_id."""
    from deerflow.subagents.executor import get_background_task_result, request_cancel_background_task

    user_id = get_effective_user_id()
    result = get_background_task_result(task_id)
    if result is not None and getattr(result, "user_id", None) == user_id:
        if result.status.value not in ("running", "pending"):
            return CancelSubtaskResponse(task_id=task_id, cancelled=False, error=f"Task is {result.status.value}")
        request_cancel_background_task(task_id)
        logger.info("Cancelled subtask %s via API (in-memory, same process)", task_id)
        return CancelSubtaskResponse(task_id=task_id, cancelled=True)

    thread_id = await _find_thread_id_for_task_async(task_id, user_id=user_id)
    if thread_id:
        try:
            from deerflow.subagents.session import SubagentSession

            session = SubagentSession(thread_id=thread_id, task_id=task_id, subagent_name="", description="", user_id=user_id)
            await asyncio.to_thread(session.request_cancel)
            await asyncio.to_thread(_mark_summary_cancelled, thread_id, task_id, user_id=user_id)
            logger.info("Wrote cancel marker for task %s (thread %s)", task_id, thread_id)
            return CancelSubtaskResponse(task_id=task_id, cancelled=True)
        except Exception:
            logger.exception("Failed to write cancel marker for task %s", task_id)

    return await _cancel_subtask_on_disk(task_id, user_id=user_id)


def _mark_summary_cancelled(thread_id: str, task_id: str, *, user_id: str | None = None) -> None:
    import json

    from deerflow.config.paths import get_paths

    summary_path = get_paths().subagent_dir(thread_id, user_id=user_id) / f"{task_id}.summary.json"
    if not summary_path.exists():
        return
    with open(summary_path, encoding="utf-8") as f:
        summary = json.load(f)
    if summary.get("status") in ("running", "pending", "unknown", "interrupted"):
        summary["status"] = "cancelled"
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)


async def _cancel_subtask_on_disk(task_id: str, *, user_id: str | None = None) -> CancelSubtaskResponse:
    import json

    from deerflow.config.paths import get_paths

    def _cancel() -> CancelSubtaskResponse:
        try:
            if user_id is None:
                return CancelSubtaskResponse(task_id=task_id, cancelled=False, error="Task not found")
            threads_dir = get_paths().user_dir(user_id) / "threads"
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
                        return CancelSubtaskResponse(task_id=task_id, cancelled=True)
        except Exception:
            logger.exception("Failed to mark subtask %s as cancelled on disk", task_id)
        return CancelSubtaskResponse(task_id=task_id, cancelled=False, error="Task not found")

    return await asyncio.to_thread(_cancel)


# ---------------------------------------------------------------------------
# Run-scoped read endpoints
# ---------------------------------------------------------------------------


async def _resolve_run(run_id: str, request: Request) -> dict:
    """Fetch run by run_id with user ownership check. Raises 404 if not found."""
    run_store = get_run_store(request)
    record = await run_store.get(run_id)  # user_id=AUTO filters by contextvar
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return record


@router.get("/{run_id}/messages")
@require_permission("runs", "read")
async def run_messages(
    run_id: str,
    request: Request,
    limit: int = Query(default=50, le=200, ge=1),
    before_seq: int | None = Query(default=None),
    after_seq: int | None = Query(default=None),
) -> dict:
    """Return paginated messages for a run (cursor-based).

    Pagination:
    - after_seq: messages with seq > after_seq (forward)
    - before_seq: messages with seq < before_seq (backward)
    - neither: latest messages

    Response: { data: [...], has_more: bool }
    """
    run = await _resolve_run(run_id, request)
    event_store = get_run_event_store(request)
    rows = await event_store.list_messages_by_run(
        run["thread_id"],
        run_id,
        limit=limit + 1,
        before_seq=before_seq,
        after_seq=after_seq,
    )
    data, has_more = trim_run_message_page(rows, limit=limit, after_seq=after_seq)
    return {"data": data, "has_more": has_more}


@router.get("/{run_id}/feedback")
@require_permission("runs", "read")
async def run_feedback(run_id: str, request: Request) -> list[dict]:
    """Return all feedback for a run."""
    run = await _resolve_run(run_id, request)
    feedback_repo = get_feedback_repo(request)
    return await feedback_repo.list_by_run(run["thread_id"], run_id)
