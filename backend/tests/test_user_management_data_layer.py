"""Focused repository tests for user management and registration approval."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.auth.models import User
from app.gateway.auth.repositories.sqlite import SQLiteUserRepository
from deerflow.persistence.base import Base
from deerflow.persistence.user.model import UserRow  # noqa: F401


@pytest_asyncio.fixture
async def user_repo(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/users.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repo = SQLiteUserRepository(async_sessionmaker(engine, expire_on_commit=False))
    try:
        yield repo
    finally:
        await engine.dispose()


def _user(email: str, *, created_at: datetime, **overrides) -> User:
    overrides.setdefault("password_hash", "hash")
    return User(email=email, created_at=created_at, **overrides)


@pytest.mark.asyncio
async def test_user_fields_summary_filter_search_and_pagination(user_repo):
    base = datetime(2026, 7, 16, tzinfo=UTC)
    pending_early = await user_repo.create_user(
        _user(
            "pending.first@sz-jlc.com",
            created_at=base + timedelta(minutes=3),
            account_status="pending",
            registration_requested_at=base + timedelta(minutes=1),
        )
    )
    pending_late = await user_repo.create_user(
        _user(
            "pending.second@sz-jlc.com",
            created_at=base + timedelta(minutes=2),
            account_status="pending",
            registration_requested_at=base + timedelta(minutes=2),
        )
    )
    active = await user_repo.create_user(_user("ACTIVE@sz-jlc.com", created_at=base, account_status="active"))
    disabled = await user_repo.create_user(_user("disabled@sz-jlc.com", created_at=base + timedelta(minutes=4), account_status="disabled"))

    summary = await user_repo.get_user_summary()
    assert summary.model_dump() == {"total": 4, "active": 1, "pending": 2, "disabled": 1}

    pending_page = await user_repo.list_users(status="pending", keyword=None, page=1, page_size=10)
    assert pending_page.total == 2
    assert [item.id for item in pending_page.items] == [pending_early.id, pending_late.id]

    all_page = await user_repo.list_users(status=None, keyword=None, page=1, page_size=2)
    assert all_page.total == 4
    assert all_page.page == 1
    assert all_page.page_size == 2
    assert [item.id for item in all_page.items] == [disabled.id, pending_early.id]

    email_search = await user_repo.list_users(status=None, keyword="active@SZ-JLC", page=1, page_size=10)
    assert [item.id for item in email_search.items] == [active.id]
    id_search = await user_repo.list_users(status=None, keyword=str(pending_late.id)[:10].upper(), page=1, page_size=10)
    assert [item.id for item in id_search.items] == [pending_late.id]

    fetched = await user_repo.get_user_by_id(str(pending_early.id))
    assert fetched is not None
    assert fetched.account_status == "pending"
    assert fetched.registration_requested_at == base + timedelta(minutes=1)
    assert fetched.registration_approved_at is None
    assert fetched.approval_email_status is None


@pytest.mark.asyncio
async def test_status_transitions_are_atomic_and_idempotent(user_repo):
    now = datetime(2026, 7, 16, 8, tzinfo=UTC)
    pending = await user_repo.create_user(
        _user(
            "pending@sz-jlc.com",
            created_at=now,
            account_status="pending",
            registration_requested_at=now,
        )
    )
    admin = await user_repo.create_user(_user("admin@sz-jlc.com", created_at=now, account_status="active", system_role="admin"))

    approved = await user_repo.approve_user(str(pending.id), admin_id=str(admin.id), now=now + timedelta(minutes=1))
    assert approved.changed is True
    assert approved.user is not None
    assert approved.user.account_status == "active"
    assert approved.user.registration_approved_by == str(admin.id)
    assert approved.user.registration_approved_at == now + timedelta(minutes=1)
    assert approved.user.approval_email_status == "pending"

    repeated = await user_repo.approve_user(str(pending.id), admin_id="other-admin", now=now + timedelta(minutes=2))
    assert repeated.changed is False
    assert repeated.user is not None
    assert repeated.user.registration_approved_by == str(admin.id)
    assert repeated.user.registration_approved_at == now + timedelta(minutes=1)

    disabled = await user_repo.disable_user(str(pending.id))
    assert disabled.changed is True
    assert disabled.user is not None
    assert disabled.user.account_status == "disabled"
    assert disabled.user.token_version == 1
    repeated_disable = await user_repo.disable_user(str(pending.id))
    assert repeated_disable.changed is False
    assert repeated_disable.user is not None
    assert repeated_disable.user.token_version == 1

    enabled = await user_repo.enable_user(str(pending.id))
    assert enabled.changed is True
    assert enabled.user is not None
    assert enabled.user.account_status == "active"
    assert enabled.user.token_version == 1

    rejected_admin_disable = await user_repo.disable_user(str(admin.id))
    assert rejected_admin_disable.changed is False
    assert rejected_admin_disable.user is not None
    assert rejected_admin_disable.user.account_status == "active"


@pytest.mark.asyncio
async def test_concurrent_status_transitions_have_one_winner(user_repo):
    now = datetime(2026, 7, 16, 8, tzinfo=UTC)
    pending = await user_repo.create_user(
        _user(
            "concurrent@sz-jlc.com",
            created_at=now,
            account_status="pending",
            registration_requested_at=now,
        )
    )

    approvals = await asyncio.gather(
        user_repo.approve_user(str(pending.id), admin_id="admin-1", now=now),
        user_repo.approve_user(str(pending.id), admin_id="admin-2", now=now + timedelta(seconds=1)),
    )
    assert sum(result.changed for result in approvals) == 1
    approved = await user_repo.get_user_by_id(str(pending.id))
    assert approved is not None
    assert approved.registration_approved_by in {"admin-1", "admin-2"}

    disables = await asyncio.gather(
        user_repo.disable_user(str(pending.id)),
        user_repo.disable_user(str(pending.id)),
    )
    assert sum(result.changed for result in disables) == 1
    disabled = await user_repo.get_user_by_id(str(pending.id))
    assert disabled is not None
    assert disabled.account_status == "disabled"
    assert disabled.token_version == 1


@pytest.mark.asyncio
async def test_approval_email_claim_finish_and_stale_reclaim(user_repo):
    now = datetime(2026, 7, 16, 8, tzinfo=UTC)
    user = await user_repo.create_user(
        _user(
            "mail@sz-jlc.com",
            created_at=now,
            account_status="active",
            registration_requested_at=now - timedelta(minutes=5),
            registration_approved_at=now - timedelta(minutes=1),
            registration_approved_by="admin-id",
            approval_email_status="pending",
        )
    )

    claimed = await user_repo.claim_approval_email(
        str(user.id),
        attempted_at=now,
        stale_before=now - timedelta(minutes=2),
    )
    assert claimed.changed is True
    assert claimed.user is not None
    assert claimed.user.approval_email_status == "sending"
    assert claimed.user.approval_email_last_attempt_at == now

    duplicate = await user_repo.claim_approval_email(
        str(user.id),
        attempted_at=now + timedelta(seconds=1),
        stale_before=now - timedelta(minutes=2),
    )
    assert duplicate.changed is False
    assert duplicate.user is not None
    assert duplicate.user.approval_email_last_attempt_at == now

    reclaimed = await user_repo.claim_approval_email(
        str(user.id),
        attempted_at=now + timedelta(minutes=3),
        stale_before=now + timedelta(minutes=1),
    )
    assert reclaimed.changed is True
    assert reclaimed.user is not None
    assert reclaimed.user.approval_email_last_attempt_at == now + timedelta(minutes=3)

    failed = await user_repo.finish_approval_email(str(user.id), success=False)
    assert failed.changed is True
    assert failed.user is not None
    assert failed.user.approval_email_status == "failed"

    retry = await user_repo.claim_approval_email(
        str(user.id),
        attempted_at=now + timedelta(minutes=4),
        stale_before=now + timedelta(minutes=2),
    )
    assert retry.changed is True
    sent = await user_repo.finish_approval_email(str(user.id), success=True)
    assert sent.changed is True
    assert sent.user is not None
    assert sent.user.approval_email_status == "sent"
    assert (await user_repo.finish_approval_email(str(user.id), success=True)).changed is False


@pytest.mark.asyncio
async def test_local_email_update_is_conditional_and_bumps_token_once(user_repo):
    now = datetime(2026, 7, 16, tzinfo=UTC)
    local = await user_repo.create_user(_user("before@sz-jlc.com", created_at=now, token_version=4))
    occupied = await user_repo.create_user(_user("occupied@sz-jlc.com", created_at=now))
    external = await user_repo.create_user(
        _user(
            "external@sz-jlc.com",
            created_at=now,
            password_hash=None,
            oauth_provider="company-oidc",
            oauth_id="subject-1",
        )
    )

    updated = await user_repo.update_local_email(str(local.id), "after@sz-jlc.com")
    assert updated.changed is True
    assert updated.user is not None
    assert str(updated.user.email) == "after@sz-jlc.com"
    assert updated.user.token_version == 5

    same = await user_repo.update_local_email(str(local.id), "after@sz-jlc.com")
    assert same.changed is False
    assert same.user is not None
    assert same.user.token_version == 5

    external_update = await user_repo.update_local_email(str(external.id), "new.external@sz-jlc.com")
    assert external_update.changed is False
    assert external_update.user is not None
    assert str(external_update.user.email) == "external@sz-jlc.com"

    with pytest.raises(ValueError, match="already registered"):
        await user_repo.update_local_email(str(local.id), str(occupied.email))
    after_conflict = await user_repo.get_user_by_id(str(local.id))
    assert after_conflict is not None
    assert str(after_conflict.email) == "after@sz-jlc.com"
    assert after_conflict.token_version == 5
