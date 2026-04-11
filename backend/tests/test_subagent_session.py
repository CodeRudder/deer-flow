"""Tests for Sub-Agent session persistence (subagents/session.py).

Covers:
- Message serialization (Human/AI/Tool messages)
- JSONL append and read
- Status markers (completed/failed/interrupted)
- Summary JSON generation
- is_terminal property
- find_interrupted / list_sessions class methods
- Edge cases: empty files, corrupt lines, missing directories
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

# Break circular imports for testing
_MOCKED_MODULES = [
    "deerflow.agents",
    "deerflow.agents.thread_state",
    "deerflow.agents.middlewares",
    "deerflow.agents.middlewares.thread_data_middleware",
    "deerflow.sandbox",
    "deerflow.sandbox.middleware",
    "deerflow.sandbox.security",
    "deerflow.models",
    "deerflow.subagents.executor",
]


@pytest.fixture(autouse=True, scope="module")
def _mock_heavy_deps():
    """Mock heavy dependencies to allow importing session module."""
    saved = {name: sys.modules.get(name) for name in _MOCKED_MODULES}
    for name in _MOCKED_MODULES:
        if name not in sys.modules:
            sys.modules[name] = MagicMock()

    yield

    for name in _MOCKED_MODULES:
        if saved[name] is None and name in sys.modules:
            del sys.modules[name]
        elif saved[name] is not None:
            sys.modules[name] = saved[name]


@pytest.fixture(autouse=True)
def _reset_imports():
    """Force re-import of session module for each test."""
    for mod in list(sys.modules.keys()):
        if "deerflow.subagents.session" in mod:
            del sys.modules[mod]
    yield


@pytest.fixture
def tmp_session_dir(tmp_path):
    """Create a temporary session directory structure."""
    session_dir = tmp_path / "threads" / "test-thread-123" / "subagents"
    session_dir.mkdir(parents=True)
    return session_dir


@pytest.fixture
def mock_paths(tmp_path):
    """Mock get_paths to use temp directory."""
    from deerflow.config import paths as paths_mod

    # Create a mock Paths object
    mock_p = MagicMock()
    mock_p.subagent_dir = MagicMock(return_value=tmp_path / "threads" / "test-thread" / "subagents")
    mock_p.subagent_dir.return_value.mkdir(parents=True, exist_ok=True)

    with patch.object(paths_mod, "get_paths", return_value=mock_p):
        yield mock_p


def _make_session(thread_id="test-thread", task_id="task-001", subagent_name="developer", description="test task"):
    """Helper: create a SubagentSession with mocked paths."""
    from deerflow.subagents.session import SubagentSession

    return SubagentSession(
        thread_id=thread_id,
        task_id=task_id,
        subagent_name=subagent_name,
        description=description,
    )


# ── Message Serialization Tests ──────────────────────────────────────────


class TestSerializeMessage:
    """Test _serialize_message helper."""

    def test_serialize_human_message(self):
        from deerflow.subagents.session import _serialize_message

        msg = HumanMessage(content="Hello agent")
        result = _serialize_message(msg)
        assert result["role"] == "human"
        assert result["content"] == "Hello agent"
        assert "ts" in result

    def test_serialize_ai_message_no_tools(self):
        from deerflow.subagents.session import _serialize_message

        msg = AIMessage(content="I will help you")
        result = _serialize_message(msg)
        assert result["role"] == "ai"
        assert result["content"] == "I will help you"
        assert "tool_calls" not in result

    def test_serialize_ai_message_with_tools(self):
        from deerflow.subagents.session import _serialize_message

        msg = AIMessage(
            content="Let me read that file",
            tool_calls=[{"id": "tc-1", "name": "read_file", "args": {"path": "/tmp/x.py"}}],
        )
        result = _serialize_message(msg)
        assert result["role"] == "ai"
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["id"] == "tc-1"
        assert result["tool_calls"][0]["name"] == "read_file"

    def test_serialize_tool_message(self):
        from deerflow.subagents.session import _serialize_message

        msg = ToolMessage(content="file contents here", tool_call_id="tc-1", name="read_file")
        result = _serialize_message(msg)
        assert result["role"] == "tool"
        assert result["tool_call_id"] == "tc-1"
        assert result["content"] == "file contents here"
        assert result["name"] == "read_file"


# ── Append & Read Tests ──────────────────────────────────────────────────


class TestAppendAndRead:
    """Test JSONL append and read operations."""

    def test_append_single_message(self, mock_paths, tmp_path):
        session = _make_session()
        jsonl = mock_paths.subagent_dir.return_value / "task-001.jsonl"

        session.append_message(HumanMessage(content="Start task"))
        assert jsonl.exists()
        lines = jsonl.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["role"] == "human"
        assert entry["content"] == "Start task"

    def test_append_multiple_messages(self, mock_paths, tmp_path):
        session = _make_session()
        jsonl = mock_paths.subagent_dir.return_value / "task-001.jsonl"

        msgs = [
            HumanMessage(content="Task prompt"),
            AIMessage(content="Working on it", tool_calls=[{"id": "tc1", "name": "bash", "args": {"cmd": "ls"}}]),
            ToolMessage(content="file1.txt\nfile2.txt", tool_call_id="tc1", name="bash"),
            AIMessage(content="Done"),
        ]
        session.append_messages(msgs)

        lines = jsonl.read_text().strip().split("\n")
        assert len(lines) == 4
        roles = [json.loads(l)["role"] for l in lines]
        assert roles == ["human", "ai", "tool", "ai"]

    def test_read_messages_excludes_status_markers(self, mock_paths, tmp_path):
        session = _make_session()
        jsonl = mock_paths.subagent_dir.return_value / "task-001.jsonl"

        # Write messages + status marker manually
        lines = [
            json.dumps({"ts": "2026-01-01", "role": "human", "content": "hi"}),
            json.dumps({"ts": "2026-01-01", "role": "ai", "content": "hello"}),
            json.dumps({"ts": "2026-01-01", "status": "completed", "result": "done"}),
        ]
        jsonl.write_text("\n".join(lines) + "\n")

        messages = session.read_messages()
        assert len(messages) == 2
        assert messages[0]["role"] == "human"
        assert messages[1]["role"] == "ai"

    def test_read_messages_empty_file(self, mock_paths, tmp_path):
        session = _make_session()
        jsonl = mock_paths.subagent_dir.return_value / "task-001.jsonl"

        # File doesn't exist yet
        messages = session.read_messages()
        assert messages == []

    def test_read_messages_skips_corrupt_lines(self, mock_paths, tmp_path):
        session = _make_session()
        jsonl = mock_paths.subagent_dir.return_value / "task-001.jsonl"

        jsonl.write_text("not json\n{bad\n" + json.dumps({"ts": "t", "role": "ai", "content": "ok"}) + "\n")
        messages = session.read_messages()
        assert len(messages) == 1
        assert messages[0]["content"] == "ok"


# ── Status Marker Tests ──────────────────────────────────────────────────


class TestStatusMarkers:
    """Test mark_completed, mark_failed, mark_interrupted."""

    def test_mark_completed(self, mock_paths, tmp_path):
        session = _make_session()
        jsonl = mock_paths.subagent_dir.return_value / "task-001.jsonl"
        summary = mock_paths.subagent_dir.return_value / "task-001.summary.json"

        session.append_message(HumanMessage(content="Do work"))
        session.mark_completed(result="Work done", message_count=1)

        # Check JSONL has status marker
        lines = jsonl.read_text().strip().split("\n")
        last = json.loads(lines[-1])
        assert last["status"] == "completed"
        assert "result" in last

        # Check summary JSON
        assert summary.exists()
        s = json.loads(summary.read_text())
        assert s["status"] == "completed"
        assert s["task_id"] == "task-001"
        assert s["message_count"] == 1

    def test_mark_failed(self, mock_paths, tmp_path):
        session = _make_session()
        session.append_message(HumanMessage(content="Do work"))
        session.mark_failed(error="Connection timeout", message_count=1)

        jsonl = mock_paths.subagent_dir.return_value / "task-001.jsonl"
        lines = jsonl.read_text().strip().split("\n")
        last = json.loads(lines[-1])
        assert last["status"] == "failed"
        assert "Connection timeout" in last["error"]

    def test_mark_interrupted(self, mock_paths, tmp_path):
        session = _make_session()
        session.append_message(HumanMessage(content="Do work"))
        session.mark_interrupted(message_count=3)

        jsonl = mock_paths.subagent_dir.return_value / "task-001.jsonl"
        lines = jsonl.read_text().strip().split("\n")
        last = json.loads(lines[-1])
        assert last["status"] == "interrupted"


# ── is_terminal Property Tests ───────────────────────────────────────────


class TestIsTerminal:
    """Test is_terminal property."""

    def test_terminal_when_completed(self, mock_paths, tmp_path):
        session = _make_session()
        session.append_message(HumanMessage(content="hi"))
        session.mark_completed(result="done")
        assert session.is_terminal is True

    def test_terminal_when_failed(self, mock_paths, tmp_path):
        session = _make_session()
        session.append_message(HumanMessage(content="hi"))
        session.mark_failed(error="oops")
        assert session.is_terminal is True

    def test_not_terminal_when_running(self, mock_paths, tmp_path):
        session = _make_session()
        session.append_message(HumanMessage(content="hi"))
        assert session.is_terminal is False

    def test_not_terminal_when_no_file(self, mock_paths, tmp_path):
        session = _make_session()
        # No JSONL file created yet
        assert session.is_terminal is False


# ── Summary Tests ────────────────────────────────────────────────────────


class TestReadSummary:
    """Test read_summary."""

    def test_read_summary_exists(self, mock_paths, tmp_path):
        session = _make_session()
        session.append_message(HumanMessage(content="hi"))
        session.mark_completed(result="done", message_count=1)

        s = session.read_summary()
        assert s is not None
        assert s["status"] == "completed"
        assert s["subagent_name"] == "developer"
        assert s["thread_id"] == "test-thread"

    def test_read_summary_missing(self, mock_paths, tmp_path):
        session = _make_session()
        assert session.read_summary() is None


# ── Class Method Tests ──────────────────────────────────────────────────


class TestFindInterrupted:
    """Test find_interrupted static method."""

    def test_find_interrupted_returns_non_terminal(self, mock_paths, tmp_path):
        from deerflow.subagents.session import SubagentSession

        d = mock_paths.subagent_dir.return_value
        # Create a running session (no terminal marker)
        jsonl = d / "task-running.jsonl"
        jsonl.write_text(json.dumps({"ts": "t", "role": "human", "content": "work"}) + "\n")

        # Create a completed session
        jsonl2 = d / "task-done.jsonl"
        jsonl2.write_text(
            json.dumps({"ts": "t", "role": "ai", "content": "ok"}) + "\n"
            + json.dumps({"ts": "t", "status": "completed", "result": "done"}) + "\n"
        )

        interrupted = SubagentSession.find_interrupted("test-thread")
        task_ids = [s.task_id for s in interrupted]
        assert "task-running" in task_ids
        assert "task-done" not in task_ids

    def test_find_interrupted_empty_dir(self, mock_paths, tmp_path):
        from deerflow.subagents.session import SubagentSession

        result = SubagentSession.find_interrupted("test-thread")
        assert result == []


class TestListSessions:
    """Test list_sessions static method."""

    def test_list_sessions_includes_all(self, mock_paths, tmp_path):
        from deerflow.subagents.session import SubagentSession

        d = mock_paths.subagent_dir.return_value
        # Create two sessions with summaries
        for tid in ["task-a", "task-b"]:
            (d / f"{tid}.jsonl").write_text(
                json.dumps({"ts": "t", "role": "ai", "content": "ok"}) + "\n"
                + json.dumps({"ts": "t", "status": "completed", "result": "done"}) + "\n"
            )
            (d / f"{tid}.summary.json").write_text(
                json.dumps({"subagent_name": "dev", "description": f"task {tid}", "started_at": "t"})
            )

        sessions = SubagentSession.list_sessions("test-thread")
        assert len(sessions) == 2
        task_ids = {s.task_id for s in sessions}
        assert task_ids == {"task-a", "task-b"}
