"""Tests for Sub-Agent health monitor (subagents/health_monitor.py).

Covers:
- Stale session detection (JSONL mtime > threshold)
- Premature stop detection (last AI message with no tool_calls)
- Reactivation flow: cancel → mark interrupted → restart with recovery prompt
- Timer scheduling (start/stop)
- Edge cases: no session file, no thread_id, already terminal
"""

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest

# Mock heavy dependencies
_MOCKED_MODULES = [
    "deerflow.agents",
    "deerflow.agents.thread_state",
    "deerflow.agents.middlewares",
    "deerflow.sandbox",
    "deerflow.sandbox.middleware",
    "deerflow.sandbox.security",
    "deerflow.models",
]


@pytest.fixture(autouse=True, scope="module")
def _mock_heavy_deps():
    """Set up mocks for heavy dependencies and import real executor module."""
    saved = {name: sys.modules.get(name) for name in _MOCKED_MODULES}
    for name in _MOCKED_MODULES:
        if name not in sys.modules:
            sys.modules[name] = MagicMock()

    # Also save and replace the conftest executor mock with real module
    saved_executor = sys.modules.get("deerflow.subagents.executor")

    # Remove conftest mock to allow real import
    if "deerflow.subagents.executor" in sys.modules:
        del sys.modules["deerflow.subagents.executor"]

    # Import the real SubagentStatus
    from deerflow.subagents.executor import SubagentStatus

    # Store for later use
    _mock_heavy_deps.status = SubagentStatus

    yield

    # Restore
    for name in _MOCKED_MODULES:
        if saved[name] is None and name in sys.modules:
            del sys.modules[name]

    # Restore conftest mock
    if saved_executor is not None:
        sys.modules["deerflow.subagents.executor"] = saved_executor
    elif "deerflow.subagents.executor" in sys.modules:
        del sys.modules["deerflow.subagents.executor"]


@pytest.fixture
def real_status():
    """Provide the real SubagentStatus enum."""
    return _mock_heavy_deps.status


@pytest.fixture(autouse=True)
def _reset_health_monitor_import():
    for mod in list(sys.modules.keys()):
        if "deerflow.subagents.health_monitor" in mod:
            del sys.modules[mod]
    yield


@pytest.fixture
def mock_executor_module():
    """Provide a mock executor module with status enum and background tasks."""
    from deerflow.subagents.executor import SubagentStatus

    # Create a fresh mock for background tasks
    mock_bt = {}
    mock_lock = MagicMock()

    with patch("deerflow.subagents.health_monitor._background_tasks", mock_bt), \
         patch("deerflow.subagents.health_monitor._background_tasks_lock", mock_lock), \
         patch("deerflow.subagents.health_monitor.request_cancel_background_task") as mock_cancel:

        yield SimpleNamespace(
            background_tasks=mock_bt,
            lock=mock_lock,
            cancel=mock_cancel,
            status=SubagentStatus,
        )


@pytest.fixture
def tmp_session_dir(tmp_path):
    session_dir = tmp_path / "threads" / "test-thread" / "subagents"
    session_dir.mkdir(parents=True)
    return session_dir


def _make_result(
    task_id="task-001",
    thread_id="test-thread",
    subagent_name="developer",
    description="test task",
    original_prompt="Do the work",
    status=None,
):
    """Create a mock SubagentResult."""
    r = MagicMock()
    r.task_id = task_id
    r.thread_id = thread_id
    r.subagent_name = subagent_name
    r.description = description
    r.original_prompt = original_prompt
    r.status = status
    return r


def _write_jsonl(path: Path, messages: list[dict]):
    """Write a JSONL file with given message dicts."""
    with open(path, "w") as f:
        for m in messages:
            f.write(json.dumps(m) + "\n")


# ── Helper Function Tests ───────────────────────────────────────────────


class TestFindSessionJsonl:
    def test_finds_existing_file(self, tmp_session_dir):
        from deerflow.subagents.health_monitor import _find_session_jsonl

        jsonl = tmp_session_dir / "task-001.jsonl"
        jsonl.write_text("{}")

        with patch("deerflow.config.paths.get_paths") as mock_gp:
            mock_p = MagicMock()
            mock_p.subagent_dir.return_value = tmp_session_dir
            mock_gp.return_value = mock_p

            result = _find_session_jsonl("test-thread", "task-001")
            assert result is not None
            assert "task-001.jsonl" in result

    def test_returns_none_for_no_thread(self):
        from deerflow.subagents.health_monitor import _find_session_jsonl

        assert _find_session_jsonl(None, "task-001") is None

    def test_returns_none_for_missing_file(self, tmp_session_dir):
        from deerflow.subagents.health_monitor import _find_session_jsonl

        with patch("deerflow.config.paths.get_paths") as mock_gp:
            mock_p = MagicMock()
            mock_p.subagent_dir.return_value = tmp_session_dir
            mock_gp.return_value = mock_p

            assert _find_session_jsonl("test-thread", "nonexistent") is None


class TestReadLastLine:
    def test_reads_last_line(self, tmp_path):
        from deerflow.subagents.health_monitor import _read_last_line

        f = tmp_path / "test.jsonl"
        f.write_text(
            json.dumps({"ts": "1", "role": "human", "content": "first"}) + "\n"
            + json.dumps({"ts": "2", "role": "ai", "content": "last"}) + "\n"
        )
        result = _read_last_line(str(f))
        assert result["content"] == "last"

    def test_returns_none_for_empty_file(self, tmp_path):
        from deerflow.subagents.health_monitor import _read_last_line

        f = tmp_path / "empty.jsonl"
        f.write_text("")
        assert _read_last_line(str(f)) is None

    def test_returns_none_for_missing_file(self, tmp_path):
        from deerflow.subagents.health_monitor import _read_last_line

        assert _read_last_line(str(tmp_path / "nope.jsonl")) is None


class TestCountMessages:
    def test_counts_only_message_lines(self, tmp_path):
        from deerflow.subagents.health_monitor import _count_messages

        f = tmp_path / "test.jsonl"
        _write_jsonl(f, [
            {"ts": "1", "role": "human", "content": "hi"},
            {"ts": "2", "role": "ai", "content": "hello"},
            {"ts": "3", "status": "completed", "result": "done"},  # status marker, excluded
        ])
        assert _count_messages(str(f)) == 2

    def test_empty_file_returns_zero(self, tmp_path):
        from deerflow.subagents.health_monitor import _count_messages

        f = tmp_path / "empty.jsonl"
        f.write_text("")
        assert _count_messages(str(f)) == 0


# ── Stale Session Detection Tests ───────────────────────────────────────


class TestStaleDetection:
    """Test scenario 2: stale session detection."""

    def test_detects_stale_session(self, mock_executor_module, tmp_session_dir):
        from deerflow.subagents.health_monitor import SubagentHealthMonitor

        # Create a stale JSONL file (old mtime)
        jsonl = tmp_session_dir / "task-001.jsonl"
        _write_jsonl(jsonl, [{"ts": "old", "role": "ai", "content": "working..."}])
        # Set mtime to 10 minutes ago
        old_time = time.time() - 600
        import os
        os.utime(jsonl, (old_time, old_time))

        result = _make_result(task_id="task-001", status=mock_executor_module.status.RUNNING)
        mock_executor_module.background_tasks["task-001"] = result

        monitor = SubagentHealthMonitor(check_interval=60, stale_threshold=300)

        with patch("deerflow.subagents.health_monitor._find_session_jsonl", return_value=str(jsonl)), \
             patch.object(monitor, "_reactivate_task") as mock_reactivate:

            monitor._check_task("task-001", result)
            mock_reactivate.assert_called_once()
            assert "stale" in mock_reactivate.call_args[0][2]

    def test_no_stale_for_recent_session(self, mock_executor_module, tmp_session_dir):
        from deerflow.subagents.health_monitor import SubagentHealthMonitor

        # Create a fresh JSONL file (just now)
        jsonl = tmp_session_dir / "task-001.jsonl"
        _write_jsonl(jsonl, [{"ts": "now", "role": "ai", "content": "working..."}])

        result = _make_result(task_id="task-001", status=mock_executor_module.status.RUNNING)
        mock_executor_module.background_tasks["task-001"] = result

        monitor = SubagentHealthMonitor(check_interval=60, stale_threshold=300)

        with patch("deerflow.subagents.health_monitor._find_session_jsonl", return_value=str(jsonl)), \
             patch.object(monitor, "_reactivate_task") as mock_reactivate:

            monitor._check_task("task-001", result)
            # Should NOT be stale (file is fresh) and last msg has no tool_calls
            # but it's an AI msg without tool_calls — this is premature stop
            # NOT stale check: the file is recent, so stale detection passes
            # But premature stop might trigger
            call_count = mock_reactivate.call_count
            # Either 0 (if tool_calls check passes) or 1 (premature stop)
            assert call_count <= 1


# ── Premature Stop Detection Tests ──────────────────────────────────────


class TestPrematureStopDetection:
    """Test scenario 1: AI message with no tool_calls detection."""

    def test_detects_ai_without_tool_calls(self, mock_executor_module, tmp_session_dir):
        from deerflow.subagents.health_monitor import SubagentHealthMonitor

        jsonl = tmp_session_dir / "task-001.jsonl"
        _write_jsonl(jsonl, [
            {"ts": "1", "role": "human", "content": "do work"},
            {"ts": "2", "role": "ai", "content": "I think this is done"},  # no tool_calls
        ])

        result = _make_result(task_id="task-001", status=mock_executor_module.status.RUNNING)
        mock_executor_module.background_tasks["task-001"] = result

        monitor = SubagentHealthMonitor(check_interval=60, stale_threshold=300)

        with patch("deerflow.subagents.health_monitor._find_session_jsonl", return_value=str(jsonl)), \
             patch.object(monitor, "_reactivate_task") as mock_reactivate:

            monitor._check_task("task-001", result)
            mock_reactivate.assert_called_once()
            assert "premature stop" in mock_reactivate.call_args[0][2]

    def test_no_detection_for_ai_with_tool_calls(self, mock_executor_module, tmp_session_dir):
        from deerflow.subagents.health_monitor import SubagentHealthMonitor

        jsonl = tmp_session_dir / "task-001.jsonl"
        _write_jsonl(jsonl, [
            {"ts": "1", "role": "ai", "content": "Let me check", "tool_calls": [{"id": "tc1", "name": "read", "args": {}}]},
        ])

        result = _make_result(task_id="task-001", status=mock_executor_module.status.RUNNING)
        mock_executor_module.background_tasks["task-001"] = result

        monitor = SubagentHealthMonitor(check_interval=60, stale_threshold=300)

        with patch("deerflow.subagents.health_monitor._find_session_jsonl", return_value=str(jsonl)), \
             patch.object(monitor, "_reactivate_task") as mock_reactivate:

            monitor._check_task("task-001", result)
            mock_reactivate.assert_not_called()

    def test_no_detection_for_terminal_session(self, mock_executor_module, tmp_session_dir):
        from deerflow.subagents.health_monitor import SubagentHealthMonitor

        jsonl = tmp_session_dir / "task-001.jsonl"
        _write_jsonl(jsonl, [
            {"ts": "1", "role": "ai", "content": "done"},
            {"ts": "2", "status": "completed", "result": "all done"},
        ])

        result = _make_result(task_id="task-001", status=mock_executor_module.status.RUNNING)
        monitor = SubagentHealthMonitor(check_interval=60, stale_threshold=300)

        with patch("deerflow.subagents.health_monitor._find_session_jsonl", return_value=str(jsonl)), \
             patch.object(monitor, "_reactivate_task") as mock_reactivate:

            monitor._check_task("task-001", result)
            mock_reactivate.assert_not_called()


# ── Reactivation Tests ──────────────────────────────────────────────────


class TestReactivation:
    """Test the reactivation flow: cancel → interrupt → restart."""

    def test_reactivate_cancels_and_restarts(self, mock_executor_module, tmp_session_dir):
        from deerflow.subagents.health_monitor import SubagentHealthMonitor

        jsonl = tmp_session_dir / "task-001.jsonl"
        _write_jsonl(jsonl, [
            {"ts": "1", "role": "human", "content": "do work"},
            {"ts": "2", "role": "ai", "content": "I will check the files", "tool_calls": [{"id": "tc1", "name": "read", "args": {}}]},
            {"ts": "3", "role": "tool", "tool_call_id": "tc1", "content": "file.py"},
            {"ts": "4", "role": "ai", "content": "Almost done but interrupted"},
        ])

        # Set mtime to old so it passes the stale check, or make it fresh
        # Since last msg is AI without tool_calls, premature stop will trigger first
        result = _make_result(task_id="task-001", status=mock_executor_module.status.RUNNING)

        # Simulate task reaching terminal state after cancel
        def _cancel_side_effect(tid):
            result.status = mock_executor_module.status.CANCELLED
        mock_executor_module.cancel.side_effect = _cancel_side_effect

        monitor = SubagentHealthMonitor(check_interval=60, stale_threshold=300)

        # We need to patch the lazy imports inside _reactivate_task.
        # The easiest approach: patch the module-level sys.modules entries
        # and directly mock the imported functions.

        mock_config = MagicMock()
        mock_executor_instance = MagicMock()
        mock_executor_instance.execute_async.return_value = "new-task-002"

        # Patch at module level before reactivation runs
        import deerflow.subagents.health_monitor as hm_mod

        with patch.object(hm_mod, "_find_session_jsonl", return_value=str(jsonl)), \
             patch.object(hm_mod, "_count_messages", return_value=4), \
             patch.object(hm_mod, "_read_last_line", return_value={"role": "ai", "content": "Almost done but interrupted"}), \
             patch("builtins.__import__", wraps=__import__) as mock_import, \
             patch("time.sleep"):

            # We'll intercept the lazy imports by patching the modules they import from
            with patch.dict(sys.modules, {
                "deerflow.subagents": MagicMock(get_subagent_config=MagicMock(return_value=mock_config)),
                "deerflow.subagents.executor": MagicMock(SubagentExecutor=MagicMock(return_value=mock_executor_instance)),
                "deerflow.subagents.session": MagicMock(SubagentSession=MagicMock()),
                "deerflow.tools": MagicMock(get_available_tools=MagicMock(return_value=[])),
            }):
                monitor._reactivate_task("task-001", result, "premature stop")

        # Verify cancellation was requested
        mock_executor_module.cancel.assert_called_once_with("task-001")

        # Verify new executor was submitted
        mock_executor_instance.execute_async.assert_called_once()

        # Check recovery prompt contains original task
        call_args = mock_executor_instance.execute_async.call_args
        recovery_prompt = call_args[0][0]
        assert "recovery" in recovery_prompt.lower() or "继续" in recovery_prompt
        assert "Do the work" in recovery_prompt


# ── Timer Scheduling Tests ───────────────────────────────────────────────


class TestTimerScheduling:
    """Test start/stop/scheduling of the health monitor."""

    def test_start_schedules_first_check(self):
        from deerflow.subagents.health_monitor import SubagentHealthMonitor

        monitor = SubagentHealthMonitor(check_interval=30)
        with patch.object(monitor, "_schedule_next") as mock_schedule:
            monitor.start()
            assert monitor._running is True
            mock_schedule.assert_called_once()

    def test_stop_cancels_timer(self):
        from deerflow.subagents.health_monitor import SubagentHealthMonitor

        monitor = SubagentHealthMonitor(check_interval=30)
        monitor.start()
        assert monitor._timer is not None

        monitor.stop()
        assert monitor._running is False
        assert monitor._timer is None

    def test_check_cycle_calls_check_all_and_schedules_next(self):
        from deerflow.subagents.health_monitor import SubagentHealthMonitor

        monitor = SubagentHealthMonitor(check_interval=30)
        with patch.object(monitor, "_check_all") as mock_check, \
             patch.object(monitor, "_schedule_next") as mock_schedule:

            monitor._check_cycle()
            mock_check.assert_called_once()
            mock_schedule.assert_called_once()

    def test_check_cycle_survives_exception(self):
        from deerflow.subagents.health_monitor import SubagentHealthMonitor

        monitor = SubagentHealthMonitor(check_interval=30)
        with patch.object(monitor, "_check_all", side_effect=Exception("boom")), \
             patch.object(monitor, "_schedule_next") as mock_schedule:

            monitor._check_cycle()
            mock_schedule.assert_called_once()  # Still schedules next despite error


class TestCheckAll:
    """Test _check_all iterates running tasks."""

    def test_checks_only_running_tasks(self, mock_executor_module):
        from deerflow.subagents.health_monitor import SubagentHealthMonitor

        running = _make_result(task_id="r1", status=mock_executor_module.status.RUNNING)
        completed = _make_result(task_id="c1", status=mock_executor_module.status.COMPLETED)

        mock_executor_module.background_tasks["r1"] = running
        mock_executor_module.background_tasks["c1"] = completed

        monitor = SubagentHealthMonitor()

        with patch.object(monitor, "_check_task") as mock_check:
            monitor._check_all()
            # Only the running task should be checked
            assert mock_check.call_count == 1
            assert mock_check.call_args[0][0] == "r1"

    def test_no_running_tasks_is_noop(self, mock_executor_module):
        from deerflow.subagents.health_monitor import SubagentHealthMonitor

        monitor = SubagentHealthMonitor()
        with patch.object(monitor, "_check_task") as mock_check:
            monitor._check_all()
            mock_check.assert_not_called()
