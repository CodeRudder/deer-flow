"""Tests for the best-effort Media Gateway lifecycle hooks (M1-4)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.admin import media_gateway_lifecycle
from app.gateway.admin.user_management_service import UserManagementService
from app.gateway.auth.models import User
from app.gateway.auth.repositories.sqlite import SQLiteUserRepository
from deerflow.config.app_config import AppConfig
from deerflow.persistence.base import Base
from deerflow.persistence.user.model import UserRow  # noqa: F401


@pytest_asyncio.fixture
async def repository(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'users.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repo = SQLiteUserRepository(async_sessionmaker(engine, expire_on_commit=False))
    try:
        yield repo
    finally:
        await engine.dispose()


def _config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
            "auth": {"allowed_email_domains": ["sz-jlc.com"]},
        }
    )


def _user(email: str, **overrides) -> User:
    overrides.setdefault("created_at", datetime(2026, 11, 1, tzinfo=UTC))
    overrides.setdefault("password_hash", "hash")
    return User(email=email, **overrides)


@pytest.fixture
def fake_provisioner(monkeypatch):
    provisioner = AsyncMock()
    provisioner.disable_user = AsyncMock(return_value=True)
    provisioner.update_role = AsyncMock(return_value=True)
    monkeypatch.setattr(media_gateway_lifecycle, "_get_provisioner", lambda: provisioner)
    return provisioner


class TestHooks:
    @pytest.mark.asyncio
    async def test_on_user_disabled_calls_provisioner(self, fake_provisioner):
        await media_gateway_lifecycle.on_user_disabled("u-1")
        fake_provisioner.disable_user.assert_awaited_once_with("u-1")

    @pytest.mark.asyncio
    async def test_on_user_role_changed_calls_provisioner(self, fake_provisioner):
        await media_gateway_lifecycle.on_user_role_changed("u-1", "admin")
        fake_provisioner.update_role.assert_awaited_once_with("u-1", "admin")

    @pytest.mark.asyncio
    async def test_provisioner_failure_is_swallowed(self, fake_provisioner, caplog):
        fake_provisioner.disable_user.side_effect = RuntimeError("mgw down")
        with caplog.at_level("WARNING"):
            await media_gateway_lifecycle.on_user_disabled("u-1")  # must not raise
        assert any("disable sync failed" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_unconfigured_mgw_is_noop(self, monkeypatch):
        monkeypatch.setattr(media_gateway_lifecycle, "_get_provisioner", lambda: None)
        await media_gateway_lifecycle.on_user_disabled("u-1")  # must not raise
        await media_gateway_lifecycle.on_user_role_changed("u-1", "admin")

    @pytest.mark.asyncio
    async def test_unresolvable_provisioner_is_swallowed(self, monkeypatch, caplog):
        def boom():
            raise RuntimeError("engine not initialised")

        monkeypatch.setattr(media_gateway_lifecycle, "_get_provisioner", boom)
        with caplog.at_level("WARNING"):
            await media_gateway_lifecycle.on_user_disabled("u-1")  # must not raise
        assert any("disable sync failed" in record.message for record in caplog.records)


class TestServiceWiring:
    @pytest.mark.asyncio
    async def test_disable_triggers_mgw_hook(self, repository, fake_provisioner):
        user = await repository.create_user(_user("alice@sz-jlc.com"))
        service = UserManagementService(repository, _config())

        await service.disable(str(user.id))

        fake_provisioner.disable_user.assert_awaited_once_with(str(user.id))

    @pytest.mark.asyncio
    async def test_idempotent_re_disable_does_not_retrigger_hook(self, repository, fake_provisioner):
        user = await repository.create_user(_user("bob@sz-jlc.com"))
        service = UserManagementService(repository, _config())
        await service.disable(str(user.id))
        fake_provisioner.disable_user.reset_mock()

        await service.disable(str(user.id))  # already disabled — no state change

        fake_provisioner.disable_user.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_hook_failure_does_not_fail_local_disable(self, repository, fake_provisioner):
        user = await repository.create_user(_user("carol@sz-jlc.com"))
        fake_provisioner.disable_user.side_effect = RuntimeError("mgw down")
        service = UserManagementService(repository, _config())

        item = await service.disable(str(user.id))  # local disable must succeed

        assert item["account_status"] == "disabled"
