from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import AIMessage
from langgraph.errors import GraphBubbleUp

from deerflow.agents.middlewares.llm_error_handling_middleware import (
    LLMErrorHandlingMiddleware,
    _EMPTY_RESPONSE_FALLBACK,
)


class FakeError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        headers: dict[str, str] | None = None,
        body: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.body = body
        self.response = SimpleNamespace(status_code=status_code, headers=headers or {}) if status_code is not None or headers else None


def _build_middleware(**attrs: int) -> LLMErrorHandlingMiddleware:
    middleware = LLMErrorHandlingMiddleware()
    for key, value in attrs.items():
        setattr(middleware, key, value)
    return middleware


def test_async_model_call_retries_busy_provider_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    middleware = _build_middleware(retry_max_attempts=3, retry_base_delay_ms=25, retry_cap_delay_ms=25)
    attempts = 0
    waits: list[float] = []
    events: list[dict] = []

    async def fake_sleep(delay: float) -> None:
        waits.append(delay)

    def fake_writer():
        return events.append

    async def handler(_request) -> AIMessage:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise FakeError("当前服务集群负载较高，请稍后重试，感谢您的耐心等待。 (2064)")
        return AIMessage(content="ok")

    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    monkeypatch.setattr(
        "langgraph.config.get_stream_writer",
        fake_writer,
    )

    result = asyncio.run(middleware.awrap_model_call(SimpleNamespace(), handler))

    assert isinstance(result, AIMessage)
    assert result.content == "ok"
    assert attempts == 3
    assert waits == [0.025, 0.025]
    assert [event["type"] for event in events] == ["llm_retry", "llm_retry"]


def test_async_model_call_returns_user_message_for_quota_errors() -> None:
    middleware = _build_middleware(retry_max_attempts=3)

    async def handler(_request) -> AIMessage:
        raise FakeError(
            "insufficient_quota: account balance is empty",
            status_code=429,
            code="insufficient_quota",
        )

    result = asyncio.run(middleware.awrap_model_call(SimpleNamespace(), handler))

    assert isinstance(result, AIMessage)
    assert "out of quota" in str(result.content)


def test_sync_model_call_uses_retry_after_header(monkeypatch: pytest.MonkeyPatch) -> None:
    middleware = _build_middleware(retry_max_attempts=2, retry_base_delay_ms=10, retry_cap_delay_ms=10)
    waits: list[float] = []
    attempts = 0

    def fake_sleep(delay: float) -> None:
        waits.append(delay)

    def handler(_request) -> AIMessage:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise FakeError(
                "server busy",
                status_code=503,
                headers={"Retry-After": "2"},
            )
        return AIMessage(content="ok")

    monkeypatch.setattr("time.sleep", fake_sleep)

    result = middleware.wrap_model_call(SimpleNamespace(), handler)

    assert isinstance(result, AIMessage)
    assert result.content == "ok"
    assert waits == [2.0]


def test_sync_model_call_propagates_graph_bubble_up() -> None:
    middleware = _build_middleware()

    def handler(_request) -> AIMessage:
        raise GraphBubbleUp()

    with pytest.raises(GraphBubbleUp):
        middleware.wrap_model_call(SimpleNamespace(), handler)


def test_async_model_call_propagates_graph_bubble_up() -> None:
    middleware = _build_middleware()

    async def handler(_request) -> AIMessage:
        raise GraphBubbleUp()

    with pytest.raises(GraphBubbleUp):
        asyncio.run(middleware.awrap_model_call(SimpleNamespace(), handler))


# ---------------------------------------------------------------------------
# Transient network error patterns (e.g. litellm 400 "网络错误")
# ---------------------------------------------------------------------------


def test_retries_400_with_network_error_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """400 + '网络错误' is retriable (litellm proxy returning network errors as 400)."""
    middleware = _build_middleware(retry_max_attempts=3, retry_base_delay_ms=10, retry_cap_delay_ms=10)
    attempts = 0
    waits: list[float] = []

    def fake_sleep(delay: float) -> None:
        waits.append(delay)

    def handler(_request) -> AIMessage:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise FakeError("litellm.BadRequestError: AnthropicException - 网络错误", status_code=400)
        return AIMessage(content="recovered")

    monkeypatch.setattr("time.sleep", fake_sleep)

    result = middleware.wrap_model_call(SimpleNamespace(), handler)

    assert isinstance(result, AIMessage)
    assert result.content == "recovered"
    assert attempts == 3


def test_retries_400_with_connection_error_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """400 + 'Connection reset' is retriable."""
    middleware = _build_middleware(retry_max_attempts=2, retry_base_delay_ms=10, retry_cap_delay_ms=10)
    attempts = 0

    def fake_sleep(delay: float) -> None:
        pass

    async def handler(_request) -> AIMessage:
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise FakeError("Connection reset by peer", status_code=400)
        return AIMessage(content="ok")

    async def fake_sleep(delay: float) -> None:
        pass

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    result = asyncio.run(middleware.awrap_model_call(SimpleNamespace(), handler))

    assert result.content == "ok"
    assert attempts == 2


def test_does_not_retry_400_with_non_transient_message() -> None:
    """400 without transient patterns is NOT retried."""
    middleware = _build_middleware(retry_max_attempts=3, retry_base_delay_ms=10)

    async def handler(_request) -> AIMessage:
        raise FakeError("Invalid request: max_tokens must be positive", status_code=400)

    result = asyncio.run(middleware.awrap_model_call(SimpleNamespace(), handler))

    assert isinstance(result, AIMessage)
    assert "LLM request failed" in result.content


# ---------------------------------------------------------------------------
# Empty AI response retry
# ---------------------------------------------------------------------------


def test_sync_retries_empty_string_response_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty string content with no tool_calls triggers retry."""
    middleware = _build_middleware(retry_max_attempts=3, retry_base_delay_ms=10, retry_cap_delay_ms=10)
    attempts = 0
    waits: list[float] = []

    def fake_sleep(delay: float) -> None:
        waits.append(delay)

    def handler(_request) -> AIMessage:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return AIMessage(content="")
        return AIMessage(content="done")

    monkeypatch.setattr("time.sleep", fake_sleep)

    result = middleware.wrap_model_call(SimpleNamespace(), handler)

    assert result.content == "done"
    assert attempts == 3
    assert len(waits) == 2


def test_async_retries_empty_list_response_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty list content with no tool_calls triggers retry."""
    middleware = _build_middleware(retry_max_attempts=2, retry_base_delay_ms=10, retry_cap_delay_ms=10)
    attempts = 0

    async def fake_sleep(delay: float) -> None:
        pass

    async def handler(_request) -> AIMessage:
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            return AIMessage(content=[])
        return AIMessage(content="ok")

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    result = asyncio.run(middleware.awrap_model_call(SimpleNamespace(), handler))

    assert result.content == "ok"
    assert attempts == 2


def test_sync_returns_fallback_after_all_empty_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returns fallback message when all retries return empty."""
    middleware = _build_middleware(retry_max_attempts=3, retry_base_delay_ms=10, retry_cap_delay_ms=10)

    def fake_sleep(delay: float) -> None:
        pass

    def handler(_request) -> AIMessage:
        return AIMessage(content="")

    monkeypatch.setattr("time.sleep", fake_sleep)

    result = middleware.wrap_model_call(SimpleNamespace(), handler)

    assert isinstance(result, AIMessage)
    assert result.content == _EMPTY_RESPONSE_FALLBACK


def test_does_not_retry_response_with_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Response with tool_calls is NOT considered empty."""
    middleware = _build_middleware(retry_max_attempts=3, retry_base_delay_ms=10)
    attempts = 0

    def fake_sleep(delay: float) -> None:
        pass

    def handler(_request) -> AIMessage:
        nonlocal attempts
        attempts += 1
        return AIMessage(content="", tool_calls=[{"name": "bash", "args": {"command": "ls"}, "id": "1", "type": "tool_call"}])

    monkeypatch.setattr("time.sleep", fake_sleep)

    result = middleware.wrap_model_call(SimpleNamespace(), handler)

    assert result.tool_calls
    assert attempts == 1  # No retry


def test_does_not_retry_response_with_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Response with non-empty content is NOT considered empty."""
    middleware = _build_middleware(retry_max_attempts=3, retry_base_delay_ms=10)
    attempts = 0

    async def fake_sleep(delay: float) -> None:
        pass

    async def handler(_request) -> AIMessage:
        nonlocal attempts
        attempts += 1
        return AIMessage(content="some response")

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    result = asyncio.run(middleware.awrap_model_call(SimpleNamespace(), handler))

    assert result.content == "some response"
    assert attempts == 1


def test_retries_modelresponse_with_empty_ai_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ModelResponse wrapping an empty AIMessage triggers retry."""
    middleware = _build_middleware(retry_max_attempts=2, retry_base_delay_ms=10, retry_cap_delay_ms=10)
    attempts = 0

    def fake_sleep(delay: float) -> None:
        pass

    def handler(_request) -> ModelResponse:
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            return ModelResponse(result=[AIMessage(content="")])
        return ModelResponse(result=[AIMessage(content="done")])

    monkeypatch.setattr("time.sleep", fake_sleep)

    result = middleware.wrap_model_call(SimpleNamespace(), handler)

    assert isinstance(result, ModelResponse)
    assert result.result[0].content == "done"
    assert attempts == 2
