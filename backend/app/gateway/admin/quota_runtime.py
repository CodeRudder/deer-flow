"""Thread-safe runtime bridge from harness loops to the Gateway quota service."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from typing import Any, TypeVar

from app.gateway.admin.quota_service import (
    ImageQuotaReservation,
    ModelQuotaReservation,
    QuotaExceededError,
    QuotaService,
    quota_exceeded_payload,
)

T = TypeVar("T")


class QuotaRuntimeBridge:
    """Expose quota operations without leaking sessions or engines to subagents."""

    def __init__(
        self,
        service: QuotaService,
        user_id: str,
        *,
        owner_loop: asyncio.AbstractEventLoop,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._service = service
        self._user_id = user_id
        self._owner_loop = owner_loop
        self._timeout_seconds = timeout_seconds

    async def _call(self, factory: Callable[[], Awaitable[T]]) -> T:
        current_loop = asyncio.get_running_loop()
        if current_loop is self._owner_loop:
            return await asyncio.wait_for(factory(), timeout=self._timeout_seconds)
        if self._owner_loop.is_closed() or not self._owner_loop.is_running():
            raise RuntimeError("Parent quota event loop is unavailable")
        future: Future[T] = asyncio.run_coroutine_threadsafe(factory(), self._owner_loop)
        try:
            return await asyncio.wait_for(asyncio.wrap_future(future), timeout=self._timeout_seconds)
        except BaseException:
            future.cancel()
            raise

    async def reserve_model_request(self, model: str) -> dict[str, Any]:
        try:
            reservation = await self._call(lambda: self._service.reserve_model_request(self._user_id, model))
        except QuotaExceededError as exc:
            return {"allowed": False, "type": "quota_exceeded", **quota_exceeded_payload(exc.exceeded)}
        return {
            "allowed": True,
            "reservation": reservation,
            "used": reservation.request_used,
        }

    async def record_model_tokens(self, reservation: ModelQuotaReservation, total_tokens: int) -> None:
        await self._call(lambda: self._service.record_model_tokens(reservation, total_tokens))

    async def release_undispatched_model_request(self, reservation: ModelQuotaReservation) -> None:
        await self._call(lambda: self._service.release_undispatched_model_request(reservation))

    async def reserve_image_generations(self, count: int) -> dict[str, Any]:
        try:
            reservation = await self._call(lambda: self._service.reserve_image_generations(self._user_id, count=count))
        except QuotaExceededError as exc:
            return {"allowed": False, "type": "quota_exceeded", **quota_exceeded_payload(exc.exceeded)}
        return {
            "allowed": True,
            "reservation": reservation,
            "used": reservation.image_used,
        }

    async def release_image_generations(self, reservation: ImageQuotaReservation) -> None:
        await self._call(lambda: self._service.release_image_generations(reservation))


__all__ = ["QuotaRuntimeBridge"]
