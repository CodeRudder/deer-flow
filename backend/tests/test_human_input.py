"""Tests for structured human-input response metadata helpers."""

from deerflow.utils.human_input import read_human_input_response


def test_read_requires_non_empty_value():
    kwargs = {
        "human_input_response": {
            "version": 1,
            "kind": "human_input_response",
            "source": "ask_clarification",
            "request_id": "clarification:call-1",
            "response_kind": "text",
            "value": "   ",
        }
    }
    assert read_human_input_response(kwargs) is None


def test_read_preserves_text_response():
    kwargs = {
        "human_input_response": {
            "version": 1,
            "kind": "human_input_response",
            "source": "ask_clarification",
            "request_id": "clarification:call-1",
            "response_kind": "text",
            "value": "staging, watch out for the new db password",
        }
    }
    parsed = read_human_input_response(kwargs)
    assert parsed == {
        "version": 1,
        "kind": "human_input_response",
        "source": "ask_clarification",
        "request_id": "clarification:call-1",
        "response_kind": "text",
        "value": "staging, watch out for the new db password",
    }


def test_read_preserves_option_response():
    kwargs = {
        "human_input_response": {
            "version": 1,
            "kind": "human_input_response",
            "source": "ask_clarification",
            "request_id": "clarification:call-1",
            "response_kind": "option",
            "option_id": "option-2",
            "value": "staging",
        }
    }
    parsed = read_human_input_response(kwargs)
    assert parsed is not None
    assert parsed["response_kind"] == "option"
    assert parsed["option_id"] == "option-2"


def test_option_response_requires_option_id():
    kwargs = {
        "human_input_response": {
            "version": 1,
            "kind": "human_input_response",
            "source": "ask_clarification",
            "request_id": "clarification:call-1",
            "response_kind": "option",
            "value": "staging",
        }
    }
    assert read_human_input_response(kwargs) is None


def test_read_rejects_unknown_kind():
    kwargs = {
        "human_input_response": {
            "version": 1,
            "kind": "human_input_response",
            "source": "ask_clarification",
            "request_id": "clarification:call-1",
            "response_kind": "form",
            "value": "x",
        }
    }
    assert read_human_input_response(kwargs) is None


def test_read_rejects_missing_required_fields():
    base = {
        "version": 1,
        "kind": "human_input_response",
        "source": "ask_clarification",
        "request_id": "clarification:call-1",
        "response_kind": "text",
        "value": "x",
    }
    for key in ("source", "request_id", "value"):
        broken = {k: v for k, v in base.items() if k != key}
        assert read_human_input_response({"human_input_response": broken}) is None
    assert read_human_input_response({"human_input_response": {**base, "version": 2}}) is None
    assert read_human_input_response({"human_input_response": {**base, "kind": "something_else"}}) is None


def test_read_rejects_non_mapping_payload():
    assert read_human_input_response({"human_input_response": "text"}) is None
    assert read_human_input_response({"human_input_response": None}) is None
    assert read_human_input_response(None) is None
    assert read_human_input_response({}) is None
