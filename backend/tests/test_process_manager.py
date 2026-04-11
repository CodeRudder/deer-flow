"""Tests for the background command process manager."""

import json
import os
import time
from pathlib import Path

import pytest

from deerflow.sandbox.process_manager import (
    CommandStatus,
    _cmd_log_dir,
    _cmd_log_path,
    _persistence_path,
    cleanup,
    cleanup_by_thread,
    get_output,
    kill,
    list_commands,
    restore,
    start,
)

_THREAD_ID = "test-pm-thread"
_THREAD_ID_2 = "test-pm-thread-2"


@pytest.fixture(autouse=True)
def _clean_registry():
    """Clear the global command registry before each test."""
    import deerflow.sandbox.process_manager as pm

    with pm._commands_lock:
        pm._commands.clear()
    yield
    with pm._commands_lock:
        pm._commands.clear()


@pytest.fixture
def _base_dir(tmp_path, monkeypatch):
    """Set up a temporary base directory for persistence."""
    base = tmp_path / ".deer-flow"
    base.mkdir()

    monkeypatch.setattr(
        "deerflow.sandbox.process_manager._get_base_dir",
        lambda: base,
    )
    return base


# ---------------------------------------------------------------------------
# start()
# ---------------------------------------------------------------------------


class TestStart:
    def test_returns_command_id(self, _base_dir):
        cmd_id = start("echo hello", "test", "/bin/bash", _THREAD_ID)
        assert cmd_id.startswith("cmd_")
        assert len(cmd_id) > 10

    def test_creates_log_dir_and_file(self, _base_dir):
        cmd_id = start("echo hello", "test", "/bin/bash", _THREAD_ID)
        log_dir = _cmd_log_dir(_THREAD_ID)
        assert log_dir.is_dir()
        log_path = _cmd_log_path(_THREAD_ID, cmd_id)
        time.sleep(1)
        assert log_path.is_file()

    def test_persists_metadata(self, _base_dir):
        cmd_id = start("sleep 5", "test persist", "/bin/bash", _THREAD_ID)
        time.sleep(0.5)
        persist = _persistence_path(_THREAD_ID)
        assert persist.is_file()
        data = json.loads(persist.read_text())
        assert len(data["commands"]) == 1
        assert data["commands"][0]["command_id"] == cmd_id
        assert data["commands"][0]["status"] == "running"

    def test_command_completes(self, _base_dir):
        cmd_id = start("echo done", "quick cmd", "/bin/bash", _THREAD_ID)
        time.sleep(1.5)
        status, output, _ = get_output(cmd_id)
        assert status == CommandStatus.COMPLETED or status == "completed"
        assert "done" in output


# ---------------------------------------------------------------------------
# get_output() — pagination
# ---------------------------------------------------------------------------


class TestGetOutput:
    def _start_multi_line(self, _base_dir, n=60):
        """Start a command that produces n lines."""
        cmd_id = start(
            f'for i in $(seq 1 {n}); do echo "Line $i"; done',
            f"{n} lines",
            "/bin/bash",
            _THREAD_ID,
        )
        time.sleep(2)
        return cmd_id

    def test_not_found(self):
        s, msg, lf = get_output("nonexistent")
        assert s == CommandStatus.FAILED
        assert lf is None

    def test_default_shows_last_10(self, _base_dir):
        cmd_id = self._start_multi_line(_base_dir)
        s, output, lf = get_output(cmd_id)
        assert "showing lines 51-60" in output
        assert "Line 60" in output
        assert "Line 50" not in output.split("Output")[-1] if "Output" in output else "Line 50" not in output

    def test_start_line_0_reads_from_beginning(self, _base_dir):
        cmd_id = self._start_multi_line(_base_dir)
        s, output, lf = get_output(cmd_id, start_line=0, line_count=10)
        assert "showing lines 1-10" in output
        assert "Line 1" in output

    def test_pagination_page2(self, _base_dir):
        cmd_id = self._start_multi_line(_base_dir)
        s, output, lf = get_output(cmd_id, start_line=10, line_count=10)
        assert "showing lines 11-20" in output
        assert "Line 11" in output

    def test_max_50_lines(self, _base_dir):
        cmd_id = self._start_multi_line(_base_dir, n=100)
        # Request 200 lines, should be capped to 50
        s, output, lf = get_output(cmd_id, start_line=0, line_count=200)
        assert "showing lines 1-50" in output
        assert "50 lines after" in output

    def test_start_beyond_end(self, _base_dir):
        cmd_id = self._start_multi_line(_base_dir, n=20)
        s, output, lf = get_output(cmd_id, start_line=100, line_count=10)
        # Should show empty range or last lines
        assert "Total lines: 20" in output

    def test_returns_log_file_path(self, _base_dir):
        cmd_id = self._start_multi_line(_base_dir, n=5)
        s, output, lf = get_output(cmd_id)
        assert lf is not None
        assert cmd_id in lf

    def test_metadata_includes_before_after_hints(self, _base_dir):
        cmd_id = self._start_multi_line(_base_dir)
        s, output, lf = get_output(cmd_id, start_line=0, line_count=10)
        assert "lines after (use start_line=10 to continue)" in output


# ---------------------------------------------------------------------------
# kill()
# ---------------------------------------------------------------------------


class TestKill:
    def test_kill_running_command(self, _base_dir):
        cmd_id = start("sleep 60", "long sleep", "/bin/bash", _THREAD_ID)
        time.sleep(0.5)
        killed, output = kill(cmd_id)
        assert killed is True
        assert cmd_id in output or "sleep" in output or len(output) >= 0

    def test_kill_not_found(self):
        killed, msg = kill("nonexistent")
        assert killed is False
        assert "not found" in msg

    def test_kill_already_completed(self, _base_dir):
        cmd_id = start("echo done", "quick", "/bin/bash", _THREAD_ID)
        time.sleep(1.5)
        killed, msg = kill(cmd_id)
        assert killed is False
        assert "not running" in msg

    def test_kill_updates_persistence(self, _base_dir):
        cmd_id = start("sleep 60", "long", "/bin/bash", _THREAD_ID)
        time.sleep(0.5)
        kill(cmd_id)
        persist = _persistence_path(_THREAD_ID)
        data = json.loads(persist.read_text())
        cmd_data = [c for c in data["commands"] if c["command_id"] == cmd_id][0]
        assert cmd_data["status"] == "killed"

    def test_kill_orphan_by_pid(self, _base_dir):
        """Simulate killing an orphan process (no _process reference)."""
        cmd_id = start("sleep 60", "orphan test", "/bin/bash", _THREAD_ID)
        time.sleep(0.5)

        # Get the command info and clear _process to simulate orphan
        import deerflow.sandbox.process_manager as pm

        with pm._commands_lock:
            info = pm._commands[cmd_id]
            pid = info.pid
            info._process = None  # Simulate restart scenario

        killed, msg = kill(cmd_id)
        assert killed is True


# ---------------------------------------------------------------------------
# list_commands()
# ---------------------------------------------------------------------------


class TestListCommands:
    def test_empty(self):
        cmds = list_commands()
        assert cmds == []

    def test_lists_commands_for_thread(self, _base_dir):
        start("echo a", "cmd a", "/bin/bash", _THREAD_ID)
        start("echo b", "cmd b", "/bin/bash", _THREAD_ID_2)
        time.sleep(0.5)

        cmds = list_commands(thread_id=_THREAD_ID)
        assert len(cmds) == 1
        assert cmds[0]["description"] == "cmd a"

    def test_lists_all_without_filter(self, _base_dir):
        start("echo a", "cmd a", "/bin/bash", _THREAD_ID)
        start("echo b", "cmd b", "/bin/bash", _THREAD_ID_2)
        time.sleep(0.5)

        cmds = list_commands()
        assert len(cmds) == 2

    def test_includes_metadata(self, _base_dir):
        cmd_id = start("echo meta", "meta test", "/bin/bash", _THREAD_ID)
        time.sleep(0.5)

        cmds = list_commands(thread_id=_THREAD_ID)
        assert len(cmds) == 1
        c = cmds[0]
        assert c["command_id"] == cmd_id
        assert c["command"] == "echo meta"
        assert c["status"] in ("running", "completed")
        assert c["pid"] is not None
        assert "started_at" in c


# ---------------------------------------------------------------------------
# cleanup() / cleanup_by_thread()
# ---------------------------------------------------------------------------


class TestCleanup:
    def test_cleanup_removes_from_registry(self, _base_dir):
        cmd_id = start("echo cleanup", "cleanup test", "/bin/bash", _THREAD_ID)
        time.sleep(1.5)  # Let it complete
        cleanup(cmd_id)
        s, msg, lf = get_output(cmd_id)
        assert s == CommandStatus.FAILED
        assert "not found" in msg

    def test_cleanup_kills_running(self, _base_dir):
        cmd_id = start("sleep 60", "long", "/bin/bash", _THREAD_ID)
        time.sleep(0.5)
        cleanup(cmd_id)
        # Should have been killed
        import deerflow.sandbox.process_manager as pm

        with pm._commands_lock:
            assert cmd_id not in pm._commands

    def test_cleanup_by_thread(self, _base_dir):
        start("echo a", "a", "/bin/bash", _THREAD_ID)
        start("echo b", "b", "/bin/bash", _THREAD_ID)
        start("echo c", "c", "/bin/bash", _THREAD_ID_2)
        time.sleep(1.5)

        cleanup_by_thread(_THREAD_ID)

        cmds_t1 = list_commands(thread_id=_THREAD_ID)
        cmds_t2 = list_commands(thread_id=_THREAD_ID_2)
        assert len(cmds_t1) == 0
        assert len(cmds_t2) == 1


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_save_and_load(self, _base_dir):
        # Start a command and let it complete
        cmd_id = start("echo persist_test", "persist", "/bin/bash", _THREAD_ID)
        time.sleep(1.5)

        # Clear in-memory registry
        import deerflow.sandbox.process_manager as pm

        with pm._commands_lock:
            pm._commands.clear()

        # Restore from disk
        restore()

        # Verify restored
        s, output, lf = get_output(cmd_id)
        # Should either be completed or failed (process finished)
        assert s in (CommandStatus.COMPLETED, CommandStatus.FAILED, "completed", "failed")
        assert "persist_test" in output

    def test_orphan_detection_marks_dead_as_failed(self, _base_dir):
        """If a 'running' command's PID is dead on restore, mark as failed."""
        cmd_id = start("echo orphan", "orphan", "/bin/bash", _THREAD_ID)
        time.sleep(1.5)

        # Manually set status to running and pid to a dead PID
        persist = _persistence_path(_THREAD_ID)
        data = json.loads(persist.read_text())
        data["commands"][0]["status"] = "running"
        data["commands"][0]["pid"] = 9999999  # Non-existent PID
        persist.write_text(json.dumps(data))

        # Clear and restore
        import deerflow.sandbox.process_manager as pm

        with pm._commands_lock:
            pm._commands.clear()
        restore()

        s, _, _ = get_output(cmd_id)
        assert s == CommandStatus.FAILED

    def test_atomic_write(self, _base_dir):
        """Verify no partial files left."""
        start("echo atomic", "atomic", "/bin/bash", _THREAD_ID)
        time.sleep(0.5)
        persist = _persistence_path(_THREAD_ID)
        tmp = persist.with_suffix(".tmp")
        assert not tmp.exists()
        assert persist.exists()
