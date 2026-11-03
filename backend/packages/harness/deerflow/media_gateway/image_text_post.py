"""``image-text-post`` 编排模块（图文帖子）。

design.md §8.3 / tasks.md M2-10：skill ``image-text-post`` 的可测编排层。
skill 本体是提示词（``skills/public/image-text-post/SKILL.md``），本模块承载
"组图来源解析（复用既有生成 / 用户素材经 MGW 入库）→ LLM 文案（可注入）→
图文版式组装 → VLM 抽检（图文相符，可注入跳过）→ 交付 post 对象"的调用序列。

契约依据：``minimax-h3-gateway/docs/integration/api/media.md``（素材库上传）、
``vlm.md``（看图问答 ``POST /v1/vlm/chat``）、``images.md``（生成产物
``/v1/images/jobs/{id}/file``）。所有调用使用当前用户的 ``h3t_`` 令牌；
deer-flow 侧不建领域表，post 对象由调用方决定落库/渲染方式（版式为数据
模板，不做前端渲染）。

纯函数 + 依赖注入：网关访问经 :class:`~deerflow.media_gateway.client.MediaGatewayClient`
（或同 surface 对象），文案生成与 VLM 抽检均为可注入异步函数，便于单测脚本化。
不依赖 ``t2i_series``（组图来源用 job/media 引用表达，解耦生成侧实现）。
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

from deerflow.media_gateway.client import MediaGatewayClient
from deerflow.media_gateway.providers import proxy_media_url

# 产物代理 URL（images.md / media.md）：生成图走作业 /file，素材库图走 media /file。
JOB_FILE_URL_TEMPLATE = "/v1/images/jobs/{job_id}/file?kind=image"
MEDIA_FILE_URL_TEMPLATE = "/v1/media/{media_id}/file?kind=original"

# 文案对齐约束（skill 文案生成的硬约束，build_post 据此校验）。
CAPTION_MAX_LEN = 60
TITLE_MAX_LEN = 40


class ImageTextPostError(Exception):
    """image-text-post 编排失败（来源非法 / 文案不对齐 / QC 不通过）。"""

    def __init__(self, message: str, *, issues: list[str] | None = None) -> None:
        super().__init__(message)
        self.issues = issues or []


# ── 注入接缝（DI seams）───────────────────────────────────────────────
class GatewayLike(Protocol):
    """编排依赖的 MGW client 切片。"""

    async def post(self, path: str, **kwargs: Any) -> dict: ...

    async def get(self, path: str, **kwargs: Any) -> dict: ...

    async def post_multipart(self, path: str, **kwargs: Any) -> dict: ...


# 文案生成：(topic, images, feedback) → {"title", "body", "captions"}。
# feedback 非空表示上一版被 VLM 抽检否决，需按理由改写。
CopyFn = Callable[[str, list[dict], str], Awaitable[dict[str, Any]]]

# VLM 抽检：(question, images) → {"match": bool, "reason": str, "raw": text}。
# images 是发给视觉模型的内容（data URL 或 MGW 路径引用，见 to_data_url）。
VlmCheckFn = Callable[[str, list[str]], Awaitable[dict[str, Any]]]


def to_data_url(data: bytes, mime: str = "image/png") -> str:
    """bytes → ``data:`` URL（vlm.md：``/v1/vlm/chat`` 的 images 接受 data URL）。"""
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


# ── ① 组图来源解析 ────────────────────────────────────────────────────
async def resolve_images(
    client: GatewayLike,
    token: str,
    sources: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """解析组图来源为统一的图片条目列表（含 MGW 路径与 deer-flow 代理 URL）。

    支持两种来源（design.md §8.3：组图"复用 t2i-series 或已有素材"）：

    - ``{"kind": "job", "id": "imgjob_…"}``    — 既有生成作业产物
      （``GET /v1/images/jobs/{id}`` 校验存在 + completed）；
    - ``{"kind": "media", "id": "med_…"}``     — 素材库已有媒体
      （``GET /v1/media/{id}``，kind=images）。

    返回条目：``{"kind", "ref", "mgw_path", "proxy_url", "position"}``。
    """
    if not sources:
        raise ImageTextPostError("组图来源为空：图文帖子至少需要一张图", issues=["images 为空"])
    entries: list[dict[str, Any]] = []
    for position, source in enumerate(sources, start=1):
        kind = str(source.get("kind") or "")
        ref = str(source.get("id") or "")
        if kind == "job":
            job = await client.get(f"/v1/images/jobs/{ref}", token=token)
            if str(job.get("status") or "") != "completed":
                raise ImageTextPostError(f"生成作业未完成：{ref}（status={job.get('status')}）")
            mgw_path = JOB_FILE_URL_TEMPLATE.format(job_id=ref)
        elif kind == "media":
            media = await client.get(f"/v1/media/{ref}", token=token)
            if media.get("kind") != "images":
                raise ImageTextPostError(f"素材 {ref} 不是组图（kind={media.get('kind')}）")
            mgw_path = MEDIA_FILE_URL_TEMPLATE.format(media_id=ref)
        else:
            raise ImageTextPostError(f"未知组图来源类型：{kind or '(空)'}（支持 job / media）")
        entries.append({"kind": kind, "ref": ref, "mgw_path": mgw_path, "proxy_url": proxy_media_url(mgw_path), "position": position})
    return entries


# ── ② 文案生成（可注入 LLM；缺省为对齐模板，供无 LLM 环境/单测使用）────
def default_copy(topic: str, images: Sequence[Mapping[str, Any]], feedback: str = "") -> dict[str, Any]:
    """确定性模板文案（非 LLM 兜底）：标题/正文/逐图图注，图注数与图数对齐。"""
    captions = [f"图{i}：{topic}" for i in range(1, len(images) + 1)]
    body = f"{topic}。\n\n" + "\n\n".join(captions)
    if feedback:
        body = f"（修订：{feedback}）\n\n{body}"
    return {"title": topic[:TITLE_MAX_LEN], "body": body, "captions": captions}


async def llm_copy(
    topic: str,
    images: Sequence[Mapping[str, Any]],
    feedback: str = "",
    *,
    copy_fn: CopyFn | None = None,
) -> dict[str, Any]:
    """调（可注入的）LLM 文案函数并做对齐约束校验。

    约束（SKILL.md「文案生成与对齐」）：标题非空 ≤40 字、正文非空、图注
    一图一条且 ≤60 字。违规 → :class:`ImageTextPostError`（issues 带明细）。
    """
    fn = copy_fn or default_copy
    result = fn(topic, list(images), feedback) if _takes_feedback(fn) else fn(topic, list(images))  # type: ignore[call-arg]
    if not isinstance(result, dict):
        result = await result
    copy = result
    if not isinstance(copy, dict):  # pragma: no cover - 防御
        raise ImageTextPostError("文案函数返回值不是 dict")
    return validate_copy(copy, image_count=len(images))


def _takes_feedback(fn: Callable) -> bool:
    try:
        import inspect

        return len(inspect.signature(fn).parameters) >= 3
    except (TypeError, ValueError):  # pragma: no cover - 内建等不可检对象
        return True


def validate_copy(copy: Mapping[str, Any], *, image_count: int) -> dict[str, Any]:
    """文案对齐约束（零 GPU）：标题/正文非空、图注数量与图数一致。"""
    issues: list[str] = []
    title = str(copy.get("title") or "").strip()
    body = str(copy.get("body") or "").strip()
    captions = list(copy.get("captions") or [])
    if not title:
        issues.append("标题为空")
    elif len(title) > TITLE_MAX_LEN:
        issues.append(f"标题超长：{len(title)} > {TITLE_MAX_LEN}")
    if not body:
        issues.append("正文为空")
    if len(captions) != image_count:
        issues.append(f"图注数量与组图不一致：{len(captions)} 条 vs {image_count} 张图")
    for i, caption in enumerate(captions, start=1):
        if not str(caption).strip():
            issues.append(f"第 {i} 条图注为空")
        elif len(str(caption)) > CAPTION_MAX_LEN:
            issues.append(f"第 {i} 条图注超长：{len(str(caption))} > {CAPTION_MAX_LEN}")
    if issues:
        raise ImageTextPostError("文案未通过对齐约束", issues=issues)
    return {"title": title, "body": body, "captions": [str(c) for c in captions]}


# ── ③ 版式组装（数据模板，不做前端渲染）───────────────────────────────
def build_post(topic: str, images: Sequence[Mapping[str, Any]], copy: Mapping[str, Any]) -> dict[str, Any]:
    """组装结构化 post 对象：``images[] + title + body + captions``。

    版式是数据模板：调用方（前端/发布渠道）据此渲染；deer-flow 不做渲染。
    每张图带 MGW 原始路径与免令牌代理 URL（``/api/media-gateway/file/…``），
    图注按下标与图对齐。
    """
    entries = list(images)
    captions = list(copy.get("captions") or [])
    return {
        "topic": topic,
        "title": str(copy.get("title") or ""),
        "body": str(copy.get("body") or ""),
        "images": [
            {
                "position": entry["position"],
                "source_kind": entry["kind"],
                "source_ref": entry["ref"],
                "mgw_path": entry["mgw_path"],
                "url": entry["proxy_url"],
                "caption": captions[entry["position"] - 1] if entry["position"] - 1 < len(captions) else "",
            }
            for entry in entries
        ],
        "layout": {"template": "image-text-card", "caption_position": "below", "title_position": "top"},
    }


def to_markdown_card(post: Mapping[str, Any]) -> str:
    """post 对象 → markdown 卡片（skill 的交付/发布格式）。"""
    lines = [f"# {post.get('title') or ''}".rstrip(), "", str(post.get("body") or "")]
    for image in post.get("images") or []:
        caption = str(image.get("caption") or "").strip()
        alt = caption or f"图{image.get('position')}"
        lines += ["", f"![{alt}]({image.get('url')})", ""]
        if caption:
            lines += [f"*{caption}*"]
    return "\n".join(lines).strip() + "\n"


# ── ④ VLM 抽检（图文相符）─────────────────────────────────────────────
def build_vlm_question(post: Mapping[str, Any]) -> str:
    """抽检问题：把标题与图注塞进 prompt，要求模型回 JSON 结论（vlm.md chat 模式）。"""
    captions = "；".join(f"图{image.get('position')}「{image.get('caption')}」" for image in (post.get("images") or []))
    return (
        "你是图文相符质检员。帖子标题："
        f"「{post.get('title')}」。图注：{captions}。"
        "逐张核对图片内容是否与对应图注/标题相符。只返回 JSON："
        '{"match": true/false, "reason": "不符时说明哪张图不符及原因"}'
    )


def parse_vlm_verdict(body: Mapping[str, Any]) -> dict[str, Any]:
    """解析 ``/v1/vlm/chat`` 响应里的 JSON 结论（容忍 ```json 围栏）。"""
    text = str(body.get("text") or "")
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    try:
        verdict = json.loads(cleaned.strip())
    except ValueError:
        # 非 JSON 回答：含明确否定词视为不符，否则按相符放行（保守倾向）。
        negated = any(word in text for word in ("不符", "不匹配", "不一致", '"match": false'))
        return {"match": not negated, "reason": text[:200] if negated else "", "raw": text}
    if not isinstance(verdict, dict):
        return {"match": bool(verdict), "reason": "", "raw": text}
    return {"match": bool(verdict.get("match")), "reason": str(verdict.get("reason") or ""), "raw": text}


def make_vlm_check(
    client: MediaGatewayClient,
    token: str,
    *,
    image_data: Mapping[str, bytes] | None = None,
    think: bool = False,
) -> VlmCheckFn:
    """缺省 VLM 抽检函数：``POST /v1/vlm/chat``（vlm.md，质检建议 think=false）。

    ``image_data`` 把「MGW 路径 → 图片字节」映射进来；命中的条目以 data URL
    发给视觉模型，未命中的以 MGW 路径引用随 prompt 传递（mock 环境即可用）。
    """
    async def check(question: str, images: list[str]) -> dict[str, Any]:
        payload_images = [to_data_url(image_data[path]) if image_data and path in image_data and _is_png(image_data[path]) else path for path in images]
        body = await client.post(
            "/v1/vlm/chat",
            token=token,
            json={"prompt": question, "images": payload_images, "think": think, "mode": "chat"},
        )
        return parse_vlm_verdict(body)

    return check


def _is_png(data: bytes) -> bool:
    try:
        return data[:4] == b"\x89PNG" or binascii.hexlify(data[:3]) == b"ffd8ff"
    except binascii.Error:  # pragma: no cover
        return False


# ── ⑤ 全流程编排 ──────────────────────────────────────────────────────
async def run_image_text_post(
    client: GatewayLike,
    token: str,
    *,
    topic: str,
    image_sources: Sequence[Mapping[str, Any]] | None = None,
    files: Sequence[Any] | None = None,
    media_title: str = "",
    copy_fn: CopyFn | None = None,
    vlm_check: VlmCheckFn | None = None,
    max_rewrites: int = 1,
) -> dict[str, Any]:
    """图文帖子全流程：来源解析 → 文案 → 版式组装 → VLM 抽检 → 交付。

    - **组图来源**：``image_sources``（既有 job/media 引用）优先；否则
      ``files``（本地图片文件）经 ``POST /v1/media`` 入库（media.md）后引用。
    - **文案**：可注入 ``copy_fn``（缺省模板），经对齐约束校验。
    - **VLM 抽检**：``vlm_check`` 可注入；``None`` = 未配置 VLM，跳过抽检
      （status 仍为 ``passed``，``qc.skipped=True``）。不符 → 带 VLM 理由重写
      文案一次（``max_rewrites``，缺省 1）后复检；仍不符 → ``qc_failed``。

    返回 ``{"status", "post", "qc", "markdown"}``；``post.images[].url`` 均为
    deer-flow 代理 URL。
    """
    if not topic or not str(topic).strip():
        raise ImageTextPostError("选题为空：图文帖子需要一个 topic")

    sources = list(image_sources or [])
    if not sources and files:
        records = await _ingest_media(client, token, list(files), title=media_title or str(topic))
        sources = [{"kind": "media", "id": record["id"]} for record in records]
    images = await resolve_images(client, token, sources)

    feedback = ""
    qc_attempts: list[dict[str, Any]] = []
    copy: dict[str, Any] | None = None
    for attempt in range(1 + max(0, max_rewrites)):
        copy = await llm_copy(str(topic), images, feedback, copy_fn=copy_fn)
        post = build_post(str(topic), images, copy)
        if vlm_check is None:
            qc_attempts.append({"attempt": attempt + 1, "skipped": True, "match": True, "reason": ""})
            return {"status": "passed", "post": post, "qc": {"skipped": True, "attempts": qc_attempts}, "markdown": to_markdown_card(post)}
        verdict = await vlm_check(build_vlm_question(post), [entry["mgw_path"] for entry in images])
        qc_attempts.append({"attempt": attempt + 1, "skipped": False, **verdict})
        if verdict.get("match"):
            return {"status": "passed", "post": post, "qc": {"skipped": False, "attempts": qc_attempts}, "markdown": to_markdown_card(post)}
        feedback = str(verdict.get("reason") or "图文不符")
    raise ImageTextPostError(f"VLM 抽检未通过（重写 {max_rewrites} 次后仍不符）：{feedback}", issues=[a.get("reason") or "" for a in qc_attempts if a.get("reason")])


async def _ingest_media(client: GatewayLike, token: str, files: list[Any], *, title: str = "") -> list[dict[str, Any]]:
    """用户素材逐张经 MGW 素材库入库（``POST /v1/media``，multipart，kind=images）。

    每张图一条媒体记录（一图一条 ref，图注按 position 与之一一对应）。
    """
    records: list[dict[str, Any]] = []
    for f in files:
        name = getattr(f, "name", None) or "material.png"
        data = f.read() if hasattr(f, "read") else f
        if isinstance(data, str):
            data = data.encode("utf-8")
        record = await client.post_multipart(
            "/v1/media",
            token=token,
            fields={"kind": "images", "title": title},
            files=[("file", (name, data, "application/octet-stream"))],
        )
        records.append(record)
    return records
