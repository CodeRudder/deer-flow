"""t2i-series（文生组图）编排层测试。

Drives :mod:`deerflow.media_gateway.t2i_series` — the testable orchestration
behind the ``t2i-series`` skill — against :class:`MockMediaGateway`'s images
endpoints (规划 → 逐张提交（幂等）→ QC 失败归因/修正 → 一致性校验 → series
登记), covering design.md §8.3 / acceptance A2c behaviours: per-image
idempotency keys, shared style params, ≤3-round QC loop, and proxied artifact
URLs.
"""

from __future__ import annotations

import json

import pytest

from deerflow.media_gateway.client import MediaGatewayError
from deerflow.media_gateway.mock_server import MockMediaGateway
from deerflow.media_gateway.t2i_series import (
    SERIES_PATH,
    T2iSeriesError,
    T2iSeriesOrchestrator,
    build_generation_request,
    build_prompt,
    check_consistency,
    check_plan,
    default_series_plan,
    run_t2i_series,
)

PNG_BYTES = b"\x89PNG-mock-series-image"


@pytest.fixture
async def mg():
    mock = MockMediaGateway()
    client = mock.client()
    try:
        yield mock, client
    finally:
        await client.aclose()


def _plan() -> dict:
    return {
        "theme": "城市夜跑",
        "style": {"mode": "h3-frame-std", "aspect_ratio": "16:9", "short_edge": 768, "style_desc": "统一插画风，低饱和暖色调"},
        "images": [
            {"index": 1, "subject": "跑者在路灯下系鞋带", "scene": "夜色街道，暖黄路灯光"},
            {"index": 2, "subject": "跑者起跑冲出画面", "scene": "同街道，镜头低角度跟随"},
            {"index": 3, "subject": "跑者在桥上停下喘息，回望城市", "scene": "桥面俯瞰，城市灯海背景"},
        ],
    }


class TestPlanAndConsistency:
    def test_default_plan_skeleton_passes_check(self):
        plan = default_series_plan("秋日咖啡", count=4)
        assert check_plan(plan) == []
        assert len(plan["images"]) == 4

    def test_check_plan_flags_incomplete_design(self):
        issues = check_plan({"theme": "", "style": {"mode": "m"}, "images": [{"index": 2, "subject": "x"}]})
        assert any("主题" in i for i in issues)
        assert any("style_desc" in i for i in issues)
        assert any("编号不连续" in i for i in issues)
        assert any("scene" in i for i in issues)

    def test_count_bounds(self):
        with pytest.raises(T2iSeriesError):
            default_series_plan("主题", count=9)

    def test_prompt_carries_shared_style_segment(self):
        plan = _plan()
        prompts = [build_prompt(plan, img) for img in plan["images"]]
        assert all(p.startswith("统一插画风") for p in prompts)
        assert prompts[0] != prompts[1]

    def test_generation_request_shares_style_params(self):
        plan = _plan()
        payloads = [build_generation_request(plan, img) for img in plan["images"]]
        assert all(p["mode"] == "h3-frame-std" for p in payloads)
        assert all(p["aspect_ratio"] == "16:9" and p["short_edge"] == 768 for p in payloads)
        assert all(p["output"] == "image" for p in payloads)

    def test_consistency_flags_style_drift_and_missing_images(self):
        plan = _plan()
        good = build_generation_request(plan, plan["images"][0])
        drift = {**good, "aspect_ratio": "9:16", "prompt": "没有风格段的提示词"}
        issues = check_consistency(plan, {1: good, 2: drift})
        assert any("第 2 张 aspect_ratio" in i for i in issues)
        assert any("风格段" in i for i in issues)
        assert any("第 3 张没有提交记录" in i for i in issues)
        # 张数齐全性：3 张都提交但只有 2 张有产物
        issues = check_consistency(plan, {i: good for i in (1, 2, 3)}, completed_indexes=[1, 3])
        assert any("张数不齐" in i and "2" in i for i in issues)

    @pytest.mark.anyio
    async def test_plan_gate_blocks_submission(self, mg):
        mock, client = mg
        bad = _plan()
        bad["images"][1]["subject"] = ""
        with pytest.raises(T2iSeriesError):
            await run_t2i_series(client, mock.admin_token, plan=bad)
        assert not [r for r in mock.requests if r[1] == "/v1/images/generations"]


class TestFullPipeline:
    @pytest.mark.anyio
    async def test_plan_to_artifacts_with_series_registry(self, mg):
        mock, client = mg
        report = await run_t2i_series(client, mock.admin_token, plan=_plan(), seed=42)
        assert report["passed"] is True
        assert report["consistency"]["issues"] == []

        images = report["images"]
        assert len(images) == 3
        keys = []
        for image in images:
            assert image["status"] == "completed"
            assert image["url"].startswith("/api/media-gateway/file/v1/images/jobs/")
            assert image["url"].endswith("kind=image")
            keys.append(image["idempotency_key"])
        # 每张独立幂等键
        assert len(set(keys)) == 3

        # 组图状态已登记进 MGW，可按 series_id 回查（逐张 completed + url）
        assert report["series_id"] and report["series_id"].startswith("ser_")
        series = await client.get(f"{SERIES_PATH}/{report['series_id']}", token=mock.admin_token)
        assert [item["status"] for item in series["items"]] == ["completed"] * 3
        assert all(item["url"].endswith("kind=image") for item in series["items"])
        assert ("POST", SERIES_PATH) in mock.requests

    @pytest.mark.anyio
    async def test_submit_is_idempotent_per_key(self, mg):
        mock, client = mg
        orchestrator = T2iSeriesOrchestrator(client, mock.admin_token, plan=_plan(), register=False)
        image = _plan()["images"][0]
        payload = build_generation_request(orchestrator.plan, image, seed=1)
        first = await client.post("/v1/images/generations", json=payload, token=mock.admin_token, idempotency_key="t2i:test-key")
        second = await client.post("/v1/images/generations", json=payload, token=mock.admin_token, idempotency_key="t2i:test-key")
        assert first["id"] == second["id"]
        submits = [r for r in mock.requests if r[1] == "/v1/images/generations"]
        assert len(submits) == 2  # 第二次命中幂等，不新建作业
        assert len(mock.jobs) == 1

    @pytest.mark.anyio
    async def test_qc_loop_seed_failure_then_reseed_passes(self, mg):
        """第一次生成失败（seed 归因）→ 换种子重抽 → 第 2 轮通过。"""
        mock, client = mg
        original_submit = MockMediaGateway._submit_generation

        def flaky_submit(self, request, user_id):
            response = original_submit(self, request, user_id)
            job_id = json.loads(response.content)["id"]
            if ":img2:v1:" in request.headers.get("Idempotency-Key", ""):
                self.fail_job(job_id, code="engine_error", message="seed 抖动伪影（mock）")
            return response

        MockMediaGateway._submit_generation = flaky_submit
        orchestrator = T2iSeriesOrchestrator(client, mock.admin_token, plan=_plan(), seed=7, register=False)
        try:
            report = await orchestrator.run()
        finally:
            MockMediaGateway._submit_generation = original_submit

        assert report["passed"] is True
        by_index = {img["index"]: img for img in report["images"]}
        assert by_index[2]["iterations"] == 2
        assert by_index[1]["iterations"] == 1
        # 失败轮走 seed 归因 → 换种子重抽（v2 幂等键 + 不同的种子）
        remediations = [h for h in orchestrator.history if h["phase"] == "remediation"]
        assert any(h["attribution"]["attribution"] == "seed" and h["index"] == 2 for h in remediations)
        v1_payload = next(h for h in orchestrator.history if h["phase"] == "submit" and h["index"] == 2 and h["attempt"] == 1)
        v2_payload = next(h for h in orchestrator.history if h["phase"] == "submit" and h["index"] == 2 and h["attempt"] == 2)
        assert ":v1:" in v1_payload["idempotency_key"] and ":v2:" in v2_payload["idempotency_key"]

    @pytest.mark.anyio
    async def test_max_iterations_marks_needs_human_and_continues(self, mg):
        """同一张图连败 3 轮 → 标记 needs_human，其余张照常交付，不静默丢失。"""
        mock, client = mg
        original_submit = MockMediaGateway._submit_generation

        def always_fail_img1(self, request, user_id):
            response = original_submit(self, request, user_id)
            job_id = __import__("json").loads(response.content)["id"]
            if ":img1:v" in request.headers.get("Idempotency-Key", ""):
                self.fail_job(job_id, code="engine_error", message="mock generation failure")
            return response

        MockMediaGateway._submit_generation = always_fail_img1
        try:
            report = await run_t2i_series(client, mock.admin_token, plan=_plan())
        finally:
            MockMediaGateway._submit_generation = original_submit

        assert report["passed"] is False
        by_index = {img["index"]: img for img in report["images"]}
        assert by_index[1]["status"] == "needs_human"
        assert by_index[1]["iterations"] == 3
        assert by_index[2]["status"] == "completed"
        assert by_index[3]["status"] == "completed"
        # 失败张仍登记进 MGW 账本（失败项可追溯）
        series = await client.get(f"{SERIES_PATH}/{report['series_id']}", token=mock.admin_token)
        item1 = next(i for i in series["items"] if i["index"] == 1)
        assert item1["status"] == "failed" and item1["error"]["message"]

    @pytest.mark.anyio
    async def test_series_owner_isolation(self, mg):
        mock, client = mg
        report = await run_t2i_series(client, mock.admin_token, plan=_plan())
        mock.users["foreign-user"] = {"id": "foreign-user", "name": "foreign", "display": "foreign", "role": "user", "disabled": False, "password": ""}
        other_token = "h3t_foreign-user-token"
        mock.tokens[other_token] = {"user_id": "foreign-user", "sha8": "deadbeef", "label": "test"}
        other = mock.client(token=other_token)
        try:
            listing = await other.get(SERIES_PATH, token=other_token)
            assert listing["total"] == 0
            with pytest.raises(MediaGatewayError) as exc_info:
                await other.get(f"{SERIES_PATH}/{report['series_id']}", token=other_token)
            assert exc_info.value.status == 404
        finally:
            await other.aclose()
