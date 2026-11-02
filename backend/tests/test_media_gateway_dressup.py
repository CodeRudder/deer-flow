"""dressup-tryon（换装试穿）编排层测试。

Drives :mod:`deerflow.media_gateway.dressup` — the testable orchestration behind
the ``dressup-tryon`` skill — against :class:`MockMediaGateway`'s dressup routes
(素材上传 → 台账复用（模特/服装查重，定妆照不重建）→ 建试穿 → generate 异步
作业轮询 → VLM 质检闭环（FAIL 自动 retry ≤3 次）→ 代理产物 URL), plus owner
isolation and pose-whitelist contract checks (dressup.md §2/§3.1 方案)。
"""

from __future__ import annotations

import pytest

from deerflow.media_gateway.client import MediaGatewayError
from deerflow.media_gateway.dressup import (
    DEFAULT_POSES,
    MAX_QC_RETRIES,
    DressupError,
    ensure_garment,
    ensure_model,
    find_garment,
    find_model,
    generate_tryon,
    qc_and_retry,
    run_dressup_tryon,
    upload_media,
)
from deerflow.media_gateway.mock_server import DRESSUP_MAX_RETRIES, MockMediaGateway

PNG_BYTES = b"\x89PNG-mock-person-image"
GARMENT_BYTES = b"\x89PNG-mock-garment-image"


@pytest.fixture
async def mg():
    mock = MockMediaGateway()
    client = mock.client()
    try:
        yield mock, client
    finally:
        await client.aclose()


async def _seed_media(mock: MockMediaGateway, client) -> tuple[str, str]:
    person, garment = await upload_media(client, mock.admin_token, [("person.png", PNG_BYTES), ("garment.png", GARMENT_BYTES)])
    return person["id"], garment["id"]


class TestMockContract:
    @pytest.mark.anyio
    async def test_model_create_starts_portrait_and_backfills(self, mg):
        mock, client = mg
        person_id, _ = await _seed_media(mock, client)
        model = await client.post("/v1/dressup/models", json={"name": "模特A", "desc": "20岁", "source_media_id": person_id}, token=mock.admin_token)
        assert model["id"].startswith("dm_")
        assert model["portrait_status"] == "queued"

        detail = await client.get(f"/v1/dressup/models/{model['id']}", token=mock.admin_token)
        assert detail["portrait_status"] == "done"  # mock 轮询状态机：一次 GET 即完成
        assert detail["portrait_url"].startswith("/v1/images/jobs/") and detail["portrait_url"].endswith("kind=image")

    @pytest.mark.anyio
    async def test_model_reuse_same_media_does_not_rebuild(self, mg):
        """台账复用：同 source_media_id 再建档 → 返回既有条目（定妆照不重跑）。"""
        mock, client = mg
        person_id, _ = await _seed_media(mock, client)
        first = await client.post("/v1/dressup/models", json={"name": "模特A", "source_media_id": person_id}, token=mock.admin_token)
        portrait_jobs_before = [j for j in mock.jobs.values() if "定妆照" in j["request"]["prompt"]]
        second = await client.post("/v1/dressup/models", json={"name": "模特A-改名", "source_media_id": person_id}, token=mock.admin_token)
        assert second["id"] == first["id"]
        portrait_jobs_after = [j for j in mock.jobs.values() if "定妆照" in j["request"]["prompt"]]
        assert len(portrait_jobs_after) == len(portrait_jobs_before) == 1

    @pytest.mark.anyio
    async def test_generate_requires_pose_or_pose_text(self, mg):
        mock, client = mg
        person_id, garment_id = await _seed_media(mock, client)
        model = await ensure_model(client, mock.admin_token, source_media_id=person_id, name="模特A")
        garment = await ensure_garment(client, mock.admin_token, media_id=garment_id, name="裙子")
        tryon = await client.post("/v1/dressup/tryons", json={"model_id": model["id"], "garment_id": garment["id"]}, token=mock.admin_token)
        with pytest.raises(MediaGatewayError) as exc_info:
            await client.post(f"/v1/dressup/tryons/{tryon['id']}/generate", json={"output": "image"}, token=mock.admin_token)
        assert exc_info.value.status == 400
        with pytest.raises(MediaGatewayError) as exc_info:
            await client.post(f"/v1/dressup/tryons/{tryon['id']}/generate", json={"pose": "jumping"}, token=mock.admin_token)
        assert exc_info.value.status == 400

    @pytest.mark.anyio
    async def test_owner_isolation_reads_as_404(self, mg):
        mock, client = mg
        person_id, _ = await _seed_media(mock, client)
        model = await client.post("/v1/dressup/models", json={"name": "模特A", "source_media_id": person_id}, token=mock.admin_token)
        mock.users["foreign-user"] = {"id": "foreign-user", "name": "foreign", "display": "foreign", "role": "user", "disabled": False, "password": ""}
        other_token = "h3t_foreign-user-token"
        mock.tokens[other_token] = {"user_id": "foreign-user", "sha8": "deadbeef", "label": "test"}
        other = mock.client(token=other_token)
        try:
            listing = await other.get("/v1/dressup/models", token=other_token)
            assert listing["total"] == 0
            with pytest.raises(MediaGatewayError) as exc_info:
                await other.get(f"/v1/dressup/models/{model['id']}", token=other_token)
            assert exc_info.value.status == 404
        finally:
            await other.aclose()

    @pytest.mark.anyio
    async def test_delete_entry(self, mg):
        mock, client = mg
        person_id, _ = await _seed_media(mock, client)
        model = await client.post("/v1/dressup/models", json={"name": "模特A", "source_media_id": person_id}, token=mock.admin_token)
        deleted = await client.delete(f"/v1/dressup/models/{model['id']}", token=mock.admin_token)
        assert deleted.get("deleted") is True
        with pytest.raises(MediaGatewayError):
            await client.get(f"/v1/dressup/models/{model['id']}", token=mock.admin_token)

    @pytest.mark.anyio
    async def test_retry_caps_at_three(self, mg):
        mock, client = mg
        person_id, garment_id = await _seed_media(mock, client)
        model = await ensure_model(client, mock.admin_token, source_media_id=person_id, name="模特A")
        garment = await ensure_garment(client, mock.admin_token, media_id=garment_id, name="裙子")
        tryon = await client.post("/v1/dressup/tryons", json={"model_id": model["id"], "garment_id": garment["id"]}, token=mock.admin_token)
        await client.post(f"/v1/dressup/tryons/{tryon['id']}/generate", json={"output": "image", "pose": "front"}, token=mock.admin_token)
        assert DRESSUP_MAX_RETRIES == MAX_QC_RETRIES == 3
        for _ in range(3):
            await client.post(f"/v1/dressup/tryons/{tryon['id']}/retry", json={}, token=mock.admin_token)
        with pytest.raises(MediaGatewayError) as exc_info:
            await client.post(f"/v1/dressup/tryons/{tryon['id']}/retry", json={}, token=mock.admin_token)
        assert exc_info.value.status == 409  # 真实网关语义：重试超限 409 conflict

    @pytest.mark.anyio
    async def test_qc_loop_stops_on_gateway_retry_limit_409(self, mg):
        """网关侧提前判超限（409 conflict）→ 闭环终止，pass=false + suggest 转人工。"""
        mock, client = mg
        person_id, garment_id = await _seed_media(mock, client)
        model = await ensure_model(client, mock.admin_token, source_media_id=person_id, name="模特A")
        garment = await ensure_garment(client, mock.admin_token, media_id=garment_id, name="裙子")
        tryon = await client.post("/v1/dressup/tryons", json={"model_id": model["id"], "garment_id": garment["id"]}, token=mock.admin_token)
        await generate_tryon(client, mock.admin_token, tryon_id=tryon["id"], pose="front")
        mock.script_dressup_qc(tryon["id"], [{"pass": False, "answers": {}, "missing": ["上装"], "suggest": "补：纯棉"}] * 4)
        # 模拟真实网关：仅允许 2 次重试，第 3 次直接 409（网关计数口径可严于本地）
        import deerflow.media_gateway.mock_server as ms

        original_routes = ms._dressup_routes

        def limited_routes(mg, request, user_id):
            if request.url.path.endswith("/retry") and mg._dressup_registry["tryons"][tryon["id"]]["retry_count"] >= 2:
                return ms._error(409, "conflict", "已自动重出 3 次仍不合格——请人工调整服装关键词/参考图（或换姿势、场景）后再试")
            return original_routes(mg, request, user_id)

        ms._dressup_routes = limited_routes
        try:
            qc = await qc_and_retry(client, mock.admin_token, tryon_id=tryon["id"])
        finally:
            ms._dressup_routes = original_routes
        assert qc["pass"] is False
        assert qc["retries"] == 3
        assert "人工调整" in qc["suggest"]


class TestOrchestrator:
    @pytest.mark.anyio
    async def test_full_pipeline_upload_to_qc_pass(self, mg):
        mock, client = mg
        report = await run_dressup_tryon(
            client,
            mock.admin_token,
            person_images=[("person.png", PNG_BYTES)],
            garment_images=[("garment.png", GARMENT_BYTES)],
            model_name="电商模特",
            garment_name="碎花裙",
        )
        assert report["passed"] is True
        assert report["reused"] == {"model": False, "garment": False}
        assert [img["pose"] for img in report["images"]] == list(DEFAULT_POSES)
        for image in report["images"]:
            assert image["status"] == "completed"
            assert image["qc_pass"] is True
            assert image["tryon_id"].startswith("dt_")
            assert image["outputs"][0]["url"].startswith("/api/media-gateway/file/v1/images/jobs/")
        # 素材上传走了 multipart file 字段（单数）
        assert ("POST", "/v1/media") in mock.requests

    @pytest.mark.anyio
    async def test_ledger_reuse_skips_rebuild(self, mg):
        mock, client = mg
        person_id, garment_id = await _seed_media(mock, client)
        kwargs = dict(person_media_id=person_id, garment_media_id=garment_id, model_name="模特A", garment_name="裙子")
        first = await run_dressup_tryon(client, mock.admin_token, **kwargs)
        assert first["reused"] == {"model": False, "garment": False}
        portrait_jobs_after_first = [j for j in mock.jobs.values() if "定妆照" in j["request"]["prompt"]]
        second = await run_dressup_tryon(client, mock.admin_token, **kwargs)
        assert second["reused"] == {"model": True, "garment": True}
        assert second["model_id"] == first["model_id"]
        portrait_jobs_after_second = [j for j in mock.jobs.values() if "定妆照" in j["request"]["prompt"]]
        assert len(portrait_jobs_after_second) == len(portrait_jobs_after_first)  # 定妆照不重建

    @pytest.mark.anyio
    async def test_qc_fail_auto_retry_until_pass(self, mg):
        """第 1 次 QC FAIL（带 suggest）→ 自动 retry → 第 2 次过。"""
        mock, client = mg
        person_id, garment_id = await _seed_media(mock, client)
        model = await ensure_model(client, mock.admin_token, source_media_id=person_id, name="模特A")
        garment = await ensure_garment(client, mock.admin_token, media_id=garment_id, name="裙子")
        tryon = await client.post("/v1/dressup/tryons", json={"model_id": model["id"], "garment_id": garment["id"]}, token=mock.admin_token)
        await generate_tryon(client, mock.admin_token, tryon_id=tryon["id"], pose="front")
        mock.script_dressup_qc(
            tryon["id"],
            [
                {"pass": False, "answers": {}, "missing": ["颜色"], "suggest": "补进提示词：白色拼接细节"},
                {"pass": True, "answers": {"颜色": "一致"}, "missing": [], "suggest": ""},
            ],
        )
        qc = await qc_and_retry(client, mock.admin_token, tryon_id=tryon["id"])
        assert qc["pass"] is True
        assert qc["retries"] == 1
        assert qc["status"] == "completed"
        assert qc["outputs"][0]["url"].endswith("kind=image")
        assert tryon["id"]  # 保留 tryon 引用

    @pytest.mark.anyio
    async def test_qc_fail_exhausts_retries_reports_suggest(self, mg):
        """连败 3 轮 → pass=false + suggest 留存（转人工），不再无限重试。"""
        mock, client = mg
        person_id, garment_id = await _seed_media(mock, client)
        model = await ensure_model(client, mock.admin_token, source_media_id=person_id, name="模特A")
        garment = await ensure_garment(client, mock.admin_token, media_id=garment_id, name="裙子")
        tryon = await client.post("/v1/dressup/tryons", json={"model_id": model["id"], "garment_id": garment["id"]}, token=mock.admin_token)
        await generate_tryon(client, mock.admin_token, tryon_id=tryon["id"], pose="front")
        suggest = "建议补进提示词的属性：袖口细节"
        mock.script_dressup_qc(tryon["id"], [{"pass": False, "answers": {}, "missing": ["袖口"], "suggest": suggest}] * 4)
        qc = await qc_and_retry(client, mock.admin_token, tryon_id=tryon["id"])
        assert qc["pass"] is False
        assert qc["retries"] == 3
        assert qc["suggest"] == suggest

    @pytest.mark.anyio
    async def test_pose_whitelist_enforced_upstream(self, mg):
        mock, client = mg
        with pytest.raises(DressupError):
            await run_dressup_tryon(client, mock.admin_token, person_images=[("p.png", PNG_BYTES)], garment_images=[("g.png", GARMENT_BYTES)], poses=["cartwheel"])
        assert not [r for r in mock.requests if r[1] == "/v1/dressup/tryons"]

    @pytest.mark.anyio
    async def test_custom_pose_text_flow(self, mg):
        mock, client = mg
        person_id, garment_id = await _seed_media(mock, client)
        report = await run_dressup_tryon(
            client,
            mock.admin_token,
            person_media_id=person_id,
            garment_media_id=garment_id,
            model_name="模特A",
            garment_name="裙子",
            poses=[],
            pose_texts=["坐姿回眸，手扶椅背"],
        )
        assert report["passed"] is True
        assert report["images"][0]["pose"] == "坐姿回眸，手扶椅背"

    @pytest.mark.anyio
    async def test_missing_inputs_raise_before_any_call(self, mg):
        mock, client = mg
        with pytest.raises(DressupError):
            await run_dressup_tryon(client, mock.admin_token)
        with pytest.raises(DressupError):
            await run_dressup_tryon(client, mock.admin_token, person_images=[("p.png", PNG_BYTES)], garment_images=[])
        assert not [r for r in mock.requests if r[1].startswith("/v1/dressup")]


class TestHelpers:
    @pytest.mark.anyio
    async def test_find_model_and_garment_match_by_media(self, mg):
        mock, client = mg
        person_id, garment_id = await _seed_media(mock, client)
        assert await find_model(client, mock.admin_token, source_media_id=person_id) is None
        model = await ensure_model(client, mock.admin_token, source_media_id=person_id, name="模特A")
        found = await find_model(client, mock.admin_token, source_media_id=person_id)
        assert found["id"] == model["id"]
        garment = await ensure_garment(client, mock.admin_token, media_id=garment_id, name="裙子")
        assert (await find_garment(client, mock.admin_token, media_id=garment_id))["id"] == garment["id"]
