"""Sub-agent session data endpoints.

Reads sub-agent conversation JSONL files to provide session metadata
and full message history for the frontend detail view.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
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
