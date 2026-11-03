"""``video-clips`` 编排模块（组图生视频 / 分镜片段）。

design.md §6.1 / §7.2、workflow.md W3b：skill ``video-clips`` 的可测编排层。
skill 本体是提示词（``skills/public/video-clips/SKILL.md``），本模块承载
"素材入库 → plan/design → 分镜五要素落库 → 提交生成（Idempotency-Key）→
QC 校验 → 逐镜产物（代理 URL）"的调用序列，供单测与 skill 共同引用。

数据真源在 MGW（组图媒体 + storyboard.json + groupvid.json）；deer-flow 侧
不建领域表，:class:`SidecarCursor` 只描述"断点续作游标"（当前镜次/阶段），
由 skill 以 sidecar 文件形式持久化。

契约依据：``minimax-h3-gateway/docs/integration/api/groupvid.md``（流水线 A：
媒体端点）+ ``media.md``（素材库）。所有调用使用当前用户的 ``h3t_`` 令牌。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from deerflow.media_gateway.client import MediaGatewayClient
from deerflow.media_gateway.providers import proxy_media_url

# groupvid 逐镜生成走作业端点；产物字节统一从 /file?kind=video 取（jobs.md）。
VIDEO_FILE_URL_TEMPLATE = "/v1/images/jobs/{job_id}/file?kind=video"

# storyboard 时长档只有四档；其它值在 MGW 落盘为 null/按方案级缺省。
DURATION_TIERS = (1, 5, 10, 15)
DEFAULT_DURATION_TIER = 5

# 分镜数量（媒体端点设计 1–10；任务端点 1–12，这里走媒体端点）。
SHOT_COUNT_MIN, SHOT_COUNT_MAX = 1, 10


class VideoClipsError(Exception):
    """video-clips 编排失败（契约错误 / QC 拦截）。"""

    def __init__(self, message: str, *, issues: list[str] | None = None) -> None:
        super().__init__(message)
        self.issues = issues or []


# ── 五要素分镜（design.md §7.2）───────────────────────────────────────
# 每镜五要素：动作 / 表情 / 对话 / 场景 / 节奏。LLM 按此起草，本模块把
# 它映射成 MGW storyboard 镜头字段（subject/action/camera/duration_tier/refs）。
FIVE_ELEMENTS = ("action", "expression", "dialogue", "scene", "pacing")


def build_shot_patch(design: dict) -> dict:
    """把一镜的五要素草稿映射成 MGW ``PATCH /storyboard/shots/{index}`` 请求体。

    合同字段只有 ``subject/action/camera/soundscape/music/refs``（groupvid.md
    §3.3），五要素按下述方式落进合同：

    - 动作（``action``）→ ``action`` 主干；
    - 表情（``expression``）/ 对话（``dialogue``）→ 追加进 ``action`` 文本
      （"表情：…；台词：…"），保证生成提示词里带情绪与台词上下文；
    - 场景（``scene``）→ ``camera``（镜头语言/机位/构图）；
    - 节奏（``pacing``）→ ``duration_tier``（只认 1/5/10/15，其它忽略走缺省）；
    - ``refs``（参考图编号）与 ``subject``（主体）原样透传（可选）。
    """
    parts: list[str] = []
    action = str(design.get("action") or "").strip()
    if action:
        parts.append(action)
    expression = str(design.get("expression") or "").strip()
    if expression:
        parts.append(f"表情：{expression}")
    dialogue = str(design.get("dialogue") or "").strip()
    if dialogue:
        parts.append(f"台词：{dialogue}")

    patch: dict = {}
    if parts:
        patch["action"] = "；".join(parts)
    camera = str(design.get("scene") or "").strip()
    if camera:
        patch["camera"] = camera
    subject = str(design.get("subject") or "").strip()
    if subject:
        patch["subject"] = subject

    pacing = design.get("pacing")
    if pacing is not None and int(pacing) in DURATION_TIERS:  # type: ignore[arg-type]
        patch["duration_tier"] = int(pacing)  # type: ignore[arg-type]

    refs = design.get("refs")
    if refs:
        patch["refs"] = [int(r) for r in refs]  # type: ignore[arg-type]
    return patch


def qc_check_shot(shot: dict, *, image_count: int | None = None) -> list[str]:
    """设计层校验（零 GPU）：五要素齐备 + 参考图合法。返回问题清单（空 = 通过）。"""
    issues: list[str] = []
    action = str(shot.get("action") or "")
    if not action.strip():
        issues.append("第 {index} 镜缺少动作（action）".format(index=shot.get("index", "?")))
    for marker, label in (("表情：", "表情"), ("台词：", "对话")):
        if marker not in action:
            issues.append("第 {index} 镜缺少{label}要素".format(index=shot.get("index", "?"), label=label))
    if not str(shot.get("camera") or "").strip():
        issues.append(f"第 {shot.get('index', '?')} 镜缺少场景/镜头语言（camera）")
    if shot.get("duration_tier") not in (None, *DURATION_TIERS):
        issues.append(f"第 {shot.get('index', '?')} 镜时长档非法：{shot.get('duration_tier')}")
    refs = shot.get("refs") or []
    if not refs:
        issues.append(f"第 {shot.get('index', '?')} 镜没有参考图（refs 为空）")
    elif image_count is not None:
        for ref in refs:
            if not 1 <= int(ref) <= image_count:
                issues.append(f"第 {shot.get('index', '?')} 镜参考图编号越界：{ref}（共 {image_count} 张）")
    return issues


def qc_check_storyboard(storyboard: dict, *, image_count: int | None = None) -> list[str]:
    """整板设计层校验：逐镜 ``qc_check_shot`` 汇总。"""
    issues: list[str] = []
    for shot in storyboard.get("shots") or []:
        issues.extend(qc_check_shot(shot, image_count=image_count))
    if not storyboard.get("shots"):
        issues.append("分镜方案为空")
    return issues


# ── 断点游标（sidecar）────────────────────────────────────────────────
@dataclass
class SidecarCursor:
    """中断续作游标：只记"做到哪了"，设计数据一律从 MGW 重读。"""

    media_id: str
    stage: str  # ingested / designed / confirmed / submitted / done
    shot_index: int = 0
    task_id: str | None = None
    job_ids: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps({"media_id": self.media_id, "stage": self.stage, "shot_index": self.shot_index, "task_id": self.task_id, "job_ids": self.job_ids}, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> SidecarCursor:
        data = json.loads(raw)
        return cls(media_id=data["media_id"], stage=data["stage"], shot_index=int(data.get("shot_index") or 0), task_id=data.get("task_id"), job_ids=list(data.get("job_ids") or []))


# ── MGW 调用序列 ──────────────────────────────────────────────────────
def _idempotency_key(media_id: str, action: str, payload: dict) -> str:
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
    return f"groupvid:{media_id}:{action}:{digest}"


async def ingest_materials(
    client: MediaGatewayClient,
    token: str,
    files: list[Path],
    *,
    title: str = "",
    description: str = "",
    idempotency_key: str | None = None,
) -> dict:
    """素材入库：``POST /v1/media``（multipart，kind=images）。返回媒体记录。"""
    if not files:
        raise VideoClipsError("素材为空：至少需要一张参考图")
    payload = {"title": title, "description": description}
    multipart_files = [("file", (Path(f).name, Path(f).read_bytes(), "application/octet-stream")) for f in files]
    key = idempotency_key or _idempotency_key("ingest", "media", {"title": title, "names": sorted(Path(f).name for f in files)})
    return await client.post_multipart("/v1/media", token=token, files=multipart_files, fields=payload, idempotency_key=key)


async def ensure_analysis(client: MediaGatewayClient, token: str, media_id: str) -> dict:
    """确保组图分析已完成（``POST /v1/media/{id}/analyze``，幂等）。"""
    await client.post(f"/v1/media/{media_id}/analyze", token=token, json={}, idempotency_key=f"groupvid:{media_id}:analyze")
    return await client.get(f"/v1/media/{media_id}/analysis", token=token)


async def draft_storyboard(
    client: MediaGatewayClient,
    token: str,
    media_id: str,
    *,
    shot_count: int = 3,
    duration_tier: int = DEFAULT_DURATION_TIER,
    notes: str | None = None,
) -> dict:
    """LLM 起草的初稿经 MGW 落库：``POST /v1/media/{id}/storyboard``。"""
    if not SHOT_COUNT_MIN <= shot_count <= SHOT_COUNT_MAX:
        raise VideoClipsError(f"分镜数量超出范围：{shot_count}（{SHOT_COUNT_MIN}–{SHOT_COUNT_MAX}）")
    body = {"shot_count": int(shot_count), "duration_tier": int(duration_tier)}
    if notes:
        body["notes"] = notes
    return await client.post(f"/v1/media/{media_id}/storyboard", token=token, json=body, idempotency_key=_idempotency_key(media_id, "storyboard", body))


async def apply_shot_designs(
    client: MediaGatewayClient,
    token: str,
    media_id: str,
    designs: dict[int, dict],
) -> dict[int, list[str]]:
    """逐镜落五要素：``PATCH /v1/media/{id}/storyboard/shots/{index}``。

    返回 ``{镜号: 校验问题}``（来自 MGW 回读的更新后镜头）。校验只报告不拦截，
    拦截由 :func:`run_video_clips` 在提交前统一执行（auto/checkpoint 一致）。
    """
    issues: dict[int, list[str]] = {}
    for index, design in sorted(designs.items()):
        patch = build_shot_patch(design)
        body = await client.patch(f"/v1/media/{media_id}/storyboard/shots/{index}", token=token, json=patch)
        shot = body.get("shot") or {}
        issues[index] = qc_check_shot(shot)
    return issues


async def read_storyboard(client: MediaGatewayClient, token: str, media_id: str) -> dict:
    """中断续作入口：从 MGW 重读现行分镜方案（``GET /storyboard``）。"""
    return await client.get(f"/v1/media/{media_id}/storyboard", token=token)


async def submit_generation(
    client: MediaGatewayClient,
    token: str,
    media_id: str,
    *,
    shots: list[int] | None = None,
    seed: int | None = None,
    candidates: int = 1,
    idempotency_key: str | None = None,
) -> dict:
    """确认提交：``POST /v1/media/{id}/generate``（每镜一单 ref2va，幂等）。"""
    body: dict = {"candidates": int(candidates)}
    if shots:
        body["shots"] = [int(s) for s in shots]
    if seed is not None:
        body["seed"] = int(seed)
    key = idempotency_key or _idempotency_key(media_id, "generate", body)
    return await client.post(f"/v1/media/{media_id}/generate", token=token, json=body, idempotency_key=key)


async def await_artifacts(
    client: MediaGatewayClient,
    token: str,
    jobs: list[dict],
    *,
    max_polls: int = 20,
) -> list[dict]:
    """轮询逐镜作业到终态，汇总产物。

    ``jobs`` 是提交响应里的 ``jobs`` 数组（``{shot, job_id, candidate, …}``）。
    返回每条 ``{shot, job_id, candidate, status, file_url, error}``；
    ``file_url`` 已改写成 deer-flow 代理地址（``/api/media-gateway/file/…``）。
    """
    results: list[dict] = []
    pending = list(jobs)
    for _ in range(max_polls):
        still_pending = []
        for entry in pending:
            body = await client.get(f"/v1/images/jobs/{entry['job_id']}", token=token)
            status = str(body.get("status") or "")
            if status in ("queued", "running"):
                still_pending.append(entry)
                continue
            result: dict = {"shot": entry.get("shot"), "job_id": entry["job_id"], "candidate": entry.get("candidate", 0), "status": status}
            if status == "completed":
                result["file_url"] = proxy_media_url(VIDEO_FILE_URL_TEMPLATE.format(job_id=entry["job_id"]))
            else:
                error = body.get("error") or {}
                result["error"] = str(error.get("message") or error.get("code") or "generation failed")
            results.append(result)
        pending = still_pending
        if not pending:
            break
    for entry in pending:
        results.append({"shot": entry.get("shot"), "job_id": entry["job_id"], "candidate": entry.get("candidate", 0), "status": "timeout", "error": f"作业 {entry['job_id']} 在 {max_polls} 次轮询内未到终态"})
    return sorted(results, key=lambda r: (int(r["shot"] or 0), int(r.get("candidate") or 0)))


async def run_video_clips(
    client: MediaGatewayClient,
    token: str,
    *,
    files: list[Path] | None = None,
    media_id: str | None = None,
    designs: dict[int, dict] | None = None,
    shot_count: int = 3,
    duration_tier: int = DEFAULT_DURATION_TIER,
    notes: str | None = None,
    seed: int | None = None,
    candidates: int = 1,
    shots: list[int] | None = None,
    max_polls: int = 20,
) -> dict:
    """全流程编排：素材 → 分析 → 分镜初稿 → 五要素落库 → 设计层 QC → 提交 → 逐镜产物。

    ``media_id`` 给定时跳过素材入库（引用素材库已有组图）；``files`` 给定时先
    上传入库。``designs``（``{镜号: 五要素草稿}``）在初稿之上逐镜覆盖落库。

    设计层 QC 在任何 GPU 提交前执行：有问题即抛 :class:`VideoClipsError`
    （checkpoint / auto 一视同仁——不合格的分镜不花卡）。确认环节
    （checkpoint 等用户目检）由 skill 层在 ``draft_storyboard`` /
    ``apply_shot_designs`` 之后、``submit_generation`` 之前自行插入。
    """
    if media_id is None:
        if not files:
            raise VideoClipsError("需要提供素材文件或已有 media_id")
        media = await ingest_materials(client, token, files, title=notes or "", description="video-clips 素材")
        media_id = str(media["id"])
    await ensure_analysis(client, token, media_id)

    media_detail = await client.get(f"/v1/media/{media_id}", token=token)
    probe_images = (media_detail.get("probe") or {}).get("images")
    if isinstance(probe_images, list):
        image_count = len(probe_images)
    else:
        image_count = int(media_detail.get("image_count") or 0)

    storyboard = await draft_storyboard(client, token, media_id, shot_count=shot_count, duration_tier=duration_tier, notes=notes)
    if designs:
        await apply_shot_designs(client, token, media_id, designs)
        storyboard = await read_storyboard(client, token, media_id)

    issues = qc_check_storyboard(storyboard, image_count=image_count or None)
    if issues:
        raise VideoClipsError("分镜设计层校验未通过，未提交生成", issues=issues)

    submission = await submit_generation(client, token, media_id, shots=shots, seed=seed, candidates=candidates)
    artifacts = await await_artifacts(client, token, submission.get("jobs") or [], max_polls=max_polls)
    return {"media_id": media_id, "storyboard": storyboard, "submission": submission, "artifacts": artifacts}
