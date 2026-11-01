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
    VideoPointsReservation,
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
        run_id: str | None = None,
        thread_id: str | None = None,
    ) -> None:
        self._service = service
        self._user_id = user_id
        self._owner_loop = owner_loop
        self._timeout_seconds = timeout_seconds
        self._run_id = run_id
        self._thread_id = thread_id

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

    async def reserve_video_generation(
        self,
        *,
        model: str | None,
        resolution: str | None,
        duration_seconds: int | None,
        idempotency_key: str | None,
        output_file: str | None = None,
        provider: str | None = None,
        operation: str = "generation",
    ) -> dict[str, Any]:
        """Reserve one point-billed video request."""
        try:
            reservation = await self._call(
                lambda: self._service.reserve_video_generation(
                    self._user_id,
                    model=model,
                    resolution=resolution,
                    duration_seconds=duration_seconds,
                    idempotency_key=idempotency_key,
                    provider=provider,
                    operation=operation,
                    run_id=self._run_id,
                    thread_id=self._thread_id,
                    output_file=output_file,
                )
            )
        except QuotaExceededError as exc:
            return {"allowed": False, "type": "quota_exceeded", **quota_exceeded_payload(exc.exceeded)}
        if getattr(reservation, "reused", False):
            return {
                "allowed": False,
                "type": "idempotent_replay",
                "message": "该视频生成请求已存在，未重复提交 Provider",
                "reservation": reservation,
            }
        return {
            "allowed": True,
            "reservation": reservation,
            "used": reservation.video_used,
            "billing_mode": "points",
            "unit": "points",
            "scale": 100,
            "reserved_points": reservation.reserved_minor_units / 100,
        }

    async def reserve_video_points(
        self,
        *,
        model: str,
        resolution: str,
        duration_seconds: int,
        idempotency_key: str | None,
        output_file: str | None = None,
        provider: str | None = None,
    ) -> dict[str, Any]:
        """Explicit point-mode reservation helper for callers that need it."""
        try:
            reservation = await self._call(
                lambda: self._service.reserve_video_points(
                    self._user_id,
                    model=model,
                    resolution=resolution,
                    duration_seconds=duration_seconds,
                    idempotency_key=idempotency_key,
                    provider=provider,
                    run_id=self._run_id,
                    thread_id=self._thread_id,
                    output_file=output_file,
                )
            )
        except QuotaExceededError as exc:
            return {"allowed": False, "type": "quota_exceeded", **quota_exceeded_payload(exc.exceeded)}
        if getattr(reservation, "reused", False):
            return {
                "allowed": False,
                "type": "idempotent_replay",
                "message": "该视频生成请求已存在，未重复提交 Provider",
                "reservation": reservation,
            }
        return {
            "allowed": True,
            "reservation": reservation,
            "used": reservation.video_used,
            "billing_mode": "points",
            "unit": "points",
            "scale": 100,
            "reserved_points": reservation.reserved_minor_units / 100,
        }

    async def settle_video_points(
        self,
        reservation: VideoPointsReservation | str,
        *,
        usage_period_id: str | None = None,
        provider_task_id: str | None = None,
        billable_duration_seconds: int | None = None,
    ) -> dict[str, Any]:
        return await self._call(
            lambda: self._service.settle_video_points(
                reservation,
                usage_period_id=usage_period_id,
                provider_task_id=provider_task_id,
                billable_duration_seconds=billable_duration_seconds,
                user_id=self._user_id,
            )
        )

    async def release_video_points(
        self,
        reservation: VideoPointsReservation | str,
        *,
        usage_period_id: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        return await self._call(
            lambda: self._service.release_video_points(
                reservation,
                usage_period_id=usage_period_id,
                reason=reason,
                user_id=self._user_id,
            )
        )

    async def mark_video_points_pending(
        self,
        reservation: VideoPointsReservation | str,
        *,
        usage_period_id: str | None = None,
        provider_task_id: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        return await self._call(
            lambda: self._service.mark_video_points_pending(
                reservation,
                usage_period_id=usage_period_id,
                provider_task_id=provider_task_id,
                reason=reason,
                user_id=self._user_id,
            )
        )


__all__ = ["QuotaRuntimeBridge"]
