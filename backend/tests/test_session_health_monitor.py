"""Tests for SessionHealthMonitor."""

from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.gateway.session_health_monitor import SessionHealthMonitor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_subagent_result(
    task_id="task-1",
    thread_id="thread-1",
    status="running",
    subagent_name="general-purpose",
    description="test task",
    original_prompt="do something",
):
    """Create a mock SubagentResult with a real-ish status.

    conftest.py mocks deerflow.subagents.executor at module level, so
    SubagentStatus is a MagicMock.  We use a simple namespace with .value
    instead so the monitor's string-based status comparison works.
    """
    from types import SimpleNamespace

    result = MagicMock()
    result.task_id = task_id
    result.thread_id = thread_id
    result.status = SimpleNamespace(value=status)
    result.subagent_name = subagent_name
    result.description = description
    result.original_prompt = original_prompt
    return result


def _patch_background_tasks(tasks: dict):
    """Return a context manager that patches _background_tasks."""
    return patch("deerflow.subagents.executor._background_tasks", tasks)


def _patch_lock():
    import threading
    return patch("deerflow.subagents.executor._background_tasks_lock", threading.Lock())


# ---------------------------------------------------------------------------
# Sub-agent zombie detection
# ---------------------------------------------------------------------------


class TestSubagentZombieDetection:

    def test_reactivates_stale_running_task(self, tmp_path):
        """A RUNNING task with stale JSONL should be reactivated."""
        jsonl = tmp_path / "task-1.jsonl"
        jsonl.write_text('{"role": "ai", "content": "working"}\n', encoding="utf-8")
        old_time = time.time() - 600
        os.utime(jsonl, (old_time, old_time))

        result = _make_subagent_result()
        monitor = SessionHealthMonitor(stale_threshold=300)

        with (
            patch.object(monitor, "_find_session_jsonl", return_value=str(jsonl)),
            _patch_background_tasks({"task-1": result}),
            _patch_lock(),
            patch.object(monitor, "_reactivate_subagent") as mock_reactivate,
        ):
            asyncio.run(monitor._check_subagent_tasks())

        mock_reactivate.assert_called_once()
        assert "stale" in mock_reactivate.call_args[0][2]

    def test_skips_fresh_running_task(self, tmp_path):
        """A RUNNING task with fresh JSONL should NOT be reactivated."""
        jsonl = tmp_path / "task-1.jsonl"
        jsonl.write_text('{"role": "ai", "content": "working"}\n', encoding="utf-8")

        result = _make_subagent_result()
        monitor = SessionHealthMonitor(stale_threshold=300)

        with (
            patch.object(monitor, "_find_session_jsonl", return_value=str(jsonl)),
            _patch_background_tasks({"task-1": result}),
            _patch_lock(),
            patch.object(monitor, "_reactivate_subagent") as mock_reactivate,
        ):
            asyncio.run(monitor._check_subagent_tasks())

        mock_reactivate.assert_not_called()

    def test_skips_completed_task(self):
        """A COMPLETED task should be skipped."""
        result = _make_subagent_result(status="completed")
        monitor = SessionHealthMonitor()

        with (
            _patch_background_tasks({"task-1": result}),
            _patch_lock(),
        ):
            asyncio.run(monitor._check_subagent_tasks())

    def test_skips_task_without_jsonl(self):
        """A task without JSONL file should be skipped."""
        result = _make_subagent_result()
        monitor = SessionHealthMonitor()

        with (
            patch.object(monitor, "_find_session_jsonl", return_value=None),
            _patch_background_tasks({"task-1": result}),
            _patch_lock(),
            patch.object(monitor, "_reactivate_subagent") as mock_reactivate,
        ):
            asyncio.run(monitor._check_subagent_tasks())

        mock_reactivate.assert_not_called()

    def test_no_running_tasks_is_noop(self):
        """When no tasks are running, check is a no-op."""
        monitor = SessionHealthMonitor()
        with (
            _patch_background_tasks({}),
            _patch_lock(),
        ):
            asyncio.run(monitor._check_subagent_tasks())


# ---------------------------------------------------------------------------
# Main session activation
# ---------------------------------------------------------------------------


class TestMainSessionActivation:

    def test_activates_when_all_tasks_stopped_and_todos_incomplete(self):
        """Activate when: all tasks stopped + not user-interrupted + has unfinished todos."""
        result = _make_subagent_result(status="completed")
        monitor = SessionHealthMonitor()

        async def _test():
            with (
                _patch_background_tasks({"task-1": result}),
                _patch_lock(),
            ):
                monitor._is_user_interrupted = AsyncMock(return_value=False)
                monitor._has_unfinished_todos = AsyncMock(return_value=True)
                monitor._activate_thread = AsyncMock()

                await monitor._check_stalled_threads()

            monitor._activate_thread.assert_called_once_with("thread-1")

        asyncio.run(_test())

    def test_does_not_activate_when_tasks_still_running(self):
        """Do NOT activate when sub-agent tasks are still running."""
        result = _make_subagent_result(status="running")
        monitor = SessionHealthMonitor()

        async def _test():
            with (
                _patch_background_tasks({"task-1": result}),
                _patch_lock(),
            ):
                monitor._activate_thread = AsyncMock()
                await monitor._check_stalled_threads()

            monitor._activate_thread.assert_not_called()

        asyncio.run(_test())

    def test_does_not_activate_when_user_interrupted(self):
        """Do NOT activate when last run was user-interrupted."""
        result = _make_subagent_result(status="completed")
        monitor = SessionHealthMonitor()

        async def _test():
            with (
                _patch_background_tasks({"task-1": result}),
                _patch_lock(),
            ):
                monitor._is_user_interrupted = AsyncMock(return_value=True)
                monitor._activate_thread = AsyncMock()

                await monitor._check_stalled_threads()

            monitor._activate_thread.assert_not_called()

        asyncio.run(_test())

    def test_does_not_activate_when_no_unfinished_todos(self):
        """Do NOT activate when all todos are completed."""
        result = _make_subagent_result(status="completed")
        monitor = SessionHealthMonitor()

        async def _test():
            with (
                _patch_background_tasks({"task-1": result}),
                _patch_lock(),
            ):
                monitor._is_user_interrupted = AsyncMock(return_value=False)
                monitor._has_unfinished_todos = AsyncMock(return_value=False)
                monitor._activate_thread = AsyncMock()

                await monitor._check_stalled_threads()

            monitor._activate_thread.assert_not_called()

        asyncio.run(_test())

    def test_no_tasks_is_noop(self):
        """When there are no sub-agent tasks, check is a no-op."""
        monitor = SessionHealthMonitor()

        async def _test():
            with (
                _patch_background_tasks({}),
                _patch_lock(),
            ):
                monitor._activate_thread = AsyncMock()
                await monitor._check_stalled_threads()

            monitor._activate_thread.assert_not_called()

        asyncio.run(_test())


# ---------------------------------------------------------------------------
# Timer scheduling
# ---------------------------------------------------------------------------


class TestTimerScheduling:

    def test_start_schedules_first_check(self):
        loop = asyncio.new_event_loop()
        monitor = SessionHealthMonitor(check_interval=1)
        monitor.start(loop)

        assert monitor._running is True
        assert monitor._timer is not None

        monitor.stop()
        loop.close()

    def test_stop_cancels_timer(self):
        loop = asyncio.new_event_loop()
        monitor = SessionHealthMonitor(check_interval=1)
        monitor.start(loop)
        monitor.stop()

        assert monitor._running is False
        assert monitor._timer is None
        loop.close()

    def test_check_cycle_reschedules_on_exception(self):
        """Check cycle should reschedule even when loop is closed."""
        monitor = SessionHealthMonitor(check_interval=1)
        monitor._running = True
        monitor._loop = MagicMock()
        monitor._loop.is_closed.return_value = True

        monitor._check_cycle()

        # Should have rescheduled
        assert monitor._timer is not None
        monitor.stop()

    def test_stop_is_idempotent(self):
        monitor = SessionHealthMonitor()
        monitor.stop()  # Should not raise
        assert monitor._running is False

    def test_find_session_jsonl_returns_none_without_thread_id(self):
        result = SessionHealthMonitor._find_session_jsonl(None, "task-1")
        assert result is None
