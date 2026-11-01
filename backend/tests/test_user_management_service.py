"""Focused tests for administrator user-management orchestration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.admin.user_management_service import UserManagementError, UserManagementService
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


def _config(*, approval: bool = False) -> AppConfig:
    local_registration = {"require_admin_approval": False}
    if approval:
        local_registration = {
            "require_admin_approval": True,
            "approval_email": {
                "enabled": True,
                "from_address": "noreply@sz-jlc.com",
                "smtp": {
                    "host": "smtp.example.com",
                    "username": "smtp-user",
                    "password": "smtp-secret",
                },
            },
        }
    return AppConfig.model_validate(
        {
            "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
            "auth": {
                "allowed_email_domains": ["sz-jlc.com"],
                "local_registration": local_registration,
            },
        }
    )


def _user(email: str, **overrides) -> User:
    overrides.setdefault("created_at", datetime(2026, 7, 16, tzinfo=UTC))
    overrides.setdefault("password_hash", "hash")
    return User(email=email, **overrides)


@pytest.mark.asyncio
async def test_summary_list_source_and_allowed_actions(repository):
    local = await repository.create_user(_user("local@sz-jlc.com"))
    pending = await repository.create_user(
        _user(
            "pending@sz-jlc.com",
            account_status="pending",
            registration_requested_at=datetime(2026, 7, 16, tzinfo=UTC),
        )
    )
    oidc = await repository.create_user(_user("oidc@sz-jlc.com", password_hash=None, oauth_provider="keycloak", oauth_id="oidc-sub"))
    platform = await repository.create_user(_user("platform@sz-jlc.com", password_hash=None, oauth_provider="company_gateway", oauth_id="platform-sub"))
    admin = await repository.create_user(_user("admin@sz-jlc.com", system_role="admin"))

    service = UserManagementService(repository, _config(approval=True))
    summary = await service.summary()
    assert summary == {
        "registration_approval_enabled": True,
        "total": 5,
        "active": 4,
        "pending": 1,
        "disabled": 0,
    }

    result = await service.list_users(status=None, keyword=None, page=1, page_size=10)
    by_id = {item["id"]: item for item in result["items"]}
    assert by_id[str(local.id)]["allowed_actions"] == ["edit", "disable"]
    assert by_id[str(pending.id)]["allowed_actions"] == ["edit", "approve"]
    assert by_id[str(oidc.id)]["source"] == "oidc"
    assert by_id[str(oidc.id)]["allowed_actions"] == ["disable"]
    assert by_id[str(platform.id)]["source"] == "platform"
    assert by_id[str(admin.id)]["allowed_actions"] == []
    assert "password_hash" not in by_id[str(local.id)]


@pytest.mark.asyncio
async def test_approve_failure_keeps_user_active_and_exposes_retry(repository):
    pending = await repository.create_user(
        _user(
            "pending@sz-jlc.com",
            account_status="pending",
            registration_requested_at=datetime(2026, 7, 16, tzinfo=UTC),
        )
    )
    service = UserManagementService(repository, _config(approval=True))

    with patch(
        "app.gateway.admin.user_management_service.send_approval_email",
        new=AsyncMock(side_effect=TimeoutError("smtp unavailable")),
    ):
        result = await service.approve(str(pending.id), admin_id="admin-id")

    assert result["account_status"] == "active"
    assert result["approval_email_status"] == "failed"
    assert "retry_approval_email" in result["allowed_actions"]
    stored = await repository.get_user_by_id(str(pending.id))
    assert stored is not None
    assert stored.account_status == "active"
    assert stored.registration_approved_by == "admin-id"


@pytest.mark.asyncio
async def test_repeated_approval_does_not_send_or_overwrite_first_approver(repository):
    pending = await repository.create_user(
        _user(
            "idempotent@sz-jlc.com",
            account_status="pending",
            registration_requested_at=datetime(2026, 7, 16, tzinfo=UTC),
        )
    )
    service = UserManagementService(repository, _config(approval=True))
    sender = AsyncMock()

    with patch("app.gateway.admin.user_management_service.send_approval_email", new=sender):
        first = await service.approve(str(pending.id), admin_id="first-admin")
        second = await service.approve(str(pending.id), admin_id="second-admin")

    assert first["approval_email_status"] == "sent"
    assert second["registration_approved_by"] == "first-admin"
    sender.assert_awaited_once()


@pytest.mark.asyncio
async def test_retry_sends_once_and_rejects_non_retryable(repository):
    user = await repository.create_user(
        _user(
            "approved@sz-jlc.com",
            registration_requested_at=datetime(2026, 7, 16, tzinfo=UTC),
            registration_approved_at=datetime(2026, 7, 16, 1, tzinfo=UTC),
            registration_approved_by="admin-id",
            approval_email_status="failed",
        )
    )
    service = UserManagementService(repository, _config(approval=True))
    sender = AsyncMock()
    with patch("app.gateway.admin.user_management_service.send_approval_email", new=sender):
        result = await service.retry_approval_email(str(user.id))

    assert result["approval_email_status"] == "sent"
    sender.assert_awaited_once()
    with pytest.raises(UserManagementError, match="not retryable") as error:
        await service.retry_approval_email(str(user.id))
    assert error.value.code == "approval_email_not_retryable"


@pytest.mark.asyncio
async def test_recent_sending_email_is_in_progress(repository):
    user = await repository.create_user(
        _user(
            "sending@sz-jlc.com",
            registration_approved_at=datetime.now(UTC) - timedelta(minutes=1),
            registration_approved_by="admin-id",
            approval_email_status="sending",
            approval_email_last_attempt_at=datetime.now(UTC),
        )
    )
    service = UserManagementService(repository, _config(approval=True))

    with pytest.raises(UserManagementError) as error:
        await service.retry_approval_email(str(user.id))
    assert error.value.code == "approval_email_in_progress"


@pytest.mark.asyncio
async def test_email_edit_validates_source_domain_and_conflicts(repository):
    local = await repository.create_user(_user("local@sz-jlc.com", token_version=2))
    await repository.create_user(_user("occupied@sz-jlc.com"))
    admin = await repository.create_user(_user("admin@sz-jlc.com", system_role="admin"))
    external = await repository.create_user(_user("external@sz-jlc.com", password_hash=None, oauth_provider="keycloak", oauth_id="subject"))
    service = UserManagementService(repository, _config())

    updated = await service.update_email(str(local.id), "updated@sz-jlc.com")
    assert updated["email"] == "updated@sz-jlc.com"
    assert (await repository.get_user_by_id(str(local.id))).token_version == 3

    with pytest.raises(UserManagementError) as source_error:
        await service.update_email(str(external.id), "new@sz-jlc.com")
    assert source_error.value.code == "external_identity_email_not_editable"

    with pytest.raises(UserManagementError) as admin_error:
        await service.update_email(str(admin.id), "admin-new@sz-jlc.com")
    assert admin_error.value.code == "admin_profile_edit_not_allowed"

    with pytest.raises(UserManagementError) as conflict_error:
        await service.update_email(str(local.id), "occupied@sz-jlc.com")
    assert conflict_error.value.code == "email_already_exists"
