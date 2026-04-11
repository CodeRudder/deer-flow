"""Tests for MainSessionMiddleware.

Covers:
- Message serialization (shared with SubagentSession format)
- JSONL append with deduplication
- Content truncation
- Thread isolation
- Handles summarization (message list shrinks)
- Concurrent access
"""

import json
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

# Break circular imports for testing
_MOCKED_MODULES = [
    "deerflow.sandbox.middleware",
    "deerflow.sandbox.security",
    "deerflow.subagents.executor",
]


@pytest.fixture(autouse=True, scope="module")
def _mock_heavy_deps():
    """Mock heavy dependencies to allow importing the middleware module."""
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
    """Force re-import of the middleware module for each test."""
    for mod in list(sys.modules.keys()):
        if "deerflow.agents.middlewares.main_session_middleware" in mod:
            del sys.modules[mod]
    yield


@pytest.fixture
def tmp_thread_dir(tmp_path):
    """Create a temporary thread directory."""
    d = tmp_path / "threads" / "test-thread"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def mock_paths(tmp_path, tmp_thread_dir):
    """Mock get_paths to use temp directory."""
    from deerflow.config import paths as paths_mod

    mock_p = MagicMock()
    mock_p.thread_dir.return_value = tmp_thread_dir

    with patch.object(paths_mod, "get_paths", return_value=mock_p):
        yield mock_p


class FakeRuntime:
    """Minimal runtime mock with configurable context."""

    def __init__(self, thread_id="test-thread", context=None):
        self.context = context or {"thread_id": thread_id}
        self.config = {"configurable": {"thread_id": thread_id}}


def _make_middleware(max_content_len=50000):
    """Helper: create a MainSessionMiddleware instance."""
    from deerflow.agents.middlewares.main_session_middleware import MainSessionMiddleware

    return MainSessionMiddleware(max_content_len=max_content_len)


def _jsonl_path(tmp_thread_dir):
    """Return the conversation JSONL path for the test thread."""
    return tmp_thread_dir / "conversation.jsonl"


# ── Message Serialization Tests (shared format) ────────────────────────────


class TestSerializeMessage:
    """Verify serialize_message produces the same format as SubagentSession."""

    def test_serialize_human_message(self, mock_paths, tmp_thread_dir):
        from deerflow.subagents.session import serialize_message

        msg = HumanMessage(content="Hello agent")
        result = serialize_message(msg)
        assert result["role"] == "human"
        assert result["content"] == "Hello agent"
        assert "ts" in result

    def test_serialize_ai_message_with_tools(self, mock_paths, tmp_thread_dir):
        from deerflow.subagents.session import serialize_message

        msg = AIMessage(
            content="Let me read that file",
            tool_calls=[{"id": "tc-1", "name": "read_file", "args": {"path": "/tmp/x.py"}}],
        )
        result = serialize_message(msg)
        assert result["role"] == "ai"
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["name"] == "read_file"

    def test_serialize_tool_message(self, mock_paths, tmp_thread_dir):
        from deerflow.subagents.session import serialize_message

        msg = ToolMessage(content="file contents here", tool_call_id="tc-1", name="read_file")
        result = serialize_message(msg)
        assert result["role"] == "tool"
        assert result["tool_call_id"] == "tc-1"

    def test_truncation(self, mock_paths, tmp_thread_dir):
        from deerflow.subagents.session import serialize_message

        big = "x" * 10000
        msg = ToolMessage(content=big, tool_call_id="tc1", name="bash")
        result = serialize_message(msg, max_content_len=1000)
        assert "TRUNCATED" in result["content"]
        assert "10000 chars" in result["content"]
        assert len(result["content"]) < 2000

    def test_backward_compat_alias(self, mock_paths, tmp_thread_dir):
        """The _serialize_message alias should still work."""
        from deerflow.subagents.session import _serialize_message

        msg = HumanMessage(content="hi")
        result = _serialize_message(msg)
        assert result["role"] == "human"


# ── Middleware Integration Tests ────────────────────────────────────────────


class TestMainSessionMiddleware:
    """Test MainSessionMiddleware append and dedup logic."""

    def test_appends_new_messages(self, mock_paths, tmp_thread_dir):
        mw = _make_middleware()
        messages = [
            HumanMessage(content="Hi", id="h1"),
            AIMessage(content="Hello!", id="a1"),
        ]
        new = mw._get_new_messages("test-thread", messages)
        assert len(new) == 2

        mw._write_messages(_jsonl_path(tmp_thread_dir), new)
        lines = _jsonl_path(tmp_thread_dir).read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["role"] == "human"
        assert json.loads(lines[1])["role"] == "ai"

    def test_deduplication(self, mock_paths, tmp_thread_dir):
        mw = _make_middleware()
        messages = [
            HumanMessage(content="Hi", id="h1"),
            AIMessage(content="Hello!", id="a1"),
        ]

        new1 = mw._get_new_messages("test-thread", messages)
        assert len(new1) == 2

        new2 = mw._get_new_messages("test-thread", messages)
        assert len(new2) == 0

    def test_incremental_append(self, mock_paths, tmp_thread_dir):
        mw = _make_middleware()
        msgs1 = [HumanMessage(content="Hi", id="h1"), AIMessage(content="Hello!", id="a1")]
        mw._get_new_messages("test-thread", msgs1)
        mw._write_messages(_jsonl_path(tmp_thread_dir), msgs1)

        msgs2 = msgs1 + [
            ToolMessage(content="result", tool_call_id="tc1", name="bash", id="t1"),
            AIMessage(content="Done!", id="a2"),
        ]
        new = mw._get_new_messages("test-thread", msgs2)
        assert len(new) == 2
        mw._write_messages(_jsonl_path(tmp_thread_dir), new)

        lines = _jsonl_path(tmp_thread_dir).read_text().strip().split("\n")
        assert len(lines) == 4

    def test_handles_summarization(self, mock_paths, tmp_thread_dir):
        """After summarization, message list shrinks but IDs prevent duplicates."""
        mw = _make_middleware()
        original = [
            HumanMessage(content="Q1", id="h1"),
            AIMessage(content="A1", id="a1"),
            HumanMessage(content="Q2", id="h2"),
            AIMessage(content="A2", id="a2"),
        ]
        mw._get_new_messages("test-thread", original)

        # Summarization shrinks to [summary, A2] + new message
        summarized = [
            HumanMessage(content="Summary of conversation", id="sum1"),
            AIMessage(content="A2", id="a2"),  # kept — already tracked
            AIMessage(content="Final answer", id="a3"),  # new
        ]
        new = mw._get_new_messages("test-thread", summarized)
        assert len(new) == 2
        assert new[0].id == "sum1"
        assert new[1].id == "a3"

    def test_thread_isolation(self, mock_paths, tmp_thread_dir):
        mw = _make_middleware()
        msgs_a = [HumanMessage(content="Thread A", id="ha1")]
        msgs_b = [HumanMessage(content="Thread B", id="hb1")]

        new_a = mw._get_new_messages("thread-a", msgs_a)
        new_b = mw._get_new_messages("thread-b", msgs_b)
        assert len(new_a) == 1
        assert len(new_b) == 1

        # Same ID in same thread → dedup
        new_c = mw._get_new_messages("thread-a", msgs_a)
        assert len(new_c) == 0

    def test_truncation_in_write(self, mock_paths, tmp_thread_dir):
        mw = _make_middleware(max_content_len=100)
        big_content = "x" * 10000
        messages = [ToolMessage(content=big_content, tool_call_id="tc1", name="bash", id="t1")]
        new = mw._get_new_messages("test-thread", messages)
        mw._write_messages(_jsonl_path(tmp_thread_dir), new)

        line = _jsonl_path(tmp_thread_dir).read_text().strip()
        entry = json.loads(line)
        assert "TRUNCATED" in entry["content"]

    def test_messages_without_id(self, mock_paths, tmp_thread_dir):
        mw = _make_middleware()
        messages = [HumanMessage(content="No ID")]
        new = mw._get_new_messages("test-thread", messages)
        assert len(new) == 1

    def test_empty_messages(self, mock_paths, tmp_thread_dir):
        mw = _make_middleware()
        new = mw._get_new_messages("test-thread", [])
        assert len(new) == 0

    def test_lru_eviction(self, mock_paths, tmp_thread_dir):
        """Per-thread tracking evicts oldest threads."""
        mw = _make_middleware()
        for i in range(110):
            messages = [HumanMessage(content=f"Thread {i}", id=f"h{i}")]
            mw._get_new_messages(f"thread-{i}", messages)

        assert len(mw._written_ids) <= 101

    def test_restart_dedup_from_existing_jsonl(self, mock_paths, tmp_thread_dir):
        """After restart, middleware reads existing JSONL to populate written IDs."""
        # Step 1: Write initial messages with ID field
        mw = _make_middleware()
        msgs = [
            HumanMessage(content="Hi", id="h1"),
            AIMessage(content="Hello!", id="a1"),
        ]
        new = mw._get_new_messages("test-thread", msgs)
        mw._write_messages(_jsonl_path(tmp_thread_dir), new)

        # Verify file exists with IDs
        lines = _jsonl_path(tmp_thread_dir).read_text().strip().split("\n")
        assert len(lines) == 2

        # Step 2: Simulate restart — new middleware instance (empty _written_ids)
        mw2 = _make_middleware()
        assert len(mw2._written_ids) == 0  # Fresh instance

        # Step 3: Checkpoint restores all messages + one new
        restored = [
            HumanMessage(content="Hi", id="h1"),       # already in JSONL
            AIMessage(content="Hello!", id="a1"),       # already in JSONL
            HumanMessage(content="Next Q", id="h2"),    # new
        ]

        new2 = mw2._get_new_messages("test-thread", restored)
        # Should only return h2 (h1 and a1 loaded from file)
        assert len(new2) == 1
        assert new2[0].id == "h2"


# ── Extract Thread ID Tests ──────────────────────────────────────────────


class TestExtractThreadId:
    def test_from_context(self, mock_paths, tmp_thread_dir):
        from deerflow.agents.middlewares.main_session_middleware import _extract_thread_id

        runtime = FakeRuntime(thread_id="abc-123")
        assert _extract_thread_id(runtime) == "abc-123"

    def test_from_config_fallback(self, mock_paths, tmp_thread_dir):
        from deerflow.agents.middlewares.main_session_middleware import _extract_thread_id

        runtime = MagicMock()
        runtime.context = None
        runtime.config = {"configurable": {"thread_id": "xyz-789"}}
        assert _extract_thread_id(runtime) == "xyz-789"

    def test_no_thread_id(self, mock_paths, tmp_thread_dir):
        from deerflow.agents.middlewares.main_session_middleware import _extract_thread_id

        runtime = MagicMock()
        runtime.context = None
        runtime.config = {"configurable": {}}
        assert _extract_thread_id(runtime) is None


# ── Concurrent Access Test ──────────────────────────────────────────────


class TestConcurrentAccess:
    def test_concurrent_threads(self, mock_paths, tmp_path):
        """Multiple threads writing to different threads should not conflict."""
        mw = _make_middleware()
        errors = []

        def write_thread(thread_idx):
            try:
                tid = f"thread-{thread_idx}"
                thread_dir = tmp_path / "threads" / tid
                thread_dir.mkdir(parents=True, exist_ok=True)
                for j in range(10):
                    msgs = [HumanMessage(content=f"Msg {j}", id=f"h{thread_idx}-{j}")]
                    new = mw._get_new_messages(tid, msgs)
                    if new:
                        mw._write_messages(thread_dir / "conversation.jsonl", new)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_thread, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors during concurrent access: {errors}"

        for i in range(5):
            jsonl = tmp_path / "threads" / f"thread-{i}" / "conversation.jsonl"
            lines = jsonl.read_text().strip().split("\n")
            assert len(lines) == 10, f"Thread {i}: expected 10 lines, got {len(lines)}"


# ── Format Compatibility Test ────────────────────────────────────────────


class TestFormatCompatibility:
    """Verify main session JSONL format matches sub-agent session format."""

    def test_same_format_as_subagent(self, mock_paths, tmp_thread_dir):
        from deerflow.subagents.session import serialize_message

        # Messages written by MainSessionMiddleware
        msgs = [
            HumanMessage(content="Hi", id="h1"),
            AIMessage(content="Working", tool_calls=[{"id": "tc1", "name": "bash", "args": {"cmd": "ls"}}], id="a1"),
            ToolMessage(content="file1.txt", tool_call_id="tc1", name="bash", id="t1"),
        ]

        # Serialize with both (main uses truncation, sub doesn't)
        for msg in msgs:
            main_entry = serialize_message(msg, max_content_len=50000)
            sub_entry = serialize_message(msg)
            # Same roles and content
            assert main_entry["role"] == sub_entry["role"]
            assert main_entry["content"] == sub_entry["content"]
