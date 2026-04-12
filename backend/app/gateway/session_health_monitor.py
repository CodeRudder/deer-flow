"""Session health monitor — periodic background task for the Gateway.

Detects and recovers from these conditions:

1. **Zombie sub-agent tasks (in-memory)**: ``_background_tasks`` shows a
   terminal status but the JSONL session file has no matching terminal marker.
   Action: reactivate the task using ``SubagentHealthMonitor`` logic.
   IMPORTANT: We NEVER interrupt a task that is still RUNNING — LLM calls,
   tool executions, and code generation can legitimately take many minutes.

2. **Orphan sub-agent sessions (on-disk)**: JSONL file exists without a terminal
   status marker and no matching entry in ``_background_tasks`` (typically after a
   process restart).  Action: mark the session as interrupted so it stops appearing
   as "running" in API responses.

3. **Stuck LangGraph runs**: Runs in ``running`` or ``pending`` state that are older
   than ``stale_threshold`` seconds, blocking the run queue.  Action: cancel them.

4. **Stalled main session**: All sub-agent sessions are terminal, the last run was
   NOT a user-initiated interrupt, and unfinished todos remain.
   Action: send a recovery message to the Lead Agent thread (fire-and-forget).

Uses ``threading.Timer`` for periodic scheduling (same pattern as
``SubagentHealthMonitor`` and the memory update queue).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langgraph_sdk import LangGraphClient

logger = logging.getLogger(__name__)

# Status values considered terminal (no further action needed).
_TERMINAL_STATUSES = frozenset({"completed", "failed", "interrupted", "cancelled", "timed_out"})


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
        self._stale_threshold = stale_threshold  # Only used for orphan session detection
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
        """Run health checks in sequence.

        IMPORTANT: We never cancel or interrupt running tasks or runs.
        Only orphan sessions (from process restarts) are marked as interrupted,
        and stalled threads (all sessions terminal + unfinished todos) are re-activated.
        """
        await self._check_orphan_sessions()
        await self._check_stalled_threads()

    # ------------------------------------------------------------------
    # Sub-agent task monitoring (removed — never interrupt running tasks)
    # ------------------------------------------------------------------
    # Previously this section detected "zombie" sub-agent tasks based on
    # JSONL mtime staleness and cancelled them.  Removed because running
    # tasks must never be interrupted — they may be legitimately processing
    # long-running LLM calls or tool executions.

    # ------------------------------------------------------------------
    # Orphan session detection (on-disk, cross-restart)
    # ------------------------------------------------------------------

    async def _check_orphan_sessions(self) -> None:
        """Scan disk for sessions without terminal markers and no in-memory task.

        After a process restart, ``_background_tasks`` is empty.  This method
        finds sessions that were running before the restart and marks them as
        interrupted so they no longer appear as "running" in API responses.

        Only marks sessions that are older than ``stale_threshold`` seconds
        (default 5 minutes) to avoid marking sessions that were just created
        by a currently-running lead agent task.
        """
        from deerflow.subagents.executor import (
            _background_tasks,
            _background_tasks_lock,
        )

        try:
            from deerflow.config.paths import get_paths

            threads_dir = get_paths().base_dir / "threads"
        except Exception:
            logger.exception("Failed to resolve threads directory")
            return

        if not threads_dir.exists():
            return

        with _background_tasks_lock:
            known_task_ids = set(_background_tasks.keys())

        marked_count = 0
        for thread_dir in threads_dir.iterdir():
            if not thread_dir.is_dir():
                continue
            subagents_dir = thread_dir / "subagents"
            if not subagents_dir.is_dir():
                continue
            thread_id = thread_dir.name

            for jsonl_file in subagents_dir.glob("*.jsonl"):
                task_id = jsonl_file.stem
                if task_id in known_task_ids:
                    continue  # Handled by _check_subagent_tasks()

                try:
                    if self._session_has_terminal_marker(jsonl_file):
                        continue

                    # Orphan session found — only mark if truly stale
                    # (old enough that it can't be from a currently running task)
                    mtime = jsonl_file.stat().st_mtime
                    stale_seconds = time.time() - mtime
                    if stale_seconds < self._stale_threshold:
                        continue  # Still recent, maybe actively running

                    logger.info(
                        "Orphan session detected: thread=%s task=%s stale=%ds, marking interrupted",
                        thread_id,
                        task_id,
                        int(stale_seconds),
                    )
                    self._mark_session_interrupted(jsonl_file)
                    marked_count += 1
                except Exception:
                    logger.exception(
                        "Failed to check orphan session %s/%s",
                        thread_id,
                        task_id,
                    )

        if marked_count:
            logger.info("Marked %d orphan session(s) as interrupted", marked_count)

    @staticmethod
    def _session_has_terminal_marker(jsonl_path: Path) -> bool:
        """Check if a JSONL file ends with a terminal status marker."""
        try:
            # Read last 4KB to find the terminal marker
            with open(jsonl_path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                if size == 0:
                    return False
                f.seek(max(0, size - 4096))
                tail = f.read().decode("utf-8", errors="replace")
        except OSError:
            return False

        # Check the last non-empty line for a terminal status marker
        for line in reversed(tail.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                import json

                entry = json.loads(line)
                status = entry.get("status", "")
                if status in _TERMINAL_STATUSES:
                    return True
            except (json.JSONDecodeError, ValueError):
                continue
            # First valid non-empty line is the last entry
            break
        return False

    @staticmethod
    def _mark_session_interrupted(jsonl_path: Path) -> None:
        """Append an interrupted status marker to a JSONL session file."""
        import json

        marker = {"status": "interrupted", "ts": datetime.now(UTC).isoformat()}
        try:
            with open(jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(marker, ensure_ascii=False) + "\n")
        except Exception:
            logger.exception("Failed to mark session %s as interrupted", jsonl_path)

    # ------------------------------------------------------------------
    # Stuck LangGraph run detection (removed — never cancel runs)
    # ------------------------------------------------------------------
    # Previously this section cancelled LangGraph runs that appeared stuck
    # based on age thresholds.  This was removed because the monitor cannot
    # reliably distinguish between a truly stuck run and one that is actively
    # processing a complex task.  Runs should only be cancelled by user action.

    # ------------------------------------------------------------------
    # Main session activation
    # ------------------------------------------------------------------

    async def _check_stalled_threads(self) -> None:
        """Detect and activate stalled main sessions.

        Discovers threads from both _background_tasks and disk scan.
        """
        thread_ids = await self._discover_threads_with_sessions()
        if not thread_ids:
            return

        for thread_id in thread_ids:
            try:
                await self._check_thread_activation(thread_id)
            except Exception:
                logger.exception("Failed to check thread %s for activation", thread_id)

    async def _check_thread_activation(self, thread_id: str) -> None:
        """Check if a thread needs activation."""
        # Skip if thread already has an active run (user is using it)
        if await self._has_active_run(thread_id):
            logger.debug("Thread %s: has active run, skipping activation", thread_id)
            return

        # Check if all sessions are terminal
        if not await self._all_sessions_terminal(thread_id):
            return  # Still has active sessions

        # Check conditions:
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

    async def _all_sessions_terminal(self, thread_id: str) -> bool:
        """Check if all sub-agent sessions for a thread have terminal status."""
        try:
            from deerflow.config.paths import get_paths

            subagents_dir = get_paths().base_dir / "threads" / thread_id / "subagents"
        except Exception:
            return True

        if not subagents_dir.exists():
            return True

        for jsonl_file in subagents_dir.glob("*.jsonl"):
            if not self._session_has_terminal_marker(jsonl_file):
                return False
        return True

    async def _activate_thread(self, thread_id: str) -> None:
        """Send a recovery message to activate a stalled thread.

        Uses the streaming API endpoint (POST /threads/{id}/runs/stream) so
        the response is visible in the frontend.  Fire-and-forget: sends the
        request and closes the connection — the server keeps processing.
        """
        message = (
            "<session_recovery>\n"
            "服务已经重启，请继续处理未完成任务。\n"
            "</session_recovery>"
        )

        async def _send() -> None:
            import httpx

            url = f"{self._langgraph_url}/threads/{thread_id}/runs/stream"
            payload = {
                "assistant_id": "lead_agent",
                "input": {
                    "messages": [
                        {"type": "human", "content": message},
                    ],
                },
                "config": {"recursion_limit": 500},
                "context": {
                    "subagent_enabled": True,
                    "is_plan_mode": True,
                },
                "stream_mode": ["values"],
            }
            try:
                # Short timeout: just confirm the server accepted the request
                async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5, read=5, write=5, pool=5)) as http:
                    resp = await http.post(url, json=payload)
                    if resp.status_code == 200:
                        logger.info("Activation message accepted for thread %s", thread_id)
                    else:
                        logger.error(
                            "Activation failed for thread %s: HTTP %d %s",
                            thread_id,
                            resp.status_code,
                            resp.text[:200],
                        )
            except httpx.TimeoutException:
                # Timeout is expected for streaming endpoints — request was sent
                logger.info("Activation request sent (timeout expected) for thread %s", thread_id)
            except Exception:
                logger.exception("Failed to activate thread %s", thread_id)

        asyncio.create_task(_send())
        logger.info("Activation message queued for thread %s", thread_id)

    # ------------------------------------------------------------------
    # Thread discovery
    # ------------------------------------------------------------------

    async def _discover_threads_with_sessions(self) -> set[str]:
        """Discover thread IDs that have sub-agent sessions.

        Combines threads from _background_tasks and disk scan.
        """
        thread_ids: set[str] = set()

        # 1. From in-memory tasks
        try:
            from deerflow.subagents.executor import (
                _background_tasks,
                _background_tasks_lock,
            )

            with _background_tasks_lock:
                for result in _background_tasks.values():
                    if result.thread_id:
                        thread_ids.add(result.thread_id)
        except Exception:
            logger.exception("Failed to read _background_tasks")

        # 2. From disk scan
        try:
            from deerflow.config.paths import get_paths

            threads_dir = get_paths().base_dir / "threads"
            if threads_dir.exists():
                for thread_dir in threads_dir.iterdir():
                    if not thread_dir.is_dir():
                        continue
                    subagents_dir = thread_dir / "subagents"
                    if subagents_dir.is_dir() and any(subagents_dir.glob("*.jsonl")):
                        thread_ids.add(thread_dir.name)
        except Exception:
            logger.exception("Failed to scan threads directory")

        return thread_ids

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

    async def _has_active_run(self, thread_id: str) -> bool:
        """Check if the thread has any running or pending runs.

        A run is considered active only if it is running/pending AND
        recently created (within 30 minutes).  Stale "running" runs
        that survived a LangGraph server restart are treated as dead.
        """
        client = self._get_client()
        if client is None:
            return False

        try:
            from datetime import datetime, timezone, timedelta

            stale_threshold = timedelta(minutes=30)

            runs = await client.runs.list(thread_id, limit=10)
            for run in runs:
                if run.get("status") in ("running", "pending"):
                    created_at = run.get("created_at")
                    if created_at:
                        try:
                            if isinstance(created_at, str):
                                created = datetime.fromisoformat(created_at)
                            else:
                                created = created_at
                            # Ensure timezone-aware comparison
                            if created.tzinfo is None:
                                created = created.replace(tzinfo=timezone.utc)
                            age = datetime.now(tz=timezone.utc) - created
                            if age > stale_threshold:
                                logger.info(
                                    "Thread %s: stale run %s (%s, age=%.0f min), treating as dead",
                                    thread_id,
                                    run.get("run_id", "?")[:12],
                                    run.get("status"),
                                    age.total_seconds() / 60,
                                )
                                continue
                        except (ValueError, TypeError):
                            pass  # If we can't parse, assume active
                    return True
            return False
        except Exception:
            logger.debug("Failed to check active runs for thread %s", thread_id, exc_info=True)
            return False

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
            metadata = last_run.get("metadata", {})
            if metadata.get("cancelled_by") == "user":
                return True
            return False
        except Exception:
            logger.debug("Failed to check run status for thread %s", thread_id, exc_info=True)
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
            logger.warning("Failed to check todos for thread %s", thread_id, exc_info=True)
            return False

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

            d = get_paths().subagent_dir(thread_id)
            p = d / f"{task_id}.jsonl"
            return str(p) if p.exists() else None
        except Exception:
            return None
