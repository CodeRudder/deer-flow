"""Tests for the drama QC iteration closed loop (qc.py) and the drama mock.

Pure-function layers (checklist, attribution, remediation) are unit-tested
directly; the orchestrator and the drama mock endpoints are driven end to end
through the real ``MediaGatewayClient`` against ``MockMediaGateway`` —
including the acceptance anchor "失败 → 修正 → 通过" full loop.
"""

from __future__ import annotations

import pytest

from deerflow.media_gateway.client import MediaGatewayError
from deerflow.media_gateway.mock_server import MockMediaGateway
from deerflow.media_gateway.qc import (
    ACTION_FIX_CITATIONS,
    ACTION_NEEDS_HUMAN,
    ACTION_RESEED,
    ACTION_REVISE_PROMPT,
    ATTR_CITATION,
    ATTR_PROMPT,
    ATTR_SEED,
    DEFAULT_FIVE_ELEMENT_CHECKLIST,
    DramaQCClosedLoop,
    attribute_qc,
    check_five_elements,
    design_verdict,
    parse_drama_error,
    remediation_plan,
)

PASS_QC = {
    "state": "qc_pass",
    "scores": {"identity": 0.95, "ball": None, "seam": None, "motion": 0.9, "space": 0.9},
    "issues": [],
    "must_fix_storyboard": False,
}
FAIL_QC_CITATION = {
    "state": "qc_sl",
    "scores": {"identity": 0.4, "ball": None, "seam": None, "motion": None, "space": None},
    "issues": [{"level": "S", "desc": "画面引用的 <Picture 2> 角色与托盘不符"}],
    "must_fix_storyboard": True,
}
FAIL_QC_PROMPT = {
    "state": "qc_sl",
    "scores": {"identity": 0.3, "ball": None, "seam": None, "motion": 0.9, "space": 0.5},
    "issues": [{"level": "S", "desc": "人物脸与角色卡不一致"}],
    "must_fix_storyboard": True,
}
FAIL_QC_SEED = {
    "state": "qc_ml",
    "scores": {"identity": 0.9, "ball": None, "seam": None, "motion": 0.5, "space": 0.9},
    "issues": [{"level": "M", "desc": "水面闪烁伪影，重抽可解"}],
    "must_fix_storyboard": False,
}


def _good_shot(**overrides):
    shot = {
        "id": "sh_1",
        "prompt_en": "Chen Fan crouches at the fire basin",
        "prompt_zh": "陈凡蹲在火盆旁，皱眉凝视夜色，手按<Picture 1>石桌",
        "dialogue": [{"speaker": "陈凡", "text": "又天黑了"}],
        "camera": "缓慢推近",
        "size": "中景",
        "duration_s": 4.0,
        "tray_tokens_used": [1],
        "scene_id": None,
        "time": None,
    }
    shot.update(overrides)
    return shot


CLEAN_PREVIEW = {
    "prompt": "…",
    "checks": {"negative_wording": "pass", "negative_hits": [], "token_manifest_match": True, "violations": [], "unused_tray_refs": [], "scene_slot_missing": False, "scene_slot_absent": False, "scene_link_dangling": False, "warnings": []},
    "valid": True,
}


@pytest.fixture
async def mg():
    mock = MockMediaGateway()
    client = mock.client()
    try:
        yield mock, client
    finally:
        await client.aclose()


async def _build_clip(client, *, shot_overrides: dict | None = None, tray=None):
    """drama → chapter → storyline → plot → clip → one shot; returns (drama_id, chapter_id, clip_id)."""
    drama = await client.post("/v1/drama", json={"title": "永夜"})
    did = drama["id"]
    chapter = await client.post(f"/v1/drama/{did}/chapters", json={"title": "第一章", "idx": 1})
    ch = chapter["id"]
    storyline = await client.post(f"/v1/drama/{did}/chapters/{ch}/storylines", json={"name": "主线", "order": 1})
    plot = await client.post(f"/v1/drama/{did}/chapters/{ch}/plots", json={"storyline_id": storyline["id"], "name": "开场", "order": 1})
    clip = await client.post(
        f"/v1/drama/{did}/chapters/{ch}/clips",
        json={
            "plot_id": plot["id"],
            "order": 1,
            "budget_tier": 5,
            "prompt_opening": "夜色木屋营地",
            "ref_tray": tray or [{"media_id": "med_scene1", "kind": "scene", "name": "营地"}, {"media_id": "med_cf1", "kind": "character", "name": "陈凡"}],
        },
    )
    cl = clip["id"]
    body = {
        "clip_id": cl,
        "order": 1,
        "duration_s": 4,
        "prompt_en": "Chen Fan crouches at the fire basin, frowning",
        "prompt_zh": "陈凡蹲在火盆旁，皱眉凝视夜色，手按<Picture 1>石桌",
        "dialogue": [{"speaker": "陈凡", "text": "又天黑了"}],
        "camera": "缓慢推近",
        "size": "中景",
    }
    body.update(shot_overrides or {})
    await client.post(f"/v1/drama/{did}/chapters/{ch}/shots", json=body)
    return did, ch, cl


class TestFiveElementChecklist:
    def test_complete_shot_passes_all_elements(self):
        assert check_five_elements(_good_shot()) == []

    def test_empty_prompt_blocks(self):
        issues = check_five_elements(_good_shot(prompt_zh="", prompt_en=""))
        assert "action" in [i.element for i in issues]
        assert next(i for i in issues if i.element == "action").severity == "block"

    def test_missing_speaker_blocks(self):
        issues = check_five_elements(_good_shot(dialogue=[{"text": "…"}]))
        assert [i.element for i in issues] == ["dialogue"]

    def test_expression_is_warn_only(self):
        issues = check_five_elements(_good_shot(prompt_zh="陈凡蹲在火盆旁"))
        assert [i.severity for i in issues] == ["warn"]
        assert issues[0].element == "expression"

    def test_zero_duration_blocks(self):
        issues = check_five_elements(_good_shot(duration_s=0))
        assert "rhythm" in [i.element for i in issues]

    def test_custom_checklist_overrides_default(self):
        issues = check_five_elements(_good_shot(), checklist=[("custom", lambda s: None)])
        assert issues == []

    def test_checklist_is_configurable_per_element(self):
        strict_expression = [("expression", lambda s: None if s.get("expression") else __import__("deerflow").media_gateway.qc.DesignIssue("expression", "sh", "block", "explicit emotion field required"))]
        issues = check_five_elements(_good_shot(), checklist=strict_expression)
        assert [i.severity for i in issues] == ["block"]

    def test_design_verdict_block_vs_warn(self):
        assert design_verdict(CLEAN_PREVIEW, [_good_shot()])["passed"] is True
        verdict = design_verdict(CLEAN_PREVIEW, [_good_shot(prompt_zh="", prompt_en="")])
        assert verdict["passed"] is False
        assert any(issue["severity"] == "block" for issue in verdict["issues"])

    def test_preview_violations_fail_the_gate(self):
        preview = {
            "prompt": "…",
            "checks": {"token_manifest_match": False, "violations": [{"shot_id": "sh_1", "token": "<Picture 9>"}], "negative_wording": "pass", "negative_hits": []},
            "valid": False,
        }
        verdict = design_verdict(preview, [])
        assert verdict["passed"] is False
        assert verdict["issues"][0]["element"] == "citation"


class TestAttribution:
    def test_citation_wins_over_everything(self):
        attribution = attribute_qc(FAIL_QC_CITATION)
        assert attribution["attribution"] == ATTR_CITATION
        assert attribution["reasons"]

    def test_prompt_on_storyboard_fix_and_low_identity(self):
        assert attribute_qc(FAIL_QC_PROMPT)["attribution"] == ATTR_PROMPT

    def test_seed_on_stochastic_markers(self):
        assert attribute_qc(FAIL_QC_SEED)["attribution"] == ATTR_SEED

    def test_fallback_is_prompt(self):
        qc = {"state": "qc_ml", "scores": {}, "issues": [{"level": "L", "desc": "色彩偏差"}], "must_fix_storyboard": False}
        assert attribute_qc(qc)["attribution"] == ATTR_PROMPT

    def test_no_issues_still_attributed(self):
        assert attribute_qc({"state": "qc_ml", "scores": {}, "issues": []})["attribution"] == ATTR_PROMPT


class TestRemediationPlan:
    def test_each_attribution_maps_to_an_action(self):
        assert remediation_plan({"attribution": ATTR_PROMPT}, attempt=1)["action"] == ACTION_REVISE_PROMPT
        assert remediation_plan({"attribution": ATTR_CITATION}, attempt=1)["action"] == ACTION_FIX_CITATIONS
        assert remediation_plan({"attribution": ATTR_SEED}, attempt=1)["action"] == ACTION_RESEED

    def test_retry_budget_is_configurable(self):
        within = remediation_plan({"attribution": ATTR_PROMPT}, attempt=2, max_iterations=3)
        assert within["action"] == ACTION_REVISE_PROMPT
        exhausted = remediation_plan({"attribution": ATTR_PROMPT}, attempt=3, max_iterations=2)
        assert exhausted["action"] == ACTION_NEEDS_HUMAN

    def test_needs_human_carries_reason(self):
        plan = remediation_plan({"attribution": ATTR_SEED}, attempt=4, max_iterations=3)
        assert plan["action"] == ACTION_NEEDS_HUMAN
        assert "3" in plan["reason"]


class TestDramaErrorParsing:
    @pytest.mark.anyio
    async def test_structured_detail_error_is_parsed(self, mg):
        _, client = mg
        with pytest.raises(MediaGatewayError) as exc_info:
            await client.post("/v1/drama", json={"title": ""})
        parsed = parse_drama_error(exc_info.value)
        assert parsed == {}  # plain-string/empty drama errors parse to {}

    @pytest.mark.anyio
    async def test_shot_save_chain_error_code(self, mg):
        mock, client = mg
        did, ch, cl = await _build_clip(client)
        with pytest.raises(MediaGatewayError) as exc_info:
            await client.post(f"/v1/drama/{did}/chapters/{ch}/shots", json={"clip_id": cl, "order": 2, "duration_s": 1, "prompt_en": "x", "prompt_zh": "不要让球出画"})
        parsed = parse_drama_error(exc_info.value)
        assert parsed["code"] == "negative_wording"
        assert parsed["violations"][0]["match"] == "不要"
        assert ("POST", f"/v1/drama/{did}/chapters/{ch}/shots") in mock.requests


class TestDramaMockContract:
    @pytest.mark.anyio
    async def test_structure_creation_and_owner_listing(self, mg):
        _, client = mg
        did, ch, cl = await _build_clip(client)
        drama = await client.get(f"/v1/drama/{did}")
        assert drama["title"] == "永夜"
        listed = await client.get("/v1/drama")
        assert [d["id"] for d in listed["items"]] == [did]
        assert cl.startswith("cl_")

    @pytest.mark.anyio
    async def test_duplicate_chapter_idx_rejected(self, mg):
        _, client = mg
        did, _, _ = await _build_clip(client)
        with pytest.raises(MediaGatewayError) as exc_info:
            await client.post(f"/v1/drama/{did}/chapters", json={"title": "重复", "idx": 1})
        assert exc_info.value.status == 400
        assert "章节序号 1 已存在" in exc_info.value.message

    @pytest.mark.anyio
    async def test_dialogue_unassigned_and_en_empty_checks(self, mg):
        _, client = mg
        did, ch, cl = await _build_clip(client)
        with pytest.raises(MediaGatewayError) as exc_info:
            await client.post(f"/v1/drama/{did}/chapters/{ch}/shots", json={"clip_id": cl, "order": 2, "duration_s": 1, "prompt_en": "ok", "prompt_zh": "正文", "dialogue": [{"text": "…"}]})
        assert parse_drama_error(exc_info.value)["code"] == "dialogue_unassigned"
        with pytest.raises(MediaGatewayError) as exc_info:
            await client.post(f"/v1/drama/{did}/chapters/{ch}/shots", json={"clip_id": cl, "order": 2, "duration_s": 1, "prompt_en": "", "prompt_zh": "正文"})
        assert parse_drama_error(exc_info.value)["code"] == "prompt_en_empty"

    @pytest.mark.anyio
    async def test_clip_over_budget_enforced_after_save(self, mg):
        _, client = mg
        did, ch, cl = await _build_clip(client)
        with pytest.raises(MediaGatewayError) as exc_info:
            await client.post(f"/v1/drama/{did}/chapters/{ch}/shots", json={"clip_id": cl, "order": 2, "duration_s": 4, "prompt_en": "extra shot", "prompt_zh": "加一镜顶满 5s 档"})
        parsed = parse_drama_error(exc_info.value)
        assert parsed["code"] == "clip_over_budget"
        assert parsed["budget_tier"] == 5

    @pytest.mark.anyio
    async def test_budget_endpoint_reports_nearest_tier(self, mg):
        _, client = mg
        did, ch, cl = await _build_clip(client)
        body = await client.post(f"/v1/drama/{did}/chapters/{ch}/clips/{cl}/budget")
        assert body == {"tier": 5, "used_s": 4.0, "ok": True, "nearest_tier": 5}

    @pytest.mark.anyio
    async def test_prompt_preview_valid_and_invalid_tokens(self, mg):
        _, client = mg
        did, ch, cl = await _build_clip(client)
        preview = await client.get(f"/v1/drama/{did}/chapters/{ch}/clips/{cl}/prompt_preview")
        assert preview["valid"] is True
        assert preview["checks"]["token_manifest_match"] is True
        assert [p["n"] for p in preview["pictures"]] == [1, 2]
        assert "[Shot 1 · 4]" in preview["prompt"]

        # Out-of-range token: valid=false, zero GPU, never submitted.
        await client.patch(
            f"/v1/drama/{did}/chapters/{ch}/clips/{cl}",
            json={"ref_tray": [{"media_id": "med_cf1", "kind": "character", "name": "陈凡"}]},
        )
        preview = await client.get(f"/v1/drama/{did}/chapters/{ch}/clips/{cl}/prompt_preview")
        # Contract §16: the removed tray entry's token is silently stripped
        # from every shot (remap is silent, no error).
        assert preview["valid"] is True
        shots = (await client.get(f"/v1/drama/{did}/chapters/{ch}/shots", params={"clip_id": cl}))["items"]
        assert "手按石桌" in shots[0]["prompt_zh"]
        assert "<Picture" not in shots[0]["prompt_zh"]
        assert preview["checks"]["unused_tray_refs"] == ["陈凡"]

    @pytest.mark.anyio
    async def test_single_job_in_flight_and_archive_gate(self, mg):
        mock, client = mg
        did, ch, cl = await _build_clip(client)
        first = await client.post(f"/v1/drama/{did}/chapters/{ch}/clips/{cl}/generate")
        assert first["state"] == "generating"

        # A second clip may not start while the first job is in flight.
        storyline = (await client.get(f"/v1/drama/{did}/chapters/{ch}/storylines"))["items"][0]
        plot2 = await client.post(f"/v1/drama/{did}/chapters/{ch}/plots", json={"storyline_id": storyline["id"], "name": "开场二", "order": 2})
        clip2 = await client.post(f"/v1/drama/{did}/chapters/{ch}/clips", json={"plot_id": plot2["id"], "order": 1, "budget_tier": 5, "prompt_opening": "夜色营地"})
        cl2 = clip2["id"]
        await client.post(f"/v1/drama/{did}/chapters/{ch}/shots", json={"clip_id": cl2, "order": 1, "duration_s": 4, "prompt_en": "Wide shot of the camp", "prompt_zh": "营地全景"})
        with pytest.raises(MediaGatewayError) as exc_info:
            await client.post(f"/v1/drama/{did}/chapters/{ch}/clips/{cl2}/generate")
        assert parse_drama_error(exc_info.value)["code"] == "single_job_in_flight"

        mock.qc_script[cl] = []
        await client.get(f"/v1/drama/{did}/chapters/{ch}/clips/{cl}/qc")
        # qc_pass then resubmit without archiving → 409 previous_version_not_archived.
        with pytest.raises(MediaGatewayError) as exc_info:
            await client.post(f"/v1/drama/{did}/chapters/{ch}/clips/{cl}/generate")
        parsed = parse_drama_error(exc_info.value)
        assert parsed["code"] == "previous_version_not_archived"
        archived = await client.post(f"/v1/drama/{did}/chapters/{ch}/clips/{cl}/archive", json={"version": 1, "reason": "重出前归档"})
        assert archived["version"] == 1
        resubmitted = await client.post(f"/v1/drama/{did}/chapters/{ch}/clips/{cl}/generate")
        assert resubmitted["state"] == "generating"

    @pytest.mark.anyio
    async def test_partition_preview_commit_episodes(self, mg):
        _, client = mg
        did, ch, cl = await _build_clip(client)
        preview = await client.get(f"/v1/drama/{did}/chapters/{ch}/partition/preview", params={"target_episode_s": "60"})
        assert preview["episodes"][0]["clip_ids"] == [cl]
        committed = await client.post(f"/v1/drama/{did}/chapters/{ch}/partition/commit", json={"target_episode_s": 60})
        assert committed == {"episodes": 1, "unchanged_from": 1}
        episodes = await client.get(f"/v1/drama/{did}/chapters/{ch}/episodes")
        assert episodes["items"][0]["id"] == "ep01"

    @pytest.mark.anyio
    async def test_foreign_drama_is_404(self, mg):
        mock, client = mg
        did, _, _ = await _build_clip(client)
        # Provision a second real user so the request passes token auth and
        # reaches the owner check (contract: cross-owner reads as 404).
        await client.post("/v1/users", json={"username": "alice", "password": "pw", "role": "user"})
        login = await client.post("/v1/auth/login", json={"username": "alice", "password": "pw"})
        token = login["token"]
        other = mock.client(token=token)
        try:
            with pytest.raises(MediaGatewayError) as exc_info:
                await other.get(f"/v1/drama/{did}")
            assert exc_info.value.status == 404
            listed = await other.get("/v1/drama")
            assert listed["items"] == []
        finally:
            await other.aclose()


class TestClosedLoop:
    @pytest.mark.anyio
    async def test_design_gate_blocks_before_any_generation(self, mg):
        mock, client = mg
        did, ch, cl = await _build_clip(client, shot_overrides={"prompt_en": "不要让球出画", "prompt_zh": "不要让球出画", "force": True})
        loop = DramaQCClosedLoop(client, drama_id=did, chapter_id=ch, clip_id=cl)

        async def fail_if_called(plan, qc):
            raise AssertionError("fix_fn must not run when the design gate fails")

        result = await loop.run(fail_if_called)
        assert result["status"] == "design_failed"
        elements = {issue["element"] for issue in result["issues"]}
        assert "prompt" in elements  # negative wording from the preview checks
        assert not any(path.endswith("/generate") for _, path in mock.requests)

    @pytest.mark.anyio
    async def test_full_loop_failure_then_fix_then_pass(self, mg):
        """Acceptance A2b anchor: QC 失败 → 归因（引用错）→ 修正 → 通过."""
        mock, client = mg
        did, ch, cl = await _build_clip(client)
        mock.script_qc(cl, [FAIL_QC_CITATION, PASS_QC])
        loop = DramaQCClosedLoop(client, drama_id=did, chapter_id=ch, clip_id=cl)
        applied: list[dict] = []

        async def fix_fn(plan, qc):
            assert plan["action"] == ACTION_FIX_CITATIONS
            assert plan["attempt"] == 1
            # Real remediation: rewrite the shot design fields (五要素修正).
            shots = (await client.get(f"/v1/drama/{did}/chapters/{ch}/shots", params={"clip_id": cl}))["items"]
            await client.patch(f"/v1/drama/{did}/chapters/{ch}/shots/{shots[0]['id']}", json={"prompt_zh": "陈凡蹲在<Picture 1>营地，抬头望向<Picture 2>的陈凡", "force": True})
            applied.append(plan)
            return {"patched_shot": shots[0]["id"]}

        result = await loop.run(fix_fn)
        assert result["status"] == "passed"
        assert result["iterations"] == 2
        assert len(applied) == 1

        # History is a traceable dict log: design → qc → remediation → fix → design → qc.
        phases = [record["phase"] for record in loop.history]
        assert phases == ["design", "qc", "remediation", "fix", "design", "qc"]
        qc_records = [r for r in loop.history if r["phase"] == "qc"]
        assert qc_records[0]["attribution"]["attribution"] == ATTR_CITATION
        assert qc_records[1]["qc"]["state"] == "qc_pass"
        # The archive gate ran before the retry (S 级须先归档旧版本).
        clip = await client.get(f"/v1/drama/{did}/chapters/{ch}/clips/{cl}")
        assert clip["archives"] and clip["archives"][0]["version"] == 1

    @pytest.mark.anyio
    async def test_reseed_path_passes_without_shot_changes(self, mg):
        mock, client = mg
        did, ch, cl = await _build_clip(client)
        mock.script_qc(cl, [FAIL_QC_SEED, PASS_QC])
        loop = DramaQCClosedLoop(client, drama_id=did, chapter_id=ch, clip_id=cl)

        async def fix_fn(plan, qc):
            assert plan["action"] == ACTION_RESEED
            return {"seed": "next"}

        result = await loop.run(fix_fn)
        assert result["status"] == "passed"
        # Contract §15.1 ③: even a reseed retry archives the previous version.
        clip = await client.get(f"/v1/drama/{did}/chapters/{ch}/clips/{cl}")
        assert clip["archives"][0]["reason"].endswith(ATTR_SEED)

    @pytest.mark.anyio
    async def test_retry_exhaustion_returns_needs_human(self, mg):
        mock, client = mg
        did, ch, cl = await _build_clip(client)
        mock.script_qc(cl, [FAIL_QC_PROMPT, FAIL_QC_PROMPT])
        loop = DramaQCClosedLoop(client, drama_id=did, chapter_id=ch, clip_id=cl, max_iterations=2)

        async def fix_fn(plan, qc):
            return {"patched": True}

        result = await loop.run(fix_fn)
        assert result["status"] == "needs_human"
        assert result["iterations"] == 2
        plans = [r["plan"] for r in loop.history if r["phase"] == "remediation"]
        assert plans[-1]["action"] == ACTION_NEEDS_HUMAN
        # Every iteration recorded its QC body + attribution (可追溯).
        assert [r["attempt"] for r in loop.history if r["phase"] == "qc"] == [1, 2]

    @pytest.mark.anyio
    async def test_default_checklist_passed_through_to_loop(self, mg):
        _, client = mg
        did, ch, cl = await _build_clip(client)
        loop = DramaQCClosedLoop(client, drama_id=did, chapter_id=ch, clip_id=cl, checklist=DEFAULT_FIVE_ELEMENT_CHECKLIST)
        verdict = await loop.check_design()
        assert verdict["passed"] is True
        assert loop.history[0]["phase"] == "design"
