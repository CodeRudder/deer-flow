import pytest
from langchain_core.messages import AIMessage

from deerflow.agents.middlewares.quota_model_middleware import QuotaModelMiddleware


class _Runtime:
    def __init__(self, bridge, *, required: bool):
        self.context = {"__quota_enforcement_required": required}
        if bridge is not None:
            self.context["__quota_runtime_bridge"] = bridge


class _Request:
    def __init__(self, bridge, *, required: bool = True):
        self.runtime = _Runtime(bridge, required=required)


class _Bridge:
    def __init__(self, *, allowed: bool = True):
        self.allowed = allowed
        self.recorded_tokens: list[int] = []
        self.released = 0

    async def reserve_model_request(self, model: str):
        if not self.allowed:
            return {
                "allowed": False,
                "code": "quota_exceeded",
                "message": "本周高级模型额度已用尽",
                "metric": "model_requests",
                "model": model,
                "used": 2,
                "limit": 2,
            }
        return {"allowed": True, "reservation": {"id": "r1"}}

    async def record_model_tokens(self, reservation, total_tokens: int):
        self.recorded_tokens.append(total_tokens)

    async def release_undispatched_model_request(self, reservation):
        self.released += 1


@pytest.mark.asyncio
async def test_quota_middleware_blocks_provider_handler_when_exhausted():
    bridge = _Bridge(allowed=False)
    middleware = QuotaModelMiddleware(model_id="claude-sonnet-4")
    called = False

    async def handler(_request):
        nonlocal called
        called = True
        return AIMessage(content="provider response")

    result = await middleware.awrap_model_call(_Request(bridge), handler)  # type: ignore[arg-type]

    assert called is False
    assert isinstance(result, AIMessage)
    assert result.additional_kwargs["deerflow_quota"]["code"] == "quota_exceeded"


@pytest.mark.asyncio
async def test_quota_middleware_fails_closed_when_required_bridge_is_missing():
    middleware = QuotaModelMiddleware(model_id="claude-sonnet-4")
    called = False

    async def handler(_request):
        nonlocal called
        called = True
        return AIMessage(content="provider response")

    result = await middleware.awrap_model_call(_Request(None), handler)  # type: ignore[arg-type]

    assert called is False
    assert result.additional_kwargs["deerflow_quota"]["code"] == "quota_service_unavailable"


@pytest.mark.asyncio
async def test_quota_middleware_skips_accounting_outside_billable_gateway_runs():
    middleware = QuotaModelMiddleware(model_id="claude-sonnet-4")
    called = False

    async def handler(_request):
        nonlocal called
        called = True
        return AIMessage(content="provider response")

    result = await middleware.awrap_model_call(_Request(None, required=False), handler)  # type: ignore[arg-type]

    assert called is True
    assert isinstance(result, AIMessage)


@pytest.mark.asyncio
async def test_quota_middleware_records_tokens_and_keeps_request_on_provider_error():
    bridge = _Bridge()
    middleware = QuotaModelMiddleware(model_id="claude-sonnet-4")

    async def successful(_request):
        return AIMessage(
            content="ok",
            usage_metadata={"input_tokens": 7, "output_tokens": 5, "total_tokens": 12},
        )

    result = await middleware.awrap_model_call(_Request(bridge), successful)  # type: ignore[arg-type]
    assert isinstance(result, AIMessage)
    assert bridge.recorded_tokens == [12]

    async def provider_error(_request):
        raise RuntimeError("provider failed after dispatch")

    with pytest.raises(RuntimeError):
        await middleware.awrap_model_call(_Request(bridge), provider_error)  # type: ignore[arg-type]
    assert bridge.released == 0
