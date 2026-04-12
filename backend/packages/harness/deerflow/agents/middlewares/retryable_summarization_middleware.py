"""SummarizationMiddleware subclass with retry on transient LLM errors.

The upstream ``SummarizationMiddleware._create_summary()`` catches all
exceptions and returns ``"Error generating summary: {error}"`` as the
summary text — no retry.  This subclass wraps the LLM call with a simple
retry loop so that transient failures (rate-limits, network glitches,
temporary provider errors) are retried before falling back to the error
string.
"""

from __future__ import annotations

import asyncio
import logging
import time

from langchain.agents.middleware.summarization import SummarizationMiddleware
from langchain_core.messages import AnyMessage

logger = logging.getLogger(__name__)

# Status codes / error patterns that are worth retrying
_RETRIABLE_STATUS_CODES = {400, 408, 429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_BASE_DELAY_SECONDS = 2.0


def _is_retriable_error(exc: Exception) -> bool:
    """Check if an exception is likely transient and worth retrying."""
    msg = str(exc).lower()
    # Check for HTTP status codes in error message
    for code in _RETRIABLE_STATUS_CODES:
        if str(code) in msg:
            return True
    # Common transient error patterns
    transient_patterns = [
        "rate limit",
        "too many requests",
        "timeout",
        "timed out",
        "connection",
        "network",
        "temporary",
        "overloaded",
        "service unavailable",
        "bad gateway",
        "internal server error",
        "litellm",
    ]
    return any(p in msg for p in transient_patterns)


class RetryableSummarizationMiddleware(SummarizationMiddleware):
    """SummarizationMiddleware with automatic retry on transient errors.

    Parameters
    ----------
    max_retries:
        Maximum number of retry attempts (default 3).
    base_delay:
        Base delay in seconds for exponential backoff (default 2.0).
    """

    def __init__(self, *args, max_retries: int = _MAX_RETRIES, base_delay: float = _BASE_DELAY_SECONDS, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._max_retries = max_retries
        self._base_delay = base_delay

    def _create_summary(self, messages_to_summarize: list[AnyMessage]) -> str:
        """Generate summary with retry on transient errors."""
        if not messages_to_summarize:
            return "No previous conversation history."

        trimmed = self._trim_messages_for_summary(messages_to_summarize)
        if not trimmed:
            return "Previous conversation was too long to summarize."

        from langchain_core.messages.utils import get_buffer_string

        formatted = get_buffer_string(trimmed)
        prompt = self.summary_prompt.format(messages=formatted)

        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = self.model.invoke(prompt)
                return response.text.strip()
            except Exception as e:
                last_exc = e
                if attempt < self._max_retries and _is_retriable_error(e):
                    delay = self._base_delay * (2 ** attempt)
                    logger.warning(
                        "Summary generation failed (attempt %d/%d), retrying in %.1fs: %s",
                        attempt + 1,
                        self._max_retries + 1,
                        delay,
                        e,
                    )
                    time.sleep(delay)
                else:
                    break

        # All retries exhausted or non-retriable error
        logger.error("Summary generation failed after %d attempts: %s", self._max_retries + 1, last_exc)
        return f"Error generating summary: {last_exc!s}"

    async def _acreate_summary(self, messages_to_summarize: list[AnyMessage]) -> str:
        """Generate summary with retry on transient errors (async)."""
        if not messages_to_summarize:
            return "No previous conversation history."

        trimmed = self._trim_messages_for_summary(messages_to_summarize)
        if not trimmed:
            return "Previous conversation was too long to summarize."

        from langchain_core.messages.utils import get_buffer_string

        formatted = get_buffer_string(trimmed)
        prompt = self.summary_prompt.format(messages=formatted)

        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await self.model.ainvoke(prompt)
                return response.text.strip()
            except Exception as e:
                last_exc = e
                if attempt < self._max_retries and _is_retriable_error(e):
                    delay = self._base_delay * (2 ** attempt)
                    logger.warning(
                        "Summary generation failed (attempt %d/%d), retrying in %.1fs: %s",
                        attempt + 1,
                        self._max_retries + 1,
                        delay,
                        e,
                    )
                    await asyncio.sleep(delay)
                else:
                    break

        logger.error("Summary generation failed after %d attempts: %s", self._max_retries + 1, last_exc)
        return f"Error generating summary: {last_exc!s}"
