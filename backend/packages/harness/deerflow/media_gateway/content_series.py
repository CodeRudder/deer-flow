"""``content-series`` 编排模块（内容创作矩阵顶层编排）。

design.md §8 / workflow.md W6 / tasks.md M2-11：skill ``content-series`` 的可测
编排层。skill 本体是提示词（``skills/public/content-series/SKILL.md``），本模块
承载"输入适配 → 内容规划器（矩阵）→ 管线路由（复用既有编排模块）→ 受控并发
生产 → 完成度追踪 → 续作（sidecar 游标）"的调用序列。

分层（design.md §8.1）：

- **输入适配**：想法直接进规划；现有文章以文本随计划携带（MGW 素材库
  ``POST /v1/media`` 只收 images/video——media.md 上传契约，文本不入库，
  见 :func:`ingest_inputs`）；图片/视频素材经 ``POST /v1/media`` 入库。
- **内容规划器**：:class:`SeriesPlan` = 垂类定位 + 风格指南 + 选题清单
  （每选题带格式组合标记）。LLM 在 skill 层起草，可注入 ``plan_fn``；
  缺省 :func:`default_series_plan` 是确定性骨架（≥3 选题 × 指定格式）。
- **管线路由**（全部复用既有模块，不重复实现生成）：

  ============ ================================================
  article      LLM 直出（可注入 ``article_fn``，文本不落 MGW）
  image_series :mod:`t2i_series`（文生组图，QC 闭环内置）
  image_post   :mod:`image_text_post`（组图 + 文案 + VLM 抽检）
  video        :mod:`video_clips`（素材 → 分镜五要素 → 组图生视频）
  drama        drama 结构 API 最小串联（建剧→托盘→单集分镜→生成→
               QC，复用 :mod:`qc`；完整 drama-production 流程由其
               skill 承担，这里只保证矩阵项可走通最小链）
  ============ ================================================

- **系列一致性**（§8.2）：风格指南注入每条管线的 style 参数——组图
  ``style_desc``、视频 ``notes``、短剧 prompt、文章正文共享同一段风格文本
  （:func:`compose_style_desc`）。
- **完成度追踪**：矩阵项状态 ``pending/running/done/failed/needs_human``；
  矩阵经 ``POST /v1/series/plans`` 落 MGW（mock 契约最小建模，M2-11）、
  状态经 ``PATCH`` 回写，deer-flow 不建内容领域表。
- **续作**：:class:`SeriesCursor` 只记"哪些矩阵项已完成"（sidecar 游标），
  续跑从已完成项旁路，设计数据一律从 MGW 重读/重算。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from deerflow.media_gateway.client import MediaGatewayClient
from deerflow.media_gateway.image_text_post import run_image_text_post
from deerflow.media_gateway.qc import DramaQCClosedLoop
from deerflow.media_gateway.t2i_series import default_series_plan as t2i_default_plan
from deerflow.media_gateway.t2i_series import run_t2i_series
from deerflow.media_gateway.video_clips import run_video_clips

# ── 常量 ──────────────────────────────────────────────────────────────
# 矩阵支持的格式组合标记（design.md §8.1 管线路由）。
FORMATS = ("article", "image_series", "image_post", "video", "drama")

# 矩阵项状态（完成度追踪）。
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_NEEDS_HUMAN = "needs_human"

# 矩阵登记端点（mock_server INSERT-SERIES 分区的最小建模：矩阵与状态在
# MGW 可查，deer-flow 侧不建内容领域表——design.md §8.3）。
SERIES_PLAN_PATH = "/v1/series/plans"

# 选题数量下限（acceptance A2c：一个想法 → ≥3 选题）。
MIN_TOPICS = 3

STYLE_DESC_DEFAULT = "统一插画风，柔和自然光，低饱和暖色调"


class ContentSeriesError(Exception):
    """content-series 编排失败（计划非法 / 无可路由格式）。"""


# ── 依赖注入切片 ──────────────────────────────────────────────────────
class GatewayLike(Protocol):
    """The slice of ``MediaGatewayClient`` the orchestrator depends on (DI)."""

    async def get(self, path: str, **kwargs: Any) -> dict: ...

    async def post(self, path: str, **kwargs: Any) -> dict: ...

    async def post_multipart(self, path: str, **kwargs: Any) -> dict: ...

    async def patch(self, path: str, **kwargs: Any) -> dict: ...


# 文章直出：(title, brief, style_guide) → {"title", "text"}。
ArticleFn = Callable[[str, str, Mapping[str, Any]], Awaitable[dict[str, Any]]]
# 内容规划器：(idea, context) → SeriesPlan 形状的 dict。
PlanFn = Callable[[str, Mapping[str, Any]], Awaitable[dict[str, Any]]]


# ── ① 数据结构：风格指南 / 选题 / 系列计划 ────────────────────────────
def compose_style_desc(style_guide: Mapping[str, Any]) -> str:
    """风格指南 → 注入各管线的共享风格段（系列一致性的第一约束）。

    视觉风格 + 口吻合成一段文本；禁用词不进生成提示词（负向约束走各
    管线的设计层校验），只随 :class:`SeriesPlan` 一起落 MGW。
    """
    visual = str(style_guide.get("visual_style") or "").strip() or STYLE_DESC_DEFAULT
    tone = str(style_guide.get("tone") or "").strip()
    return f"{visual}；{tone}" if tone else visual


@dataclass
class SeriesTopic:
    """一个选题：标题 + 简述 + 格式组合标记（design.md §8.1 规划器输出）。"""

    id: str
    title: str
    brief: str = ""
    formats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "brief": self.brief, "formats": list(self.formats)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SeriesTopic:
        return cls(id=str(data.get("id") or ""), title=str(data.get("title") or ""), brief=str(data.get("brief") or ""), formats=[f for f in (data.get("formats") or []) if f in FORMATS])


@dataclass
class SeriesPlan:
    """内容矩阵：垂类定位 + 风格指南（落 MGW）+ 选题清单。"""

    positioning: str
    style_guide: dict[str, Any]
    topics: list[SeriesTopic]
    source: dict[str, Any] = field(default_factory=dict)  # idea / article_text / media_ids

    def to_dict(self) -> dict[str, Any]:
        return {"positioning": self.positioning, "style_guide": dict(self.style_guide), "topics": [t.to_dict() for t in self.topics], "source": dict(self.source)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SeriesPlan:
        return cls(
            positioning=str(data.get("positioning") or ""),
            style_guide=dict(data.get("style_guide") or {}),
            topics=[SeriesTopic.from_dict(t) for t in data.get("topics") or []],
            source=dict(data.get("source") or {}),
        )

    def items(self) -> list[dict[str, Any]]:
        """矩阵项（选题 × 格式）：完成度追踪的基本单元。"""
        return [{"id": f"{t.id}:{fmt}", "topic_id": t.id, "topic_title": t.title, "format": fmt, "status": STATUS_PENDING} for t in self.topics for fmt in t.formats]


def default_series_plan(
    idea: str,
    *,
    positioning: str = "",
    style_guide: Mapping[str, Any] | None = None,
    topics: list[Mapping[str, Any]] | None = None,
    topic_count: int = MIN_TOPICS,
    formats: list[str] | None = None,
    source: Mapping[str, Any] | None = None,
) -> SeriesPlan:
    """确定性骨架计划（skill 层 LLM 在此基础上覆写选题/格式组合）。

    ``topics`` 给定时逐条采用（LLM 起草的矩阵）；否则按 ``topic_count``
    生成骨架选题，每个都带 ``formats``（缺省 article + image_series，
    第一个选题额外带 image_post——图文帖可直接复用组图）。
    """
    guide = dict(style_guide or {})
    if not guide.get("visual_style"):
        guide["visual_style"] = STYLE_DESC_DEFAULT
    if topics:
        picked = [SeriesTopic.from_dict(t) for t in topics]
    else:
        base_formats = [f for f in (formats or ["article", "image_series"]) if f in FORMATS]
        if not base_formats:
            raise ContentSeriesError("格式组合为空：至少需要一个管线格式")
        picked = []
        for index in range(1, max(MIN_TOPICS, int(topic_count)) + 1):
            fmts = list(base_formats)
            if index == 1 and "image_post" not in fmts:
                fmts.append("image_post")  # 首选题图文帖：组图可直接复用（§8.3）
            picked.append(SeriesTopic(id=f"t{index:02d}", title=f"{idea}——选题{index}", brief=f"围绕「{idea}」的第 {index} 个选题", formats=fmts))
    if len(picked) < MIN_TOPICS:
        raise ContentSeriesError(f"选题数量不足：{len(picked)}（acceptance A2c 要求 ≥{MIN_TOPICS}）")
    return SeriesPlan(positioning=positioning or str(idea), style_guide=guide, topics=picked, source=dict(source or {"idea": str(idea)}))


# ── ② 输入适配 ────────────────────────────────────────────────────────
async def ingest_inputs(
    client: GatewayLike,
    token: str,
    *,
    idea: str = "",
    article_text: str | None = None,
    files: list[Any] | None = None,
    title: str = "",
) -> dict[str, Any]:
    """输入适配（design.md §8.1 输入层）：想法 / 现有文章 / 图片视频素材。

    - 想法：直接进规划（不产生 MGW 调用）；
    - 现有文章：**文本不入库**——MGW ``POST /v1/media`` 上传契约只收
      images/video（media.md），文本随 :class:`SeriesPlan.source` 携带，
      由规划器（LLM）直接阅读后派生选题；
    - 图片/视频素材：经 ``POST /v1/media`` 入库（multipart），返回
      ``media_ids`` 供 video/image_post 管线引用。

    返回 ``{"article_text", "media_ids"}``。
    """
    media_ids: list[str] = []
    for f in files or []:
        path = Path(f)
        name = path.name
        data = path.read_bytes() if isinstance(f, (str, Path)) else (f.read() if hasattr(f, "read") else f)  # noqa: PLW2901
        record = await client.post_multipart("/v1/media", token=token, fields={"kind": "images", "title": title or str(idea)}, files=[("file", (name, data, "application/octet-stream"))])
        media_ids.append(str(record["id"]))
    return {"article_text": article_text, "media_ids": media_ids}


# ── ③ 各格式管线（全部复用既有模块）───────────────────────────────────
async def produce_article(topic: Mapping[str, Any], style_guide: Mapping[str, Any], *, article_fn: ArticleFn | None = None) -> dict[str, Any]:
    """短篇文章：LLM 直出（可注入）。缺省模板把风格指南写进正文，供
    无 LLM 环境/单测使用；文本不落 MGW（§8.1：文本落素材库/文档由调用方决定）。"""
    title = str(topic.get("title") or "")
    brief = str(topic.get("brief") or "")
    desc = compose_style_desc(style_guide)
    if article_fn is not None:
        result = await article_fn(title, brief, dict(style_guide))
        return {"text": str(result.get("text") or ""), "style": desc}
    body = f"{title}。\n\n{brief}\n\n（风格：{desc}）"
    return {"text": body, "style": desc}


async def produce_image_series(
    client: GatewayLike,
    token: str,
    topic: Mapping[str, Any],
    style_guide: Mapping[str, Any],
    *,
    count: int = 2,
    seed: int | None = None,
    max_iterations: int = 3,
    fix_fn: Any = None,
) -> dict[str, Any]:
    """文生组图：复用 :mod:`t2i_series`，风格指南注入组图级 ``style_desc``。"""
    plan = t2i_default_plan(str(topic.get("title") or ""), count=int(count), style={"style_desc": compose_style_desc(style_guide)})
    return await run_t2i_series(client, token, plan=plan, seed=seed, max_iterations=max_iterations, register=True, fix_fn=fix_fn)


async def produce_image_post(
    client: GatewayLike,
    token: str,
    topic: Mapping[str, Any],
    style_guide: Mapping[str, Any],
    *,
    image_refs: list[Mapping[str, Any]] | None = None,
    copy_fn: Any = None,
    vlm_check: Any = None,
    seed: int | None = None,
    max_iterations: int = 3,
) -> dict[str, Any]:
    """图文帖子：复用 :mod:`image_text_post`。组图来源优先用 ``image_refs``
    （既有 job/media 引用）；否则先出一张最小组图（复用 t2i 管线）再组装。"""
    refs = [dict(r) for r in image_refs or []]
    t2i_report: dict[str, Any] | None = None
    if not refs:
        t2i_report = await produce_image_series(client, token, topic, style_guide, count=1, seed=seed, max_iterations=max_iterations)
        refs = [{"kind": "job", "id": img["job_id"]} for img in t2i_report.get("images") or [] if img.get("status") == "completed" and img.get("job_id")]
        if not refs:
            return {"status": STATUS_NEEDS_HUMAN, "reason": "组图来源为空：前置组图未产出可用图", "t2i": t2i_report}
    return await run_image_text_post(client, token, topic=str(topic.get("title") or ""), image_sources=refs, copy_fn=copy_fn, vlm_check=vlm_check)


def default_video_designs(topic: Mapping[str, Any], style_guide: Mapping[str, Any]) -> dict[int, dict]:
    """确定性三镜五要素骨架（skill 层 LLM 覆写）；风格段写进每镜场景。"""
    desc = compose_style_desc(style_guide)
    brief = str(topic.get("brief") or "")
    return {
        1: {"action": f"开场：交代主题（{brief}）", "expression": "专注", "dialogue": "（开场）我们开始吧。", "scene": f"定场镜头；{desc}", "pacing": 5, "refs": [1]},
        2: {"action": "主体展开：核心内容演示", "expression": "自然", "dialogue": "（讲解）看这里。", "scene": f"中景推近；{desc}", "pacing": 5, "refs": [1]},
        3: {"action": "收束：总结与引导关注", "expression": "微笑", "dialogue": "（收尾）下次再见。", "scene": f"拉远定格；{desc}", "pacing": 1, "refs": [1]},
    }


async def produce_video(
    client: GatewayLike,
    token: str,
    topic: Mapping[str, Any],
    style_guide: Mapping[str, Any],
    *,
    files: list[Any] | None = None,
    media_id: str | None = None,
    designs: dict[int, dict] | None = None,
    seed: int | None = None,
    max_polls: int = 20,
) -> dict[str, Any]:
    """组图生视频：复用 :mod:`video_clips`；风格指南随 ``notes`` 落分镜方案。"""
    if media_id is None and not files:
        return {"status": STATUS_NEEDS_HUMAN, "reason": "视频缺素材：需要 files（本地图片）或 media_id（素材库组图）"}
    notes = f"{topic.get('brief') or ''}；统一风格：{compose_style_desc(style_guide)}".strip("；")
    return await run_video_clips(
        client,
        token,
        files=files,
        media_id=media_id,
        designs=designs or default_video_designs(topic, style_guide),
        shot_count=3,
        notes=notes,
        seed=seed,
        max_polls=max_polls,
    )


def default_tray() -> list[dict[str, Any]]:
    """最小托盘（与 mock 契约对齐的角色/场景引用；完整托盘由 drama-production 建）。"""
    return [
        {"media_id": "med_scene1", "kind": "scene", "name": "主场景"},
        {"media_id": "med_char1", "kind": "character", "name": "主角"},
    ]


async def _noop_fix(plan: dict, qc: dict) -> dict:
    """缺省修正函数：短剧最小链不做托盘/分镜改写（完整闭环在 drama-production）。"""
    return {}


async def produce_drama(
    client: GatewayLike,
    token: str,
    topic: Mapping[str, Any],
    style_guide: Mapping[str, Any],
    *,
    tray: list[dict[str, Any]] | None = None,
    max_iterations: int = 3,
    fix_fn: Any = None,
) -> dict[str, Any]:
    """短剧最小串联：建剧 → 托盘 → 单集分镜 → 生成 → QC（复用 :mod:`qc`）。

    **注意**：完整的 drama-production 流程（多章节/多集/托盘精细设计/逐镜
    迭代）由该 skill 承担；这里只保证矩阵项能走通"结构 → 生成 → QC"最小
    链，风格指南注入首镜提示词。
    """
    desc = compose_style_desc(style_guide)
    title = str(topic.get("title") or "")
    drama = await client.post("/v1/drama", json={"title": title}, token=token)
    did = drama["id"]
    chapter = await client.post(f"/v1/drama/{did}/chapters", json={"title": "第一章", "idx": 1}, token=token)
    ch = chapter["id"]
    storyline = await client.post(f"/v1/drama/{did}/chapters/{ch}/storylines", json={"name": "主线", "order": 1}, token=token)
    plot = await client.post(f"/v1/drama/{did}/chapters/{ch}/plots", json={"storyline_id": storyline["id"], "name": "开场", "order": 1}, token=token)
    clip = await client.post(
        f"/v1/drama/{did}/chapters/{ch}/clips",
        json={
            "plot_id": plot["id"],
            "order": 1,
            "budget_tier": 5,
            "prompt_opening": f"{topic.get('brief') or title}；{desc}",
            "ref_tray": tray or default_tray(),
        },
        token=token,
    )
    cl = clip["id"]
    await client.post(
        f"/v1/drama/{did}/chapters/{ch}/shots",
        json={
            "clip_id": cl,
            "order": 1,
            "duration_s": 4,
            "prompt_en": f"{title}: opening shot. {desc}",
            "prompt_zh": f"{title}开场；风格：{desc}；主角按<Picture 1>站位",
            "dialogue": [{"speaker": "主角", "text": "开始了。"}],
            "camera": "缓慢推近",
            "size": "中景",
        },
        token=token,
    )
    loop = DramaQCClosedLoop(client, drama_id=did, chapter_id=ch, clip_id=cl, token=token, max_iterations=max_iterations)
    result = await loop.run(fix_fn or _noop_fix)
    return {"status": result["status"], "drama_id": did, "chapter_id": ch, "clip_id": cl, "style": desc, "qc": result.get("qc"), "iterations": result.get("iterations"), "issues": result.get("issues", [])}


# ── ④ 续作游标（sidecar）──────────────────────────────────────────────
@dataclass
class SeriesCursor:
    """续作游标：只记"哪些矩阵项已完成"（结果摘要随行），设计数据从 MGW 重读。"""

    series_id: str | None = None
    done: dict[str, Any] = field(default_factory=dict)  # item_id → 结果摘要

    def mark_done(self, item_id: str, summary: Mapping[str, Any]) -> None:
        self.done[item_id] = dict(summary)

    def to_json(self) -> str:
        return json.dumps({"series_id": self.series_id, "done": self.done}, ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> SeriesCursor:
        data = json.loads(raw)
        return cls(series_id=data.get("series_id"), done=dict(data.get("done") or {}))


# ── ⑤ 编排器：受控并发 + 完成度追踪 + 续作 ────────────────────────────
class ContentSeriesOrchestrator:
    """内容矩阵编排：路由 → 受控并发生产 → 状态追踪 → 矩阵落 MGW → 续作。

    ``handlers`` 可按格式覆写管线（单测注入失败/桩）；矩阵经
    ``POST /v1/series/plans`` 登记并逐项 ``PATCH`` 状态（``persist=False``
    可关闭——矩阵留在会话侧）。失败/需人工项按限次策略停在对应状态，
    汇总里显式列出，不静默丢失。
    """

    def __init__(
        self,
        client: GatewayLike,
        token: str,
        plan: SeriesPlan,
        *,
        concurrency: int = 2,
        seed: int | None = None,
        max_iterations: int = 3,
        article_fn: ArticleFn | None = None,
        copy_fn: Any = None,
        vlm_check: Any = None,
        handlers: Mapping[str, Any] | None = None,
        persist: bool = True,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self.token = token
        self.plan = plan
        self.concurrency = max(1, int(concurrency))
        self.seed = seed
        self.max_iterations = max_iterations
        self.article_fn = article_fn
        self.copy_fn = copy_fn
        self.vlm_check = vlm_check
        self.persist = persist
        self._sleep = sleep
        self.items: list[dict[str, Any]] = plan.items()
        self.series_id: str | None = None
        self.cursor = SeriesCursor()
        self._handlers: dict[str, Any] = dict(handlers or {}) or {
            "article": self._h_article,
            "image_series": self._h_image_series,
            "image_post": self._h_image_post,
            "video": self._h_video,
            "drama": self._h_drama,
        }

    # ── 管线包装（统一签名：client, token, topic, style_guide）────────
    async def _h_article(self, topic: dict, style: dict) -> dict:
        return await produce_article(topic, style, article_fn=self.article_fn)

    async def _h_image_series(self, topic: dict, style: dict) -> dict:
        return await produce_image_series(self._client, self.token, topic, style, seed=self.seed, max_iterations=self.max_iterations)

    async def _h_image_post(self, topic: dict, style: dict) -> dict:
        return await produce_image_post(self._client, self.token, topic, style, image_refs=topic.get("image_refs"), copy_fn=self.copy_fn, vlm_check=self.vlm_check, seed=self.seed, max_iterations=self.max_iterations)

    async def _h_video(self, topic: dict, style: dict) -> dict:
        media_id = topic.get("media_id") or next(iter(topic.get("media_ids") or []), None)
        return await produce_video(self._client, self.token, topic, style, files=topic.get("files"), media_id=media_id, seed=self.seed, max_polls=self._max_polls)

    async def _h_drama(self, topic: dict, style: dict) -> dict:
        return await produce_drama(self._client, self.token, topic, style, max_iterations=self.max_iterations)

    _max_polls: int = 20

    # ── 矩阵落 MGW（最小建模，best-effort）────────────────────────────
    async def _register(self) -> None:
        if not self.persist:
            return
        body = await self._client.post(
            SERIES_PLAN_PATH,
            json={
                "positioning": self.plan.positioning,
                "style_guide": self.plan.style_guide,
                "topics": [t.to_dict() for t in self.plan.topics],
                "items": [{k: v for k, v in item.items() if k in ("id", "topic_id", "format", "status")} for item in self.items],
            },
            token=self.token,
        )
        self.series_id = body.get("id")
        self.cursor.series_id = self.series_id

    async def _sync_statuses(self) -> None:
        if not (self.persist and self.series_id):
            return
        try:
            await self._client.patch(
                f"{SERIES_PLAN_PATH}/{self.series_id}",
                json={"items": [{"id": item["id"], "status": item["status"]} for item in self.items]},
                token=self.token,
            )
        except Exception:
            pass  # 状态回写是 best-effort；完成度真相以本侧 items + cursor 为准

    # ── 单项执行 ──────────────────────────────────────────────────────
    async def _run_item(self, item: dict[str, Any]) -> None:
        item["status"] = STATUS_RUNNING
        await self._sync_statuses()
        topic = next(t.to_dict() for t in self.plan.topics if t.id == item["topic_id"])
        topic.update({k: v for k, v in (self.plan.source or {}).items() if k not in topic})
        handler = self._handlers.get(item["format"])
        if handler is None:
            item.update({"status": STATUS_FAILED, "error": f"未知格式：{item['format']}（支持 {FORMATS}）"})
            return
        try:
            result = await handler(topic, self.plan.style_guide)
        except Exception as exc:  # noqa: BLE001 — 单项失败不拖垮整个矩阵（design.md §8 限次策略）
            item.update({"status": STATUS_FAILED, "error": str(exc)})
            return
        status = str(result.get("status") or "")
        if status in (STATUS_NEEDS_HUMAN, "design_failed"):  # qc.py 的设计门失败同样需人工
            item.update({"status": STATUS_NEEDS_HUMAN, "reason": str(result.get("reason") or "、".join(result.get("issues") or []) or status), "result": result})
        elif result.get("passed") is False:  # t2i 组图：passed=False（QC/一致性未全过）
            issues = "、".join((result.get("consistency") or {}).get("issues") or [])
            item.update({"status": STATUS_NEEDS_HUMAN, "reason": issues or "管线报告 passed=False", "result": result})
        else:
            item.update({"status": STATUS_DONE, "result": result})
            self.cursor.mark_done(item["id"], {"topic_id": item["topic_id"], "format": item["format"], "at": item.get("result") is not None})

    # ── 全流程 ────────────────────────────────────────────────────────
    async def run(self, *, resume: SeriesCursor | None = None, on_plan: Callable[[SeriesPlan], Awaitable[None]] | None = None) -> dict[str, Any]:
        """生产整个矩阵。``resume`` 给定时已完成项旁路（续作不重复生产）。

        ``on_plan`` 是 skill 层的确认接缝（checkpoint：用户增删选题后继续；
        auto：传 None 直过）。返回完成度报告 ``{series_id, plan, items,
        summary, passed}``。
        """
        if on_plan is not None:
            await on_plan(self.plan)
        if resume is not None:
            self.cursor = resume
            for item in self.items:
                if item["id"] in resume.done:
                    item["status"] = STATUS_DONE
                    item["result"] = {"resumed": True}
        await self._register()

        semaphore = asyncio.Semaphore(self.concurrency)

        async def guarded(item: dict[str, Any]) -> None:
            async with semaphore:
                await self._run_item(item)

        # 续作：已完成项旁路（resume 游标标记），只生产未完成项
        todo = [item for item in self.items if item["status"] != STATUS_DONE]
        await asyncio.gather(*(guarded(item) for item in todo))
        await self._sync_statuses()

        summary = {s: [i["id"] for i in self.items if i["status"] == s] for s in (STATUS_DONE, STATUS_FAILED, STATUS_NEEDS_HUMAN)}
        return {
            "series_id": self.series_id,
            "plan": self.plan.to_dict(),
            "items": self.items,
            "summary": summary,
            "cursor": self.cursor,
            "passed": len(summary[STATUS_DONE]) == len(self.items),
        }


async def run_content_series(
    client: MediaGatewayClient,
    token: str,
    plan: SeriesPlan,
    *,
    concurrency: int = 2,
    seed: int | None = None,
    max_iterations: int = 3,
    article_fn: ArticleFn | None = None,
    copy_fn: Any = None,
    vlm_check: Any = None,
    handlers: Mapping[str, Any] | None = None,
    resume: SeriesCursor | None = None,
    on_plan: Callable[[SeriesPlan], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """一次性编排入口（见 :class:`ContentSeriesOrchestrator`）。"""
    orchestrator = ContentSeriesOrchestrator(
        client,
        token,
        plan,
        concurrency=concurrency,
        seed=seed,
        max_iterations=max_iterations,
        article_fn=article_fn,
        copy_fn=copy_fn,
        vlm_check=vlm_check,
        handlers=handlers,
    )
    return await orchestrator.run(resume=resume, on_plan=on_plan)
