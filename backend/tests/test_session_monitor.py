"""Tests for SessionMonitor — thread activation and status detection."""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.gateway.session_monitor import SessionMonitor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_subagent_result(
    task_id="task-1",
    thread_id="thread-1",
    status="running",
    subagent_name="general-purpose",
    description="test task",
):
    """Create a mock SubagentResult."""
    result = MagicMock()
    result.task_id = task_id
    result.thread_id = thread_id
    result.status = SimpleNamespace(value=status)
    result.subagent_name = subagent_name
    result.description = description
    return result


def _patch_background_tasks(tasks: dict):
    return patch("deerflow.subagents.executor._background_tasks", tasks)


def _patch_lock():
    import threading
    return patch("deerflow.subagents.executor._background_tasks_lock", threading.Lock())


def _write_jsonl(path: Path, messages: list[dict], terminal_status: str | None = None):
    with open(path, "w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        if terminal_status:
            f.write(json.dumps({"status": terminal_status}, ensure_ascii=False) + "\n")


def _patch_langgraph_store(thread_ids: list[str] | None = None):
    """Patch _get_client to return a mock that searches threads."""
    mock_client = AsyncMock()
    if thread_ids is not None:
        mock_client.threads.search.return_value = [{"thread_id": tid} for tid in thread_ids]
    else:
        mock_client.threads.search.return_value = []
    return patch.object(SessionMonitor, "_get_client", return_value=mock_client)


def _patch_get_client_none():
    """Patch _get_client to return None (no LangGraph client)."""
    return patch.object(SessionMonitor, "_get_client", return_value=None)


# ---------------------------------------------------------------------------
# Thread discovery
# ---------------------------------------------------------------------------


class TestThreadDiscovery:
    def test_discovers_from_background_tasks(self):
        monitor = SessionMonitor()
        result = _make_subagent_result(thread_id="t1")
        with (
            _patch_background_tasks({"task-1": result}),
            _patch_lock(),
            _patch_langgraph_store(),
        ):
            threads = asyncio.run(monitor._discover_threads_with_sessions())
        assert "t1" in threads

    def test_discovers_from_disk(self, tmp_path):
        subagents_dir = tmp_path / "threads" / "thread-disk" / "subagents"
        subagents_dir.mkdir(parents=True)
        (subagents_dir / "task-1.jsonl").write_text("{}", encoding="utf-8")

        monitor = SessionMonitor()
        with (
            _patch_background_tasks({}),
            _patch_lock(),
            patch("deerflow.config.paths.get_paths") as mock_paths,
            _patch_get_client_none(),
        ):
            mock_paths.return_value.base_dir = tmp_path
            threads = asyncio.run(monitor._discover_threads_with_sessions())
        assert "thread-disk" in threads

    def test_discovers_from_langgraph_store(self):
        monitor = SessionMonitor()
        with (
            _patch_background_tasks({}),
            _patch_lock(),
            patch("deerflow.config.paths.get_paths") as mock_paths,
            _patch_langgraph_store(thread_ids=["store-t1", "store-t2"]),
        ):
            mock_paths.return_value.base_dir = Path("/nonexistent")
            threads = asyncio.run(monitor._discover_threads_with_sessions())
        assert "store-t1" in threads
        assert "store-t2" in threads

    def test_deduplicates(self, tmp_path):
        subagents_dir = tmp_path / "threads" / "thread-dup" / "subagents"
        subagents_dir.mkdir(parents=True)
        (subagents_dir / "task-1.jsonl").write_text("{}", encoding="utf-8")

        result = _make_subagent_result(thread_id="thread-dup")
        monitor = SessionMonitor()
        with (
            _patch_background_tasks({"task-1": result}),
            _patch_lock(),
            patch("deerflow.config.paths.get_paths") as mock_paths,
            _patch_get_client_none(),
        ):
            mock_paths.return_value.base_dir = tmp_path
            threads = asyncio.run(monitor._discover_threads_with_sessions())
        assert "thread-dup" in threads


# ---------------------------------------------------------------------------
# Running subtask detection
# ---------------------------------------------------------------------------


class TestHasRunningSubtask:
    def test_running_in_memory(self):
        result = _make_subagent_result(status="running")
        monitor = SessionMonitor()
        with (
            _patch_background_tasks({"task-1": result}),
            _patch_lock(),
        ):
            assert asyncio.run(monitor._has_running_subtask("thread-1")) is True

    def test_completed_in_memory(self):
        result = _make_subagent_result(status="completed")
        monitor = SessionMonitor()
        with (
            _patch_background_tasks({"task-1": result}),
            _patch_lock(),
        ):
            assert asyncio.run(monitor._has_running_subtask("thread-1")) is False

    def test_running_on_disk_fresh(self, tmp_path):
        subagents_dir = tmp_path / "threads" / "thread-1" / "subagents"
        subagents_dir.mkdir(parents=True)
        jsonl = subagents_dir / "task-1.jsonl"
        _write_jsonl(jsonl, [{"role": "ai", "content": "working"}])

        monitor = SessionMonitor()
        with (
            _patch_background_tasks({}),
            _patch_lock(),
            patch("deerflow.config.paths.get_paths") as mock_paths,
            _patch_get_client_none(),
        ):
            mock_paths.return_value.base_dir = tmp_path
            assert asyncio.run(monitor._has_running_subtask("thread-1")) is True

    def test_running_on_disk_stale(self, tmp_path):
        subagents_dir = tmp_path / "threads" / "thread-1" / "subagents"
        subagents_dir.mkdir(parents=True)
        jsonl = subagents_dir / "task-1.jsonl"
        _write_jsonl(jsonl, [{"role": "ai", "content": "working"}])
        old_time = time.time() - 1000
        os.utime(jsonl, (old_time, old_time))

        monitor = SessionMonitor()
        with (
            _patch_background_tasks({}),
            _patch_lock(),
            patch("deerflow.config.paths.get_paths") as mock_paths,
            _patch_get_client_none(),
        ):
            mock_paths.return_value.base_dir = tmp_path
            assert asyncio.run(monitor._has_running_subtask("thread-1")) is False

    def test_completed_on_disk(self, tmp_path):
        import json as _json

        subagents_dir = tmp_path / "threads" / "thread-1" / "subagents"
        subagents_dir.mkdir(parents=True)
        jsonl = subagents_dir / "task-1.jsonl"
        _write_jsonl(jsonl, [{"role": "ai", "content": "done"}], terminal_status="completed")
        # Write summary with terminal status so _has_running_subtask skips it
        summary = subagents_dir / "task-1.summary.json"
        summary.write_text(_json.dumps({"status": "completed"}))

        monitor = SessionMonitor()
        with (
            _patch_background_tasks({}),
            _patch_lock(),
            patch("deerflow.config.paths.get_paths") as mock_paths,
            _patch_get_client_none(),
        ):
            mock_paths.return_value.base_dir = tmp_path
            assert asyncio.run(monitor._has_running_subtask("thread-1")) is False


# ---------------------------------------------------------------------------
# Thread activation check
# ---------------------------------------------------------------------------


class TestCheckAndActivateThread:
    def test_skips_when_subtask_running(self):
        monitor = SessionMonitor()
        with (
            patch.object(monitor, "_has_running_subtask", new_callable=AsyncMock, return_value=True),
            patch.object(monitor, "_has_active_run", new_callable=AsyncMock),
            patch.object(monitor, "_activate_thread", new_callable=AsyncMock) as mock_activate,
        ):
            asyncio.run(monitor._check_and_activate_thread("t1"))
        mock_activate.assert_not_called()

    def test_skips_when_main_run_active(self):
        monitor = SessionMonitor()
        with (
            patch.object(monitor, "_has_running_subtask", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_has_active_run", new_callable=AsyncMock, return_value=True),
            patch.object(monitor, "_activate_thread", new_callable=AsyncMock) as mock_activate,
        ):
            asyncio.run(monitor._check_and_activate_thread("t1"))
        mock_activate.assert_not_called()

    def test_skips_when_user_interrupted(self):
        monitor = SessionMonitor()
        with (
            patch.object(monitor, "_has_running_subtask", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_has_active_run", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_is_user_interrupted", new_callable=AsyncMock, return_value=True),
            patch.object(monitor, "_activate_thread", new_callable=AsyncMock) as mock_activate,
        ):
            asyncio.run(monitor._check_and_activate_thread("t1"))
        mock_activate.assert_not_called()

    def test_skips_when_no_unfinished_todos(self):
        monitor = SessionMonitor()
        with (
            patch.object(monitor, "_has_running_subtask", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_has_active_run", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_is_user_interrupted", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_has_unfinished_todos", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_activate_thread", new_callable=AsyncMock) as mock_activate,
        ):
            asyncio.run(monitor._check_and_activate_thread("t1"))
        mock_activate.assert_not_called()

    def test_activates_when_stalled_with_todos(self):
        monitor = SessionMonitor()
        with (
            patch.object(monitor, "_has_running_subtask", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_has_active_run", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_is_user_interrupted", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_has_unfinished_todos", new_callable=AsyncMock, return_value=True),
            patch.object(monitor, "_activate_thread", new_callable=AsyncMock, return_value=True) as mock_activate,
        ):
            asyncio.run(monitor._check_and_activate_thread("t1"))
        mock_activate.assert_called_once_with("t1", message=monitor.DEFAULT_ACTIVATION_MESSAGE)
        assert monitor._activation_counts["t1"] == 1

    def test_does_not_count_failed_activation(self):
        monitor = SessionMonitor()
        with (
            patch.object(monitor, "_has_running_subtask", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_has_active_run", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_is_user_interrupted", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_has_unfinished_todos", new_callable=AsyncMock, return_value=True),
            patch.object(monitor, "_activate_thread", new_callable=AsyncMock, return_value=False),
        ):
            asyncio.run(monitor._check_and_activate_thread("t1"))
        # Count should not be incremented on failure
        assert monitor._activation_counts.get("t1", 0) == 0

    def test_stops_after_max_activations(self):
        monitor = SessionMonitor()
        mocks = dict(
            _has_running_subtask=AsyncMock(return_value=False),
            _has_active_run=AsyncMock(return_value=False),
            _is_user_interrupted=AsyncMock(return_value=False),
            _has_unfinished_todos=AsyncMock(return_value=True),
            _activate_thread=AsyncMock(return_value=True),
        )
        # Activate 5 times
        with patch.multiple(monitor, **mocks):
            for _ in range(5):
                asyncio.run(monitor._check_and_activate_thread("t1"))
        assert monitor._activation_counts["t1"] == 5

        # 6th call should be skipped
        mocks["_activate_thread"].reset_mock()
        with patch.multiple(monitor, **mocks):
            asyncio.run(monitor._check_and_activate_thread("t1"))
        mocks["_activate_thread"].assert_not_called()

    def test_resets_count_when_active_run_found(self):
        monitor = SessionMonitor()
        monitor._activation_counts["t1"] = 3
        with (
            patch.object(monitor, "_has_running_subtask", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_has_active_run", new_callable=AsyncMock, return_value=True),
            patch.object(monitor, "_activate_thread", new_callable=AsyncMock),
        ):
            asyncio.run(monitor._check_and_activate_thread("t1"))
        assert "t1" not in monitor._activation_counts

    def test_resets_count_when_subtask_running(self):
        monitor = SessionMonitor()
        monitor._activation_counts["t1"] = 4
        with (
            patch.object(monitor, "_has_running_subtask", new_callable=AsyncMock, return_value=True),
            patch.object(monitor, "_has_active_run", new_callable=AsyncMock),
            patch.object(monitor, "_activate_thread", new_callable=AsyncMock),
        ):
            asyncio.run(monitor._check_and_activate_thread("t1"))
        assert "t1" not in monitor._activation_counts


# ---------------------------------------------------------------------------
# Activation via runs/stream
# ---------------------------------------------------------------------------


class TestActivateThread:
    def test_posts_to_runs_stream(self):
        mock_client = AsyncMock()
        mock_client.threads.get_state.return_value = {
            "config": {"configurable": {"checkpoint_id": "cp-123", "checkpoint_ns": ""}},
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        monitor = SessionMonitor()
        with (
            patch.object(monitor, "_get_client", return_value=mock_client),
            patch("httpx.AsyncClient") as mock_http_client_cls,
        ):
            mock_http = AsyncMock()
            mock_http.post.return_value = mock_resp
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http_client_cls.return_value = mock_http

            asyncio.run(monitor._activate_thread("t1"))

        # Verify the POST was made with correct payload
        mock_http.post.assert_called_once()
        call_args = mock_http.post.call_args
        payload = call_args[1].get("json") or call_args[0][1] if len(call_args[0]) > 1 else call_args[1]["json"]
        assert payload["assistant_id"] == "lead_agent"
        assert payload["multitask_strategy"] == "interrupt"
        assert payload["on_disconnect"] == "cancel"
        # Verify message content
        msg = payload["input"]["messages"][0]
        assert msg["type"] == "human"

    def test_posts_without_checkpoint_when_get_state_fails(self):
        mock_client = AsyncMock()
        mock_client.threads.get_state.side_effect = Exception("no state")

        monitor = SessionMonitor()
        with (
            patch.object(monitor, "_get_client", return_value=mock_client),
            patch("httpx.AsyncClient") as mock_http_client_cls,
        ):
            mock_http = AsyncMock()
            mock_http.post.return_value = MagicMock(status_code=200)
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http_client_cls.return_value = mock_http

            asyncio.run(monitor._activate_thread("t1"))

        payload = mock_http.post.call_args[1]["json"]
        assert "checkpoint" not in payload
        assert payload["multitask_strategy"] == "interrupt"

    def test_logs_error_when_no_client(self):
        monitor = SessionMonitor()
        with patch.object(monitor, "_get_client", return_value=None):
            asyncio.run(monitor._activate_thread("t1"))  # Should not raise

    def test_handles_http_error(self):
        mock_client = AsyncMock()
        mock_client.threads.get_state.return_value = {"config": {"configurable": {}}}

        monitor = SessionMonitor()
        with (
            patch.object(monitor, "_get_client", return_value=mock_client),
            patch("httpx.AsyncClient") as mock_http_client_cls,
        ):
            mock_http = AsyncMock()
            mock_http.post.side_effect = Exception("connection error")
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http_client_cls.return_value = mock_http

            asyncio.run(monitor._activate_thread("t1"))  # Should not raise


# ---------------------------------------------------------------------------
# _check_all integration
# ---------------------------------------------------------------------------


class TestCheckAll:
    def test_no_threads_found(self):
        monitor = SessionMonitor()
        with (
            patch.object(monitor, "_discover_threads_with_sessions", new_callable=AsyncMock, return_value=set()),
        ):
            asyncio.run(monitor._check_all())  # Should not raise

    def test_checks_each_thread(self):
        monitor = SessionMonitor()
        with (
            patch.object(monitor, "_discover_threads_with_sessions", new_callable=AsyncMock, return_value={"t1", "t2"}),
            patch.object(monitor, "_check_and_activate_thread", new_callable=AsyncMock) as mock_check,
        ):
            asyncio.run(monitor._check_all())
        assert mock_check.call_count == 2

    def test_continues_on_exception(self):
        monitor = SessionMonitor()
        async def _check_fail(tid):
            if tid == "t1":
                raise RuntimeError("test error")

        with (
            patch.object(monitor, "_discover_threads_with_sessions", new_callable=AsyncMock, return_value={"t1", "t2"}),
            patch.object(monitor, "_check_and_activate_thread", side_effect=_check_fail),
        ):
            asyncio.run(monitor._check_all())  # Should not raise


# ---------------------------------------------------------------------------
# Terminal marker detection
# ---------------------------------------------------------------------------


class TestTerminalMarker:
    def test_detects_completed(self, tmp_path):
        jsonl = tmp_path / "test.jsonl"
        _write_jsonl(jsonl, [{"role": "ai", "content": "done"}], terminal_status="completed")
        assert SessionMonitor._session_has_terminal_marker(jsonl) is True

    def test_detects_failed(self, tmp_path):
        jsonl = tmp_path / "test.jsonl"
        _write_jsonl(jsonl, [{"role": "ai", "content": "err"}], terminal_status="failed")
        assert SessionMonitor._session_has_terminal_marker(jsonl) is True

    def test_no_terminal_marker(self, tmp_path):
        jsonl = tmp_path / "test.jsonl"
        _write_jsonl(jsonl, [{"role": "ai", "content": "working"}])
        assert SessionMonitor._session_has_terminal_marker(jsonl) is False

    def test_empty_file(self, tmp_path):
        jsonl = tmp_path / "test.jsonl"
        jsonl.write_text("", encoding="utf-8")
        assert SessionMonitor._session_has_terminal_marker(jsonl) is False


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


class TestScheduling:
    def test_start_schedules_timer(self):
        monitor = SessionMonitor()
        loop = asyncio.new_event_loop()
        monitor.start(loop)
        assert monitor._timer is not None
        monitor.stop()
        loop.close()

    def test_stop_cancels_timer(self):
        monitor = SessionMonitor()
        loop = asyncio.new_event_loop()
        monitor.start(loop)
        monitor.stop()
        assert monitor._timer is None
        loop.close()


# ---------------------------------------------------------------------------
# Auto iteration
# ---------------------------------------------------------------------------


def _make_auto_iter_session(
    thread_id="t1",
    iteration_prompt="iterate",
    max_iterations=3,
    max_duration_seconds=3600,
    enabled=True,
):
    return {
        "thread_id": thread_id,
        "iteration_prompt": iteration_prompt,
        "max_iterations": max_iterations,
        "max_duration_seconds": max_duration_seconds,
        "enabled": enabled,
    }


class TestAutoIteration:
    def test_skips_when_no_todos(self):
        """No todos at all → skip (no plan started)."""
        monitor = SessionMonitor(auto_iteration_sessions=[_make_auto_iter_session()])
        with (
            patch.object(monitor, "_has_running_subtask", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_has_active_run", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_is_user_interrupted", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_has_unfinished_todos", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_has_any_todos", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_activate_thread", new_callable=AsyncMock) as mock_activate,
        ):
            asyncio.run(monitor._check_and_activate_thread("t1"))
        mock_activate.assert_not_called()

    def test_skips_when_todos_incomplete(self):
        """Unfinished todos → 会话激活 (not auto iteration), iteration state untouched."""
        monitor = SessionMonitor(auto_iteration_sessions=[_make_auto_iter_session()])
        with (
            patch.object(monitor, "_has_running_subtask", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_has_active_run", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_is_user_interrupted", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_has_unfinished_todos", new_callable=AsyncMock, return_value=True),
            patch.object(monitor, "_activate_thread", new_callable=AsyncMock, return_value=True) as mock_activate,
        ):
            asyncio.run(monitor._check_and_activate_thread("t1"))
        # 会话激活 fires, not auto iteration
        mock_activate.assert_called_once()
        assert "t1" not in monitor._iteration_states

    def test_sends_iteration_prompt_when_todos_done(self):
        """All todos completed, within limits → sends iteration_prompt."""
        session = _make_auto_iter_session(iteration_prompt="go next", max_iterations=5)
        monitor = SessionMonitor(auto_iteration_sessions=[session])
        with (
            patch.object(monitor, "_has_running_subtask", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_has_active_run", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_is_user_interrupted", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_has_unfinished_todos", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_has_any_todos", new_callable=AsyncMock, return_value=True),
            patch.object(monitor, "_activate_thread", new_callable=AsyncMock, return_value=True) as mock_activate,
        ):
            asyncio.run(monitor._check_and_activate_thread("t1"))
        mock_activate.assert_called_once_with("t1", message="go next")
        assert monitor._iteration_states["t1"].iteration_count == 1

    def test_resets_state_when_max_iterations_reached(self):
        """Iteration count >= max_iterations → stop, do NOT reset state."""
        session = _make_auto_iter_session(max_iterations=3)
        monitor = SessionMonitor(auto_iteration_sessions=[session])
        from app.gateway.session_monitor import _IterationState
        monitor._iteration_states["t1"] = _IterationState(iteration_count=3, cycle_start_time=1.0)
        with (
            patch.object(monitor, "_has_running_subtask", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_has_active_run", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_is_user_interrupted", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_has_unfinished_todos", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_has_any_todos", new_callable=AsyncMock, return_value=True),
            patch.object(monitor, "_activate_thread", new_callable=AsyncMock) as mock_activate,
        ):
            asyncio.run(monitor._check_and_activate_thread("t1"))
        mock_activate.assert_not_called()
        # State preserved (not reset) — will resume when user sends a new message
        assert monitor._iteration_states["t1"].iteration_count == 3

    def test_resets_state_when_duration_exceeded(self):
        """Elapsed time >= max_duration_seconds → stop, do NOT reset state."""
        session = _make_auto_iter_session(max_iterations=100, max_duration_seconds=60)
        monitor = SessionMonitor(auto_iteration_sessions=[session])
        from app.gateway.session_monitor import _IterationState
        monitor._iteration_states["t1"] = _IterationState(
            iteration_count=1, cycle_start_time=time.time() - 120
        )
        with (
            patch.object(monitor, "_has_running_subtask", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_has_active_run", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_is_user_interrupted", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_has_unfinished_todos", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_has_any_todos", new_callable=AsyncMock, return_value=True),
            patch.object(monitor, "_activate_thread", new_callable=AsyncMock) as mock_activate,
        ):
            asyncio.run(monitor._check_and_activate_thread("t1"))
        mock_activate.assert_not_called()
        # State preserved — count still 1
        assert monitor._iteration_states["t1"].iteration_count == 1

    def test_disabled_session_not_included_in_check(self):
        """Disabled auto-iteration session is not added to thread_ids in _check_all."""
        session = _make_auto_iter_session(thread_id="t-disabled", enabled=False)
        monitor = SessionMonitor(auto_iteration_sessions=[session])
        with (
            patch.object(monitor, "_discover_threads_with_sessions", new_callable=AsyncMock, return_value=set()),
            patch.object(monitor, "_check_and_activate_thread", new_callable=AsyncMock) as mock_check,
        ):
            asyncio.run(monitor._check_all())
        mock_check.assert_not_called()

    def test_per_session_activation_message_override(self):
        """Per-session activation_message overrides global for 会话激活."""
        monitor = SessionMonitor(
            activation_message="global msg",
            session_activation_overrides={"t1": "per-session msg"},
        )
        assert monitor._get_session_activation_message("t1") == "per-session msg"
        assert monitor._get_session_activation_message("t2") == "global msg"

    def test_iteration_state_cleared_when_active_run(self):
        """Active run resets iteration state."""
        session = _make_auto_iter_session()
        monitor = SessionMonitor(auto_iteration_sessions=[session])
        from app.gateway.session_monitor import _IterationState
        monitor._iteration_states["t1"] = _IterationState(iteration_count=2, cycle_start_time=1.0)
        with (
            patch.object(monitor, "_has_running_subtask", new_callable=AsyncMock, return_value=False),
            patch.object(monitor, "_has_active_run", new_callable=AsyncMock, return_value=True),
            patch.object(monitor, "_activate_thread", new_callable=AsyncMock),
        ):
            asyncio.run(monitor._check_and_activate_thread("t1"))
        assert "t1" not in monitor._iteration_states
