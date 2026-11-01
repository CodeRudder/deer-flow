"""Unit tests for the Media Gateway image/video generation provider adapter (M2-1/M2-2).

Uses a lightweight fake ``httpx.MockTransport`` (no dependency on the MGW
mock_server module) and the registry wiring for the ``media_gateway`` provider.
"""

from __future__ import annotations

import json

import httpx
import pytest

from deerflow.media_gateway.client import MediaGatewayClient
from deerflow.media_gateway.providers import (
    MEDIA_GATEWAY_FILE_PROXY_PREFIX,
    MediaGatewayGenerationProvider,
    ModelCapability,
    UnsupportedParameterError,
    check_params_supported,
    map_capabilities,
    normalize_status,
    proxy_media_url,
)
from deerflow.media_gateway.settings import set_media_gateway_settings
from deerflow.models.image_generation import registry as image_registry
from deerflow.models.video_generation import registry as video_registry

# --- fakes ---------------------------------------------------------------------


class FakeBackend:
    """Records requests and replays scripted MGW responses.

    Each entry is ``(method, path-prefix, response)`` consumed in order, or
    ``(method, path-prefix, response, repeat=True)`` for a sticky response that
    answers every matching request (used for poll-until-timeout).
    """

    def __init__(self, responses: list[tuple]):
        self.responses = responses
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for index, entry in enumerate(self.responses):
            method, prefix, response = entry[0], entry[1], entry[2]
            repeat = entry[3] if len(entry) > 3 else False
            if request.method == method and request.url.path.startswith(prefix):
                if not repeat:
                    self.responses.pop(index)
                return response
        return httpx.Response(404, json={"error": {"code": "not_found", "message": "no scripted response", "retryable": False}})

    def transport(self) -> httpx.AsyncBaseTransport:
        return httpx.MockTransport(self.handler)

    def last_json(self) -> dict:
        body = self.requests[-1].read().decode("utf-8")
        return json.loads(body) if body else {}

    def last_header(self, name: str) -> str | None:
        return self.requests[-1].headers.get(name)


# Shape mirrors GET /v1/images/modes (images.md §1): {default_mode, modes[]}.
CAPABILITIES_BODY = {
    "default_mode": "mgw-image-1",
    "modes": [
        {
            "name": "mgw-image-1",
            "display_name": "MGW Image 1",
            "available": True,
            "ratios": ["1:1", "16:9"],
            "resolutions": ["1024x1024"],
            "supported_params": ["prompt", "ratio"],
        },
        {
            "name": "mgw-image-ref",
            "supported_params": ["prompt"],
            "supports_reference": True,
        },
    ],
}


def _provider(backend: FakeBackend, token_provider=None, sleep=None) -> MediaGatewayGenerationProvider:
    client = MediaGatewayClient("http://mgw", "admin-token", transport=backend.transport())
    return MediaGatewayGenerationProvider(
        client,
        kind="image",
        token_provider=token_provider,
        sleep=sleep or _no_sleep,
        poll_interval=0,
    )


async def _no_sleep(_seconds: float) -> None:
    return None


async def _fake_token() -> str:
    return "user-h3t-token"


# --- capability discovery mapping ----------------------------------------------


class TestCapabilityMapping:
    def test_maps_model_matrix_ratio_and_param_domains(self):
        caps = map_capabilities(CAPABILITIES_BODY)

        assert set(caps) == {"mgw-image-1", "mgw-image-ref"}
        first = caps["mgw-image-1"]
        assert first.ratios == ("1:1", "16:9")
        assert first.resolutions == ("1024x1024",)
        assert first.supported_params == frozenset({"prompt", "ratio"})
        assert "image" not in first.supported_params  # no reference support declared

    def test_reference_support_adds_image_params(self):
        caps = map_capabilities(CAPABILITIES_BODY)
        assert "image" in caps["mgw-image-ref"].supported_params
        assert "reference_images" in caps["mgw-image-ref"].supported_params

    def test_missing_param_domain_is_unsupported(self):
        caps = map_capabilities(CAPABILITIES_BODY)
        # mgw-image-1 declares no resolution parameter domain and no duration.
        assert "resolution" not in caps["mgw-image-1"].supported_params
        assert caps["mgw-image-1"].duration_range is None

    def test_accepts_data_wrapped_list(self):
        caps = map_capabilities({"data": [{"id": "m1", "aspect_ratios": ["1:1"]}]})
        assert caps["m1"].ratios == ("1:1",)

    def test_local_rejection_of_unsupported_param(self):
        cap = map_capabilities(CAPABILITIES_BODY)["mgw-image-1"]
        with pytest.raises(UnsupportedParameterError):
            check_params_supported(cap, {"resolution": "2048x2048"})

    def test_local_rejection_of_ratio_outside_enum(self):
        cap = map_capabilities(CAPABILITIES_BODY)["mgw-image-1"]
        with pytest.raises(UnsupportedParameterError):
            check_params_supported(cap, {"ratio": "9:16"})

    def test_supported_params_pass(self):
        cap = map_capabilities(CAPABILITIES_BODY)["mgw-image-1"]
        check_params_supported(cap, {"ratio": "16:9"})

    def test_none_values_are_not_rejected(self):
        cap = ModelCapability(name="m", supported_params=frozenset({"prompt"}))
        check_params_supported(cap, {"ratio": None})


# --- submission (Idempotency-Key + user token) ----------------------------------


class TestSubmit:
    @pytest.mark.anyio
    async def test_submit_sends_idempotency_key_and_user_token(self):
        backend = FakeBackend(
            [
                ("GET", "/v1/images/modes", httpx.Response(200, json=CAPABILITIES_BODY)),
                ("POST", "/v1/images/generations", httpx.Response(202, json={"id": "t-1", "kind": "generate", "status": "queued"})),
            ]
        )
        provider = _provider(backend, token_provider=_fake_token)

        task_id = await provider.submit("a cat", model="mgw-image-1", params={"ratio": "1:1"}, idempotency_key="idem-123")

        assert task_id == "t-1"
        submit = backend.requests[1]
        assert submit.headers.get("Idempotency-Key") == "idem-123"
        assert submit.headers.get("Authorization") == "Bearer user-h3t-token"
        assert backend.last_json() == {"mode": "mgw-image-1", "prompt": "a cat", "ratio": "1:1"}

    @pytest.mark.anyio
    async def test_submit_rejects_unsupported_param_without_http_call(self):
        backend = FakeBackend([("GET", "/v1/images/modes", httpx.Response(200, json=CAPABILITIES_BODY))])
        provider = _provider(backend, token_provider=_fake_token)

        with pytest.raises(UnsupportedParameterError):
            await provider.submit("a cat", model="mgw-image-1", params={"duration": 5})

        # Only the capability request happened; the submit never left.
        assert [r.method for r in backend.requests] == ["GET"]

    @pytest.mark.anyio
    async def test_submit_rejects_unknown_model(self):
        backend = FakeBackend([("GET", "/v1/images/modes", httpx.Response(200, json=CAPABILITIES_BODY))])
        provider = _provider(backend, token_provider=_fake_token)

        with pytest.raises(UnsupportedParameterError):
            await provider.submit("a cat", model="nope")

    @pytest.mark.anyio
    async def test_token_provider_injection_overrides_admin_token(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("Authorization")
            return httpx.Response(200, json={"models": []})

        client = MediaGatewayClient("http://mgw", "admin-token", transport=httpx.MockTransport(handler))
        provider = MediaGatewayGenerationProvider(client, kind="image", token_provider=_fake_token)
        try:
            await provider.discover_capabilities()
        finally:
            await client.aclose()
        assert seen["auth"] == "Bearer user-h3t-token"

    @pytest.mark.anyio
    async def test_without_token_provider_no_auth_header_override(self):
        # Without the injectable provider the client falls back to its default
        # (admin) token; generation callers must always wire a user token
        # (see M2-3), this only pins the fallback behaviour.
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("Authorization")
            return httpx.Response(200, json={"models": []})

        client = MediaGatewayClient("http://mgw", "admin-token", transport=httpx.MockTransport(handler))
        provider = MediaGatewayGenerationProvider(client, kind="image")
        try:
            await provider.discover_capabilities()
        finally:
            await client.aclose()
        assert seen["auth"] == "Bearer admin-token"

    @pytest.mark.anyio
    async def test_video_kind_uses_video_paths(self):
        backend = FakeBackend(
            [
                ("GET", "/v1/images/modes", httpx.Response(200, json={"modes": [{"name": "mgw-vid", "duration_range": {"min": 4, "max": 15}}]})),
                ("POST", "/v1/images/generations", httpx.Response(202, json={"id": "v-9", "kind": "generate", "status": "queued"})),
            ]
        )
        client = MediaGatewayClient("http://mgw", "admin-token", transport=backend.transport())
        provider = MediaGatewayGenerationProvider(client, kind="video", token_provider=_fake_token, sleep=_no_sleep)
        try:
            task_id = await provider.submit("a wave", model="mgw-vid")
        finally:
            await client.aclose()
        assert task_id == "v-9"
        # Real contract: video shares the image endpoints; kind only flips the
        # ``output`` parameter on submit.
        assert backend.requests[0].url.path == "/v1/images/modes"
        assert backend.requests[1].url.path == "/v1/images/generations"
        assert backend.last_json()["output"] == "video"


# --- polling state transitions ---------------------------------------------------


class TestPolling:
    @pytest.mark.anyio
    async def test_poll_transitions_pending_to_succeeded(self):
        backend = FakeBackend(
            [
                ("GET", "/v1/images/jobs/t-1", httpx.Response(200, json={"id": "t-1", "status": "queued"})),
                ("GET", "/v1/images/jobs/t-1", httpx.Response(200, json={"id": "t-1", "status": "running"})),
                ("GET", "/v1/images/jobs/t-1", httpx.Response(200, json={"id": "t-1", "status": "completed", "data": [{"b64_json": None, "url": "/v1/images/jobs/t-1/file?kind=image&variant=0&v=1", "v": "1", "meta": {}}]})),
            ]
        )
        provider = _provider(backend, token_provider=_fake_token)

        result = await provider.wait_for_result("t-1")

        assert result["data"][0]["url"].startswith("/v1/images/jobs/t-1/file?kind=image")
        assert [r.method for r in backend.requests] == ["GET", "GET", "GET"]

    @pytest.mark.anyio
    async def test_poll_raises_on_failed_status(self):
        backend = FakeBackend([("GET", "/v1/images/jobs/t-2", httpx.Response(200, json={"status": "failed", "error": "moderation"}))])
        provider = _provider(backend, token_provider=_fake_token)

        from deerflow.media_gateway.client import MediaGatewayError

        with pytest.raises(MediaGatewayError, match="task_failed"):
            await provider.wait_for_result("t-2")

    @pytest.mark.anyio
    async def test_poll_times_out_after_max_attempts(self):
        backend = FakeBackend([("GET", "/v1/images/jobs/t-3", httpx.Response(200, json={"status": "pending"}), True)])
        provider = _provider(backend, token_provider=_fake_token)

        from deerflow.media_gateway.client import MediaGatewayError

        with pytest.raises(MediaGatewayError, match="task_timeout"):
            await provider.wait_for_result("t-3", max_attempts=3)
        # unknown status counts as pending and keeps polling
        assert len(backend.requests) == 3

    def test_normalize_status(self):
        assert normalize_status("succeeded") == "succeeded"
        assert normalize_status("COMPLETED") == "succeeded"
        assert normalize_status("cancelled") == "failed"
        assert normalize_status("queued") == "pending"
        assert normalize_status(None) == "pending"
        assert normalize_status("brand-new-future-state") == "pending"


# --- artifact URL rewriting -------------------------------------------------------


class TestArtifactUrl:
    def test_proxy_prefix_constant(self):
        assert MEDIA_GATEWAY_FILE_PROXY_PREFIX == "/api/media-gateway/file"

    def test_rewrites_absolute_url_to_proxy_path(self):
        assert proxy_media_url("http://mgw-host:8000/v1/files/abc/download") == "/api/media-gateway/file/v1/files/abc/download"

    def test_rewrites_bare_path(self):
        assert proxy_media_url("v1/files/abc") == "/api/media-gateway/file/v1/files/abc"
        assert proxy_media_url("/v1/files/abc?download=1") == "/api/media-gateway/file/v1/files/abc?download=1"

    @pytest.mark.anyio
    async def test_generate_returns_proxied_url(self):
        backend = FakeBackend(
            [
                ("GET", "/v1/images/modes", httpx.Response(200, json=CAPABILITIES_BODY)),
                ("POST", "/v1/images/generations", httpx.Response(202, json={"id": "t-9", "kind": "generate", "status": "queued"})),
                ("GET", "/v1/images/jobs/t-9", httpx.Response(200, json={"status": "completed", "data": [{"b64_json": None, "url": "/v1/images/jobs/t-9/file?kind=image&variant=0&v=1", "v": "1", "meta": {}}]})),
            ]
        )
        provider = _provider(backend, token_provider=_fake_token)

        url = await provider.generate("a cat", model="mgw-image-1", idempotency_key="idem-9")

        assert url == "/api/media-gateway/file/v1/images/jobs/t-9/file?kind=image&variant=0&v=1"


# --- registry wiring --------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_mgw_settings(monkeypatch):
    # The registry gating reads the environment directly (no dotenv), so env
    # control is enough; the settings singleton is only reset for hygiene.
    set_media_gateway_settings(None)
    for env in ("MEDIA_GATEWAY_BASE_URL", "MEDIA_GATEWAY_IMAGE_MODEL", "MEDIA_GATEWAY_VIDEO_MODEL"):
        monkeypatch.delenv(env, raising=False)
    yield
    set_media_gateway_settings(None)


def _skills_enabled(monkeypatch):
    from deerflow.config.extensions_config import ExtensionsConfig

    monkeypatch.setattr(image_registry, "get_extensions_config", lambda: ExtensionsConfig(skills={}))
    monkeypatch.setattr(video_registry, "get_extensions_config", lambda: ExtensionsConfig(skills={}))
    monkeypatch.setattr(image_registry, "get_app_config", lambda: type("C", (), {"model_extra": {}})())
    monkeypatch.setattr(video_registry, "get_app_config", lambda: type("C", (), {"model_extra": {}})())


class TestRegistryWiring:
    def test_image_registry_appends_media_gateway_when_configured(self, monkeypatch):
        _skills_enabled(monkeypatch)
        monkeypatch.setenv("MEDIA_GATEWAY_BASE_URL", "http://mgw:8000")
        monkeypatch.setenv("MEDIA_GATEWAY_IMAGE_MODEL", "mgw-image-1")

        response = image_registry.get_image_generation_providers()

        by_name = {p.name: p for p in response.providers}
        assert "media_gateway" in by_name
        provider = by_name["media_gateway"]
        assert provider.configured is True
        assert [m.name for m in provider.models] == ["mgw-image-1"]

    def test_video_registry_appends_media_gateway_when_configured(self, monkeypatch):
        _skills_enabled(monkeypatch)
        monkeypatch.setenv("MEDIA_GATEWAY_BASE_URL", "http://mgw:8000")
        monkeypatch.setenv("MEDIA_GATEWAY_VIDEO_MODEL", "mgw-vid-1")

        response = video_registry.get_video_generation_providers()

        by_name = {p.name: p for p in response.providers}
        assert "media_gateway" in by_name
        assert [m.name for m in by_name["media_gateway"].models] == ["mgw-vid-1"]

    def test_registry_omits_media_gateway_when_unconfigured(self, monkeypatch):
        _skills_enabled(monkeypatch)

        image_response = image_registry.get_image_generation_providers()
        video_response = video_registry.get_video_generation_providers()

        assert "media_gateway" not in {p.name for p in image_response.providers}
        assert "media_gateway" not in {p.name for p in video_response.providers}

    def test_media_gateway_uses_env_default_model(self, monkeypatch):
        _skills_enabled(monkeypatch)
        monkeypatch.setenv("MEDIA_GATEWAY_BASE_URL", "http://mgw:8000")
        monkeypatch.setenv("MEDIA_GATEWAY_IMAGE_MODEL", "env-image-model")
        monkeypatch.setenv("MEDIA_GATEWAY_VIDEO_MODEL", "env-video-model")

        image_by_name = {p.name: p for p in image_registry.get_image_generation_providers().providers}
        video_by_name = {p.name: p for p in video_registry.get_video_generation_providers().providers}

        assert [m.name for m in image_by_name["media_gateway"].models] == ["env-image-model"]
        assert [m.name for m in video_by_name["media_gateway"].models] == ["env-video-model"]

    def test_config_declared_media_gateway_is_not_duplicated(self, monkeypatch):
        _skills_enabled(monkeypatch)
        monkeypatch.setenv("MEDIA_GATEWAY_BASE_URL", "http://mgw:8000")
        monkeypatch.setattr(
            image_registry,
            "get_app_config",
            lambda: type(
                "C",
                (),
                {"model_extra": {"image_generation": {"providers": [{"name": "media_gateway", "models": ["cfg-model"]}]}}},
            )(),
        )

        providers = image_registry.get_image_generation_providers().providers

        assert [p.name for p in providers].count("media_gateway") == 1


# --- runtime model enumeration (modes → registry model list) ----------------------


from deerflow.media_gateway import providers as mgw_providers  # noqa: E402
from deerflow.media_gateway.client import MediaGatewayError  # noqa: E402


def _fake_modes_body() -> dict:
    return {
        "default_mode": "h3-frame-std",
        "baseline_modes": ["h3-frame-draft"],
        "modes": [
            {"name": "qwen-image-std", "available": True, "engine_task": "qwen-image", "params": {"steps": 30, "short_edge": 1024, "duration_seconds": 5}, "description": "Qwen 文生图 / 参考图生图", "accepts_reference_images": True},
            {"name": "h3-ref-std", "available": True, "engine_task": "ref2va", "needs_reference": True, "params": {"steps": 4, "short_edge": 768, "duration_seconds": 5}, "description": "ref2va 多参考图 → 短片段抽帧"},
            {"name": "h3-ref-hq", "available": True, "engine_task": "ref2va", "needs_reference": True, "params": {"steps": 8, "short_edge": 768, "duration_seconds": 5}, "description": "ref2va 多参考图 → 短片段抽帧"},
            {"name": "h3-i2i-std", "available": True, "engine_task": "fl2va", "needs_reference": True, "params": {"steps": 4, "short_edge": 768, "duration_seconds": 5}, "description": "fl2va 参考图 → 短片段抽帧"},
            {"name": "moss-image-std", "available": True, "engine_task": "moss", "params": {"steps": 50, "short_edge": 1024, "duration_seconds": 5}, "description": "MOSS 文生图"},
            {"name": "h3-retired-tier", "available": False, "engine_task": "t2va", "params": {"steps": 4, "short_edge": 768, "duration_seconds": 4}, "description": "disabled"},
            {"name": "wan-t2v-std", "available": True, "engine_task": "wan-t2v", "params": {"steps": 20, "short_edge": 480, "duration_seconds": 5}, "capabilities": {"duration_tiers": [5]}, "description": "Wan t2v"},
            {"name": "ltx-t2v-std", "available": True, "engine_task": "ltx-t2v", "params": {"steps": 20, "short_edge": 720, "duration_seconds": 8}, "capabilities": {"duration_tiers": [2, 20]}, "description": "LTX t2v"},
            {"name": "wan-vace-std", "available": True, "engine_task": "wan-vace", "params": {"steps": 20, "short_edge": 480, "duration_seconds": 5}, "capabilities": {"duration_tiers": [5]}, "description": "Wan vace"},
            {"name": "ltx-offline", "available": False, "engine_task": "ltx-t2v", "params": {"steps": 20, "short_edge": 720, "duration_seconds": 8}, "description": "disabled video"},
        ],
    }


class _FakeFetch:
    def __init__(self, body=None, exc=None):
        self.body = body
        self.exc = exc
        self.calls = 0

    async def __call__(self, base_url, admin_token):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.body


@pytest.fixture()
def _mgw_enum_env(monkeypatch):
    monkeypatch.setenv("MEDIA_GATEWAY_BASE_URL", "http://mgw:8000")
    monkeypatch.setenv("MEDIA_GATEWAY_IMAGE_MODEL", "qwen-image-std")
    monkeypatch.setenv("MEDIA_GATEWAY_VIDEO_MODEL", "wan-t2v-std")
    yield
    mgw_providers.clear_media_gateway_models_cache()


@pytest.mark.anyio
class TestRuntimeModelEnumeration:
    async def test_full_enumeration_with_unavailable_filtered(self, monkeypatch, _mgw_enum_env):
        _skills_enabled(monkeypatch)
        fake = _FakeFetch(_fake_modes_body())
        monkeypatch.setattr(mgw_providers, "_fetch_modes_body", fake)

        assert await mgw_providers.refresh_media_gateway_models(force=True) is True

        image_by_name = {p.name: p for p in image_registry.get_image_generation_providers().providers}
        video_by_name = {p.name: p for p in video_registry.get_video_generation_providers().providers}

        image_names = [m.name for m in image_by_name["media_gateway"].models]
        video_names = [m.name for m in video_by_name["media_gateway"].models]

        # Full enumeration of available tiers, kind-split by engine_task.
        assert image_names == ["qwen-image-std", "h3-ref-std", "h3-ref-hq", "h3-i2i-std", "moss-image-std"]
        assert video_names == ["wan-t2v-std", "ltx-t2v-std", "wan-vace-std"]
        # unavailable tiers are dropped in both kinds
        assert "h3-retired-tier" not in image_names and "ltx-offline" not in video_names
        # env-default tier stays, first
        assert image_names[0] == "qwen-image-std" and video_names[0] == "wan-t2v-std"

        # Capability summary in the description: reference requirement + durations.
        ref_model = next(m for m in image_by_name["media_gateway"].models if m.name == "h3-ref-std")
        assert ref_model.description and "ref2va" in ref_model.description and "必须提供参考图" in ref_model.description
        ltx_model = next(m for m in video_by_name["media_gateway"].models if m.name == "ltx-t2v-std")
        assert ltx_model.description and "2–20s" in ltx_model.description

    async def test_ttl_cache_avoids_refetch(self, monkeypatch, _mgw_enum_env):
        _skills_enabled(monkeypatch)
        fake = _FakeFetch(_fake_modes_body())
        monkeypatch.setattr(mgw_providers, "_fetch_modes_body", fake)

        await mgw_providers.refresh_media_gateway_models()
        await mgw_providers.refresh_media_gateway_models()
        assert fake.calls == 1

        # force bypasses the TTL
        await mgw_providers.refresh_media_gateway_models(force=True)
        assert fake.calls == 2

    async def test_failure_falls_back_to_env_default(self, monkeypatch, _mgw_enum_env):
        _skills_enabled(monkeypatch)
        fake = _FakeFetch(exc=MediaGatewayError(status=0, code="transport_error", message="boom", retryable=True))
        monkeypatch.setattr(mgw_providers, "_fetch_modes_body", fake)

        assert await mgw_providers.refresh_media_gateway_models(force=True) is False

        image_by_name = {p.name: p for p in image_registry.get_image_generation_providers().providers}
        video_by_name = {p.name: p for p in video_registry.get_video_generation_providers().providers}
        # byte-identical legacy fallback: exactly the env-default model
        assert [m.name for m in image_by_name["media_gateway"].models] == ["qwen-image-std"]
        assert [m.name for m in video_by_name["media_gateway"].models] == ["wan-t2v-std"]

        # negative cache: no re-fetch inside the TTL window
        await mgw_providers.refresh_media_gateway_models()
        assert fake.calls == 1

    async def test_stale_good_snapshot_survives_refresh_failure(self, monkeypatch, _mgw_enum_env):
        _skills_enabled(monkeypatch)
        good = _FakeFetch(_fake_modes_body())
        monkeypatch.setattr(mgw_providers, "_fetch_modes_body", good)
        await mgw_providers.refresh_media_gateway_models(force=True)

        bad = _FakeFetch(exc=MediaGatewayError(status=0, code="http_error", message="500"))
        monkeypatch.setattr(mgw_providers, "_fetch_modes_body", bad)
        await mgw_providers.refresh_media_gateway_models(force=True)

        image_by_name = {p.name: p for p in image_registry.get_image_generation_providers().providers}
        names = [m.name for m in image_by_name["media_gateway"].models]
        assert "h3-ref-std" in names  # previous snapshot still served

    async def test_unconfigured_mgw_is_noop(self, monkeypatch):
        _skills_enabled(monkeypatch)
        monkeypatch.delenv("MEDIA_GATEWAY_BASE_URL", raising=False)
        fake = _FakeFetch(_fake_modes_body())
        monkeypatch.setattr(mgw_providers, "_fetch_modes_body", fake)

        assert await mgw_providers.refresh_media_gateway_models() is False
        assert fake.calls == 0
        assert "media_gateway" not in {p.name for p in image_registry.get_image_generation_providers().providers}

    async def test_env_default_without_enumeration_matches_legacy_output(self, monkeypatch, _mgw_enum_env):
        _skills_enabled(monkeypatch)
        image_by_name = {p.name: p for p in image_registry.get_image_generation_providers().providers}
        provider = image_by_name["media_gateway"]
        assert [m.name for m in provider.models] == ["qwen-image-std"]
        assert provider.models[0].description == "通过 Media Gateway 生成图片；能力（比例/参数域）以网关侧 capability API 为准，网关不支持的参数会被本地拒绝。"
