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


# --- short_edge / aspect_ratio local validation + passthrough --------------------


class TestShortEdgeValidation:
    def _cap(self) -> ModelCapability:
        # Merged-tier shape: only ``prompt`` is declared, yet short_edge /
        # aspect_ratio are accepted (validated locally) — the gateway canvas
        # takes any positive short_edge in range.
        return ModelCapability(name="m", supported_params=frozenset({"prompt"}))

    @pytest.mark.parametrize("value", [512, 256, 2048, 512.0, 720])
    def test_valid_short_edge_passes(self, value):
        check_params_supported(self._cap(), {"short_edge": value})

    @pytest.mark.parametrize("value", [255, 2049, -512, 0])
    def test_out_of_range_short_edge_rejected(self, value):
        with pytest.raises(UnsupportedParameterError, match=r"between 256 and 2048"):
            check_params_supported(self._cap(), {"short_edge": value})

    @pytest.mark.parametrize("value", ["512", "abc", None])
    def test_non_number_short_edge_rejected(self, value):
        if value is None:
            # ``None`` means "not supplied" — never validated, never rejected.
            check_params_supported(self._cap(), {"short_edge": None})
            return
        with pytest.raises(UnsupportedParameterError, match="short_edge must be a number"):
            check_params_supported(self._cap(), {"short_edge": value})

    @pytest.mark.parametrize("value", [512.5, 0.5])
    def test_fractional_short_edge_rejected(self, value):
        with pytest.raises(UnsupportedParameterError, match="short_edge must be an integer"):
            check_params_supported(self._cap(), {"short_edge": value})

    def test_bool_short_edge_rejected(self):
        with pytest.raises(UnsupportedParameterError, match="short_edge must be a number"):
            check_params_supported(self._cap(), {"short_edge": True})

    def test_error_message_carries_model_and_range(self):
        with pytest.raises(UnsupportedParameterError, match="model=m on media_gateway"):
            check_params_supported(self._cap(), {"short_edge": 99999})


class TestAspectRatioParam:
    def test_aspect_ratio_allowed_without_declaration(self):
        check_params_supported(ModelCapability(name="m", supported_params=frozenset({"prompt"})), {"aspect_ratio": "16:9"})

    def test_aspect_ratio_checked_against_ratios_when_declared(self):
        cap = ModelCapability(name="m", supported_params=frozenset({"prompt"}), ratios=("1:1", "16:9"))
        check_params_supported(cap, {"aspect_ratio": "16:9"})
        with pytest.raises(UnsupportedParameterError, match="aspect_ratio='9:16'"):
            check_params_supported(cap, {"aspect_ratio": "9:16"})

    def test_other_unknown_params_still_rejected(self):
        with pytest.raises(UnsupportedParameterError, match="does not support parameter\\(s\\): duration"):
            check_params_supported(ModelCapability(name="m", supported_params=frozenset({"prompt"})), {"duration": 5})


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
    async def test_submit_forwards_short_edge_and_aspect_ratio(self):
        # short_edge / aspect_ratio pass the merged-tier param check and are
        # forwarded verbatim into the gateway payload.
        backend = FakeBackend(
            [
                ("GET", "/v1/images/modes", httpx.Response(200, json=CAPABILITIES_BODY)),
                ("POST", "/v1/images/generations", httpx.Response(202, json={"id": "t-2"})),
                ("GET", "/v1/images/modes", httpx.Response(200, json=CAPABILITIES_BODY)),
                ("POST", "/v1/images/generations", httpx.Response(202, json={"id": "t-3"})),
            ]
        )
        provider = _provider(backend, token_provider=_fake_token)

        await provider.submit("a cat", model="mgw-image-1", params={"short_edge": 512, "aspect_ratio": "16:9"})
        assert backend.last_json() == {"mode": "mgw-image-1", "prompt": "a cat", "short_edge": 512, "aspect_ratio": "16:9"}

        # invalid short_edge is rejected before any submit HTTP call
        backend.requests.clear()
        with pytest.raises(UnsupportedParameterError):
            await provider.submit("a cat", model="mgw-image-1", params={"short_edge": 4096})
        assert [r.method for r in backend.requests] == ["GET"]

    @pytest.mark.anyio
    async def test_submit_without_short_edge_unchanged(self):
        # No short_edge → payload identical to the pre-short_edge behaviour
        # (tier defaults decide the canvas).
        backend = FakeBackend(
            [
                ("GET", "/v1/images/modes", httpx.Response(200, json=CAPABILITIES_BODY)),
                ("POST", "/v1/images/generations", httpx.Response(202, json={"id": "t-4"})),
            ]
        )
        provider = _provider(backend, token_provider=_fake_token)

        await provider.submit("a cat", model="mgw-image-1", params={"ratio": "1:1"})
        assert backend.last_json() == {"mode": "mgw-image-1", "prompt": "a cat", "ratio": "1:1"}
        assert "short_edge" not in backend.last_json()

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
    from deerflow.media_gateway.settings import MediaGatewaySettings

    # Explicit *empty* settings (not None): with None the settings singleton
    # would lazily reload from the environment / dotenv, defeating the env
    # deletion below.
    set_media_gateway_settings(MediaGatewaySettings())
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
            {
                "name": "qwen-image-std",
                "available": True,
                "quality": "balanced",
                "engine_task": "qwen-image",
                "params": {"steps": 30, "short_edge": 1024, "duration_seconds": 5},
                "description": "Qwen 文生图",
                "accepts_reference_images": True,
            },
            {"name": "h3-ref-std", "available": True, "quality": "balanced", "engine_task": "ref2va", "needs_reference": True, "params": {"steps": 4, "short_edge": 768, "duration_seconds": 5}, "description": "ref2va 多参考图 → 短片段抽帧"},
            {"name": "h3-ref-hq", "available": True, "quality": "high", "engine_task": "ref2va", "needs_reference": True, "params": {"steps": 8, "short_edge": 768, "duration_seconds": 5}, "description": "ref2va 多参考图 → 短片段抽帧"},
            {"name": "h3-i2i-std", "available": True, "quality": "balanced", "engine_task": "fl2va", "needs_reference": True, "params": {"steps": 4, "short_edge": 768, "duration_seconds": 5}, "description": "fl2va 参考图 → 短片段抽帧"},
            {"name": "moss-image-std", "available": True, "quality": "balanced", "engine_task": "moss", "params": {"steps": 50, "short_edge": 1024, "duration_seconds": 5}, "description": "MOSS 文生图"},
            {"name": "h3-retired-tier", "available": False, "engine_task": "t2va", "params": {"steps": 4, "short_edge": 768, "duration_seconds": 4}, "description": "disabled"},
            {"name": "wan-t2v-std", "available": True, "quality": "balanced", "engine_task": "wan-t2v", "params": {"steps": 20, "short_edge": 480, "duration_seconds": 5}, "capabilities": {"duration_tiers": [5]}, "description": "Wan t2v"},
            {
                "name": "ltx-t2v-std",
                "available": True,
                "quality": "balanced",
                "engine_task": "ltx-t2v",
                "params": {"steps": 20, "short_edge": 720, "duration_seconds": 8},
                "capabilities": {"duration_tiers": [2, 20]},
                "description": "LTX t2v",
            },
            {
                "name": "wan-vace-std",
                "available": True,
                "quality": "balanced",
                "engine_task": "wan-vace",
                "params": {"steps": 20, "short_edge": 480, "duration_seconds": 5},
                "capabilities": {"duration_tiers": [5]},
                "description": "Wan vace",
            },
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

        # One merged model per engine family. The *2va families are dual-listed
        # (video-capable engines whose default output is frame extraction); the
        # merged item whose std rung IS the env-default tier is dropped there
        # because the registry prepends the env default itself.
        assert image_names == ["qwen-image-std", "h3-ref", "h3-i2v", "moss-image"]
        assert video_names == ["wan-t2v-std", "h3-ref", "h3-i2v", "ltx-t2v", "wan-vace"]
        # merged family names, never raw per-rung mode names
        for raw in ("h3-ref-std", "h3-ref-hq", "h3-i2i-std", "moss-image-std"):
            assert raw not in image_names
        # unavailable tiers are dropped in both kinds
        assert "h3-retired-tier" not in image_names and "ltx-offline" not in video_names
        # env-default tier stays, first
        assert image_names[0] == "qwen-image-std" and video_names[0] == "wan-t2v-std"

        # Capability summary in the merged description: reference requirement,
        # engine, and the quality rungs selectable via the quality parameter.
        ref_model = next(m for m in image_by_name["media_gateway"].models if m.name == "h3-ref")
        assert ref_model.description and "ref2va" in ref_model.description and "必须提供参考图" in ref_model.description
        assert "quality 档：std（默认）、hq=768p" in ref_model.description

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
        assert "h3-ref" in names  # previous snapshot still served

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


# --- same-family merging + merged-name submit resolution --------------------------


def _full_modes_body() -> dict:
    """Mirrors the real gateway payload: one mode per quality rung per engine."""

    def tier(name, task, quality, edge, dur, **extra):
        return {"name": name, "available": True, "quality": quality, "engine_task": task, "params": {"steps": 4, "short_edge": edge, "duration_seconds": dur}, "needs_reference": False, **extra}

    return {
        "default_mode": "h3-frame-std",
        "modes": [
            tier("h3-frame-draft", "t2va", "preview", 256, 4),
            tier("h3-frame-fast", "t2va", "draft", 256, 4),
            tier("h3-frame-std", "t2va", "balanced", 768, 4),
            tier("h3-frame-hq", "t2va", "high", 768, 4),
            tier("h3-clip-std", "t2va", "balanced", 768, 5),
            tier("h3-clip-hq", "t2va", "high", 768, 5),
            tier("h3-clip-ultra", "t2va", "ultra", 1024, 5),
            tier("h3-clip-2k", "t2va", "2k", 1472, 5),
            tier("moss-image-fast", "moss", "draft", 1024, 5),
            tier("moss-image-std", "moss", "balanced", 1024, 5),
            {
                "name": "qwen-image-std",
                "available": True,
                "quality": "balanced",
                "engine_task": "qwen-image",
                "params": {"steps": 30, "short_edge": 1024, "duration_seconds": 5},
                "description": "Qwen 文生图",
                "accepts_reference_images": True,
            },
            tier("h3-i2i-std", "fl2va", "balanced", 768, 5, needs_reference=True),
            tier("h3-i2i-hq", "fl2va", "high", 768, 5, needs_reference=True),
            tier("h3-i2i-ultra", "fl2va", "ultra", 1024, 5, needs_reference=True),
            tier("h3-i2i-2k", "fl2va", "2k", 1472, 5, needs_reference=True),
            tier("h3-ref-std", "ref2va", "balanced", 768, 5, needs_reference=True),
            tier("h3-ref-hq", "ref2va", "high", 768, 5, needs_reference=True),
            tier("wan-t2v-std", "wan-t2v", "balanced", 480, 5),
            tier("wan-t2v-fast", "wan-t2v", "draft", 480, 5),
            tier("wan-i2v-fast", "wan-i2v", "draft", 480, 5, accepts_reference_images=True),
            tier("wan-vace-std", "wan-vace", "balanced", 480, 5, accepts_reference_images=True),
            tier("ltx-t2v-std", "ltx-t2v", "balanced", 720, 8),
            tier("ltx-i2v-std", "ltx-i2v", "balanced", 720, 8, accepts_reference_images=True),
        ],
    }


class _StubClient:
    """Minimal client stub: serves the fake modes body, records submissions."""

    def __init__(self, body):
        self.body = body
        self.posts = []

    async def get(self, path, **kwargs):
        return self.body

    async def post(self, path, json=None, **kwargs):
        self.posts.append({"path": path, "json": json})
        return {"id": "job-1"}


@pytest.mark.anyio
class TestFamilyMergingAndAliases:
    async def test_one_model_per_engine_family(self, monkeypatch, _mgw_enum_env):
        _skills_enabled(monkeypatch)
        monkeypatch.setattr(mgw_providers, "_fetch_modes_body", _FakeFetch(_full_modes_body()))
        await mgw_providers.refresh_media_gateway_models(force=True)

        image_names = [m.name for m in {p.name: p for p in image_registry.get_image_generation_providers().providers}["media_gateway"].models]
        video_names = [m.name for m in {p.name: p for p in video_registry.get_video_generation_providers().providers}["media_gateway"].models]

        # image: 5 items (env default first, then one merged item per family)
        assert image_names == ["qwen-image-std", "h3-t2v", "moss-image", "h3-i2v", "h3-ref"]
        # video: 8 items; merged wan-t2v dropped because its std rung is the env default
        assert video_names == ["wan-t2v-std", "h3-t2v", "h3-i2v", "h3-ref", "wan-i2v", "wan-vace", "ltx-t2v", "ltx-i2v"]
        # no raw per-rung names leak into either list
        for raw in ("h3-frame-std", "h3-clip-2k", "h3-i2i-std", "moss-image-fast", "wan-t2v-fast"):
            assert raw not in image_names + video_names

    async def test_merged_description_carries_rungs_and_reference(self, monkeypatch, _mgw_enum_env):
        _skills_enabled(monkeypatch)
        monkeypatch.setattr(mgw_providers, "_fetch_modes_body", _FakeFetch(_full_modes_body()))
        await mgw_providers.refresh_media_gateway_models(force=True)

        models = {m.name: m for m in {p.name: p for p in image_registry.get_image_generation_providers().providers}["media_gateway"].models}
        t2v = models["h3-t2v"]
        # t2va semantics note + selectable resolution rungs with their sizes
        assert "文生视频·音画，可抽帧当生图" in t2v.description
        assert "quality 档：std（默认）、hq=768p、ultra=1024p、2k=1472p" in t2v.description
        assert "engine=t2va" in t2v.description
        i2v = models["h3-i2v"]
        assert "必须提供参考图" in i2v.description and "2k=1472p" in i2v.description

    async def test_submit_resolves_merged_name_and_quality(self, monkeypatch, _mgw_enum_env):
        _skills_enabled(monkeypatch)
        body = _full_modes_body()
        monkeypatch.setattr(mgw_providers, "_fetch_modes_body", _FakeFetch(body))
        await mgw_providers.refresh_media_gateway_models(force=True)

        client = _StubClient(body)
        provider = MediaGatewayGenerationProvider(client, kind="video")

        cases = [
            ({"quality": "2k"}, "h3-clip-2k"),
            ({"quality": "ultra"}, "h3-clip-ultra"),
            ({"quality": "hq"}, "h3-clip-hq"),
            ({}, "h3-clip-std"),  # default rung: largest balanced canvas/duration
            ({"quality": "2k"}, "h3-clip-2k"),
        ]
        for params, expected_mode in cases:
            client.posts.clear()
            await provider.submit("p", model="h3-t2v", params=dict(params))
            assert client.posts[-1]["json"]["mode"] == expected_mode
            # quality is a local selection parameter, never forwarded
            assert "quality" not in client.posts[-1]["json"]

        # missing rung falls down the ladder: h3-ref has no ultra/2k
        await provider.submit("p", model="h3-ref", params={"quality": "2k", "reference_images": ["x"]})
        assert client.posts[-1]["json"]["mode"] == "h3-ref-hq"

        # real mode names stay identity and still work
        await provider.submit("p", model="wan-t2v-std", params={})
        assert client.posts[-1]["json"]["mode"] == "wan-t2v-std"

    async def test_alias_resolution_fallbacks(self, monkeypatch, _mgw_enum_env):
        _skills_enabled(monkeypatch)
        monkeypatch.setattr(mgw_providers, "_fetch_modes_body", _FakeFetch(_full_modes_body()))
        await mgw_providers.refresh_media_gateway_models(force=True)

        available = ["h3-ref-std", "h3-ref-hq"]
        # 2k/ultra missing → falls to hq; unknown quality → std
        assert mgw_providers.resolve_merged_model_alias("h3-ref", "2k", available) == "h3-ref-hq"
        assert mgw_providers.resolve_merged_model_alias("h3-ref", None, available) == "h3-ref-std"
        assert mgw_providers.resolve_merged_model_alias("h3-ref", "weird", available) == "h3-ref-std"
        # real names pass through; unresolvable names returned unchanged
        assert mgw_providers.resolve_merged_model_alias("h3-ref-std", None, available) == "h3-ref-std"
        assert mgw_providers.resolve_merged_model_alias("nope", None, available) == "nope"

    async def test_unknown_quality_value_falls_back_to_std_rung(self, monkeypatch, _mgw_enum_env):
        _skills_enabled(monkeypatch)
        body = _full_modes_body()
        monkeypatch.setattr(mgw_providers, "_fetch_modes_body", _FakeFetch(body))
        await mgw_providers.refresh_media_gateway_models(force=True)

        client = _StubClient(body)
        provider = MediaGatewayGenerationProvider(client, kind="image")
        await provider.submit("p", model="h3-t2v", params={"quality": "garbage"})
        assert client.posts[-1]["json"]["mode"] == "h3-clip-std"

    async def test_submit_quality_and_short_edge_coexist(self, monkeypatch, _mgw_enum_env):
        # quality picks the rung (steps/duration tier); short_edge only
        # overrides the canvas short edge and rides along in the payload.
        _skills_enabled(monkeypatch)
        body = _full_modes_body()
        monkeypatch.setattr(mgw_providers, "_fetch_modes_body", _FakeFetch(body))
        await mgw_providers.refresh_media_gateway_models(force=True)

        client = _StubClient(body)
        provider = MediaGatewayGenerationProvider(client, kind="video")
        await provider.submit("p", model="h3-t2v", params={"quality": "2k", "short_edge": 512})
        sent = client.posts[-1]["json"]
        assert sent["mode"] == "h3-clip-2k"
        assert sent["short_edge"] == 512
        assert "quality" not in sent

        # invalid short_edge rejected even with a valid quality rung
        with pytest.raises(UnsupportedParameterError):
            await provider.submit("p", model="h3-t2v", params={"quality": "2k", "short_edge": 100})

    async def test_submit_forwards_aspect_ratio_on_merged_model(self, monkeypatch, _mgw_enum_env):
        _skills_enabled(monkeypatch)
        body = _full_modes_body()
        monkeypatch.setattr(mgw_providers, "_fetch_modes_body", _FakeFetch(body))
        await mgw_providers.refresh_media_gateway_models(force=True)

        client = _StubClient(body)
        provider = MediaGatewayGenerationProvider(client, kind="video")
        await provider.submit("p", model="h3-t2v", params={"aspect_ratio": "9:16"})
        assert client.posts[-1]["json"]["aspect_ratio"] == "9:16"

    async def test_merged_description_mentions_short_edge(self, monkeypatch, _mgw_enum_env):
        _skills_enabled(monkeypatch)
        monkeypatch.setattr(mgw_providers, "_fetch_modes_body", _FakeFetch(_full_modes_body()))
        await mgw_providers.refresh_media_gateway_models(force=True)

        models = {m.name: m for m in {p.name: p for p in image_registry.get_image_generation_providers().providers}["media_gateway"].models}
        assert "可用 short_edge 自定义短边（256–2048，如 512），不传用档位默认" in models["h3-t2v"].description
