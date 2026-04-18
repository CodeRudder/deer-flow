"""Tests for deerflow.runtime.serialization."""

from __future__ import annotations


class _FakePydanticV2:
    """Object with model_dump (Pydantic v2)."""

    def model_dump(self):
        return {"key": "v2"}


class _FakePydanticV1:
    """Object with dict (Pydantic v1)."""

    def dict(self):
        return {"key": "v1"}


class _Unprintable:
    """Object whose str() raises."""

    def __str__(self):
        raise RuntimeError("no str")

    def __repr__(self):
        return "<Unprintable>"


def test_serialize_none():
    from deerflow.runtime.serialization import serialize_lc_object

    assert serialize_lc_object(None) is None


def test_serialize_primitives():
    from deerflow.runtime.serialization import serialize_lc_object

    assert serialize_lc_object("hello") == "hello"
    assert serialize_lc_object(42) == 42
    assert serialize_lc_object(3.14) == 3.14
    assert serialize_lc_object(True) is True


def test_serialize_dict():
    from deerflow.runtime.serialization import serialize_lc_object

    obj = {"a": _FakePydanticV2(), "b": [1, "two"]}
    result = serialize_lc_object(obj)
    assert result == {"a": {"key": "v2"}, "b": [1, "two"]}


def test_serialize_list():
    from deerflow.runtime.serialization import serialize_lc_object

    result = serialize_lc_object([_FakePydanticV1(), 1])
    assert result == [{"key": "v1"}, 1]


def test_serialize_tuple():
    from deerflow.runtime.serialization import serialize_lc_object

    result = serialize_lc_object((_FakePydanticV2(),))
    assert result == [{"key": "v2"}]


def test_serialize_pydantic_v2():
    from deerflow.runtime.serialization import serialize_lc_object

    assert serialize_lc_object(_FakePydanticV2()) == {"key": "v2"}


def test_serialize_pydantic_v1():
    from deerflow.runtime.serialization import serialize_lc_object

    assert serialize_lc_object(_FakePydanticV1()) == {"key": "v1"}


def test_serialize_fallback_str():
    from deerflow.runtime.serialization import serialize_lc_object

    result = serialize_lc_object(object())
    assert isinstance(result, str)


def test_serialize_fallback_repr():
    from deerflow.runtime.serialization import serialize_lc_object

    assert serialize_lc_object(_Unprintable()) == "<Unprintable>"


def test_serialize_channel_values_strips_pregel_keys():
    from deerflow.runtime.serialization import serialize_channel_values

    raw = {
        "messages": ["hello"],
        "__pregel_tasks": "internal",
        "__pregel_resuming": True,
        "__interrupt__": "stop",
        "title": "Test",
    }
    result = serialize_channel_values(raw)
    assert "messages" in result
    assert "title" in result
    assert "__pregel_tasks" not in result
    assert "__pregel_resuming" not in result
    assert "__interrupt__" not in result


def test_serialize_channel_values_serializes_objects():
    from deerflow.runtime.serialization import serialize_channel_values

    result = serialize_channel_values({"obj": _FakePydanticV2()})
    assert result == {"obj": {"key": "v2"}}


def test_serialize_messages_tuple():
    from deerflow.runtime.serialization import serialize_messages_tuple

    chunk = _FakePydanticV2()
    metadata = {"langgraph_node": "agent"}
    result = serialize_messages_tuple((chunk, metadata))
    assert result == [{"key": "v2"}, {"langgraph_node": "agent"}]


def test_serialize_messages_tuple_non_dict_metadata():
    from deerflow.runtime.serialization import serialize_messages_tuple

    result = serialize_messages_tuple((_FakePydanticV2(), "not-a-dict"))
    assert result == [{"key": "v2"}, {}]


def test_serialize_messages_tuple_fallback():
    from deerflow.runtime.serialization import serialize_messages_tuple

    result = serialize_messages_tuple("not-a-tuple")
    assert result == "not-a-tuple"


def test_serialize_dispatcher_messages_mode():
    from deerflow.runtime.serialization import serialize

    chunk = _FakePydanticV2()
    result = serialize((chunk, {"node": "x"}), mode="messages")
    assert result == [{"key": "v2"}, {"node": "x"}]


def test_serialize_dispatcher_values_mode():
    from deerflow.runtime.serialization import serialize

    result = serialize({"msg": "hi", "__pregel_tasks": "x"}, mode="values")
    assert result == {"msg": "hi"}


def test_serialize_dispatcher_default_mode():
    from deerflow.runtime.serialization import serialize

    result = serialize(_FakePydanticV1())
    assert result == {"key": "v1"}


# ---------------------------------------------------------------------------
# Slim mode tests
# ---------------------------------------------------------------------------


def _image_url_part(b64: str = "aaaa", mime: str = "image/png") -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def _text_part(text: str) -> dict:
    return {"type": "text", "text": text}


def _make_msg(content: list) -> dict:
    return {"role": "user", "content": content, "id": "m1"}


class TestSlimMessage:
    def test_replaces_image_url_with_placeholder(self):
        from deerflow.runtime.serialization import _slim_message

        msg = _make_msg([_text_part("hello"), _image_url_part("A" * 1000)])
        result = _slim_message(msg)
        parts = result["content"]
        assert len(parts) == 2
        assert parts[0] == _text_part("hello")
        assert parts[1] == {"type": "text", "text": "[图片已省略]"}
        # Original not mutated
        assert msg["content"][1]["type"] == "image_url"

    def test_truncates_long_text(self):
        from deerflow.runtime.serialization import _slim_message, _MAX_CONTENT_CHARS

        long_text = "x" * (_MAX_CONTENT_CHARS + 500)
        msg = _make_msg([_text_part(long_text)])
        result = _slim_message(msg)
        text = result["content"][0]["text"]
        assert len(text) < len(long_text)
        assert text.endswith("...[truncated]")

    def test_short_text_untouched(self):
        from deerflow.runtime.serialization import _slim_message

        msg = _make_msg([_text_part("short")])
        result = _slim_message(msg)
        assert result["content"][0] == _text_part("short")

    def test_string_content_unchanged(self):
        from deerflow.runtime.serialization import _slim_message

        msg = {"role": "user", "content": "plain text", "id": "m1"}
        result = _slim_message(msg)
        assert result == msg

    def test_non_dict_returned_as_is(self):
        from deerflow.runtime.serialization import _slim_message

        assert _slim_message("hello") == "hello"


class TestSerializeChannelValuesSlim:
    def test_slim_strips_viewed_images(self):
        from deerflow.runtime.serialization import serialize_channel_values

        raw = {"messages": [], "viewed_images": {"/a.png": {"base64": "xxx"}}}
        result = serialize_channel_values(raw, slim=True)
        assert "viewed_images" not in result

    def test_slim_strips_images_from_messages(self):
        from deerflow.runtime.serialization import serialize_channel_values

        raw = {"messages": [{"content": [_image_url_part()], "id": "m1"}]}
        result = serialize_channel_values(raw, slim=True)
        msgs = result["messages"]
        assert msgs[0]["content"][0]["text"] == "[图片已省略]"

    def test_no_slim_preserves_everything(self):
        from deerflow.runtime.serialization import serialize_channel_values

        raw = {"messages": [], "viewed_images": {"a": 1}}
        result = serialize_channel_values(raw, slim=False)
        assert "viewed_images" in result


class TestSerializeSlim:
    def test_serialize_values_slim(self):
        from deerflow.runtime.serialization import serialize

        raw = {"messages": [{"content": [_image_url_part()], "id": "m1"}], "__pregel_x": True}
        result = serialize(raw, mode="values", slim=True)
        assert "__pregel_x" not in result
        assert result["messages"][0]["content"][0]["text"] == "[图片已省略]"

    def test_serialize_values_no_slim(self):
        from deerflow.runtime.serialization import serialize

        raw = {"messages": [{"content": [_image_url_part()], "id": "m1"}]}
        result = serialize(raw, mode="values", slim=False)
        assert result["messages"][0]["content"][0]["type"] == "image_url"

    def test_serialize_messages_mode_ignores_slim(self):
        from deerflow.runtime.serialization import serialize

        result = serialize(("chunk", {"node": "x"}), mode="messages", slim=True)
        assert result == ["chunk", {"node": "x"}]
