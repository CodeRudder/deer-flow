"""Tests for TodoMiddleware context-loss detection and incremental operations."""

import asyncio
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.middlewares.todo_middleware import (
    TodoMiddleware,
    _format_todos,
    _reminder_in_messages,
    _todos_in_messages,
)
from deerflow.agents.thread_state import merge_todos


def _ai_with_write_todos():
    return AIMessage(content="", tool_calls=[{"name": "write_todos", "id": "tc_1", "args": {}}])


def _reminder_msg():
    return HumanMessage(name="todo_reminder", content="reminder")


def _make_runtime():
    runtime = MagicMock()
    runtime.context = {"thread_id": "test-thread"}
    return runtime


def _sample_todos():
    return [
        {"status": "completed", "content": "Set up project"},
        {"status": "in_progress", "content": "Write tests"},
        {"status": "pending", "content": "Deploy"},
    ]


class TestTodosInMessages:
    def test_true_when_write_todos_present(self):
        msgs = [HumanMessage(content="hi"), _ai_with_write_todos()]
        assert _todos_in_messages(msgs) is True

    def test_false_when_no_write_todos(self):
        msgs = [
            HumanMessage(content="hi"),
            AIMessage(content="hello", tool_calls=[{"name": "bash", "id": "tc_1", "args": {}}]),
        ]
        assert _todos_in_messages(msgs) is False

    def test_false_for_empty_list(self):
        assert _todos_in_messages([]) is False

    def test_false_for_ai_without_tool_calls(self):
        msgs = [AIMessage(content="hello")]
        assert _todos_in_messages(msgs) is False


class TestReminderInMessages:
    def test_true_when_reminder_present(self):
        msgs = [HumanMessage(content="hi"), _reminder_msg()]
        assert _reminder_in_messages(msgs) is True

    def test_false_when_no_reminder(self):
        msgs = [HumanMessage(content="hi"), AIMessage(content="hello")]
        assert _reminder_in_messages(msgs) is False

    def test_false_for_empty_list(self):
        assert _reminder_in_messages([]) is False

    def test_false_for_human_without_name(self):
        msgs = [HumanMessage(content="todo_reminder")]
        assert _reminder_in_messages(msgs) is False


class TestFormatTodos:
    def test_formats_with_index(self):
        todos = _sample_todos()
        result = _format_todos(todos)
        assert "- [0] [completed] Set up project" in result
        assert "- [1] [in_progress] Write tests" in result
        assert "- [2] [pending] Deploy" in result

    def test_empty_list(self):
        assert _format_todos([]) == ""

    def test_missing_fields_use_defaults(self):
        todos = [{"content": "No status"}, {"status": "done"}]
        result = _format_todos(todos)
        assert "- [0] [pending] No status" in result
        assert "- [1] [done] " in result


class TestBeforeModel:
    def test_returns_none_when_no_todos(self):
        mw = TodoMiddleware()
        state = {"messages": [HumanMessage(content="hi")], "todos": []}
        assert mw.before_model(state, _make_runtime()) is None

    def test_returns_none_when_todos_is_none(self):
        mw = TodoMiddleware()
        state = {"messages": [HumanMessage(content="hi")], "todos": None}
        assert mw.before_model(state, _make_runtime()) is None

    def test_returns_none_when_write_todos_still_visible(self):
        mw = TodoMiddleware()
        state = {
            "messages": [_ai_with_write_todos()],
            "todos": _sample_todos(),
        }
        assert mw.before_model(state, _make_runtime()) is None

    def test_returns_none_when_reminder_already_present(self):
        mw = TodoMiddleware()
        state = {
            "messages": [HumanMessage(content="hi"), _reminder_msg()],
            "todos": _sample_todos(),
        }
        assert mw.before_model(state, _make_runtime()) is None

    def test_injects_reminder_when_todos_exist_but_truncated(self):
        mw = TodoMiddleware()
        state = {
            "messages": [HumanMessage(content="hi"), AIMessage(content="sure")],
            "todos": _sample_todos(),
        }
        result = mw.before_model(state, _make_runtime())
        assert result is not None
        msgs = result["messages"]
        assert len(msgs) == 1
        assert isinstance(msgs[0], HumanMessage)
        assert msgs[0].name == "todo_reminder"

    def test_reminder_contains_formatted_todos(self):
        mw = TodoMiddleware()
        state = {
            "messages": [HumanMessage(content="hi")],
            "todos": _sample_todos(),
        }
        result = mw.before_model(state, _make_runtime())
        content = result["messages"][0].content
        assert "Set up project" in content
        assert "Write tests" in content
        assert "Deploy" in content
        assert "system_reminder" in content


class TestAbeforeModel:
    def test_delegates_to_sync(self):
        mw = TodoMiddleware()
        state = {
            "messages": [HumanMessage(content="hi")],
            "todos": _sample_todos(),
        }
        result = asyncio.run(mw.abefore_model(state, _make_runtime()))
        assert result is not None
        assert result["messages"][0].name == "todo_reminder"


# ---------------------------------------------------------------------------
# merge_todos reducer — incremental operations
# ---------------------------------------------------------------------------


class TestMergeTodosFullReplace:
    def test_full_replace(self):
        existing = [{"content": "A", "status": "pending"}]
        new = [{"content": "B", "status": "in_progress"}]
        result = merge_todos(existing, new)
        assert result == [{"content": "B", "status": "in_progress"}]

    def test_full_replace_with_none_existing(self):
        new = [{"content": "A", "status": "pending"}]
        result = merge_todos(None, new)
        assert result == [{"content": "A", "status": "pending"}]

    def test_none_new_returns_existing(self):
        existing = [{"content": "A", "status": "pending"}]
        result = merge_todos(existing, None)
        assert result == existing

    def test_both_none_returns_empty(self):
        result = merge_todos(None, None)
        assert result == []


class TestMergeTodosUpdate:
    def test_update_status_by_index(self):
        existing = [
            {"content": "Task A", "status": "pending"},
            {"content": "Task B", "status": "pending"},
        ]
        new = {
            "_todo_ops": True,
            "updates": [{"index": 0, "status": "completed"}, {"index": 1, "status": "in_progress"}],
        }
        result = merge_todos(existing, new)
        assert result[0]["status"] == "completed"
        assert result[0]["content"] == "Task A"
        assert result[1]["status"] == "in_progress"
        assert result[1]["content"] == "Task B"
        assert len(result) == 2

    def test_update_content_by_index(self):
        existing = [{"content": "Old", "status": "pending"}]
        new = {"_todo_ops": True, "updates": [{"index": 0, "content": "New"}]}
        result = merge_todos(existing, new)
        assert result[0]["content"] == "New"
        assert result[0]["status"] == "pending"

    def test_update_skips_invalid_index(self):
        existing = [{"content": "A", "status": "pending"}]
        new = {"_todo_ops": True, "updates": [{"index": 5, "status": "completed"}]}
        result = merge_todos(existing, new)
        assert result == [{"content": "A", "status": "pending"}]

    def test_update_with_none_existing(self):
        new = {"_todo_ops": True, "updates": [{"index": 0, "status": "completed"}]}
        result = merge_todos(None, new)
        assert result == []


class TestMergeTodosRemove:
    def test_remove_by_index(self):
        existing = [
            {"content": "A", "status": "completed"},
            {"content": "B", "status": "in_progress"},
            {"content": "C", "status": "pending"},
        ]
        new = {"_todo_ops": True, "updates": [{"index": 1, "remove": True}]}
        result = merge_todos(existing, new)
        assert len(result) == 2
        assert result[0]["content"] == "A"
        assert result[1]["content"] == "C"

    def test_remove_multiple_descending_order(self):
        existing = [
            {"content": "A", "status": "pending"},
            {"content": "B", "status": "pending"},
            {"content": "C", "status": "pending"},
        ]
        new = {
            "_todo_ops": True,
            "updates": [{"index": 0, "remove": True}, {"index": 2, "remove": True}],
        }
        result = merge_todos(existing, new)
        assert len(result) == 1
        assert result[0]["content"] == "B"

    def test_update_and_remove_combined(self):
        existing = [
            {"content": "A", "status": "pending"},
            {"content": "B", "status": "pending"},
        ]
        new = {
            "_todo_ops": True,
            "updates": [{"index": 0, "status": "completed"}, {"index": 1, "remove": True}],
        }
        result = merge_todos(existing, new)
        assert len(result) == 1
        assert result[0]["content"] == "A"
        assert result[0]["status"] == "completed"


class TestMergeTodosAdd:
    def test_add_append_to_end(self):
        existing = [{"content": "A", "status": "pending"}]
        new = {"_todo_ops": True, "adds": [{"content": "B", "status": "pending"}]}
        result = merge_todos(existing, new)
        assert len(result) == 2
        assert result[1]["content"] == "B"

    def test_add_insert_at_position(self):
        existing = [
            {"content": "A", "status": "pending"},
            {"content": "C", "status": "pending"},
        ]
        new = {"_todo_ops": True, "adds": [{"content": "B", "status": "in_progress", "index": 1}]}
        result = merge_todos(existing, new)
        assert len(result) == 3
        assert result[0]["content"] == "A"
        assert result[1]["content"] == "B"
        assert result[1]["status"] == "in_progress"
        assert result[2]["content"] == "C"

    def test_add_insert_at_beginning(self):
        existing = [{"content": "B", "status": "pending"}]
        new = {"_todo_ops": True, "adds": [{"content": "A", "status": "pending", "index": 0}]}
        result = merge_todos(existing, new)
        assert result[0]["content"] == "A"
        assert result[1]["content"] == "B"

    def test_add_out_of_range_appends(self):
        existing = [{"content": "A", "status": "pending"}]
        new = {"_todo_ops": True, "adds": [{"content": "B", "status": "pending", "index": 99}]}
        result = merge_todos(existing, new)
        assert len(result) == 2
        assert result[1]["content"] == "B"

    def test_add_negative_index_appends(self):
        existing = [{"content": "A", "status": "pending"}]
        new = {"_todo_ops": True, "adds": [{"content": "B", "status": "pending", "index": -1}]}
        result = merge_todos(existing, new)
        assert len(result) == 2
        assert result[1]["content"] == "B"

    def test_add_defaults_to_pending(self):
        existing = []
        new = {"_todo_ops": True, "adds": [{"content": "Task"}]}
        result = merge_todos(existing, new)
        assert result[0]["status"] == "pending"

    def test_add_skips_empty_content(self):
        existing = [{"content": "A", "status": "pending"}]
        new = {"_todo_ops": True, "adds": [{"content": ""}]}
        result = merge_todos(existing, new)
        assert len(result) == 1


class TestMergeTodosCombined:
    def test_update_then_add(self):
        existing = [
            {"content": "A", "status": "pending"},
            {"content": "B", "status": "pending"},
        ]
        new = {
            "_todo_ops": True,
            "updates": [{"index": 0, "status": "completed"}],
            "adds": [{"content": "C", "status": "pending"}],
        }
        result = merge_todos(existing, new)
        assert len(result) == 3
        assert result[0]["status"] == "completed"
        assert result[2]["content"] == "C"

    def test_remove_then_add_at_same_position(self):
        existing = [
            {"content": "Old", "status": "pending"},
            {"content": "Keep", "status": "pending"},
        ]
        new = {
            "_todo_ops": True,
            "updates": [{"index": 0, "remove": True}],
            "adds": [{"content": "New", "status": "in_progress", "index": 0}],
        }
        result = merge_todos(existing, new)
        assert len(result) == 2
        assert result[0]["content"] == "New"
        assert result[1]["content"] == "Keep"
