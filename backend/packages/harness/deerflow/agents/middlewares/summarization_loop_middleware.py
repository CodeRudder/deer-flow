"""Middleware to detect and break infinite summarization loops.

When SummarizationMiddleware fires repeatedly because the preserved messages
(including large ToolMessage responses) are still above the token threshold,
this middleware detects the cycle and takes corrective action:

1. **Soft limit** (default 2 rounds): Truncate oversized ToolMessage content
   and inject a warning so the model stops reading large files.
2. **Hard limit** (default 3 rounds): Strip all ToolMessage content and
   remove tool_calls from the last AI message, forcing a final text answer.

Runs in ``before_model`` immediately after ``SummarizationMiddleware`` so it
can observe whether summarisation just occurred.
"""

import logging
import threading
from collections import OrderedDict
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

# Default configuration
_DEFAULT_WARN_THRESHOLD = 2
_DEFAULT_HARD_LIMIT = 3
_DEFAULT_MAX_TOOL_CONTENT_LEN = 2000
_DEFAULT_MAX_TRACKED_THREADS = 100

_SUMMARY_PREFIX = "Here is a summary of the conversation to date:"

_WARNING_MSG = (
    "[SUMMARIZATION LOOP] Context summarization has been triggered multiple times. "
    "Large tool responses are being truncated. Stop reading large files and produce "
    "your final answer using the information you already have."
)

_HARD_STOP_MSG = (
    "[FORCED STOP: SUMMARIZATION LOOP] Summarization loop limit reached. "
    "All tool response content has been cleared. Produce your final answer now."
)


class SummarizationLoopMiddleware(AgentMiddleware[AgentState]):
    """Detects and breaks infinite summarization loops.

    Args:
        warn_threshold: Number of summary rounds before truncating large
            ToolMessages and injecting a warning. Default: 2.
        hard_limit: Number of summary rounds before forcing a stop by
            removing all tool_calls. Default: 3.
        max_tool_content_len: Maximum characters to keep in each
            ToolMessage before truncation. Default: 2000.
        max_tracked_threads: Maximum number of threads to track before
            evicting the least recently used. Default: 100.
    """

    def __init__(
        self,
        warn_threshold: int = _DEFAULT_WARN_THRESHOLD,
        hard_limit: int = _DEFAULT_HARD_LIMIT,
        max_tool_content_len: int = _DEFAULT_MAX_TOOL_CONTENT_LEN,
        max_tracked_threads: int = _DEFAULT_MAX_TRACKED_THREADS,
    ):
        super().__init__()
        self.warn_threshold = warn_threshold
        self.hard_limit = hard_limit
        self.max_tool_content_len = max_tool_content_len
        self.max_tracked_threads = max_tracked_threads
        self._lock = threading.Lock()
        # Per-thread summary count, ordered for LRU eviction
        self._summary_counts: OrderedDict[str, int] = OrderedDict()

    def _get_thread_id(self, runtime: Runtime) -> str:
        """Extract thread_id from runtime context."""
        thread_id = runtime.context.get("thread_id") if runtime.context else None
        return thread_id if thread_id else "default"

    def _evict_if_needed(self) -> None:
        """Evict least recently used threads if over the limit.

        Must be called while holding self._lock.
        """
        while len(self._summary_counts) > self.max_tracked_threads:
            evicted_id, _ = self._summary_counts.popitem(last=False)
            logger.debug("Evicted summarization loop tracking for thread %s (LRU)", evicted_id)

    def _detect(self, messages: list) -> tuple[int, list[int], list[int]]:
        """Scan messages for summary markers and oversized tool responses.

        Returns:
            (summary_count, large_tool_indices, all_tool_indices)
        """
        summary_count = 0
        large_tool_indices: list[int] = []
        all_tool_indices: list[int] = []

        for i, msg in enumerate(messages):
            if isinstance(msg, HumanMessage):
                content = msg.content if isinstance(msg.content, str) else ""
                if content.startswith(_SUMMARY_PREFIX):
                    summary_count += 1
            elif isinstance(msg, ToolMessage):
                all_tool_indices.append(i)
                content = msg.content
                if isinstance(content, str) and len(content) > self.max_tool_content_len:
                    large_tool_indices.append(i)

        return summary_count, large_tool_indices, all_tool_indices

    @staticmethod
    def _truncate_content(content: str, max_len: int) -> str:
        """Truncate content to max_len with a truncation notice."""
        return content[:max_len] + f"\n...[TRUNCATED: original {len(content)} chars]"

    def _apply(self, state: AgentState, runtime: Runtime) -> dict | None:
        messages = state.get("messages", [])
        if not messages:
            return None

        summary_count, large_tool_indices, all_tool_indices = self._detect(messages)
        thread_id = self._get_thread_id(runtime)

        with self._lock:
            # Update per-thread count
            if thread_id in self._summary_counts:
                self._summary_counts.move_to_end(thread_id)
            else:
                self._summary_counts[thread_id] = 0
                self._evict_if_needed()

            if summary_count == 0:
                # No summarization this round — reset counter
                self._summary_counts[thread_id] = 0
                return None

            # Increment — we see a summary message, meaning summarization just ran
            self._summary_counts[thread_id] = summary_count
            current_count = summary_count

        logger.debug(
            "Summarization loop check: thread=%s summary_count=%d warn=%d hard=%d",
            thread_id,
            current_count,
            self.warn_threshold,
            self.hard_limit,
        )

        # Below thresholds — no action yet
        if current_count < self.warn_threshold:
            return None

        updated_messages: list = []
        needs_update = False

        if current_count >= self.hard_limit:
            # Hard stop: truncate ALL ToolMessages + remove tool_calls from last AI
            logger.error(
                "Summarization loop hard limit reached — forcing stop",
                extra={"thread_id": thread_id, "summary_count": current_count},
            )
            # Find the last AIMessage with tool_calls (may not be the last message)
            last_ai_with_tools = -1
            for i in range(len(messages) - 1, -1, -1):
                if isinstance(messages[i], AIMessage) and messages[i].tool_calls:
                    last_ai_with_tools = i
                    break

            for i, msg in enumerate(messages):
                if isinstance(msg, ToolMessage):
                    truncated_content = self._truncate_content(
                        msg.content if isinstance(msg.content, str) else str(msg.content),
                        self.max_tool_content_len,
                    )
                    updated_messages.append(
                        msg.model_copy(update={"content": truncated_content})
                    )
                    needs_update = True
                elif i == last_ai_with_tools:
                    # Remove tool_calls from last AI message to force text output
                    updated_messages.append(
                        msg.model_copy(
                            update={
                                "tool_calls": [],
                                "content": self._append_text(msg.content, _HARD_STOP_MSG),
                            }
                        )
                    )
                    needs_update = True
                else:
                    updated_messages.append(msg)

            # Always inject hard-stop warning
            updated_messages.append(HumanMessage(content=_HARD_STOP_MSG))
            needs_update = True

        else:
            # Soft limit: truncate only oversized ToolMessages + inject warning
            logger.warning(
                "Summarization loop detected — truncating large tool responses",
                extra={
                    "thread_id": thread_id,
                    "summary_count": current_count,
                    "large_tools": len(large_tool_indices),
                },
            )
            for i, msg in enumerate(messages):
                if i in large_tool_indices:
                    truncated_content = self._truncate_content(msg.content, self.max_tool_content_len)
                    updated_messages.append(
                        msg.model_copy(update={"content": truncated_content})
                    )
                    needs_update = True
                else:
                    updated_messages.append(msg)

            # Inject warning
            updated_messages.append(HumanMessage(content=_WARNING_MSG))
            needs_update = True

        if needs_update:
            return {"messages": updated_messages}

        return None

    @staticmethod
    def _append_text(content: str | list | None, text: str) -> str | list:
        """Append text to AIMessage content, handling str, list, and None."""
        if content is None:
            return text
        if isinstance(content, list):
            return [*content, {"type": "text", "text": f"\n\n{text}"}]
        if isinstance(content, str):
            return content + f"\n\n{text}"
        return str(content) + f"\n\n{text}"

    @override
    def before_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._apply(state, runtime)

    @override
    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._apply(state, runtime)

    def reset(self, thread_id: str | None = None) -> None:
        """Clear tracking state. If thread_id given, clear only that thread."""
        with self._lock:
            if thread_id:
                self._summary_counts.pop(thread_id, None)
            else:
                self._summary_counts.clear()
