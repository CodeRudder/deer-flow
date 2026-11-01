"""Router tests for the Media Gateway proxy routes (M2 batch 1).

Covers the acceptance points for the artifact proxy route and the console-link
API: per-user token forwarding, 401 when unauthenticated, 404 passthrough,
cross-user isolation (user B's own token is used for B's request — MGW then
404s A's file for B), and the console-link happy/unconfigured/degraded paths.

Identity always comes from the session user (acceptance A5): the routes accept
no user/token parameter, so the "cannot pass another user's identity" test is
structural — we assert the forwarded Authorization header is always the
*current session user's* token.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.gateway.routers.media_gateway as mgw_router
from app.gateway.auth.models import User
from deerflow.media_gateway.client import MediaGatewayClient, MediaGatewayError

MGW_BASE = "http://mgw.test"


def _make_user(email: str) -> User:
    return User(email=email, password_hash="x", system_role="user", id=uuid4())


@pytest.fixture
def user_a() -> User:
    return _make_user("a@example.com")


@pytest.fixture
def user_b() -> User:
    return _make_user("b@example.com")


def _provisioner(user: User, tokens: dict[str, str] | None = None) -> AsyncMock:
    """Mock provisioner whose get_token resolves the *session user's* own token."""
    if tokens is None:
        p = AsyncMock()
        p.get_token = AsyncMock(return_value="tok-A")
        return p
    p = AsyncMock()

    async def _get(user_id: str) -> str:
        return tokens[user_id]

    p.get_token = AsyncMock(side_effect=_get)
    return p


def _install_runtime(provisioner, upstream_handler) -> dict:
    """Swap in a mock runtime backed by httpx.MockTransport.

    ``upstream_handler(request, seen) -> httpx.Response``; ``seen`` records what
    the proxy actually sent upstream (headers, path). Returns ``seen``.
    """
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        return upstream_handler(request, seen)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url=MGW_BASE, transport=transport)
    mgw_client = MediaGatewayClient(MGW_BASE, "adm", transport=transport)
    runtime = mgw_router.MediaGatewayRuntime(client=mgw_client, file_client=client, provisioner=provisioner)
    mgw_router.set_runtime(runtime)
    return seen


@pytest.fixture(autouse=True)
def _reset_runtime():
    yield
    from deerflow.media_gateway.settings import set_media_gateway_settings

    mgw_router.reset_runtime()
    set_media_gateway_settings(None)


def _app(as_user: User) -> FastAPI:
    from starlette.middleware.base import BaseHTTPMiddleware

    from app.gateway.auth_disabled import AUTH_SOURCE_SESSION

    class _StubAuthMiddleware(BaseHTTPMiddleware):
        """Stamp the fake user + auth_source so get_current_user_from_request
        takes its validated fast path, mirroring production AuthMiddleware."""

        async def dispatch(self, request, call_next):
            request.state.user = as_user
            request.state.auth_source = AUTH_SOURCE_SESSION
            return await call_next(request)

    app = FastAPI()
    app.add_middleware(_StubAuthMiddleware)
    app.include_router(mgw_router.router)
    return app


def _settings(monkeypatch, console_url: str | None) -> None:
    from deerflow.media_gateway.settings import MediaGatewaySettings, set_media_gateway_settings

    set_media_gateway_settings(MediaGatewaySettings(base_url=MGW_BASE, admin_token="adm", console_url=console_url))


class TestFileProxy:
    def test_forwards_with_current_user_token_and_content_type(self, user_a):
        provisioner = _provisioner(user_a)

        def handler(request: httpx.Request, seen: dict) -> httpx.Response:
            seen["auth"] = request.headers.get("Authorization")
            seen["path"] = request.url.path
            return httpx.Response(200, content=b"pngbytes", headers={"Content-Type": "image/png"})

        seen = _install_runtime(provisioner, handler)
        with TestClient(_app(user_a)) as client:
            response = client.get("/api/media-gateway/file/outputs/generated/img.png")

        assert response.status_code == 200
        assert response.content == b"pngbytes"
        assert response.headers["content-type"] == "image/png"
        assert seen["path"] == "/file/outputs/generated/img.png"
        # The Authorization seen upstream must be the session user's own token.
        assert seen["auth"] == "Bearer tok-A"

    def test_404_passthrough(self, user_a):
        def handler(request: httpx.Request, seen: dict) -> httpx.Response:
            return httpx.Response(404, json={"error": {"code": "not_found", "message": "no such file"}})

        _install_runtime(_provisioner(user_a), handler)
        with TestClient(_app(user_a)) as client:
            response = client.get("/api/media-gateway/file/outputs/missing.png")

        assert response.status_code == 404
        assert "不存在" in response.json()["detail"]

    def test_upstream_401_maps_to_401(self, user_a):
        def handler(request: httpx.Request, seen: dict) -> httpx.Response:
            return httpx.Response(401, json={"error": {"code": "unauthorized", "message": "bad token"}})

        _install_runtime(_provisioner(user_a), handler)
        with TestClient(_app(user_a)) as client:
            response = client.get("/api/media-gateway/file/outputs/x.png")

        assert response.status_code == 401

    def test_cross_user_gets_own_token_and_mgw_404s(self, user_a, user_b):
        """User B requests A's file path: the proxy forwards **B's** token, and
        MGW (which scopes files per user) answers 404 — never A's bytes."""

        def handler(request: httpx.Request, seen: dict) -> httpx.Response:
            seen["auth"] = request.headers.get("Authorization")
            return httpx.Response(404, json={"error": {"code": "not_found", "message": "not your file"}})

        seen = _install_runtime(_provisioner(user_b, {str(user_a.id): "tok-A", str(user_b.id): "tok-B"}), handler)

        with TestClient(_app(user_b)) as client:
            response = client.get("/api/media-gateway/file/outputs/user-a-private.png")

        assert response.status_code == 404
        assert seen["auth"] == "Bearer tok-B"  # B's own token, never A's

    def test_unauthenticated_is_401(self, user_a):
        def handler(request: httpx.Request, seen: dict) -> httpx.Response:
            raise AssertionError("upstream must not be reached when unauthenticated")

        _install_runtime(_provisioner(user_a), handler)
        bare = FastAPI()
        bare.include_router(mgw_router.router)
        with TestClient(bare) as client:
            response = client.get("/api/media-gateway/file/outputs/x.png")

        assert response.status_code == 401

    def test_user_without_mapping_is_404_with_hint(self, user_a):
        provisioner = _provisioner(user_a)
        provisioner.get_token.side_effect = MediaGatewayError(status=404, code="not_provisioned", message="no mapping")

        def handler(request: httpx.Request, seen: dict) -> httpx.Response:
            raise AssertionError("upstream must not be reached when unmapped")

        _install_runtime(provisioner, handler)
        with TestClient(_app(user_a)) as client:
            response = client.get("/api/media-gateway/file/outputs/x.png")

        assert response.status_code == 404
        assert "未置备" in response.json()["detail"] or "开通" in response.json()["detail"]


class TestConsoleLink:
    def test_returns_console_url_with_ticket(self, user_a, monkeypatch):
        _settings(monkeypatch, "https://console.example/ui")

        def handler(request: httpx.Request, seen: dict) -> httpx.Response:
            if request.url.path == "/v1/auth/ticket":
                seen["auth"] = request.headers.get("Authorization")
                return httpx.Response(200, json={"ticket": "t-123", "expires": 0})
            raise AssertionError(f"unexpected upstream path {request.url.path}")

        seen = _install_runtime(_provisioner(user_a), handler)
        with TestClient(_app(user_a)) as client:
            response = client.post("/api/media-gateway/console-link")

        assert response.status_code == 200
        assert response.json() == {"url": "https://console.example/ui?ticket=t-123"}
        assert seen["auth"] == "Bearer tok-A"  # ticket minted with the session user's own token

    def test_unconfigured_console_url_is_400(self, user_a, monkeypatch):
        _settings(monkeypatch, None)

        def handler(request: httpx.Request, seen: dict) -> httpx.Response:
            raise AssertionError("upstream must not be reached when console URL is unset")

        _install_runtime(_provisioner(user_a), handler)
        with TestClient(_app(user_a)) as client:
            response = client.post("/api/media-gateway/console-link")

        assert response.status_code == 400
        assert "MEDIA_GATEWAY_CONSOLE_URL" in response.json()["detail"]

    def test_ticket_failure_degrades_to_plain_url(self, user_a, monkeypatch, caplog):
        _settings(monkeypatch, "https://console.example/ui")

        def handler(request: httpx.Request, seen: dict) -> httpx.Response:
            if request.url.path == "/v1/auth/ticket":
                return httpx.Response(503, json={"error": {"code": "unavailable", "message": "down", "retryable": True}})
            raise AssertionError(f"unexpected upstream path {request.url.path}")

        _install_runtime(_provisioner(user_a), handler)
        with caplog.at_level("WARNING"):
            with TestClient(_app(user_a)) as client:
                response = client.post("/api/media-gateway/console-link")

        assert response.status_code == 200
        assert response.json() == {"url": "https://console.example/ui"}
        assert any("ticket" in record.message.lower() for record in caplog.records)

    def test_unauthenticated_is_401(self, user_a, monkeypatch):
        _settings(monkeypatch, "https://console.example/ui")

        def handler(request: httpx.Request, seen: dict) -> httpx.Response:
            raise AssertionError("upstream must not be reached when unauthenticated")

        _install_runtime(_provisioner(user_a), handler)
        bare = FastAPI()
        bare.include_router(mgw_router.router)
        with TestClient(bare) as client:
            response = client.post("/api/media-gateway/console-link")

        assert response.status_code == 401
