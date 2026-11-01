"""Unit tests for the R2 extra image/video gateway APIs (R2-1 … R2-5).

Contract source of truth: ``minimax-h3-gateway/docs/integration/api/images.md``
(§1 modes, §6 compare, §7 shape-lock, §8 assessment, §9 cancel/delete).
Same fake ``httpx.MockTransport`` harness as ``test_media_gateway_providers.py``
— no dependency on the MGW mock_server.
"""

from __future__ import annotations

import json

import httpx
import pytest

from deerflow.media_gateway.client import MediaGatewayClient, MediaGatewayError
from deerflow.media_gateway.providers import (
    MediaGatewayGenerationProvider,
    UnsupportedParameterError,
    check_params_supported,
    map_capabilities,
    normalize_assessment,
)
from deerflow.media_gateway.qc import ATTR_CITATION, ATTR_PROMPT, ATTR_SEED, attribute_qc

# --- fakes ---------------------------------------------------------------------


class FakeBackend:
    """Records requests and replays scripted MGW responses (sticky w/ repeat)."""

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


async def _no_sleep(_seconds: float) -> None:
    return None


async def _fake_token() -> str:
    return "user-h3t-token"


def _provider(backend: FakeBackend, kind: str = "image") -> MediaGatewayGenerationProvider:
    client = MediaGatewayClient("http://mgw", "admin-token", transport=backend.transport())
    return MediaGatewayGenerationProvider(client, kind=kind, token_provider=_fake_token, sleep=_no_sleep, poll_interval=0)


CAPABILITIES_BODY = {
    "default_mode": "h3-frame-std",
    "baseline_modes": ["h3-frame-draft", "h3-frame-fast", "h3-frame-std", "h3-frame-hq"],
    "modes": [
        {"name": "h3-frame-std", "available": True, "engine_task": "t2va", "params": {"steps": 4, "short_edge": 768, "duration_seconds": 5}},
        {"name": "h3-i2i-std", "available": True, "needs_reference": True, "engine_task": "fl2va", "params": {"steps": 4, "short_edge": 768, "duration_seconds": 5}},
    ],
}

# R2-4: the full real tier list (gateway/h3img/config.py MODE_TIERS + the
# opt-in moss/qwen/wan/ltx tiers from images.md §1). Every available tier the
# gateway can report must map to a usable ModelCapability.
FULL_MODES_BODY = {
    "default_mode": "h3-frame-std",
    "baseline_modes": ["h3-frame-draft", "h3-frame-fast", "h3-frame-std", "h3-frame-hq"],
    "modes": [
        {"name": "h3-frame-draft", "available": True, "quality": "preview", "engine_task": "t2va", "eta_s": 12.0, "params": {"steps": 4, "short_edge": 256, "duration_seconds": 4}},
        {"name": "h3-frame-fast", "available": True, "quality": "draft", "engine_task": "t2va", "eta_s": 20.0, "params": {"steps": 8, "short_edge": 256, "duration_seconds": 4}},
        {"name": "h3-frame-std", "available": True, "quality": "balanced", "engine_task": "t2va", "eta_s": 150.0, "params": {"steps": 4, "short_edge": 768, "duration_seconds": 4}},
        {"name": "h3-frame-hq", "available": True, "quality": "high", "engine_task": "t2va", "eta_s": 240.0, "params": {"steps": 8, "short_edge": 768, "duration_seconds": 4}},
        {"name": "h3-clip-std", "available": True, "quality": "balanced", "engine_task": "t2va", "eta_s": 25.0, "params": {"steps": 4, "short_edge": 768, "duration_seconds": 5}},
        {"name": "h3-clip-hq", "available": True, "quality": "high", "engine_task": "t2va", "eta_s": 45.0, "params": {"steps": 8, "short_edge": 768, "duration_seconds": 5}},
        {"name": "h3-clip-ultra", "available": True, "quality": "ultra", "engine_task": "t2va", "eta_s": 60.0, "params": {"steps": 12, "short_edge": 1024, "duration_seconds": 5}},
        {"name": "h3-clip-2k", "available": True, "quality": "2k", "engine_task": "t2va", "eta_s": 195.0, "params": {"steps": 8, "short_edge": 1472, "duration_seconds": 5}},
        {"name": "h3-i2i-std", "available": True, "quality": "balanced", "engine_task": "fl2va", "eta_s": 35.0, "needs_reference": True, "params": {"steps": 4, "short_edge": 768, "duration_seconds": 5}},
        {"name": "h3-i2i-hq", "available": True, "quality": "high", "engine_task": "fl2va", "eta_s": 60.0, "needs_reference": True, "params": {"steps": 8, "short_edge": 768, "duration_seconds": 5}},
        {"name": "h3-i2i-ultra", "available": True, "quality": "ultra", "engine_task": "fl2va", "eta_s": 75.0, "needs_reference": True, "params": {"steps": 12, "short_edge": 1024, "duration_seconds": 5}},
        {"name": "h3-i2i-2k", "available": True, "quality": "2k", "engine_task": "fl2va", "eta_s": 210.0, "needs_reference": True, "params": {"steps": 8, "short_edge": 1472, "duration_seconds": 5}},
        {"name": "h3-ref-std", "available": True, "quality": "balanced", "engine_task": "ref2va", "eta_s": 155.0, "needs_reference": True, "params": {"steps": 4, "short_edge": 768, "duration_seconds": 5}},
        {"name": "h3-ref-hq", "available": True, "quality": "high", "engine_task": "ref2va", "eta_s": 175.0, "needs_reference": True, "params": {"steps": 8, "short_edge": 768, "duration_seconds": 5}},
        {"name": "moss-image-fast", "available": True, "quality": "balanced", "engine_task": "moss", "eta_s": 110.0, "needs_reference": False, "params": {"steps": 25, "short_edge": 1024, "duration_seconds": 5}},
        {"name": "moss-image-std", "available": True, "quality": "high", "engine_task": "moss", "eta_s": 220.0, "needs_reference": False, "params": {"steps": 50, "short_edge": 1024, "duration_seconds": 5}},
        {
            "name": "qwen-image-std",
            "available": True,
            "quality": "high",
            "engine_task": "qwen-image",
            "eta_s": 150.0,
            "needs_reference": False,
            "accepts_reference_images": True,
            "params": {"steps": 30, "short_edge": 1024, "duration_seconds": 5},
        },
        {"name": "wan-t2v-std", "available": True, "quality": "balanced", "engine_task": "wan-t2v", "eta_s": 900.0, "needs_reference": False, "params": {"steps": 20, "short_edge": 480, "duration_seconds": 5}},
        {"name": "wan-t2v-fast", "available": True, "quality": "preview", "engine_task": "wan-t2v", "eta_s": 300.0, "needs_reference": False, "params": {"steps": 20, "short_edge": 480, "duration_seconds": 5}},
        {
            "name": "wan-i2v-fast",
            "available": True,
            "quality": "preview",
            "engine_task": "wan-i2v",
            "eta_s": 360.0,
            "needs_reference": False,
            "accepts_reference_images": True,
            "capabilities": {"reference_images": True},
            "params": {"steps": 20, "short_edge": 480, "duration_seconds": 5},
        },
        {
            "name": "wan-vace-std",
            "available": True,
            "quality": "balanced",
            "engine_task": "wan-vace",
            "eta_s": 1200.0,
            "needs_reference": False,
            "accepts_reference_images": True,
            "capabilities": {"reference_images": True},
            "params": {"steps": 20, "short_edge": 480, "duration_seconds": 5},
        },
        {"name": "ltx-t2v-std", "available": True, "quality": "balanced", "engine_task": "ltx-t2v", "eta_s": 600.0, "needs_reference": False, "params": {"steps": 20, "short_edge": 720, "duration_seconds": 8}},
        {
            "name": "ltx-i2v-std",
            "available": True,
            "quality": "balanced",
            "engine_task": "ltx-i2v",
            "eta_s": 600.0,
            "needs_reference": False,
            "accepts_reference_images": True,
            "capabilities": {"reference_images": True, "audio": True},
            "params": {"steps": 20, "short_edge": 720, "duration_seconds": 8},
        },
        {"name": "h3-x-maintenance", "available": False, "engine_task": "t2va", "reason": "engine offline"},
    ],
}


# --- R2-1: compare ---------------------------------------------------------------


class TestCompare:
    @pytest.mark.anyio
    async def test_compare_submits_modes_and_groups_by_meta_mode(self):
        backend = FakeBackend(
            [
                ("GET", "/v1/images/modes", httpx.Response(200, json=CAPABILITIES_BODY)),
                (
                    "POST",
                    "/v1/images/compare",
                    httpx.Response(
                        200,
                        json={
                            "id": "cmp-1",
                            "status": "completed",
                            "data": [
                                {"url": "/v1/images/jobs/cmp-1/file?kind=image&variant=0&v=a", "meta": {"mode": "h3-frame-std", "index": 0}},
                                {"url": "/v1/images/jobs/cmp-1/file?kind=image&variant=1&v=b", "meta": {"mode": "h3-i2i-std", "index": 1}},
                            ],
                        },
                    ),
                ),
            ]
        )
        provider = _provider(backend)

        report = await provider.compare_generate("a cat", ["h3-frame-std", "h3-i2i-std"], params={"ratio": "1:1"}, idempotency_key="idem-cmp")

        assert report["job_id"] == "cmp-1"
        assert set(report["modes"]) == {"h3-frame-std", "h3-i2i-std"}
        assert report["modes"]["h3-frame-std"]["artifacts"] == ["/api/media-gateway/file/v1/images/jobs/cmp-1/file?kind=image&variant=0&v=a"]
        assert report["modes"]["h3-i2i-std"]["artifacts"] == ["/api/media-gateway/file/v1/images/jobs/cmp-1/file?kind=image&variant=1&v=b"]
        assert report["modes"]["h3-i2i-std"]["meta"]["mode"] == "h3-i2i-std"

        request = backend.requests[1]
        assert request.url.path == "/v1/images/compare"
        assert request.headers.get("Idempotency-Key") == "idem-cmp"
        assert request.headers.get("Authorization") == "Bearer user-h3t-token"
        payload = backend.last_json()
        assert payload["modes"] == ["h3-frame-std", "h3-i2i-std"]
        assert payload["prompt"] == "a cat"
        assert "include_baseline" not in payload

    @pytest.mark.anyio
    async def test_compare_include_baseline_and_no_duplicate_post_on_wait_body(self):
        backend = FakeBackend(
            [
                ("GET", "/v1/images/modes", httpx.Response(200, json=CAPABILITIES_BODY)),
                ("POST", "/v1/images/compare", httpx.Response(200, json={"status": "completed", "data": [{"url": "/f?kind=image", "meta": {"mode": "h3-frame-std"}}]})),
            ]
        )
        provider = _provider(backend)

        report = await provider.compare_generate("a cat", ["h3-frame-std"], include_baseline=True)

        assert backend.last_json()["include_baseline"] is True
        # no job id → nothing to poll; the wait body itself was the result
        assert [r.method for r in backend.requests] == ["GET", "POST"]
        assert report["modes"]["h3-frame-std"]["status"] == "succeeded"

    @pytest.mark.anyio
    async def test_compare_202_brief_is_polled(self):
        backend = FakeBackend(
            [
                ("GET", "/v1/images/modes", httpx.Response(200, json=CAPABILITIES_BODY)),
                ("POST", "/v1/images/compare", httpx.Response(202, json={"id": "cmp-2", "status": "queued"})),
                ("GET", "/v1/images/jobs/cmp-2", httpx.Response(200, json={"status": "completed", "data": [{"url": "/v1/images/jobs/cmp-2/file?kind=image&variant=0", "meta": {"mode": "h3-frame-std"}}]})),
            ]
        )
        provider = _provider(backend)

        report = await provider.compare_generate("a cat", ["h3-frame-std"])

        assert report["job_id"] == "cmp-2"
        assert [r.method for r in backend.requests] == ["GET", "POST", "GET"]
        assert report["modes"]["h3-frame-std"]["artifacts"]

    @pytest.mark.anyio
    async def test_compare_rejects_empty_and_unknown_and_unavailable_modes_before_post(self):
        unavailable_body = {**CAPABILITIES_BODY, "modes": CAPABILITIES_BODY["modes"] + [{"name": "h3-x", "available": False}]}
        backend = FakeBackend([("GET", "/v1/images/modes", httpx.Response(200, json=unavailable_body), True)])
        provider = _provider(backend)

        with pytest.raises(UnsupportedParameterError):
            await provider.compare_generate("a cat", [])
        with pytest.raises(UnsupportedParameterError):
            await provider.compare_generate("a cat", ["nope"])
        with pytest.raises(UnsupportedParameterError, match="not available"):
            await provider.compare_generate("a cat", ["h3-x"])
        # every rejection happened locally: no compare POST ever left
        # empty modes raises before discovery; the other two only fetch capabilities
        assert [r.method for r in backend.requests] == ["GET", "GET"]


# --- R2-2: cancel / delete --------------------------------------------------------


class TestCancelDelete:
    @pytest.mark.anyio
    async def test_cancel_queued_job(self):
        backend = FakeBackend([("DELETE", "/v1/images/jobs/j-1", httpx.Response(200, json={"cancelled": True}))])
        provider = _provider(backend)

        result = await provider.cancel_or_delete_job("j-1")

        assert result == {"cancelled": True, "deleted": False, "raw": {"cancelled": True}}
        request = backend.requests[0]
        assert request.url.path == "/v1/images/jobs/j-1"
        assert "force" not in str(request.url.params)

    @pytest.mark.anyio
    async def test_force_flag_and_delete_terminal_job(self):
        backend = FakeBackend(
            [
                ("DELETE", "/v1/images/jobs/j-run", httpx.Response(200, json={"cancelled": True})),
                ("DELETE", "/v1/images/jobs/j-done", httpx.Response(200, json={"deleted": True})),
            ]
        )
        provider = _provider(backend)

        await provider.cancel_or_delete_job("j-run", force=True)
        assert backend.requests[0].url.params["force"] == "1"

        result = await provider.cancel_or_delete_job("j-done")
        assert result["deleted"] is True and result["cancelled"] is False

    @pytest.mark.anyio
    async def test_wait_loop_honours_external_cancel_signal(self):
        backend = FakeBackend(
            [
                ("GET", "/v1/images/jobs/j-2", httpx.Response(200, json={"status": "queued"}), True),
                ("DELETE", "/v1/images/jobs/j-2", httpx.Response(200, json={"cancelled": True})),
            ]
        )
        provider = _provider(backend)
        polls = {"n": 0}

        async def cancel_after_first_poll() -> bool:
            polls["n"] += 1
            return polls["n"] > 1

        result = await provider.wait_for_result("j-2", max_attempts=10, cancel_check=cancel_after_first_poll)

        assert result["status"] == "cancelled" and result["cancelled"] is True and result["job_id"] == "j-2"
        assert [r.method for r in backend.requests] == ["GET", "DELETE"]

    @pytest.mark.anyio
    async def test_generate_surfaces_cancellation_as_error(self):
        backend = FakeBackend(
            [
                ("GET", "/v1/images/modes", httpx.Response(200, json=CAPABILITIES_BODY)),
                ("POST", "/v1/images/generations", httpx.Response(202, json={"id": "j-3", "status": "queued"})),
                ("DELETE", "/v1/images/jobs/j-3", httpx.Response(200, json={"cancelled": True})),
            ]
        )
        provider = _provider(backend)

        async def always() -> bool:
            return True

        with pytest.raises(MediaGatewayError, match="cancelled"):
            await provider.generate("a cat", model="h3-frame-std", cancel_check=always)
        assert [r.method for r in backend.requests] == ["GET", "POST", "DELETE"]


# --- R2-3: assessment --------------------------------------------------------------


class TestAssessment:
    @pytest.mark.anyio
    async def test_get_assessment_query_params_and_normalisation(self):
        backend = FakeBackend(
            [
                (
                    "GET",
                    "/v1/images/jobs/img-1/assessment",
                    httpx.Response(
                        200,
                        json={
                            "ok": True,
                            "seed": 1234,
                            "quality_score": 0.91,
                            "similarity": {"identity": 0.77},
                            "quality": {"issues": [{"severity": "S", "desc": "第 3 帧严重模糊"}]},
                        },
                    ),
                ),
            ]
        )
        provider = _provider(backend)

        report = await provider.get_assessment("img-1", variant=2, refresh=True)

        request = backend.requests[0]
        assert request.url.path == "/v1/images/jobs/img-1/assessment"
        assert request.url.params["variant"] == "2"
        assert request.url.params["refresh"] == "true"
        assert report["score"] == 0.91
        assert report["seed"] == 1234
        assert report["must_fix_storyboard"] is True
        assert report["issues"][0]["desc"] == "第 3 帧严重模糊"

    def test_normalize_assessment_falls_back_to_similarity_score_and_plain_issues(self):
        report = normalize_assessment({"ok": True, "similarity": {"identity": 0.62}, "issues": ["轻微抖动"]})
        assert report["score"] == 0.62
        assert report["must_fix_storyboard"] is False
        assert report["issues"] == [{"desc": "轻微抖动"}]

    def test_normalize_assessment_empty_body(self):
        report = normalize_assessment({"ok": True})
        assert report["score"] is None
        assert report["must_fix_storyboard"] is False
        assert report["issues"] == []

    @pytest.mark.anyio
    async def test_attribute_qc_consumes_assessment_data_source(self):
        # no assessment → the plain QC body drives attribution (identity low → prompt)
        base = attribute_qc({"state": "qc_fail", "issues": []})
        assert base["attribution"] == ATTR_PROMPT

        # assessment overlay: a citation-flavoured issue in the assessment flips to citation
        cited = attribute_qc({"state": "qc_fail"}, assessment={"score": None, "must_fix_storyboard": False, "issues": [{"desc": "参考图 <Picture 7> 超出托盘编号范围"}]})
        assert cited["attribution"] == ATTR_CITATION

        # assessment seed-flavoured issue → seed attribution
        seeded = attribute_qc({"state": "qc_fail"}, assessment={"issues": [{"desc": "motion flicker across frames"}]})
        assert seeded["attribution"] == ATTR_SEED

        # assessment low score counts as a low identity dimension → prompt
        scored = attribute_qc({"state": "qc_fail"}, assessment={"score": 0.5, "issues": []})
        assert scored["attribution"] == ATTR_PROMPT
        assert any("identity=0.5" in reason for reason in scored["reasons"])

        # assessment must_fix_storyboard wins over the QC body's False
        must_fix = attribute_qc({"state": "qc_fail", "must_fix_storyboard": False}, assessment={"must_fix_storyboard": True, "issues": []})
        assert must_fix["attribution"] == ATTR_PROMPT
        assert any("must_fix_storyboard" in reason for reason in must_fix["reasons"])

        # empty/absent assessment → pure heuristic fallback
        fallback = attribute_qc({"state": "qc_fail"}, assessment={})
        assert fallback["attribution"] == ATTR_PROMPT


# --- R2-4: full mode matrix → ModelCapability ----------------------------------------


class TestFullModeMatrix:
    def test_every_available_mode_maps_to_usable_capability(self):
        caps = map_capabilities(FULL_MODES_BODY)
        available_names = [m["name"] for m in FULL_MODES_BODY["modes"] if m.get("available", True)]

        # every available tier maps to a usable capability (unavailable tiers
        # are kept too, flagged available=False, so the skill can explain why)
        assert set(available_names) <= set(caps)
        for name in available_names:
            cap = caps[name]
            assert cap.available is True
            assert "prompt" in cap.supported_params
            source = next(m for m in FULL_MODES_BODY["modes"] if m["name"] == name)
            assert cap.engine_task == source["engine_task"]
            duration = source["params"]["duration_seconds"]
            assert cap.duration_range is not None and cap.duration_range[1] >= duration

    def test_unavailable_mode_is_flagged(self):
        caps = map_capabilities(FULL_MODES_BODY)
        assert caps["h3-x-maintenance"].available is False

    @pytest.mark.parametrize(
        "name",
        ["h3-i2i-std", "h3-i2i-hq", "h3-i2i-ultra", "h3-i2i-2k", "h3-ref-std", "h3-ref-hq"],
    )
    def test_needs_reference_tiers_mark_reference_images_mandatory(self, name):
        caps = map_capabilities(FULL_MODES_BODY)
        cap = caps[name]
        assert cap.needs_reference is True
        assert "reference_images" in cap.supported_params
        # skill layer rejection path: no reference image → local rejection, no HTTP
        with pytest.raises(UnsupportedParameterError, match="reference"):
            check_params_supported(cap, {"prompt": "a cat"})
        # with reference images the parameter set passes
        check_params_supported(cap, {"prompt": "a cat", "reference_images": ["/refs/a.png"]})

    @pytest.mark.parametrize("name", ["h3-frame-std", "h3-clip-2k", "moss-image-fast", "qwen-image-std", "wan-t2v-std", "ltx-t2v-std"])
    def test_text_only_tiers_do_not_require_reference(self, name):
        cap = map_capabilities(FULL_MODES_BODY)[name]
        assert cap.needs_reference is False
        check_params_supported(cap, {"prompt": "a cat"})

    @pytest.mark.parametrize("name", ["wan-i2v-fast", "wan-vace-std", "ltx-i2v-std", "qwen-image-std"])
    def test_accepts_reference_tiers_expose_optional_reference_domain(self, name):
        cap = map_capabilities(FULL_MODES_BODY)[name]
        assert cap.needs_reference is False
        assert "reference_images" in cap.supported_params


# --- R2-5: shape lock -----------------------------------------------------------------


class TestShapeLock:
    @pytest.mark.anyio
    async def test_get_shape_lock_locked_and_unlocked(self):
        locked = FakeBackend([("GET", "/v1/shape-lock", httpx.Response(200, json={"locked": {"short_edge": 768, "aspect_ratio": "7:4", "duration_seconds": 5.0}}))])
        provider = _provider(locked)
        assert await provider.check_shape_lock() == {"short_edge": 768, "aspect_ratio": "7:4", "duration_seconds": 5.0}
        assert locked.requests[0].url.path == "/v1/shape-lock"

        unlocked = FakeBackend([("GET", "/v1/shape-lock", httpx.Response(200, json={"locked": None}))])
        assert await _provider(unlocked).check_shape_lock() is None

    @pytest.mark.anyio
    async def test_generate_report_attaches_shape_lock_hint(self):
        backend = FakeBackend(
            [
                ("GET", "/v1/shape-lock", httpx.Response(200, json={"locked": {"short_edge": 768, "aspect_ratio": "7:4", "duration_seconds": 5.0}})),
                ("GET", "/v1/images/modes", httpx.Response(200, json=CAPABILITIES_BODY)),
                ("POST", "/v1/images/generations", httpx.Response(202, json={"id": "j-4", "status": "queued"})),
                ("GET", "/v1/images/jobs/j-4", httpx.Response(200, json={"status": "completed", "data": [{"url": "/v1/images/jobs/j-4/file?kind=image&variant=0"}]})),
            ]
        )
        provider = _provider(backend)

        report = await provider.generate_report("a cat", model="h3-frame-std", validate_shape_lock=True)

        assert report["artifact"] == "/api/media-gateway/file/v1/images/jobs/j-4/file?kind=image&variant=0"
        assert report["shape_lock"]["short_edge"] == 768
        # hint only: the generation was NOT blocked by the lock
        assert [r.method for r in backend.requests] == ["GET", "GET", "POST", "GET"]

    @pytest.mark.anyio
    async def test_generate_report_skips_shape_lock_by_default(self):
        backend = FakeBackend(
            [
                ("GET", "/v1/images/modes", httpx.Response(200, json=CAPABILITIES_BODY)),
                ("POST", "/v1/images/generations", httpx.Response(202, json={"id": "j-5", "status": "queued"})),
                ("GET", "/v1/images/jobs/j-5", httpx.Response(200, json={"status": "completed", "data": [{"url": "/v1/images/jobs/j-5/file?kind=image&variant=0"}]})),
            ]
        )
        provider = _provider(backend)

        report = await provider.generate_report("a cat", model="h3-frame-std")

        assert report["shape_lock"] is None
        assert [r.method for r in backend.requests] == ["GET", "POST", "GET"]
