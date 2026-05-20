"""Tests for TodoMiddleware context-loss detection and incremental operations."""

import asyncio
import json
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.middlewares.todo_middleware import (
    TodoMiddleware,
    _coerce_json_list,
    _format_todos,
    _reminder_in_messages,
    _todos_in_messages,
)
from deerflow.agents.thread_state import apply_todo_ops


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


class TestCoerceJsonList:
    def test_accepts_native_list(self):
        value, error = _coerce_json_list([{"content": "A"}], "todos")
        assert value == [{"content": "A"}]
        assert error is None

    def test_parses_json_string_list(self):
        value, error = _coerce_json_list('[{"content": "A", "status": "pending"}]', "todos")
        assert value == [{"content": "A", "status": "pending"}]
        assert error is None

    def test_rejects_json_string_object(self):
        value, error = _coerce_json_list('{"content": "A"}', "todos")
        assert value is None
        assert "JSON must decode to a list" in error

    def test_rejects_invalid_json_string(self):
        value, error = _coerce_json_list("[bad json", "todos")
        assert value is None
        assert "Failed to parse string input" in error

    def test_rejects_json_string_with_non_dict_items(self):
        value, error = _coerce_json_list('["a", "b", "c"]', "todos")
        assert value is None
        assert "items must be objects" in error

    def test_rejects_native_list_with_non_dict_items(self):
        value, error = _coerce_json_list(["a", "b", "c"], "todos")
        assert value is None
        assert "items must be objects" in error


class TestWriteTodosTool:
    def test_write_todos_schema_does_not_advertise_string_inputs(self):
        mw = TodoMiddleware()
        schema = mw.tools[0].args_schema.model_json_schema()
        schema_json = json.dumps(
            {key: schema["properties"][key] for key in ("todos", "updates", "adds")},
            ensure_ascii=False,
        )

        assert '"type": "string"' not in schema_json
        assert '"type": "array"' in schema_json

    def test_write_todos_accepts_stringified_todos(self):
        mw = TodoMiddleware()
        tool = mw.tools[0]

        result = tool.func(
            todos='[{"content": "Task A", "status": "in_progress"}, {"content": "Task B", "status": "pending"}]',
            tool_call_id="tc-string",
        )

        assert result.update["todos"] == [
            {"content": "Task A", "status": "in_progress"},
            {"content": "Task B", "status": "pending"},
        ]
        assert result.update["messages"][0].tool_call_id == "tc-string"

    def test_write_todos_returns_error_for_invalid_stringified_todos(self):
        mw = TodoMiddleware()
        tool = mw.tools[0]

        result = tool.func(todos="[bad json", tool_call_id="tc-bad")

        msg = result.update["messages"][0]
        assert msg.status == "error"
        assert msg.tool_call_id == "tc-bad"
        assert "'todos' must be a list or a JSON array string" in msg.content

    def test_write_todos_accepts_stringified_updates(self):
        mw = TodoMiddleware()
        tool = mw.tools[0]

        result = tool.func(
            updates='[{"index": 0, "status": "completed"}]',
            state={"todos": [{"content": "Task A", "status": "in_progress"}]},
            tool_call_id="tc-updates",
        )

        assert result.update["todos"] == [{"content": "Task A", "status": "completed"}]


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
# apply_todo_ops — incremental operations
# ---------------------------------------------------------------------------


class TestApplyTodoOpsBasic:
    def test_no_ops_returns_existing(self):
        existing = [{"content": "A", "status": "pending"}]
        result = apply_todo_ops(existing, None, None)
        assert result == existing

    def test_none_existing_returns_empty(self):
        result = apply_todo_ops(None, None, None)
        assert result == []

    def test_does_not_mutate_existing(self):
        existing = [{"content": "A", "status": "pending"}]
        result = apply_todo_ops(existing, [{"index": 0, "status": "completed"}], None)
        assert existing[0]["status"] == "pending"  # Original unchanged
        assert result[0]["status"] == "completed"


class TestApplyTodoOpsUpdate:
    def test_update_status_by_index(self):
        existing = [
            {"content": "Task A", "status": "pending"},
            {"content": "Task B", "status": "pending"},
        ]
        result = apply_todo_ops(
            existing,
            [
                {"index": 0, "status": "completed"},
                {"index": 1, "status": "in_progress"},
            ],
            None,
        )
        assert result[0]["status"] == "completed"
        assert result[0]["content"] == "Task A"
        assert result[1]["status"] == "in_progress"
        assert result[1]["content"] == "Task B"
        assert len(result) == 2

    def test_update_content_by_index(self):
        existing = [{"content": "Old", "status": "pending"}]
        result = apply_todo_ops(existing, [{"index": 0, "content": "New"}], None)
        assert result[0]["content"] == "New"
        assert result[0]["status"] == "pending"

    def test_update_skips_invalid_index(self):
        existing = [{"content": "A", "status": "pending"}]
        result = apply_todo_ops(existing, [{"index": 5, "status": "completed"}], None)
        assert result == [{"content": "A", "status": "pending"}]

    def test_update_with_none_existing(self):
        result = apply_todo_ops(None, [{"index": 0, "status": "completed"}], None)
        assert result == []


class TestApplyTodoOpsRemove:
    def test_remove_by_index(self):
        existing = [
            {"content": "A", "status": "completed"},
            {"content": "B", "status": "in_progress"},
            {"content": "C", "status": "pending"},
        ]
        result = apply_todo_ops(existing, [{"index": 1, "remove": True}], None)
        assert len(result) == 2
        assert result[0]["content"] == "A"
        assert result[1]["content"] == "C"

    def test_remove_multiple_descending_order(self):
        existing = [
            {"content": "A", "status": "pending"},
            {"content": "B", "status": "pending"},
            {"content": "C", "status": "pending"},
        ]
        result = apply_todo_ops(
            existing,
            [
                {"index": 0, "remove": True},
                {"index": 2, "remove": True},
            ],
            None,
        )
        assert len(result) == 1
        assert result[0]["content"] == "B"

    def test_update_and_remove_combined(self):
        existing = [
            {"content": "A", "status": "pending"},
            {"content": "B", "status": "pending"},
        ]
        result = apply_todo_ops(
            existing,
            [
                {"index": 0, "status": "completed"},
                {"index": 1, "remove": True},
            ],
            None,
        )
        assert len(result) == 1
        assert result[0]["content"] == "A"
        assert result[0]["status"] == "completed"


class TestApplyTodoOpsAdd:
    def test_add_append_to_end(self):
        existing = [{"content": "A", "status": "pending"}]
        result = apply_todo_ops(existing, None, [{"content": "B", "status": "pending"}])
        assert len(result) == 2
        assert result[1]["content"] == "B"

    def test_add_insert_at_position(self):
        existing = [
            {"content": "A", "status": "pending"},
            {"content": "C", "status": "pending"},
        ]
        result = apply_todo_ops(existing, None, [{"content": "B", "status": "in_progress", "index": 1}])
        assert len(result) == 3
        assert result[0]["content"] == "A"
        assert result[1]["content"] == "B"
        assert result[1]["status"] == "in_progress"
        assert result[2]["content"] == "C"

    def test_add_insert_at_beginning(self):
        existing = [{"content": "B", "status": "pending"}]
        result = apply_todo_ops(existing, None, [{"content": "A", "status": "pending", "index": 0}])
        assert result[0]["content"] == "A"
        assert result[1]["content"] == "B"

    def test_add_out_of_range_appends(self):
        existing = [{"content": "A", "status": "pending"}]
        result = apply_todo_ops(existing, None, [{"content": "B", "status": "pending", "index": 99}])
        assert len(result) == 2
        assert result[1]["content"] == "B"

    def test_add_negative_index_appends(self):
        existing = [{"content": "A", "status": "pending"}]
        result = apply_todo_ops(existing, None, [{"content": "B", "status": "pending", "index": -1}])
        assert len(result) == 2
        assert result[1]["content"] == "B"

    def test_add_defaults_to_pending(self):
        result = apply_todo_ops([], None, [{"content": "Task"}])
        assert result[0]["status"] == "pending"

    def test_add_skips_empty_content(self):
        existing = [{"content": "A", "status": "pending"}]
        result = apply_todo_ops(existing, None, [{"content": ""}])
        assert len(result) == 1


class TestApplyTodoOpsCombined:
    def test_update_then_add(self):
        existing = [
            {"content": "A", "status": "pending"},
            {"content": "B", "status": "pending"},
        ]
        result = apply_todo_ops(
            existing,
            [{"index": 0, "status": "completed"}],
            [{"content": "C", "status": "pending"}],
        )
        assert len(result) == 3
        assert result[0]["status"] == "completed"
        assert result[2]["content"] == "C"

    def test_remove_then_add_at_same_position(self):
        existing = [
            {"content": "Old", "status": "pending"},
            {"content": "Keep", "status": "pending"},
        ]
        result = apply_todo_ops(
            existing,
            [{"index": 0, "remove": True}],
            [{"content": "New", "status": "in_progress", "index": 0}],
        )
        assert len(result) == 2
        assert result[0]["content"] == "New"
        assert result[1]["content"] == "Keep"
