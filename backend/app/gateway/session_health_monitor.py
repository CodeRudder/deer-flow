"""Session health monitor — periodic background task for the Gateway.

Detects stalled main sessions where all sub-agent tasks have finished but
unfinished todos remain.  Action: inject a continuation message into the
thread state via LangGraph SDK (no run creation or cancellation).

Uses ``threading.Timer`` for periodic scheduling.
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
        check_interval: Seconds between check cycles (default: 180).
        stale_threshold: Seconds without JSONL update before a sub-agent
            task is considered stale (default: 300).
        langgraph_url: LangGraph Server URL for standard mode queries.
    """

    DEFAULT_ACTIVATION_MESSAGE = "请按要求使用子任务继续处理未完成任务计划。"

    def __init__(
        self,
        check_interval: int = 180,
        stale_threshold: int = 300,
        langgraph_url: str = "http://localhost:2024",
        activation_message: str | None = None,
    ) -> None:
        self._check_interval = check_interval
        self._stale_threshold = stale_threshold
        self._langgraph_url = langgraph_url
        self._activation_message = activation_message or self.DEFAULT_ACTIVATION_MESSAGE
        self._timer: threading.Timer | None = None
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: LangGraphClient | None = None
        self._activation_counts: dict[str, int] = {}  # thread_id → activation attempt count
        self._max_activations: int = 5

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
        """Run health checks and activate stalled threads.

        For each thread with sub-agent sessions:
        1. Check if any subtask is still running (in-memory or on-disk)
        2. Check if the main session has an active run
        3. If nothing is active and unfinished todos exist → send activation message
        """
        thread_ids = await self._discover_threads_with_sessions()
        if not thread_ids:
            return

        for thread_id in thread_ids:
            try:
                await self._check_and_activate_thread(thread_id)
            except Exception:
                logger.exception("Failed to check thread %s", thread_id)

    async def _check_and_activate_thread(self, thread_id: str) -> None:
        """Check a single thread and activate if stalled."""
        # 0. Check activation limit
        if self._activation_counts.get(thread_id, 0) >= self._max_activations:
            return

        # 1. Check for running subtasks
        if await self._has_running_subtask(thread_id):
            logger.debug("Thread %s: has running subtask, skipping", thread_id)
            self._activation_counts.pop(thread_id, None)
            return

        # 2. Check for active main session run
        if await self._has_active_run(thread_id):
            logger.debug("Thread %s: has active run, skipping", thread_id)
            self._activation_counts.pop(thread_id, None)
            return

        # 3. Check if we should activate
        if await self._is_user_interrupted(thread_id):
            logger.debug("Thread %s: last run was user-interrupted, skipping", thread_id)
            return

        if not await self._has_unfinished_todos(thread_id):
            logger.debug("Thread %s: no unfinished todos, skipping", thread_id)
            return

        # All conditions met — activate
        count = self._activation_counts.get(thread_id, 0) + 1
        logger.info(
            "Activating stalled thread %s (attempt %d/%d)",
            thread_id, count, self._max_activations,
        )
        success = await self._activate_thread(thread_id)
        if success:
            self._activation_counts[thread_id] = count
        else:
            logger.warning(
                "Activation failed for thread %s, will retry next cycle",
                thread_id,
            )

    async def _has_running_subtask(self, thread_id: str) -> bool:
        """Check if any subtask for this thread is still running."""
        # Check in-memory tasks
        try:
            from deerflow.subagents.executor import (
                _background_tasks,
                _background_tasks_lock,
            )

            with _background_tasks_lock:
                for result in _background_tasks.values():
                    if result.thread_id == thread_id:
                        status = result.status.value if hasattr(result.status, "value") else str(result.status)
                        if status == "running":
                            return True
        except Exception:
            logger.debug("Failed to check background tasks", exc_info=True)

        # Check on-disk sessions
        try:
            from deerflow.config.paths import get_paths

            timeout_seconds = 900  # 15 minutes
            subagents_dir = get_paths().base_dir / "threads" / thread_id / "subagents"
            if not subagents_dir.exists():
                return False

            for jsonl_file in subagents_dir.glob("*.jsonl"):
                if self._session_has_terminal_marker(jsonl_file):
                    continue
                # Also check summary file — a race condition can leave
                # messages appended after the JSONL terminal marker
                if self._summary_has_terminal_status(jsonl_file):
                    continue
                # No terminal marker — check if recently updated
                mtime = jsonl_file.stat().st_mtime
                stale_seconds = time.time() - mtime
                if stale_seconds < timeout_seconds:
                    return True  # Still actively updating
        except Exception:
            logger.debug("Failed to check disk sessions for thread %s", thread_id, exc_info=True)

        return False

    # ------------------------------------------------------------------
    # Orphan session detection (on-disk, cross-restart)
    # ------------------------------------------------------------------

    async def _check_orphan_sessions(self) -> None:
        """Scan disk for sessions without terminal markers and no in-memory task."""
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
                    continue

                try:
                    if self._session_has_terminal_marker(jsonl_file):
                        continue

                    mtime = jsonl_file.stat().st_mtime
                    stale_seconds = time.time() - mtime
                    if stale_seconds < self._stale_threshold:
                        continue

                    logger.info(
                        "Orphan session detected: thread=%s task=%s stale=%ds, marking interrupted",
                        thread_id, task_id, int(stale_seconds),
                    )
                    self._mark_session_interrupted(jsonl_file)
                    marked_count += 1
                except Exception:
                    logger.exception("Failed to check orphan session %s/%s", thread_id, task_id)

        if marked_count:
            logger.info("Marked %d orphan session(s) as interrupted", marked_count)

    @staticmethod
    def _session_has_terminal_marker(jsonl_path: Path) -> bool:
        """Check if a JSONL file ends with a terminal status marker."""
        try:
            with open(jsonl_path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                if size == 0:
                    return False
                f.seek(max(0, size - 4096))
                tail = f.read().decode("utf-8", errors="replace")
        except OSError:
            return False

        for line in reversed(tail.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                import json

                entry = json.loads(line)
                if entry.get("status", "") in _TERMINAL_STATUSES:
                    return True
            except (json.JSONDecodeError, ValueError):
                continue
            break
        return False

    @staticmethod
    def _summary_has_terminal_status(jsonl_path: Path) -> bool:
        """Check if the summary file alongside a JSONL indicates a terminal state.

        Handles the race condition where a dying background thread appends
        messages to the JSONL *after* the startup cleanup wrote the terminal
        marker, effectively burying it beneath non-status lines.
        """
        import json

        summary_path = jsonl_path.parent / jsonl_path.name.replace(".jsonl", ".summary.json")
        if not summary_path.exists():
            return False
        try:
            with open(summary_path, encoding="utf-8") as f:
                summary = json.load(f)
            return summary.get("status", "") in _TERMINAL_STATUSES
        except (json.JSONDecodeError, OSError):
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
    # Main session activation
    # ------------------------------------------------------------------

    async def _activate_thread(self, thread_id: str) -> bool:
        """Activate a stalled thread by simulating user input.

        Replicates the exact API call the frontend makes when a user sends a
        message: ``POST /threads/{id}/runs/stream``.  Uses
        ``multitask_strategy: "interrupt"`` so any stale zombie runs are
        automatically interrupted, preventing the new run from getting stuck
        in ``pending``.

        Returns True if the activation request was accepted (HTTP 200 or
        timeout expected for streaming), False on error.
        """
        import httpx

        client = self._get_client()
        if client is None:
            logger.error("Cannot activate thread %s: no LangGraph client", thread_id)
            return False

        # Get latest checkpoint to resume from
        checkpoint = None
        try:
            state = await client.threads.get_state(thread_id)
            configurable = state.get("config", {}).get("configurable", {})
            cp_id = configurable.get("checkpoint_id")
            if cp_id:
                checkpoint = {
                    "checkpoint_id": cp_id,
                    "checkpoint_ns": configurable.get("checkpoint_ns", ""),
                }
        except Exception:
            logger.debug("Failed to get checkpoint for thread %s", thread_id, exc_info=True)

        message = self._activation_message

        payload: dict[str, Any] = {
            "assistant_id": "lead_agent",
            "input": {
                "messages": [{
                    "type": "human",
                    "content": [{"type": "text", "text": message}],
                    "additional_kwargs": {},
                }],
            },
            "config": {"recursion_limit": 1000},
            "context": {
                "subagent_enabled": True,
                "is_plan_mode": True,
                "thread_id": thread_id,
            },
            "stream_mode": ["values"],
            "stream_subgraphs": True,
            "stream_resumable": True,
            "on_disconnect": "continue",
            "multitask_strategy": "interrupt",
        }
        if checkpoint:
            payload["checkpoint"] = checkpoint

        url = f"{self._langgraph_url}/threads/{thread_id}/runs/stream"

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5, read=5, write=5, pool=5),
            ) as http:
                resp = await http.post(url, json=payload)
                if resp.status_code == 200:
                    logger.info("Activation run accepted for thread %s", thread_id)
                    return True
                else:
                    logger.error(
                        "Activation failed for thread %s: HTTP %d %s",
                        thread_id, resp.status_code, resp.text[:200],
                    )
                    return False
        except httpx.TimeoutException:
            # Timeout is expected for streaming endpoints — request was sent
            logger.info("Activation request sent (timeout expected) for thread %s", thread_id)
            return True
        except Exception:
            logger.exception("Failed to activate thread %s", thread_id)
            return False

    # ------------------------------------------------------------------
    # Thread discovery
    # ------------------------------------------------------------------

    async def _discover_threads_with_sessions(self) -> set[str]:
        """Discover thread IDs that have sub-agent sessions OR unfinished todos."""
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

        # 2. From disk scan (threads with subagent JSONL files)
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

        # 3. From LangGraph store
        try:
            client = self._get_client()
            if client:
                store_threads = await client.threads.search(limit=20)
                for t in store_threads:
                    tid = t.get("thread_id") if isinstance(t, dict) else str(t)
                    if tid:
                        thread_ids.add(tid)
        except Exception:
            logger.debug("Failed to search threads from LangGraph store", exc_info=True)

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
            return metadata.get("cancelled_by") == "user"
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
