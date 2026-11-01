"""Focused HTTP tests for pending local registration and login."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.gateway.auth.config import AuthConfig, set_auth_config
from app.gateway.deps import get_config

_PASSWORD = "Str0ng!Password99"


@pytest.fixture(autouse=True)
def _database(tmp_path):
    from app.gateway import deps
    from app.gateway.routers.auth import _login_attempts
    from deerflow.persistence.engine import close_engine, init_engine

    set_auth_config(AuthConfig(jwt_secret="registration-approval-test-secret-min32"))
    asyncio.run(init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}", sqlite_dir=str(tmp_path)))
    deps._cached_local_provider = None
    deps._cached_repo = None
    _login_attempts.clear()
    try:
        yield
    finally:
        deps._cached_local_provider = None
        deps._cached_repo = None
        _login_attempts.clear()
        asyncio.run(close_engine())


@pytest.fixture
def client():
    from app.gateway.app import create_app

    config = SimpleNamespace(
        auth=SimpleNamespace(
            allowed_email_domains=["sz-jlc.com"],
            enforce_email_domain_on_login=False,
            local_registration=SimpleNamespace(require_admin_approval=True),
        )
    )
    app = create_app()
    app.dependency_overrides[get_config] = lambda: config
    with TestClient(app) as test_client:
        yield test_client


def test_registration_pending_is_202_without_session_and_duplicate_is_idempotent(client):
    email = "pending@sz-jlc.com"
    first = client.post("/api/v1/auth/register", json={"email": email, "password": _PASSWORD})
    second = client.post("/api/v1/auth/register", json={"email": email, "password": "An0ther!Password99"})

    assert first.status_code == 202
    assert first.json() == {"status": "pending", "message": "申请已提交，请联系管理员审核。"}
    assert "access_token" not in first.cookies
    assert second.status_code == 202

    # A changed password on the second request still cannot authenticate,
    # proving the idempotent retry did not replace the original password hash.
    changed_password = client.post(
        "/api/v1/auth/login/local",
        data={"username": email, "password": "An0ther!Password99"},
    )
    original_password = client.post(
        "/api/v1/auth/login/local",
        data={"username": email, "password": _PASSWORD},
    )
    assert changed_password.status_code == 401
    assert original_password.status_code == 403
    assert original_password.json()["detail"]["code"] == "registration_pending"


def test_pending_user_password_is_checked_before_status_error(client):
    email = "login-pending@sz-jlc.com"
    assert client.post("/api/v1/auth/register", json={"email": email, "password": _PASSWORD}).status_code == 202

    wrong = client.post("/api/v1/auth/login/local", data={"username": email, "password": "wrong-password"})
    correct = client.post("/api/v1/auth/login/local", data={"username": email, "password": _PASSWORD})

    assert wrong.status_code == 401
    assert wrong.json()["detail"]["code"] == "invalid_credentials"
    assert correct.status_code == 403
    assert correct.json()["detail"]["code"] == "registration_pending"
    assert "access_token" not in correct.cookies
