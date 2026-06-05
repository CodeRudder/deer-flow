"""Automatic task recovery on Gateway startup.

Scans the filesystem for interrupted sub-agent session files (.jsonl without
a terminal status marker) and logs the affected Lead Agent threads.

This module is called once from the Gateway ``lifespan()`` handler during cold
start. Automatic recovery is intentionally disabled; users can manually resume
tasks from the UI.
"""

import asyncio
import logging
from collections import defaultdict

from deerflow.config.paths import get_paths
from deerflow.subagents.session import SubagentSession

logger = logging.getLogger(__name__)


def _scan_interrupted_sessions() -> dict[str, list[SubagentSession]]:
    """Scan all thread directories for interrupted sub-agent sessions.

    Returns:
        Mapping of ``{thread_id: [interrupted sessions]}``.
    """
    result: dict[str, list[SubagentSession]] = defaultdict(list)

    try:
        users_dir = get_paths().base_dir / "users"
    except Exception:
        logger.exception("Failed to resolve users directory")
        return result

    if not users_dir.exists():
        return result

    for user_dir in users_dir.iterdir():
        if not user_dir.is_dir():
            continue
        threads_dir = user_dir / "threads"
        if not threads_dir.is_dir():
            continue
        for thread_dir in threads_dir.iterdir():
            if not thread_dir.is_dir():
                continue
            thread_id = thread_dir.name
            subagents_dir = thread_dir / "subagents"
            if not subagents_dir.is_dir():
                continue
            try:
                interrupted = SubagentSession.find_interrupted(thread_id, user_id=user_dir.name)
                if interrupted:
                    result[thread_id].extend(interrupted)
            except Exception:
                logger.exception("Failed to scan thread %s for interrupted sessions", thread_id)

    return dict(result)


def _build_recovery_message(sessions: list[SubagentSession]) -> str:
    """Build a brief recovery message for interrupted work.

    Args:
        sessions: List of interrupted sessions for a single thread.

    Returns:
        Simple recovery prompt to send to the Lead Agent thread.
    """
    return f"<task_recovery>\n服务已经重启，有 {len(sessions)} 个子任务被中断，请继续处理未完成任务。\n</task_recovery>"


async def _notify_thread(thread_id: str, message: str) -> None:
    """Send a recovery message to a Lead Agent thread via the LangGraph SDK.

    Spawns a background task via ``asyncio.create_task()`` so the Gateway
    lifespan is not blocked waiting for the agent to process the message.
    The recovery message is sent as a human message on the existing thread.
    """
    try:
        from langgraph_sdk import get_client

        client = get_client(url="http://localhost:2024")
    except ImportError:
        logger.error("langgraph_sdk not installed, cannot send recovery message")
        return
    except Exception:
        logger.exception("Failed to create LangGraph client for recovery")
        return

    async def _send() -> None:
        try:
            # Add message to thread state without creating a run.
            # Creating a run would block the main session.
            # The user will see the recovery message when they next interact.
            await client.threads.update_state(
                thread_id=thread_id,
                values={
                    "messages": [
                        {
                            "role": "human",
                            "content": message,
                        }
                    ]
                },
            )
            logger.info("Recovery message added to thread %s state", thread_id)
        except Exception:
            logger.exception("Failed to add recovery message to thread %s", thread_id)

    import asyncio

    asyncio.create_task(_send())
    logger.info("Recovery message queued for thread %s", thread_id)


async def auto_recover_interrupted_tasks() -> None:
    """Main entry point — scan for interrupted sessions.

    Currently disabled: automatic recovery was causing issues:
    - Creating runs blocks the main session
    - update_state fails when in-flight runs exist
    - Recovery messages trigger agent to execute code directly

    Only logs the number of interrupted sessions for visibility.
    Users can manually resume tasks from the UI.
    """
    logger.info("Scanning for interrupted sub-agent sessions...")

    interrupted = await asyncio.to_thread(_scan_interrupted_sessions)

    if not interrupted:
        logger.info("No interrupted sub-agent sessions found")
        return

    total_sessions = sum(len(sessions) for sessions in interrupted.values())
    logger.info(
        "Found %d interrupted session(s) across %d thread(s) — auto-recovery disabled, use UI to resume",
        total_sessions,
        len(interrupted),
    )
