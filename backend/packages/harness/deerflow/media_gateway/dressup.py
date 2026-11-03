"""``dressup-tryon`` 编排模块（换装试穿）。

tasks.md M2-12（P0 试点，方案 ``docs/plans/2026-11-01-media-gateway-integration/
dressup-and-remaining-apis.md`` §3.1）：skill ``dressup-tryon`` 的可测编排层。
skill 本体是提示词（``skills/public/dressup-tryon/SKILL.md``），本模块承载
"素材上传 → 台账复用（模特/服装查重，**定妆照复用是主要提速点**）→ 建试穿 →
generate 异步作业轮询 → VLM 质检闭环（FAIL 自动 retry ≤3 次）→ 产物 URL 列表
（deer-flow 代理前缀）"的调用序列，供单测与 skill 共同引用。

契约依据：``minimax-h3-gateway/docs/integration/api/dressup.md``——条目 CRUD
同步；生成/定妆照异步（作业轮询同 ``/v1/images/jobs`` 语义）；qc 同步（VLM）；
owner 隔离（跨 owner 一律 404）；pose 白名单外必须 ``pose_text``；建档 body
``extra=forbid``。deer-flow 侧不建领域表，MGW 是真源。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from deerflow.media_gateway.client import MediaGatewayClient, MediaGatewayError
from deerflow.media_gateway.providers import proxy_media_url

MODELS_PATH = "/v1/dressup/models"
GARMENTS_PATH = "/v1/dressup/garments"
GARMENTS_FROM_MEDIA_PATH = "/v1/dressup/garments/from_media"
TRYONS_PATH = "/v1/dressup/tryons"
MEDIA_PATH = "/v1/media"

# pose 预设（dressup.md；认中文别名由网关侧处理，这里只透传预设名）。
POSES = ("front", "side", "back", "stool", "walking", "bag")
DEFAULT_POSES = ("front", "side", "back")  # 单模特×单服装×3 姿势电商上身图

# QC FAIL 自动重试上限（dressup.md：retry ≤3 次，超限转人工）。
MAX_QC_RETRIES = 3

PENDING_STATUSES = ("queued", "running", "processing", "pending", "generating", "created")

# 定妆照/试穿生成实测各约 50s；轮询由调用方注入 sleep（测试零延迟）。
DEFAULT_POLL_INTERVAL = 3.0
DEFAULT_MAX_POLLS = 60


class DressupError(Exception):
    """dressup 编排失败（契约错误 / 输入不合法 / 轮询超时）。"""


class GatewayLike:
    """The slice of ``MediaGatewayClient`` the orchestrator depends on (DI)."""

    async def get(self, path: str, **kwargs: Any) -> dict: ...
    async def post(self, path: str, **kwargs: Any) -> dict: ...
    async def post_multipart(self, path: str, **kwargs: Any) -> dict: ...


async def upload_media(client: GatewayLike, token: str, files: list[tuple[str, bytes]]) -> list[dict[str, Any]]:
    """素材入库（``POST /v1/media``，multipart 字段名 ``file`` 单数）。"""
    records = []
    for name, data in files:
        records.append(
            await client.post_multipart(  # type: ignore[attr-defined]
                MEDIA_PATH,
                token=token,
                files=[("file", (Path(name).name, data, "application/octet-stream"))],
                fields={"kind": "images"},
            )
        )
    return records


async def find_model(client: GatewayLike, token: str, *, source_media_id: str | None = None, name: str | None = None) -> dict[str, Any] | None:
    """台账查重：同 source_media_id（优先）或同名模特已有条目 → 复用。"""
    listing = await client.get(MODELS_PATH, token=token)
    for item in listing.get("items") or []:
        if source_media_id and item.get("source_media_id") == source_media_id:
            return item
        if not source_media_id and name and item.get("name") == name:
            return item
    return None


async def find_garment(client: GatewayLike, token: str, *, media_id: str | None = None, name: str | None = None) -> dict[str, Any] | None:
    listing = await client.get(GARMENTS_PATH, token=token)
    for item in listing.get("items") or []:
        if media_id and item.get("media_id") == media_id:
            return item
        if not media_id and name and item.get("name") == name:
            return item
    return None


async def ensure_model(
    client: GatewayLike,
    token: str,
    *,
    source_media_id: str,
    name: str,
    desc: str = "",
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    max_polls: int = DEFAULT_MAX_POLLS,
    wait_portrait: bool = True,
) -> dict[str, Any]:
    """复用或建模特；定妆照异步回填（``portrait_status=done``），失败不拦流程。

    重复模特**不重建**——定妆照一次约 50s，台账复用是主要提速手段。
    """
    existing = await find_model(client, token, source_media_id=source_media_id, name=name)
    model = existing or await client.post(MODELS_PATH, json={"name": name, "desc": desc, "source_media_id": source_media_id}, token=token)
    if not wait_portrait or model.get("portrait_status") == "done":
        return model
    for attempt in range(max_polls):
        model = await client.get(f"{MODELS_PATH}/{model['id']}", token=token)
        status = str(model.get("portrait_status") or "")
        if status in ("done", "failed", "skipped"):
            return model
        if attempt < max_polls - 1:
            await sleep(poll_interval)
    return model  # 超时：portrait 未完成，照常返回（portrait_url 可能为空）


async def ensure_garment(
    client: GatewayLike,
    token: str,
    *,
    media_id: str,
    name: str,
    keywords: str | list[str] | None = None,
) -> dict[str, Any]:
    """复用或建服装；keywords 缺省走 ``/garments/from_media``（自动 VLM 属性分析）。

    契约：``keywords`` 是**单个字符串**（≤4000，类目/颜色/材质词，生成时照抄进
    服装保真段）；传 list 时以空格拼接。
    """
    existing = await find_garment(client, token, media_id=media_id, name=name)
    if existing is not None:
        return existing
    if keywords:
        keyword_text = " ".join(keywords) if isinstance(keywords, list) else str(keywords)
        return await client.post(GARMENTS_PATH, json={"name": name, "gtype": "onmodel", "media_id": media_id, "keywords": keyword_text}, token=token)
    return await client.post(GARMENTS_FROM_MEDIA_PATH, json={"media_id": media_id, "name": name}, token=token)


async def generate_tryon(
    client: GatewayLike,
    token: str,
    *,
    tryon_id: str,
    pose: str | None = None,
    pose_text: str | None = None,
    output: str = "image",
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    max_polls: int = DEFAULT_MAX_POLLS,
) -> dict[str, Any]:
    """提交试穿生成（异步作业）并轮询 tryon 详情到终态。

    pose 白名单外必须给 ``pose_text``；产物 URL 已改写为 deer-flow 代理前缀。
    """
    if not pose and not pose_text:
        raise DressupError("pose 或 pose_text 必填")
    body = await client.post(f"{TRYONS_PATH}/{tryon_id}/generate", json={"output": output, **({"pose": pose} if pose else {"pose_text": pose_text})}, token=token)  # type: ignore[arg-type]
    job_id = str(body.get("job_id") or "")
    for attempt in range(max_polls):
        detail = await client.get(f"{TRYONS_PATH}/{tryon_id}", token=token)
        status = str(detail.get("status") or "")
        if status not in PENDING_STATUSES:
            outputs = [{**o, "url": proxy_media_url(o["url"])} for o in detail.get("outputs") or [] if isinstance(o, dict) and o.get("url")]
            return {**detail, "outputs": outputs, "job_id": job_id or detail.get("job_id")}
        if attempt < max_polls - 1:
            await sleep(poll_interval)
    raise DressupError(f"试穿 {tryon_id} 在 {max_polls} 次轮询内未到终态（job {job_id}）")


async def qc_and_retry(
    client: GatewayLike,
    token: str,
    *,
    tryon_id: str,
    max_retries: int = MAX_QC_RETRIES,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    max_polls: int = DEFAULT_MAX_POLLS,
) -> dict[str, Any]:
    """VLM 质检闭环：FAIL 且有 suggest → 自动 ``POST …/retry`` 重出，≤``max_retries`` 次。

    返回最终 ``{pass, answers, missing, suggest, retries, status, outputs}``；
    超限仍未过时 ``pass=false`` 并带 ``suggest``（转人工/控制台说明）。
    """
    qc = await client.post(f"{TRYONS_PATH}/{tryon_id}/qc", json={}, token=token)
    retries = 0
    while not qc.get("pass") and retries < max_retries and str(qc.get("suggest") or "").strip():
        retries += 1
        try:
            await client.post(f"{TRYONS_PATH}/{tryon_id}/retry", json={}, token=token)
        except MediaGatewayError as exc:
            if exc.status == 409:
                # 网关侧重试上限（真实网关：重试超限 409 conflict）→ 终止闭环转人工。
                qc = {**qc, "pass": False, "suggest": str(exc.message or qc.get("suggest") or "")}
                break
            raise
        await _regenerate_and_wait(client, token, tryon_id=tryon_id, sleep=sleep, poll_interval=poll_interval, max_polls=max_polls)
        qc = await client.post(f"{TRYONS_PATH}/{tryon_id}/qc", json={}, token=token)
    detail = await client.get(f"{TRYONS_PATH}/{tryon_id}", token=token)
    outputs = [{**o, "url": proxy_media_url(o["url"])} for o in detail.get("outputs") or [] if isinstance(o, dict) and o.get("url")]
    return {**qc, "retries": retries, "status": detail.get("status"), "outputs": outputs}


async def _regenerate_and_wait(
    client: GatewayLike,
    token: str,
    *,
    tryon_id: str,
    sleep: Callable[[float], Awaitable[None]],
    poll_interval: float,
    max_polls: int,
) -> dict[str, Any]:
    """retry 已在网关侧重排出作业；这里只轮询详情到终态。"""
    for attempt in range(max_polls):
        detail = await client.get(f"{TRYONS_PATH}/{tryon_id}", token=token)
        if str(detail.get("status") or "") not in PENDING_STATUSES:
            return detail
        if attempt < max_polls - 1:
            await sleep(poll_interval)
    raise DressupError(f"试穿 {tryon_id} 重出后轮询超时")


async def run_dressup_tryon(
    client: MediaGatewayClient,
    token: str,
    *,
    person_images: list[tuple[str, bytes]] | None = None,
    garment_images: list[tuple[str, bytes]] | None = None,
    person_media_id: str | None = None,
    garment_media_id: str | None = None,
    model_name: str = "模特",
    model_desc: str = "",
    garment_name: str = "服装",
    garment_keywords: list[str] | None = None,
    poses: list[str] | None = None,
    pose_texts: list[str] | None = None,
    max_retries: int = MAX_QC_RETRIES,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    max_polls: int = DEFAULT_MAX_POLLS,
) -> dict[str, Any]:
    """全流程编排：上传 → 台账复用 → 建档 → 试穿 → 生成 → QC 闭环 → 报告。

    缺省场景：单模特 × 单服装 × 3 姿势电商上身图（``poses=front/side/back``）。
    返回 ``{model_id, reused: {model, garment}, garment_id, images: [...], passed}``；
    每张 ``images[]`` 带该姿势的 tryon_id / 最终状态 / QC 结论 / 代理产物 URL。
    """
    chosen_poses = list(poses) if poses is not None else list(DEFAULT_POSES)
    extra_pose_texts = list(pose_texts or [])
    for pose in chosen_poses:
        if pose not in POSES:
            raise DressupError(f"pose 白名单外：{pose}（{POSES}）；自定义姿势请走 pose_texts")

    if person_media_id is None:
        if not person_images:
            raise DressupError("需要人物图（person_images 或 person_media_id）")
        person_media_id = (await upload_media(client, token, person_images))[0]["id"]
    if garment_media_id is None:
        if not garment_images:
            raise DressupError("需要服装图（garment_images 或 garment_media_id）")
        garment_media_id = (await upload_media(client, token, garment_images))[0]["id"]

    model_before = await find_model(client, token, source_media_id=person_media_id)
    garment_before = await find_garment(client, token, media_id=garment_media_id)
    model = await ensure_model(client, token, source_media_id=person_media_id, name=model_name, desc=model_desc, sleep=sleep, poll_interval=poll_interval, max_polls=max_polls)
    garment = await ensure_garment(client, token, media_id=garment_media_id, name=garment_name, keywords=garment_keywords)
    reused = {"model": model_before is not None, "garment": garment_before is not None}

    images: list[dict[str, Any]] = []
    jobs = list(enumerate(chosen_poses + extra_pose_texts))
    if not jobs:
        raise DressupError("至少需要一个姿势（poses 或 pose_texts）")
    for index, pose in jobs:
        is_preset = pose in POSES
        tryon = await client.post(TRYONS_PATH, json={"model_id": model["id"], "garment_id": garment["id"]}, token=token)
        generated = await generate_tryon(
            client,
            token,
            tryon_id=tryon["id"],
            pose=pose if is_preset else None,
            pose_text=None if is_preset else pose,
            sleep=sleep,
            poll_interval=poll_interval,
            max_polls=max_polls,
        )
        qc = await qc_and_retry(client, token, tryon_id=tryon["id"], max_retries=max_retries, sleep=sleep, poll_interval=poll_interval, max_polls=max_polls)
        images.append(
            {
                "pose": pose,
                "tryon_id": tryon["id"],
                "status": qc.get("status"),
                "qc_pass": bool(qc.get("pass")),
                "retries": qc.get("retries", 0),
                "missing": qc.get("missing") or [],
                "suggest": qc.get("suggest") or "",
                "outputs": qc.get("outputs") or generated.get("outputs") or [],
            }
        )

    passed = all(img["status"] == "completed" and img["qc_pass"] for img in images)
    return {"model_id": model["id"], "garment_id": garment["id"], "reused": reused, "images": images, "passed": passed}
