"""Automatic task recovery on Gateway startup.

Scans the filesystem for interrupted sub-agent session files (.jsonl without
a terminal status marker) and notifies the corresponding Lead Agent threads
via the LangGraph SDK so they can resume work.

This module is called once from the Gateway ``lifespan()`` handler during cold
start.  It does **not** auto-restart sub-agents directly — instead it sends a
recovery message to the Lead Agent thread, letting the Lead Agent decide how
to continue (matching the interrupt-recovery strategy already in the system
prompt).
"""

import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

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
        threads_dir = get_paths().base_dir / "threads"
    except Exception:
        logger.exception("Failed to resolve threads directory")
        return result

    if not threads_dir.exists():
        return result

    for thread_dir in threads_dir.iterdir():
        if not thread_dir.is_dir():
            continue
        thread_id = thread_dir.name
        subagents_dir = thread_dir / "subagents"
        if not subagents_dir.is_dir():
            continue
        try:
            interrupted = SubagentSession.find_interrupted(thread_id)
            if interrupted:
                result[thread_id].extend(interrupted)
        except Exception:
            logger.exception("Failed to scan thread %s for interrupted sessions", thread_id)

    return dict(result)


def _build_recovery_message(sessions: list[SubagentSession]) -> str:
    """Build a human-readable recovery message summarising interrupted work.

    Args:
        sessions: List of interrupted sessions for a single thread.

    Returns:
        Formatted recovery prompt to send to the Lead Agent thread.
    """
    parts: list[str] = []
    for s in sessions:
        messages = s.read_messages()
        ai_messages = [m for m in messages if m.get("role") == "ai"]

        last_ai = ""
        if ai_messages:
            content = ai_messages[-1].get("content", "")
            last_ai = content[:500] if isinstance(content, str) else str(content)[:500]

        parts.append(
            f"- **{s.subagent_name}** (task {s.task_id}): "
            f"已执行 {len(messages)} 步，最后进度：{last_ai or '（无 AI 响应）'}"
        )

    session_lines = "\n".join(parts)
    return (
        "<task_recovery>\n"
        "服务重启后发现以下子任务在上次运行中被中断：\n\n"
        f"{session_lines}\n\n"
        "请检查每个任务的进度，决定是否需要继续执行未完成的工作。\n"
        "如果需要继续，请使用 task() 工具重新启动相关子任务，"
        "并在 prompt 中包含之前的进度信息，让子 Agent 从断点继续。\n"
        "</task_recovery>"
    )


async def _notify_thread(thread_id: str, message: str) -> None:
    """Send a recovery message to a Lead Agent thread via the LangGraph SDK.

    Uses ``client.runs.wait()`` so the recovery is synchronous (the Gateway
    lifespan waits for it to complete before yielding).  The recovery message
    is sent as a human message on the existing thread.
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

    try:
        await client.runs.wait(
            thread_id=thread_id,
            assistant_id="lead_agent",
            input={
                "messages": [
                    {
                        "role": "human",
                        "content": message,
                    }
                ]
            },
            config={
                "recursion_limit": 50,
            },
        )
        logger.info("Recovery message sent to thread %s", thread_id)
    except Exception:
        logger.exception("Failed to send recovery message to thread %s", thread_id)


async def auto_recover_interrupted_tasks() -> None:
    """Main entry point — scan for interrupted sessions and notify threads.

    Called once from the Gateway ``lifespan()`` handler after all services are
    up (config, LangGraph runtime, channel service).
    """
    logger.info("Scanning for interrupted sub-agent sessions...")

    interrupted = _scan_interrupted_sessions()

    if not interrupted:
        logger.info("No interrupted sub-agent sessions found")
        return

    total_sessions = sum(len(sessions) for sessions in interrupted.values())
    logger.info(
        "Found %d interrupted session(s) across %d thread(s)",
        total_sessions,
        len(interrupted),
    )

    for thread_id, sessions in interrupted.items():
        message = _build_recovery_message(sessions)
        logger.info(
            "Sending recovery message to thread %s (%d interrupted session(s))",
            thread_id,
            len(sessions),
        )
        await _notify_thread(thread_id, message)
