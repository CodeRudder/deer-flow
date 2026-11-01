"""Response-side base64 slimming for message/event API payloads.

Read-only API responses (run/thread messages, run events, SSE frames) can
carry multi-megabyte inline base64 images injected by
``ViewImageMiddleware`` (``image_url`` content parts with ``data:`` URLs) or
returned by image-generation tools (``b64_json`` fields).  Persisted data is
left untouched — this module only slims what crosses the HTTP boundary so
opening a thread does not download megabytes of base64.

Rules (see :func:`sanitize_value`):

1. ``content`` parts of the form ``{"type": "image_url", "image_url": {"url":
   "data:image/...;base64,..."}}`` (or a bare ``data:`` string) become
   ``{"type": "image_url_removed", "note": ..., "size_bytes": N}`` so clients
   can still render a degradation notice instead of silently dropping the part.
2. Any string value containing a base64 data segment larger than
   :data:`BASE64_TRUNCATE_THRESHOLD` bytes is truncated to a
   ``[base64 omitted N KB]`` placeholder (the surrounding text is kept).
3. ``b64_json`` string fields are replaced with ``b64_json_omitted: true``.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "BASE64_TRUNCATE_THRESHOLD",
    "sanitize_message_list",
    "sanitize_message_record",
    "sanitize_value",
]

# Base64 data segments longer than this are truncated in string values.
BASE64_TRUNCATE_THRESHOLD = 8 * 1024

_IMAGE_URL_REMOVED = "image_url_removed"
_IMAGE_REMOVED_NOTE = "inline image omitted for performance"

# data:image/png;base64,<payload>  (payload captured)
_DATA_IMAGE_URL_RE = re.compile(r"data:image/[A-Za-z0-9.+-]+;base64,([A-Za-z0-9+/=_-]+)")
# Bare base64 runs (no data: prefix), bounded by non-base64 characters.
_BARE_BASE64_RE = re.compile(r"(?<![A-Za-z0-9+/=_-])([A-Za-z0-9+/]{32,}={0,2})(?![A-Za-z0-9+/=_-])")


def _omitted_placeholder(size_bytes: int) -> str:
    kb = max(1, size_bytes // 1024)
    return f"[base64 omitted {kb} KB]"


def _truncate_base64_segments(text: str) -> str:
    """Replace oversized base64 segments in *text* with placeholders."""
    if len(text) <= BASE64_TRUNCATE_THRESHOLD:
        return text
    if ";base64," in text:
        text = _DATA_IMAGE_URL_RE.sub(
            lambda m: _omitted_placeholder(len(m.group(1))),
            text,
        )
    else:
        text = _BARE_BASE64_RE.sub(
            lambda m: m.group(1) if len(m.group(1)) <= BASE64_TRUNCATE_THRESHOLD else _omitted_placeholder(len(m.group(1))),
            text,
        )
    return text


def _extract_data_url_payload(url: Any) -> str | None:
    """Return the base64 payload if *url* is an inline base64 image URL."""
    if not isinstance(url, str):
        return None
    match = _DATA_IMAGE_URL_RE.fullmatch(url) if "\n" not in url else None
    if match is None:
        # Tolerate data URLs embedded in larger strings (e.g. markdown).
        match = _DATA_IMAGE_URL_RE.search(url)
    if match is None:
        return None
    return match.group(1)


def _image_url_replacement(url: Any) -> dict[str, Any] | None:
    payload = _extract_data_url_payload(url)
    if payload is None:
        return None
    return {
        "type": _IMAGE_URL_REMOVED,
        "note": _IMAGE_REMOVED_NOTE,
        "size_bytes": len(payload),
    }


def _is_image_url_part(part: dict[str, Any]) -> bool:
    if part.get("type") != "image_url":
        return False
    image_url = part.get("image_url")
    if isinstance(image_url, dict):
        return _extract_data_url_payload(image_url.get("url")) is not None
    return _extract_data_url_payload(image_url) is not None


def _image_url_part_replacement(part: dict[str, Any]) -> dict[str, Any] | None:
    if not _is_image_url_part(part):
        return None
    image_url = part.get("image_url")
    url = image_url.get("url") if isinstance(image_url, dict) else image_url
    return _image_url_replacement(url)


def sanitize_message_record(message: Any) -> Any:
    """Slim a single serialized message record (does not mutate the input)."""
    return sanitize_value(message)


def sanitize_message_list(messages: Any) -> Any:
    """Slim a list of message records, returning a new list."""
    if not isinstance(messages, list):
        return messages
    return [sanitize_message_record(message) for message in messages]


def sanitize_value(value: Any, _depth: int = 0) -> Any:
    """Recursively remove oversized base64 payloads from *value*.

    Returns a new structure; the input is never mutated.  Non-dict/list/str
    values pass through unchanged.
    """
    if _depth > 48:
        return value
    if isinstance(value, str):
        return _truncate_base64_segments(value)
    if isinstance(value, dict):
        # Check for an inline-base64 image_url part BEFORE recursing — string
        # truncation would otherwise rewrite the data URL and hide the match.
        replacement = _image_url_part_replacement(value)
        if replacement is not None:
            return replacement
        changed = False
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if key == "b64_json" and isinstance(item, str):
                cleaned["b64_json_omitted"] = True
                changed = True
                continue
            new_item = sanitize_value(item, _depth + 1)
            if new_item is not item:
                changed = True
            cleaned[key] = new_item
        return cleaned if changed else value
    if isinstance(value, list):
        changed = False
        cleaned_items: list[Any] = []
        for item in value:
            new_item = sanitize_value(item, _depth + 1)
            if new_item is not item:
                changed = True
            cleaned_items.append(new_item)
        return cleaned_items if changed else value
    return value
