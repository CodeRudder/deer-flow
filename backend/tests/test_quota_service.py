import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.admin.quota_service import QuotaExceededError, QuotaService
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


async def _create_claude_scope(service: QuotaService, *, limit: int = 2) -> dict:
    return await service.create_scope(
        {
            "code": "claude_advanced",
            "name": "Claude 高级模型",
            "resource_type": "model",
            "match_rules": {"exact": [], "prefix": ["claude-"]},
            "period_type": "weekly",
            "request_enforced": True,
            "request_limit": limit,
            "enabled": True,
        },
        updated_by="admin-1",
    )


async def _create_image_scope(service: QuotaService, *, limit: int = 2) -> dict:
    return await service.create_scope(
        {
            "code": "image_generation",
            "name": "生图资源",
            "resource_type": "image_generation",
            "match_rules": {"exact": [], "prefix": []},
            "period_type": "weekly",
            "image_enforced": True,
            "image_limit": limit,
            "enabled": True,
        },
        updated_by="admin-1",
    )


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
