"""Session health monitor — periodic background task for the Gateway.

Detects stalled main sessions where all sub-agent tasks have finished but
unfinished todos remain.  Action: inject a continuation message into the
thread state via LangGraph SDK (no run creation or cancellation).

Also supports **auto iteration**: for configured sessions, when all todos are
completed, automatically sends an iteration prompt to start the next iteration.

Uses ``threading.Timer`` for periodic scheduling.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langgraph_sdk import LangGraphClient

logger = logging.getLogger(__name__)

# Status values considered terminal (no further action needed).
_TERMINAL_STATUSES = frozenset({"completed", "failed", "interrupted", "cancelled", "timed_out"})


@dataclass
class _IterationState:
    """In-memory state for a single auto-iteration session."""
    iteration_count: int = 0
    cycle_start_time: float | None = None


class SessionMonitor:
    """Gateway-level periodic health monitor for sessions.

    Args:
        check_interval: Seconds between check cycles (default: 180).
        stale_threshold: Seconds without JSONL update before a sub-agent
            task is considered stale (default: 300).
        langgraph_url: LangGraph Server URL for standard mode queries.
        activation_message: Global default message for 会话激活.
        session_activation_overrides: Per-thread activation_message overrides.
            Dict mapping thread_id → activation_message string.
        auto_iteration_sessions: List of session configs for 自动迭代.
            Each dict: {thread_id, iteration_prompt, max_iterations,
            max_duration_seconds, enabled}.
    """

    DEFAULT_ACTIVATION_MESSAGE = "请按要求使用子任务继续处理未完成任务计划。"

    def __init__(
        self,
        check_interval: int = 180,
        stale_threshold: int = 300,
        langgraph_url: str = "http://localhost:2024",
        activation_message: str | None = None,
        session_activation_overrides: dict[str, str] | None = None,
        auto_iteration_sessions: list[dict[str, Any]] | None = None,
    ) -> None:
        self._check_interval = check_interval
        self._stale_threshold = stale_threshold
        self._langgraph_url = langgraph_url
        self._activation_message = activation_message or self.DEFAULT_ACTIVATION_MESSAGE
        self._session_activation_overrides: dict[str, str] = session_activation_overrides or {}
        self._auto_iteration_sessions: list[dict[str, Any]] = auto_iteration_sessions or []
        self._iteration_states: dict[str, _IterationState] = {}
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
        logger.info("Health monitor check cycle triggered")
        try:
            if self._loop and not self._loop.is_closed():
                future = asyncio.run_coroutine_threadsafe(
                    self._check_all(), self._loop,
                )
                future.result(timeout=60)
            else:
                logger.warning("Health monitor: event loop is not available (loop=%s, closed=%s)",
                               self._loop is not None, self._loop.is_closed() if self._loop else "N/A")
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
        3. If nothing is active and unfinished todos exist → 会话激活
        4. If nothing is active and all todos completed → 自动迭代 (configured sessions only)
        """
        thread_ids = await self._discover_threads_with_sessions()

        # Also include auto-iteration configured threads (they may not have subagent sessions)
        for s in self._auto_iteration_sessions:
            tid = s.get("thread_id", "")
            if tid and s.get("enabled", True):
                thread_ids.add(tid)

        if not thread_ids:
            return

        for thread_id in thread_ids:
            try:
                await self._check_and_activate_thread(thread_id)
            except Exception:
                logger.exception("Failed to check thread %s", thread_id)

    async def _check_and_activate_thread(self, thread_id: str) -> None:
        """Check a single thread and activate if stalled.

        Decision tree:
        1. Skip if activation limit reached (会话激活 counter).
        2. Skip if subtask running or active run (reset counters).
        3. Skip if user-interrupted.
        4. If unfinished todos → 会话激活.
        5. Elif all todos completed → 自动迭代 (configured sessions only).
        6. Else → skip.
        """
        # 0. Check activation limit
        if self._activation_counts.get(thread_id, 0) >= self._max_activations:
            return

        # 1. Check for running subtasks
        if await self._has_running_subtask(thread_id):
            logger.debug("Thread %s: has running subtask, skipping", thread_id)
            self._activation_counts.pop(thread_id, None)
            self._iteration_states.pop(thread_id, None)
            return

        # 2. Check for active main session run
        if await self._has_active_run(thread_id):
            logger.debug("Thread %s: has active run, skipping", thread_id)
            self._activation_counts.pop(thread_id, None)
            self._iteration_states.pop(thread_id, None)
            return

        # 3. Skip if last user run was interrupted (user actively stopped)
        if await self._is_user_run_interrupted(thread_id):
            logger.debug("Thread %s: last user run was interrupted, skipping", thread_id)
            return

        # 4. 会话激活: unfinished todos
        if await self._has_unfinished_todos(thread_id):
            count = self._activation_counts.get(thread_id, 0) + 1
            msg = self._get_session_activation_message(thread_id)
            logger.info(
                "Activating stalled thread %s (attempt %d/%d)",
                thread_id, count, self._max_activations,
            )
            success = await self._activate_thread(thread_id, message=msg)
            if success:
                self._activation_counts[thread_id] = count
            else:
                logger.warning(
                    "Activation failed for thread %s, will retry next cycle",
                    thread_id,
                )
            return

        # 5. 自动迭代: all todos completed (configured sessions only)
        auto_cfg = self._get_auto_iteration_session(thread_id)
        if auto_cfg:
            await self._run_auto_iteration(thread_id, auto_cfg)
            return

        logger.debug("Thread %s: no unfinished todos, skipping", thread_id)

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

        # Check on-disk sessions: a session is considered running if it has
        # been updated recently AND its summary does not show a terminal status.
        try:
            import json

            from deerflow.config.paths import get_paths

            timeout_seconds = 900  # 15 minutes
            subagents_dir = get_paths().base_dir / "threads" / thread_id / "subagents"
            if not subagents_dir.exists():
                return False

            for jsonl_file in subagents_dir.glob("*.jsonl"):
                # Check summary for terminal status
                summary_path = jsonl_file.parent / jsonl_file.name.replace(".jsonl", ".summary.json")
                if summary_path.exists():
                    try:
                        with open(summary_path, encoding="utf-8") as f:
                            summary = json.load(f)
                        if summary.get("status", "") in _TERMINAL_STATUSES:
                            continue
                    except (json.JSONDecodeError, OSError):
                        pass
                # Also check JSONL itself for terminal marker
                if self._session_has_terminal_marker(jsonl_file):
                    continue
                # Check if recently updated (not stale)
                mtime = jsonl_file.stat().st_mtime
                if time.time() - mtime < timeout_seconds:
                    return True  # Still actively updating
        except Exception:
            logger.debug("Failed to check disk sessions for thread %s", thread_id, exc_info=True)

        return False

    # ------------------------------------------------------------------
    # Main session activation
    # ------------------------------------------------------------------

    async def _activate_thread(self, thread_id: str, message: str | None = None) -> bool:
        """Activate a stalled thread by creating a run with auto-cancellation."""
        import httpx

        msg = message if message is not None else self._activation_message

        # Fetch latest checkpoint so the run resumes from the correct state
        checkpoint_info: dict[str, Any] = {}
        client = self._get_client()
        if client is not None:
            try:
                state = await client.threads.get_state(thread_id)
                cfg = state.get("config", {}) if isinstance(state, dict) else {}
                configurable = cfg.get("configurable", {})
                checkpoint_id = configurable.get("checkpoint_id")
                checkpoint_ns = configurable.get("checkpoint_ns", "")
                if checkpoint_id:
                    checkpoint_info = {
                        "checkpoint_id": checkpoint_id,
                        "checkpoint_ns": checkpoint_ns,
                    }
            except Exception:
                logger.debug("Failed to fetch checkpoint for thread %s (non-fatal)", thread_id, exc_info=True)

        payload: dict[str, Any] = {
            "assistant_id": "lead_agent",
            "input": {
                "messages": [{
                    "type": "human",
                    "content": [{"type": "text", "text": msg}],
                    "additional_kwargs": {},
                }],
            },
            "config": {"recursion_limit": 1000},
            "context": {
                "subagent_enabled": True,
                "is_plan_mode": True,
                "thread_id": thread_id,
            },
            "metadata": {"source": "health_monitor"},
            "stream_mode": ["values"],
            "multitask_strategy": "interrupt",
            "on_disconnect": "cancel",
        }
        if checkpoint_info:
            payload["checkpoint"] = checkpoint_info

        url = f"{self._langgraph_url}/threads/{thread_id}/runs/stream"

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=10, read=30, write=10, pool=10),
            ) as http:
                resp = await http.post(url, json=payload)
                if resp.status_code == 200:
                    logger.info("Activation run completed for thread %s", thread_id)
                    return True
                else:
                    logger.error(
                        "Activation failed for thread %s: HTTP %d %s",
                        thread_id, resp.status_code, resp.text[:200],
                    )
                    return False
        except httpx.TimeoutException:
            # 30s timeout — the run was accepted and processed for a while
            logger.info("Activation request sent (timeout) for thread %s", thread_id)
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

    async def _thread_exists(self, thread_id: str) -> bool:
        """Check if thread exists in LangGraph server.

        Skips zombie threads that have disk data but were deleted from
        LangGraph, which would otherwise waste check cycles with 404 errors.
        """
        client = self._get_client()
        if client is None:
            return False
        try:
            state = await client.threads.get_state(thread_id)
            return state is not None
        except Exception:
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

    async def _has_any_todos(self, thread_id: str) -> bool:
        """Check if the thread has any todos (regardless of status)."""
        client = self._get_client()
        if client is None:
            return False
        try:
            runs = await client.runs.list(thread_id, limit=20)
            for run in runs:
                meta = run.get("metadata", {})
                if meta.get("source") == "health_monitor":
                    continue  # Skip activation runs
                return run.get("status") == "interrupted"
            return False
        except Exception:
            return False

    async def _is_user_run_failed(self, thread_id: str) -> bool:
        """Check if the last *user-initiated* run ended with error/timeout."""
        client = self._get_client()
        if client is None:
            return False
        try:
            runs = await client.runs.list(thread_id, limit=20)
            for run in runs:
                meta = run.get("metadata", {})
                if meta.get("source") == "health_monitor":
                    continue  # Skip activation runs
                return run.get("status") in ("error", "timeout")
            return False
        except Exception:
            return False

    async def _is_last_message_llm_error(self, thread_id: str) -> bool:
        """Check if the last AI message in the thread is an LLM error.

        LLM error messages carry ``additional_kwargs.llm_error = True``,
        set by ``LLMErrorHandlingMiddleware``.
        """
        client = self._get_client()
        if client is None:
            return False
        try:
            state = await client.threads.get_state(thread_id)
            values = state.get("values", {})
            todos = values.get("todos", [])
            return bool(todos)
        except Exception:
            logger.debug("Failed to check todos for thread %s", thread_id, exc_info=True)
            return False

    def _get_session_activation_message(self, thread_id: str) -> str:
        """Return per-session activation message if configured, else global default."""
        return self._session_activation_overrides.get(thread_id) or self._activation_message

    # ------------------------------------------------------------------
    # Auto iteration helpers
    # ------------------------------------------------------------------

    def _get_auto_iteration_session(self, thread_id: str) -> dict[str, Any] | None:
        """Return the auto-iteration config for thread_id, or None if not configured."""
        for s in self._auto_iteration_sessions:
            if s.get("thread_id") == thread_id and s.get("enabled", True):
                return s
        return None

    async def _run_auto_iteration(self, thread_id: str, session_cfg: dict[str, Any]) -> None:
        """Execute the 自动迭代 branch for a configured session.

        Called only when: no active run, no running subtask, not user-interrupted,
        no unfinished todos.

        - If todos list is empty → skip (no plan started).
        - If within limits → send iteration_prompt, increment counter.
        - If limits reached → reset state, send nothing.
        """
        if not await self._has_any_todos(thread_id):
            logger.debug("Auto iteration thread %s: no todos, skipping", thread_id)
            return

        iteration_prompt: str = session_cfg.get("iteration_prompt", "")
        max_iterations: int = int(session_cfg.get("max_iterations", 10))
        max_duration_seconds: float = float(session_cfg.get("max_duration_seconds", 3600))

        state = self._iteration_states.setdefault(thread_id, _IterationState())
        now = time.time()
        duration_exceeded = (
            state.cycle_start_time is not None
            and now - state.cycle_start_time >= max_duration_seconds
        )
        limits_reached = state.iteration_count >= max_iterations or duration_exceeded

        if limits_reached:
            logger.info(
                "Auto iteration thread %s: limits reached (count=%d/%d), stopping until next user message",
                thread_id, state.iteration_count, max_iterations,
            )
            return

        if state.cycle_start_time is None:
            state.cycle_start_time = now

        logger.info(
            "Auto iteration thread %s: sending iteration prompt (count=%d/%d)",
            thread_id, state.iteration_count + 1, max_iterations,
        )
        success = await self._activate_thread(thread_id, message=iteration_prompt)
        if success:
            state.iteration_count += 1

    @staticmethod
    def _session_has_terminal_marker(jsonl_path: "Path") -> bool:
        """Return True if the JSONL file's last non-empty line has a terminal status marker."""
        import json as _json
        try:
            with open(jsonl_path, encoding="utf-8") as f:
                f.seek(0, 2)
                size = f.tell()
                if size == 0:
                    return False
                read_size = min(size, 4096)
                f.seek(size - read_size)
                lines = f.readlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                status = entry.get("status")
                if status in _TERMINAL_STATUSES:
                    return True
                if "role" in entry:
                    return False  # Last message line, no terminal marker
            return False
        except OSError:
            return False

    @staticmethod
    def _status_value(status: Any) -> str:
        """Extract string value from a status enum or string."""
        return status.value if hasattr(status, "value") else str(status)
