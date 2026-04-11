"""Middleware to persist main conversation to local JSONL for debugging.

Each agent turn appends new messages (Human/AI/Tool) as JSONL lines to
``{base_dir}/threads/{thread_id}/conversation.jsonl``.  The file is
append-only and line-oriented, making it safe to read while the agent
is still running.

Uses the same JSONL format as ``SubagentSession`` (from
``deerflow.subagents.session.serialize_message``) so that tooling can
consume both main and sub-agent conversation logs uniformly.

Design:
  - Uses ``aafter_model`` to capture messages in real-time after each
    model response.
  - Tracks last N written message IDs per thread to avoid duplicates
    (handles summarization which may shrink the message list).
  - No truncation — records full content for debugging.
  - Thread-safe via ``threading.Lock``.
"""

import json
import logging
import threading
from collections import OrderedDict, deque
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import BaseMessage
from langgraph.runtime import Runtime

from deerflow.config.paths import get_paths
from deerflow.subagents.session import serialize_message

logger = logging.getLogger(__name__)

_DEFAULT_DEDUP_WINDOW = 10  # only check last N message IDs per thread


def _extract_thread_id(runtime: Runtime) -> str | None:
    """Extract thread_id from runtime context."""
    # Try runtime.context first (set by ThreadDataMiddleware)
    context = getattr(runtime, "context", None)
    if context and isinstance(context, dict):
        tid = context.get("thread_id")
        if tid:
            return tid

    # Fallback: LangGraph configurable
    configurable = {}
    if hasattr(runtime, "config") and runtime.config:
        configurable = runtime.config.get("configurable", {})
    if not configurable:
        return None
    return configurable.get("thread_id")


class MainSessionMiddleware(AgentMiddleware[AgentState]):
    """Persists main conversation messages to local JSONL for debugging.

    Runs after each model response (``aafter_model``) to capture messages
    in real-time.  Uses a small sliding window of recent message IDs per
    thread to avoid duplicates, which correctly handles SummarizationMiddleware
    shrinking the message list.

    The JSONL format is identical to ``SubagentSession`` so that the same
    tooling can process both main and sub-agent logs.
    """

    def __init__(self, dedup_window: int = _DEFAULT_DEDUP_WINDOW):
        self._dedup_window = dedup_window
        # thread_id -> deque of recent written message IDs
        self._written_ids: dict[str, deque[str]] = {}
        self._lock = threading.Lock()

    def _get_jsonl_path(self, thread_id: str) -> "Any":
        """Return the JSONL file path for a thread."""
        return get_paths().thread_dir(thread_id) / "conversation.jsonl"

    def _get_new_messages(self, thread_id: str, messages: list[BaseMessage]) -> list[BaseMessage]:
        """Return messages not yet written for this thread (sliding window dedup)."""
        with self._lock:
            ids = self._written_ids.get(thread_id)
            if ids is None:
                ids = deque(maxlen=self._dedup_window)
                self._written_ids[thread_id] = ids

            new_msgs: list[BaseMessage] = []
            for msg in messages:
                msg_id = getattr(msg, "id", None) or ""
                if msg_id and msg_id in ids:
                    continue
                new_msgs.append(msg)
                if msg_id:
                    ids.append(msg_id)

            return new_msgs

    def _write_messages(self, jsonl_path: "Any", messages: list[BaseMessage]) -> None:
        """Append serialized messages to JSONL file."""
        lines = []
        for msg in messages:
            entry = serialize_message(msg)
            lines.append(json.dumps(entry, ensure_ascii=False))

        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    @override
    def after_model(self, state: dict, runtime: Runtime) -> dict | None:
        """Sync version — no-op, async version is preferred."""
        return None

    @override
    async def aafter_model(self, state: dict, runtime: Runtime) -> dict | None:
        """Append new messages to conversation JSONL after each model response."""
        thread_id = _extract_thread_id(runtime)
        if not thread_id:
            return None

        messages = state.get("messages", [])
        if not messages:
            return None

        new_messages = self._get_new_messages(thread_id, messages)
        if not new_messages:
            return None

        import asyncio

        jsonl_path = self._get_jsonl_path(thread_id)
        await asyncio.to_thread(self._write_messages, jsonl_path, new_messages)
        logger.debug("Appended %d messages to %s", len(new_messages), jsonl_path)
        return None
