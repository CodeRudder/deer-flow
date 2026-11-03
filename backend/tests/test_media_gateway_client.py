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


class TestMultiCandidateFailover:
    @pytest.mark.anyio
    async def test_first_candidate_connect_failure_falls_over_to_second(self):
        hits: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hits.append(str(request.url))
            if request.url.host == "down":
                raise httpx.ConnectError("connection refused", request=request)
            return httpx.Response(200, json={"ok": True})

        client = MediaGatewayClient("http://down:8000,http://up:8000", "t", transport=_transport(handler))
        try:
            body = await client.get("/v1/ping")
        finally:
            await client.aclose()
        assert body == {"ok": True}
        assert hits == ["http://down:8000/v1/ping", "http://up:8000/v1/ping"]
        assert client.base_url == "http://up:8000"

    @pytest.mark.anyio
    async def test_active_candidate_is_remembered_without_probing(self):
        hits: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hits.append(str(request.url))
            if request.url.host == "down":
                raise httpx.ConnectError("connection refused", request=request)
            return httpx.Response(200, json={})

        client = MediaGatewayClient("http://down:8000,http://up:8000", "t", transport=_transport(handler))
        try:
            await client.get("/v1/a")
            assert len(hits) == 2  # down attempt + successful failover
            await client.get("/v1/b")
            await client.get("/v1/c")
        finally:
            await client.aclose()
        # Subsequent requests go straight to the remembered candidate.
        assert hits == [
            "http://down:8000/v1/a",
            "http://up:8000/v1/a",
            "http://up:8000/v1/b",
            "http://up:8000/v1/c",
        ]

    @pytest.mark.anyio
    async def test_http_error_does_not_trigger_failover(self):
        hits: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hits.append(str(request.url))
            return httpx.Response(500, json={"error": {"code": "boom", "message": "nope"}})

        client = MediaGatewayClient("http://a:8000,http://b:8000", "t", transport=_transport(handler))
        try:
            with pytest.raises(MediaGatewayError) as exc_info:
                await client.get("/v1/ping")
        finally:
            await client.aclose()
        assert exc_info.value.status == 500
        assert hits == ["http://a:8000/v1/ping"]
        assert client.base_url == "http://a:8000"

    @pytest.mark.anyio
    async def test_all_candidates_failed_error_lists_urls(self):
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url.host))
            raise httpx.ConnectTimeout("timed out", request=request)

        client = MediaGatewayClient("http://a:8000,http://b:8000", "t", transport=_transport(handler))
        try:
            with pytest.raises(MediaGatewayError) as exc_info:
                await client.get("/v1/ping")
        finally:
            await client.aclose()
        message = str(exc_info.value)
        assert "http://a:8000" in message and "http://b:8000" in message
        assert calls == ["a", "b"]

    @pytest.mark.anyio
    async def test_read_timeout_does_not_fail_over(self):
        hits: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hits.append(str(request.url))
            raise httpx.ReadTimeout("read timed out", request=request)

        client = MediaGatewayClient("http://a:8000,http://b:8000", "t", transport=_transport(handler))
        try:
            with pytest.raises(MediaGatewayError):
                await client.get("/v1/ping")
        finally:
            await client.aclose()
        assert hits == ["http://a:8000/v1/ping"]

    @pytest.mark.anyio
    async def test_multipart_post_fails_over(self):
        hits: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hits.append(str(request.url))
            if request.url.host == "down":
                raise httpx.ConnectError("connection refused", request=request)
            return httpx.Response(201, json={"id": "med_1"})

        client = MediaGatewayClient("http://down:8000,http://up:8000", "t", transport=_transport(handler))
        try:
            body = await client.post_multipart("/v1/media", files=[("file", ("a.png", b"x", "image/png"))])
        finally:
            await client.aclose()
        assert body == {"id": "med_1"}
        assert hits == ["http://down:8000/v1/media", "http://up:8000/v1/media"]

    def test_single_address_behaviour_unchanged(self):
        client = MediaGatewayClient("http://mgw:8000/", "t")
        assert client.base_url == "http://mgw:8000"
        assert client.candidates == ["http://mgw:8000"]

    def test_empty_base_url_rejected(self):
        with pytest.raises(ValueError):
            MediaGatewayClient(", ,", "t")
