"""Tests for SessionHealthMonitor."""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
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


def _write_jsonl(path: Path, messages: list[dict], terminal_status: str | None = None):
    """Write a JSONL file with given messages and optional terminal marker."""
    with open(path, "w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        if terminal_status:
            f.write(json.dumps({"status": terminal_status}, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Sub-agent zombie detection (in-memory)
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
# Orphan session detection (on-disk, cross-restart)
# ---------------------------------------------------------------------------


class TestOrphanSessionDetection:

    def test_marks_orphan_stale_session_as_interrupted(self, tmp_path):
        """An orphan session (no _background_tasks entry) with stale JSONL should be marked interrupted."""
        threads_dir = tmp_path / "threads" / "thread-1" / "subagents"
        threads_dir.mkdir(parents=True)
        jsonl = threads_dir / "task-orphan.jsonl"
        _write_jsonl(jsonl, [{"role": "ai", "content": "working"}])
        old_time = time.time() - 600
        os.utime(jsonl, (old_time, old_time))

        monitor = SessionHealthMonitor(stale_threshold=300)

        with (
            patch("deerflow.config.paths.get_paths") as mock_paths,
            _patch_background_tasks({}),
            _patch_lock(),
        ):
            mock_paths.return_value.base_dir = tmp_path
            asyncio.run(monitor._check_orphan_sessions())

        # Check terminal marker was appended
        lines = jsonl.read_text(encoding="utf-8").strip().split("\n")
        last = json.loads(lines[-1])
        assert last["status"] == "interrupted"

    def test_skips_session_with_terminal_marker(self, tmp_path):
        """A session with a terminal status marker should NOT be touched."""
        threads_dir = tmp_path / "threads" / "thread-1" / "subagents"
        threads_dir.mkdir(parents=True)
        jsonl = threads_dir / "task-done.jsonl"
        _write_jsonl(jsonl, [{"role": "ai", "content": "done"}], terminal_status="completed")

        monitor = SessionHealthMonitor(stale_threshold=300)

        with (
            patch("deerflow.config.paths.get_paths") as mock_paths,
            _patch_background_tasks({}),
            _patch_lock(),
        ):
            mock_paths.return_value.base_dir = tmp_path
            asyncio.run(monitor._check_orphan_sessions())

        # Should NOT have added anything
        lines = jsonl.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2  # original message + terminal marker

    def test_skips_session_in_background_tasks(self, tmp_path):
        """A session that has a _background_tasks entry should NOT be treated as orphan."""
        threads_dir = tmp_path / "threads" / "thread-1" / "subagents"
        threads_dir.mkdir(parents=True)
        jsonl = threads_dir / "task-active.jsonl"
        _write_jsonl(jsonl, [{"role": "ai", "content": "working"}])

        result = _make_subagent_result(task_id="task-active")
        monitor = SessionHealthMonitor(stale_threshold=300)

        with (
            patch("deerflow.config.paths.get_paths") as mock_paths,
            _patch_background_tasks({"task-active": result}),
            _patch_lock(),
        ):
            mock_paths.return_value.base_dir = tmp_path
            asyncio.run(monitor._check_orphan_sessions())

        # Should NOT have added terminal marker
        lines = jsonl.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1  # Only original message

    def test_skips_fresh_orphan_session(self, tmp_path):
        """A recently updated orphan session should NOT be marked (might just be starting)."""
        threads_dir = tmp_path / "threads" / "thread-1" / "subagents"
        threads_dir.mkdir(parents=True)
        jsonl = threads_dir / "task-fresh.jsonl"
        _write_jsonl(jsonl, [{"role": "ai", "content": "just started"}])

        monitor = SessionHealthMonitor(stale_threshold=300)

        with (
            patch("deerflow.config.paths.get_paths") as mock_paths,
            _patch_background_tasks({}),
            _patch_lock(),
        ):
            mock_paths.return_value.base_dir = tmp_path
            asyncio.run(monitor._check_orphan_sessions())

        lines = jsonl.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1  # Only original message, no terminal marker

    def test_no_threads_dir_is_noop(self, tmp_path):
        """When threads directory doesn't exist, check is a no-op."""
        monitor = SessionHealthMonitor(stale_threshold=300)

        with (
            patch("deerflow.config.paths.get_paths") as mock_paths,
            _patch_background_tasks({}),
            _patch_lock(),
        ):
            mock_paths.return_value.base_dir = tmp_path / "nonexistent"
            asyncio.run(monitor._check_orphan_sessions())

    def test_session_has_terminal_marker_detects_completed(self, tmp_path):
        """_session_has_terminal_marker should detect 'completed' status."""
        jsonl = tmp_path / "test.jsonl"
        _write_jsonl(jsonl, [{"role": "ai", "content": "done"}], terminal_status="completed")
        assert SessionHealthMonitor._session_has_terminal_marker(jsonl) is True

    def test_session_has_terminal_marker_detects_interrupted(self, tmp_path):
        """_session_has_terminal_marker should detect 'interrupted' status."""
        jsonl = tmp_path / "test.jsonl"
        _write_jsonl(jsonl, [{"role": "ai", "content": "stopped"}], terminal_status="interrupted")
        assert SessionHealthMonitor._session_has_terminal_marker(jsonl) is True

    def test_session_has_terminal_marker_returns_false_for_active(self, tmp_path):
        """_session_has_terminal_marker should return False for active session."""
        jsonl = tmp_path / "test.jsonl"
        _write_jsonl(jsonl, [{"role": "ai", "content": "working"}])
        assert SessionHealthMonitor._session_has_terminal_marker(jsonl) is False


# ---------------------------------------------------------------------------
# Stuck run detection and cleanup
# ---------------------------------------------------------------------------


class TestStuckRunDetection:

    def test_cancels_stuck_running_run(self):
        """A run in 'running' state older than threshold should be cancelled."""
        monitor = SessionHealthMonitor(stale_threshold=300)
        old_time = datetime(2020, 1, 1, tzinfo=UTC).isoformat()

        mock_client = AsyncMock()
        mock_client.runs.list.return_value = [
            {"run_id": "run-1", "status": "running", "created_at": old_time},
        ]
        mock_client.runs.cancel = AsyncMock()

        async def _test():
            count = await monitor._cancel_stuck_runs_for_thread(
                mock_client, "thread-1", time.time(),
            )
            assert count == 1
            mock_client.runs.cancel.assert_called_once_with("thread-1", "run-1")

        asyncio.run(_test())

    def test_cancels_stuck_pending_run(self):
        """A run in 'pending' state older than threshold should be cancelled."""
        monitor = SessionHealthMonitor(stale_threshold=300)
        old_time = datetime(2020, 1, 1, tzinfo=UTC).isoformat()

        mock_client = AsyncMock()
        mock_client.runs.list.return_value = [
            {"run_id": "run-1", "status": "pending", "created_at": old_time},
        ]
        mock_client.runs.cancel = AsyncMock()

        async def _test():
            count = await monitor._cancel_stuck_runs_for_thread(
                mock_client, "thread-1", time.time(),
            )
            assert count == 1

        asyncio.run(_test())

    def test_skips_recent_runs(self):
        """A recent run should NOT be cancelled."""
        monitor = SessionHealthMonitor(stale_threshold=300)
        recent_time = datetime.now(UTC).isoformat()

        mock_client = AsyncMock()
        mock_client.runs.list.return_value = [
            {"run_id": "run-1", "status": "running", "created_at": recent_time},
        ]
        mock_client.runs.cancel = AsyncMock()

        async def _test():
            count = await monitor._cancel_stuck_runs_for_thread(
                mock_client, "thread-1", time.time(),
            )
            assert count == 0
            mock_client.runs.cancel.assert_not_called()

        asyncio.run(_test())

    def test_skips_completed_runs(self):
        """Completed/interrupted/success runs should be skipped."""
        monitor = SessionHealthMonitor(stale_threshold=300)
        old_time = datetime(2020, 1, 1, tzinfo=UTC).isoformat()

        mock_client = AsyncMock()
        mock_client.runs.list.return_value = [
            {"run_id": "run-1", "status": "success", "created_at": old_time},
            {"run_id": "run-2", "status": "interrupted", "created_at": old_time},
            {"run_id": "run-3", "status": "error", "created_at": old_time},
        ]
        mock_client.runs.cancel = AsyncMock()

        async def _test():
            count = await monitor._cancel_stuck_runs_for_thread(
                mock_client, "thread-1", time.time(),
            )
            assert count == 0

        asyncio.run(_test())

    def test_handles_list_failure_gracefully(self):
        """If runs.list fails, should return 0 without raising."""
        monitor = SessionHealthMonitor(stale_threshold=300)
        mock_client = AsyncMock()
        mock_client.runs.list.side_effect = Exception("connection refused")

        async def _test():
            count = await monitor._cancel_stuck_runs_for_thread(
                mock_client, "thread-1", time.time(),
            )
            assert count == 0

        asyncio.run(_test())

    def test_skips_own_run_ids(self):
        """Runs created by this monitor should NOT be cancelled."""
        monitor = SessionHealthMonitor(stale_threshold=300)
        monitor._our_run_ids.add("our-run-1")
        old_time = datetime(2020, 1, 1, tzinfo=UTC).isoformat()

        mock_client = AsyncMock()
        mock_client.runs.list.return_value = [
            {"run_id": "our-run-1", "status": "running", "created_at": old_time},
            {"run_id": "other-run", "status": "running", "created_at": old_time},
        ]
        mock_client.runs.cancel = AsyncMock()

        async def _test():
            count = await monitor._cancel_stuck_runs_for_thread(
                mock_client, "thread-1", time.time(),
            )
            assert count == 1  # Only other-run cancelled
            mock_client.runs.cancel.assert_called_once_with("thread-1", "other-run")

        asyncio.run(_test())


# ---------------------------------------------------------------------------
# Main session activation
# ---------------------------------------------------------------------------


class TestMainSessionActivation:

    def test_activates_when_all_sessions_terminal_and_todos_incomplete(self, tmp_path):
        """Activate when: all sessions terminal + not user-interrupted + has unfinished todos."""
        threads_dir = tmp_path / "threads" / "thread-1" / "subagents"
        threads_dir.mkdir(parents=True)
        jsonl = threads_dir / "task-1.jsonl"
        _write_jsonl(jsonl, [{"role": "ai", "content": "done"}], terminal_status="completed")

        monitor = SessionHealthMonitor()

        async def _test():
            with (
                patch.object(monitor, "_discover_threads_with_sessions", return_value={"thread-1"}),
                patch.object(monitor, "_is_user_interrupted", return_value=False),
                patch.object(monitor, "_has_unfinished_todos", return_value=True),
                patch.object(monitor, "_all_sessions_terminal", return_value=True),
                patch("deerflow.config.paths.get_paths") as mock_paths,
            ):
                mock_paths.return_value.base_dir = tmp_path
                monitor._activate_thread = AsyncMock()
                await monitor._check_stalled_threads()

            monitor._activate_thread.assert_called_once_with("thread-1")

        asyncio.run(_test())

    def test_does_not_activate_when_sessions_still_active(self):
        """Do NOT activate when some sessions are still active."""
        monitor = SessionHealthMonitor()

        async def _test():
            with (
                patch.object(monitor, "_discover_threads_with_sessions", return_value={"thread-1"}),
                patch.object(monitor, "_all_sessions_terminal", return_value=False),
            ):
                monitor._activate_thread = AsyncMock()
                await monitor._check_stalled_threads()

            monitor._activate_thread.assert_not_called()

        asyncio.run(_test())

    def test_does_not_activate_when_user_interrupted(self):
        """Do NOT activate when last run was user-interrupted."""
        monitor = SessionHealthMonitor()

        async def _test():
            with (
                patch.object(monitor, "_discover_threads_with_sessions", return_value={"thread-1"}),
                patch.object(monitor, "_all_sessions_terminal", return_value=True),
                patch.object(monitor, "_is_user_interrupted", return_value=True),
            ):
                monitor._activate_thread = AsyncMock()
                await monitor._check_stalled_threads()

            monitor._activate_thread.assert_not_called()

        asyncio.run(_test())

    def test_does_not_activate_when_no_unfinished_todos(self):
        """Do NOT activate when all todos are completed."""
        monitor = SessionHealthMonitor()

        async def _test():
            with (
                patch.object(monitor, "_discover_threads_with_sessions", return_value={"thread-1"}),
                patch.object(monitor, "_all_sessions_terminal", return_value=True),
                patch.object(monitor, "_is_user_interrupted", return_value=False),
                patch.object(monitor, "_has_unfinished_todos", return_value=False),
            ):
                monitor._activate_thread = AsyncMock()
                await monitor._check_stalled_threads()

            monitor._activate_thread.assert_not_called()

        asyncio.run(_test())

    def test_no_threads_is_noop(self):
        """When there are no threads with sessions, check is a no-op."""
        monitor = SessionHealthMonitor()

        async def _test():
            with patch.object(monitor, "_discover_threads_with_sessions", return_value=set()):
                monitor._activate_thread = AsyncMock()
                await monitor._check_stalled_threads()

            monitor._activate_thread.assert_not_called()

        asyncio.run(_test())


# ---------------------------------------------------------------------------
# Thread discovery
# ---------------------------------------------------------------------------


class TestThreadDiscovery:

    def test_discovers_from_background_tasks(self):
        """Threads from _background_tasks should be discovered."""
        result = _make_subagent_result(thread_id="thread-mem")
        monitor = SessionHealthMonitor()

        async def _test():
            with (
                _patch_background_tasks({"task-1": result}),
                _patch_lock(),
                patch("deerflow.config.paths.get_paths") as mock_paths,
            ):
                mock_paths.return_value.base_dir = Path("/nonexistent")
                threads = await monitor._discover_threads_with_sessions()

            assert "thread-mem" in threads

        asyncio.run(_test())

    def test_discovers_from_disk(self, tmp_path):
        """Threads with JSONL files on disk should be discovered."""
        threads_dir = tmp_path / "threads" / "thread-disk" / "subagents"
        threads_dir.mkdir(parents=True)
        (threads_dir / "task-1.jsonl").write_text("{}", encoding="utf-8")

        monitor = SessionHealthMonitor()

        async def _test():
            with (
                _patch_background_tasks({}),
                _patch_lock(),
                patch("deerflow.config.paths.get_paths") as mock_paths,
            ):
                mock_paths.return_value.base_dir = tmp_path
                threads = await monitor._discover_threads_with_sessions()

            assert "thread-disk" in threads

        asyncio.run(_test())

    def test_deduplicates(self, tmp_path):
        """Same thread from both sources should only appear once."""
        threads_dir = tmp_path / "threads" / "thread-dup" / "subagents"
        threads_dir.mkdir(parents=True)
        (threads_dir / "task-1.jsonl").write_text("{}", encoding="utf-8")

        result = _make_subagent_result(thread_id="thread-dup")
        monitor = SessionHealthMonitor()

        async def _test():
            with (
                _patch_background_tasks({"task-1": result}),
                _patch_lock(),
                patch("deerflow.config.paths.get_paths") as mock_paths,
            ):
                mock_paths.return_value.base_dir = tmp_path
                threads = await monitor._discover_threads_with_sessions()

            assert threads == {"thread-dup"}

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


# ---------------------------------------------------------------------------
# All-sessions-terminal check
# ---------------------------------------------------------------------------


class TestAllSessionsTerminal:

    def test_returns_true_when_all_terminal(self, tmp_path):
        """Returns True when all sessions have terminal markers."""
        threads_dir = tmp_path / "threads" / "thread-1" / "subagents"
        threads_dir.mkdir(parents=True)
        jsonl1 = threads_dir / "task-1.jsonl"
        jsonl2 = threads_dir / "task-2.jsonl"
        _write_jsonl(jsonl1, [{"role": "ai", "content": "done"}], terminal_status="completed")
        _write_jsonl(jsonl2, [{"role": "ai", "content": "err"}], terminal_status="failed")

        monitor = SessionHealthMonitor()

        async def _test():
            with patch("deerflow.config.paths.get_paths") as mock_paths:
                mock_paths.return_value.base_dir = tmp_path
                result = await monitor._all_sessions_terminal("thread-1")
            assert result is True

        asyncio.run(_test())

    def test_returns_false_when_active_session(self, tmp_path):
        """Returns False when at least one session has no terminal marker."""
        threads_dir = tmp_path / "threads" / "thread-1" / "subagents"
        threads_dir.mkdir(parents=True)
        jsonl1 = threads_dir / "task-1.jsonl"
        jsonl2 = threads_dir / "task-2.jsonl"
        _write_jsonl(jsonl1, [{"role": "ai", "content": "done"}], terminal_status="completed")
        _write_jsonl(jsonl2, [{"role": "ai", "content": "running"}])

        monitor = SessionHealthMonitor()

        async def _test():
            with patch("deerflow.config.paths.get_paths") as mock_paths:
                mock_paths.return_value.base_dir = tmp_path
                result = await monitor._all_sessions_terminal("thread-1")
            assert result is False

        asyncio.run(_test())

    def test_returns_true_when_no_sessions(self, tmp_path):
        """Returns True when thread has no sub-agent sessions."""
        monitor = SessionHealthMonitor()

        async def _test():
            with patch("deerflow.config.paths.get_paths") as mock_paths:
                mock_paths.return_value.base_dir = tmp_path
                result = await monitor._all_sessions_terminal("nonexistent")
            assert result is True

        asyncio.run(_test())
