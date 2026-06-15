"""Tests for ClarificationMiddleware, focusing on options type coercion."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from langgraph.graph.message import add_messages

from deerflow.agents.middlewares.clarification_middleware import ClarificationMiddleware
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
