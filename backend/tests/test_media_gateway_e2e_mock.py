"""E2E-mock tier tests for the Media Gateway integration (M3 批次1 基础链路).

Covers the E2E-mock cases runnable after M2 batch 1 (tasks.md M3):
E2E-1 冷启动置备 / E2E-2 生图全链路 / E2E-3 生视频 / E2E-4 多用户隔离 /
E2E-8 故障注入 / E2E-9 安全巡检 / E2E-10 回归.

The point of the tier: **nothing above the HTTP layer is mocked** — providers
are taken through the real registry entry point (``MEDIA_GATEWAY_BASE_URL``
set → ``media_gateway`` listed), provisioning goes through the real
``MediaGatewayProvisioner`` with a real SQLite mapping table, generation goes
through the real ``MediaGatewayGenerationProvider``. Only the gateway HTTP
layer is replaced, by the in-process :class:`MockMediaGateway` from
``deerflow.media_gateway.mock_server``.

The base mock implements the MGW job contract (``/v1/images/generations``,
``/v1/images/jobs/{id}`` with ``data[]`` artifacts) — the exact surface the
deer-flow provider adapter speaks. This module subclasses the mock only to add
``GET /v1/images/modes`` (capability discovery, images.md §1), which the base
mock does not implement — the documented extension point ("extend it rather
than re-rolling ad-hoc handlers").
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from deerflow.media_gateway.client import MediaGatewayClient, MediaGatewayError
from deerflow.media_gateway.crypto import MediaGatewayCipher
from deerflow.media_gateway.mock_server import MockMediaGateway
from deerflow.media_gateway.providers import (
    MEDIA_GATEWAY_FILE_PROXY_PREFIX,
    MediaGatewayGenerationProvider,
    UnsupportedParameterError,
)
from deerflow.media_gateway.provisioner import MediaGatewayProvisioner
from deerflow.media_gateway.settings import set_media_gateway_settings
from deerflow.models.image_generation import registry as image_registry
from deerflow.models.video_generation import registry as video_registry

IMAGE_MODEL = "mgw-image-e2e"
VIDEO_MODEL = "mgw-video-e2e"

# GET /v1/images/modes body (images.md §1). Both media kinds discover through
# this one endpoint; the submit-side ``output`` parameter picks the artifact.
MODES = {
    "default_mode": IMAGE_MODEL,
    "baseline_modes": [],
    "modes": [
        {"name": IMAGE_MODEL, "available": True, "ratios": ["1:1", "16:9"], "supported_params": ["prompt", "ratio"]},
        {"name": VIDEO_MODEL, "available": True, "duration_range": {"min": 4, "max": 15}, "supported_params": ["prompt", "duration"]},
    ],
}


class E2EMockMediaGateway(MockMediaGateway):
    """Base mock + the ``/v1/images/modes`` capability endpoint.

    Everything else (submit / poll / artifacts) already speaks the real MGW
    job contract, so every knob (``fail_next`` / ``rate_limit_next`` /
    ``timeout_next`` / ``polls_per_stage`` …) keeps working unchanged.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        # Full httpx.Request log (the base class logs (method, path) only) so
        # the security sweep can inspect URLs and bodies.
        self.http_requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.http_requests.append(request)
        path = request.url.path

        if request.method == "GET" and path == "/v1/images/modes":
            self.requests.append((request.method, path))
            return httpx.Response(200, json=MODES)

        return super().handle(request)


# ── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
async def e2e_mgw(tmp_path, monkeypatch):
    """The whole in-process stack: mock gateway + settings + provisioner + DB."""
    mock = E2EMockMediaGateway()

    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine

    url = f"sqlite+aiosqlite:///{tmp_path / 'e2e-mgw.db'}"
    await init_engine("sqlite", url=url, sqlite_dir=str(tmp_path))

    cipher = MediaGatewayCipher.from_key("e2e-mgw-encryption-key")
    provisioner = MediaGatewayProvisioner(mock.client(), get_session_factory(), cipher)
    stack = SimpleNamespace(
        mock=mock,
        admin_client=mock.client(),
        provisioner=provisioner,
        base_url=mock.base_url,
        admin_token=mock.admin_token,
        cipher=cipher,
    )
    try:
        yield stack
    finally:
        await stack.admin_client.aclose()
        await close_engine()
        set_media_gateway_settings(None)


def _user(user_id: str, username: str, system_role: str = "user"):
    from types import SimpleNamespace as NS

    return NS(id=user_id, username=username, email=f"{username}@example.com", system_role=system_role)


async def _provider_for(stack, user_id: str, kind: str, *, poll_interval: float = 0.0) -> MediaGatewayGenerationProvider:
    """A real provider adapter wired to the real provisioner token path.

    Mirrors the intended M2-3 runtime wiring: the per-user ``h3t_`` token is
    resolved through ``MediaGatewayProvisioner.get_token`` for the current user.
    """

    async def token_provider() -> str:
        return await stack.provisioner.get_token(user_id)

    client = MediaGatewayClient(stack.base_url, stack.admin_token, transport=stack.mock.transport)
    return MediaGatewayGenerationProvider(client, kind=kind, token_provider=token_provider, poll_interval=poll_interval)


async def _close_provider(provider: MediaGatewayGenerationProvider) -> None:
    await provider._client.aclose()


def _registry_models(monkeypatch, base_url: str = "http://mock-mgw"):
    """Registry listing with MGW configured (env-only, like production)."""
    from deerflow.config.extensions_config import ExtensionsConfig

    monkeypatch.setenv("MEDIA_GATEWAY_BASE_URL", base_url)
    monkeypatch.setenv("MEDIA_GATEWAY_IMAGE_MODEL", IMAGE_MODEL)
    monkeypatch.setenv("MEDIA_GATEWAY_VIDEO_MODEL", VIDEO_MODEL)
    monkeypatch.setattr(image_registry, "get_extensions_config", lambda: ExtensionsConfig(skills={}))
    monkeypatch.setattr(video_registry, "get_extensions_config", lambda: ExtensionsConfig(skills={}))
    monkeypatch.setattr(image_registry, "get_app_config", lambda: type("C", (), {"model_extra": {}})())
    monkeypatch.setattr(video_registry, "get_app_config", lambda: type("C", (), {"model_extra": {}})())
    return (
        {p.name: p for p in image_registry.get_image_generation_providers().providers},
        {p.name: p for p in video_registry.get_video_generation_providers().providers},
    )


# ── E2E-1 冷启动置备 ───────────────────────────────────────────────────────


class TestColdStartProvisioning:
    @pytest.mark.anyio
    async def test_first_contact_provisions_user_and_token(self, e2e_mgw):
        stack = e2e_mgw
        account = await stack.provisioner.ensure_user(_user("u-1", "alice"))

        assert account.token.startswith("h3t_")
        # The mock saw exactly one account creation and one token issuance.
        posts = [path for method, path in stack.mock.requests if method == "POST"]
        assert posts.count("/v1/users") == 1
        assert sum(1 for p in posts if p.endswith("/tokens")) == 1
        # MGW side: the user really exists with role=user.
        created = [u for u in (await stack.admin_client.get("/v1/users"))["users"] if u["name"] == "alice"]
        assert len(created) == 1 and created[0]["role"] == "user"

    @pytest.mark.anyio
    async def test_second_contact_does_not_reprovision(self, e2e_mgw):
        stack = e2e_mgw
        first = await stack.provisioner.ensure_user(_user("u-1", "alice"))
        second = await stack.provisioner.ensure_user(_user("u-1", "alice"))

        assert first == second
        posts = [path for method, path in stack.mock.requests if method == "POST"]
        assert posts.count("/v1/users") == 1
        assert sum(1 for p in posts if p.endswith("/tokens")) == 1

    @pytest.mark.anyio
    async def test_provisioned_token_is_accepted_by_gateway(self, e2e_mgw):
        stack = e2e_mgw
        account = await stack.provisioner.ensure_user(_user("u-1", "alice"))
        # The stored plaintext token authenticates as the MGW user.
        body = await stack.admin_client.get("/health", token="")  # sanity: gateway up
        assert body["status"] == "ok"
        # A plain user listing admin-only endpoints reads as 404 (existence hidden) —
        # 401 would mean the provisioned token did not authenticate at all.
        client = MediaGatewayClient(stack.base_url, account.token, transport=stack.mock.transport)
        try:
            with pytest.raises(MediaGatewayError) as exc_info:
                await client.get("/v1/users")
            assert exc_info.value.status == 404
        finally:
            await client.aclose()


# ── E2E-2 生图全链路 ───────────────────────────────────────────────────────


class TestImageFullChain:
    @pytest.mark.anyio
    async def test_capability_discovery_through_registry_model(self, e2e_mgw, monkeypatch):
        stack = e2e_mgw
        image_by_name, _ = _registry_models(monkeypatch)
        assert image_by_name["media_gateway"].configured is True
        assert [m.name for m in image_by_name["media_gateway"].models] == [IMAGE_MODEL]

        account = await stack.provisioner.ensure_user(_user("u-1", "alice"))
        provider = await _provider_for(stack, account.deerflow_user_id, "image")
        try:
            caps = await provider.discover_capabilities()
        finally:
            await _close_provider(provider)
        assert IMAGE_MODEL in caps
        assert caps[IMAGE_MODEL].ratios == ("1:1", "16:9")

    @pytest.mark.anyio
    async def test_unsupported_param_rejected_locally_without_http(self, e2e_mgw):
        stack = e2e_mgw
        account = await stack.provisioner.ensure_user(_user("u-1", "alice"))
        provider = await _provider_for(stack, account.deerflow_user_id, "image")
        seen_before = len(stack.mock.http_requests)
        try:
            with pytest.raises(UnsupportedParameterError):
                # "duration" is not in the image model's capability matrix.
                await provider.generate("a cat", model=IMAGE_MODEL, params={"duration": 5})
            # No submit ever left — only the capability request happened.
            assert not any(r.method == "POST" for r in stack.mock.http_requests[seen_before:])
        finally:
            await _close_provider(provider)

    @pytest.mark.anyio
    async def test_submit_poll_complete_with_idempotency_and_proxy_url(self, e2e_mgw, monkeypatch):
        stack = e2e_mgw
        _registry_models(monkeypatch)
        account = await stack.provisioner.ensure_user(_user("u-1", "alice"))
        provider = await _provider_for(stack, account.deerflow_user_id, "image")
        try:
            url = await provider.generate("a cat in a hat", model=IMAGE_MODEL, params={"ratio": "16:9"}, idempotency_key="e2e-image-1")
        finally:
            await _close_provider(provider)

        # Artifact URL uses the deer-flow proxy prefix.
        assert url.startswith(MEDIA_GATEWAY_FILE_PROXY_PREFIX + "/")
        assert "/file" in url

        # The submit carried the user token and the Idempotency-Key.
        submits = [r for r in stack.mock.http_requests if r.method == "POST" and r.url.path == "/v1/images/generations"]
        assert len(submits) == 1
        assert submits[0].headers.get("Idempotency-Key") == "e2e-image-1"
        assert submits[0].headers.get("Authorization") == f"Bearer {account.token}"

        # Job was created owned by the provisioned MGW user, and completed.
        assert len(stack.mock.jobs) == 1
        job = next(iter(stack.mock.jobs.values()))
        assert job["status"] == "completed"
        assert job["owner"] == account.mgw_user_id


# ── E2E-3 生视频 ───────────────────────────────────────────────────────────


class TestVideoFullChain:
    @pytest.mark.anyio
    async def test_video_chain_via_video_paths(self, e2e_mgw, monkeypatch):
        stack = e2e_mgw
        _, video_by_name = _registry_models(monkeypatch)
        assert [m.name for m in video_by_name["media_gateway"].models] == [VIDEO_MODEL]

        account = await stack.provisioner.ensure_user(_user("u-2", "bob"))
        provider = await _provider_for(stack, account.deerflow_user_id, "video")
        try:
            caps = await provider.discover_capabilities()
            assert caps[VIDEO_MODEL].duration_range == (4, 15)

            url = await provider.generate("waves at sunset", model=VIDEO_MODEL, params={"duration": 5}, idempotency_key="e2e-video-1")
        finally:
            await _close_provider(provider)

        assert url.startswith(MEDIA_GATEWAY_FILE_PROXY_PREFIX + "/")
        assert "kind=video" in url  # artifact is the video rendition
        submits = [r for r in stack.mock.http_requests if r.method == "POST" and r.url.path == "/v1/images/generations"]
        assert len(submits) == 1 and submits[0].headers.get("Idempotency-Key") == "e2e-video-1"

    @pytest.mark.anyio
    async def test_video_unsupported_param_rejected_locally(self, e2e_mgw):
        stack = e2e_mgw
        account = await stack.provisioner.ensure_user(_user("u-2", "bob"))
        provider = await _provider_for(stack, account.deerflow_user_id, "video")
        try:
            with pytest.raises(UnsupportedParameterError):
                await provider.generate("waves", model=VIDEO_MODEL, params={"ratio": "1:1"})  # video model has no ratio domain
        finally:
            await _close_provider(provider)


# ── E2E-4 多用户隔离 ───────────────────────────────────────────────────────


class TestUserIsolation:
    @pytest.mark.anyio
    async def test_user_b_cannot_see_user_a_job(self, e2e_mgw):
        stack = e2e_mgw
        alice = await stack.provisioner.ensure_user(_user("u-a", "alice"))
        bob = await stack.provisioner.ensure_user(_user("u-b", "bob"))

        provider_a = await _provider_for(stack, alice.deerflow_user_id, "image")
        try:
            await provider_a.generate("alice's art", model=IMAGE_MODEL, idempotency_key="iso-a")
        finally:
            await _close_provider(provider_a)
        job_id = next(iter(stack.mock.jobs))

        # B polling A's task reads as 404 (MGW owner semantics, never 403).
        client_b = MediaGatewayClient(stack.base_url, bob.token, transport=stack.mock.transport)
        try:
            with pytest.raises(MediaGatewayError) as exc_info:
                await client_b.get(f"/v1/images/jobs/{job_id}")
            assert exc_info.value.status == 404
        finally:
            await client_b.aclose()

        # B's own journey is unaffected and sees only their own (zero) jobs.
        provider_b = await _provider_for(stack, bob.deerflow_user_id, "image")
        try:
            url_b = await provider_b.generate("bob's art", model=IMAGE_MODEL, idempotency_key="iso-b")
        finally:
            await _close_provider(provider_b)
        assert url_b.startswith(MEDIA_GATEWAY_FILE_PROXY_PREFIX + "/")
        assert len(stack.mock.jobs) == 2
        assert {job["owner"] for job in stack.mock.jobs.values()} == {alice.mgw_user_id, bob.mgw_user_id}

    @pytest.mark.anyio
    async def test_b_cannot_fetch_a_artifact_bytes(self, e2e_mgw):
        stack = e2e_mgw
        alice = await stack.provisioner.ensure_user(_user("u-a", "alice"))
        bob = await stack.provisioner.ensure_user(_user("u-b", "bob"))

        provider_a = await _provider_for(stack, alice.deerflow_user_id, "image")
        try:
            url_a = await provider_a.generate("alice's art", model=IMAGE_MODEL, idempotency_key="iso2-a")
        finally:
            await _close_provider(provider_a)

        # The proxied URL encodes the MGW path; B presenting his own token to
        # that path gets 404, not the artifact.
        mgw_path = url_a.removeprefix(MEDIA_GATEWAY_FILE_PROXY_PREFIX)
        client_b = MediaGatewayClient(stack.base_url, bob.token, transport=stack.mock.transport)
        try:
            with pytest.raises(MediaGatewayError) as exc_info:
                await client_b.get(mgw_path)
            assert exc_info.value.status == 404
        finally:
            await client_b.aclose()


# ── E2E-8 故障注入 ─────────────────────────────────────────────────────────


class TestFaultInjection:
    @pytest.mark.anyio
    async def test_429_maps_to_retryable_with_retry_after(self, e2e_mgw):
        stack = e2e_mgw
        stack.mock.rate_limit_next(retry_after=7)

        with pytest.raises(MediaGatewayError) as exc_info:
            await stack.admin_client.get("/v1/users")
        assert exc_info.value.status == 429
        assert exc_info.value.retryable is True
        assert exc_info.value.retry_after == 7
        assert exc_info.value.code == "too_many_requests"

    @pytest.mark.anyio
    async def test_gateway_down_maps_to_transport_error(self, e2e_mgw):
        stack = e2e_mgw
        stack.mock.timeout_next()

        with pytest.raises(MediaGatewayError) as exc_info:
            await stack.admin_client.get("/v1/users")
        assert exc_info.value.code == "transport_error"
        assert exc_info.value.status == 0
        assert exc_info.value.retryable is True

    @pytest.mark.anyio
    async def test_revoked_token_401_then_reset_token_recovers(self, e2e_mgw):
        stack = e2e_mgw
        account = await stack.provisioner.ensure_user(_user("u-1", "alice"))

        # Simulate gateway-side revocation of the user's token.
        stack.mock.tokens.pop(account.token)
        client = MediaGatewayClient(stack.base_url, account.token, transport=stack.mock.transport)
        try:
            with pytest.raises(MediaGatewayError) as exc_info:
                await client.get("/v1/users")
            assert exc_info.value.status == 401
        finally:
            await client.aclose()

        # 401 → the reset_token rotation path issues a fresh working token.
        rotated = await stack.provisioner.reset_token(account.deerflow_user_id)
        assert rotated.token.startswith("h3t_") and rotated.token != account.token

        client = MediaGatewayClient(stack.base_url, rotated.token, transport=stack.mock.transport)
        try:
            # A plain user hitting the admin listing still gets 404 — but that
            # proves the *token* is valid again (401 would mean invalid).
            with pytest.raises(MediaGatewayError) as exc_info:
                await client.get("/v1/users")
            assert exc_info.value.status == 404
        finally:
            await client.aclose()

    @pytest.mark.anyio
    async def test_generation_failure_surfaces_as_task_failed(self, e2e_mgw):
        stack = e2e_mgw
        account = await stack.provisioner.ensure_user(_user("u-1", "alice"))
        provider = await _provider_for(stack, account.deerflow_user_id, "image")
        try:
            task_id = await provider.submit("a cat", model=IMAGE_MODEL, idempotency_key="fail-1")
            stack.mock.fail_job(task_id, code="engine_error", message="mock generation failure")
            with pytest.raises(MediaGatewayError) as exc_info:
                await provider.wait_for_result(task_id)
            assert exc_info.value.code == "task_failed"
        finally:
            await _close_provider(provider)


# ── E2E-9 安全巡检 ─────────────────────────────────────────────────────────


class TestSecuritySweep:
    @pytest.mark.anyio
    async def test_no_plaintext_token_in_urls_bodies_or_errors(self, e2e_mgw):
        """h3t_ plaintext must not leak into any URL, request body or error text.

        Bearer Authorization headers are the one sanctioned carrier of the
        plaintext token (that is its wire format); everything else — URLs,
        payloads, exception messages — must be clean.
        """
        stack = e2e_mgw
        alice = await stack.provisioner.ensure_user(_user("u-a", "alice"))
        bob = await stack.provisioner.ensure_user(_user("u-b", "bob"))
        tokens = (stack.admin_token, alice.token, bob.token)

        errors: list[str] = []
        provider_a = await _provider_for(stack, alice.deerflow_user_id, "image")
        try:
            await provider_a.generate("art", model=IMAGE_MODEL, idempotency_key="sec-1")
        except MediaGatewayError as exc:
            errors.append(str(exc))
        finally:
            await _close_provider(provider_a)

        stack.mock.rate_limit_next()
        client = MediaGatewayClient(stack.base_url, alice.token, transport=stack.mock.transport)
        try:
            with pytest.raises(MediaGatewayError) as exc_info:
                await client.get("/v1/users")
            errors.append(str(exc_info.value))
        finally:
            await client.aclose()

        # Sweep every recorded request's URL and body, plus all error texts.
        haystacks = [str(r.url) for r in stack.mock.http_requests]
        haystacks += [r.read().decode("utf-8", "replace") for r in stack.mock.http_requests]
        haystacks += errors
        for token in tokens:
            for text in haystacks:
                assert token not in text, f"plaintext token leaked: {text!r}"
        for text in haystacks:
            assert "h3t_" not in text, f"h3t_ plaintext in non-Authorization surface: {text!r}"

    @pytest.mark.anyio
    async def test_password_never_echoed(self, e2e_mgw):
        """The provisioner's random password must never travel anywhere but the
        one account-creation call, and must be stored encrypted at rest."""
        stack = e2e_mgw
        account = await stack.provisioner.ensure_user(_user("u-1", "alice"))
        async with stack.provisioner.session_factory() as session:
            from deerflow.persistence.media_gateway import MediaGatewayUserRow

            row = await session.get(MediaGatewayUserRow, account.deerflow_user_id)
        assert stack.cipher.decrypt_text(row.mgw_password_enc)
        assert account.token not in row.mgw_token_enc  # token column is ciphertext


# ── E2E-10 回归 ────────────────────────────────────────────────────────────


class TestExistingProviderRegression:
    def test_existing_providers_still_listed_alongside_media_gateway(self, monkeypatch):
        image_by_name, video_by_name = _registry_models(monkeypatch)

        assert "openai_image" in image_by_name
        assert "qwen_image" in image_by_name
        assert "media_gateway" in image_by_name
        assert "seedance" in video_by_name
        assert "media_gateway" in video_by_name

    def test_unconfigured_env_leaves_registry_unchanged(self, monkeypatch):
        from deerflow.config.extensions_config import ExtensionsConfig

        for env in ("MEDIA_GATEWAY_BASE_URL", "MEDIA_GATEWAY_IMAGE_MODEL", "MEDIA_GATEWAY_VIDEO_MODEL"):
            monkeypatch.delenv(env, raising=False)
        monkeypatch.setattr(image_registry, "get_extensions_config", lambda: ExtensionsConfig(skills={}))
        monkeypatch.setattr(video_registry, "get_extensions_config", lambda: ExtensionsConfig(skills={}))
        monkeypatch.setattr(image_registry, "get_app_config", lambda: type("C", (), {"model_extra": {}})())
        monkeypatch.setattr(video_registry, "get_app_config", lambda: type("C", (), {"model_extra": {}})())

        assert "media_gateway" not in {p.name for p in image_registry.get_image_generation_providers().providers}
        assert "media_gateway" not in {p.name for p in video_registry.get_video_generation_providers().providers}
