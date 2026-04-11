"""Tests for SummarizationLoopMiddleware.

Covers:
- Summary message detection and counting
- No-loop passthrough (returns None)
- Soft limit: ToolMessage truncation + warning injection
- Hard limit: all ToolMessage truncation + tool_calls removal + forced stop
- Counter reset when no summary messages present
- Multi-thread isolation
- LRU eviction
"""

from unittest.mock import MagicMock

import pytest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def _make_runtime(thread_id: str = "test-thread"):
    """Create a mock Runtime with a thread_id."""
    runtime = MagicMock()
    runtime.context = {"thread_id": thread_id}
    return runtime


def _summary_msg(text: str = "Previous work completed.") -> HumanMessage:
    """Create a message that looks like a summarization output."""
    return HumanMessage(content=f"Here is a summary of the conversation to date:\n\n{text}")


def _import_middleware():
    """Import the middleware class."""
    from deerflow.agents.middlewares.summarization_loop_middleware import SummarizationLoopMiddleware

    return SummarizationLoopMiddleware


# ── Detection Logic Tests ────────────────────────────────────────────────


class TestDetection:
    """Test _detect method."""

    def test_counts_summary_messages(self):
        MW = _import_middleware()
        mw = MW()
        messages = [
            _summary_msg("First summary"),
            HumanMessage(content="User message"),
            AIMessage(content="AI response"),
            _summary_msg("Second summary"),
        ]
        count, large, all_tools = mw._detect(messages)
        assert count == 2
        assert large == []
        assert all_tools == []

    def test_finds_large_tool_messages(self):
        MW = _import_middleware()
        mw = MW(max_tool_content_len=100)
        messages = [
            AIMessage(content="Let me read the file", tool_calls=[{"id": "tc1", "name": "read_file", "args": {"path": "/foo"}}]),
            ToolMessage(content="x" * 500, tool_call_id="tc1", name="read_file"),
        ]
        count, large, all_tools = mw._detect(messages)
        assert count == 0
        assert large == [1]
        assert all_tools == [1]

    def test_mixed_messages(self):
        MW = _import_middleware()
        mw = MW(max_tool_content_len=200)
        messages = [
            _summary_msg("Summary here"),
            HumanMessage(content="Read the auth module"),
            AIMessage(content="", tool_calls=[{"id": "tc1", "name": "read_file", "args": {}}]),
            ToolMessage(content="y" * 300, tool_call_id="tc1"),
            _summary_msg("Another summary"),
            AIMessage(content="Done"),
        ]
        count, large, all_tools = mw._detect(messages)
        assert count == 2
        assert large == [3]
        assert all_tools == [3]

    def test_empty_messages(self):
        MW = _import_middleware()
        mw = MW()
        count, large, all_tools = mw._detect([])
        assert count == 0
        assert large == []
        assert all_tools == []


# ── No Loop (passthrough) Tests ──────────────────────────────────────────


class TestNoLoop:
    """Test that middleware returns None when no summarization loop is detected."""

    def test_no_summary_messages(self):
        MW = _import_middleware()
        mw = MW()
        state = {
            "messages": [
                HumanMessage(content="Hello"),
                AIMessage(content="Hi there"),
            ]
        }
        result = mw._apply(state, _make_runtime())
        assert result is None

    def test_one_summary_below_threshold(self):
        MW = _import_middleware()
        mw = MW(warn_threshold=2)
        state = {
            "messages": [
                _summary_msg("First summary"),
                HumanMessage(content="Continue"),
            ]
        }
        result = mw._apply(state, _make_runtime())
        assert result is None

    def test_counter_resets_when_no_summary(self):
        MW = _import_middleware()
        mw = MW(warn_threshold=2)
        thread_id = "reset-test"
        runtime = _make_runtime(thread_id)

        # First: 2 summary messages → triggers soft limit
        state1 = {
            "messages": [
                _summary_msg("S1"),
                _summary_msg("S2"),
            ]
        }
        result1 = mw._apply(state1, runtime)
        assert result1 is not None  # soft limit triggered

        # Second: no summary messages → should reset and return None
        state2 = {
            "messages": [
                HumanMessage(content="New user message"),
                AIMessage(content="Response"),
            ]
        }
        result2 = mw._apply(state2, runtime)
        assert result2 is None


# ── Soft Limit Tests ────────────────────────────────────────────────────


class TestSoftLimit:
    """Test soft limit behavior: truncate large ToolMessages + inject warning."""

    def test_truncates_large_tool_content(self):
        MW = _import_middleware()
        mw = MW(warn_threshold=2, hard_limit=3, max_tool_content_len=100)

        large_content = "A" * 500
        state = {
            "messages": [
                _summary_msg("S1"),
                _summary_msg("S2"),
                AIMessage(content="", tool_calls=[{"id": "tc1", "name": "read_file", "args": {}}]),
                ToolMessage(content=large_content, tool_call_id="tc1"),
            ]
        }
        result = mw._apply(state, _make_runtime())
        assert result is not None

        updated = result["messages"]
        # Last message should be warning HumanMessage
        assert isinstance(updated[-1], HumanMessage)
        assert "SUMMARIZATION LOOP" in updated[-1].content

        # ToolMessage should be truncated
        tool_msgs = [m for m in updated if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 1
        assert len(tool_msgs[0].content) < 200  # 100 + truncation notice
        assert "TRUNCATED" in tool_msgs[0].content
        assert "original 500 chars" in tool_msgs[0].content

    def test_does_not_truncate_small_tool_content(self):
        MW = _import_middleware()
        mw = MW(warn_threshold=2, max_tool_content_len=200)

        small_content = "Small response"
        state = {
            "messages": [
                _summary_msg("S1"),
                _summary_msg("S2"),
                AIMessage(content=""),
                ToolMessage(content=small_content, tool_call_id="tc1"),
            ]
        }
        result = mw._apply(state, _make_runtime())
        assert result is not None

        tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 1
        assert tool_msgs[0].content == small_content  # unchanged

    def test_injects_warning_message(self):
        MW = _import_middleware()
        mw = MW(warn_threshold=2)
        state = {
            "messages": [
                _summary_msg("S1"),
                _summary_msg("S2"),
            ]
        }
        result = mw._apply(state, _make_runtime())
        assert result is not None

        # Last message is the warning
        last = result["messages"][-1]
        assert isinstance(last, HumanMessage)
        assert "SUMMARIZATION LOOP" in last.content


# ── Hard Limit Tests ────────────────────────────────────────────────────


class TestHardLimit:
    """Test hard limit behavior: truncate all ToolMessages + strip tool_calls."""

    def test_strips_tool_calls_from_last_ai(self):
        MW = _import_middleware()
        mw = MW(warn_threshold=2, hard_limit=3, max_tool_content_len=100)

        state = {
            "messages": [
                _summary_msg("S1"),
                _summary_msg("S2"),
                _summary_msg("S3"),
                AIMessage(
                    content="Let me read more",
                    tool_calls=[{"id": "tc1", "name": "read_file", "args": {"path": "/big"}}],
                ),
                ToolMessage(content="B" * 500, tool_call_id="tc1"),
            ]
        }
        result = mw._apply(state, _make_runtime())
        assert result is not None

        updated = result["messages"]

        # Last AI message should have no tool_calls
        ai_msgs = [m for m in updated if isinstance(m, AIMessage)]
        last_ai = ai_msgs[-1]
        assert last_ai.tool_calls == []
        assert "FORCED STOP" in last_ai.content

        # ToolMessage should be truncated
        tool_msgs = [m for m in updated if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 1
        assert "TRUNCATED" in tool_msgs[0].content

        # Last message should be hard stop HumanMessage
        assert isinstance(updated[-1], HumanMessage)
        assert "FORCED STOP" in updated[-1].content

    def test_no_tool_calls_to_strip(self):
        MW = _import_middleware()
        mw = MW(warn_threshold=2, hard_limit=3)

        state = {
            "messages": [
                _summary_msg("S1"),
                _summary_msg("S2"),
                _summary_msg("S3"),
                AIMessage(content="I'm done analyzing."),
            ]
        }
        result = mw._apply(state, _make_runtime())
        # Still returns messages (with hard stop message injected)
        assert result is not None
        last = result["messages"][-1]
        assert "FORCED STOP" in last.content


# ── Thread Isolation Tests ───────────────────────────────────────────────


class TestThreadIsolation:
    """Test that different threads have independent tracking."""

    def test_independent_counts(self):
        MW = _import_middleware()
        mw = MW(warn_threshold=2, hard_limit=3)

        # Thread A: 2 summaries → soft limit
        state_a = {
            "messages": [_summary_msg("S1"), _summary_msg("S2")]
        }
        result_a = mw._apply(state_a, _make_runtime("thread-a"))
        assert result_a is not None  # soft limit

        # Thread B: no summaries → None
        state_b = {
            "messages": [HumanMessage(content="Hello"), AIMessage(content="Hi")]
        }
        result_b = mw._apply(state_b, _make_runtime("thread-b"))
        assert result_b is None

    def test_independent_hard_limit(self):
        MW = _import_middleware()
        mw = MW(warn_threshold=2, hard_limit=3)

        # Thread A: 2 summaries
        state_a2 = {
            "messages": [_summary_msg("S1"), _summary_msg("S2")]
        }
        mw._apply(state_a2, _make_runtime("thread-a"))

        # Thread B: 3 summaries → hard limit
        state_b3 = {
            "messages": [_summary_msg("S1"), _summary_msg("S2"), _summary_msg("S3")]
        }
        result_b = mw._apply(state_b3, _make_runtime("thread-b"))
        assert result_b is not None
        # Verify hard stop injected
        last = result_b["messages"][-1]
        assert "FORCED STOP" in last.content


# ── LRU Eviction Tests ──────────────────────────────────────────────────


class TestLRUEviction:
    """Test that old threads are evicted when limit is reached."""

    def test_evicts_oldest_thread(self):
        MW = _import_middleware()
        mw = MW(warn_threshold=1, max_tracked_threads=3)

        # Fill 3 threads
        for i in range(3):
            mw._apply(
                {"messages": [_summary_msg(f"S{i}")]},
                _make_runtime(f"thread-{i}"),
            )

        assert len(mw._summary_counts) == 3

        # Add 4th thread — should evict thread-0
        mw._apply(
            {"messages": [_summary_msg("S-new")]},
            _make_runtime("thread-3"),
        )
        assert len(mw._summary_counts) == 3
        assert "thread-0" not in mw._summary_counts
        assert "thread-3" in mw._summary_counts


# ── Reset Tests ─────────────────────────────────────────────────────────


class TestReset:
    """Test reset functionality."""

    def test_reset_specific_thread(self):
        MW = _import_middleware()
        mw = MW()
        mw._summary_counts["thread-a"] = 5
        mw._summary_counts["thread-b"] = 3

        mw.reset("thread-a")
        assert "thread-a" not in mw._summary_counts
        assert "thread-b" in mw._summary_counts

    def test_reset_all(self):
        MW = _import_middleware()
        mw = MW()
        mw._summary_counts["thread-a"] = 5
        mw._summary_counts["thread-b"] = 3

        mw.reset()
        assert len(mw._summary_counts) == 0


# ── Truncation Helper Tests ─────────────────────────────────────────────


class TestTruncation:
    """Test _truncate_content helper."""

    def test_truncates_correctly(self):
        MW = _import_middleware()
        mw = MW(max_tool_content_len=100)
        result = mw._truncate_content("A" * 200, 100)
        assert result.startswith("A" * 100)
        assert "TRUNCATED" in result
        assert "200 chars" in result

    def test_short_content_unchanged(self):
        MW = _import_middleware()
        mw = MW()
        # This method always truncates at max_len, caller checks length first
        result = mw._truncate_content("Short", 100)
        assert "Short" in result


# ── Async Tests ─────────────────────────────────────────────────────────


class TestAsync:
    """Test that async method delegates to sync."""

    @pytest.mark.anyio
    async def test_abefore_model_delegates(self):
        MW = _import_middleware()
        mw = MW(warn_threshold=2)

        state = {
            "messages": [_summary_msg("S1"), _summary_msg("S2")]
        }
        result = await mw.abefore_model(state, _make_runtime())
        assert result is not None

    @pytest.mark.anyio
    async def test_abefore_model_no_loop(self):
        MW = _import_middleware()
        mw = MW()

        state = {
            "messages": [HumanMessage(content="Hello")]
        }
        result = await mw.abefore_model(state, _make_runtime())
        assert result is None
