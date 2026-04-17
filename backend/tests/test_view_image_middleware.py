"""Tests for ViewImageMiddleware — image injection and viewed_images cleanup."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.agents.middlewares.view_image_middleware import ViewImageMiddleware

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

IMG_A = {"base64": "aaaa", "mime_type": "image/png"}
IMG_B = {"base64": "bbbb", "mime_type": "image/jpeg"}


def _make_tool_call(name: str, call_id: str = "tc1") -> dict:
    return {"name": name, "id": call_id, "args": {}}


def _runtime() -> MagicMock:
    return MagicMock()


def _state(
    messages: list | None = None,
    viewed_images: dict | None = None,
) -> dict:
    return {
        "messages": messages or [],
        "viewed_images": viewed_images or {},
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInjectAndClear:
    """Verify that injection includes viewed_images AND clears the state."""

    def test_inject_returns_empty_viewed_images(self):
        """After injection, viewed_images should be cleared to {}."""
        mw = ViewImageMiddleware()
        messages = [
            AIMessage(
                content="",
                tool_calls=[_make_tool_call("view_image", "tc1")],
            ),
            ToolMessage(content="OK", tool_call_id="tc1"),
        ]
        state = _state(messages=messages, viewed_images={"/path/a.png": IMG_A})

        result = mw.before_model(state, _runtime())

        assert result is not None
        assert result["viewed_images"] == {}

    def test_no_injection_no_clear(self):
        """When no injection happens, viewed_images should not be touched."""
        mw = ViewImageMiddleware()
        messages = [
            AIMessage(content="no tools here"),
        ]
        state = _state(messages=messages, viewed_images={"/path/a.png": IMG_A})

        result = mw.before_model(state, _runtime())

        assert result is None

    def test_inject_contains_all_current_images(self):
        """Injected message should contain all images from viewed_images."""
        mw = ViewImageMiddleware()
        messages = [
            AIMessage(
                content="",
                tool_calls=[_make_tool_call("view_image", "tc1")],
            ),
            ToolMessage(content="OK", tool_call_id="tc1"),
        ]
        state = _state(
            messages=messages,
            viewed_images={"/path/a.png": IMG_A, "/path/b.jpg": IMG_B},
        )

        result = mw.before_model(state, _runtime())

        assert result is not None
        msg = result["messages"][0]
        assert isinstance(msg, HumanMessage)
        # Should contain both images
        content_str = str(msg.content)
        assert "/path/a.png" in content_str
        assert "/path/b.jpg" in content_str
        # And cleared
        assert result["viewed_images"] == {}

    def test_second_injection_only_gets_new_images(self):
        """After first injection clears state, second round only has new images."""
        mw = ViewImageMiddleware()

        # Round 1: view_image A
        messages_r1 = [
            AIMessage(content="", tool_calls=[_make_tool_call("view_image", "tc1")]),
            ToolMessage(content="OK", tool_call_id="tc1"),
        ]
        state_r1 = _state(messages=messages_r1, viewed_images={"/a.png": IMG_A})
        result_r1 = mw.before_model(state_r1, _runtime())
        assert result_r1 is not None
        assert result_r1["viewed_images"] == {}

        # Round 2: view_image B — only B should be injected
        messages_r2 = [
            *messages_r1,
            result_r1["messages"][0],  # injected human msg from round 1
            AIMessage(content="I see image A"),
            AIMessage(content="", tool_calls=[_make_tool_call("view_image", "tc2")]),
            ToolMessage(content="OK", tool_call_id="tc2"),
        ]
        # viewed_images only has B (A was cleared)
        state_r2 = _state(messages=messages_r2, viewed_images={"/b.jpg": IMG_B})
        result_r2 = mw.before_model(state_r2, _runtime())

        assert result_r2 is not None
        msg2 = result_r2["messages"][0]
        content_str = str(msg2.content)
        assert "/b.jpg" in content_str
        assert "/a.png" not in content_str  # A should NOT be re-injected


class TestShouldNotInject:
    """Cases where injection should NOT happen."""

    def test_no_view_image_tool_call(self):
        mw = ViewImageMiddleware()
        messages = [
            AIMessage(content="", tool_calls=[_make_tool_call("bash", "tc1")]),
            ToolMessage(content="OK", tool_call_id="tc1"),
        ]
        state = _state(messages=messages, viewed_images={"/a.png": IMG_A})
        assert mw.before_model(state, _runtime()) is None

    def test_tools_not_yet_completed(self):
        mw = ViewImageMiddleware()
        messages = [
            AIMessage(content="", tool_calls=[_make_tool_call("view_image", "tc1")]),
            # No ToolMessage yet
        ]
        state = _state(messages=messages, viewed_images={"/a.png": IMG_A})
        assert mw.before_model(state, _runtime()) is None

    def test_already_injected(self):
        mw = ViewImageMiddleware()
        messages = [
            AIMessage(content="", tool_calls=[_make_tool_call("view_image", "tc1")]),
            ToolMessage(content="OK", tool_call_id="tc1"),
            HumanMessage(content="Here are the images you've viewed:"),
        ]
        state = _state(messages=messages, viewed_images={"/a.png": IMG_A})
        assert mw.before_model(state, _runtime()) is None

    def test_no_messages(self):
        mw = ViewImageMiddleware()
        state = _state(messages=[], viewed_images={"/a.png": IMG_A})
        assert mw.before_model(state, _runtime()) is None

    def test_empty_viewed_images(self):
        mw = ViewImageMiddleware()
        messages = [
            AIMessage(content="", tool_calls=[_make_tool_call("view_image", "tc1")]),
            ToolMessage(content="OK", tool_call_id="tc1"),
        ]
        state = _state(messages=messages, viewed_images={})
        result = mw.before_model(state, _runtime())
        # Should still inject but with "No images" message, and clear is ok
        assert result is not None
        assert result["viewed_images"] == {}
