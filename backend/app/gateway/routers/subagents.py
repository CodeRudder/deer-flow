"""Sub-agent session data endpoints.

Reads sub-agent conversation JSONL files to provide session metadata
and full message history for the frontend detail view.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/threads/{thread_id}/subagents", tags=["subagents"])


class SubagentSessionSummary(BaseModel):
    task_id: str
    subagent_name: str
    description: str = ""
    status: str = "unknown"
    started_at: str = ""
    completed_at: str = ""
    message_count: int = 0


class SubagentSessionDetail(BaseModel):
    task_id: str
    subagent_name: str
    status: str = "unknown"
    messages: list[dict[str, Any]] = []


@router.get("", response_model=list[SubagentSessionSummary])
async def list_subagent_sessions(thread_id: str, request: Request) -> list[SubagentSessionSummary]:
    """List all sub-agent sessions for a thread."""
    from deerflow.subagents.session import SubagentSession

    try:
        sessions = SubagentSession.list_sessions(thread_id)
    except Exception:
        logger.exception("Failed to list subagent sessions for thread %s", thread_id)
        return []

    results: list[SubagentSessionSummary] = []
    for session in sessions:
        summary = session.read_summary()
        if summary:
            results.append(SubagentSessionSummary(
                task_id=session.task_id,
                subagent_name=session.subagent_name,
                description=session.description,
                status=summary.get("status", "unknown"),
                started_at=summary.get("started_at", ""),
                completed_at=summary.get("completed_at", ""),
                message_count=summary.get("message_count", 0),
            ))
        else:
            # No summary file — derive from session metadata
            msg_count = len(session.read_messages())
            results.append(SubagentSessionSummary(
                task_id=session.task_id,
                subagent_name=session.subagent_name,
                description=session.description,
                status="running" if not session.is_terminal else "unknown",
                started_at=session.started_at,
                message_count=msg_count,
            ))
    return results


@router.get("/{task_id}", response_model=SubagentSessionDetail)
async def get_subagent_session(thread_id: str, task_id: str, request: Request) -> SubagentSessionDetail:
    """Get full message history for a sub-agent session."""
    from deerflow.subagents.session import SubagentSession

    session = SubagentSession(
        thread_id=thread_id,
        task_id=task_id,
        subagent_name="unknown",
    )

    messages = session.read_messages()

    # Try to get status from summary
    summary = session.read_summary()
    status = "unknown"
    subagent_name = "unknown"
    if summary:
        status = summary.get("status", "unknown")
        subagent_name = summary.get("subagent_name", "unknown")

    return SubagentSessionDetail(
        task_id=task_id,
        subagent_name=subagent_name,
        status=status,
        messages=messages,
    )


class ResumeSubagentRequest(BaseModel):
    description: str = ""


class ResumeSubagentResponse(BaseModel):
    success: bool
    message: str


@router.post("/{task_id}/resume", response_model=ResumeSubagentResponse)
async def resume_subagent_session(
    thread_id: str,
    task_id: str,
    body: ResumeSubagentRequest,
    request: Request,
) -> ResumeSubagentResponse:
    """Resume an interrupted/failed subtask by sending an activation message to the thread.

    The message instructs the lead agent to use the task tool with action="resume"
    and the specified task_id.  The agent reads the session history and continues
    from where the subtask left off.
    """
    from deerflow.subagents.session import SubagentSession

    # Check session exists and is resumable
    info = SubagentSession.get_resume_info(task_id, thread_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Subtask {task_id} not found in thread {thread_id}")

    if info["status"] not in ("interrupted", "failed", "unknown"):
        raise HTTPException(
            status_code=400,
            detail=f"Subtask {task_id} has status '{info['status']}' — only interrupted/failed tasks can be resumed",
        )

    description = body.description or info["description"] or "Resume subtask"

    # Build the resume instruction message
    subagent_type = info.get("subagent_type", "general-purpose")
    message = (
        f"恢复执行子任务 {task_id}（{description}）。\n"
        f"请使用 task tool 的 action=\"resume\" 模式恢复执行：\n"
        f'task(description="{description}", prompt="继续执行", subagent_type="{subagent_type}", action="resume", task_id="{task_id}")'
    )

    # Send message to the thread via LangGraph client
    try:
        from langgraph_sdk import get_client

        client = get_client(url="http://localhost:2024")

        # Create a new run with the resume instruction
        run = await client.runs.create(
            thread_id,
            assistant_id="lead_agent",
            input={"messages": [{"type": "human", "content": message}]},
            config={"recursion_limit": 500},
            context={
                "subagent_enabled": True,
                "is_plan_mode": True,
            },
            stream_mode=["values"],
        )

        logger.info("Resumed subtask %s on thread %s, run_id=%s", task_id, thread_id, run["run_id"])

        return ResumeSubagentResponse(
            success=True,
            message=f"Resume message sent for subtask {task_id}",
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to resume subtask %s on thread %s", task_id, thread_id)
        raise HTTPException(status_code=500, detail="Failed to send resume message")
