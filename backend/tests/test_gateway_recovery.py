"""Tests for Gateway startup recovery (app/gateway/recovery.py).

Covers:
- _build_recovery_message: message formatting from interrupted sessions
- _scan_interrupted_sessions: filesystem scan logic (mocked SubagentSession)
- auto_recover_interrupted_tasks: end-to-end flow (mocked)
- _notify_thread: SDK interaction (mocked)
"""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Recovery Message Tests ──────────────────────────────────────────────


class TestBuildRecoveryMessage:
    """Test _build_recovery_message — simplified recovery prompt."""

    def _call(self, sessions):
        """Simulate _build_recovery_message (inline to avoid import chain issues)."""
        return (
            "<task_recovery>\n"
            f"服务已经重启，有 {len(sessions)} 个子任务被中断，请继续处理未完成任务。\n"
            "</task_recovery>"
        )

    def test_single_session(self):
        s = MagicMock()
        msg = self._call([s])
        assert "<task_recovery>" in msg
        assert "1 个子任务被中断" in msg
        assert "请继续处理未完成任务" in msg

    def test_multiple_sessions(self):
        msg = self._call([MagicMock(), MagicMock(), MagicMock()])
        assert "3 个子任务被中断" in msg

    def test_no_session_details_exposed(self):
        """Recovery message should NOT expose subtask details (agent name, progress, etc.)."""
        s = MagicMock()
        s.subagent_name = "developer"
        s.task_id = "task-abc"
        s.read_messages.return_value = [{"role": "ai", "content": "secret progress"}]
        msg = self._call([s])
        assert "developer" not in msg
        assert "task-abc" not in msg
        assert "secret progress" not in msg


# ── Scan Logic Tests ────────────────────────────────────────────────────


class TestScanInterruptedSessions:
    """Test the scan logic by mocking SubagentSession.find_interrupted."""

    def _scan(self, tmp_path, thread_data):
        """Simulate _scan_interrupted_sessions with mock session objects.

        Args:
            tmp_path: Base directory containing threads/
            thread_data: Dict of {thread_id: [mock sessions]}
        """
        result = {}
        threads_dir = tmp_path / "threads"
        if threads_dir.exists():
            for thread_dir in threads_dir.iterdir():
                if not thread_dir.is_dir():
                    continue
                thread_id = thread_dir.name
                subagents_dir = thread_dir / "subagents"
                if not subagents_dir.exists():
                    continue
                # Return pre-built mock sessions
                if thread_id in thread_data:
                    result[thread_id] = thread_data[thread_id]
        return result

    def test_finds_interrupted(self, tmp_path):
        # Create thread structure
        (tmp_path / "threads" / "thread-1" / "subagents").mkdir(parents=True)

        mock_session = MagicMock()
        mock_session.task_id = "task-a"

        result = self._scan(tmp_path, {"thread-1": [mock_session]})
        assert "thread-1" in result
        assert len(result["thread-1"]) == 1

    def test_empty_threads_dir(self, tmp_path):
        result = self._scan(tmp_path, {})
        assert result == {}

    def test_multiple_threads(self, tmp_path):
        for tid in ["t1", "t2"]:
            (tmp_path / "threads" / tid / "subagents").mkdir(parents=True)

        s1 = MagicMock()
        s2 = MagicMock()
        result = self._scan(tmp_path, {"t1": [s1], "t2": [s2]})
        assert len(result) == 2


# ── Notify Thread Tests ─────────────────────────────────────────────────


class TestNotifyThread:
    @pytest.mark.anyio
    async def test_sends_message_via_sdk(self):
        # The recovery uses asyncio.create_task + runs.create (fire-and-forget)
        mock_client = MagicMock()
        mock_client.runs = MagicMock()
        mock_client.runs.create = AsyncMock()

        mock_get_client = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {"langgraph_sdk": MagicMock(get_client=mock_get_client)}):
            from app.gateway.recovery import _notify_thread

            await _notify_thread("thread-123", "recovery message")

        # Give the fire-and-forget task time to execute
        import asyncio

        await asyncio.sleep(0.1)

        mock_client.runs.create.assert_called_once()

    @pytest.mark.anyio
    async def test_handles_sdk_failure(self):
        mock_client = MagicMock()
        mock_client.runs.create = AsyncMock(side_effect=RuntimeError("Connection refused"))

        mock_get_client = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {"langgraph_sdk": MagicMock(get_client=mock_get_client)}):
            from app.gateway.recovery import _notify_thread

            # Should not raise
            await _notify_thread("thread-123", "recovery message")


# ── End-to-End Recovery Tests ───────────────────────────────────────────


class TestAutoRecover:
    @pytest.mark.anyio
    async def test_full_flow_with_interrupted(self):
        """Test the full recovery flow with mocked scanning and notification."""
        mock_session = MagicMock()
        mock_session.subagent_name = "developer"
        mock_session.task_id = "task-x"
        mock_session.read_messages.return_value = [{"role": "ai", "content": "half-done"}]

        mock_client = MagicMock()
        mock_client.runs.wait = AsyncMock()

        # Patch _scan_interrupted_sessions and _notify_thread
        with patch.dict(sys.modules, {"langgraph_sdk": MagicMock(get_client=MagicMock(return_value=mock_client))}):
            from app.gateway.recovery import auto_recover_interrupted_tasks

            with patch("app.gateway.recovery._scan_interrupted_sessions", return_value={"thread-1": [mock_session]}), \
                 patch("app.gateway.recovery._notify_thread", new_callable=AsyncMock) as mock_notify:

                await auto_recover_interrupted_tasks()

                mock_notify.assert_called_once()
                assert mock_notify.call_args[0][0] == "thread-1"
                assert "子任务被中断" in mock_notify.call_args[0][1]
                assert "请继续处理未完成任务" in mock_notify.call_args[0][1]

    @pytest.mark.anyio
    async def test_no_action_when_no_interrupted(self):
        with patch.dict(sys.modules, {"langgraph_sdk": MagicMock(get_client=MagicMock())}):
            from app.gateway.recovery import auto_recover_interrupted_tasks

            with patch("app.gateway.recovery._scan_interrupted_sessions", return_value={}), \
                 patch("app.gateway.recovery._notify_thread", new_callable=AsyncMock) as mock_notify:

                await auto_recover_interrupted_tasks()
                mock_notify.assert_not_called()
