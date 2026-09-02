"""GET /api/quotas/me 用户侧额度端点（feat-df-8）。

覆盖：维度过滤（模型维度不下发）、仅记录态字段透传（评审 P0-2）、
本人隔离、无鉴权 default 用户空账本、无数据库 503、泄漏断言（评审 P1-2）。
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.admin.quota_service import QuotaService
from app.gateway.routers import quota as quota_router
from deerflow.persistence.base import Base
from deerflow.persistence.user.model import UserRow
from deerflow.runtime import user_context

# 管理员身份/内部字段不得出现在用户侧响应（评审 P1-2/P1-3 白名单）
_FORBIDDEN_FIELDS = (
    '"email"',
    '"role"',
    '"overridden_by"',
    '"overridden_at"',
    '"reason"',
    '"updated_by"',
    '"updated_at"',
    '"scope_policy_version"',
    '"usage_period_id"',
    '"is_system"',
    '"token_observation"',
    '"billing_mode"',
    '"match_rules"',
)


async def _quota_service(tmp_path) -> tuple[QuotaService, async_sessionmaker]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'quota_me.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    service = QuotaService(sf)
    service._test_engine = engine  # type: ignore[attr-defined]
    return service, sf


async def _create_user(sf, user_id: str, email: str) -> None:
    async with sf() as session:
        session.add(UserRow(id=user_id, email=email))
        await session.commit()


async def _create_scopes(
    service: QuotaService,
    *,
    video_enforced: bool = True,
    video_limit: int | float | None = 100,
) -> dict:
    """建 model/image/video 三个 scope，返回 video scope 配置。"""
    await service.create_scope(
        {
            "code": "claude_advanced",
            "name": "Claude 高级模型",
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
    return await service.create_scope(
        {
            "code": "video_generation",
            "name": "视频资源",
            "resource_type": "video_generation",
            "match_rules": {"exact": [], "prefix": []},
            "period_type": "weekly",
            "video_enforced": video_enforced,
            "video_limit": video_limit,
            "video_billing_rules": {
                "currency": "CNY",
                "point_to_yuan": 1,
                "models": {"seedance-2.5": {"1080p": 3.5}},
            },
            "enabled": True,
        },
        updated_by="admin-1",
    )


def _patch_factory(monkeypatch, sf) -> None:
    monkeypatch.setattr(quota_router, "get_session_factory", lambda: sf)


def _acting_user(user_id: str):
    return user_context.set_current_user(SimpleNamespace(id=user_id))


@pytest.mark.asyncio
async def test_quota_me_returns_all_configured_dimensions(tmp_path, monkeypatch):
    service, sf = await _quota_service(tmp_path)
    try:
        await _create_user(sf, "user-1", "user-1@example.com")
        await _create_scopes(service)

        _patch_factory(monkeypatch, sf)
        token = _acting_user("user-1")
        try:
            result = await quota_router.get_my_quota()
        finally:
            user_context.reset_current_user(token)

        assert result.user_id == "user-1"
        assert {item.resource_type for item in result.items} == {
            "model",
            "image_generation",
            "video_generation",
        }
        model_item = next(item for item in result.items if item.resource_type == "model")
        image_item = next(item for item in result.items if item.resource_type == "image_generation")
        video_item = next(item for item in result.items if item.resource_type == "video_generation")
        assert model_item.requests is not None
        assert model_item.requests.unit == "count"
        assert model_item.requests.limit == 2.0
        assert model_item.images is None and model_item.videos is None
        assert image_item.images is not None
        assert image_item.images.unit == "count"
        assert image_item.images.limit == 5.0
        assert image_item.images.reserved is None
        assert video_item.videos is not None
        assert video_item.videos.unit == "points"
        assert video_item.videos.limit == 100.0
        assert video_item.videos.reserved == 0.0
        assert video_item.scope_name == "视频资源"
        assert video_item.scope_code == "video_generation"
        dump = result.model_dump_json()
        for field in _FORBIDDEN_FIELDS:
            assert field not in dump, f"响应泄漏了 {field}"
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_quota_me_record_only_scope_keeps_limit_values(tmp_path, monkeypatch):
    """仅记录但设了上限：status=unlimited 且 limit/remaining 有值（评审 P0-2）。"""
    service, sf = await _quota_service(tmp_path)
    try:
        await _create_user(sf, "user-1", "user-1@example.com")
        await _create_scopes(service, video_enforced=False, video_limit=50)

        _patch_factory(monkeypatch, sf)
        token = _acting_user("user-1")
        try:
            result = await quota_router.get_my_quota()
        finally:
            user_context.reset_current_user(token)

        video_item = next(item for item in result.items if item.resource_type == "video_generation")
        assert video_item.videos is not None
        assert video_item.videos.status == "unlimited"
        assert video_item.videos.enforced is False
        assert video_item.videos.limit == 50.0
        assert video_item.videos.remaining == 50.0
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_quota_me_unlimited_without_limit_returns_nulls(tmp_path, monkeypatch):
    service, sf = await _quota_service(tmp_path)
    try:
        await _create_user(sf, "user-1", "user-1@example.com")
        await _create_scopes(service, video_enforced=False, video_limit=None)

        _patch_factory(monkeypatch, sf)
        token = _acting_user("user-1")
        try:
            result = await quota_router.get_my_quota()
        finally:
            user_context.reset_current_user(token)

        video_item = next(item for item in result.items if item.resource_type == "video_generation")
        assert video_item.videos is not None
        assert video_item.videos.status == "unlimited"
        assert video_item.videos.limit is None
        assert video_item.videos.remaining is None
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_quota_me_empty_when_no_scopes(tmp_path, monkeypatch):
    service, sf = await _quota_service(tmp_path)
    try:
        await _create_user(sf, "user-1", "user-1@example.com")

        _patch_factory(monkeypatch, sf)
        token = _acting_user("user-1")
        try:
            result = await quota_router.get_my_quota()
        finally:
            user_context.reset_current_user(token)

        assert result.items == []
        assert result.status == "unlimited"
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.no_auto_user
async def test_quota_me_default_user_without_row_returns_empty(tmp_path, monkeypatch):
    """无鉴权模式 default 用户无 users 行：404 被吞成空账本，而非报错。"""
    service, sf = await _quota_service(tmp_path)
    try:
        await _create_scopes(service)

        _patch_factory(monkeypatch, sf)
        result = await quota_router.get_my_quota()

        assert result.user_id == "default"
        assert result.items == []
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_quota_me_without_database_returns_503(monkeypatch):
    _patch_factory(monkeypatch, None)
    with pytest.raises(HTTPException) as exc_info:
        await quota_router.get_my_quota()
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_quota_me_isolated_to_calling_user(tmp_path, monkeypatch):
    service, sf = await _quota_service(tmp_path)
    try:
        await _create_user(sf, "user-a", "a@example.com")
        await _create_user(sf, "user-b", "b@example.com")
        await _create_scopes(service)
        await service.reserve_image_generations("user-a", count=2)

        _patch_factory(monkeypatch, sf)
        token_a = _acting_user("user-a")
        try:
            result_a = await quota_router.get_my_quota()
        finally:
            user_context.reset_current_user(token_a)
        token_b = _acting_user("user-b")
        try:
            result_b = await quota_router.get_my_quota()
        finally:
            user_context.reset_current_user(token_b)

        image_a = next(item for item in result_a.items if item.resource_type == "image_generation")
        image_b = next(item for item in result_b.items if item.resource_type == "image_generation")
        assert image_a.images is not None and image_a.images.used == 2.0
        assert image_b.images is not None and image_b.images.used == 0.0
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_quota_me_items_are_display_ordered(tmp_path, monkeypatch):
    """展示顺序由后端固定（模型组通用→高级 → 生图 → 视频），前端按返回顺序渲染。"""
    service, sf = await _quota_service(tmp_path)
    try:
        await _create_user(sf, "user-1", "user-1@example.com")
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

        _patch_factory(monkeypatch, sf)
        token = _acting_user("user-1")
        try:
            result = await quota_router.get_my_quota()
        finally:
            user_context.reset_current_user(token)

        assert [item.scope_code for item in result.items] == [
            "general_model",
            "claude_advanced",
            "image_generation",
            "video_generation",
        ]
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_quota_me_reflects_admin_override(tmp_path, monkeypatch):
    service, sf = await _quota_service(tmp_path)
    try:
        await _create_user(sf, "user-1", "user-1@example.com")
        video_scope = await _create_scopes(service)

        _patch_factory(monkeypatch, sf)
        await service.override_user_current_period(
            "user-1",
            video_scope["id"],
            request_enforced=None,
            request_limit=None,
            image_enforced=None,
            image_limit=None,
            reason="临时调额",
            updated_by="admin-1",
            video_enforced=True,
            video_limit=7,
        )
        token = _acting_user("user-1")
        try:
            result = await quota_router.get_my_quota()
        finally:
            user_context.reset_current_user(token)

        video_item = next(item for item in result.items if item.resource_type == "video_generation")
        assert video_item.videos is not None
        assert video_item.videos.limit == 7.0
        assert video_item.source == "temporary_override"
    finally:
        await service._test_engine.dispose()  # type: ignore[attr-defined]
