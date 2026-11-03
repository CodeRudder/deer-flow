"""``t2i-series`` 编排模块（文生组图）。

design.md §8.3 / tasks.md M2-9：skill ``t2i-series`` 的可测编排层。skill 本体
是提示词（``skills/public/t2i-series/SKILL.md``），本模块承载"主题 → 组图设计
（plan）→ 逐张提交生成（同一 mode、每张独立 Idempotency-Key）→ 组图内一致性
校验 → QC 闭环（复用 qc.py 归因/修正，≤3 轮）→ 产物 URL 列表（deer-flow
代理前缀）"的调用序列，供单测与 skill 共同引用。

契约依据：``minimax-h3-gateway/docs/integration/api/images.md``——§1 能力发现
（``GET /v1/images/modes``）、§2 提交（``POST /v1/images/generations``，幂等键
同 key 同体复用 / 异体 409）、§4 作业状态机（``GET /v1/images/jobs/{id}``，
产物在 ``data[]``）。组图 = 多次单图提交的批，状态经
``POST /v1/images/series`` 登记进 MGW（最小建模），deer-flow 侧不建领域表。

组图设计（plan）是"每张图的画面/风格/连贯性约束"——LLM 在 skill 层起草，本
模块接受任意符合 :func:`check_plan` 的 dict（可注入的 plan 函数/数据结构）；
缺省 :func:`default_series_plan` 只是一副确定性的骨架草稿。QC 闭环复用
:mod:`deerflow.media_gateway.qc` 的归因/修正策略（prompt 缺陷 → 改提示词重写；
模型能力边界/随机性 → 换种子重抽），每张图自动迭代 ≤3 轮，仍失败即标记
``needs_human`` 并继续其余图（auto 语义；checkpoint 拦截在 skill 层）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from deerflow.media_gateway.client import MediaGatewayClient
from deerflow.media_gateway.providers import JOB_PATH_TEMPLATE, SUBMIT_PATH, proxy_media_url
from deerflow.media_gateway.qc import ACTION_NEEDS_HUMAN, ACTION_RESEED, remediation_plan

# 组图登记端点（images.md 语义的最小建模：批量=多次单图提交的账本）。
SERIES_PATH = "/v1/images/series"

# 组图规模（images.md §2.1 count 维度 1–8；组图逐张提交，同样限幅）。
SERIES_COUNT_MIN, SERIES_COUNT_MAX = 1, 8
DEFAULT_SERIES_COUNT = 4

# 每张图自动迭代上限（design.md §7.3：每镜/每图 ≤N 次，缺省 3）。
DEFAULT_MAX_ITERATIONS = 3

# 组图级风格参数：全部图必须共享（一致性校验的基准，design.md §8.2）。
STYLE_KEYS = ("mode", "aspect_ratio", "short_edge", "style_desc")
DEFAULT_STYLE: dict[str, Any] = {
    "mode": "h3-frame-std",
    "aspect_ratio": "16:9",
    "short_edge": 768,
    "style_desc": "统一插画风，柔和自然光，低饱和暖色调",
}


class T2iSeriesError(Exception):
    """t2i-series 编排失败（契约错误 / 一致性校验拦截）。"""

    def __init__(self, message: str, *, issues: list[str] | None = None) -> None:
        super().__init__(message)
        self.issues = issues or []


class GatewayLike(Protocol):
    """The slice of ``MediaGatewayClient`` the orchestrator depends on (DI)."""

    async def get(self, path: str, **kwargs: Any) -> dict: ...

    async def post(self, path: str, **kwargs: Any) -> dict: ...


# ── 1. 组图设计（plan）与设计层校验（零 GPU）──────────────────────────
def default_series_plan(theme: str, *, count: int = DEFAULT_SERIES_COUNT, style: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """确定性骨架草稿：skill 层的 LLM 会在此基础上逐张覆写画面/场景。"""
    if not SERIES_COUNT_MIN <= count <= SERIES_COUNT_MAX:
        raise T2iSeriesError(f"组图张数超出范围：{count}（{SERIES_COUNT_MIN}–{SERIES_COUNT_MAX}）")
    merged = {**DEFAULT_STYLE, **{k: v for k, v in (style or {}).items() if v is not None}}
    beats = ("开篇定场", "主体入场", "冲突/高潮", "收束定格")
    images = []
    for index in range(1, count + 1):
        beat = beats[min(index - 1, len(beats) - 1)]
        images.append(
            {
                "index": index,
                "subject": f"第{index}张（{beat}）：与主题一致的主体",
                "scene": f"第{index}张的场景/构图（与前后张承接）",
                "notes": "",
            }
        )
    return {"theme": theme, "style": dict(merged), "images": images}


def build_prompt(plan: Mapping[str, Any], image: Mapping[str, Any]) -> str:
    """把风格约束 + 主题 + 单张设计合成一条提示词。

    风格段（``style_desc``）对全组每张图都相同——组图一致性的第一约束；
    画面/场景段来自该张的设计字段。``prompt`` 字段存在时整条覆写（LLM 微调）。
    """
    override = str(image.get("prompt") or "").strip()
    if override:
        return override
    parts = [str(plan.get("style", {}).get("style_desc") or "").strip()]
    parts.append(f"主题：{plan.get('theme', '')}")
    subject = str(image.get("subject") or "").strip()
    if subject:
        parts.append(f"画面：{subject}")
    scene = str(image.get("scene") or "").strip()
    if scene:
        parts.append(f"场景：{scene}")
    notes = str(image.get("notes") or "").strip()
    if notes:
        parts.append(f"补充：{notes}")
    return "；".join(p for p in parts if p)


def build_generation_request(plan: Mapping[str, Any], image: Mapping[str, Any], *, seed: int | None = None) -> dict[str, Any]:
    """单张提交体（images.md §2.1）：全组同一 mode/比例/短边，``output=image``。"""
    style = plan.get("style") or {}
    payload: dict[str, Any] = {
        "mode": style.get("mode"),
        "prompt": build_prompt(plan, image),
        "aspect_ratio": style.get("aspect_ratio"),
        "short_edge": style.get("short_edge"),
        "output": "image",
    }
    if seed is not None:
        payload["seed"] = int(seed)
    return {k: v for k, v in payload.items() if v is not None}


def check_plan(plan: Mapping[str, Any]) -> list[str]:
    """设计层校验（零 GPU）：主题/风格/每张画面齐备、张数连续编号。"""
    issues: list[str] = []
    if not str(plan.get("theme") or "").strip():
        issues.append("组图主题为空")
    style = plan.get("style") or {}
    for key in ("mode", "style_desc"):
        if not str(style.get(key) or "").strip():
            issues.append(f"风格参数缺失：{key}")
    images = plan.get("images") or []
    if not images:
        issues.append("组图为空：至少需要一张图的设计")
        return issues
    indexes = [int(img.get("index") or 0) for img in images]
    if sorted(indexes) != list(range(1, len(images) + 1)):
        issues.append(f"张数/编号不连续：{sorted(indexes)}（应为 1..{len(images)}）")
    for img in images:
        idx = img.get("index", "?")
        if not str(img.get("subject") or "").strip():
            issues.append(f"第 {idx} 张缺少画面设计（subject）")
        if not str(img.get("scene") or "").strip():
            issues.append(f"第 {idx} 张缺少场景/构图设计（scene）")
    return issues


def check_consistency(
    plan: Mapping[str, Any],
    requests: Mapping[int, Mapping[str, Any]],
    *,
    completed_indexes: list[int] | None = None,
) -> list[str]:
    """组图内一致性校验：风格参数逐张一致 + 张数齐全（design.md §8.2）。"""
    issues: list[str] = []
    style = plan.get("style") or {}
    images = plan.get("images") or []
    for img in images:
        index = int(img.get("index") or 0)
        request = requests.get(index)
        if request is None:
            issues.append(f"第 {index} 张没有提交记录（张数不齐）")
            continue
        if request.get("mode") != style.get("mode"):
            issues.append(f"第 {index} 张 mode 与组图风格不一致：{request.get('mode')} ≠ {style.get('mode')}")
        if request.get("aspect_ratio") != style.get("aspect_ratio"):
            issues.append(f"第 {index} 张 aspect_ratio 与组图风格不一致：{request.get('aspect_ratio')} ≠ {style.get('aspect_ratio')}")
        if request.get("short_edge") != style.get("short_edge"):
            issues.append(f"第 {index} 张 short_edge 与组图风格不一致：{request.get('short_edge')} ≠ {style.get('short_edge')}")
        shared = str(style.get("style_desc") or "").strip()
        if shared and shared not in str(request.get("prompt") or ""):
            issues.append(f"第 {index} 张提示词缺少统一风格段（style_desc）")
    if completed_indexes is not None:
        expected = {int(img.get("index") or 0) for img in images}
        missing = sorted(expected - {int(i) for i in completed_indexes})
        if missing:
            issues.append(f"组图张数不齐：第 {missing} 张没有产物")
    return issues


# ── 2. 生成层 QC 归因（复用 qc.py 修正策略）───────────────────────────
_SEED_MARKERS = ("seed", "种子", "抖动", "伪影", "随机", "噪声", "flicker", "artifact", "noise")


def attribute_generation_failure(error: Mapping[str, Any]) -> dict[str, Any]:
    """把单张生成失败归因（design.md §7.3 三分法，图片版）。

    有 seed/随机性特征 → ``seed``（换种子重抽，不改设计）；其余按
    ``prompt`` 缺陷兜底（改提示词重写后重出）。图片生成没有托盘引用，
    ``citation`` 归因不适用。
    """
    code = str(error.get("code") or "")
    message = str(error.get("message") or "")
    text = f"{code} {message}".lower()
    if code == "seed" or any(marker in text for marker in _SEED_MARKERS):
        return {"attribution": "seed", "reasons": [f"失败信息指向随机性/模型边界：{message or code}"]}
    return {"attribution": "prompt", "reasons": [f"按提示词缺陷兜底：{message or code or 'unknown'}"]}


# ── 3. 幂等键与断点游标 ───────────────────────────────────────────────
def image_idempotency_key(plan: Mapping[str, Any], image: Mapping[str, Any], *, attempt: int, seed: int | None = None) -> str:
    """每张图每次尝试一个幂等键：同 key 同体重用，重试（新体）自然换 key。

    主题进摘要不进明文——HTTP 头（``Idempotency-Key``）只允许 ASCII。
    """
    theme_slug = hashlib.sha256(str(plan.get("theme") or "").encode("utf-8")).hexdigest()[:8]
    digest = hashlib.sha256(json.dumps({"index": image.get("index"), "subject": image.get("subject"), "scene": image.get("scene"), "seed": seed}, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
    return f"t2i-series:{theme_slug}:img{image.get('index', '?')}:v{attempt}:{digest}"


# ── 4. 编排器 ─────────────────────────────────────────────────────────
FixFn = Callable[[dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any] | None]]


class T2iSeriesOrchestrator:
    """文生组图编排：plan 门控 → 逐张提交（幂等）→ QC 闭环 → 一致性校验 → 登记。"""

    def __init__(
        self,
        client: GatewayLike,
        token: str,
        *,
        plan: Mapping[str, Any],
        seed: int | None = None,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        register: bool = True,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        poll_interval: float = 0.0,
        max_polls: int = 40,
    ) -> None:
        self._client = client
        self.token = token
        self.plan = json.loads(json.dumps(dict(plan)))  # deep copy：编排不改调用方数据
        self.seed = seed
        self.max_iterations = max_iterations
        self.register = register
        self._sleep = sleep
        self.poll_interval = poll_interval
        self.max_polls = max_polls
        self.history: list[dict[str, Any]] = []

    def _seed_for(self, index: int, attempt: int) -> int | None:
        """重抽（seed 归因）时换种子：base + 1000*attempt + 图号，确定性可复现。"""
        if self.seed is None:
            return None
        return int(self.seed) + 1000 * attempt + index

    async def _wait_job(self, job_id: str) -> dict[str, Any]:
        for attempt in range(self.max_polls):
            body = await self._client.get(JOB_PATH_TEMPLATE.format(job_id=job_id), token=self.token)  # type: ignore[arg-type]
            status = str(body.get("status") or "")
            if status not in ("queued", "running", "processing", "pending"):
                return body
            if attempt < self.max_polls - 1 and self.poll_interval > 0:
                await self._sleep(self.poll_interval)
        raise T2iSeriesError(f"作业 {job_id} 在 {self.max_polls} 次轮询内未到终态")

    async def generate_image(self, image: Mapping[str, Any]) -> dict[str, Any]:
        """单张闭环：提交 → 轮询 → 失败归因 → 修正（改词/换种子）→ 重试 ≤N 轮。

        返回 ``{index, status, job_id, iterations, url|error, attribution}``；
        ``url`` 已改写为 deer-flow 代理地址（``/api/media-gateway/file/…``）。
        """
        index = int(image.get("index") or 0)
        prompt_override: str | None = None
        last: dict[str, Any] = {}
        for attempt in range(1, self.max_iterations + 1):
            payload = build_generation_request(self.plan, image, seed=self._seed_for(index, attempt))
            if prompt_override:
                payload["prompt"] = prompt_override
            key = image_idempotency_key(self.plan, image, attempt=attempt, seed=payload.get("seed"))
            body = await self._client.post(SUBMIT_PATH, json=payload, token=self.token, idempotency_key=key)  # type: ignore[arg-type]
            job_id = str(body.get("id") or "")
            self.history.append({"phase": "submit", "index": index, "attempt": attempt, "job_id": job_id, "idempotency_key": key})
            last = {"index": index, "attempt": attempt, "job_id": job_id, "idempotency_key": key, "request": payload}
            result = await self._wait_job(job_id)
            status = str(result.get("status") or "")
            self.history.append({"phase": "job", "index": index, "attempt": attempt, "job_id": job_id, "status": status})
            if status == "completed":
                data = [item for item in result.get("data") or [] if isinstance(item, dict) and item.get("url")]
                last.update({"status": "completed", "iterations": attempt, "url": proxy_media_url(data[0]["url"]) if data else ""})
                return last
            attribution = attribute_generation_failure(result.get("error") or {})
            plan_r = remediation_plan(attribution, attempt=attempt, max_iterations=self.max_iterations)
            self.history.append({"phase": "remediation", "index": index, "attempt": attempt, "attribution": attribution, "plan": plan_r})
            last["attribution"] = attribution["attribution"]
            if plan_r["action"] == ACTION_NEEDS_HUMAN:
                break
            if plan_r["action"] != ACTION_RESEED:
                # prompt 归因 → 改提示词重写（LLM 注入 fix_fn；缺省原样重出）。
                applied: dict[str, Any] | None = None
                if self._fix_fn is not None:
                    applied = await self._fix_fn(plan_r, {"index": index, "attempt": attempt, "error": result.get("error"), "prompt": payload.get("prompt")})
                if applied and str(applied.get("prompt") or "").strip():
                    prompt_override = str(applied["prompt"])
        last.setdefault("status", "needs_human")
        last.update({"status": "needs_human", "iterations": self.max_iterations, "error": "已达每张最大迭代次数，需人工介入"})
        return last

    async def run(self, fix_fn: FixFn | None = None) -> dict[str, Any]:
        """全流程编排，返回 ``{plan, images, consistency, series_id, passed}``。"""
        self._fix_fn = fix_fn  # type: ignore[attr-defined]
        issues = check_plan(self.plan)
        if issues:
            raise T2iSeriesError("组图设计层校验未通过，未提交生成", issues=issues)

        images = [img for img in self.plan.get("images") or []]
        requests: dict[int, dict[str, Any]] = {}
        results: list[dict[str, Any]] = []
        for image in sorted(images, key=lambda img: int(img.get("index") or 0)):
            result = await self.generate_image(image)
            requests[int(image["index"])] = result.get("request") or build_generation_request(self.plan, image)
            results.append({k: v for k, v in result.items() if k != "request"})

        completed = [r["index"] for r in results if r["status"] == "completed"]
        consistency = check_consistency(self.plan, requests, completed_indexes=completed)

        series_id: str | None = None
        if self.register:
            body = await self._client.post(  # type: ignore[arg-type]
                SERIES_PATH,
                json={
                    "theme": self.plan.get("theme"),
                    "style": self.plan.get("style"),
                    "items": [{"index": r["index"], "job_id": r.get("job_id"), "idempotency_key": r.get("idempotency_key")} for r in results],
                },
                token=self.token,
            )
            series_id = body.get("id")

        passed = len(completed) == len(images) and not consistency
        return {"plan": self.plan, "images": results, "consistency": {"passed": not consistency, "issues": consistency}, "series_id": series_id, "passed": passed}

    _fix_fn: FixFn | None


async def run_t2i_series(
    client: MediaGatewayClient,
    token: str,
    *,
    plan: Mapping[str, Any],
    seed: int | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    register: bool = True,
    fix_fn: FixFn | None = None,
    poll_interval: float = 0.0,
    max_polls: int = 40,
) -> dict[str, Any]:
    """一次性编排入口（见 :class:`T2iSeriesOrchestrator`）。"""
    orchestrator = T2iSeriesOrchestrator(
        client,
        token,
        plan=plan,
        seed=seed,
        max_iterations=max_iterations,
        register=register,
        poll_interval=poll_interval,
        max_polls=max_polls,
    )
    return await orchestrator.run(fix_fn)
