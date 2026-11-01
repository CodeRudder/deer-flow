"""Tests for response-side base64 slimming (app.gateway.response_sanitizer).

Unit coverage for the sanitizer shapes (image_url parts, b64_json fields,
oversized base64 strings) plus endpoint-level tests that a run message
containing base64 comes back slimmed while the structure stays intact.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, MagicMock

from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.response_sanitizer import sanitize_message_list, sanitize_message_record, sanitize_value


def _b64(size: int) -> str:
    return base64.b64encode(b"a" * size).decode()


def _data_url(size: int) -> str:
    return f"data:image/png;base64,{_b64(size)}"


def _b64_len(size: int) -> int:
    """Length of the base64 payload produced for *size* raw bytes."""
    return len(_b64(size))


# ---------------------------------------------------------------------------
# Unit tests: sanitizer shapes
# ---------------------------------------------------------------------------


class TestImageUrlParts:
    def test_image_url_dict_part_replaced(self):
        msg = {"content": [{"type": "image_url", "image_url": {"url": _data_url(100)}}]}
        out = sanitize_message_record(msg)
        part = out["content"][0]
        assert part["type"] == "image_url_removed"
        assert part["note"] == "inline image omitted for performance"
        assert part["size_bytes"] == _b64_len(100)
        assert "base64" not in json.dumps(out)

    def test_image_url_string_part_replaced(self):
        url = _data_url(64)
        msg = {"content": [{"type": "image_url", "image_url": url}]}
        out = sanitize_message_record(msg)
        assert out["content"][0]["type"] == "image_url_removed"
        assert out["content"][0]["size_bytes"] == _b64_len(64)

    def test_http_image_url_untouched(self):
        msg = {"content": [{"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}]}
        out = sanitize_message_record(msg)
        assert out["content"][0] == msg["content"][0]

    def test_original_message_not_mutated(self):
        url = _data_url(64)
        msg = {"content": [{"type": "image_url", "image_url": {"url": url}}]}
        sanitize_message_record(msg)
        assert msg["content"][0]["image_url"]["url"] == url

    def test_non_image_content_untouched(self):
        msg = {"content": [{"type": "text", "text": "hello"}], "type": "ai"}
        out = sanitize_message_record(msg)
        assert out == msg


class TestB64Json:
    def test_b64_json_replaced_with_flag(self):
        msg = {"content": [{"type": "image_generation_result", "b64_json": _b64(100)}]}
        out = sanitize_message_record(msg)
        part = out["content"][0]
        assert "b64_json" not in part
        assert part["b64_json_omitted"] is True

    def test_non_string_b64_json_untouched(self):
        value = {"b64_json": None}
        assert sanitize_value(value) == value


class TestLargeStringTruncation:
    def test_data_url_inside_text_truncated(self):
        text = f"see this {_data_url(20_000)} thanks"
        out = sanitize_value({"text": text})
        assert "base64" not in out["text"].replace("[base64 omitted", "")
        assert out["text"].startswith("see this [base64 omitted ")
        assert out["text"].endswith(" KB] thanks")

    def test_bare_base64_run_truncated(self):
        out = sanitize_value({"blob": "prefix " + _b64(20_000) + " suffix"})
        assert out["blob"].startswith("prefix [base64 omitted ")
        assert out["blob"].endswith("KB] suffix")

    def test_small_strings_untouched(self):
        text = "short text with normal words, no truncation"
        assert sanitize_value({"text": text}) == {"text": text}

    def test_placeholder_reports_kb(self):
        out = sanitize_value({"blob": _b64(10_000)})
        assert f"[base64 omitted {_b64_len(10_000) // 1024} KB]" in out["blob"]


class TestNestedAndNoBase64:
    def test_nested_structures_sanitized(self):
        payload = {
            "messages": [{"content": [{"type": "image_url", "image_url": {"url": _data_url(64)}}]}],
            "metadata": {"extra": {"b64_json": _b64(64)}},
        }
        out = sanitize_value(payload)
        assert out["messages"][0]["content"][0]["type"] == "image_url_removed"
        assert out["metadata"]["extra"]["b64_json_omitted"] is True

    def test_plain_message_list_passthrough(self):
        messages = [{"content": "hello", "type": "human"}, {"content": [{"type": "text", "text": "hi"}], "type": "ai"}]
        assert sanitize_message_list(messages) == messages


# ---------------------------------------------------------------------------
# Endpoint-level tests
# ---------------------------------------------------------------------------


def _make_run_message_app(rows: list[dict], *, thread_rows: list[dict] | None = None):
    app = make_authed_test_app()
    from app.gateway.routers import thread_runs

    app.include_router(thread_runs.router)

    store = MagicMock()
    store.list_messages_by_run = AsyncMock(return_value=rows)
    store.list_messages = AsyncMock(return_value=thread_rows or [])
    app.state.run_event_store = store

    feedback_repo = MagicMock()
    feedback_repo.list_by_thread_grouped = AsyncMock(return_value={})
    app.state.feedback_repo = feedback_repo

    run_manager = MagicMock()
    run_manager.get = AsyncMock(return_value=None)
    run_manager.list_by_thread = AsyncMock(return_value=[])
    app.state.run_manager = run_manager
    return app


def _base64_run_message() -> dict:
    return {
        "run_id": "run-1",
        "seq": 1,
        "event_type": "llm.ai.response",
        "category": "message",
        "content": {
            "id": "msg-1",
            "type": "ai",
            "content": [
                {"type": "text", "text": "here is your image"},
                {"type": "image_url", "image_url": {"url": _data_url(5000)}},
                {"type": "tool", "b64_json": _b64(3000)},
            ],
        },
        "metadata": {},
        "created_at": "2026-01-01T00:00:00+00:00",
    }


def test_run_messages_endpoint_strips_base64():
    app = _make_run_message_app([_base64_run_message()])
    client = TestClient(app)
    resp = client.get("/api/threads/t1/runs/run-1/messages")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_more"] is False
    assert "base64," not in resp.text
    msg = body["data"][0]
    assert msg["content"]["id"] == "msg-1"
    parts = msg["content"]["content"]
    assert parts[0] == {"type": "text", "text": "here is your image"}
    assert parts[1]["type"] == "image_url_removed"
    assert parts[1]["size_bytes"] == _b64_len(5000)
    assert parts[2]["b64_json_omitted"] is True
    # Source row (persisted-shape data) is untouched.
    assert "base64," in _base64_run_message()["content"]["content"][1]["image_url"]["url"]


def test_thread_messages_endpoint_strips_base64():
    row = _base64_run_message()
    app = _make_run_message_app([], thread_rows=[row])
    client = TestClient(app)
    resp = client.get("/api/threads/t1/messages")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["content"]["content"][1]["type"] == "image_url_removed"
    assert '"b64_json"' not in json.dumps(body)
    assert "b64_json_omitted" in json.dumps(body)


def test_sse_format_strips_base64_from_values_snapshot():
    from app.gateway.services import format_sse

    snapshot = {"messages": [{"content": [{"type": "image_url", "image_url": {"url": _data_url(9000)}}]}]}
    frame = format_sse("values", snapshot)
    payload = json.loads(frame.split("data: ", 1)[1].split("\n")[0])
    part = payload["messages"][0]["content"][0]
    assert part["type"] == "image_url_removed"
    assert part["size_bytes"] == _b64_len(9000)
    assert "AAAA" not in frame
