"""content-series（内容创作矩阵顶层编排）测试。

Drives :mod:`deerflow.media_gateway.content_series` — the testable orchestration
behind the ``content-series`` skill — against :class:`MockMediaGateway`（含
INSERT-SERIES 矩阵账本分区）。覆盖 workflow W6 / acceptance A2c：一个想法 →
矩阵（≥3 选题 × ≥2 格式）→ 全部生产完成（article 直出 / t2i / image-text-post
直连，video/drama 走 mock 最小链）；失败项 needs_human 不静默丢失；续作不重复
已完成项；风格指南注入每条管线。
"""

from __future__ import annotations

import json
import pathlib

import pytest

from deerflow.media_gateway.content_series import (
    FORMATS,
    MIN_TOPICS,
    SERIES_PLAN_PATH,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_NEEDS_HUMAN,
    STATUS_PENDING,
    STATUS_RUNNING,
    ContentSeriesError,
    ContentSeriesOrchestrator,
    SeriesCursor,
    SeriesPlan,
    compose_style_desc,
    default_series_plan,
    ingest_inputs,
    produce_article,
)
from deerflow.media_gateway.mock_server import MockMediaGateway

PNG_BYTES = b"\x89PNG-mock-material"

pytestmark = pytest.mark.anyio
STYLE = {"visual_style": "水墨国风", "tone": "克制、留白", "banned_words": ["震惊"]}


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


def _matrix_topics() -> list[dict]:
    """≥3 选题 × ≥2 格式，覆盖全部五条管线（A2c 主链路）。"""
    return [
        {"id": "t1", "title": "敦煌游记：开篇", "brief": "莫高窟一日路线", "formats": ["article", "image_series", "image_post"]},
        {"id": "t2", "title": "敦煌游记：壁画里的飞天", "brief": "飞天形象科普", "formats": ["video"]},
        {"id": "t3", "title": "敦煌游记：夜市奇遇", "brief": "小短剧", "formats": ["drama"]},
    ]


def _plan(**kwargs) -> SeriesPlan:
    kwargs.setdefault("style_guide", STYLE)
    kwargs.setdefault("topics", _matrix_topics())
    return default_series_plan("敦煌游记", positioning="旅行科普垂类", **kwargs)


# ── 规划器与数据结构 ──────────────────────────────────────────────────
class TestPlan:
    def test_default_plan_meets_a2c_floor(self):
        plan = _plan()
        assert len(plan.topics) >= MIN_TOPICS
        marks = {fmt for t in plan.topics for fmt in t.formats}
        assert marks <= set(FORMATS)
        assert len(marks) >= 2  # ≥3 选题 × ≥2 格式（A2c）
        assert plan.topics[0].formats  # 每个选题都有格式组合标记

    def test_topics_below_floor_rejected(self):
        with pytest.raises(ContentSeriesError):
            default_series_plan("想法", topics=[{"id": "t1", "title": "only one", "formats": ["article"]}])

    def test_explicit_llm_topics_pass_through(self):
        plan = default_series_plan(
            "想法",
            style_guide=STYLE,
            topics=[
                {"id": "a", "title": "选题A", "formats": ["article", "video"]},
                {"id": "b", "title": "选题B", "formats": ["drama"]},
                {"id": "c", "title": "选题C", "formats": ["image_series", "image_post"]},
            ],
        )
        assert [t.id for t in plan.topics] == ["a", "b", "c"]
        assert plan.items()[0]["id"] == "a:article"

    def test_unknown_formats_dropped(self):
        topic = SeriesPlan.from_dict({"topics": [{"id": "t", "title": "x", "formats": ["article", "hologram"]}]}).topics[0]
        assert topic.formats == ["article"]

    def test_style_desc_composition(self):
        desc = compose_style_desc(STYLE)
        assert "水墨国风" in desc and "克制、留白" in desc
        assert "震惊" not in desc  # 禁用词不进生成提示词

    def test_cursor_roundtrip(self):
        cursor = SeriesCursor(series_id="csplan_x")
        cursor.mark_done("t01:article", {"topic_id": "t01"})
        restored = SeriesCursor.from_json(cursor.to_json())
        assert restored.series_id == "csplan_x" and "t01:article" in restored.done


# ── 输入适配 ──────────────────────────────────────────────────────────
class TestIngest:
    async def test_files_ingested_via_media(self, mg):
        mock, client = mg
        result = await ingest_inputs(client, mock.admin_token, idea="敦煌游记", files=[pathlib.Path(f) for f in []])
        assert result["media_ids"] == [] and result["article_text"] is None

    async def test_material_files_get_media_ids(self, mg, material_files):
        mock, client = mg
        result = await ingest_inputs(client, mock.admin_token, idea="敦煌游记", files=material_files, title="游记素材")
        assert len(result["media_ids"]) == 3
        media = await client.get(f"/v1/media/{result['media_ids'][0]}")
        assert media["kind"] == "images" and media["title"] == "游记素材"

    async def test_article_text_stays_local(self, mg):
        mock, client = mg
        result = await ingest_inputs(client, mock.admin_token, article_text="# 一篇游记\n...")
        # media.md 契约只收 images/video —— 文本不入库，随计划携带
        assert result["article_text"].startswith("# 一篇游记")
        assert result["media_ids"] == []


# ── 文章直出（风格注入）───────────────────────────────────────────────
class TestArticle:
    async def test_default_template_injects_style(self):
        result = await produce_article({"title": "莫高窟一日", "brief": "路线"}, STYLE)
        assert "莫高窟一日" in result["text"]
        assert "水墨国风" in result["text"]

    async def test_injected_article_fn_receives_style_guide(self):
        seen = {}

        async def article_fn(title, brief, style_guide):
            seen.update(style=style_guide, title=title)
            return {"text": f"LLM:{title}"}

        result = await produce_article({"title": "T", "brief": ""}, STYLE, article_fn=article_fn)
        assert result["text"] == "LLM:T" and seen["style"]["visual_style"] == "水墨国风" and seen["title"] == "T"


# ── 全矩阵编排（mock 最小链）──────────────────────────────────────────
class TestOrchestration:
    async def test_full_matrix_produced_and_persisted(self, mg, material_files):
        mock, client = mg
        ingest = await ingest_inputs(client, mock.admin_token, idea="敦煌游记", files=material_files)
        plan = _plan(source={"idea": "敦煌游记", "media_ids": ingest["media_ids"]})
        report = await ContentSeriesOrchestrator(client, mock.admin_token, plan, concurrency=2, seed=7).run()

        assert report["passed"], report["summary"]
        assert {i["format"] for i in report["items"]} == set(FORMATS)  # 五条管线全部走通
        assert len(report["items"]) >= 3
        assert all(i["status"] == STATUS_DONE for i in report["items"])
        assert report["series_id"] and report["series_id"].startswith("csplan_")
        # 矩阵在 MGW 可查，且状态回写为 done
        stored = await client.get(f"{SERIES_PLAN_PATH}/{report['series_id']}")
        assert stored["positioning"] == "旅行科普垂类"
        assert stored["style_guide"]["visual_style"] == "水墨国风"
        assert {i["status"] for i in stored["items"]} == {STATUS_DONE}

    async def test_style_guide_injected_into_every_pipeline(self, mg, material_files):
        mock, client = mg
        ingest = await ingest_inputs(client, mock.admin_token, files=material_files)
        plan = _plan(source={"media_ids": ingest["media_ids"]})
        report = await ContentSeriesOrchestrator(client, mock.admin_token, plan, seed=3).run()
        assert report["passed"]

        for item in report["items"]:
            result = json.loads(json.dumps(item["result"], ensure_ascii=False, default=str))
            if item["format"] == "article":
                assert "水墨国风" in result["style"]
            elif item["format"] == "image_series":
                assert result["plan"]["style"]["style_desc"] == compose_style_desc(STYLE)
            elif item["format"] == "video":
                blob = json.dumps(result["storyboard"], ensure_ascii=False, default=str)
                assert "水墨国风" in blob
            elif item["format"] == "drama":
                assert "水墨国风" in json.dumps(result, ensure_ascii=False, default=str)

    async def test_video_without_material_needs_human_not_lost(self, mg):
        mock, client = mg
        plan = default_series_plan(
            "敦煌游记",
            style_guide=STYLE,
            topics=[{"id": "t1", "title": "视频无素材", "formats": ["video"]}, {"id": "t2", "title": "文章", "formats": ["article"]}, {"id": "t3", "title": "组图", "formats": ["image_series"]}],
        )
        report = await ContentSeriesOrchestrator(client, mock.admin_token, plan).run()
        by_id = {i["id"]: i for i in report["items"]}
        assert by_id["t1:video"]["status"] == STATUS_NEEDS_HUMAN
        assert "素材" in by_id["t1:video"]["reason"]
        assert not report["passed"]
        assert report["summary"][STATUS_NEEDS_HUMAN] == ["t1:video"]  # 不静默丢失

    async def test_handler_exception_marks_failed_item(self, mg):
        mock, client = mg

        async def boom(topic, style):
            raise RuntimeError("mock 管线爆炸")

        plan = default_series_plan(
            "敦煌游记",
            style_guide=STYLE,
            topics=[{"id": "t1", "title": "A", "formats": ["article"]}, {"id": "t2", "title": "B", "formats": ["article"]}, {"id": "t3", "title": "C", "formats": ["article"]}],
        )
        handlers = {"article": boom}
        report = await ContentSeriesOrchestrator(client, mock.admin_token, plan, handlers=handlers).run()
        assert all(i["status"] == STATUS_FAILED and "爆炸" in i["error"] for i in report["items"])
        assert report["summary"][STATUS_FAILED] == ["t1:article", "t2:article", "t3:article"]

    async def test_drama_needs_human_surfaces_from_qc_loop(self, mg):
        mock, client = mg
        plan = default_series_plan(
            "敦煌游记",
            style_guide=STYLE,
            topics=[
                {"id": "t1", "title": "飞天疑云", "formats": ["drama"]},
                {"id": "t2", "title": "B", "formats": ["article"]},
                {"id": "t3", "title": "C", "formats": ["article"]},
            ],
        )
        mock.script_qc  # noqa: B018 — knob 存在性说明：QC 队列空时缺省 qc_pass
        report = await ContentSeriesOrchestrator(client, mock.admin_token, plan).run()
        by_id = {i["id"]: i for i in report["items"]}
        assert by_id["t1:drama"]["status"] == STATUS_DONE
        assert by_id["t1:drama"]["result"]["drama_id"].startswith("dr_")

    async def test_concurrency_cap_respected(self, mg):
        mock, client = mg
        active = peak = 0
        lock = __import__("asyncio").Lock()

        async def slow_article(topic, style):
            nonlocal active, peak
            async with lock:
                active += 1
                peak = max(peak, active)
            await __import__("asyncio").sleep(0)
            async with lock:
                active -= 1
            return {"text": "x"}

        plan = _plan()
        for i, t in enumerate(plan.topics):
            t.formats = ["article"]
            t.id = f"c{i}"
        report = await ContentSeriesOrchestrator(client, mock.admin_token, plan, concurrency=2, handlers={"article": slow_article}).run()
        assert report["passed"] and peak <= 2

    async def test_resume_skips_done_items(self, mg):
        mock, client = mg
        plan = _plan(topics=[{"id": "t1", "title": "A", "formats": ["article"]}, {"id": "t2", "title": "B", "formats": ["article"]}, {"id": "t3", "title": "C", "formats": ["article"]}])
        calls: list[str] = []

        async def counting_article(topic, style):
            calls.append(topic["id"])
            return {"text": topic["id"]}

        orch = ContentSeriesOrchestrator(client, mock.admin_token, plan, handlers={"article": counting_article})
        first = await orch.run()
        assert first["passed"]

        # 续作：已完成项旁路，不重复生产
        cursor = SeriesCursor.from_json(first["cursor"].to_json())
        resumed_report = await ContentSeriesOrchestrator(client, mock.admin_token, plan, handlers={"article": counting_article}).run(resume=cursor)
        assert resumed_report["passed"]
        assert calls == ["t1", "t2", "t3"]  # 没有任何重复调用

    async def test_on_plan_checkpoint_hook(self, mg):
        mock, client = mg
        shown: list[SeriesPlan] = []

        async def confirm(plan: SeriesPlan) -> None:
            shown.append(plan)  # checkpoint：用户增删选题后继续（skill 层改 plan 再放行）

        article_plan = _plan(topics=[{"id": "t1", "title": "A", "formats": ["article"]}, {"id": "t2", "title": "B", "formats": ["article"]}, {"id": "t3", "title": "C", "formats": ["article"]}])
        report = await ContentSeriesOrchestrator(client, mock.admin_token, article_plan).run(on_plan=confirm)
        assert report["passed"] and shown and shown[0].positioning == "旅行科普垂类"

    async def test_persist_off_keeps_matrix_local(self, mg):
        mock, client = mg
        article_plan = _plan(topics=[{"id": "t1", "title": "A", "formats": ["article"]}, {"id": "t2", "title": "B", "formats": ["article"]}, {"id": "t3", "title": "C", "formats": ["article"]}])
        report = await ContentSeriesOrchestrator(client, mock.admin_token, article_plan, persist=False).run()
        assert report["series_id"] is None and report["passed"]

    async def test_status_lifecycle_through_running(self, mg):
        mock, client = mg
        states: list[str] = []

        async def probe(topic, style):
            if topic["id"] == "t1":
                states.append(next(i for i in orch.items if i["id"] == "t1:article")["status"])
            return {"text": "x"}

        plan = _plan(topics=[{"id": "t1", "title": "A", "formats": ["article"]}, {"id": "t2", "title": "B", "formats": ["article"]}, {"id": "t3", "title": "C", "formats": ["article"]}])
        orch = ContentSeriesOrchestrator(client, mock.admin_token, plan, handlers={"article": probe})
        await orch.run()
        assert states == [STATUS_RUNNING]  # 进入管线时已是 running，结束置 done
        assert all(i["status"] == STATUS_DONE for i in orch.items)
        assert orch.items[0]["status"] != STATUS_PENDING
