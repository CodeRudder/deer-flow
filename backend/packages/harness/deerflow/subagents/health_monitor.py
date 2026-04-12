"""Sub-agent session health monitor.

Periodically checks sub-agent sessions that have already stopped (terminal status)
but whose JSONL files lack a matching terminal status marker — a sign that the
executor crashed or was killed before writing the marker.

IMPORTANT: The monitor NEVER interrupts or cancels a running task.  LLM calls,
tool executions, and code generation can legitimately take many minutes without
writing to the JSONL file.  Only tasks whose executor has already terminated
unexpectedly are recovered.
"""

import json
import logging
import threading
import time
from datetime import datetime
from typing import TYPE_CHECKING

from deerflow.subagents.executor import (
    SubagentStatus,
    _background_tasks,
    _background_tasks_lock,
    request_cancel_background_task,
)

if TYPE_CHECKING:
    from deerflow.subagents.executor import SubagentResult

logger = logging.getLogger(__name__)


def _find_session_jsonl(thread_id: str | None, task_id: str) -> str | None:
    """Locate the JSONL file for a task, or return None."""
    if not thread_id:
        return None
    try:
        from deerflow.config.paths import get_paths

        d = get_paths().subagent_dir(thread_id)
        p = d / f"{task_id}.jsonl"
        return str(p) if p.exists() else None
    except Exception:
        return None


def _read_last_line(path: str) -> dict | None:
    """Read the last non-empty line from a file efficiently."""
    try:
        with open(path, encoding="utf-8") as f:
            # Seek to end, read last 4KB for the last line
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return None
            read_size = min(size, 4096)
            f.seek(size - read_size)
            lines = f.readlines()
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
        return None
    except OSError:
        return None


def _count_messages(jsonl_path: str) -> int:
    """Count conversation message lines (excluding status markers)."""
    count = 0
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Status marker lines have "status" but no "role"
                if "status" in entry and "role" not in entry:
                    continue
                count += 1
    except OSError:
        pass
    return count


class SubagentHealthMonitor:
    """Background health monitor for sub-agent sessions.

    Uses ``threading.Timer`` for periodic checks (same pattern as the memory
    update queue in ``agents/memory/queue.py``).
    """

    def __init__(self, check_interval: int = 60) -> None:
        self._check_interval = check_interval
        self._timer: threading.Timer | None = None
        self._running = False

    def start(self) -> None:
        """Start the periodic health check loop."""
        self._running = True
        self._schedule_next()
        logger.info(
            "Health monitor started (interval=%ds)",
            self._check_interval,
        )

    def stop(self) -> None:
        """Stop the health check loop."""
        self._running = False
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        logger.info("Health monitor stopped")

    def _schedule_next(self) -> None:
        if not self._running:
            return
        self._timer = threading.Timer(self._check_interval, self._check_cycle)
        self._timer.daemon = True
        self._timer.start()

    def _check_cycle(self) -> None:
        """Execute one check cycle, then schedule the next."""
        try:
            self._check_all()
        except Exception:
            logger.exception("Health monitor check cycle failed")
        self._schedule_next()

    def _check_all(self) -> None:
        """Check RUNNING sub-agent tasks whose executor may have silently died."""
        with _background_tasks_lock:
            running = {
                tid: r
                for tid, r in _background_tasks.items()
                if r.status == SubagentStatus.RUNNING
            }

        if not running:
            return

        logger.debug("Health monitor checking %d running task(s)", len(running))

        for task_id, result in running.items():
            try:
                self._check_task(task_id, result)
            except Exception:
                logger.exception("Health monitor failed to check task %s", task_id)

    def _check_task(self, task_id: str, result: "SubagentResult") -> None:
        """Check a RUNNING task for stale in-memory status.

        Only handles the case where a task is RUNNING in memory but the JSONL
        file already has a terminal marker (executor finished but status was
        never updated, e.g. due to a crash). Updates the in-memory status.

        If JSONL has NO terminal marker, the task may still be legitimately
        running (LLM call, tool execution, etc.) — never interfere.

        Failed/completed tasks are NOT reactivated here; the main session
        health monitor handles recovery.
        """

        jsonl_path = _find_session_jsonl(result.thread_id, task_id)
        if jsonl_path is None:
            return

        last_entry = _read_last_line(jsonl_path)
        if last_entry is None:
            return

        # Only act if JSONL has a terminal marker — task finished but
        # in-memory status was never updated.
        terminal_status = last_entry.get("status")
        if terminal_status not in ("completed", "failed", "interrupted"):
            return  # No terminal marker — task may still be running, leave alone

        # Sync in-memory status from JSONL
        logger.info(
            "Health monitor: task %s JSONL has terminal marker '%s' but in-memory status is RUNNING, updating",
            task_id,
            terminal_status,
        )
        try:
            status_map = {
                "completed": SubagentStatus.COMPLETED,
                "failed": SubagentStatus.FAILED,
                "interrupted": SubagentStatus.CANCELLED,
            }
            result.status = status_map.get(terminal_status, SubagentStatus.FAILED)
            result.completed_at = datetime.now()
            if terminal_status == "completed":
                result.result = last_entry.get("result", "")
            else:
                result.error = last_entry.get("error", last_entry.get("message", f"Task {terminal_status}"))
        except Exception:
            logger.exception("Failed to update stale status for task %s", task_id)

    def _reactivate_task(self, task_id: str, result: "SubagentResult", reason: str) -> None:
        """Restart a stopped task with a recovery prompt."""
        # Mark session as interrupted
        jsonl_path = _find_session_jsonl(result.thread_id, task_id)
        if jsonl_path:
            try:
                from deerflow.subagents.session import SubagentSession

                session = SubagentSession(
                    thread_id=result.thread_id or "",
                    task_id=task_id,
                    subagent_name=result.subagent_name or "unknown",
                    description=result.description or "",
                )
                msg_count = _count_messages(jsonl_path)
                session.mark_interrupted(message_count=msg_count)
            except Exception:
                logger.exception("Failed to mark session %s as interrupted", task_id)

        # Build recovery prompt and restart
        recovery_summary = ""
        if jsonl_path:
            last_entry = _read_last_line(jsonl_path)
            if last_entry and last_entry.get("role") == "ai":
                content = last_entry.get("content", "")
                if isinstance(content, str):
                    recovery_summary = content[:500]
                else:
                    recovery_summary = str(content)[:500]

        msg_count = _count_messages(jsonl_path) if jsonl_path else 0
        original = result.original_prompt or ""
        recovery_prompt = (
            f"<recovery>\n任务因 {reason} 被中断。已执行 {msg_count} 步。"
            f"\n最后完成的工作：{recovery_summary}\n"
            f"原始任务：{original[:500]}\n"
            f"请继续完成剩余工作，不要重复已完成的步骤。\n</recovery>\n\n"
            f"{original}"
        )

        # Create new executor and submit
        try:
            from deerflow.subagents import get_subagent_config
            from deerflow.subagents.executor import SubagentExecutor
            from deerflow.tools import get_available_tools

            config = get_subagent_config(result.subagent_name or "general-purpose")
            if config is None:
                logger.error("Cannot reactivate task %s: unknown subagent %s", task_id, result.subagent_name)
                return

            tools = get_available_tools(subagent_enabled=False)
            executor = SubagentExecutor(
                config=config,
                tools=tools,
                thread_id=result.thread_id,
            )
            new_task_id = executor.execute_async(recovery_prompt, description=f"[recovery] {result.description or ''}")
            logger.info(
                "Health monitor reactivated task %s as new task %s (reason: %s)",
                task_id,
                new_task_id,
                reason,
            )
        except Exception:
            logger.exception("Health monitor failed to reactivate task %s", task_id)
