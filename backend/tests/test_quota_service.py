import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.admin.quota_service import QuotaExceeded, QuotaExceededError, QuotaService, quota_exceeded_http_error, quota_period_window
from deerflow.config.quota_control_config import QuotaControlConfig
from deerflow.persistence.base import Base
from deerflow.persistence.quota.model import UserQuotaPeriodRow
from deerflow.persistence.user.model import UserRow


def test_custom_quota_period_uses_full_month_window():
    window = quota_period_window("custom", "2026-05-01")

    assert window.period == "monthly"
    assert window.period_start == datetime(2026, 4, 30, 16, 0, tzinfo=UTC)
    assert window.period_end == datetime(2026, 5, 31, 16, 0, tzinfo=UTC)


def test_quota_exceeded_http_error_shape():
    error = quota_exceeded_http_error(
        QuotaExceededError(
            QuotaExceeded(
                quota_type="model_tokens",
                used=100,
                limit=100,
                period_start=datetime(2026, 6, 30, 16, 0, tzinfo=UTC),
                period_end=datetime(2026, 7, 31, 16, 0, tzinfo=UTC),
            )
        )
    )

    assert error.status_code == 429
    assert error.detail["code"] == "quota_exceeded"
    assert error.detail["exceeded_quota"] == "model_tokens"
    assert error.detail["used"] == 100
    assert error.detail["limit"] == 100
    assert error.detail["period"]["timezone"] == "Asia/Shanghai"


async def _quota_service(tmp_path, *, image_limit: int = 2) -> tuple[QuotaService, async_sessionmaker]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'quota.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    config = QuotaControlConfig.model_validate(
        {
            "enabled": True,
            "defaults": {
                "image_generations": {"enabled": True, "limit_value": image_limit},
            },
        }
    )
    service = QuotaService(sf, config)
    service._test_engine = engine  # type: ignore[attr-defined]
    return service, sf


@pytest.mark.asyncio
async def test_image_generation_consumption_is_atomic_at_limit(tmp_path):
    service, sf = await _quota_service(tmp_path, image_limit=2)
    try:
        results = await asyncio.gather(
            service.consume_image_generations("user-1", count=1),
            service.consume_image_generations("user-1", count=1),
        )
        assert results == [1, 2]

        with pytest.raises(QuotaExceededError) as exc_info:
            await service.consume_image_generations("user-1", count=1)
        assert exc_info.value.exceeded.used == 2
        assert exc_info.value.exceeded.limit == 2

        async with sf() as session:
            used = (await session.execute(select(UserQuotaPeriodRow.image_generations_used))).scalar_one()
        assert used == 2
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_concurrent_period_creation_is_idempotent(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        await asyncio.gather(*(service.ensure_user_period("user-1") for _ in range(5)))

        async with sf() as session:
            row_count = (await session.execute(select(func.count(UserQuotaPeriodRow.id)))).scalar_one()
        assert row_count == 1
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_quota_list_filters_before_pagination_and_reports_filtered_total(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        async with sf() as session:
            session.add_all(
                [
                    UserRow(id="user-1", email="a@example.com", system_role="user"),
                    UserRow(id="user-2", email="b@example.com", system_role="user"),
                    UserRow(id="user-3", email="c@example.com", system_role="user"),
                ]
            )
            await session.commit()
        window = quota_period_window("this_month")
        for user_id in ("user-1", "user-2", "user-3"):
            await service.ensure_user_period(user_id, window=window)
        async with sf() as session:
            row = (await session.execute(select(UserQuotaPeriodRow).where(UserQuotaPeriodRow.user_id == "user-3"))).scalar_one()
            row.model_tokens_used = row.model_tokens_limit
            await session.commit()

        response = await service.list_user_quotas(window=window, status="exceeded", page=1, page_size=1)

        assert response["total"] == 1
        assert [item["user_id"] for item in response["items"]] == ["user-3"]
        assert response["items"][0]["period"]["period_start"].endswith("+00:00")
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_disabled_quota_does_not_create_or_consume_period(tmp_path):
    service, sf = await _quota_service(tmp_path)
    service._config = QuotaControlConfig(enabled=False)
    try:
        assert await service.consume_image_generations("user-1") == 0
        async with sf() as session:
            row_count = (await session.execute(select(func.count(UserQuotaPeriodRow.id)))).scalar_one()
        assert row_count == 0
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_get_user_quota_persists_lazily_created_period(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        async with sf() as session:
            session.add(UserRow(id="user-1", email="a@example.com", system_role="user"))
            await session.commit()

        await service.get_user_quota("user-1", window=quota_period_window("this_month"))

        async with sf() as session:
            row_count = (await session.execute(select(func.count(UserQuotaPeriodRow.id)))).scalar_one()
        assert row_count == 1
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_enforcement_switch_keeps_tracking_without_blocking_new_run(tmp_path):
    service, sf = await _quota_service(tmp_path)
    service._config = QuotaControlConfig.model_validate(
        {
            "enabled": True,
            "enforce_on_run_create": False,
            "defaults": {"model_tokens": {"enabled": True, "limit_value": 0}},
        }
    )
    try:
        await service.check_run_creation("user-1")
        async with sf() as session:
            row_count = (await session.execute(select(func.count(UserQuotaPeriodRow.id)))).scalar_one()
        assert row_count == 1
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_config_model_switches_override_enabled_user_period(tmp_path):
    service, sf = await _quota_service(tmp_path)
    service._config = QuotaControlConfig.model_validate(
        {
            "enabled": True,
            "defaults": {
                "model_tokens": {"enabled": False, "limit_value": 0},
                "model_requests": {"enabled": False, "limit_value": 0},
            },
        }
    )
    try:
        await service.ensure_user_period("user-1")
        async with sf() as session:
            row = (await session.execute(select(UserQuotaPeriodRow))).scalar_one()
            row.model_tokens_enabled = True
            row.model_tokens_limit = 0
            row.model_tokens_used = 1
            row.model_requests_enabled = True
            row.model_requests_limit = 0
            row.model_requests_used = 1
            await session.commit()

        await service.check_run_creation("user-1")

        async with sf() as session:
            row = (await session.execute(select(UserQuotaPeriodRow))).scalar_one()
        assert row.model_tokens_enabled is True
        assert row.model_requests_enabled is True
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_disabled_config_image_switch_tracks_without_enforcing_user_limit(tmp_path):
    service, sf = await _quota_service(tmp_path, image_limit=0)
    service._config = QuotaControlConfig.model_validate(
        {
            "enabled": True,
            "defaults": {"image_generations": {"enabled": False, "limit_value": 0}},
        }
    )
    try:
        await service.ensure_user_period("user-1")
        async with sf() as session:
            row = (await session.execute(select(UserQuotaPeriodRow))).scalar_one()
            row.image_generations_enabled = True
            row.image_generations_limit = 0
            await session.commit()

        assert await service.consume_image_generations("user-1") == 1

        async with sf() as session:
            row = (await session.execute(select(UserQuotaPeriodRow))).scalar_one()
        assert row.image_generations_used == 1
        assert row.image_generations_enabled is True
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]
