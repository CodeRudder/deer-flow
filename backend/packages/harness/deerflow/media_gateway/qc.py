"""QC iteration closed loop for the drama-production workflow (M2-8).

Implements the 检验修正迭代闭环 of design.md §7.3 on top of the MGW drama API
(``minimax-h3-gateway/docs/integration/api/drama.md``). Three layers:

1. **设计层校验** (zero GPU): ``prompt_preview`` from MGW plus a configurable
   five-element completeness checklist (人物动作 / 表情 / 对话 / 场景 / 时长节奏)
   evaluated against the chapter's shot documents.
2. **生成层 QC**: after generation, read ``GET …/clips/{id}/qc`` and attribute
   every issue to one of three causes — ``prompt`` (design defect),
   ``citation`` (asset tray reference wrong) or ``seed`` (model capability
   boundary / stochastic take).
3. **修正策略**: map the attribution to a concrete remediation (revise the shot
   design fields / fix the tray citations / re-take with a different seed),
   retrying at most ``max_iterations`` times per clip before returning
   ``needs_human``.

Everything is pure functions + dependency-injected async gateway access: the
orchestrator :class:`DramaQCClosedLoop` takes a
:class:`~deerflow.media_gateway.client.MediaGatewayClient` (or anything with
the same ``get``/``post``/``patch`` surface), so tests drive it against
:class:`~deerflow.media_gateway.mock_server.MockMediaGateway`.

Iteration records — design issues, QC bodies, attributions, remediations —
are appended as plain dicts to :attr:`DramaQCClosedLoop.history`; persistence
(sidecar cursor) is the caller's decision, matching design.md §7.3 (deer-flow
keeps no domain tables; MGW is the source of truth).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

from deerflow.media_gateway.client import MediaGatewayClient, MediaGatewayError

# Attributions (design.md §7.3: prompt 缺陷 / 资产引用错 / 模型能力边界).
ATTR_PROMPT = "prompt"
ATTR_CITATION = "citation"
ATTR_SEED = "seed"

# Remediation actions per attribution + the give-up signal.
ACTION_REVISE_PROMPT = "revise_prompt"
ACTION_FIX_CITATIONS = "fix_citations"
ACTION_RESEED = "reseed"
ACTION_NEEDS_HUMAN = "needs_human"

# Score dimensions that indicate a prompt/design defect when low.
_PROMPT_SCORE_DIMS = ("identity", "space")
_PROMPT_SCORE_THRESHOLD = 0.8
# Markers in an issue description that point at a tray/citation problem.
_CITATION_MARKERS = ("<picture", "图片", "tray", "托盘", "引用", "token", "参考图")
# Markers that point at the model/stochastic take rather than the design.
_SEED_MARKERS = ("seed", "种子", "抖动", "闪烁", "伪影", "flicker", "artifact", "motion", "动作变形")

STATUS_PASSED = "passed"
STATUS_DESIGN_FAILED = "design_failed"
STATUS_NEEDS_HUMAN = "needs_human"


class GatewayLike(Protocol):
    """The slice of ``MediaGatewayClient`` the loop depends on (DI seam)."""

    async def get(self, path: str, **kwargs: Any) -> dict: ...

    async def post(self, path: str, **kwargs: Any) -> dict: ...


# ── drama error helpers (drama.md §1: {"detail": {"error": …}} envelope) ──
def parse_drama_error(exc: MediaGatewayError) -> dict[str, Any]:
    """Extract ``{"code", …}`` from a drama structured error, else ``{}``.

    The HTTP client surfaces the raw body text as ``exc.message`` for the
    drama ``{"detail": …}`` envelope (it only unwraps the top-level
    ``{"error": …}`` envelope used by the generation routes).
    """
    try:
        body = json.loads(exc.message)
    except ValueError:
        return {}
    if isinstance(body, dict) and isinstance(body.get("detail"), dict):
        error = body["detail"].get("error")
        if isinstance(error, dict):
            return error
    return {}


# ── 1. design-layer checks (zero GPU) ────────────────────────────────────
DesignCheck = Callable[[Mapping[str, Any]], "DesignIssue | None"]


class DesignIssue:
    """One design-layer finding against a shot (or the clip as a whole)."""

    __slots__ = ("element", "shot_id", "severity", "hint")

    def __init__(self, element: str, shot_id: str, severity: str, hint: str) -> None:
        self.element = element
        self.shot_id = shot_id
        self.severity = severity  # "block" | "warn"
        self.hint = hint

    def to_dict(self) -> dict[str, str]:
        return {"element": self.element, "shot_id": self.shot_id, "severity": self.severity, "hint": self.hint}


def _has_expression(shot: Mapping[str, Any]) -> bool:
    text = f"{shot.get('prompt_zh') or ''}{shot.get('prompt_en') or ''}{shot.get('expression') or ''}"
    markers = ("表情", "神情", "笑", "皱眉", "瞪", "惊", "怒", "smil", "express", "frown", "gaze")
    return any(m in text.lower() for m in markers)


def _default_expression_check(shot: Mapping[str, Any]) -> DesignIssue | None:
    """表情 is desirable but hard to guarantee → warn, never block."""
    if _has_expression(shot):
        return None
    return DesignIssue("expression", str(shot.get("id") or ""), "warn", "镜头未描述表情/情绪，建议补充（warning，不拦截）")


#: Default five-element checklist (design.md §7.2). Order matters: the first
#: failing check per element wins. ``None`` return = element satisfied.
DEFAULT_FIVE_ELEMENT_CHECKLIST: tuple[tuple[str, DesignCheck], ...] = (
    (
        "action",
        lambda shot: None
        if (str(shot.get("prompt_zh") or "").strip() or str(shot.get("prompt_en") or "").strip())
        else DesignIssue("action", str(shot.get("id") or ""), "block", "镜头正文为空（人物动作/画面描述缺失）"),
    ),
    ("expression", _default_expression_check),
    (
        "dialogue",
        lambda shot: None
        if all(str(line.get("speaker") or "").strip() for line in shot.get("dialogue") or [])
        else DesignIssue("dialogue", str(shot.get("id") or ""), "block", "台词条目缺 speaker（保存链也会拒绝 dialogue_unassigned）"),
    ),
    (
        "scene",
        lambda shot: None
        if (shot.get("scene_id") or shot.get("camera") or shot.get("size") or _token_numbers_of(shot))
        else DesignIssue("scene", str(shot.get("id") or ""), "block", "缺场景要素：场景引用（scene_id/<Picture N>）或机位/景别均未填"),
    ),
    (
        "rhythm",
        lambda shot: None
        if float(shot.get("duration_s") or 0) > 0
        else DesignIssue("rhythm", str(shot.get("id") or ""), "block", "时长未填（节奏要素缺失，也无法计入片段预算）"),
    ),
)


def _token_numbers_of(shot: Mapping[str, Any]) -> bool:
    tokens = shot.get("tray_tokens_used") or []
    return bool(tokens)


def check_five_elements(shot: Mapping[str, Any], checklist: Sequence[tuple[str, DesignCheck]] | None = None) -> list[DesignIssue]:
    """Run the configurable checklist over one shot document."""
    checks = checklist if checklist is not None else DEFAULT_FIVE_ELEMENT_CHECKLIST
    issues = []
    for element, check in checks:
        issue = check(shot)
        if issue is not None:
            issue.element = issue.element or element
            issues.append(issue)
    return issues


def evaluate_preview_checks(preview: Mapping[str, Any]) -> list[DesignIssue]:
    """Design findings from an MGW ``prompt_preview`` body (clip level)."""
    checks = preview.get("checks") or {}
    issues: list[DesignIssue] = []
    if not checks.get("token_manifest_match", True):
        for violation in checks.get("violations") or []:
            issues.append(DesignIssue("citation", str(violation.get("shot_id") or ""), "block", f"编外托盘引用 {violation.get('token')}（<Picture N> 超出托盘编号范围）"))
    if checks.get("negative_wording") == "fail":
        for hit in checks.get("negative_hits") or []:
            issues.append(DesignIssue("prompt", "", "block", f"负向措辞『{hit.get('match')}』：{hit.get('suggest')}"))
    if checks.get("scene_link_dangling"):
        issues.append(DesignIssue("citation", "", "block", "scene_slot.media_id 指向的图不在托盘里（场景链接悬空）"))
    if checks.get("scene_slot_missing"):
        issues.append(DesignIssue("scene", "", "warn", "设置了 scene_slot 但 note 为空"))
    for unused in checks.get("unused_tray_refs") or []:
        issues.append(DesignIssue("citation", "", "warn", f"托盘图未被子镜正文引用：{unused}"))
    return issues


def design_verdict(
    preview: Mapping[str, Any],
    shots: Sequence[Mapping[str, Any]],
    *,
    checklist: Sequence[tuple[str, DesignCheck]] | None = None,
) -> dict[str, Any]:
    """Combine ``prompt_preview`` findings with the five-element checklist.

    Returns ``{"passed", "issues": [dict]}``; any ``block`` issue fails the
    gate — a clip never reaches GPU generation with a failing design layer.
    """
    issues = evaluate_preview_checks(preview)
    for shot in shots:
        issues.extend(check_five_elements(shot, checklist))
    passed = not any(issue.severity == "block" for issue in issues)
    return {"passed": passed, "issues": [issue.to_dict() for issue in issues]}


# ── 2. generation-layer QC attribution ───────────────────────────────────
def attribute_qc(qc: Mapping[str, Any]) -> dict[str, Any]:
    """Attribute a QC body (``GET …/qc``) to prompt / citation / seed.

    Deterministic precedence (documented, testable):

    1. any issue mentioning tray/citation markers → ``citation`` (most
       specific: a wrong ``<Picture N>`` reference explains every symptom);
    2. ``must_fix_storyboard`` or a low identity/space score or issues
       mentioning the design/consistency → ``prompt``;
    3. seed/stochastic markers (or M/L-only motion issues) → ``seed``;
    4. otherwise fall back to ``prompt`` (the safest fix loop).
    """
    issues = [str(issue.get("desc") or "") for issue in qc.get("issues") or []]
    lowered = [text.lower() for text in issues]
    reasons: list[str] = []

    for text, lower in zip(issues, lowered):
        if any(marker in lower for marker in _CITATION_MARKERS):
            return {"attribution": ATTR_CITATION, "reasons": [f"issue 提及托盘/引用标记：{text}"]}

    scores = qc.get("scores") or {}
    low = [dim for dim in _PROMPT_SCORE_DIMS if isinstance(scores.get(dim), (int, float)) and scores[dim] < _PROMPT_SCORE_THRESHOLD]
    if qc.get("must_fix_storyboard"):
        reasons.append("must_fix_storyboard=true（存在 S 级问题，须回改分镜）")
    if low:
        reasons.append(f"一致性维度得分过低：{', '.join(f'{dim}={scores[dim]}' for dim in low)}")
    for text in issues:
        if any(marker in text for marker in ("一致", "脸", "身份", "穿模", "空间", "identit", "consisten")):
            reasons.append(f"issue 指向设计与一致性：{text}")
    if reasons:
        return {"attribution": ATTR_PROMPT, "reasons": reasons}

    for text, lower in zip(issues, lowered):
        if any(marker in lower for marker in _SEED_MARKERS):
            return {"attribution": ATTR_SEED, "reasons": [f"issue 指向随机性/模型边界：{text}"]}

    if not issues:
        return {"attribution": ATTR_PROMPT, "reasons": ["QC 状态非通过但无结构化 issues，按 prompt 缺陷兜底"]}
    return {"attribution": ATTR_PROMPT, "reasons": [f"未命中引用/种子特征，按 prompt 缺陷兜底：{issues[0]}"]}


def remediation_plan(attribution: Mapping[str, Any], *, attempt: int, max_iterations: int = 3) -> dict[str, Any]:
    """Map an attribution to "what to change", honouring the retry budget.

    ``attempt`` is 1-based. Beyond ``max_iterations`` the loop must escalate:
    checkpoint 模式回交用户 / auto 模式标记跳过 → ``needs_human``.
    """
    kind = str(attribution.get("attribution") or ATTR_PROMPT)
    if attempt >= max_iterations:
        return {
            "action": ACTION_NEEDS_HUMAN,
            "attribution": kind,
            "reason": f"已达每镜最大迭代次数 {max_iterations}，需人工介入",
        }
    action = {
        ATTR_PROMPT: ACTION_REVISE_PROMPT,
        ATTR_CITATION: ACTION_FIX_CITATIONS,
        ATTR_SEED: ACTION_RESEED,
    }.get(kind, ACTION_REVISE_PROMPT)
    hint = {
        ACTION_REVISE_PROMPT: "改镜头设计字段（prompt_zh/prompt_en 五要素），先归档旧版本再重出",
        ACTION_FIX_CITATIONS: "修托盘引用：核对 ref_tray 编号与 <Picture N> token（PATCH clips 全量回传触发重映射）",
        ACTION_RESEED: "同设计换种子重抽（seedmatch /score 择优），不改分镜",
    }[action]
    return {"action": action, "attribution": kind, "attempt": attempt, "max_iterations": max_iterations, "hint": hint}


# ── 3. the closed-loop orchestrator ──────────────────────────────────────
FixFn = Callable[[dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any] | None]]


class DramaQCClosedLoop:
    """Per-clip QC iteration loop: design gate → generate → QC → fix → retry.

    Parameters are dependency-injected; ``sleep`` replaces ``asyncio.sleep``
    in tests. ``history`` accumulates one dict per iteration
    (design/QC/attribution/remediation) — the caller persists it if desired.
    """

    def __init__(
        self,
        client: GatewayLike,
        *,
        drama_id: str,
        chapter_id: str,
        clip_id: str,
        token: str | None = None,
        max_iterations: int = 3,
        checklist: Sequence[tuple[str, DesignCheck]] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        poll_interval: float = 0.0,
        poll_max_attempts: int = 10,
    ) -> None:
        self._client = client
        self.drama_id = drama_id
        self.chapter_id = chapter_id
        self.clip_id = clip_id
        self.token = token
        self.max_iterations = max_iterations
        self.checklist = checklist
        self._sleep = sleep
        self.poll_interval = poll_interval
        self.poll_max_attempts = poll_max_attempts
        self.history: list[dict[str, Any]] = []

    # ── MGW paths ─────────────────────────────────────────────────────
    def _clips_base(self) -> str:
        return f"/v1/drama/{self.drama_id}/chapters/{self.chapter_id}"

    def _clip_path(self, suffix: str = "") -> str:
        return f"{self._clips_base()}/clips/{self.clip_id}{suffix}"

    async def _get(self, path: str) -> dict:
        return await self._client.get(path, token=self.token)  # type: ignore[arg-type]

    # ── design layer ──────────────────────────────────────────────────
    async def check_design(self) -> dict[str, Any]:
        """Design-layer gate: ``prompt_preview`` + five-element checklist.

        Reads the clip's shots via the chapter shot list (``clip_id`` filter)
        and the zero-GPU preview; never touches a GPU endpoint.
        """
        preview = await self._get(self._clip_path("/prompt_preview"))
        shots_body = await self._client.get(f"{self._clips_base()}/shots", params={"clip_id": self.clip_id}, token=self.token)  # type: ignore[arg-type]
        verdict = design_verdict(preview, shots_body.get("items") or [], checklist=self.checklist)
        self.history.append({"phase": "design", "clip_id": self.clip_id, "passed": verdict["passed"], "issues": verdict["issues"]})
        return verdict

    # ── generation layer ──────────────────────────────────────────────
    async def generate_and_wait(self) -> dict[str, Any]:
        """Submit the clip (single lease) and poll until QC is readable."""
        await self._client.post(self._clip_path("/generate"), token=self.token)  # type: ignore[arg-type]
        for _ in range(self.poll_max_attempts):
            status = await self._get(self._clip_path("/generate"))
            if str(status.get("state")) != "generating":
                return await self._get(self._clip_path("/qc"))
            if self.poll_interval > 0:
                await self._sleep(self.poll_interval)
        raise MediaGatewayError(status=0, code="task_timeout", message=f"clip {self.clip_id} stuck in generating after {self.poll_max_attempts} polls")

    # ── the loop ──────────────────────────────────────────────────────
    async def run(self, fix_fn: FixFn) -> dict[str, Any]:
        """Run the full closed loop for one clip.

        ``fix_fn(action_plan, qc_body)`` applies the remediation decided by
        :func:`remediation_plan` (patch shots / patch the tray / reseed) and
        returns a description dict of what changed (recorded in history).

        Returns ``{"status": passed|design_failed|needs_human, "iterations": n,
        "qc": last_qc_body}``.
        """
        verdict = await self.check_design()
        if not verdict["passed"]:
            return {"status": STATUS_DESIGN_FAILED, "iterations": 0, "issues": verdict["issues"], "qc": None}

        attempt = 0
        qc: dict[str, Any] = {}
        while attempt < self.max_iterations:
            attempt += 1
            qc = await self.generate_and_wait()
            attribution = attribute_qc(qc)
            self.history.append({"phase": "qc", "clip_id": self.clip_id, "attempt": attempt, "qc": qc, "attribution": attribution})
            if str(qc.get("state")) == "qc_pass":
                return {"status": STATUS_PASSED, "iterations": attempt, "qc": qc}
            if bool(qc.get("state") != "pending"):
                # Contract (drama.md §15.1 check ③): any resubmission after a QC
                # verdict requires archiving the current version first.
                clip_doc = await self._get(self._clip_path())
                await self._client.post(self._clip_path("/archive"), json={"version": clip_doc.get("version"), "reason": f"qc {attempt}: {attribution['attribution']}"}, token=self.token)  # type: ignore[arg-type]
            plan = remediation_plan(attribution, attempt=attempt, max_iterations=self.max_iterations)
            self.history.append({"phase": "remediation", "clip_id": self.clip_id, "attempt": attempt, "plan": plan})
            if plan["action"] == ACTION_NEEDS_HUMAN:
                break
            applied = await fix_fn(plan, qc)
            self.history.append({"phase": "fix", "clip_id": self.clip_id, "attempt": attempt, "applied": applied})
            verdict = await self.check_design()
            if not verdict["passed"]:
                return {"status": STATUS_DESIGN_FAILED, "iterations": attempt, "issues": verdict["issues"], "qc": qc}
        return {"status": STATUS_NEEDS_HUMAN, "iterations": attempt, "qc": qc}


async def ensure_design_gate(
    client: MediaGatewayClient,
    *,
    drama_id: str,
    chapter_id: str,
    clip_id: str,
    token: str | None = None,
    checklist: Sequence[tuple[str, DesignCheck]] | None = None,
) -> dict[str, Any]:
    """One-shot design-layer check (零 GPU) without the generation loop."""
    loop = DramaQCClosedLoop(client, drama_id=drama_id, chapter_id=chapter_id, clip_id=clip_id, token=token, checklist=checklist)
    return await loop.check_design()
