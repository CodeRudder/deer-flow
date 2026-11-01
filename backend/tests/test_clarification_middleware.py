"""Tests for ClarificationMiddleware, focusing on options type coercion."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph.message import add_messages
from langgraph.types import Command

from deerflow.agents.middlewares.clarification_middleware import (
    MAX_FORM_FIELDS,
    ClarificationMiddleware,
)
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal


@pytest.fixture
def middleware():
    return ClarificationMiddleware()


class TestFormatClarificationMessage:
    """Tests for _format_clarification_message options handling."""

    def test_options_as_native_list(self, middleware):
        """Normal case: options is already a list."""
        args = {
            "question": "Which env?",
            "clarification_type": "approach_choice",
            "options": ["dev", "staging", "prod"],
        }
        result = middleware._format_clarification_message(args)
        assert "1. dev" in result
        assert "2. staging" in result
        assert "3. prod" in result

    def test_options_as_json_string(self, middleware):
        """Bug case (#1995): model serializes options as a JSON string."""
        args = {
            "question": "Which env?",
            "clarification_type": "approach_choice",
            "options": json.dumps(["dev", "staging", "prod"]),
        }
        result = middleware._format_clarification_message(args)
        assert "1. dev" in result
        assert "2. staging" in result
        assert "3. prod" in result
        # Must NOT contain per-character output
        assert "1. [" not in result
        assert '2. "' not in result

    def test_options_as_json_string_scalar(self, middleware):
        """JSON string decoding to a non-list scalar is treated as one option."""
        args = {
            "question": "Which env?",
            "clarification_type": "approach_choice",
            "options": json.dumps("development"),
        }
        result = middleware._format_clarification_message(args)
        assert "1. development" in result
        # Must be a single option, not per-character iteration.
        assert "2." not in result

    def test_options_as_plain_string(self, middleware):
        """Edge case: options is a non-JSON string, treated as single option."""
        args = {
            "question": "Which env?",
            "clarification_type": "approach_choice",
            "options": "just one option",
        }
        result = middleware._format_clarification_message(args)
        assert "1. just one option" in result

    def test_options_none(self, middleware):
        """Options is None — no options section rendered."""
        args = {
            "question": "Tell me more",
            "clarification_type": "missing_info",
            "options": None,
        }
        result = middleware._format_clarification_message(args)
        assert "1." not in result

    def test_options_empty_list(self, middleware):
        """Options is an empty list — no options section rendered."""
        args = {
            "question": "Tell me more",
            "clarification_type": "missing_info",
            "options": [],
        }
        result = middleware._format_clarification_message(args)
        assert "1." not in result

    def test_options_missing(self, middleware):
        """Options key is absent — defaults to empty list."""
        args = {
            "question": "Tell me more",
            "clarification_type": "missing_info",
        }
        result = middleware._format_clarification_message(args)
        assert "1." not in result

    def test_context_included(self, middleware):
        """Context is rendered before the question."""
        args = {
            "question": "Which env?",
            "clarification_type": "approach_choice",
            "context": "Need target env for config",
            "options": ["dev", "prod"],
        }
        result = middleware._format_clarification_message(args)
        assert "Need target env for config" in result
        assert "Which env?" in result
        assert "1. dev" in result

    def test_json_string_with_mixed_types(self, middleware):
        """JSON string containing non-string elements still works."""
        args = {
            "question": "Pick one",
            "clarification_type": "approach_choice",
            "options": json.dumps(["Option A", 2, True, None]),
        }
        result = middleware._format_clarification_message(args)
        assert "1. Option A" in result
        assert "2. 2" in result
        assert "3. True" in result
        assert "4. None" in result


class TestClarificationCommandIdempotency:
    """Clarification tool-call retries should not duplicate messages in state."""

    def test_repeated_tool_call_uses_stable_message_id(self, middleware):
        request = SimpleNamespace(
            tool_call={
                "name": "ask_clarification",
                "id": "call-clarify-1",
                "args": {
                    "question": "Which environment should I use?",
                    "clarification_type": "approach_choice",
                    "options": ["dev", "prod"],
                },
            }
        )

        first = middleware.wrap_tool_call(request, lambda _req: pytest.fail("handler should not be called"))
        second = middleware.wrap_tool_call(request, lambda _req: pytest.fail("handler should not be called"))

        first_message = first.update["messages"][0]
        second_message = second.update["messages"][0]

        assert first_message.id == "clarification:call-clarify-1"
        assert second_message.id == first_message.id
        assert second_message.tool_call_id == first_message.tool_call_id

        merged = add_messages(add_messages([], [first_message]), [second_message])

        assert len(merged) == 1
        assert merged[0].id == "clarification:call-clarify-1"
        assert merged[0].content == first_message.content

    def test_missing_tool_call_id_still_gets_stable_message_id(self, middleware):
        request = SimpleNamespace(
            tool_call={
                "name": "ask_clarification",
                "args": {
                    "question": "Which environment should I use?",
                    "clarification_type": "missing_info",
                },
            }
        )

        first = middleware.wrap_tool_call(request, lambda _req: pytest.fail("handler should not be called"))
        second = middleware.wrap_tool_call(request, lambda _req: pytest.fail("handler should not be called"))

        first_message = first.update["messages"][0]
        second_message = second.update["messages"][0]

        assert first_message.id.startswith("clarification:")
        assert second_message.id == first_message.id

        merged = add_messages(add_messages([], [first_message]), [second_message])

        assert len(merged) == 1


class TestClarificationJournalRecording:
    def test_records_clarification_tool_message_to_run_journal(self, middleware):
        store = MemoryRunEventStore()
        journal = RunJournal("run-1", "thread-1", store, flush_threshold=100)
        request = SimpleNamespace(
            tool_call={
                "name": "ask_clarification",
                "id": "call-clarify-1",
                "args": {
                    "question": "Which environment should I use?",
                    "clarification_type": "approach_choice",
                    "context": "Need target env for config",
                    "options": ["dev", "prod"],
                },
            },
            runtime=SimpleNamespace(context={"__run_journal": journal}),
        )

        command = middleware.wrap_tool_call(request, lambda _req: pytest.fail("handler should not be called"))

        assert command.goto == "__end__"
        tool_message = command.update["messages"][0]
        assert tool_message.name == "ask_clarification"

        asyncio.run(journal.flush())
        messages = asyncio.run(store.list_messages("thread-1"))

        assert len(messages) == 1
        assert messages[0]["event_type"] == "llm.tool.result"
        assert messages[0]["category"] == "message"
        assert messages[0]["content"]["type"] == "tool"
        assert messages[0]["content"]["name"] == "ask_clarification"
        assert messages[0]["content"]["tool_call_id"] == "call-clarify-1"
        assert messages[0]["content"]["id"] == "clarification:call-clarify-1"
        assert "Which environment should I use?" in messages[0]["content"]["content"]

    def test_missing_run_journal_still_returns_clarification_command(self, middleware):
        request = SimpleNamespace(
            tool_call={
                "name": "ask_clarification",
                "id": "call-clarify-1",
                "args": {
                    "question": "Which environment should I use?",
                    "clarification_type": "missing_info",
                },
            },
            runtime=SimpleNamespace(context={}),
        )

        command = middleware.wrap_tool_call(request, lambda _req: pytest.fail("handler should not be called"))

        assert command.goto == "__end__"
        assert command.update["messages"][0].name == "ask_clarification"


class TestHumanInputPayload:
    """Tests for _build_human_input_payload mode selection and wire shape."""

    def test_free_text_mode_without_options_or_fields(self, middleware):
        payload = middleware._build_human_input_payload(
            {"question": "What is the project name?"},
            tool_call_id="call-1",
            request_id="clarification:call-1",
        )
        assert payload["version"] == 1
        assert payload["kind"] == "human_input_request"
        assert payload["source"] == "ask_clarification"
        assert payload["request_id"] == "clarification:call-1"
        assert payload["tool_call_id"] == "call-1"
        assert payload["input_mode"] == "free_text"
        assert payload["question"] == "What is the project name?"
        assert "options" not in payload
        assert "fields" not in payload

    def test_choice_with_other_mode_with_options(self, middleware):
        payload = middleware._build_human_input_payload(
            {"question": "Which env?", "options": ["dev", "staging"]},
            tool_call_id="call-1",
            request_id="clarification:call-1",
        )
        assert payload["version"] == 1
        assert payload["input_mode"] == "choice_with_other"
        assert payload["options"] == [
            {"id": "option-1", "label": "dev", "value": "dev"},
            {"id": "option-2", "label": "staging", "value": "staging"},
        ]

    def test_form_mode_with_fields_is_version_2(self, middleware):
        fields_arg = [{"name": "env", "label": "Env", "type": "select", "required": True, "options": ["dev", "prod"]}]
        fields = middleware._normalize_fields(fields_arg)
        payload = middleware._build_human_input_payload(
            {"question": "Configure deploy", "fields": fields_arg},
            tool_call_id="call-1",
            request_id="clarification:call-1",
        )
        assert payload["version"] == 2
        assert payload["input_mode"] == "form"
        assert payload["fields"] == fields
        assert "options" not in payload

    def test_fields_take_precedence_over_options(self, middleware):
        payload = middleware._build_human_input_payload(
            {"question": "Q", "options": ["a"], "fields": [{"name": "x", "type": "text"}]},
            tool_call_id="call-1",
            request_id="clarification:call-1",
        )
        assert payload["input_mode"] == "form"
        assert "options" not in payload

    def test_context_none_is_preserved(self, middleware):
        payload = middleware._build_human_input_payload(
            {"question": "Q", "context": None},
            tool_call_id="call-1",
            request_id="clarification:call-1",
        )
        assert payload["context"] is None

    def test_clarification_type_non_string_is_coerced(self, middleware):
        payload = middleware._build_human_input_payload(
            {"question": "Q", "clarification_type": []},
            tool_call_id="call-1",
            request_id="clarification:call-1",
        )
        assert payload["clarification_type"] == "[]"

    def test_handle_clarification_attaches_artifact(self, middleware):
        request = SimpleNamespace(
            tool_call={
                "name": "ask_clarification",
                "id": "call-abc",
                "args": {"question": "Which env?", "clarification_type": "approach_choice", "options": ["dev", "prod"]},
            },
            runtime=SimpleNamespace(context={}),
        )
        command = middleware.wrap_tool_call(request, lambda _req: pytest.fail("handler should not be called"))
        tool_message = command.update["messages"][0]
        assert tool_message.artifact["human_input"]["kind"] == "human_input_request"
        assert tool_message.artifact["human_input"]["request_id"] == "clarification:call-abc"
        assert tool_message.artifact["human_input"]["input_mode"] == "choice_with_other"
        assert "Which env?" in tool_message.content


class TestFormPayload:
    """Atomic degradation for structurally broken forms; benign local degradation."""

    def _fields_arg(self, fields):
        return {"question": "Q", "fields": fields}

    def test_valid_multi_field_form(self, middleware):
        fields = middleware._normalize_fields(
            [
                {"name": "env", "type": "select", "options": ["dev", "prod"], "required": True},
                {"name": "notes", "type": "textarea", "placeholder": "Anything else"},
                {"name": "count", "type": "number"},
                {"name": "flags", "type": "multi_select", "options": ["a", "b"]},
                {"name": "ok", "type": "checkbox"},
                {"name": "when", "type": "date"},
            ]
        )
        assert [f["type"] for f in fields] == ["select", "textarea", "number", "multi_select", "checkbox", "date"]
        assert fields[0]["options"][0]["id"] == "env-option-1"
        assert fields[0]["required"] is True
        assert fields[1]["placeholder"] == "Anything else"

    def test_non_dict_entry_invalidates_whole_form(self, middleware):
        assert middleware._normalize_fields([{"name": "a"}, "not-a-dict"]) == []

    def test_missing_name_invalidates_whole_form(self, middleware):
        assert middleware._normalize_fields([{"type": "text"}]) == []

    def test_duplicate_name_invalidates_whole_form(self, middleware):
        assert middleware._normalize_fields([{"name": "a"}, {"name": "a"}]) == []

    def test_reserved_name_invalidates_whole_form(self, middleware):
        assert middleware._normalize_fields([{"name": "__proto__"}]) == []
        assert middleware._normalize_fields([{"name": "constructor"}]) == []

    def test_over_field_cap_invalidates_whole_form(self, middleware):
        fields = [{"name": f"f{i}", "type": "text"} for i in range(MAX_FORM_FIELDS + 1)]
        assert middleware._normalize_fields(fields) == []
        assert len(middleware._normalize_fields(fields[:MAX_FORM_FIELDS])) == MAX_FORM_FIELDS

    def test_over_option_cap_invalidates_whole_form(self, middleware):
        fields = [{"name": "a", "type": "select", "options": [f"o{i}" for i in range(25)]}]
        assert middleware._normalize_fields(fields) == []

    def test_oversized_name_invalidates_whole_form(self, middleware):
        assert middleware._normalize_fields([{"name": "x" * 201}]) == []

    def test_oversized_placeholder_invalidates_whole_form(self, middleware):
        assert middleware._normalize_fields([{"name": "a", "placeholder": "p" * 201}]) == []

    def test_fields_as_broken_json_string_degrades_to_empty(self, middleware):
        assert middleware._normalize_fields("{not json") == []

    def test_fields_as_non_list_degrades_to_empty(self, middleware):
        assert middleware._normalize_fields({"name": "a"}) == []

    def test_unknown_type_degrades_locally_to_text(self, middleware):
        fields = middleware._normalize_fields([{"name": "a", "type": "wibble"}])
        assert fields[0]["type"] == "text"

    def test_unhashable_type_degrades_locally_to_text(self, middleware):
        fields = middleware._normalize_fields([{"name": "a", "type": ["text"]}])
        assert fields[0]["type"] == "text"

    def test_select_without_options_degrades_locally_to_text(self, middleware):
        fields = middleware._normalize_fields([{"name": "a", "type": "select"}])
        assert fields[0]["type"] == "text"
        assert "options" not in fields[0]

    def test_select_options_get_stable_ids(self, middleware):
        fields = middleware._normalize_fields([{"name": "env", "type": "select", "options": ["dev", "prod"]}])
        assert fields[0]["options"] == [
            {"id": "env-option-1", "label": "dev", "value": "dev"},
            {"id": "env-option-2", "label": "prod", "value": "prod"},
        ]

    def test_required_string_true_is_coerced(self, middleware):
        fields = middleware._normalize_fields([{"name": "a", "required": "true"}])
        assert fields[0]["required"] is True
        fields = middleware._normalize_fields([{"name": "a", "required": "nope"}])
        assert fields[0]["required"] is False

    def test_label_defaults_to_name(self, middleware):
        fields = middleware._normalize_fields([{"name": "a"}])
        assert fields[0]["label"] == "a"

    def test_broken_form_degrades_payload_to_free_text(self, middleware):
        payload = middleware._build_human_input_payload(
            self._fields_arg([{"name": "__proto__", "type": "text"}]),
            tool_call_id="call-1",
            request_id="clarification:call-1",
        )
        assert payload["input_mode"] == "free_text"
        assert payload["version"] == 1
        assert "fields" not in payload

    def test_form_text_fallback_lists_fields(self, middleware):
        message = middleware._format_clarification_message(
            self._fields_arg(
                [
                    {"name": "env", "label": "Env", "type": "select", "required": True, "options": ["dev", "prod"]},
                    {"name": "flags", "label": "Flags", "type": "multi_select", "options": ["a", "b"]},
                ]
            )
        )
        assert "1. Env (required) — options: dev / prod" in message
        assert "2. Flags — options: a / b (multiple allowed)" in message
        assert "Please reply with a value for each field." in message


class TestClarificationToolSchema:
    """The tool schema must expose `fields` so models can discover it."""

    def test_schema_has_fields_param(self):
        from deerflow.tools.builtins.clarification_tool import ask_clarification_tool

        properties = ask_clarification_tool.args_schema.model_json_schema()["properties"]
        assert "fields" in properties
        assert "options" in properties


class TestClarificationDisabled:
    """disable_clarification runs turn the interrupt into a proceed ToolMessage."""

    def _request(self, middleware=None, context=None):
        return SimpleNamespace(
            tool_call={
                "name": "ask_clarification",
                "id": "call-1",
                "args": {"question": "Which env?", "options": ["dev", "prod"]},
            },
            runtime=SimpleNamespace(context=context or {}),
        )

    def test_enabled_by_default_interrupts(self, middleware):
        result = middleware.wrap_tool_call(self._request(), lambda _req: pytest.fail("handler should not be called"))
        assert isinstance(result, Command)
        assert result.goto == "__end__"

    def test_disabled_returns_proceed_tool_message(self, middleware):
        result = middleware.wrap_tool_call(self._request(context={"disable_clarification": True}), lambda _req: pytest.fail("handler should not be called"))
        assert isinstance(result, ToolMessage)
        assert result.tool_call_id == "call-1"
        assert "Proceed with your best judgment" in result.content

    def test_disabled_string_value_is_falsy(self, middleware):
        result = middleware.wrap_tool_call(self._request(context={"disable_clarification": "false"}), lambda _req: pytest.fail("handler should not be called"))
        assert isinstance(result, Command)


class TestDropParallelSiblingTools:
    """after_model drops sibling tool calls so the turn can interrupt cleanly."""

    def _ai_message(self, tool_calls=None, invalid_tool_calls=None, content=None):
        kwargs = {"content": content if content is not None else "thinking"}
        if tool_calls is not None:
            kwargs["tool_calls"] = tool_calls
        message = AIMessage(**kwargs)
        if invalid_tool_calls is not None:
            message.invalid_tool_calls = invalid_tool_calls
        return message

    def test_mixed_batch_drops_siblings(self, middleware):
        state = {
            "messages": [
                self._ai_message(
                    tool_calls=[
                        {"name": "ask_clarification", "id": "call-1", "args": {}},
                        {"name": "bash", "id": "call-2", "args": {"command": "rm -rf /"}},
                    ]
                )
            ]
        }
        result = middleware.after_model(state, SimpleNamespace(context={}))
        assert result is not None
        patched = result["messages"][0]
        assert [tc["name"] for tc in patched.tool_calls] == ["ask_clarification"]

    def test_clarification_only_batch_is_untouched(self, middleware):
        state = {
            "messages": [
                self._ai_message(
                    tool_calls=[
                        {"name": "ask_clarification", "id": "call-1", "args": {}},
                    ]
                )
            ]
        }
        assert middleware.after_model(state, SimpleNamespace(context={})) is None

    def test_no_clarification_call_is_untouched(self, middleware):
        state = {
            "messages": [
                self._ai_message(
                    tool_calls=[
                        {"name": "bash", "id": "call-2", "args": {}},
                    ]
                )
            ]
        }
        assert middleware.after_model(state, SimpleNamespace(context={})) is None

    def test_invalid_clarification_call_also_drops_siblings(self, middleware):
        state = {
            "messages": [
                self._ai_message(
                    tool_calls=[{"name": "bash", "id": "call-2", "args": {}}],
                    invalid_tool_calls=[
                        {"name": "ask_clarification", "id": "call-1", "args": "{bad json", "error": "invalid"},
                    ],
                )
            ]
        }
        result = middleware.after_model(state, SimpleNamespace(context={}))
        assert result is not None
        patched = result["messages"][0]
        assert patched.tool_calls == []

    def test_disable_clarification_keeps_siblings(self, middleware):
        state = {
            "messages": [
                self._ai_message(
                    tool_calls=[
                        {"name": "ask_clarification", "id": "call-1", "args": {}},
                        {"name": "bash", "id": "call-2", "args": {}},
                    ]
                )
            ]
        }
        assert middleware.after_model(state, SimpleNamespace(context={"disable_clarification": True})) is None

    def test_provider_tool_use_content_blocks_are_filtered(self, middleware):
        content = [
            {"type": "text", "text": "let me ask"},
            {"type": "tool_use", "id": "call-1", "name": "ask_clarification", "input": {}},
            {"type": "tool_use", "id": "call-2", "name": "bash", "input": {}},
        ]
        state = {
            "messages": [
                self._ai_message(
                    tool_calls=[
                        {"name": "ask_clarification", "id": "call-1", "args": {}},
                        {"name": "bash", "id": "call-2", "args": {}},
                    ],
                    content=content,
                )
            ]
        }
        result = middleware.after_model(state, SimpleNamespace(context={}))
        patched = result["messages"][0]
        remaining = [block for block in patched.content if isinstance(block, dict) and block.get("type") == "tool_use"]
        assert [block["id"] for block in remaining] == ["call-1"]

    def test_empty_state_is_untouched(self, middleware):
        assert middleware.after_model({"messages": []}, SimpleNamespace(context={})) is None
        assert middleware.after_model({}, SimpleNamespace(context={})) is None
