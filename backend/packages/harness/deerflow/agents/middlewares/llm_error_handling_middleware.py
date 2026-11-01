"""LLM error handling middleware with retry/backoff and user-facing fallbacks."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from email.utils import parsedate_to_datetime
from typing import Any, override
from uuid import uuid4

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import (
    ModelCallResult,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage
from langgraph.errors import GraphBubbleUp

from deerflow.config.app_config import AppConfig
from deerflow.runtime.run_outcome import RUN_OUTCOME_CONTEXT_KEY, RunOutcomeTracker

logger = logging.getLogger(__name__)

_EMPTY_RESPONSE_FALLBACK = "LLM returned an empty response after multiple retries. Please try again later or check the model service status."
_MAX_TOKENS_FALLBACK = (
    "The model's thinking ran past the output token limit (thinking and answer share one max_tokens budget), "
    "so the response was cut off before any answer was produced. Retry with the task split into more, smaller "
    "steps so each turn needs less single-shot reasoning; if it persists, raise max_tokens or lower the "
    "thinking budget in Settings -> Models."
)
_RETRIABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
_BUSY_PATTERNS = (
    "server busy",
    "temporarily unavailable",
    "try again later",
    "please retry",
    "please try again",
    "overloaded",
    "high demand",
    "rate limit",
    "负载较高",
    "服务繁忙",
    "稍后重试",
    "请稍后重试",
)
_TRANSIENT_NETWORK_PATTERNS = (
    "网络错误",
    "connection error",
    "connection reset",
    "connection refused",
    "connection aborted",
    "network error",
    "network timeout",
    "network unreachable",
    "socket error",
    "broken pipe",
    "econnreset",
    "econnrefused",
    "etimedout",
    "eof occurred",
)
_QUOTA_PATTERNS = (
    "insufficient_quota",
    "quota",
    "billing",
    "credit",
    "payment",
    "余额不足",
    "超出限额",
    "额度不足",
    "欠费",
)
_AUTH_PATTERNS = (
    "authentication",
    "unauthorized",
    "invalid api key",
    "invalid_api_key",
    "permission",
    "forbidden",
    "access denied",
    "无权",
    "未授权",
)

# Per-exception retry budget overrides.
#
# Some transient errors are retriable in principle but expensive to retry at
# the default budget. StreamChunkTimeoutError in particular fires after the
# upstream provider has already stalled for `stream_chunk_timeout` seconds
# (typically 120-240s); a full 3-attempt loop can therefore stack 6-12 minutes
# of dead air before surfacing the failure to the user. We keep exactly one
# retry (cheap reconnect that catches genuine transient TCP blips) and then
# fail fast — the same buffered payload is overwhelmingly likely to fail
# again at the upstream provider for the same reason.
#
# Keys are exception class *names* (not classes) so we don't introduce
# import-time coupling on optional dependencies like langchain-openai. The
# value is the absolute max attempt count, NOT additional retries — so a
# value of 2 means "1 first attempt + 1 retry" (the CR-requested
# "keep one retry" behavior).
_RETRY_BUDGET_OVERRIDES: dict[str, int] = {
    "StreamChunkTimeoutError": 2,
}

# Exception class names that indicate the upstream stream-chunk watchdog
# fired because the model stalled mid-flight. These deserve a more specific
# user-facing message than the generic "temporarily unavailable" copy,
# because the typical root cause is a long tool-call serialization stalling
# the upstream stream — and the most actionable advice we can give the user
# is "ask for a shorter / split output" rather than "wait and retry".
# Generic connection drops (httpx RemoteProtocolError / ReadError) are
# intentionally excluded: they routinely fire on transient network blips
# with normal payloads, where the "split the work" guidance is misleading.
_STREAM_DROP_EXCEPTIONS: frozenset[str] = frozenset(
    {
        "StreamChunkTimeoutError",
    }
)


class LLMErrorHandlingMiddleware(AgentMiddleware[AgentState]):
    """Retry transient LLM errors and surface graceful assistant messages."""

    retry_max_attempts: int = 3
    retry_base_delay_ms: int = 1000
    retry_cap_delay_ms: int = 8000

    def __init__(self, *, app_config: AppConfig, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        self.circuit_failure_threshold = app_config.circuit_breaker.failure_threshold
        self.circuit_recovery_timeout_sec = app_config.circuit_breaker.recovery_timeout_sec

        # Circuit Breaker state
        self._circuit_lock = threading.Lock()
        self._circuit_failure_count = 0
        self._circuit_open_until = 0.0
        self._circuit_state = "closed"
        self._circuit_probe_in_flight = False

    def _max_attempts_for(self, exc: BaseException) -> int:
        """Return the effective max attempt count for this exception.

        Falls back to `self.retry_max_attempts` unless the exception class name
        appears in the per-exception override table.
        """
        override = _RETRY_BUDGET_OVERRIDES.get(type(exc).__name__)
        if override is None:
            return self.retry_max_attempts

        return min(override, self.retry_max_attempts)

    def _check_circuit(self) -> bool:
        """Returns True if circuit is OPEN (fast fail), False otherwise."""
        with self._circuit_lock:
            now = time.time()

            if self._circuit_state == "open":
                if now < self._circuit_open_until:
                    return True
                self._circuit_state = "half_open"
                self._circuit_probe_in_flight = False

            if self._circuit_state == "half_open":
                if self._circuit_probe_in_flight:
                    return True
                self._circuit_probe_in_flight = True
                return False

            return False

    def _record_success(self) -> None:
        with self._circuit_lock:
            if self._circuit_state != "closed" or self._circuit_failure_count > 0:
                logger.info("Circuit breaker reset (Closed). LLM service recovered.")
            self._circuit_failure_count = 0
            self._circuit_open_until = 0.0
            self._circuit_state = "closed"
            self._circuit_probe_in_flight = False

    def _record_failure(self) -> None:
        with self._circuit_lock:
            if self._circuit_state == "half_open":
                self._circuit_open_until = time.time() + self.circuit_recovery_timeout_sec
                self._circuit_state = "open"
                self._circuit_probe_in_flight = False
                logger.error(
                    "Circuit breaker probe failed (Open). Will probe again after %ds.",
                    self.circuit_recovery_timeout_sec,
                )
                return

            self._circuit_failure_count += 1
            if self._circuit_failure_count >= self.circuit_failure_threshold:
                self._circuit_open_until = time.time() + self.circuit_recovery_timeout_sec
                if self._circuit_state != "open":
                    self._circuit_state = "open"
                    self._circuit_probe_in_flight = False
                    logger.error(
                        "Circuit breaker tripped (Open). Threshold reached (%d). Will probe after %ds.",
                        self.circuit_failure_threshold,
                        self.circuit_recovery_timeout_sec,
                    )

    def _classify_error(self, exc: BaseException) -> tuple[bool, str]:
        detail = _extract_error_detail(exc)
        lowered = detail.lower()
        error_code = _extract_error_code(exc)
        status_code = _extract_status_code(exc)

        if _matches_any(lowered, _QUOTA_PATTERNS) or _matches_any(str(error_code).lower(), _QUOTA_PATTERNS):
            return False, "quota"
        if _matches_any(lowered, _AUTH_PATTERNS):
            return False, "auth"

        exc_name = exc.__class__.__name__
        if exc_name in {
            "APITimeoutError",
            "APIConnectionError",
            "InternalServerError",
            "ReadError",  # httpx.ReadError: connection dropped mid-stream
            "RemoteProtocolError",  # httpx: server closed connection unexpectedly
            "StreamChunkTimeoutError",  # langchain-openai: chunk gap exceeded stream_chunk_timeout
        }:
            return True, "transient"
        if status_code in _RETRIABLE_STATUS_CODES:
            return True, "transient"
        if _matches_any(lowered, _BUSY_PATTERNS):
            return True, "busy"
        if _matches_any(lowered, _TRANSIENT_NETWORK_PATTERNS):
            return True, "transient"

        return False, "generic"

    def _build_retry_delay_ms(self, attempt: int, exc: BaseException) -> int:
        retry_after = _extract_retry_after_ms(exc)
        if retry_after is not None:
            return retry_after
        backoff = self.retry_base_delay_ms * (2 ** max(0, attempt - 1))
        return min(backoff, self.retry_cap_delay_ms)

    def _build_retry_message(self, attempt: int, wait_ms: int, reason: str) -> str:
        seconds = max(1, round(wait_ms / 1000))
        if reason == "empty_response":
            return f"LLM returned an empty response on attempt {attempt}/{self.retry_max_attempts}. Retrying in {seconds}s."
        reason_text = "provider is busy" if reason == "busy" else "provider request failed temporarily"
        return f"LLM request retry {attempt}/{self.retry_max_attempts}: {reason_text}. Retrying in {seconds}s."

    def _build_circuit_breaker_message(self) -> str:
        return "The configured LLM provider is currently unavailable due to continuous failures. Circuit breaker is engaged to protect the system. Please wait a moment before trying again."

    def _build_error_fallback_message(
        self,
        content: str,
        *,
        error_type: str,
        reason: str,
        detail: str,
    ) -> AIMessage:
        return AIMessage(
            id=f"deerflow:error-fallback:{uuid4()}",
            content=content,
            additional_kwargs={
                "deerflow_error_fallback": True,
                "error_type": error_type,
                "error_reason": reason,
                "error_detail": detail,
            },
        )

    def _finalize_error_fallback_message(self, message: AIMessage, request: ModelRequest) -> AIMessage:
        runtime = getattr(request, "runtime", None)
        context = getattr(runtime, "context", None)
        if not isinstance(context, dict):
            return message

        outcome = context.get(RUN_OUTCOME_CONTEXT_KEY)
        if isinstance(outcome, RunOutcomeTracker) and context.get("is_subagent") is not True:
            outcome.record_llm_error_fallback(message)

        journal = context.get("__run_journal")
        if journal is not None:
            try:
                journal.record_llm_error_fallback_message(message, caller="lead_agent")
            except Exception:  # noqa: BLE001
                logger.debug("Failed to record LLM error fallback message", exc_info=True)
        return message

    def _build_user_message(self, exc: BaseException, reason: str) -> str:
        detail = _extract_error_detail(exc)
        if reason == "quota":
            return "The configured LLM provider rejected the request because the account is out of quota, billing is unavailable, or usage is restricted. Please fix the provider account and try again."
        if reason == "auth":
            return "The configured LLM provider rejected the request because authentication or access is invalid. Please check the provider credentials and try again."
        if reason in {"busy", "transient"}:
            # Stream-drop failures (chunk-gap timeout, peer-closed connection,
            # raw read error) almost always point at a single oversized
            # tool-call payload — the model spent so long serializing JSON
            # arguments that the upstream provider buffered and the stream
            # gap exceeded `stream_chunk_timeout`. Surfacing this distinct
            # cause lets the user split or shorten their next request
            # instead of helplessly retrying the same prompt.
            if type(exc).__name__ in _STREAM_DROP_EXCEPTIONS:
                return (
                    "The model's streaming response was interrupted before it could "
                    "finish. This usually happens when a single response or tool call "
                    "is very large — please ask the assistant to split the work into "
                    "smaller steps, or shorten the requested output, and try again."
                )
            return "The configured LLM provider is temporarily unavailable after multiple retries. Please wait a moment and continue the conversation."
        return f"LLM request failed: {detail}"

    def _build_user_fallback_message(self, exc: BaseException, reason: str) -> AIMessage:
        return self._build_error_fallback_message(
            self._build_user_message(exc, reason),
            error_type=type(exc).__name__,
            reason=reason,
            detail=_extract_error_detail(exc),
        )

    def _emit_retry_event(self, attempt: int, wait_ms: int, reason: str) -> None:
        try:
            from langgraph.config import get_stream_writer

            writer = get_stream_writer()
            writer(
                {
                    "type": "llm_retry",
                    "attempt": attempt,
                    "max_attempts": self.retry_max_attempts,
                    "wait_ms": wait_ms,
                    "reason": reason,
                    "message": self._build_retry_message(attempt, wait_ms, reason),
                }
            )
        except Exception:
            logger.debug("Failed to emit llm_retry event", exc_info=True)

    @staticmethod
    def _stopped_at_max_tokens(result: ModelCallResult) -> bool:
        """True when the empty response was caused by hitting the output cap.

        Thinking tokens count toward max_tokens on Anthropic-style APIs, so a
        model whose thinking budget is >= max_tokens deterministically burns the
        whole budget on thinking and returns an empty message with
        ``stop_reason=max_tokens`` (OpenAI-style providers report
        ``finish_reason=length``). Retrying cannot change that outcome — the
        same prompt truncates again — so callers short-circuit instead.
        """
        msg: AIMessage | None = None
        if isinstance(result, AIMessage):
            msg = result
        elif isinstance(result, ModelResponse) and result.result:
            first = result.result[0]
            if isinstance(first, AIMessage):
                msg = first
        if msg is None:
            return False

        metadata = msg.response_metadata
        if not isinstance(metadata, dict):
            return False
        return metadata.get("stop_reason") == "max_tokens" or metadata.get("finish_reason") == "length"

    @staticmethod
    def _is_empty_ai_response(result: ModelCallResult) -> bool:
        msg: AIMessage | None = None
        if isinstance(result, AIMessage):
            msg = result
        elif isinstance(result, ModelResponse) and result.result:
            first = result.result[0]
            if isinstance(first, AIMessage):
                msg = first
        if msg is None or msg.tool_calls:
            return False

        content = msg.content
        if isinstance(content, str):
            return not content.strip()
        if isinstance(content, list):
            if not content:
                return True
            for item in content:
                if isinstance(item, str) and item.strip():
                    return False
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        return False
            return True
        return False

    def _empty_retry_delay_ms(self, attempt: int) -> int:
        backoff = self.retry_base_delay_ms * (2 ** max(0, attempt - 1))
        return min(backoff, self.retry_cap_delay_ms)

    def _build_empty_response_fallback_message(self) -> AIMessage:
        return self._build_error_fallback_message(
            _EMPTY_RESPONSE_FALLBACK,
            error_type="EmptyAIResponse",
            reason="empty_response",
            detail="LLM returned empty content without tool calls",
        )

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        if self._check_circuit():
            return self._finalize_error_fallback_message(
                self._build_error_fallback_message(
                    self._build_circuit_breaker_message(),
                    error_type="CircuitBreakerOpen",
                    reason="circuit_open",
                    detail="LLM circuit breaker is open",
                ),
                request,
            )

        attempt = 1
        while True:
            try:
                response = handler(request)
                if self._is_empty_ai_response(response):
                    if self._stopped_at_max_tokens(response):
                        # Deterministic config limit: thinking (counted toward
                        # max_tokens) consumed the entire budget, leaving no
                        # answer. Retrying reproduces the same truncation, so
                        # short-circuit with an actionable message and do not
                        # push the circuit breaker (provider is healthy).
                        logger.warning("Empty AI response caused by max_tokens truncation; not retrying (the same prompt will truncate again). Raise max_tokens or lower the thinking budget.")
                        return self._finalize_error_fallback_message(
                            self._build_error_fallback_message(
                                _MAX_TOKENS_FALLBACK,
                                error_type="MaxTokensTruncation",
                                reason="max_tokens",
                                detail="stop_reason indicates the output cap was hit",
                            ),
                            request,
                        )
                    if attempt < self.retry_max_attempts:
                        wait_ms = self._empty_retry_delay_ms(attempt)
                        logger.warning(
                            "Empty AI response (no content, no tool_calls) on attempt %d/%d; retrying in %dms",
                            attempt,
                            self.retry_max_attempts,
                            wait_ms,
                        )
                        self._emit_retry_event(attempt, wait_ms, "empty_response")
                        time.sleep(wait_ms / 1000)
                        attempt += 1
                        continue
                    logger.warning("LLM returned empty response after %d attempt(s)", attempt)
                    self._record_failure()
                    return self._finalize_error_fallback_message(self._build_empty_response_fallback_message(), request)
                self._record_success()
                return response
            except GraphBubbleUp:
                # Preserve LangGraph control-flow signals (interrupt/pause/resume).
                with self._circuit_lock:
                    if self._circuit_state == "half_open":
                        self._circuit_probe_in_flight = False
                raise
            except Exception as exc:
                retriable, reason = self._classify_error(exc)
                max_attempts = self._max_attempts_for(exc)
                if retriable and attempt < max_attempts:
                    wait_ms = self._build_retry_delay_ms(attempt, exc)
                    logger.warning(
                        "Transient LLM error on attempt %d/%d; retrying in %dms: %s",
                        attempt,
                        self.retry_max_attempts,
                        wait_ms,
                        _extract_error_detail(exc),
                    )
                    self._emit_retry_event(attempt, wait_ms, reason)
                    time.sleep(wait_ms / 1000)
                    attempt += 1
                    continue
                logger.warning(
                    "LLM call failed after %d attempt(s): %s",
                    attempt,
                    _extract_error_detail(exc),
                    exc_info=exc,
                )
                if retriable:
                    self._record_failure()
                return self._finalize_error_fallback_message(self._build_user_fallback_message(exc, reason), request)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        if self._check_circuit():
            return self._finalize_error_fallback_message(
                self._build_error_fallback_message(
                    self._build_circuit_breaker_message(),
                    error_type="CircuitBreakerOpen",
                    reason="circuit_open",
                    detail="LLM circuit breaker is open",
                ),
                request,
            )

        attempt = 1
        while True:
            try:
                response = await handler(request)
                if self._is_empty_ai_response(response):
                    if self._stopped_at_max_tokens(response):
                        # Deterministic config limit: thinking (counted toward
                        # max_tokens) consumed the entire budget, leaving no
                        # answer. Retrying reproduces the same truncation, so
                        # short-circuit with an actionable message and do not
                        # push the circuit breaker (provider is healthy).
                        logger.warning("Empty AI response caused by max_tokens truncation; not retrying (the same prompt will truncate again). Raise max_tokens or lower the thinking budget.")
                        return self._finalize_error_fallback_message(
                            self._build_error_fallback_message(
                                _MAX_TOKENS_FALLBACK,
                                error_type="MaxTokensTruncation",
                                reason="max_tokens",
                                detail="stop_reason indicates the output cap was hit",
                            ),
                            request,
                        )
                    if attempt < self.retry_max_attempts:
                        wait_ms = self._empty_retry_delay_ms(attempt)
                        logger.warning(
                            "Empty AI response (no content, no tool_calls) on attempt %d/%d; retrying in %dms",
                            attempt,
                            self.retry_max_attempts,
                            wait_ms,
                        )
                        self._emit_retry_event(attempt, wait_ms, "empty_response")
                        await asyncio.sleep(wait_ms / 1000)
                        attempt += 1
                        continue
                    logger.warning("LLM returned empty response after %d attempt(s)", attempt)
                    self._record_failure()
                    return self._finalize_error_fallback_message(self._build_empty_response_fallback_message(), request)
                self._record_success()
                return response
            except GraphBubbleUp:
                # Preserve LangGraph control-flow signals (interrupt/pause/resume).
                with self._circuit_lock:
                    if self._circuit_state == "half_open":
                        self._circuit_probe_in_flight = False
                raise
            except Exception as exc:
                retriable, reason = self._classify_error(exc)
                max_attempts = self._max_attempts_for(exc)
                if retriable and attempt < max_attempts:
                    wait_ms = self._build_retry_delay_ms(attempt, exc)
                    logger.warning(
                        "Transient LLM error on attempt %d/%d; retrying in %dms: %s",
                        attempt,
                        self.retry_max_attempts,
                        wait_ms,
                        _extract_error_detail(exc),
                    )
                    self._emit_retry_event(attempt, wait_ms, reason)
                    await asyncio.sleep(wait_ms / 1000)
                    attempt += 1
                    continue
                logger.warning(
                    "LLM call failed after %d attempt(s): %s",
                    attempt,
                    _extract_error_detail(exc),
                    exc_info=exc,
                )
                if retriable:
                    self._record_failure()
                return self._finalize_error_fallback_message(self._build_user_fallback_message(exc, reason), request)


def _matches_any(detail: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in detail for pattern in patterns)


def _extract_error_code(exc: BaseException) -> Any:
    for attr in ("code", "error_code"):
        value = getattr(exc, attr, None)
        if value not in (None, ""):
            return value

    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            for key in ("code", "type"):
                value = error.get(key)
                if value not in (None, ""):
                    return value
    return None


def _extract_status_code(exc: BaseException) -> int | None:
    for attr in ("status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _extract_retry_after_ms(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None

    raw = None
    header_name = ""
    for key in ("retry-after-ms", "Retry-After-Ms", "retry-after", "Retry-After"):
        header_name = key
        if hasattr(headers, "get"):
            raw = headers.get(key)
        if raw:
            break
    if not raw:
        return None

    try:
        multiplier = 1 if "ms" in header_name.lower() else 1000
        return max(0, int(float(raw) * multiplier))
    except (TypeError, ValueError):
        try:
            target = parsedate_to_datetime(str(raw))
            delta = target.timestamp() - time.time()
            return max(0, int(delta * 1000))
        except (TypeError, ValueError, OverflowError):
            return None


def _extract_error_detail(exc: BaseException) -> str:
    detail = str(exc).strip()
    if detail:
        return detail
    message = getattr(exc, "message", None)
    if isinstance(message, str) and message.strip():
        return message.strip()
    return exc.__class__.__name__
