"""video-clips（组图生视频/分镜片段）编排层测试。

Drives :mod:`deerflow.media_gateway.video_clips` — the testable orchestration
behind the ``video-clips`` skill — against :class:`MockMediaGateway`'s groupvid
media endpoints (素材入库 → plan/design → 分镜五要素 → 提交生成 → 逐镜产物),
covering workflow W3b / acceptance A2b behaviours: idempotent submission,
design-layer QC gating, per-shot artifact proxy URLs, and owner isolation.
"""

from __future__ import annotations

import pytest

from deerflow.media_gateway.client import MediaGatewayError
from deerflow.media_gateway.mock_server import MockMediaGateway
from deerflow.media_gateway.video_clips import (
    SidecarCursor,
    VideoClipsError,
    apply_shot_designs,
    build_shot_patch,
    ingest_materials,
    qc_check_shot,
    qc_check_storyboard,
    read_storyboard,
    run_video_clips,
    submit_generation,
)

PNG_BYTES = b"\x89PNG-mock-shot-image"


@pytest.fixture
async def mg():
    mock = MockMediaGateway()
    client = mock.client()
    try:
        yield mock, client
    finally:
        await client.aclose()


@pytest.fixture
def material_files(tmp_path):
    paths = []
    for i in range(1, 4):
        p = tmp_path / f"ref_{i}.png"
        p.write_bytes(PNG_BYTES)
        paths.append(p)
    return paths


def _designs() -> dict[int, dict]:
    return {
        1: {
            "action": "人物从画面左侧走入，停在桌前",
            "expression": "神情专注，眉头微蹙",
            "dialogue": "（低声）就是这里了。",
            "scene": "缓推，室内暖光，桌上的地图入画",
            "pacing": 5,
            "refs": [1, 2],
        },
        2: {
            "action": "人物伸手拿起地图，展开",
            "expression": "惊讶后转为笃定",
            "dialogue": "（提高音量）出发！",
            "scene": "固定机位，特写手部动作",
            "pacing": 1,
            "refs": [3],
        },
        3: {
            "action": "人物转身走向门口，背影定格",
            "expression": "嘴角上扬",
            "dialogue": "（自语）等我的好消息。",
            "scene": "横移跟随，门口逆光",
            "pacing": 5,
            "refs": [2],
        },
    }


class TestFiveElementMapping:
    def test_build_shot_patch_maps_five_elements(self):
        patch = build_shot_patch(_designs()[1])
        assert "人物从画面左侧走入" in patch["action"]
        assert "表情：神情专注" in patch["action"]
        assert "台词：（低声）就是这里了。" in patch["action"]
        assert patch["camera"] == "缓推，室内暖光，桌上的地图入画"
        assert patch["duration_tier"] == 5
        assert patch["refs"] == [1, 2]

    def test_invalid_pacing_tier_is_dropped(self):
        patch = build_shot_patch({"action": "走位", "expression": "微笑", "dialogue": "你好", "scene": "固定", "pacing": 7})
        assert "duration_tier" not in patch

    def test_qc_flags_missing_elements(self):
        issues = qc_check_shot({"index": 2, "action": "只写了动作", "camera": "固定机位", "refs": [1]})
        assert any("表情" in i for i in issues)
        assert any("对话" in i for i in issues)

    def test_qc_flags_bad_refs_and_empty_board(self):
        assert any("越界" in i for i in qc_check_shot({"index": 1, "action": "a 表情：b 台词：c", "camera": "x", "refs": [9]}, image_count=3))
        assert any("refs" in i for i in qc_check_shot({"index": 1, "action": "a 表情：b 台词：c", "camera": "x", "refs": []}))
        assert qc_check_storyboard({}) == ["分镜方案为空"]


class TestSidecarCursor:
    def test_round_trip(self):
        cursor = SidecarCursor(media_id="med_abc", stage="submitted", shot_index=3, job_ids=["ref2va_1", "ref2va_2"])
        restored = SidecarCursor.from_json(cursor.to_json())
        assert restored == cursor


class TestFullPipeline:
    @pytest.mark.anyio
    async def test_material_ingest_to_artifacts(self, mg, material_files):
        mock, client = mg
        report = await run_video_clips(client, mock.admin_token, files=material_files, designs=_designs(), shot_count=3, seed=42)
        media_id = report["media_id"]
        assert media_id.startswith("med_")

        # 素材入库 + 分析 + 分镜设计 + 逐镜 PATCH 都打到 MGW
        assert ("POST", "/v1/media") in mock.requests
        assert ("POST", f"/v1/media/{media_id}/storyboard") in mock.requests
        assert ("PATCH", f"/v1/media/{media_id}/storyboard/shots/1") in mock.requests

        # 五要素已落 MGW（补丁内容回读可见）
        storyboard = await read_storyboard(client, mock.admin_token, media_id)
        assert "台词：（低声）就是这里了。" in storyboard["shots"][0]["action"]
        assert storyboard["shots"][0]["edited"] is True

        # 逐镜产物：每个提交作业都有 deer-flow 代理产物 URL
        artifacts = report["artifacts"]
        assert len(artifacts) == 3
        for artifact in artifacts:
            assert artifact["status"] == "completed"
            assert artifact["file_url"].startswith("/api/media-gateway/file/v1/images/jobs/")
            assert artifact["file_url"].endswith("kind=video")

    @pytest.mark.anyio
    async def test_submit_is_idempotent_per_key(self, mg, material_files):
        mock, client = mg
        media = await ingest_materials(client, mock.admin_token, material_files)
        media_id = media["id"]
        from deerflow.media_gateway.video_clips import draft_storyboard, ensure_analysis

        await ensure_analysis(client, mock.admin_token, media_id)
        await draft_storyboard(client, mock.admin_token, media_id, shot_count=2)
        await apply_shot_designs(client, mock.admin_token, media_id, {i: d for i, d in _designs().items() if i <= 2})

        first = await submit_generation(client, mock.admin_token, media_id, seed=7, idempotency_key="groupvid:test-key")
        second = await submit_generation(client, mock.admin_token, media_id, seed=7, idempotency_key="groupvid:test-key")
        assert [j["job_id"] for j in first["jobs"]] == [j["job_id"] for j in second["jobs"]]

        with pytest.raises(MediaGatewayError) as exc_info:
            await submit_generation(client, mock.admin_token, media_id, seed=8, idempotency_key="groupvid:test-key")
        assert exc_info.value.status == 409

    @pytest.mark.anyio
    async def test_design_layer_qc_blocks_submission(self, mg, material_files):
        mock, client = mg
        # 镜 2 缺对话/表情要素 → 提交前被拦截，零 GPU 消耗
        bad_designs = _designs()
        bad_designs[2] = {"action": "人物伸手拿起地图", "scene": "固定机位", "refs": [3]}
        with pytest.raises(VideoClipsError) as exc_info:
            await run_video_clips(client, mock.admin_token, files=material_files, designs=bad_designs, shot_count=3)
        assert any("第 2 镜" in issue for issue in exc_info.value.issues)
        generate_calls = [r for r in mock.requests if r[1].endswith("/generate") and r[0] == "POST"]
        assert not generate_calls

    @pytest.mark.anyio
    async def test_resume_from_mgw_state(self, mg, material_files):
        """中断续作：不传 designs 重建上下文时，MGW 现行方案可直接重读。"""
        mock, client = mg
        media = await ingest_materials(client, mock.admin_token, material_files)
        from deerflow.media_gateway.video_clips import draft_storyboard, ensure_analysis

        await ensure_analysis(client, mock.admin_token, media["id"])
        await draft_storyboard(client, mock.admin_token, media["id"], shot_count=3, notes="生活流叙事")
        storyboard = await read_storyboard(client, mock.admin_token, media["id"])
        assert storyboard["params"]["notes"] == "生活流叙事"
        assert len(storyboard["shots"]) == 3

    @pytest.mark.anyio
    async def test_foreign_media_hidden(self, mg, material_files):
        mock, client = mg
        media = await ingest_materials(client, mock.admin_token, material_files)
        other_token = "h3t_foreign-user-token"
        mock.users["foreign-user"] = {"id": "foreign-user", "name": "foreign", "display": "foreign", "role": "user", "disabled": False, "password": ""}
        mock.tokens[other_token] = {"user_id": "foreign-user", "sha8": "deadbeef", "label": "test"}
        other = mock.client(token=other_token)
        try:
            with pytest.raises(MediaGatewayError) as exc_info:
                await other.get(f"/v1/media/{media['id']}")
            assert exc_info.value.status == 404
        finally:
            await other.aclose()
