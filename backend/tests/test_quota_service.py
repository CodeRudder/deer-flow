import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.admin.quota_service import QuotaExceededError, QuotaService, VideoPointsReservation, quota_exceeded_payload
from deerflow.persistence.base import Base
from deerflow.persistence.quota.model import QuotaScopeRow, UserQuotaUsagePeriodRow
from deerflow.persistence.user.model import UserRow


async def _quota_service(tmp_path) -> tuple[QuotaService, async_sessionmaker]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'quota.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    service = QuotaService(sf, configured_models=["claude-sonnet-4", "qwen-max"])
    service._test_engine = engine  # type: ignore[attr-defined]
    return service, sf


async def _create_claude_scope(service: QuotaService, *, limit: int = 2, daily: dict | None = None) -> dict:
    payload = {
        "code": "claude_advanced",
        "name": "Claude 高级模型",
        "resource_type": "model",
        "match_rules": {"exact": [], "prefix": ["claude-"]},
        "period_type": "weekly",
        "request_enforced": True,
        "request_limit": limit,
        "enabled": True,
    }
    if daily is not None:
        payload["requests_daily"] = daily
    return await service.create_scope(payload, updated_by="admin-1")


async def _create_image_scope(service: QuotaService, *, limit: int = 2, daily: dict | None = None) -> dict:
    payload = {
        "code": "image_generation",
        "name": "生图资源",
        "resource_type": "image_generation",
        "match_rules": {"exact": [], "prefix": []},
        "period_type": "weekly",
        "image_enforced": True,
        "image_limit": limit,
        "enabled": True,
    }
    if daily is not None:
        payload["images_daily"] = daily
    return await service.create_scope(payload, updated_by="admin-1")


async def _create_video_scope(service: QuotaService, *, limit: int | float = 100, daily: dict | None = None) -> dict:
    payload = {
        "code": "video_generation",
        "name": "视频资源",
        "resource_type": "video_generation",
        "match_rules": {"exact": [], "prefix": []},
        "period_type": "weekly",
        "video_enforced": True,
        "video_limit": limit,
        "video_billing_rules": {
            "currency": "CNY",
            "point_to_yuan": 1,
            "models": {"seedance-2.5": {"1080p": 3.5}},
        },
        "enabled": True,
    }
    if daily is not None:
        payload["videos_daily"] = daily
    return await service.create_scope(payload, updated_by="admin-1")


@pytest.mark.asyncio
async def test_model_request_reservation_is_atomic_and_tokens_are_observation_only(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        await _create_claude_scope(service, limit=2)

        reservations = await asyncio.gather(
            service.reserve_model_request("user-1", "claude-sonnet-4"),
            service.reserve_model_request("user-1", "claude-sonnet-4"),
        )
        with pytest.raises(QuotaExceededError) as exc_info:
            await service.reserve_model_request("user-1", "claude-sonnet-4")
        assert exc_info.value.exceeded.metric == "model_requests"
        assert exc_info.value.exceeded.used == 2

        await asyncio.gather(
            service.record_model_tokens(reservations[0], 120),
            service.record_model_tokens(reservations[1], 80),
        )
        async with sf() as session:
            row = (await session.execute(select(UserQuotaUsagePeriodRow))).scalar_one()
        assert row.request_used == 2
        assert row.token_used == 200
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_model_scope_rules_are_loaded_once_per_quota_service(tmp_path):
    service, _ = await _quota_service(tmp_path)
    try:
        await _create_claude_scope(service, limit=10)
        scope_selects = 0

        def count_scope_selects(_conn, _cursor, statement, _parameters, _context, _executemany):
            nonlocal scope_selects
            normalized = " ".join(statement.lower().split())
            if normalized.startswith("select") and " from quota_scopes " in normalized:
                scope_selects += 1

        event.listen(service._test_engine.sync_engine, "before_cursor_execute", count_scope_selects)  # type: ignore[attr-defined]
        try:
            await asyncio.gather(
                service.reserve_model_request("user-1", "claude-sonnet-4"),
                service.reserve_model_request("user-1", "claude-sonnet-4"),
            )
            await service.reserve_model_request("user-1", "qwen-max")
            await service.reserve_model_request("user-1", "qwen-max")
        finally:
            event.remove(service._test_engine.sync_engine, "before_cursor_execute", count_scope_selects)  # type: ignore[attr-defined]

        assert scope_selects == 1
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_unmatched_model_is_not_blocked_or_written(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        await _create_claude_scope(service, limit=0)

        reservation = await service.reserve_model_request("user-1", "qwen-max")

        assert reservation.matched_scope_id is None
        async with sf() as session:
            count = (await session.execute(select(func.count(UserQuotaUsagePeriodRow.id)))).scalar_one()
        assert count == 0
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_scope_default_update_preserves_usage_and_protects_override(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        scope = await _create_claude_scope(service, limit=10)
        async with sf() as session:
            session.add_all(
                [
                    UserRow(id="user-1", email="one@example.com", system_role="user"),
                    UserRow(id="user-2", email="two@example.com", system_role="user"),
                ]
            )
            await session.commit()
        await service.reserve_model_request("user-1", "claude-sonnet-4")
        await service.reserve_model_request("user-2", "claude-sonnet-4")
        await service.override_user_current_period(
            "user-2",
            scope["id"],
            request_enforced=True,
            request_limit=30,
            image_enforced=None,
            image_limit=None,
            reason="temporary",
            updated_by="admin-1",
        )

        await service.update_scope(
            scope["id"],
            {
                "name": scope["name"],
                "match_rules": scope["match_rules"],
                "period_type": "weekly",
                "request_enforced": True,
                "request_limit": 20,
                "enabled": True,
            },
            updated_by="admin-2",
        )

        async with sf() as session:
            rows = {row.user_id: row for row in (await session.execute(select(UserQuotaUsagePeriodRow))).scalars()}
        assert rows["user-1"].request_limit_snapshot == 20
        assert rows["user-1"].request_used == 1
        assert rows["user-1"].is_overridden is False
        assert rows["user-2"].request_limit_snapshot == 30
        assert rows["user-2"].request_used == 1
        assert rows["user-2"].is_overridden is True

        restored = await service.restore_user_current_period("user-2", scope["id"])
        assert restored["requests"]["limit"] == 20
        assert restored["requests"]["used"] == 1
        assert restored["source"] == "scope_default"
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_scope_metadata_update_does_not_bump_policy_or_rewrite_snapshot(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        scope = await _create_claude_scope(service, limit=10)
        await service.reserve_model_request("user-1", "claude-sonnet-4")
        async with sf() as session:
            before = (await session.execute(select(UserQuotaUsagePeriodRow))).scalar_one()
            before_updated_at = before.updated_at

        updated = await service.update_scope(
            scope["id"],
            {
                "name": "Claude 模型组（重命名）",
                "match_rules": scope["match_rules"],
                "period_type": "weekly",
                "request_enforced": True,
                "request_limit": 10,
                "enabled": True,
            },
            updated_by="admin-2",
        )

        async with sf() as session:
            row = (await session.execute(select(UserQuotaUsagePeriodRow))).scalar_one()
        assert updated["default_policy"]["policy_version"] == 1
        assert row.scope_policy_version_snapshot == 1
        assert row.updated_at == before_updated_at
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_identical_scope_update_is_a_noop(tmp_path):
    service, _ = await _quota_service(tmp_path)
    try:
        scope = await _create_claude_scope(service, limit=10)

        updated = await service.update_scope(
            scope["id"],
            {
                "name": scope["name"],
                "match_rules": scope["match_rules"],
                "period_type": "weekly",
                "request_enforced": True,
                "request_limit": 10,
                "enabled": True,
            },
            updated_by="admin-2",
        )

        assert updated["default_policy"]["policy_version"] == 1
        assert updated["updated_by"] == "admin-1"
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_new_week_resets_usage_and_does_not_inherit_override(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        scope = await _create_claude_scope(service, limit=10)
        async with sf() as session:
            session.add(UserRow(id="user-1", email="one@example.com", system_role="user"))
            await session.commit()
        first_week = datetime(2026, 7, 15, tzinfo=UTC)
        next_week = datetime(2026, 7, 22, tzinfo=UTC)
        await service.reserve_model_request("user-1", "claude-sonnet-4", at=first_week)
        await service.override_user_current_period(
            "user-1",
            scope["id"],
            request_enforced=True,
            request_limit=99,
            image_enforced=None,
            image_limit=None,
            reason=None,
            updated_by="admin-1",
            at=first_week,
        )

        reservation = await service.reserve_model_request("user-1", "claude-sonnet-4", at=next_week)

        assert reservation.request_used == 1
        detail = await service.get_user_quota("user-1", at=next_week)
        item = next(item for item in detail["items"] if item["scope"]["id"] == scope["id"])
        assert item["requests"]["limit"] == 10
        assert item["source"] == "scope_default"
        history = await service.list_usage_periods("user-1", scope["id"], limit=1)
        assert len(history["items"]) == 1
        assert history["next_cursor"] is not None
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_image_generation_reservation_is_atomic_and_release_keeps_period(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        await _create_image_scope(service, limit=2)
        reservations = await asyncio.gather(
            service.reserve_image_generations("user-1", count=1),
            service.reserve_image_generations("user-1", count=1),
        )
        with pytest.raises(QuotaExceededError):
            await service.reserve_image_generations("user-1", count=1)

        await service.release_image_generations(reservations[0])

        async with sf() as session:
            row = (await session.execute(select(UserQuotaUsagePeriodRow))).scalar_one()
            scope = (await session.execute(select(QuotaScopeRow))).scalar_one()
        assert scope.code == "image_generation"
        assert row.image_used == 1
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_image_generation_is_untracked_until_admin_creates_scope(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        reservation = await service.reserve_image_generations("user-1", count=1)

        assert reservation.matched_scope_id is None
        async with sf() as session:
            scope_count = (await session.execute(select(func.count(QuotaScopeRow.id)))).scalar_one()
            usage_count = (await session.execute(select(func.count(UserQuotaUsagePeriodRow.id)))).scalar_one()
        assert scope_count == 0
        assert usage_count == 0
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_only_one_image_generation_scope_can_be_created(tmp_path):
    service, _ = await _quota_service(tmp_path)
    try:
        await _create_image_scope(service)

        with pytest.raises(HTTPException) as exc_info:
            await _create_image_scope(service)

        assert exc_info.value.status_code == 409
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_video_points_reservation_settlement_and_release_keep_period(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        await _create_video_scope(service, limit=70)
        first = await service.reserve_video_generation("user-1", model="seedance-2.5", resolution="1080p", duration_seconds=10, idempotency_key="video-1")
        assert first.reserved_minor_units == 3500
        replay = await service.reserve_video_generation("user-1", model="seedance-2.5", resolution="1080p", duration_seconds=10, idempotency_key="video-1")
        assert replay.reused is True
        assert replay.reservation_id == first.reservation_id
        await service.settle_video_points(first)

        second = await service.reserve_video_generation("user-1", model="seedance-2.5", resolution="1080p", duration_seconds=10, idempotency_key="video-2")
        with pytest.raises(QuotaExceededError) as exc_info:
            await service.reserve_video_generation("user-1", model="seedance-2.5", resolution="1080p", duration_seconds=10, idempotency_key="video-3")
        assert exc_info.value.exceeded.metric == "video_generations"
        assert exc_info.value.exceeded.used == 7000

        await service.release_video_points(second)

        async with sf() as session:
            row = (await session.execute(select(UserQuotaUsagePeriodRow))).scalar_one()
            scope = (await session.execute(select(QuotaScopeRow))).scalar_one()
        assert scope.code == "video_generation"
        assert row.video_used == 3500
        assert row.video_reserved == 0
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_video_generation_requires_admin_points_configuration(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        with pytest.raises(HTTPException) as exc_info:
            await service.reserve_video_generation("user-1", model="seedance-2.5", resolution="1080p", duration_seconds=10, idempotency_key="video-1")

        assert exc_info.value.status_code == 409
        async with sf() as session:
            scope_count = (await session.execute(select(func.count(QuotaScopeRow.id)))).scalar_one()
            usage_count = (await session.execute(select(func.count(UserQuotaUsagePeriodRow.id)))).scalar_one()
        assert scope_count == 0
        assert usage_count == 0
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_only_one_video_generation_scope_can_be_created(tmp_path):
    service, _ = await _quota_service(tmp_path)
    try:
        await _create_video_scope(service)

        with pytest.raises(HTTPException) as exc_info:
            await _create_video_scope(service)

        assert exc_info.value.status_code == 409
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_image_and_video_quotas_are_independent_aggregates(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        await _create_image_scope(service, limit=1)
        await _create_video_scope(service, limit=35)

        image_reservation = await service.reserve_image_generations("user-1", count=1)
        video_reservation = await service.reserve_video_generation("user-1", model="seedance-2.5", resolution="1080p", duration_seconds=10, idempotency_key="video-1")
        with pytest.raises(QuotaExceededError):
            await service.reserve_image_generations("user-1", count=1)
        with pytest.raises(QuotaExceededError):
            await service.reserve_video_generation("user-1", model="seedance-2.5", resolution="1080p", duration_seconds=10, idempotency_key="video-2")

        await service.release_image_generations(image_reservation)
        await service.release_video_points(video_reservation)

        async with sf() as session:
            rows = {row.quota_scope_id: row for row in (await session.execute(select(UserQuotaUsagePeriodRow))).scalars()}
        assert len(rows) == 2
        assert {row.image_used for row in rows.values()} == {0}
        assert {row.video_used for row in rows.values()} == {0}
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_video_new_week_resets_usage_and_does_not_inherit_override(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        scope = await _create_video_scope(service, limit=100)
        async with sf() as session:
            session.add(UserRow(id="user-1", email="one@example.com", system_role="user"))
            await session.commit()
        first_week = datetime(2026, 7, 15, tzinfo=UTC)
        next_week = datetime(2026, 7, 22, tzinfo=UTC)
        await service.reserve_video_generation("user-1", model="seedance-2.5", resolution="1080p", duration_seconds=10, idempotency_key="week-1", at=first_week)
        await service.override_user_current_period(
            "user-1",
            scope["id"],
            request_enforced=None,
            request_limit=None,
            image_enforced=None,
            image_limit=None,
            video_enforced=True,
            video_limit=99,
            reason=None,
            updated_by="admin-1",
            at=first_week,
        )

        reservation = await service.reserve_video_generation("user-1", model="seedance-2.5", resolution="1080p", duration_seconds=10, idempotency_key="week-2", at=next_week)

        assert reservation.video_used == 0
        detail = await service.get_user_quota("user-1", at=next_week)
        item = next(item for item in detail["items"] if item["scope"]["id"] == scope["id"])
        assert item["videos"]["limit"] == 100
        assert item["videos"]["reserved"] == 35
        assert item["source"] == "scope_default"
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_user_list_synthesizes_unused_scopes_without_creating_period_rows(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        await _create_claude_scope(service, limit=10)
        async with sf() as session:
            session.add(UserRow(id="user-1", email="a@example.com", system_role="user"))
            await session.commit()

        response = await service.list_user_quotas(page=1, page_size=10)

        assert response["total"] == 1
        assert response["items"][0]["model_groups"]["enabled"] == 1
        async with sf() as session:
            count = (await session.execute(select(func.count(UserQuotaUsagePeriodRow.id)))).scalar_one()
        assert count == 0
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_user_list_includes_model_group_request_and_token_usage(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        scope = await _create_claude_scope(service, limit=10)
        async with sf() as session:
            session.add(UserRow(id="user-1", email="a@example.com", system_role="user"))
            await session.commit()

        reservation = await service.reserve_model_request("user-1", "claude-sonnet-4")
        await service.record_model_tokens(reservation, 1250)

        response = await service.list_user_quotas(page=1, page_size=10)

        [model_group] = response["items"][0]["model_groups"]["items"]
        assert model_group["scope_id"] == scope["id"]
        assert model_group["requests"] == {
            "enforced": True,
            "used": 1,
            "limit": 10,
            "remaining": 9,
            "ratio": 0.1,
            "status": "normal",
        }
        assert model_group["token_observation"] == {"used": 1250}
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_user_list_status_filter_is_applied_before_database_pagination(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        await _create_claude_scope(service, limit=1)
        now = datetime.now(UTC)
        async with sf() as session:
            session.add_all(
                [
                    UserRow(
                        id=f"user-{index}",
                        email=f"user-{index}@example.com",
                        system_role="user",
                        created_at=now - timedelta(minutes=index),
                    )
                    for index in range(5)
                ]
            )
            await session.commit()
        await service.reserve_model_request("user-4", "claude-sonnet-4")

        response = await service.list_user_quotas(
            page=1,
            page_size=2,
            status="exceeded",
        )

        assert response["total"] == 1
        assert [item["user_id"] for item in response["items"]] == ["user-4"]
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_user_list_returns_correct_page_and_total(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        now = datetime.now(UTC)
        async with sf() as session:
            session.add_all(
                [
                    UserRow(
                        id=f"user-{index}",
                        email=f"user-{index}@example.com",
                        system_role="user",
                        created_at=now - timedelta(minutes=index),
                    )
                    for index in range(5)
                ]
            )
            await session.commit()

        response = await service.list_user_quotas(page=2, page_size=2)

        assert response["total"] == 5
        assert [item["user_id"] for item in response["items"]] == ["user-2", "user-3"]
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_video_regeneration_uses_dedicated_rate(tmp_path):
    """升格（regeneration）按 regeneration 费率计价，而非分辨率整价。"""
    service, sf = await _quota_service(tmp_path)
    try:
        await service.create_scope(
            {
                "code": "video_generation",
                "name": "视频资源",
                "resource_type": "video_generation",
                "match_rules": {"exact": [], "prefix": []},
                "period_type": "weekly",
                "video_enforced": True,
                "video_limit": 100,
                "video_billing_rules": {
                    "currency": "CNY",
                    "point_to_yuan": 1,
                    "models": {
                        "minimax-h3": {
                            "resolutions": {"768P": 0.5, "2K": 0.8},
                            "regeneration": 0.3,
                        },
                    },
                },
                "enabled": True,
            },
            updated_by="admin-1",
        )

        regeneration = await service.reserve_video_generation(
            "user-1",
            model="minimax-h3",
            resolution="2K",
            duration_seconds=10,
            operation="regeneration",
        )
        # 重生成 0.3 元/秒 × 10s = 300 分，而非 2K 整价 0.8 元/秒 = 800 分
        assert regeneration.reserved_minor_units == 300

        generation = await service.reserve_video_generation(
            "user-2",
            model="minimax-h3",
            resolution="2K",
            duration_seconds=10,
        )
        assert generation.reserved_minor_units == 800

        async with sf() as session:
            rows = (await session.execute(select(UserQuotaUsagePeriodRow))).scalars().all()
        records = [record for row in rows for record in row.video_billing_records["items"].values()]
        assert {record["operation"] for record in records} == {"regeneration", "generation"}
        assert {record["price_fen_per_second"] for record in records} == {30, 80}
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_video_regeneration_conflicts_on_operation_mismatch(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        await service.create_scope(
            {
                "code": "video_generation",
                "name": "视频资源",
                "resource_type": "video_generation",
                "match_rules": {"exact": [], "prefix": []},
                "period_type": "weekly",
                "video_enforced": True,
                "video_limit": 100,
                "video_billing_rules": {
                    "currency": "CNY",
                    "point_to_yuan": 1,
                    "models": {"minimax-h3": {"768P": 0.5, "2K": 0.8, "regeneration": 0.3}},
                },
                "enabled": True,
            },
            updated_by="admin-1",
        )
        key = "idem-operation-mismatch"
        await service.reserve_video_generation(
            "user-1",
            model="minimax-h3",
            resolution="2K",
            duration_seconds=5,
            idempotency_key=key,
            operation="regeneration",
        )
        with pytest.raises(HTTPException) as exc_info:
            await service.reserve_video_generation(
                "user-1",
                model="minimax-h3",
                resolution="2K",
                duration_seconds=5,
                idempotency_key=key,
            )
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["code"] == "video_billing_idempotency_conflict"
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_video_regeneration_fails_closed_without_rate(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        await _create_video_scope(service)

        with pytest.raises(HTTPException) as exc_info:
            await service.reserve_video_generation(
                "user-1",
                model="seedance-2.5",
                resolution="1080p",
                duration_seconds=5,
                operation="regeneration",
            )
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["code"] == "video_regeneration_rate_not_found"
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


def test_video_billing_rules_validate_regeneration_price():
    from app.gateway.admin.quota_service import _validate_video_billing_rules

    # 扁平写法下 regeneration 不被当作分辨率，且价格合法性被校验
    rules = {
        "currency": "CNY",
        "point_to_yuan": 1,
        "models": {"minimax-h3": {"768P": 0.5, "2K": 0.8, "regeneration": 0.3}},
    }
    validated = _validate_video_billing_rules(rules)
    assert "regeneration" not in validated["models"]["minimax-h3"].get("resolutions", {})

    with pytest.raises(HTTPException):
        _validate_video_billing_rules(
            {
                "currency": "CNY",
                "point_to_yuan": 1,
                "models": {"minimax-h3": {"768P": 0.5, "regeneration": -1}},
            }
        )


def test_model_scope_tier_detects_premium_hints():
    from app.gateway.admin.quota_service import _model_scope_tier

    assert _model_scope_tier({"exact": [], "prefix": ["claude-"]}) == 1
    assert _model_scope_tier({"exact": ["GPT-4o"], "prefix": []}) == 1
    assert _model_scope_tier({"exact": ["qwen-max"], "prefix": ["doubao-"]}) == 0
    assert _model_scope_tier({"exact": [], "prefix": []}) == 0
    assert _model_scope_tier({}) == 0
    assert _model_scope_tier(None) == 0
    assert _model_scope_tier({"exact": "not-a-list", "prefix": 42}) == 0


@pytest.mark.asyncio
async def test_user_quota_items_are_display_ordered(tmp_path):
    """get_user_quota 固定展示顺序：模型组（通用→高级）→ 生图 → 视频，与创建顺序无关。"""
    service, sf = await _quota_service(tmp_path)
    try:
        async with sf() as session:
            session.add(UserRow(id="user-1", email="one@example.com", system_role="user"))
            await session.commit()
        await service.create_scope(
            {
                "code": "video_generation",
                "name": "视频资源",
                "resource_type": "video_generation",
                "match_rules": {"exact": [], "prefix": []},
                "period_type": "weekly",
                "video_enforced": True,
                "video_limit": 100,
                "video_billing_rules": {"currency": "CNY", "point_to_yuan": 1, "models": {"seedance-2.5": {"1080p": 3.5}}},
                "enabled": True,
            },
            updated_by="admin-1",
        )
        await service.create_scope(
            {
                "code": "claude_advanced",
                "name": "高级模型",
                "resource_type": "model",
                "match_rules": {"exact": [], "prefix": ["claude-"]},
                "period_type": "weekly",
                "request_enforced": True,
                "request_limit": 2,
                "enabled": True,
            },
            updated_by="admin-1",
        )
        await service.create_scope(
            {
                "code": "image_generation",
                "name": "生图资源",
                "resource_type": "image_generation",
                "match_rules": {"exact": [], "prefix": []},
                "period_type": "weekly",
                "image_enforced": True,
                "image_limit": 5,
                "enabled": True,
            },
            updated_by="admin-1",
        )
        await service.create_scope(
            {
                "code": "general_model",
                "name": "通用模型",
                "resource_type": "model",
                "match_rules": {"exact": [], "prefix": ["qwen", "doubao-"]},
                "period_type": "weekly",
                "request_enforced": True,
                "request_limit": 10,
                "enabled": True,
            },
            updated_by="admin-1",
        )

        detail = await service.get_user_quota("user-1")

        assert [(item["scope"]["code"], item["scope"]["name"]) for item in detail["items"]] == [
            ("general_model", "通用模型"),
            ("claude_advanced", "高级模型"),
            ("image_generation", "生图资源"),
            ("video_generation", "视频资源"),
        ]
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


async def _usage_row(sf) -> UserQuotaUsagePeriodRow:
    async with sf() as session:
        return (await session.execute(select(UserQuotaUsagePeriodRow))).scalar_one()


# 2026-09-02 04:00 UTC == 上海时间 2026-09-02 12:00；所有日桶日期由此固定。
_SHANGHAI_NOON = datetime(2026, 9, 2, 4, 0, tzinfo=UTC)
_SHANGHAI_LATE_NIGHT = datetime(2026, 9, 2, 15, 30, tzinfo=UTC)  # 上海 23:30，仍是 09-02
_SHANGHAI_PAST_MIDNIGHT = datetime(2026, 9, 2, 16, 30, tzinfo=UTC)  # 上海 09-03 00:30
_NEXT_WEEK = datetime(2026, 9, 10, 4, 0, tzinfo=UTC)  # 下一个自然周


@pytest.mark.asyncio
async def test_video_daily_limit_blocks_while_period_quota_remains(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        await _create_video_scope(service, limit=100, daily={"enforced": True, "limit": 20})
        first = await service.reserve_video_generation("user-1", model="seedance-2.5", resolution="1080p", duration_seconds=4, idempotency_key="d-1", at=_SHANGHAI_NOON)
        assert first.reserved_minor_units == 1400

        with pytest.raises(QuotaExceededError) as exc_info:
            await service.reserve_video_generation("user-1", model="seedance-2.5", resolution="1080p", duration_seconds=4, idempotency_key="d-2", at=_SHANGHAI_NOON)

        exceeded = exc_info.value.exceeded
        assert exceeded.dimension == "daily"
        assert exceeded.used == 1400
        assert exceeded.limit == 2000
        payload = quota_exceeded_payload(exceeded)
        assert payload["dimension"] == "daily"
        assert payload["daily_period"] == {"period_type": "daily", "label": "2026-09-02", "timezone": "Asia/Shanghai"}
        assert payload["used_points"] == 14.0
        assert payload["limit_points"] == 20.0
        assert "今日" in payload["message"]

        row = await _usage_row(sf)
        assert row.daily_usage["2026-09-02"] == {"used": 0, "reserved": 1400}
        assert row.video_used == 0
        assert row.video_reserved == 1400
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_video_period_limit_blocks_while_daily_quota_remains(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        await _create_video_scope(service, limit=20, daily={"enforced": True, "limit": 100})
        await service.reserve_video_generation("user-1", model="seedance-2.5", resolution="1080p", duration_seconds=4, idempotency_key="p-1", at=_SHANGHAI_NOON)

        with pytest.raises(QuotaExceededError) as exc_info:
            await service.reserve_video_generation("user-1", model="seedance-2.5", resolution="1080p", duration_seconds=4, idempotency_key="p-2", at=_SHANGHAI_NOON)

        exceeded = exc_info.value.exceeded
        assert exceeded.dimension == "period"
        # used = 已用 + 已预占（1400），本次请求量 1400 导致越限。
        assert exceeded.used == 1400
        assert exceeded.requested == 1400
        assert exceeded.limit == 2000
        assert "daily_period" not in quota_exceeded_payload(exceeded)
        row = await _usage_row(sf)
        assert row.daily_usage["2026-09-02"]["reserved"] == 1400
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_video_daily_replay_is_idempotent_on_daily_bucket(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        await _create_video_scope(service, limit=100, daily={"enforced": True, "limit": 20})
        first = await service.reserve_video_generation("user-1", model="seedance-2.5", resolution="1080p", duration_seconds=4, idempotency_key="same", at=_SHANGHAI_NOON)
        replay = await service.reserve_video_generation("user-1", model="seedance-2.5", resolution="1080p", duration_seconds=4, idempotency_key="same", at=_SHANGHAI_NOON)
        assert replay.reused is True
        assert replay.reservation_id == first.reservation_id

        row = await _usage_row(sf)
        assert row.daily_usage["2026-09-02"]["reserved"] == 1400

        with pytest.raises(QuotaExceededError) as exc_info:
            await service.reserve_video_generation("user-1", model="seedance-2.5", resolution="1080p", duration_seconds=4, idempotency_key="other", at=_SHANGHAI_NOON)
        assert exc_info.value.exceeded.dimension == "daily"
        row = await _usage_row(sf)
        assert row.daily_usage["2026-09-02"]["reserved"] == 1400
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_video_daily_buckets_follow_shanghai_local_date(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        await _create_video_scope(service, limit=100, daily={"enforced": True, "limit": 100})
        await service.reserve_video_generation("user-1", model="seedance-2.5", resolution="1080p", duration_seconds=4, idempotency_key="late", at=_SHANGHAI_LATE_NIGHT)
        await service.reserve_video_generation("user-1", model="seedance-2.5", resolution="1080p", duration_seconds=4, idempotency_key="next-day", at=_SHANGHAI_PAST_MIDNIGHT)

        row = await _usage_row(sf)
        assert set(row.daily_usage) == {"2026-09-02", "2026-09-03"}
        assert row.daily_usage["2026-09-02"]["reserved"] == 1400
        assert row.daily_usage["2026-09-03"]["reserved"] == 1400
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_video_settle_and_release_keep_original_date_bucket(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        await _create_video_scope(service, limit=100, daily={"enforced": True, "limit": 100})
        first = await service.reserve_video_generation("user-1", model="seedance-2.5", resolution="1080p", duration_seconds=4, idempotency_key="s-1", at=_SHANGHAI_NOON)
        # 结算发生在“今天”，但必须回写预占日 09-02 的桶。
        await service.settle_video_points(first)
        second = await service.reserve_video_generation("user-1", model="seedance-2.5", resolution="1080p", duration_seconds=4, idempotency_key="s-2", at=_SHANGHAI_PAST_MIDNIGHT)
        await service.release_video_points(second)

        row = await _usage_row(sf)
        assert row.daily_usage["2026-09-02"] == {"used": 1400, "reserved": 0}
        assert row.daily_usage["2026-09-03"] == {"used": 0, "reserved": 0}
        assert row.video_used == 1400
        assert row.video_reserved == 0
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_video_daily_concurrent_reserves_do_not_corrupt_buckets(tmp_path):
    """SQLite 没有父行锁、并发表现为末写胜出；严格上限由 PostgreSQL 行锁保证。

    这里断言并发不产生负数或脏桶、被拒请求均为日维度拒绝；顺序场景的
    严格拦截由 test_video_daily_limit_blocks_while_period_quota_remains 覆盖。
    """
    service, sf = await _quota_service(tmp_path)
    try:
        await _create_video_scope(service, limit=100, daily={"enforced": True, "limit": 20})

        results = await asyncio.gather(
            *(service.reserve_video_generation("user-1", model="seedance-2.5", resolution="1080p", duration_seconds=4, idempotency_key=f"c-{index}", at=_SHANGHAI_NOON) for index in range(3)),
            return_exceptions=True,
        )
        assert all(isinstance(item, (VideoPointsReservation, QuotaExceededError)) for item in results)
        assert all(item.exceeded.dimension == "daily" for item in results if isinstance(item, QuotaExceededError))

        row = await _usage_row(sf)
        bucket = row.daily_usage["2026-09-02"]
        assert bucket["used"] >= 0 and bucket["reserved"] >= 0
        assert row.video_used >= 0 and row.video_reserved >= 0
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_video_daily_new_period_starts_with_fresh_buckets(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        await _create_video_scope(service, limit=100, daily={"enforced": True, "limit": 20})
        await service.reserve_video_generation("user-1", model="seedance-2.5", resolution="1080p", duration_seconds=4, idempotency_key="w-1", at=_SHANGHAI_NOON)
        await service.reserve_video_generation("user-1", model="seedance-2.5", resolution="1080p", duration_seconds=4, idempotency_key="w-2", at=_NEXT_WEEK)

        async with sf() as session:
            rows = {row.period_start.isoformat(): row for row in (await session.execute(select(UserQuotaUsagePeriodRow))).scalars()}
        assert len(rows) == 2
        buckets = list(rows.values())
        assert {"2026-09-02": {"used": 0, "reserved": 1400}} in [row.daily_usage for row in buckets]
        assert {"2026-09-10": {"used": 0, "reserved": 1400}} in [row.daily_usage for row in buckets]
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_video_scope_update_enables_daily_and_rebuilds_legacy_records(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        scope = await _create_video_scope(service, limit=100)
        await service.reserve_video_generation("user-1", model="seedance-2.5", resolution="1080p", duration_seconds=4, idempotency_key="r-1", at=_SHANGHAI_NOON)
        async with sf() as session:
            row = (await session.execute(select(UserQuotaUsagePeriodRow))).scalar_one()
            # 重建外层 dict，确保 SQLAlchemy 标记 JSON 列为脏并落库。
            records = dict(row.video_billing_records)
            records["items"] = dict(records.get("items") or {})
            records["items"]["legacy-1"] = {
                "record_id": "legacy-1",
                "record_type": "generation",
                "reservation_id": "legacy-1",
                "status": "settled",
                "reserved_minor_units": 700,
                "settled_minor_units": 700,
                "created_at": "2026-09-01T10:00:00+00:00",
            }
            row.video_billing_records = records
            await session.commit()

        await service.update_scope(
            scope["id"],
            {"name": "视频资源", "enabled": True, "period_type": "weekly", "videos_daily": {"enforced": True, "limit": 20}},
            updated_by="admin-1",
        )

        row = await _usage_row(sf)
        assert row.daily_enforced_snapshot is True
        assert row.daily_limit_snapshot == 2000
        assert row.daily_usage == {
            "2026-09-01": {"used": 700, "reserved": 0},
            "2026-09-02": {"used": 0, "reserved": 1400},
        }
        with pytest.raises(QuotaExceededError) as exc_info:
            await service.reserve_video_generation("user-1", model="seedance-2.5", resolution="1080p", duration_seconds=4, idempotency_key="r-2", at=_SHANGHAI_NOON)
        assert exc_info.value.exceeded.dimension == "daily"
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_video_scope_update_keeps_daily_policy_when_daily_absent(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        scope = await _create_video_scope(service, limit=100, daily={"enforced": True, "limit": 20})
        assert scope["default_policy"]["videos"]["daily"] == {"enforced": True, "limit": 20.0}
        await service.reserve_video_generation("user-1", model="seedance-2.5", resolution="1080p", duration_seconds=4, idempotency_key="k-0", at=_SHANGHAI_NOON)

        await service.update_scope(scope["id"], {"name": "视频资源", "enabled": True, "period_type": "weekly"}, updated_by="admin-1")
        async with sf() as session:
            refreshed = await session.get(QuotaScopeRow, scope["id"])
        assert refreshed.daily_enforced is True
        assert refreshed.daily_limit == 2000

        await service.update_scope(
            scope["id"],
            {"name": "视频资源", "enabled": True, "period_type": "weekly", "videos_daily": {"enforced": False, "limit": None}},
            updated_by="admin-1",
        )
        row = await _usage_row(sf)
        assert row.daily_enforced_snapshot is False
        assert row.daily_limit_snapshot is None

        # 日限额关闭后只走父周期校验，28 积分 < 100 积分，正常预占。
        reservation = await service.reserve_video_generation("user-1", model="seedance-2.5", resolution="1080p", duration_seconds=4, idempotency_key="k-1", at=_SHANGHAI_NOON)
        assert reservation.reserved_minor_units == 1400
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_video_override_and_restore_sync_daily_snapshots(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        scope = await _create_video_scope(service, limit=100, daily={"enforced": True, "limit": 20})
        async with sf() as session:
            session.add(UserRow(id="user-1", email="one@example.com", system_role="user"))
            await session.commit()
        await service.override_user_current_period(
            "user-1",
            scope["id"],
            request_enforced=None,
            request_limit=None,
            image_enforced=None,
            image_limit=None,
            video_enforced=True,
            video_limit=50,
            video_daily={"enforced": True, "limit": 10},
            reason="临时调整",
            updated_by="admin-1",
            at=_SHANGHAI_NOON,
        )
        row = await _usage_row(sf)
        assert row.video_limit_snapshot == 5000
        assert row.daily_limit_snapshot == 1000
        assert row.is_overridden is True

        await service.restore_user_current_period("user-1", scope["id"], updated_by="admin-1", at=_SHANGHAI_NOON)
        row = await _usage_row(sf)
        assert row.video_limit_snapshot == 10000
        assert row.daily_enforced_snapshot is True
        assert row.daily_limit_snapshot == 2000
        assert row.is_overridden is False

        await service.override_user_current_period(
            "user-1",
            scope["id"],
            request_enforced=None,
            request_limit=None,
            image_enforced=None,
            image_limit=None,
            video_enforced=True,
            video_limit=60,
            video_daily=None,
            reason=None,
            updated_by="admin-1",
            at=_SHANGHAI_NOON,
        )
        row = await _usage_row(sf)
        assert row.video_limit_snapshot == 6000
        assert row.daily_limit_snapshot == 2000
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_video_usage_item_daily_metric_and_history_omission(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        scope = await _create_video_scope(service, limit=100, daily={"enforced": True, "limit": 20})
        async with sf() as session:
            session.add(UserRow(id="user-1", email="one@example.com", system_role="user"))
            await session.commit()
        await service.reserve_video_generation("user-1", model="seedance-2.5", resolution="1080p", duration_seconds=4, idempotency_key="u-1", at=_SHANGHAI_NOON)

        detail = await service.get_user_quota("user-1", at=_SHANGHAI_NOON)
        videos = next(item for item in detail["items"] if item["scope"]["id"] == scope["id"])["videos"]
        assert videos["daily"]["period"]["label"] == "2026-09-02"
        assert videos["daily"]["used"] == 0.0
        assert videos["daily"]["reserved"] == 14.0
        assert videos["daily"]["remaining"] == 6.0
        assert videos["daily"]["status"] == "normal"
        assert videos["daily"]["period"] == {"period_type": "daily", "label": "2026-09-02", "timezone": "Asia/Shanghai"}

        history = await service.list_usage_periods("user-1", scope["id"])
        assert "daily" not in history["items"][0]["videos"]

        override = await service.override_user_current_period(
            "user-1",
            scope["id"],
            request_enforced=None,
            request_limit=None,
            image_enforced=None,
            image_limit=None,
            video_enforced=True,
            video_limit=100,
            video_daily=None,
            reason=None,
            updated_by="admin-1",
            at=_SHANGHAI_NOON,
        )
        assert override["videos"]["daily"]["limit"] == 20.0
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_model_request_daily_limit_blocks_and_release_returns_bucket(tmp_path):
    """模型组日拦截：日维度拒绝、释放回扣当日桶，计数单位文案。"""
    service, sf = await _quota_service(tmp_path)
    try:
        async with sf() as session:
            session.add(UserRow(id="user-1", email="one@example.com", system_role="user"))
            await session.commit()
        scope = await _create_claude_scope(service, limit=10, daily={"enforced": True, "limit": 3})

        reservations = [await service.reserve_model_request("user-1", "claude-sonnet-4", at=_SHANGHAI_NOON) for _ in range(3)]
        with pytest.raises(QuotaExceededError) as exc_info:
            await service.reserve_model_request("user-1", "claude-sonnet-4", at=_SHANGHAI_NOON)

        exceeded = exc_info.value.exceeded
        assert exceeded.dimension == "daily"
        assert exceeded.used == 3
        assert exceeded.limit == 3
        assert exceeded.requested == 1
        payload = quota_exceeded_payload(exceeded)
        assert payload["daily_period"] == {"period_type": "daily", "label": "2026-09-02", "timezone": "Asia/Shanghai"}
        assert "今日额度已用尽" in payload["message"]
        assert "1 次" in payload["message"] and "仅剩余 0 次" in payload["message"]

        await service.release_undispatched_model_request(reservations[0])

        row = await _usage_row(sf)
        assert row.daily_usage["2026-09-02"] == {"used": 2, "reserved": 0}
        assert row.request_used == 2
        assert row.daily_enforced_snapshot is True
        assert row.daily_limit_snapshot == 3
        detail = await service.get_user_quota("user-1", at=_SHANGHAI_NOON)
        item = next(item for item in detail["items"] if item["scope"]["id"] == scope["id"])
        assert item["requests"]["daily"]["used"] == 2
        assert item["requests"]["daily"]["limit"] == 3
        assert item["requests"]["daily"]["remaining"] == 1
        assert item["requests"]["daily"]["status"] == "normal"
        assert item["requests"]["daily"]["period"]["label"] == "2026-09-02"
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_image_daily_limit_blocks_and_release_restores_bucket(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        async with sf() as session:
            session.add(UserRow(id="user-1", email="one@example.com", system_role="user"))
            await session.commit()
        await _create_image_scope(service, limit=10, daily={"enforced": True, "limit": 2})

        first = await service.reserve_image_generations("user-1", count=1, at=_SHANGHAI_NOON)
        await service.reserve_image_generations("user-1", count=1, at=_SHANGHAI_NOON)
        with pytest.raises(QuotaExceededError) as exc_info:
            await service.reserve_image_generations("user-1", count=1, at=_SHANGHAI_NOON)

        exceeded = exc_info.value.exceeded
        assert exceeded.metric == "image_generations"
        assert exceeded.dimension == "daily"
        assert exceeded.used == 2

        await service.release_image_generations(first)

        row = await _usage_row(sf)
        assert row.daily_usage["2026-09-02"] == {"used": 1, "reserved": 0}
        assert row.image_used == 1
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_model_daily_unconfigured_scope_has_no_daily_metric(tmp_path):
    """未配置日限额的模型组保持原样：无日桶、无 daily 字段、无锁路径开销。"""
    service, sf = await _quota_service(tmp_path)
    try:
        async with sf() as session:
            session.add(UserRow(id="user-1", email="one@example.com", system_role="user"))
            await session.commit()
        scope = await _create_claude_scope(service, limit=10)

        await service.reserve_model_request("user-1", "claude-sonnet-4", at=_SHANGHAI_NOON)

        row = await _usage_row(sf)
        assert row.daily_usage == {}
        detail = await service.get_user_quota("user-1", at=_SHANGHAI_NOON)
        item = next(item for item in detail["items"] if item["scope"]["id"] == scope["id"])
        assert "daily" not in item["requests"]
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_model_daily_new_period_starts_fresh(tmp_path):
    service, sf = await _quota_service(tmp_path)
    try:
        async with sf() as session:
            session.add(UserRow(id="user-1", email="one@example.com", system_role="user"))
            await session.commit()
        await _create_claude_scope(service, limit=10, daily={"enforced": True, "limit": 3})

        await service.reserve_model_request("user-1", "claude-sonnet-4", at=_SHANGHAI_NOON)
        await service.reserve_model_request("user-1", "claude-sonnet-4", at=_NEXT_WEEK)

        async with sf() as session:
            rows = {row.period_start.isoformat(): row for row in (await session.execute(select(UserQuotaUsagePeriodRow))).scalars()}
        assert len(rows) == 2
        assert {"2026-09-02": {"used": 1, "reserved": 0}} in [row.daily_usage for row in rows.values()]
        assert {"2026-09-10": {"used": 1, "reserved": 0}} in [row.daily_usage for row in rows.values()]
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_model_daily_override_and_restore_sync_snapshots(tmp_path):
    """日限额跟随 requests 覆盖调整；restore 回到 scope 默认。"""
    service, sf = await _quota_service(tmp_path)
    try:
        async with sf() as session:
            session.add(UserRow(id="user-1", email="one@example.com", system_role="user"))
            await session.commit()
        scope = await _create_claude_scope(service, limit=10, daily={"enforced": True, "limit": 3})

        await service.override_user_current_period(
            "user-1",
            scope["id"],
            request_enforced=True,
            request_limit=50,
            image_enforced=None,
            image_limit=None,
            request_daily={"enforced": True, "limit": 5},
            reason="临时调整",
            updated_by="admin-1",
            at=_SHANGHAI_NOON,
        )
        row = await _usage_row(sf)
        assert row.request_limit_snapshot == 50
        assert row.daily_limit_snapshot == 5
        assert row.is_overridden is True

        restored = await service.restore_user_current_period("user-1", scope["id"], at=_SHANGHAI_NOON)
        assert restored["requests"]["limit"] == 10
        row = await _usage_row(sf)
        assert row.daily_enforced_snapshot is True
        assert row.daily_limit_snapshot == 3
        assert row.is_overridden is False
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_scope_response_exposes_daily_policy_for_all_resource_types(tmp_path):
    service, _ = await _quota_service(tmp_path)
    try:
        model_scope = await _create_claude_scope(service, limit=10, daily={"enforced": True, "limit": 3})
        image_scope = await _create_image_scope(service, limit=10, daily={"enforced": True, "limit": 5})
        video_scope = await _create_video_scope(service, limit=100, daily={"enforced": True, "limit": 20})

        assert model_scope["default_policy"]["requests"]["daily"] == {"enforced": True, "limit": 3}
        assert image_scope["default_policy"]["images"]["daily"] == {"enforced": True, "limit": 5}
        assert video_scope["default_policy"]["videos"]["daily"] == {"enforced": True, "limit": 20.0}
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]
