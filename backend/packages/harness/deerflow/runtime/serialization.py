"""Canonical serialization for LangChain / LangGraph objects.

Provides a single source of truth for converting LangChain message
objects, Pydantic models, and LangGraph state dicts into plain
JSON-serialisable Python structures.

Consumers: ``deerflow.runtime.runs.worker`` (SSE publishing) and
``app.gateway.routers.threads`` (REST responses).
"""

from __future__ import annotations

from typing import Any

# Maximum characters for a single text content block in a message.
# Longer blocks are truncated to keep SSE payloads small.
_MAX_CONTENT_CHARS = 4000


def serialize_lc_object(obj: Any) -> Any:
    """Recursively serialize a LangChain object to a JSON-serialisable dict."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: serialize_lc_object(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [serialize_lc_object(item) for item in obj]
    # Pydantic v2
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    # Pydantic v1 / older objects
    if hasattr(obj, "dict"):
        try:
            return obj.dict()
        except Exception:
            pass
    # Last resort
    try:
        return str(obj)
    except Exception:
        return repr(obj)


def _slim_message(msg: Any) -> Any:
    """Strip image_url parts and truncate long text in a serialized message dict."""
    if not isinstance(msg, dict):
        return msg
    content = msg.get("content")
    if not isinstance(content, list):
        return msg
    new_parts: list[Any] = []
    for part in content:
        if not isinstance(part, dict):
            new_parts.append(part)
            continue
        if part.get("type") == "image_url":
            new_parts.append({"type": "text", "text": "[图片已省略]"})
            continue
        text = part.get("text")
        if isinstance(text, str) and len(text) > _MAX_CONTENT_CHARS:
            part = {**part, "text": text[:_MAX_CONTENT_CHARS] + "...[truncated]"}
        new_parts.append(part)
    return {**msg, "content": new_parts}


def serialize_channel_values(channel_values: dict[str, Any], *, slim: bool = False) -> dict[str, Any]:
    """Serialize channel values, stripping internal LangGraph keys.

    Internal keys like ``__pregel_*`` and ``__interrupt__`` are removed
    to match what the LangGraph Platform API returns.

    When *slim* is True, message content is trimmed: ``image_url`` parts
    are replaced with placeholders and long text blocks are truncated.
    This keeps SSE ``values`` events small for long conversations.
    """
    result: dict[str, Any] = {}
    for key, value in channel_values.items():
        if key.startswith("__pregel_") or key == "__interrupt__":
            continue
        if key == "viewed_images" and slim:
            # Frontend never uses viewed_images; skip to reduce payload.
            continue
        serialized = serialize_lc_object(value)
        if slim and key == "messages" and isinstance(serialized, list):
            serialized = [_slim_message(m) for m in serialized]
        result[key] = serialized
    return result


def serialize_messages_tuple(obj: Any) -> Any:
    """Serialize a messages-mode tuple ``(chunk, metadata)``."""
    if isinstance(obj, tuple) and len(obj) == 2:
        chunk, metadata = obj
        return [serialize_lc_object(chunk), metadata if isinstance(metadata, dict) else {}]
    return serialize_lc_object(obj)


def serialize(obj: Any, *, mode: str = "", slim: bool = False) -> Any:
    """Serialize LangChain objects with mode-specific handling.

    * ``messages`` — obj is ``(message_chunk, metadata_dict)``
    * ``values`` — obj is the full state dict; ``__pregel_*`` keys stripped
    * everything else — recursive ``model_dump()`` / ``dict()`` fallback

    When *slim* is True and mode is ``values``, the output is trimmed
    (image_url parts removed, long text truncated) to keep SSE payloads small.
    """
    if mode == "messages":
        return serialize_messages_tuple(obj)
    if mode == "values":
        if isinstance(obj, dict):
            return serialize_channel_values(obj, slim=slim)
        return serialize_lc_object(obj)
    return serialize_lc_object(obj)
