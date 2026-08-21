import asyncio
import threading

import pytest

from app.gateway.admin.quota_runtime import QuotaRuntimeBridge
from app.gateway.admin.quota_service import (
    ImageQuotaReservation,
    ModelQuotaReservation,
    VideoQuotaReservation,
)


class _Service:
    def __init__(self) -> None:
        self.thread_ids: list[int] = []

    async def reserve_model_request(self, user_id: str, model: str):
        self.thread_ids.append(threading.get_ident())
        return ModelQuotaReservation("r1", user_id, model, "scope", "period", True, 1)

    async def record_model_tokens(self, reservation, total_tokens: int):
        self.thread_ids.append(threading.get_ident())

    async def release_undispatched_model_request(self, reservation):
        self.thread_ids.append(threading.get_ident())

    async def reserve_image_generations(self, user_id: str, *, count: int):
        self.thread_ids.append(threading.get_ident())
        return ImageQuotaReservation("i1", user_id, "scope", "period", count, count)

    async def release_image_generations(self, reservation):
        self.thread_ids.append(threading.get_ident())

    async def reserve_video_generations(self, user_id: str, *, count: int):
        self.thread_ids.append(threading.get_ident())
        return VideoQuotaReservation("v1", user_id, "scope", "period", count, count)

    async def release_video_generations(self, reservation):
        self.thread_ids.append(threading.get_ident())


@pytest.mark.asyncio
async def test_subagent_bridge_schedules_database_work_on_parent_loop():
    service = _Service()
    parent_thread = threading.get_ident()
    bridge = QuotaRuntimeBridge(service, "user-1", owner_loop=asyncio.get_running_loop())  # type: ignore[arg-type]

    result = await asyncio.to_thread(lambda: asyncio.run(bridge.reserve_model_request("claude-sonnet-4")))

    assert result["allowed"] is True
    assert service.thread_ids == [parent_thread]


@pytest.mark.asyncio
async def test_subagent_bridge_schedules_video_reserve_on_parent_loop():
    service = _Service()
    parent_thread = threading.get_ident()
    bridge = QuotaRuntimeBridge(service, "user-1", owner_loop=asyncio.get_running_loop())  # type: ignore[arg-type]

    result = await asyncio.to_thread(lambda: asyncio.run(bridge.reserve_video_generations(2)))

    assert result["allowed"] is True
    assert result["used"] == 2
    assert service.thread_ids == [parent_thread]


@pytest.mark.asyncio
async def test_bridge_fails_closed_when_parent_loop_is_unavailable():
    loop = asyncio.new_event_loop()
    bridge = QuotaRuntimeBridge(_Service(), "user-1", owner_loop=loop)  # type: ignore[arg-type]
    try:
        with pytest.raises(RuntimeError, match="unavailable"):
            await bridge.reserve_model_request("claude-sonnet-4")
    finally:
        loop.close()
