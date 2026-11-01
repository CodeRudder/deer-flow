"""Tests for the in-process Media Gateway mock (mock_server.py).

These cases drive the real ``MediaGatewayClient`` and (for the provisioning
scenarios) the real ``MediaGatewayProvisioner`` against ``MockMediaGateway``,
locking down the MGW contract behaviour the E2E-mock tier relies on.
"""

from __future__ import annotations

import base64
import time

import pytest

from deerflow.media_gateway.client import MediaGatewayClient, MediaGatewayError
from deerflow.media_gateway.crypto import MediaGatewayCipher
from deerflow.media_gateway.mock_server import TICKET_TTL_S, MockMediaGateway
from deerflow.media_gateway.provisioner import MediaGatewayProvisioner


@pytest.fixture
async def mg():
    """Fresh mock + admin client, closed on exit."""
    mock = MockMediaGateway()
    client = mock.client()
    try:
        yield mock, client
    finally:
        await client.aclose()


def _submit(client: MediaGatewayClient, prompt="a corgi running", *, key: str | None = None, output: str | None = None):
    body: dict = {"prompt": prompt}
    if output:
        body["output"] = output
    return client.post("/v1/images/generations", json=body, idempotency_key=key)


class TestAuthFlows:
    @pytest.mark.anyio
    async def test_health_is_unauthenticated(self, mg):
        mock, client = mg
        assert await client.health() == {"status": "ok", "auth_mode": "users"}
        assert ("GET", "/health") in mock.requests

    @pytest.mark.anyio
    async def test_login_issues_session_token(self, mg):
        mock, client = mg
        body = await client.post("/v1/auth/login", json={"username": mock.admin_username, "password": mock.admin_password})
        assert body["token"].startswith("h3s_")
        assert body["user"]["role"] == "admin"

    @pytest.mark.anyio
    async def test_login_rejects_wrong_password(self, mg):
        _, client = mg
        with pytest.raises(MediaGatewayError) as exc_info:
            await client.post("/v1/auth/login", json={"username": "admin", "password": "nope"})
        assert exc_info.value.status == 401
        assert exc_info.value.code == "invalid_credentials"

    @pytest.mark.anyio
    async def test_ticket_issue_and_redeem(self, mg):
        _, client = mg
        ticket = await client.post("/v1/auth/ticket")
        assert ticket["ticket"]
        assert 0 < ticket["expires"] - int(time.time()) <= TICKET_TTL_S

        redeemed = await client.post("/v1/auth/login-ticket", json={"ticket": ticket["ticket"]})
        assert redeemed["token"].startswith("h3s_")

    @pytest.mark.anyio
    async def test_ticket_is_single_use(self, mg):
        _, client = mg
        ticket = await client.post("/v1/auth/ticket")
        await client.post("/v1/auth/login-ticket", json={"ticket": ticket["ticket"]})
        with pytest.raises(MediaGatewayError) as exc_info:
            await client.post("/v1/auth/login-ticket", json={"ticket": ticket["ticket"]})
        assert exc_info.value.status == 401
        assert exc_info.value.code == "invalid_ticket"

    @pytest.mark.anyio
    async def test_ticket_expiry_is_injectable(self, mg):
        mock, client = mg
        ticket = await client.post("/v1/auth/ticket")
        mock.expire_ticket(ticket["ticket"])
        with pytest.raises(MediaGatewayError):
            await client.post("/v1/auth/login-ticket", json={"ticket": ticket["ticket"]})

    @pytest.mark.anyio
    async def test_missing_token_is_401_envelope(self, mg):
        mock = MockMediaGateway()
        anon = mock.client(token="")
        try:
            with pytest.raises(MediaGatewayError) as exc_info:
                await anon.get("/v1/users")
        finally:
            await anon.aclose()
        assert exc_info.value.status == 401
        assert exc_info.value.retryable is False
        assert exc_info.value.code == "unauthorized"

    @pytest.mark.anyio
    async def test_disabled_user_tokens_stop_validating(self, mg):
        mock, client = mg
        created = await client.post("/v1/users", json={"username": "bob", "password": "bob-password-1", "role": "user"})
        uid = created["id"]
        token = (await client.post(f"/v1/users/{uid}/tokens", json={"label": "t"}))["token"]

        bob_client = mock.client(token=token)
        try:
            assert (await bob_client.post("/v1/auth/ticket"))["ticket"]
            await client.patch(f"/v1/users/{uid}", json={"disabled": True})
            with pytest.raises(MediaGatewayError) as exc_info:
                await bob_client.post("/v1/auth/ticket")
            assert exc_info.value.status == 401
        finally:
            await bob_client.aclose()


class TestUsersAndTokens:
    @pytest.mark.anyio
    async def test_list_users_returns_contract_fields(self, mg):
        mock, client = mg
        await client.post("/v1/users", json={"username": "alice", "password": "alice-password-1", "role": "user"})
        listing = await client.get("/v1/users")
        alice = next(u for u in listing["users"] if u["name"] == "alice")
        assert set(alice) >= {"id", "name", "role", "disabled"}
        assert alice["role"] == "user" and alice["disabled"] is False

    @pytest.mark.anyio
    async def test_non_admin_listing_is_404(self, mg):
        mock, client = mg
        uid = (await client.post("/v1/users", json={"username": "alice", "password": "alice-password-1", "role": "user"}))["id"]
        alice_token = (await client.post(f"/v1/users/{uid}/tokens", json={"label": "t"}))["token"]
        alice_client = mock.client(token=alice_token)
        try:
            with pytest.raises(MediaGatewayError) as exc_info:
                await alice_client.get("/v1/users")
        finally:
            await alice_client.aclose()
        assert exc_info.value.status == 404  # admin existence hidden, never 403
        assert exc_info.value.code == "not_found"

    @pytest.mark.anyio
    async def test_create_duplicate_username_is_409(self, mg):
        _, client = mg
        await client.post("/v1/users", json={"username": "alice", "password": "alice-password-1", "role": "user"})
        with pytest.raises(MediaGatewayError) as exc_info:
            await client.post("/v1/users", json={"username": "alice", "password": "other-password-1", "role": "user"})
        assert exc_info.value.status == 409
        assert exc_info.value.code == "username_exists"

    @pytest.mark.anyio
    async def test_patch_role_and_disabled(self, mg):
        _, client = mg
        uid = (await client.post("/v1/users", json={"username": "alice", "password": "alice-password-1", "role": "user"}))["id"]
        await client.patch(f"/v1/users/{uid}", json={"role": "admin"})
        assert next(u for u in (await client.get("/v1/users"))["users"] if u["id"] == uid)["role"] == "admin"
        await client.patch(f"/v1/users/{uid}", json={"disabled": True})
        assert next(u for u in (await client.get("/v1/users"))["users"] if u["id"] == uid)["disabled"] is True

    @pytest.mark.anyio
    async def test_token_plaintext_once_and_sha8(self, mg):
        import hashlib

        _, client = mg
        uid = (await client.post("/v1/users", json={"username": "alice", "password": "alice-password-1", "role": "user"}))["id"]
        body = await client.post(f"/v1/users/{uid}/tokens", json={"label": "deerflow"})
        token, sha8 = body["token"], body["token_info"]["sha8"]
        assert token.startswith("h3t_")
        assert sha8 == hashlib.sha256(token.encode()).hexdigest()[:8]
        # The listing never echoes the plaintext.
        listing = await client.get("/v1/users")
        assert token not in str(listing)

    @pytest.mark.anyio
    async def test_token_revoke(self, mg):
        mock, client = mg
        uid = (await client.post("/v1/users", json={"username": "alice", "password": "alice-password-1", "role": "user"}))["id"]
        body = await client.post(f"/v1/users/{uid}/tokens", json={"label": "deerflow"})
        sha8 = body["token_info"]["sha8"]
        assert await client.delete(f"/v1/users/{uid}/tokens/{sha8}") == {}
        assert sha8 not in (await client.get("/v1/users"))["users"][0].get("tokens", [])
        with pytest.raises(MediaGatewayError) as exc_info:
            await client.delete(f"/v1/users/{uid}/tokens/{sha8}")
        assert exc_info.value.status == 404
        assert body["token"] not in mock.tokens  # plaintext revoked for good


class TestGenerationLifecycle:
    @pytest.mark.anyio
    async def test_submit_returns_202_queued_brief(self, mg):
        _, client = mg
        body = await _submit(client)
        assert body["id"].startswith("img_")
        assert body["status"] == "queued"

    @pytest.mark.anyio
    async def test_idempotency_key_replays_same_job(self, mg):
        _, client = mg
        first = await _submit(client, key="batch-001")
        second = await _submit(client, key="batch-001")
        assert first["id"] == second["id"]
        assert len({jid for (jid,) in [(b["id"],) for b in (first, second)]}) == 1

    @pytest.mark.anyio
    async def test_idempotency_key_with_different_body_is_409(self, mg):
        _, client = mg
        await _submit(client, key="batch-002")
        with pytest.raises(MediaGatewayError) as exc_info:
            await _submit(client, prompt="a different prompt entirely", key="batch-002")
        assert exc_info.value.status == 409
        assert exc_info.value.code == "conflict"

    @pytest.mark.anyio
    async def test_state_machine_queued_running_completed(self, mg):
        mock, client = mg
        job_id = (await _submit(client))["id"]
        statuses = [(await client.get(f"/v1/images/jobs/{job_id}"))["status"] for _ in range(3)]
        assert statuses == ["running", "completed", "completed"]

        job = await client.get(f"/v1/images/jobs/{job_id}")
        data = job["data"][0]
        assert base64.b64decode(data["b64_json"]) == mock.file_bytes
        assert data["url"].startswith(f"/v1/images/jobs/{job_id}/file")

    @pytest.mark.anyio
    async def test_slow_job_polls_per_stage(self):
        mock = MockMediaGateway(polls_per_stage=2)
        client = mock.client()
        try:
            job_id = (await client.post("/v1/images/generations", json={"prompt": "x"}))["id"]
            statuses = [(await client.get(f"/v1/images/jobs/{job_id}"))["status"] for _ in range(4)]
            assert statuses == ["queued", "running", "running", "completed"]
        finally:
            await client.aclose()

    @pytest.mark.anyio
    async def test_injected_job_failure(self, mg):
        mock, client = mg
        job_id = (await _submit(client))["id"]
        mock.fail_job(job_id, code="engine_error", message="mock generation failure")
        await client.get(f"/v1/images/jobs/{job_id}")  # queued -> running
        failed = await client.get(f"/v1/images/jobs/{job_id}")
        assert failed["status"] == "failed"
        assert failed["error"] == {"code": "engine_error", "message": "mock generation failure"}

    @pytest.mark.anyio
    async def test_missing_prompt_is_400(self, mg):
        _, client = mg
        with pytest.raises(MediaGatewayError) as exc_info:
            await client.post("/v1/images/generations", json={"prompt": "  "})
        assert exc_info.value.status == 400
        assert exc_info.value.code == "invalid_request"

    @pytest.mark.anyio
    async def test_file_endpoint_returns_configured_bytes(self, mg):
        mock, client = mg
        mock.set_file(b"fake-mp4-bytes", content_type="video/mp4")
        job_id = (await _submit(client))["id"]
        await client.get(f"/v1/images/jobs/{job_id}")
        await client.get(f"/v1/images/jobs/{job_id}")  # to completed

        raw = await client._client.get(f"/v1/images/jobs/{job_id}/file", params={"kind": "video"}, headers={"Authorization": f"Bearer {mock.admin_token}"})
        assert raw.status_code == 200
        assert raw.content == b"fake-mp4-bytes"
        assert raw.headers["Content-Type"] == "video/mp4"
        assert "immutable" in raw.headers["Cache-Control"]

    @pytest.mark.anyio
    async def test_file_unknown_kind_is_400(self, mg):
        mock, client = mg
        job_id = (await _submit(client))["id"]
        await client.get(f"/v1/images/jobs/{job_id}")
        await client.get(f"/v1/images/jobs/{job_id}")
        raw = await client._client.get(f"/v1/images/jobs/{job_id}/file", params={"kind": "nope"}, headers={"Authorization": f"Bearer {mock.admin_token}"})
        assert raw.status_code == 400

    @pytest.mark.anyio
    async def test_owner_isolation_foreign_job_is_404(self, mg):
        mock, client = mg
        job_id = (await _submit(client))["id"]
        uid = (await client.post("/v1/users", json={"username": "mallory", "password": "mallory-password", "role": "user"}))["id"]
        mallory_token = (await client.post(f"/v1/users/{uid}/tokens", json={"label": "t"}))["token"]
        mallory = mock.client(token=mallory_token)
        try:
            with pytest.raises(MediaGatewayError) as exc_info:
                await mallory.get(f"/v1/images/jobs/{job_id}")
        finally:
            await mallory.aclose()
        assert exc_info.value.status == 404  # not 403 — existence is hidden
        assert mock._is_admin("")

    @pytest.mark.anyio
    async def test_video_output_data_has_null_b64(self, mg):
        _, client = mg
        job = await _submit(client, output="video")
        job_id = job["id"]
        await client.get(f"/v1/images/jobs/{job_id}")
        body = await client.get(f"/v1/images/jobs/{job_id}")
        assert body["data"][0]["b64_json"] is None
        assert "kind=video" in body["data"][0]["url"]


class TestScriptableFailures:
    @pytest.mark.anyio
    async def test_fail_next_returns_envelope_and_retry_after(self, mg):
        mock, client = mg
        mock.fail_next(status=503, code="unavailable", retry_after=30)
        with pytest.raises(MediaGatewayError) as exc_info:
            await client.get("/v1/users")
        assert exc_info.value.status == 503
        assert exc_info.value.code == "unavailable"
        assert exc_info.value.retryable is True
        assert exc_info.value.retry_after == 30.0
        # one-shot: the next request succeeds again
        uid = (await client.post("/v1/users", json={"username": "alice", "password": "alice-password-1", "role": "user"}))["id"]
        assert (await client.get("/v1/users"))["users"][0]["id"] == uid

    @pytest.mark.anyio
    async def test_rate_limit_next(self, mg):
        mock, client = mg
        mock.rate_limit_next(retry_after=7)
        with pytest.raises(MediaGatewayError) as exc_info:
            await client.get("/v1/users")
        assert exc_info.value.status == 429
        assert exc_info.value.code == "too_many_requests"
        assert exc_info.value.retry_after == 7.0

    @pytest.mark.anyio
    async def test_timeout_next_maps_to_transport_error(self, mg):
        mock, client = mg
        mock.timeout_next()
        with pytest.raises(MediaGatewayError) as exc_info:
            await client.get("/v1/users")
        assert exc_info.value.status == 0
        assert exc_info.value.code == "transport_error"
        assert exc_info.value.retryable is True

    @pytest.mark.anyio
    async def test_request_log_records_order(self, mg):
        mock, client = mg
        await client.get("/health")
        await client.get("/v1/users")
        assert ("GET", "/health") in mock.requests
        assert mock.requests.index(("GET", "/health")) < mock.requests.index(("GET", "/v1/users"))


class TestProvisionerAgainstMock:
    """The real provisioner end-to-end on the mock — no FakeClient involved."""

    @pytest.mark.anyio
    async def test_ensure_user_provisions_via_mock(self, mg, tmp_path):
        from types import SimpleNamespace

        mock, client = mg
        from deerflow.persistence.engine import close_engine, get_session_factory, init_engine

        await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'p.db'}", sqlite_dir=str(tmp_path))
        try:
            provisioner = MediaGatewayProvisioner(client, get_session_factory(), MediaGatewayCipher.from_key("k"))
            user = SimpleNamespace(id="u-1", username="alice", email="alice@example.com", system_role="user")
            account = await provisioner.ensure_user(user)

            assert account.mgw_user_id.startswith("mgw-")
            assert account.token.startswith("h3t_")
            assert account.token_sha8
            # The user really exists on the mock, mapped by name.
            names = [u["name"] for u in (await client.get("/v1/users"))["users"]]
            assert account.mgw_username in names

            # Second call is idempotent — no second MGW account.
            again = await provisioner.ensure_user(user)
            assert again == account
            assert sum(1 for m, p in mock.requests if m == "POST" and p == "/v1/users") == 1
        finally:
            await close_engine()

    @pytest.mark.anyio
    async def test_provisioner_409_claims_existing_user(self, mg, tmp_path):
        from types import SimpleNamespace

        mock, client = mg
        from deerflow.persistence.engine import close_engine, get_session_factory, init_engine

        # Pre-create the username on the mock so POST /v1/users hits the 409 path.
        uid = (await client.post("/v1/users", json={"username": "alice", "password": "pre-existing-1", "role": "user"}))["id"]
        assert uid.startswith("mgw-")

        await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'p2.db'}", sqlite_dir=str(tmp_path))
        try:
            provisioner = MediaGatewayProvisioner(client, get_session_factory(), MediaGatewayCipher.from_key("k"))
            account = await provisioner.ensure_user(SimpleNamespace(id="u-2", username="alice", email="a@example.com", system_role="user"))
            assert account.mgw_user_id == uid  # claimed, not recreated
            assert ("GET", "/v1/users") in mock.requests
        finally:
            await close_engine()

    @pytest.mark.anyio
    async def test_disable_and_reset_token_against_mock(self, mg, tmp_path):
        from types import SimpleNamespace

        mock, client = mg
        from deerflow.persistence.engine import close_engine, get_session_factory, init_engine

        await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'p3.db'}", sqlite_dir=str(tmp_path))
        try:
            provisioner = MediaGatewayProvisioner(client, get_session_factory(), MediaGatewayCipher.from_key("k"))
            account = await provisioner.ensure_user(SimpleNamespace(id="u-3", username="carol", email="c@example.com", system_role="user"))
            old_token = account.token

            rotated = await provisioner.reset_token("u-3")
            assert rotated.token != old_token
            assert old_token not in mock.tokens  # revoked on the mock itself

            assert await provisioner.disable_user("u-3") is True
            assert next(u for u in (await client.get("/v1/users"))["users"] if u["id"] == account.mgw_user_id)["disabled"] is True

            # A disabled user's token is rejected by the mock — owner isolation holds end-to-end.
            disabled_client = mock.client(token=rotated.token)
            try:
                with pytest.raises(MediaGatewayError):
                    await disabled_client.post("/v1/auth/ticket")
            finally:
                await disabled_client.aclose()
        finally:
            await close_engine()
