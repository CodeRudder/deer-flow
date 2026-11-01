"""Unit tests for the Media Gateway HTTP client."""

from __future__ import annotations

import json

import httpx
import pytest

from deerflow.media_gateway.client import DEFAULT_TIMEOUT, MediaGatewayClient, MediaGatewayError


def _transport(handler) -> httpx.AsyncBaseTransport:
    return httpx.MockTransport(handler)


class TestBearerInjection:
    @pytest.mark.anyio
    async def test_default_token_is_admin_token(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("Authorization")
            return httpx.Response(200, json={"ok": True})

        client = MediaGatewayClient("http://mgw", "admin-token", transport=_transport(handler))
        try:
            await client.get("/v1/users")
        finally:
            await client.aclose()
        assert seen["auth"] == "Bearer admin-token"

    @pytest.mark.anyio
    async def test_per_request_token_overrides_admin(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("Authorization")
            return httpx.Response(200, json={})

        client = MediaGatewayClient("http://mgw", "admin-token", transport=_transport(handler))
        try:
            await client.post("/v1/things", token="h3t_user")
        finally:
            await client.aclose()
        assert seen["auth"] == "Bearer h3t_user"

    @pytest.mark.anyio
    async def test_empty_string_token_sends_no_auth_header(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("Authorization")
            return httpx.Response(200, json={})

        client = MediaGatewayClient("http://mgw", "admin-token", transport=_transport(handler))
        try:
            await client.health()
        finally:
            await client.aclose()
        assert seen["auth"] is None

    @pytest.mark.anyio
    async def test_no_token_configured_sends_no_auth_header(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("Authorization")
            return httpx.Response(200, json={})

        client = MediaGatewayClient("http://mgw", transport=_transport(handler))
        try:
            await client.get("/v1/users")
        finally:
            await client.aclose()
        assert seen["auth"] is None


class TestErrorEnvelope:
    @pytest.mark.anyio
    async def test_error_envelope_is_mapped(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404,
                json={"error": {"code": "not_found", "message": "no such job", "retryable": False}},
            )

        client = MediaGatewayClient("http://mgw", "t", transport=_transport(handler))
        try:
            with pytest.raises(MediaGatewayError) as exc_info:
                await client.get("/v1/jobs/xyz")
        finally:
            await client.aclose()
        err = exc_info.value
        assert err.status == 404
        assert err.code == "not_found"
        assert err.message == "no such job"
        assert err.retryable is False
        assert err.retry_after is None

    @pytest.mark.anyio
    async def test_429_maps_retry_after_and_retryable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                headers={"Retry-After": "12"},
                json={"error": {"code": "rate_limited", "message": "slow down", "retryable": True}},
            )

        client = MediaGatewayClient("http://mgw", "t", transport=_transport(handler))
        try:
            with pytest.raises(MediaGatewayError) as exc_info:
                await client.post("/v1/images")
        finally:
            await client.aclose()
        err = exc_info.value
        assert err.status == 429
        assert err.code == "rate_limited"
        assert err.retryable is True
        assert err.retry_after == 12.0

    @pytest.mark.anyio
    async def test_503_without_envelope_is_retryable_with_retry_after(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, headers={"Retry-After": "30"}, text="gateway overloaded")

        client = MediaGatewayClient("http://mgw", "t", transport=_transport(handler))
        try:
            with pytest.raises(MediaGatewayError) as exc_info:
                await client.get("/v1/anything")
        finally:
            await client.aclose()
        err = exc_info.value
        assert err.status == 503
        assert err.code == "http_error"  # fallback when no envelope present
        assert err.retryable is True
        assert err.retry_after == 30.0

    @pytest.mark.anyio
    async def test_non_json_retry_after_header_is_ignored(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, headers={"Retry-After": "Wed, 01 Nov 2026 00:00:00 GMT"}, text="")

        client = MediaGatewayClient("http://mgw", "t", transport=_transport(handler))
        try:
            with pytest.raises(MediaGatewayError) as exc_info:
                await client.get("/v1/anything")
        finally:
            await client.aclose()
        assert exc_info.value.retry_after is None

    @pytest.mark.anyio
    async def test_transport_failure_maps_to_retryable_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        client = MediaGatewayClient("http://mgw", "t", transport=_transport(handler))
        try:
            with pytest.raises(MediaGatewayError) as exc_info:
                await client.get("/health")
        finally:
            await client.aclose()
        err = exc_info.value
        assert err.status == 0
        assert err.code == "transport_error"
        assert err.retryable is True


class TestIdempotencyKey:
    @pytest.mark.anyio
    async def test_idempotency_key_header_is_sent(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["key"] = request.headers.get("Idempotency-Key")
            return httpx.Response(200, json={})

        client = MediaGatewayClient("http://mgw", "t", transport=_transport(handler))
        try:
            await client.post("/v1/images", json={"prompt": "cat"}, idempotency_key="abc-123")
        finally:
            await client.aclose()
        assert seen["key"] == "abc-123"

    @pytest.mark.anyio
    async def test_no_idempotency_key_header_by_default(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["key"] = request.headers.get("Idempotency-Key")
            return httpx.Response(200, json={})

        client = MediaGatewayClient("http://mgw", "t", transport=_transport(handler))
        try:
            await client.post("/v1/images", json={"prompt": "cat"})
        finally:
            await client.aclose()
        assert seen["key"] is None


class TestMisc:
    def test_timeout_configuration(self):
        assert DEFAULT_TIMEOUT.connect == 5.0
        assert DEFAULT_TIMEOUT.read == 30.0

    @pytest.mark.anyio
    async def test_health_returns_body(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/health"
            return httpx.Response(200, json={"status": "ok", "auth_mode": "users"})

        client = MediaGatewayClient("http://mgw", "t", transport=_transport(handler))
        try:
            body = await client.health()
        finally:
            await client.aclose()
        assert body["auth_mode"] == "users"

    @pytest.mark.anyio
    async def test_empty_success_body_returns_empty_dict(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(204)

        client = MediaGatewayClient("http://mgw", "t", transport=_transport(handler))
        try:
            assert await client.delete("/v1/users/u1/tokens/abcd1234") == {}
        finally:
            await client.aclose()

    @pytest.mark.anyio
    async def test_json_body_is_forwarded(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return httpx.Response(201, json={"id": "u1"})

        client = MediaGatewayClient("http://mgw", "t", transport=_transport(handler))
        try:
            body = await client.post("/v1/users", json={"username": "alice", "password": "pw", "role": "user"})
        finally:
            await client.aclose()
        assert seen["body"] == {"username": "alice", "password": "pw", "role": "user"}
        assert body == {"id": "u1"}
