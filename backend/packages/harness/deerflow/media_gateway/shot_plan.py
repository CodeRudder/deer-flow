"""``shot-plan`` 编排模块（参考视频 → 分镜制作包）。

MGW ``POST/GET /v1/media/{id}/plan``（+ ``GET …/plan/estimate``）对已入库、
已切分分析的视频做**确定性**分镜规划（零 GPU、可复现）：逐镜分析字段 →
模板生成每镜提示词（``shot-prompt-v2``）→ plan.json 制作包（prompt /
时长档 1/5/10/15 s / 拆镜告警 / 运镜语言）。

本模块是 skill ``video-clips`` / ``drama-production`` "参考视频模式"的可测
编排层：入库（已有上传链）→ ``make_plan`` →
:func:`plan_shots_to_storyboard` 把 plan 镜头表转成 skill 的五要素分镜
骨架（镜头数/每镜时长/运镜继承参考片），提示词内容部分由 agent 按 target
主题改写后再走既有落库/生成路径。无参考视频时 skill 走原 LLM 分镜路径，
不经本模块。

契约依据：网关 ``gateway/h3img/shot_plan.py::build_plan``（模板 v2）+
``docs/integration/api/media.md`` §plan。鉴权与 owner 规则同其它
``/v1/media`` 端点（用户 ``h3t_`` 令牌）。

决策（2026-11-03，用户已批）：
- plan → shots 映射 **1:1 为基线、agent 可增删**（>15 s 拆镜告警必须拆）；
- 参考视频首期仅支持用户上传（复用既有入库链，不做链接抓取）；
- ``estimate_plan`` 封装备用，SKILL.md 主流程不调（plan 本身已含 estimate）。
"""

from __future__ import annotations

from deerflow.media_gateway.client import MediaGatewayClient

#: 制作包模板版本（提示词模板 ``shot-prompt-v2``，动作前置 / 摇摆视线舍弃）。
TEMPLATE_VERSION = "shot-prompt-v2"

#: plan 镜头提示词里各行的固定前缀（模板 v2 拼装规则，见网关 shot_plan.py）。
_PREFIX_SUBJECT = "主体："
_PREFIX_BEATS = "动作节奏："
_PREFIX_SETTING = "场景："
_PREFIX_DIRECTION = "画面方向："
_PREFIX_EYELINE = "视线："


class ShotPlanError(Exception):
    """shot-plan 编排失败（媒体未分析 / 参数非法 / 网关错误）。"""


async def make_plan(
    client: MediaGatewayClient,
    token: str,
    media_id: str,
    *,
    aspect: str | None = None,
    short_edge: int | None = None,
    duration_policy: str = "up",
) -> dict:
    """制作包生成（落盘）：``POST /v1/media/{id}/plan``。确定性：同输入逐字节相同。"""
    body: dict = {"duration_policy": duration_policy}
    if aspect:
        body["aspect"] = aspect
    if short_edge is not None:
        body["short_edge"] = int(short_edge)
    return await client.post(f"/v1/media/{media_id}/plan", token=token, json=body)


async def get_plan(client: MediaGatewayClient, token: str, media_id: str) -> dict:
    """读取已生成的制作包：``GET /v1/media/{id}/plan``。"""
    return await client.get(f"/v1/media/{media_id}/plan", token=token)


async def estimate_plan(
    client: MediaGatewayClient,
    token: str,
    media_id: str,
    *,
    aspect: str | None = None,
    short_edge: int | None = None,
) -> dict:
    """只读估算（不落盘）：``GET /v1/media/{id}/plan/estimate``。备用，主流程不调。"""
    params: dict[str, object] = {}
    if aspect:
        params["aspect"] = aspect
    if short_edge is not None:
        params["short_edge"] = int(short_edge)
    return await client.get(f"/v1/media/{media_id}/plan/estimate", token=token, params=params or None)


def plan_shots_to_storyboard(plan: dict) -> dict:
    """plan.json 镜头表 → 五要素分镜骨架（``video_clips.build_shot_patch`` 的输入）。

    1:1 基线映射（agent 可增删，>15 s 拆镜告警必须拆）：

    - 运镜/景别/角度/场景（模板首句 + ``场景：``/``画面方向：``/``视线：`` 行）
      → ``scene``（落库进 storyboard ``camera``）；
    - ``主体：`` + ``动作节奏：`` 行 → ``action``（**内容主体由 agent 按
      target 主题改写**，保留动作结构与节奏）；
    - ``expression`` / ``dialogue``：plan 无对应分析，留空由 agent 按 target
      补写（五要素校验要求齐备）；
    - ``duration_s``（1/5/10/15 档）→ ``pacing``；
    - ``source``（参考片时间码）与 ``template_prompt``（模板 v2 原文）随镜
      透传，供 agent 改写时对照。

    返回 ``{"shots": [...], "style": {...}, "warnings": [...], "feasible": bool}``：
    ``style`` 是 plan 顶层众数聚合（影调/色调/景别等），作为跨镜一致性基线；
    ``warnings`` 是 plan 级告警（含 ``#N：…超过 15 s 档，需要拆镜``），
    ``feasible=False`` 表示存在阻断性告警，必须先拆镜/补分析再落库。
    """
    shots_out: list[dict] = []
    for shot in plan.get("shots") or []:
        lines = [ln.strip() for ln in str(shot.get("prompt") or "").splitlines() if ln.strip()]
        head = ""
        subject: list[str] = []
        beats: list[str] = []
        setting: list[str] = []
        direction: list[str] = []
        for ln in lines:
            if ln.startswith("[Shot"):
                # "[Shot 1] 景别，角度，运镜；光线；色调。" → 首句运镜语言
                # （分析为空时模板只给 "[Shot 1]。"，剥掉空句号不产空运镜行）
                head = ln.split("]", 1)[-1].strip()
                if head and not head.strip("。，；"):
                    head = ""
            elif ln.startswith(_PREFIX_SUBJECT):
                subject.append(ln)
            elif ln.startswith(_PREFIX_BEATS):
                beats.append(ln)
            elif ln.startswith(_PREFIX_SETTING):
                setting.append(ln)
            elif ln.startswith((_PREFIX_DIRECTION, _PREFIX_EYELINE)):
                direction.append(ln)

        scene = "；".join(part for part in (head, *setting, *direction) if part)
        action = "；".join(part for part in (*subject, *beats) if part)
        feasibility = shot.get("feasibility") or {}
        shots_out.append(
            {
                "index": shot.get("index"),
                "subject": "",
                "action": action,
                "expression": "",
                "dialogue": "",
                "scene": scene,
                "pacing": shot.get("duration_s"),
                "source": shot.get("source"),
                "keyframe": shot.get("keyframe"),
                "template_prompt": str(shot.get("prompt") or ""),
                "feasible": bool(feasibility.get("ok", True)),
                "warnings": [w for w in (feasibility.get("warnings") or []) if w],
            }
        )

    warnings_all = [str(w) for w in (plan.get("warnings") or [])]
    feasible = all(s["feasible"] for s in shots_out) if shots_out else False
    return {
        "shots": shots_out,
        "style": plan.get("style") or {},
        "warnings": warnings_all,
        "feasible": feasible,
    }
