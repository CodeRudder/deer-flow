"""Session health monitor — periodic background task for the Gateway.

Detects and recovers from two conditions:

1. **Zombie sub-agent tasks**: ``_background_tasks`` shows RUNNING but the
   JSONL session file has not been updated for ``stale_threshold`` seconds.
   Action: reactivate the task using ``SubagentHealthMonitor`` logic.

2. **Stalled main session**: All sub-agent tasks have stopped, the last run
   was NOT a user-initiated interrupt, and unfinished todos remain.
   Action: send a recovery message to the Lead Agent thread.

Uses ``threading.Timer`` for periodic scheduling (same pattern as
``SubagentHealthMonitor`` and the memory update queue).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langgraph_sdk import LangGraphClient

logger = logging.getLogger(__name__)


class SessionHealthMonitor:
    """Gateway-level periodic health monitor for sessions.

    Args:
        check_interval: Seconds between check cycles (default: 120).
        stale_threshold: Seconds without JSONL update before a sub-agent
            task is considered zombie (default: 300).
        langgraph_url: LangGraph Server URL for standard mode queries.
    """

    def __init__(
        self,
        check_interval: int = 120,
        stale_threshold: int = 300,
        langgraph_url: str = "http://localhost:2024",
    ) -> None:
        self._check_interval = check_interval
        self._stale_threshold = stale_threshold
        self._langgraph_url = langgraph_url
        self._timer: threading.Timer | None = None
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: LangGraphClient | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start the periodic check loop."""
        self._running = True
        self._loop = loop
        self._schedule_next()
        logger.info(
            "Session health monitor started (interval=%ds, stale_threshold=%ds)",
            self._check_interval,
            self._stale_threshold,
        )

    def stop(self) -> None:
        """Stop the periodic check loop."""
        self._running = False
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        logger.info("Session health monitor stopped")

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    def _schedule_next(self) -> None:
        if not self._running:
            return
        self._timer = threading.Timer(self._check_interval, self._check_cycle)
        self._timer.daemon = True
        self._timer.start()

    def _check_cycle(self) -> None:
        """Execute one check cycle, then schedule the next."""
        try:
            if self._loop and not self._loop.is_closed():
                future = asyncio.run_coroutine_threadsafe(
                    self._check_all(), self._loop,
                )
                future.result(timeout=30)
        except Exception:
            logger.exception("Session health monitor check cycle failed")
        self._schedule_next()

    # ------------------------------------------------------------------
    # Main check entry
    # ------------------------------------------------------------------

    async def _check_all(self) -> None:
        """Run all health checks in sequence."""
        await self._check_subagent_tasks()
        await self._check_stalled_threads()

    # ------------------------------------------------------------------
    # Sub-agent zombie detection
    # ------------------------------------------------------------------

    async def _check_subagent_tasks(self) -> None:
        """Detect and reactivate zombie sub-agent tasks."""
        from deerflow.subagents.executor import (
            _background_tasks,
            _background_tasks_lock,
        )

        with _background_tasks_lock:
            running = {
                tid: r
                for tid, r in _background_tasks.items()
                if self._status_value(r.status) == "running"
            }

        if not running:
            return

        logger.debug("Checking %d running sub-agent task(s)", len(running))

        for task_id, result in running.items():
            try:
                await self._check_subagent_task(task_id, result)
            except Exception:
                logger.exception("Failed to check sub-agent task %s", task_id)

    async def _check_subagent_task(self, task_id: str, result: Any) -> None:
        """Check a single sub-agent task for staleness."""
        jsonl_path = self._find_session_jsonl(result.thread_id, task_id)
        if jsonl_path is None:
            return

        try:
            from pathlib import Path

            mtime = Path(jsonl_path).stat().st_mtime
            stale_seconds = time.time() - mtime
        except OSError:
            return

        if stale_seconds > self._stale_threshold:
            logger.warning(
                "Zombie sub-agent task detected: task_id=%s stale=%ds threshold=%ds",
                task_id,
                int(stale_seconds),
                self._stale_threshold,
            )
            self._reactivate_subagent(task_id, result, f"session stale for {int(stale_seconds)}s")

    def _reactivate_subagent(self, task_id: str, result: Any, reason: str) -> None:
        """Reactivate a zombie sub-agent task using SubagentHealthMonitor logic."""
        from deerflow.subagents.health_monitor import SubagentHealthMonitor

        monitor = SubagentHealthMonitor.__new__(SubagentHealthMonitor)
        monitor._reactivate_task(task_id, result, reason)

    # ------------------------------------------------------------------
    # Main session activation
    # ------------------------------------------------------------------

    async def _check_stalled_threads(self) -> None:
        """Detect and activate stalled main sessions."""
        from deerflow.subagents.executor import (
            _background_tasks,
            _background_tasks_lock,
        )

        # Collect unique thread_ids that have sub-agent tasks
        with _background_tasks_lock:
            thread_task_statuses: dict[str, set[str]] = {}
            for result in _background_tasks.values():
                if not result.thread_id:
                    continue
                status_val = result.status.value if hasattr(result.status, "value") else str(result.status)
                thread_task_statuses.setdefault(result.thread_id, set()).add(status_val)

        if not thread_task_statuses:
            return

        for thread_id, statuses in thread_task_statuses.items():
            try:
                await self._check_thread_activation(thread_id, statuses)
            except Exception:
                logger.exception("Failed to check thread %s for activation", thread_id)

    async def _check_thread_activation(
        self, thread_id: str, task_statuses: set[str],
    ) -> None:
        """Check if a thread needs activation."""
        active_states = {"running", "pending"}
        if task_statuses & active_states:
            # Still has running/pending tasks — not stalled
            return

        # All tasks stopped. Check conditions:
        # 1. Not user-interrupted
        if await self._is_user_interrupted(thread_id):
            logger.debug("Thread %s: last run was user-interrupted, skipping", thread_id)
            return

        # 2. Has unfinished todos
        has_todos = await self._has_unfinished_todos(thread_id)
        if not has_todos:
            logger.debug("Thread %s: no unfinished todos, skipping", thread_id)
            return

        # All conditions met — activate
        logger.info("Activating stalled thread %s", thread_id)
        await self._activate_thread(thread_id)

    async def _activate_thread(self, thread_id: str) -> None:
        """Send a recovery message to activate a stalled thread."""
        client = self._get_client()
        if client is None:
            logger.error("Cannot activate thread %s: LangGraph client unavailable", thread_id)
            return

        # Build activation message with todo summary
        todos_summary = await self._get_todos_summary(thread_id)
        message = (
            "<session_recovery>\n"
            "所有子任务已停止，但以下任务尚未完成：\n"
            f"{todos_summary}\n\n"
            "请检查每个子任务的完成情况，继续推进未完成的工作。"
            "如果某个子任务需要继续，请使用 task() 工具重新启动。"
            "</session_recovery>"
        )

        try:
            await client.runs.wait(
                thread_id=thread_id,
                assistant_id="lead_agent",
                input={
                    "messages": [
                        {"role": "human", "content": message},
                    ],
                },
                config={"recursion_limit": 50},
            )
            logger.info("Activation message sent to thread %s", thread_id)
        except Exception:
            logger.exception("Failed to activate thread %s", thread_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> LangGraphClient | None:
        """Lazy-initialize and return the LangGraph SDK client."""
        if self._client is not None:
            return self._client
        try:
            from langgraph_sdk import get_client

            self._client = get_client(url=self._langgraph_url)
            return self._client
        except Exception:
            logger.exception("Failed to create LangGraph client")
            return None

    async def _is_user_interrupted(self, thread_id: str) -> bool:
        """Check if the last run on this thread was user-interrupted."""
        client = self._get_client()
        if client is None:
            return False

        try:
            runs = await client.runs.list(thread_id, limit=1)
            if not runs:
                return False
            last_run = runs[0]
            # Check metadata for user cancel marker
            metadata = last_run.get("metadata", {})
            if metadata.get("cancelled_by") == "user":
                return True
            # Also check if status is "interrupted" which could indicate user cancel
            # But not all interrupts are user-initiated, so we rely on metadata
            return False
        except Exception:
            logger.exception("Failed to check run status for thread %s", thread_id)
            return False

    async def _has_unfinished_todos(self, thread_id: str) -> bool:
        """Check if the thread has unfinished (in_progress or pending) todos."""
        client = self._get_client()
        if client is None:
            return False

        try:
            state = await client.threads.get_state(thread_id)
            values = state.get("values", {})
            todos = values.get("todos", [])
            for todo in todos:
                status = todo.get("status", "")
                if status in ("in_progress", "pending"):
                    return True
            return False
        except Exception:
            logger.exception("Failed to check todos for thread %s", thread_id)
            return False

    async def _get_todos_summary(self, thread_id: str) -> str:
        """Get a summary of unfinished todos for the activation message."""
        client = self._get_client()
        if client is None:
            return "(无法获取任务列表)"

        try:
            state = await client.threads.get_state(thread_id)
            values = state.get("values", {})
            todos = values.get("todos", [])
            lines = []
            for todo in todos:
                status = todo.get("status", "")
                content = todo.get("content", todo.get("description", ""))
                if status in ("in_progress", "pending"):
                    lines.append(f"- [{status}] {content}")
            return "\n".join(lines) if lines else "(无未完成任务)"
        except Exception:
            return "(无法获取任务列表)"

    @staticmethod
    def _status_value(status: Any) -> str:
        """Extract string value from a status enum or string."""
        return status.value if hasattr(status, "value") else str(status)

    @staticmethod
    def _find_session_jsonl(thread_id: str | None, task_id: str) -> str | None:
        """Locate the JSONL file for a task, or return None."""
        if not thread_id:
            return None
        try:
            from deerflow.config.paths import get_paths
            from pathlib import Path

            d = get_paths().subagent_dir(thread_id)
            p = d / f"{task_id}.jsonl"
            return str(p) if p.exists() else None
        except Exception:
            return None
