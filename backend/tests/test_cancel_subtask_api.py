"""Tests for the cancel-subtask API endpoint and helpers in runs.py.

Covers:
- POST /api/runs/subtasks/{task_id}/cancel — three-tier cancel strategy
- _find_thread_id_for_task — scan session files to find thread
- _cancel_subtask_on_disk — fallback disk-based cancellation
"""

import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import the runs router module
runs_module = importlib.import_module("app.gateway.routers.runs")


# ── _find_thread_id_for_task Tests ──────────────────────────────────────


class TestFindThreadIdForTask:
    """Test _find_thread_id_for_task helper in runs.py."""

    def test_finds_task_by_summary_file(self, tmp_path, monkeypatch):
        threads_dir = tmp_path / "threads"
        subagents = threads_dir / "thread-xyz" / "subagents"
        subagents.mkdir(parents=True)
        (subagents / "tc-abc.summary.json").write_text('{"status":"running"}')

        mock_paths = MagicMock()
        mock_paths.base_dir = tmp_path
        monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: mock_paths)

        result = runs_module._find_thread_id_for_task("tc-abc")
        assert result == "thread-xyz"

    def test_returns_none_for_missing_task(self, tmp_path, monkeypatch):
        threads_dir = tmp_path / "threads"
        threads_dir.mkdir()

        mock_paths = MagicMock()
        mock_paths.base_dir = tmp_path
        monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: mock_paths)

        result = runs_module._find_thread_id_for_task("tc-missing")
        assert result is None

    def test_returns_none_when_no_threads_dir(self, tmp_path, monkeypatch):
        mock_paths = MagicMock()
        mock_paths.base_dir = tmp_path
        monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: mock_paths)

        result = runs_module._find_thread_id_for_task("tc-any")
        assert result is None

    def test_finds_first_match_with_multiple_threads(self, tmp_path, monkeypatch):
        threads_dir = tmp_path / "threads"

        for tid in ["t1", "t2"]:
            sub = threads_dir / tid / "subagents"
            sub.mkdir(parents=True)
            (sub / "tc-target.summary.json").write_text('{"status":"running"}')

        mock_paths = MagicMock()
        mock_paths.base_dir = tmp_path
        monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: mock_paths)

        result = runs_module._find_thread_id_for_task("tc-target")
        assert result in ("t1", "t2")


# ── _cancel_subtask_on_disk Tests ───────────────────────────────────────


class TestCancelSubtaskOnDisk:
    """Test _cancel_subtask_on_disk fallback."""

    @pytest.mark.anyio
    async def test_marks_running_task_as_cancelled(self, tmp_path, monkeypatch):
        threads_dir = tmp_path / "threads"
        subagents = threads_dir / "thread-1" / "subagents"
        subagents.mkdir(parents=True)
        summary = subagents / "tc-disk.summary.json"
        summary.write_text(json.dumps({"status": "running"}))

        mock_paths = MagicMock()
        mock_paths.base_dir = tmp_path
        monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: mock_paths)

        result = await runs_module._cancel_subtask_on_disk("tc-disk")
        assert result.cancelled is True
        assert result.task_id == "tc-disk"

        # Verify on-disk status
        updated = json.loads(summary.read_text())
        assert updated["status"] == "cancelled"

    @pytest.mark.anyio
    async def test_marks_interrupted_task_as_cancelled(self, tmp_path, monkeypatch):
        threads_dir = tmp_path / "threads"
        subagents = threads_dir / "thread-1" / "subagents"
        subagents.mkdir(parents=True)
        summary = subagents / "tc-intr.summary.json"
        summary.write_text(json.dumps({"status": "interrupted"}))

        mock_paths = MagicMock()
        mock_paths.base_dir = tmp_path
        monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: mock_paths)

        result = await runs_module._cancel_subtask_on_disk("tc-intr")
        assert result.cancelled is True

    @pytest.mark.anyio
    async def test_skips_already_completed_task(self, tmp_path, monkeypatch):
        threads_dir = tmp_path / "threads"
        subagents = threads_dir / "thread-1" / "subagents"
        subagents.mkdir(parents=True)
        summary = subagents / "tc-done.summary.json"
        summary.write_text(json.dumps({"status": "completed"}))

        mock_paths = MagicMock()
        mock_paths.base_dir = tmp_path
        monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: mock_paths)

        result = await runs_module._cancel_subtask_on_disk("tc-done")
        assert result.cancelled is False
        assert "Task not found" in result.error

    @pytest.mark.anyio
    async def test_returns_error_for_missing_task(self, tmp_path, monkeypatch):
        mock_paths = MagicMock()
        mock_paths.base_dir = tmp_path
        monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: mock_paths)

        result = await runs_module._cancel_subtask_on_disk("tc-gone")
        assert result.cancelled is False
        assert "Task not found" in result.error


# ── cancel_subtask Endpoint Tests ───────────────────────────────────────


class TestCancelSubtaskEndpoint:
    """Test cancel_subtask endpoint three-tier strategy."""

    @pytest.mark.anyio
    async def test_path1_same_process_cancel(self, monkeypatch):
        """Task found in _background_tasks → direct cancel."""
        mock_result = SimpleNamespace(status=SimpleNamespace(value="running"))
        cancel_called = []

        # The function lazy-imports from deerflow.subagents.executor
        import deerflow.subagents.executor as executor_mod

        monkeypatch.setattr(executor_mod, "get_background_task_result", lambda _: mock_result)
        monkeypatch.setattr(executor_mod, "request_cancel_background_task", lambda tid: cancel_called.append(tid))

        result = await runs_module.cancel_subtask("tc-same", request=MagicMock())
        assert result.cancelled is True
        assert result.task_id == "tc-same"
        assert cancel_called == ["tc-same"]

    @pytest.mark.anyio
    async def test_path1_task_not_running(self, monkeypatch):
        """Task found but already completed → returns error."""
        mock_result = SimpleNamespace(status=SimpleNamespace(value="completed"))

        import deerflow.subagents.executor as executor_mod

        monkeypatch.setattr(executor_mod, "get_background_task_result", lambda _: mock_result)

        result = await runs_module.cancel_subtask("tc-done", request=MagicMock())
        assert result.cancelled is False
        assert "completed" in result.error

    @pytest.mark.anyio
    async def test_path2_file_marker_cancel(self, tmp_path, monkeypatch):
        """Task not in memory, found on disk → file marker cancellation."""
        import deerflow.subagents.executor as executor_mod

        monkeypatch.setattr(executor_mod, "get_background_task_result", lambda _: None)

        # Set up disk structure for _find_thread_id_for_task
        threads_dir = tmp_path / "threads"
        subagents = threads_dir / "thread-1" / "subagents"
        subagents.mkdir(parents=True)
        (subagents / "tc-marker.summary.json").write_text('{"status":"running"}')

        mock_paths = MagicMock()
        mock_paths.base_dir = tmp_path
        monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: mock_paths)

        # Mock SubagentSession (lazy-imported from deerflow.subagents.session)
        marker_created = []

        class FakeSession:
            def __init__(self, thread_id, task_id, subagent_name, description=""):
                self.thread_id = thread_id
                self.task_id = task_id

            def request_cancel(self):
                marker_created.append(self.task_id)

        import deerflow.subagents.session as session_mod

        monkeypatch.setattr(session_mod, "SubagentSession", FakeSession)

        result = await runs_module.cancel_subtask("tc-marker", request=MagicMock())
        assert result.cancelled is True
        assert "tc-marker" in marker_created

    @pytest.mark.anyio
    async def test_path3_disk_fallback(self, tmp_path, monkeypatch):
        """Task not in memory and not found on disk → fallback disk cancellation."""
        import deerflow.subagents.executor as executor_mod

        monkeypatch.setattr(executor_mod, "get_background_task_result", lambda _: None)
        monkeypatch.setattr(
            runs_module,
            "_find_thread_id_for_task",
            lambda _: None,
        )

        # _cancel_subtask_on_disk will also not find it
        result = await runs_module.cancel_subtask("tc-ghost", request=MagicMock())
        assert result.cancelled is False
        assert "Task not found" in result.error
