"""API regressions for local-auth company email domain enforcement."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.gateway.auth.config import AuthConfig, set_auth_config
from app.gateway.deps import get_config

_TEST_SECRET = "test-secret-local-email-domains-minimum-32-chars"
_PASSWORD = "Str0ng!Password99"
_NEW_PASSWORD = "An0ther!Password99"


@pytest.fixture(autouse=True)
def _auth_database(tmp_path):
    """Give every API test isolated auth persistence and singleton state."""
    from app.gateway import deps
    from app.gateway.routers.auth import _SETUP_STATUS_CACHE, _SETUP_STATUS_INFLIGHT, _login_attempts
    from deerflow.persistence.engine import close_engine, init_engine

    set_auth_config(AuthConfig(jwt_secret=_TEST_SECRET))
    database_url = f"sqlite+aiosqlite:///{tmp_path}/local_email_domains.db"
    asyncio.run(init_engine("sqlite", url=database_url, sqlite_dir=str(tmp_path)))
    deps._cached_local_provider = None
    deps._cached_repo = None
    _login_attempts.clear()
    _SETUP_STATUS_CACHE.clear()
    _SETUP_STATUS_INFLIGHT.clear()
    try:
        yield
    finally:
        deps._cached_local_provider = None
        deps._cached_repo = None
        _login_attempts.clear()
        _SETUP_STATUS_CACHE.clear()
        _SETUP_STATUS_INFLIGHT.clear()
        asyncio.run(close_engine())


@pytest.fixture()
def auth_config():
    """Mutable request-time config used to exercise hot-reload semantics."""
    return SimpleNamespace(
        auth=SimpleNamespace(
            allowed_email_domains=["sz-jlc.com", "partner.example"],
            enforce_email_domain_on_login=False,
        )
    )


@pytest.fixture()
def client(auth_config):
    """Build the real auth router without starting the full app lifespan."""
    from app.gateway.app import create_app

    app = create_app()
    app.dependency_overrides[get_config] = lambda: auth_config
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        test_client.close()
        app.dependency_overrides.clear()


def _register(client: TestClient, email: str, password: str = _PASSWORD):
    return client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )


def _initialize(client: TestClient, email: str, password: str = _PASSWORD):
    return client.post(
        "/api/v1/auth/initialize",
        json={"email": email, "password": password},
    )


def _login(client: TestClient, email: str, password: str = _PASSWORD):
    return client.post(
        "/api/v1/auth/login/local",
        data={"username": email, "password": password},
    )


def _get_user_by_email(email: str):
    from app.gateway.deps import get_local_provider

    return asyncio.run(get_local_provider().get_user_by_email(email))


def test_register_allows_configured_domain_and_sets_session(client):
    response = _register(client, "employee@partner.example")

    assert response.status_code == 201
    assert response.json()["email"] == "employee@partner.example"
    assert "access_token" in response.cookies


def test_register_rejects_unapproved_domain_before_hash_or_create(client, auth_config):
    auth_config.auth.enforce_email_domain_on_login = False
    hash_password = AsyncMock(return_value="$dfv2$unused")

    with patch("app.gateway.auth.local_provider.hash_password_async", hash_password):
        response = _register(client, "outsider@example.com")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "email_domain_not_allowed"
    assert response.json()["detail"]["message"] == "请使用公司邮箱注册"
    assert "access_token" not in response.cookies
    assert _get_user_by_email("outsider@example.com") is None
    hash_password.assert_not_awaited()


def test_register_keeps_existing_duplicate_email_error(client):
    email = "duplicate@sz-jlc.com"
    assert _register(client, email).status_code == 201

    response = _register(client, email, password=_NEW_PASSWORD)

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "email_already_exists"


def test_login_reads_switch_on_each_request_and_rejects_without_session(client, auth_config):
    email = "legacy@example.com"
    assert _initialize(client, email).status_code == 201
    client.cookies.clear()

    auth_config.auth.enforce_email_domain_on_login = False
    allowed = _login(client, email)
    assert allowed.status_code == 200
    assert "access_token" in allowed.cookies

    client.cookies.clear()
    auth_config.auth.enforce_email_domain_on_login = True
    denied = _login(client, email)

    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "email_domain_not_allowed"
    assert denied.json()["detail"]["message"] == "请使用公司邮箱注册"
    assert "access_token" not in denied.cookies
    assert "access_token" not in denied.json()


def test_login_switch_enabled_allows_approved_domain(client, auth_config):
    auth_config.auth.enforce_email_domain_on_login = True
    email = "employee@sz-jlc.com"
    assert _register(client, email).status_code == 201
    client.cookies.clear()

    response = _login(client, email)

    assert response.status_code == 200
    assert "access_token" in response.cookies


def test_login_domain_rejection_does_not_reset_password_failures(client, auth_config):
    from app.gateway.routers.auth import _login_attempts

    email = "legacy@example.com"
    assert _initialize(client, email).status_code == 201
    client.cookies.clear()
    auth_config.auth.enforce_email_domain_on_login = True

    bad_password = _login(client, email, password="Wr0ng!Password99")
    assert bad_password.status_code == 401
    assert bad_password.json()["detail"]["code"] == "invalid_credentials"
    assert sum(failures for failures, _ in _login_attempts.values()) == 1

    correct_password = _login(client, email)
    assert correct_password.status_code == 403
    assert correct_password.json()["detail"]["code"] == "email_domain_not_allowed"
    assert sum(failures for failures, _ in _login_attempts.values()) == 1
    assert "access_token" not in correct_password.cookies

    auth_config.auth.enforce_email_domain_on_login = False
    successful_login = _login(client, email)
    assert successful_login.status_code == 200
    assert _login_attempts == {}


def test_change_password_allows_approved_new_email(client, auth_config):
    auth_config.auth.enforce_email_domain_on_login = False
    original_email = "before@sz-jlc.com"
    new_email = "after@partner.example"
    assert _register(client, original_email).status_code == 201

    response = client.post(
        "/api/v1/auth/change-password",
        json={
            "current_password": _PASSWORD,
            "new_password": _NEW_PASSWORD,
            "new_email": new_email,
        },
    )

    assert response.status_code == 200
    assert _get_user_by_email(original_email) is None
    updated = _get_user_by_email(new_email)
    assert updated is not None
    assert updated.token_version == 1
    assert "access_token" in response.cookies


def test_change_password_domain_rejection_is_atomic(client, auth_config):
    auth_config.auth.enforce_email_domain_on_login = False
    email = "unchanged@sz-jlc.com"
    assert _register(client, email).status_code == 201
    before = _get_user_by_email(email)
    assert before is not None

    response = client.post(
        "/api/v1/auth/change-password",
        json={
            "current_password": _PASSWORD,
            "new_password": _NEW_PASSWORD,
            "new_email": "outsider@example.com",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "email_domain_not_allowed"
    assert response.json()["detail"]["message"] == "请使用公司邮箱注册"
    assert "access_token" not in response.cookies
    after = _get_user_by_email(email)
    assert after is not None
    assert after.email == before.email
    assert after.password_hash == before.password_hash
    assert after.token_version == before.token_version
    assert after.needs_setup == before.needs_setup
    assert _get_user_by_email("outsider@example.com") is None


def test_change_password_checks_current_password_before_new_email_domain(client):
    email = "employee@sz-jlc.com"
    assert _register(client, email).status_code == 201
    before = _get_user_by_email(email)
    assert before is not None

    response = client.post(
        "/api/v1/auth/change-password",
        json={
            "current_password": "Wr0ng!Password99",
            "new_password": _NEW_PASSWORD,
            "new_email": "outsider@example.com",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_credentials"
    after = _get_user_by_email(email)
    assert after is not None
    assert after.password_hash == before.password_hash
    assert after.token_version == before.token_version


def test_legacy_external_user_can_change_only_password(client, auth_config):
    email = "legacy@example.com"
    assert _initialize(client, email).status_code == 201
    auth_config.auth.enforce_email_domain_on_login = True

    response = client.post(
        "/api/v1/auth/change-password",
        json={
            "current_password": _PASSWORD,
            "new_password": _NEW_PASSWORD,
        },
    )

    assert response.status_code == 200
    updated = _get_user_by_email(email)
    assert updated is not None
    assert updated.email == email
    assert updated.token_version == 1


def test_initialize_is_excluded_from_local_email_domain_policy(client, auth_config):
    auth_config.auth.enforce_email_domain_on_login = True

    response = _initialize(client, "first-admin@example.com")

    assert response.status_code == 201
    assert response.json()["email"] == "first-admin@example.com"
    assert "access_token" in response.cookies
