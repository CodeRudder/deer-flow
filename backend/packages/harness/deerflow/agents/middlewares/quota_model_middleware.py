"""Per-user model-group request enforcement at the provider call boundary."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, override
from uuid import uuid4

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)
_BRIDGE_CONTEXT_KEY = "__quota_runtime_bridge"
_REQUIRED_CONTEXT_KEY = "__quota_enforcement_required"


def _context_from_request(request: ModelRequest) -> dict[str, Any]:
    runtime = getattr(request, "runtime", None)
    context = getattr(runtime, "context", None)
    return context if isinstance(context, dict) else {}


def _bridge_from_request(request: ModelRequest) -> Any | None:
    return _context_from_request(request).get(_BRIDGE_CONTEXT_KEY)


def _first_ai_message(result: ModelCallResult) -> AIMessage | None:
    if isinstance(result, AIMessage):
        return result
    if isinstance(result, ModelResponse):
        return next((item for item in result.result if isinstance(item, AIMessage)), None)
    return None


def _total_tokens(result: ModelCallResult) -> int:
    message = _first_ai_message(result)
    usage = getattr(message, "usage_metadata", None) if message is not None else None
    if not isinstance(usage, dict):
        return 0
    total = usage.get("total_tokens")
    if total is None:
        total = int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
    return max(0, int(total or 0))


def _quota_message(payload: dict[str, Any]) -> AIMessage:
    normalized = {
        "type": payload.get("type", "quota_exceeded"),
        "code": payload.get("code", "quota_exceeded"),
        "message": payload.get("message", "当前模型组额度已用尽"),
        "scope": payload.get("scope"),
        "metric": payload.get("metric", "model_requests"),
        "model": payload.get("model"),
        "period": payload.get("period"),
        "used": payload.get("used"),
        "limit": payload.get("limit"),
        "retryable": False,
    }
    try:
        from langgraph.config import get_stream_writer

        get_stream_writer()(normalized)
    except Exception:
        logger.debug("Failed to emit quota runtime event", exc_info=True)
    return AIMessage(
        id=f"deerflow:quota:{uuid4()}",
        content=str(normalized["message"]),
        additional_kwargs={"deerflow_quota": normalized},
    )


class QuotaModelMiddleware(AgentMiddleware[AgentState]):
    """Reserve one model request before each actual provider handler call."""

    def __init__(self, *, model_id: str) -> None:
        super().__init__()
        self.model_id = model_id

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        bridge = _bridge_from_request(request)
        if bridge is None:
            if not _context_from_request(request).get(_REQUIRED_CONTEXT_KEY, False):
                return handler(request)
            return _quota_message(
                {
                    "type": "quota_service_unavailable",
                    "code": "quota_service_unavailable",
                    "message": "额度服务上下文不可用，请稍后重试",
                    "model": self.model_id,
                }
            )
        return _quota_message(
            {
                "type": "quota_service_unavailable",
                "code": "quota_service_unavailable",
                "message": "额度服务仅支持异步模型调用，请稍后重试",
                "model": self.model_id,
            }
        )

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        bridge = _bridge_from_request(request)
        if bridge is None:
            if not _context_from_request(request).get(_REQUIRED_CONTEXT_KEY, False):
                return await handler(request)
            return _quota_message(
                {
                    "type": "quota_service_unavailable",
                    "code": "quota_service_unavailable",
                    "message": "额度服务上下文不可用，请稍后重试",
                    "model": self.model_id,
                }
            )
        try:
            result = await bridge.reserve_model_request(self.model_id)
        except Exception:  # noqa: BLE001 - quota persistence is fail-closed
            logger.exception("Failed to reserve model quota for %s", self.model_id)
            return _quota_message(
                {
                    "type": "quota_service_unavailable",
                    "code": "quota_service_unavailable",
                    "message": "额度服务暂时不可用，请稍后重试",
                    "model": self.model_id,
                }
            )
        if not isinstance(result, dict) or not result.get("allowed", False):
            return _quota_message(result if isinstance(result, dict) else {})

        reservation = result.get("reservation")
        # Entering the provider handler is DeerFlow's observable dispatch boundary.
        response = await handler(request)
        total_tokens = _total_tokens(response)
        if reservation is not None and total_tokens > 0:
            try:
                await bridge.record_model_tokens(reservation, total_tokens)
            except Exception:  # noqa: BLE001 - dispatch already happened; preserve response
                logger.exception("Failed to record model token observation for %s", self.model_id)
        return response


__all__ = ["QuotaModelMiddleware"]
