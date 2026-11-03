"""shot-plan（参考视频 → 分镜制作包）编排层测试。

Drives :mod:`deerflow.media_gateway.shot_plan` — the testable orchestration
behind the ``video-clips`` / ``drama-production`` "参考视频模式" branch —
against :class:`MockMediaGateway`'s ``/v1/media/{id}/plan`` endpoints,
covering: make/get/estimate round-trips (estimate never persists), the
plan→五要素 1:1 mapping (template-v2 line prefixes → action/scene/pacing),
split-shot warning propagation, and owner isolation.
"""

from __future__ import annotations

import pytest

from deerflow.media_gateway.client import MediaGatewayError
from deerflow.media_gateway.mock_server import MockMediaGateway
from deerflow.media_gateway.shot_plan import (
    estimate_plan,
    get_plan,
    make_plan,
    plan_shots_to_storyboard,
)

MP4_BYTES = b"\x00\x00\x00\x18ftypmock-mp4-bytes"


@pytest.fixture
async def mg():
    mock = MockMediaGateway()
    client = mock.client()
    try:
        yield mock, client
    finally:
        await client.aclose()


async def _ingest_video(mock, client, *, duration: float | None = None) -> str:
    resp = await client.post_multipart(
        "/v1/media",
        token=mock.admin_token,
        files=[("file", ("ref.mp4", MP4_BYTES, "application/octet-stream"))],
        fields={"title": "参考片"},
    )
    media_id = resp["id"]
    if duration is not None:
        mock.media[media_id]["probe"] = {"duration_s": duration}
    return media_id


# ── 封装调用 ──────────────────────────────────────────────────────────
@pytest.mark.anyio
async def test_make_plan_persists_and_get_roundtrips(mg):
    mock, client = mg
    media_id = await _ingest_video(mock, client, duration=12.0)

    plan = await make_plan(client, mock.admin_token, media_id, aspect="9:16", short_edge=768)
    assert plan["media_id"] == media_id
    assert plan["template_version"] == "shot-prompt-v2"
    assert plan["target"] == {"aspect": "9:16", "short_edge": 768, "duration_policy": "up"}
    assert plan["source"]["duration_s"] == 12.0

    again = await get_plan(client, mock.admin_token, media_id)
    assert again == plan  # 落盘后 GET 逐字节一致（确定性）

    detail = await client.get(f"/v1/media/{media_id}", token=mock.admin_token)
    assert detail["has_plan"] is True


@pytest.mark.anyio
async def test_make_plan_is_deterministic(mg):
    mock, client = mg
    media_id = await _ingest_video(mock, client, duration=12.0)
    first = await make_plan(client, mock.admin_token, media_id)
    mock.media[media_id]["plan"] = None
    second = await make_plan(client, mock.admin_token, media_id)
    assert first == second


@pytest.mark.anyio
async def test_estimate_does_not_persist(mg):
    mock, client = mg
    media_id = await _ingest_video(mock, client, duration=12.0)

    estimate = await estimate_plan(client, mock.admin_token, media_id, aspect="9:16")
    assert estimate["shot_count"] >= 1
    assert estimate["gpu_s"] > 0

    # estimate 只读不落盘：之后 GET plan 仍 404
    with pytest.raises(MediaGatewayError) as exc:
        await get_plan(client, mock.admin_token, media_id)
    assert exc.value.status == 404


@pytest.mark.anyio
async def test_plan_requires_video_kind(mg):
    mock, client = mg
    resp = await client.post_multipart(
        "/v1/media",
        token=mock.admin_token,
        files=[("file", ("ref.png", b"\x89PNG", "application/octet-stream"))],
        fields={"title": "组图"},
    )
    with pytest.raises(MediaGatewayError) as exc:
        await make_plan(client, mock.admin_token, resp["id"])
    assert exc.value.status == 400


@pytest.mark.anyio
async def test_plan_owner_isolation(mg):
    mock, client = mg
    media_id = await _ingest_video(mock, client, duration=12.0)
    foreign_token = "h3t_foreign-plan-token"
    mock.users["foreign-user"] = {"id": "foreign-user", "name": "foreign", "display": "foreign", "role": "user", "disabled": False, "password": ""}
    mock.tokens[foreign_token] = {"user_id": "foreign-user", "sha8": "deadbe11", "label": "test"}
    async with mock.client(token=foreign_token) as foreign:
        with pytest.raises(MediaGatewayError) as exc:
            await make_plan(foreign, foreign_token, media_id)
        assert exc.value.status == 404


# ── 拆镜告警 ──────────────────────────────────────────────────────────
@pytest.mark.anyio
async def test_plan_split_warning_for_long_shots(mg):
    mock, client = mg
    media_id = await _ingest_video(mock, client, duration=40.0)  # 18+8+8+6
    plan = await make_plan(client, mock.admin_token, media_id)
    long_shots = [s for s in plan["shots"] if s["source"]["end_s"] - s["source"]["start_s"] > 15]
    assert long_shots, "40s 切镜应含一个 >15 s 段"
    assert any("需要拆镜" in w for w in plan["warnings"])
    storyboard = plan_shots_to_storyboard(plan)
    assert storyboard["feasible"] is False
    assert any("需要拆镜" in w for w in storyboard["warnings"])


# ── plan → 五要素映射 ─────────────────────────────────────────────────
def _sample_plan() -> dict:
    return {
        "version": 1,
        "template_version": "shot-prompt-v2",
        "media_id": "med_x",
        "source": {"duration_s": 16.0, "shot_count": 2},
        "target": {"aspect": "9:16", "short_edge": 768, "duration_policy": "up"},
        "style": {"look": "暖调", "lighting": "自然光", "setting": "城市街道", "shot_size": "中景"},
        "shots": [
            {
                "index": 1,
                "source": {"start_s": 0.0, "end_s": 8.0},
                "mode": "t2va",
                "mode_name": "h3-clip-std",
                "duration_s": 10,
                "aspect": "9:16",
                "short_edge": 768,
                "prompt": ("[Shot 1] 中景，平视，缓推；自然光，暖调。\n主体：人物位于画面中央，向前行走。\n动作节奏：起步：迈步向前；收势：站定。\n场景：城市街道，两旁店铺入画。\n画面方向：自左向右。\n视线：看向镜头。"),
                "keyframe": "frames/1.jpg",
                "refs": [],
                "feasibility": {"ok": True, "warnings": ["时长 8.00s 向上取到 10s 档"], "confidence": 0.8},
                "estimate": {"gpu_s": 50.0, "wall_s": 50.0, "basis": "h3-clip-std"},
            },
            {
                "index": 2,
                "source": {"start_s": 8.0, "end_s": 16.0},
                "mode": "t2va",
                "mode_name": None,
                "duration_s": 15,
                "aspect": "9:16",
                "short_edge": 768,
                "prompt": "[Shot 2] 特写，俯视，固定机位。",
                "keyframe": None,
                "refs": [],
                "feasibility": {"ok": False, "warnings": ["缺少分析结果"], "confidence": 0.2},
                "estimate": {"gpu_s": 75.0, "wall_s": 75.0, "basis": None},
            },
        ],
        "warnings": ["#2：缺少分析结果"],
        "estimate": {"gpu_s": 125.0},
        "assembly": {"order": [1, 2]},
    }


def test_mapping_is_one_to_one_with_five_elements():
    storyboard = plan_shots_to_storyboard(_sample_plan())
    shots = storyboard["shots"]
    assert len(shots) == 2  # 1:1 基线
    first = shots[0]
    # 运镜语言（首句 + 场景 + 画面方向 + 视线）→ scene
    assert first["scene"] == "中景，平视，缓推；自然光，暖调。；场景：城市街道，两旁店铺入画。；画面方向：自左向右。；视线：看向镜头。"
    # 主体 + 动作节奏 → action（内容主体由 agent 按 target 改写）
    assert "主体：人物位于画面中央" in first["action"]
    assert "动作节奏：起步" in first["action"]
    # expression / dialogue 留空，由 agent 按 target 补写
    assert first["expression"] == "" and first["dialogue"] == ""
    # 时长档 → pacing；时间码/模板 prompt 透传
    assert first["pacing"] == 10
    assert first["source"] == {"start_s": 0.0, "end_s": 8.0}
    assert first["template_prompt"].startswith("[Shot 1]")
    assert first["feasible"] is True
    assert first["warnings"] == ["时长 8.00s 向上取到 10s 档"]


def test_mapping_surfaces_style_and_feasibility():
    storyboard = plan_shots_to_storyboard(_sample_plan())
    assert storyboard["style"]["look"] == "暖调"  # 跨镜一致性基线
    assert storyboard["warnings"] == ["#2：缺少分析结果"]
    assert storyboard["feasible"] is False  # 第 2 镜 feasibility.ok=False → 必须处理后落库
    assert storyboard["shots"][1]["feasible"] is False


def test_mapping_empty_plan_is_infeasible():
    storyboard = plan_shots_to_storyboard({"shots": [], "warnings": ["没有分析结果"]})
    assert storyboard["shots"] == []
    assert storyboard["feasible"] is False
