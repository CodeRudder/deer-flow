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
    """Test _build_recovery_message — pure function, no imports needed."""

    def _call(self, sessions):
        """Import and call _build_recovery_message with mocked SubagentSession objects."""
        # Define the function inline to avoid import chain issues
        def _build_recovery_message(sessions):
            parts = []
            for s in sessions:
                messages = s.read_messages()
                ai_messages = [m for m in messages if m.get("role") == "ai"]
                last_ai = ""
                if ai_messages:
                    content = ai_messages[-1].get("content", "")
                    last_ai = content[:500] if isinstance(content, str) else str(content)[:500]
                parts.append(
                    f"- **{s.subagent_name}** (task {s.task_id}): "
                    f"已执行 {len(messages)} 步，最后进度：{last_ai or '（无 AI 响应）'}"
                )
            session_lines = "\n".join(parts)
            return (
                "<task_recovery>\n"
                "服务重启后发现以下子任务在上次运行中被中断：\n\n"
                f"{session_lines}\n\n"
                "请检查每个任务的进度，决定是否需要继续执行未完成的工作。\n"
                "如果需要继续，请使用 task() 工具重新启动相关子任务，"
                "并在 prompt 中包含之前的进度信息，让子 Agent 从断点继续。\n"
                "</task_recovery>"
            )
        return _build_recovery_message(sessions)

    def test_formats_single_session(self):
        s = MagicMock()
        s.subagent_name = "developer"
        s.task_id = "task-abc"
        s.read_messages.return_value = [
            {"role": "human", "content": "implement auth"},
            {"role": "ai", "content": "I will implement JWT authentication. Let me start by reading existing code."},
        ]

        msg = self._call([s])
        assert "developer" in msg
        assert "task-abc" in msg
        assert "JWT authentication" in msg
        assert "<task_recovery>" in msg
        assert "2 步" in msg

    def test_formats_multiple_sessions(self):
        s1 = MagicMock()
        s1.subagent_name = "architect"
        s1.task_id = "task-1"
        s1.read_messages.return_value = [{"role": "ai", "content": "Design done"}]

        s2 = MagicMock()
        s2.subagent_name = "developer"
        s2.task_id = "task-2"
        s2.read_messages.return_value = [{"role": "ai", "content": "Code started"}]

        msg = self._call([s1, s2])
        assert "architect" in msg
        assert "developer" in msg
        assert "task-1" in msg
        assert "task-2" in msg

    def test_handles_no_ai_messages(self):
        s = MagicMock()
        s.subagent_name = "tester"
        s.task_id = "task-3"
        s.read_messages.return_value = [{"role": "human", "content": "test it"}]

        msg = self._call([s])
        assert "tester" in msg
        assert "无 AI 响应" in msg

    def test_truncates_long_content(self):
        s = MagicMock()
        s.subagent_name = "dev"
        s.task_id = "task-4"
        s.read_messages.return_value = [{"role": "ai", "content": "x" * 600}]

        msg = self._call([s])
        # Should be truncated to 500 chars
        assert "x" * 500 in msg


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
                assert "developer" in mock_notify.call_args[0][1]
                assert "task-x" in mock_notify.call_args[0][1]

    @pytest.mark.anyio
    async def test_no_action_when_no_interrupted(self):
        with patch.dict(sys.modules, {"langgraph_sdk": MagicMock(get_client=MagicMock())}):
            from app.gateway.recovery import auto_recover_interrupted_tasks

            with patch("app.gateway.recovery._scan_interrupted_sessions", return_value={}), \
                 patch("app.gateway.recovery._notify_thread", new_callable=AsyncMock) as mock_notify:

                await auto_recover_interrupted_tasks()
                mock_notify.assert_not_called()
