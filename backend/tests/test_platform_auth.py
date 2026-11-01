from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.auth.password import verify_password
from app.gateway.auth.platform_jwt import PlatformTokenError, parse_platform_user_claims
from app.gateway.auth.platform_provider import PlatformAuthProvider
from app.gateway.auth_middleware import AuthMiddleware
from deerflow.config.app_config import AppConfig
from deerflow.config.platform_auth_config import PlatformAuthConfig
from deerflow.config.sandbox_config import SandboxConfig


@pytest.fixture()
def rsa_keys() -> tuple[str, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private_pem, public_pem


def _token(private_pem: str, **overrides) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": "company-user-1",
        "email": "user@example.com",
        "preferred_username": "user",
        "name": "zhangsan",
        "iss": "ai-code-base-platform",
        "aud": "ai-code-base-platform",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    payload.update(overrides)
    return jwt.encode(payload, private_pem, algorithm="RS256")


def _platform_config(public_pem: str) -> PlatformAuthConfig:
    return PlatformAuthConfig(enabled=True, public_key=public_pem)


def test_parse_platform_user_claims_validates_rs256_jwt(rsa_keys):
    private_pem, public_pem = rsa_keys
    claims = parse_platform_user_claims(_token(private_pem), _platform_config(public_pem))
    assert claims.sub == "company-user-1"
    assert claims.email == "user@example.com"
    assert claims.preferred_username == "user"
    assert claims.name == "zhangsan"


def test_parse_platform_user_claims_defaults_email_from_name(rsa_keys):
    private_pem, public_pem = rsa_keys
    claims = parse_platform_user_claims(_token(private_pem, email=""), _platform_config(public_pem))
    assert claims.name == "zhangsan"
    assert claims.email == "zhangsan@sz-jlc.com"


def test_parse_platform_user_claims_rejects_wrong_audience(rsa_keys):
    private_pem, public_pem = rsa_keys
    with pytest.raises(PlatformTokenError, match="audience"):
        parse_platform_user_claims(_token(private_pem, aud="wrong"), _platform_config(public_pem))


def test_parse_platform_user_claims_allows_old_iat_when_exp_is_valid(rsa_keys):
    private_pem, public_pem = rsa_keys
    old = datetime.now(UTC) - timedelta(minutes=30)
    claims = parse_platform_user_claims(
        _token(private_pem, iat=old, exp=datetime.now(UTC) + timedelta(minutes=5)),
        _platform_config(public_pem),
    )
    assert claims.name == "zhangsan"


class FakeUserRepository:
    def __init__(self):
        self.users: list[User] = []

    async def create_user(self, user: User) -> User:
        self.users.append(user)
        return user

    async def get_user_by_id(self, user_id: str) -> User | None:
        return next((u for u in self.users if str(u.id) == user_id), None)

    async def get_user_by_email(self, email: str) -> User | None:
        return next((u for u in self.users if u.email == email), None)

    async def update_user(self, user: User) -> User:
        return user

    async def count_users(self) -> int:
        return len(self.users)

    async def count_admin_users(self) -> int:
        return sum(1 for u in self.users if u.system_role == "admin")

    async def get_user_by_oauth(self, provider: str, oauth_id: str) -> User | None:
        return next((u for u in self.users if u.oauth_provider == provider and u.oauth_id == oauth_id), None)


@pytest.mark.asyncio
async def test_platform_provider_creates_shadow_user_with_default_password(rsa_keys):
    _, public_pem = rsa_keys
    repo = FakeUserRepository()
    provider = PlatformAuthProvider(repo, _platform_config(public_pem))
    claims = SimpleNamespace(sub="company-user-1", name="zhangsan", email="user@example.com")

    user = await provider.get_or_create_user_from_claims(claims)

    assert user.oauth_provider == "company_gateway"
    assert user.oauth_id == "zhangsan"
    assert user.system_role == "user"
    assert user.password_hash is not None
    assert verify_password("jlc@123456", user.password_hash)


@pytest.mark.asyncio
async def test_platform_provider_binds_existing_email_without_overwriting_password(rsa_keys):
    _, public_pem = rsa_keys
    repo = FakeUserRepository()
    existing = User(email="user@example.com", password_hash="already-set", system_role="admin")
    repo.users.append(existing)
    provider = PlatformAuthProvider(repo, _platform_config(public_pem))
    claims = SimpleNamespace(sub="company-user-1", name="zhangsan", email="user@example.com")

    user = await provider.get_or_create_user_from_claims(claims)

    assert user.id == existing.id
    assert user.oauth_provider == "company_gateway"
    assert user.oauth_id == "zhangsan"
    assert user.system_role == "admin"
    assert user.password_hash == "already-set"


def test_auth_middleware_accepts_platform_header_and_stamps_state(rsa_keys):
    private_pem, public_pem = rsa_keys
    config = AppConfig(
        sandbox=SandboxConfig(use="test"),
        platform_auth=PlatformAuthConfig(enabled=True, public_key=public_pem),
    )
    user = User(email="user@example.com", password_hash="hash")

    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/api/protected")
    async def protected():
        from deerflow.runtime.user_context import get_current_user

        current = get_current_user()
        return {"user_id": str(current.id) if current else None}

    with (
        patch("app.gateway.auth_middleware.get_config", return_value=config),
        patch("app.gateway.auth_middleware.get_platform_provider") as provider_factory,
    ):
        provider_factory.return_value.get_or_create_user_from_claims = AsyncMock(return_value=user)
        res = TestClient(app).get(
            "/api/protected",
            headers={"JLC-WEB-API-AccessToken": _token(private_pem)},
        )

    assert res.status_code == 200
    assert res.json() == {"user_id": str(user.id)}


@pytest.mark.parametrize(
    ("account_status", "error_code"),
    [("pending", "registration_pending"), ("disabled", "account_disabled")],
)
def test_auth_middleware_rejects_non_active_platform_user(rsa_keys, account_status, error_code):
    private_pem, public_pem = rsa_keys
    config = AppConfig(
        sandbox=SandboxConfig(use="test"),
        platform_auth=PlatformAuthConfig(enabled=True, public_key=public_pem),
    )
    user = User(email="user@example.com", password_hash="hash", account_status=account_status)

    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/api/protected")
    async def protected():
        return {"ok": True}

    with (
        patch("app.gateway.auth_middleware.get_config", return_value=config),
        patch("app.gateway.auth_middleware.get_platform_provider") as provider_factory,
    ):
        provider_factory.return_value.get_or_create_user_from_claims = AsyncMock(return_value=user)
        res = TestClient(app).get(
            "/api/protected",
            headers={"JLC-WEB-API-AccessToken": _token(private_pem)},
        )

    assert res.status_code == 403
    assert res.json()["detail"]["code"] == error_code


def test_app_config_loads_platform_auth_from_yaml_env(tmp_path, monkeypatch, rsa_keys):
    _, public_pem = rsa_keys
    config_path = tmp_path / "config.yaml"
    extensions_path = tmp_path / "extensions_config.json"
    extensions_path.write_text('{"mcpServers": {}, "skills": {}}', encoding="utf-8")
    config_path.write_text(
        """
sandbox:
  use: test
platform_auth:
  enabled: true
  public_key: $PLATFORM_AUTH_PUBLIC_KEY
  default_password: $PLATFORM_AUTH_DEFAULT_PASSWORD
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", str(extensions_path))
    monkeypatch.setenv("PLATFORM_AUTH_PUBLIC_KEY", public_pem)
    monkeypatch.setenv("PLATFORM_AUTH_DEFAULT_PASSWORD", "jlc@123456")

    config = AppConfig.from_file(str(config_path))

    assert config.platform_auth.enabled is True
    assert config.platform_auth.public_key == public_pem
    assert config.platform_auth.default_password == "jlc@123456"
