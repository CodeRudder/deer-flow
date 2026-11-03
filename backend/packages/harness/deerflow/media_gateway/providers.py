"""Image/video generation provider adapter for the Media Gateway (MGW).

Implements the M2-1 / M2-2 provider contract (design.md §4/§4.1) on top of
:class:`~deerflow.media_gateway.client.MediaGatewayClient`:

- capability discovery: MGW's capability API is mapped into the provider
  capability shape used by the generation registries (model matrix / ratio
  enum / parameter domains). A parameter domain MGW does not expose for a
  model is reported as unsupported, so the existing generation skills reject
  it locally instead of sending it to the gateway;
- submission: ``POST`` with an ``Idempotency-Key`` and the **current user's**
  ``h3t_`` token (never the admin token — owner isolation, design.md §5.2);
- polling: MGW job status is normalised to ``pending`` / ``succeeded`` /
  ``failed``;
- artifact URLs are rewritten to the deer-flow proxy prefix so ``<img src>``
  works without an Authorization header (the proxy route itself lives in the
  gateway layer).

Token wiring: the registry layer has no request/user context, so the per-user
token is obtained through an injectable async ``token_provider`` callback.
The default wiring — ``MediaGatewayProvisioner.get_token`` bound to the
current request user via ``get_effective_user_id()`` — is connected at the
gateway/runtime layer in M2-3 and later. When ``token_provider`` is ``None``
the client's admin token would be used; callers must not rely on that for
generation requests.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from deerflow.media_gateway.client import MediaGatewayClient, MediaGatewayError
from deerflow.media_gateway.settings import get_media_gateway_settings

logger = logging.getLogger(__name__)

# deer-flow proxy prefix for MGW media artifacts. The route
# ``/api/media-gateway/file/<mgw path>`` is served by the gateway layer (M2
# follow-up): it forwards to MGW with the requesting user's token, so browser
# clients never need an Authorization header on media URLs.
MEDIA_GATEWAY_FILE_PROXY_PREFIX = "/api/media-gateway/file"

# MGW API paths (contract source of truth:
# minimax-h3-gateway/docs/integration/api/images.md). Capability discovery is
# ``GET /v1/images/modes`` for both media kinds (the ``output`` request
# parameter selects image vs video on the same submit endpoint); polling reads
# the full job body from ``GET /v1/images/jobs/{id}``.
MODES_PATH = "/v1/images/modes"
SUBMIT_PATH = "/v1/images/generations"
JOB_PATH_TEMPLATE = "/v1/images/jobs/{job_id}"
# R2 extras (images.md §6/§8/§9/§7): multi-mode compare, per-job assessment
# read, cancel/delete, and the gateway-level shape lock.
COMPARE_PATH = "/v1/images/compare"
ASSESSMENT_PATH_TEMPLATE = "/v1/images/jobs/{job_id}/assessment"
SHAPE_LOCK_PATH = "/v1/shape-lock"

# Backwards-compatible aliases (pre-contract-fix names).
IMAGE_CAPABILITIES_PATH = MODES_PATH
VIDEO_CAPABILITIES_PATH = MODES_PATH
IMAGE_SUBMIT_PATH = SUBMIT_PATH
VIDEO_SUBMIT_PATH = SUBMIT_PATH
TASK_PATH_TEMPLATE = JOB_PATH_TEMPLATE

# Normalised job states, mirroring the skill-side provider contract.
STATUS_PENDING = "pending"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
# A job the caller cancelled through the wait loop's cancel_check signal
# (R2-2): the provider DELETEs the queued job and reports this state instead of
# raising, so skill orchestration can branch on "user changed their mind".
STATUS_CANCELLED = "cancelled"

# MGW status strings that map onto the normalised states. Anything unknown is
# treated as pending so a future MGW status cannot fail a running job.
_PENDING_STATUSES = {"pending", "queued", "running", "processing"}
_SUCCEEDED_STATUSES = {"succeeded", "success", "completed"}
_FAILED_STATUSES = {"failed", "error", "cancelled", "canceled"}

TokenProvider = Callable[[], Awaitable[str]]
# Injected into the wait loop; returning True means "the caller wants out" —
# the provider then DELETEs the job (images.md §9) and reports cancelled.
CancelCheck = Callable[[], Awaitable[bool]]


def proxy_media_url(url_or_path: str) -> str:
    """Rewrite an MGW artifact URL/path to the deer-flow proxy address.

    Convention: ``/api/media-gateway/file/<mgw原样路径>`` — everything after
    the proxy prefix is the MGW-side path, forwarded verbatim by the gateway
    route with the requesting user's token.
    """
    if not url_or_path:
        return url_or_path
    if url_or_path.startswith(("http://", "https://")):
        # Strip scheme+host, keep the MGW path (and query) verbatim.
        path = url_or_path.split("://", 1)[1].split("/", 1)
        mgw_path = f"/{path[1]}" if len(path) == 2 else "/"
    else:
        mgw_path = url_or_path if url_or_path.startswith("/") else f"/{url_or_path}"
    return f"{MEDIA_GATEWAY_FILE_PROXY_PREFIX}{mgw_path}"


@dataclass(frozen=True)
class ModelCapability:
    """One MGW model's capability matrix in provider-contract form.

    A field that is empty/``None`` means "MGW does not expose this parameter
    domain for the model" — the parameter must be rejected locally.
    """

    name: str
    display_name: str | None = None
    ratios: tuple[str, ...] = ()
    resolutions: tuple[str, ...] = ()
    duration_range: tuple[int, int] | None = None
    supported_params: frozenset[str] = frozenset({"prompt"})
    # images.md §1 mode metadata: an unavailable tier 400s at submit time (the
    # registry rejects it), and ``needs_reference`` tiers (i2i / ref2va /
    # wan-i2v ...) must be submitted with a reference image — the local
    # parameter check enforces both before any HTTP call (R2-4).
    available: bool = True
    needs_reference: bool = False
    engine_task: str | None = None


class UnsupportedParameterError(ValueError):
    """Raised locally when a parameter is outside the model's MGW capability.

    Subclasses ``ValueError`` so the existing skill-side error handling treats
    it as a pre-submission rejection (the provider was never called).
    """


def _as_tuple(values: Any) -> tuple[str, ...]:
    if isinstance(values, str) or not isinstance(values, (list, tuple)):
        return ()
    return tuple(str(v) for v in values)


def _duration_range(values: Any) -> tuple[int, int] | None:
    if isinstance(values, dict):
        low, high = values.get("min"), values.get("max")
        if isinstance(low, (int, float)) and isinstance(high, (int, float)):
            return (int(low), int(high))
        return None
    if isinstance(values, (list, tuple)) and len(values) == 2 and all(isinstance(v, (int, float)) for v in values):
        return (int(values[0]), int(values[1]))
    return None


def map_capabilities(body: dict, *, base_params: frozenset[str] = frozenset({"prompt"})) -> dict[str, ModelCapability]:
    """Map an MGW capability response into ``{model_name: ModelCapability}``.

    Accepts either ``{"models": [...]}`` or a bare list wrapped by the client
    as ``{"data": [...]}``; per-model keys fall back defensively (``name`` or
    ``id``; ``ratios`` or ``aspect_ratios``).
    """
    models = None
    if isinstance(body, dict):
        # Real MGW contract: ``GET /v1/images/modes`` returns
        # ``{default_mode, baseline_modes, modes[]}`` (images.md §1). The
        # legacy ``models`` / ``data`` container keys stay accepted for
        # compatibility with older gateways.
        for key in ("modes", "models", "data"):
            if isinstance(body.get(key), list):
                models = body[key]
                break
    capabilities: dict[str, ModelCapability] = {}
    for entry in models or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or entry.get("id") or entry.get("model")
        if not isinstance(name, str) or not name:
            continue
        params = set(base_params)
        params.update(_as_tuple(entry.get("supported_params")))
        params.update(_as_tuple(entry.get("params")))
        # modes[] entries carry default parameters as a dict
        # (``{steps, short_edge, duration_seconds}``, images.md §1).
        mode_params = entry.get("params")
        # Reference-capable tiers come in three contract spellings: the legacy
        # ``supports_reference`` flag, the real ``needs_reference`` flag
        # (mandatory reference — images.md §1) and the comfy-family
        # ``accepts_reference_images`` / ``capabilities.reference_images``
        # (optional reference — wan-i2v / wan-vace / ltx-i2v).
        needs_reference = bool(entry.get("needs_reference"))
        accepts_reference = bool(entry.get("supports_reference") or entry.get("accepts_reference_images") or (isinstance(entry.get("capabilities"), dict) and entry["capabilities"].get("reference_images")))
        if needs_reference or accepts_reference:
            params.update({"image", "reference_images"})
        capabilities[name] = ModelCapability(
            name=name,
            display_name=str(entry["display_name"]) if isinstance(entry.get("display_name"), str) else None,
            ratios=_as_tuple(entry.get("ratios") or entry.get("aspect_ratios")),
            resolutions=_as_tuple(entry.get("resolutions") or entry.get("sizes")),
            duration_range=(
                _duration_range(entry.get("duration_range") or entry.get("durations"))
                or (
                    # Derive the range from the mode's default duration
                    # (tiers 1/5/10/15 s, images.md §3.2): 1 s .. default.
                    (1, int(mode_params["duration_seconds"])) if isinstance(mode_params, dict) and isinstance(mode_params.get("duration_seconds"), (int, float)) else None
                )
            ),
            supported_params=frozenset(params),
            available=bool(entry.get("available", True)),
            needs_reference=needs_reference,
            engine_task=str(entry["engine_task"]) if isinstance(entry.get("engine_task"), str) else None,
        )
    return capabilities


#: Parameters the merged-tier capability list does not declare but the
#: gateway accepts anyway; they pass the "unsupported" check and are instead
#: validated locally (see :func:`check_params_supported`).
_LOCALLY_VALIDATED_PARAMS = frozenset({"short_edge", "aspect_ratio"})
#: short_edge bounds (canvas short edge in px; 512 etc. are valid).
_SHORT_EDGE_MIN = 256
_SHORT_EDGE_MAX = 2048


def _validate_short_edge(model_name: str, value: Any) -> int:
    """Validate a caller-supplied ``short_edge``; return it as ``int``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UnsupportedParameterError(f"model={model_name} on media_gateway: short_edge must be a number, got {value!r}")
    if isinstance(value, float) and not value.is_integer():
        raise UnsupportedParameterError(f"model={model_name} on media_gateway: short_edge must be an integer, got {value!r}")
    short_edge = int(value)
    if not (_SHORT_EDGE_MIN <= short_edge <= _SHORT_EDGE_MAX):
        raise UnsupportedParameterError(f"model={model_name} on media_gateway: short_edge must be an integer between {_SHORT_EDGE_MIN} and {_SHORT_EDGE_MAX}, got {short_edge}")
    return short_edge


def check_params_supported(capability: ModelCapability, params: dict[str, Any]) -> None:
    """Reject parameters outside the model's capability before any HTTP call."""
    # R2-4: needs_reference tiers (i2i / ref2va, images.md §1) hard-require a
    # reference image; reject locally so the skill layer refuses a
    # no-reference submission instead of round-tripping a 400.
    if capability.needs_reference and not any(params.get(key) for key in ("reference_images", "reference_image", "image")):
        raise UnsupportedParameterError(f"model={capability.name} on media_gateway requires a reference image (parameter domain reference_images is mandatory for needs_reference tiers)")
    unsupported = sorted(k for k, v in params.items() if v is not None and k not in capability.supported_params and k not in _LOCALLY_VALIDATED_PARAMS)
    if unsupported:
        raise UnsupportedParameterError(f"model={capability.name} on media_gateway does not support parameter(s): {', '.join(unsupported)}")
    if capability.ratios and isinstance(params.get("ratio"), str) and params["ratio"] not in capability.ratios:
        raise UnsupportedParameterError(f"model={capability.name} on media_gateway does not support ratio={params['ratio']!r} (supported: {', '.join(capability.ratios)})")
    # short_edge / aspect_ratio: always accepted (the merged-tier capability
    # list only carries ``prompt``), validated locally before any HTTP call.
    if params.get("short_edge") is not None:
        _validate_short_edge(capability.name, params["short_edge"])
    aspect_ratio = params.get("aspect_ratio")
    if aspect_ratio is not None and capability.ratios and aspect_ratio not in capability.ratios:
        raise UnsupportedParameterError(f"model={capability.name} on media_gateway does not support aspect_ratio={aspect_ratio!r} (supported: {', '.join(capability.ratios)})")


def normalize_status(raw: Any) -> str:
    status = str(raw or "").lower()
    if status in _SUCCEEDED_STATUSES:
        return STATUS_SUCCEEDED
    if status in _FAILED_STATUSES:
        return STATUS_FAILED
    return STATUS_PENDING


# Severity labels that mean "S 级 / must fix" in an assessment issue (images.md
# §8 does not fix a schema for ``issues``, so accept dicts or plain strings and
# derive must_fix from the usual severity spellings).
_MUST_FIX_SEVERITIES = {"s", "block", "critical", "must_fix"}


def normalize_assessment(body: dict) -> dict:
    """Normalise ``GET /v1/images/jobs/{id}/assessment`` (images.md §8).

    The assessment report is a grab-bag (``seed`` / ``similarity`` / ``body`` /
    ``quality`` / optional ``quality_score`` / ``issues``); consumers (qc.py,
    drama / video-clips loops) want one stable shape:

    ``{ok, seed, score, must_fix_storyboard, issues[], raw}`` where ``score``
    prefers the assistant-side ``quality_score``, falling back to the measured
    identity similarity, and ``issues`` accepts both the report's own list and
    the ffmpeg quality summary's per-frame alert list.
    """
    raw_issues: Any = body.get("issues")
    quality = body.get("quality") if isinstance(body.get("quality"), dict) else {}
    if not isinstance(raw_issues, list):
        raw_issues = quality.get("issues")
    issues: list[dict] = []
    if isinstance(raw_issues, list):
        for item in raw_issues:
            if isinstance(item, dict):
                issues.append({"desc": str(item.get("desc") or item.get("message") or item.get("detail") or ""), **item})
            elif item:
                issues.append({"desc": str(item)})

    similarity = body.get("similarity") if isinstance(body.get("similarity"), dict) else {}
    score: float | None = None
    for candidate in (body.get("quality_score"), similarity.get("identity"), similarity.get("score")):
        if isinstance(candidate, (int, float)):
            score = float(candidate)
            break

    must_fix = bool(body.get("must_fix_storyboard")) or any(str(issue.get("severity") or "").lower() in _MUST_FIX_SEVERITIES for issue in issues)
    return {
        "ok": bool(body.get("ok", True)),
        "seed": body.get("seed"),
        "score": score,
        "must_fix_storyboard": must_fix,
        "issues": issues,
        "raw": body,
    }


def _group_compare_artifacts(result: dict) -> dict[str, list[dict]]:
    """Group a compare result body's ``data[]`` items by ``meta.mode``.

    Contract (images.md §6): every response item carries its own ``meta.mode``;
    older bodies may put the mode on the item itself. Items without any mode
    marker fall into the ``""`` bucket (still returned, never dropped).
    """
    groups: dict[str, list[dict]] = {}
    data = result.get("data") if isinstance(result.get("data"), list) else []
    for item in data:
        if not isinstance(item, dict):
            continue
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        mode = str(meta.get("mode") or item.get("mode") or "")
        groups.setdefault(mode, []).append(item)
    return groups


def _extract_artifact_url(result: dict, *, kind: str | None = None) -> str:
    # Real contract (images.md §4.2): a completed job body carries its
    # artifacts in ``data[]`` — each item ``{b64_json, url, v, meta}`` with
    # ``url`` shaped ``/v1/images/jobs/{id}/file?kind=image&variant=N&v=…``.
    data = result.get("data")
    if isinstance(data, list):
        items = [item for item in data if isinstance(item, dict) and isinstance(item.get("url"), str) and item["url"]]
        if items:
            if kind == "video":
                for item in items:
                    if "kind=video" in item["url"]:
                        return item["url"]
            return items[0]["url"]
    for key in ("url", "file_url", "video_url", "image_url"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return value
    for container in ("result", "output", "artifact", "file"):
        inner = result.get(container)
        if isinstance(inner, dict):
            url = _extract_artifact_url(inner)
            if url:
                return url
        if isinstance(inner, str) and inner:
            return inner
    for item in result.get("files") or result.get("outputs") or []:
        if isinstance(item, dict):
            url = _extract_artifact_url(item)
            if url:
                return url
    return ""


class MediaGatewayGenerationProvider:
    """Async capability-discovery / submit / poll adapter for one media kind."""

    def __init__(
        self,
        client: MediaGatewayClient,
        *,
        kind: str,  # "image" | "video"
        token_provider: TokenProvider | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        poll_interval: float = 5.0,
        poll_max_attempts: int = 120,
    ) -> None:
        if kind not in ("image", "video"):
            raise ValueError(f"unsupported media kind: {kind!r}")
        self._client = client
        self.kind = kind
        # Injectable per-user token source. Wiring point (M2-3 / gateway layer):
        # bind MediaGatewayProvisioner.get_token to the current request user so
        # every generation call carries that user's h3t_ token.
        self._token_provider = token_provider
        self._sleep = sleep
        self.poll_interval = poll_interval
        self.poll_max_attempts = poll_max_attempts

    @property
    def capabilities_path(self) -> str:
        # Both media kinds share the modes endpoint (images.md §1).
        return MODES_PATH

    @property
    def submit_path(self) -> str:
        # Both media kinds submit to /v1/images/generations; kind only flips
        # the ``output`` request parameter (images.md §2.1).
        return SUBMIT_PATH

    async def _token(self) -> str | None:
        if self._token_provider is None:
            return None
        return await self._token_provider()

    async def discover_capabilities(self) -> dict[str, ModelCapability]:
        """Fetch MGW's capability API and map it into the provider format."""
        body = await self._client.get(self.capabilities_path, token=await self._token())
        return map_capabilities(body)

    async def submit(self, prompt: str, *, model: str, params: dict[str, Any] | None = None, idempotency_key: str | None = None) -> str:
        """Submit one generation job; returns the MGW task id."""
        params = dict(params or {})
        capabilities = await self.discover_capabilities()
        # Merged family names (one item per engine family, e.g. ``h3-t2v``) are
        # not real gateway modes: the ``quality`` parameter selects the rung
        # (std default / hq / ultra / 2k) and the name resolves to that rung's
        # real mode. Real mode names stay identity; ``quality`` is a local
        # selection parameter and never forwarded to the gateway.
        quality = str(params.pop("quality", None) or "").strip().lower() or None
        model_name = resolve_merged_model_alias(model, quality, capabilities)
        capability = capabilities.get(model_name)
        if capability is None:
            raise UnsupportedParameterError(f"model={model} is not offered by media_gateway ({self.kind})")
        check_params_supported(capability, params)

        payload: dict[str, Any] = {"mode": model_name, "prompt": prompt, **{k: v for k, v in params.items() if v is not None}}
        if self.kind == "video":
            # images.md §2.1: output=video makes the clip mp4 the artifact.
            payload.setdefault("output", "video")
        body = await self._client.post(
            self.submit_path,
            json=payload,
            token=await self._token(),
            idempotency_key=idempotency_key,
        )
        # Submit semantics (images.md §2.3): wait=false → 202 + job brief,
        # wait=true → 200 + full job body. Both carry the job ``id``.
        task_id = body.get("id") or body.get("task_id") or (body.get("task") or {}).get("id")
        if not task_id:
            raise MediaGatewayError(status=0, code="bad_response", message="media gateway submit response missing job id")
        return str(task_id)

    async def poll_once(self, task_id: str) -> tuple[str, dict]:
        """Poll one job; returns ``(normalized_status, raw_result)``."""
        # images.md §4.2: the job body is the result — a completed body carries
        # the artifacts in ``data[]``.
        body = await self._client.get(JOB_PATH_TEMPLATE.format(job_id=task_id), token=await self._token())
        status = normalize_status(body.get("status"))
        result = body.get("result") if isinstance(body.get("result"), dict) else body
        return status, result

    async def wait_for_result(self, task_id: str, *, max_attempts: int | None = None, cancel_check: CancelCheck | None = None) -> dict:
        """Poll until a terminal state; returns the succeeded raw result.

        ``cancel_check`` (R2-2) is an optional async callback consulted before
        every poll; when it returns True the provider DELETEs the queued job
        (images.md §9) and returns ``{"status": "cancelled", "cancelled": True,
        "job_id": ...}`` instead of a succeeded result — :meth:`generate`
        turns that marker into a ``MediaGatewayError(code="cancelled")``.
        """
        attempts = max_attempts if max_attempts is not None else self.poll_max_attempts
        for attempt in range(attempts):
            if cancel_check is not None and await cancel_check():
                await self.cancel_or_delete_job(task_id)
                return {"status": STATUS_CANCELLED, "cancelled": True, "job_id": task_id}
            status, result = await self.poll_once(task_id)
            if status == STATUS_SUCCEEDED:
                return result
            if status == STATUS_FAILED:
                raise MediaGatewayError(status=0, code="task_failed", message=f"media gateway task {task_id} failed: {result}")
            if attempt < attempts - 1:
                await self._sleep(self.poll_interval)
        raise MediaGatewayError(status=0, code="task_timeout", message=f"media gateway task {task_id} timed out after {attempts} polls")

    async def generate(
        self,
        prompt: str,
        *,
        model: str,
        params: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        max_attempts: int | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> str:
        """Submit + poll; returns the deer-flow proxied artifact URL."""
        task_id = await self.submit(prompt, model=model, params=params, idempotency_key=idempotency_key)
        result = await self.wait_for_result(task_id, max_attempts=max_attempts, cancel_check=cancel_check)
        if result.get("cancelled") and result.get("status") == STATUS_CANCELLED:
            raise MediaGatewayError(status=0, code="cancelled", message=f"media gateway task {task_id} was cancelled by the caller before completion")
        artifact = _extract_artifact_url(result, kind=self.kind)
        if not artifact:
            raise MediaGatewayError(status=0, code="bad_response", message=f"media gateway task {task_id} succeeded without an artifact URL")
        return proxy_media_url(artifact)

    # ── R2-1: multi-mode compare (images.md §6) ───────────────────────────
    async def compare_generate(
        self,
        prompt: str,
        modes: list[str],
        *,
        params: dict[str, Any] | None = None,
        include_baseline: bool = False,
        idempotency_key: str | None = None,
        max_attempts: int | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> dict:
        """Submit one prompt against several modes in a single job.

        ``POST /v1/images/compare`` (images.md §6): 1–4 modes of the *same*
        engine task family (the gateway 400s a cross-family mix), optionally
        appending the four ``h3-frame-*`` baseline tiers via
        ``include_baseline``. Skill layer entry point: t2i-series / content-
        series can call this for "same prompt, N tiers side by side" runs.

        Returns ``{"job_id", "modes": {mode: {"status", "artifacts[]",
        "meta"}}, "raw"}`` — artifacts are deer-flow proxied URLs.
        """
        if not modes:
            raise UnsupportedParameterError("compare_generate requires at least one mode")
        capabilities = await self.discover_capabilities()
        for mode in modes:
            capability = capabilities.get(mode)
            if capability is None:
                raise UnsupportedParameterError(f"mode={mode} is not offered by media_gateway ({self.kind})")
            if not capability.available:
                raise UnsupportedParameterError(f"mode={mode} is not available on media_gateway (tier disabled)")

        payload: dict[str, Any] = {"prompt": prompt, "modes": list(modes), **{k: v for k, v in (params or {}).items() if v is not None}}
        if include_baseline:
            payload["include_baseline"] = True
        if self.kind == "video":
            payload.setdefault("output", "video")
        body = await self._client.post(COMPARE_PATH, json=payload, token=await self._token(), idempotency_key=idempotency_key)

        job_id = body.get("id") or body.get("task_id") or (body.get("task") or {}).get("id")
        # wait=true submissions answer with the finished job body directly;
        # wait=false (202) answers with a brief we still have to poll.
        result = body
        if job_id and not isinstance(result.get("data"), list):
            result = await self.wait_for_result(str(job_id), max_attempts=max_attempts, cancel_check=cancel_check)
            if result.get("cancelled") and result.get("status") == STATUS_CANCELLED:
                return {"job_id": str(job_id), "modes": {}, "status": STATUS_CANCELLED, "raw": result}

        grouped: dict[str, dict[str, Any]] = {}
        for mode, items in _group_compare_artifacts(result).items():
            artifacts = [proxy_media_url(url) for item in items if isinstance((url := _extract_artifact_url(item, kind=self.kind)), str) and url]
            grouped[mode] = {"status": STATUS_SUCCEEDED, "artifacts": artifacts, "meta": (items[0].get("meta") or {}) if items else {}}
        return {"job_id": str(job_id) if job_id else None, "modes": grouped, "raw": result}

    # ── R2-2: cancel / delete (images.md §9) ─────────────────────────────
    async def cancel_or_delete_job(self, job_id: str, *, force: bool = False) -> dict:
        """``DELETE /v1/images/jobs/{id}``: cancel a queued job, delete a finished one.

        ``force=1`` is the documented escape hatch for jobs stuck in running
        after a gateway restart — UI cancel buttons must not send it.
        """
        params = {"force": "1"} if force else None
        body = await self._client.delete(JOB_PATH_TEMPLATE.format(job_id=job_id), params=params, token=await self._token())
        return {"cancelled": bool(body.get("cancelled")), "deleted": bool(body.get("deleted")), "raw": body}

    # ── R2-3: assessment read (images.md §8) ─────────────────────────────
    async def get_assessment(self, job_id: str, *, variant: int = 0, refresh: bool = False) -> dict:
        """``GET /v1/images/jobs/{id}/assessment`` → :func:`normalize_assessment` shape.

        ``refresh=true`` bypasses the per-variant ``assessment_v<N>.json``
        snapshot cache and recomputes.
        """
        params: dict[str, str] = {"variant": str(variant)}
        if refresh:
            params["refresh"] = "true"
        body = await self._client.get(ASSESSMENT_PATH_TEMPLATE.format(job_id=job_id), params=params, token=await self._token())
        return normalize_assessment(body)

    # ── R2-5: shape lock (images.md §7) ──────────────────────────────────
    async def get_shape_lock(self) -> dict:
        """``GET /v1/shape-lock``: the gateway-level canvas/duration lock.

        ``{"locked": null}`` when unlocked; otherwise the locked *effective*
        values (64-aligned short edge / quantised duration).
        """
        return await self._client.get(SHAPE_LOCK_PATH, token=await self._token())

    async def check_shape_lock(self) -> dict | None:
        """The locked shape dict, or ``None`` when the gateway is unlocked."""
        lock = (await self.get_shape_lock()).get("locked")
        return lock if isinstance(lock, dict) else None

    async def generate_report(
        self,
        prompt: str,
        *,
        model: str,
        params: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        max_attempts: int | None = None,
        cancel_check: CancelCheck | None = None,
        validate_shape_lock: bool = False,
    ) -> dict:
        """Structured :meth:`generate` for skill layer: ``{"artifact",
        "shape_lock", "assessment"}``.

        ``validate_shape_lock=True`` (R2-5) reads ``GET /v1/shape-lock`` before
        submitting and attaches the state to the return — **hint only, never
        blocking**: the gateway itself enforces the lock at queue time (409
        ``shape_locked``); the skill uses the attached dict to pre-warn the
        user that their canvas/duration will be clamped.
        """
        shape_lock = await self.check_shape_lock() if validate_shape_lock else None
        artifact = await self.generate(
            prompt,
            model=model,
            params=params,
            idempotency_key=idempotency_key,
            max_attempts=max_attempts,
            cancel_check=cancel_check,
        )
        return {"artifact": artifact, "shape_lock": shape_lock}


# ── runtime model enumeration (registry feed) ──────────────────────────────
# The generation registries build their provider/model lists per request
# (``get_*_generation_providers()``), but the modes discovery is a *remote*
# HTTP call and the registries are synchronous. The split of responsibilities:
#
# - the async Gateway routes call :func:`refresh_media_gateway_models` before
#   listing providers — one ``GET /v1/images/modes`` per TTL window, cached in
#   process for both media kinds;
# - the sync registries read the cached snapshot via
#   :func:`cached_media_gateway_models` and always keep the env-default tier
#   first, falling back to the env-default-only list when the gateway is
#   unreachable or unconfigured (byte-identical legacy output).

MODES_CACHE_TTL_SECONDS = 60.0

# Engine-task → media-kind classification. The modes[] payload carries no
# explicit kind field, so kinds derive from the engine_task semantics:
#
# - ``*2va`` (``t2va`` / ``fl2va`` / ``ref2va`` — h3-frame/clip/i2i/ref) are
#   **video+audio engines**: the gateway generates a short clip and by default
#   extracts frames from it; with ``output=video`` the mp4 is kept (gateway
#   contract: ``POST /v1/images/generations`` ``output`` parameter, exercised
#   by the gateway's own ``test_api_frames.py``). They are therefore
#   video-capable AND frame-capable — listed under BOTH media kinds.
# - Pure image backends (``moss``, ``qwen-image``) can never yield a video —
#   image kind only.
# - Motion families (``*-t2v`` / ``*-i2v`` / ``*-vace`` — wan-*, ltx-*) are
#   video-only outputs — video kind only.
_PURE_IMAGE_ENGINE_TASKS = {"moss", "qwen-image"}
_FRAME_ENGINE_TASK_SUFFIX = "2va"
_VIDEO_ENGINE_TASK_SUFFIXES = ("-t2v", "-i2v")
_VIDEO_ENGINE_TASK_KEYWORDS = ("vace",)

# key: MEDIA_GATEWAY_BASE_URL -> (monotonic timestamp, snapshot or None while
# the last fetch failed). Snapshot shape:
# ``{"image": [merged model], "video": [merged model], "aliases": {...}}``.
# The failure marker doubles as a negative cache so an unreachable gateway is
# retried at most once per TTL window.
_models_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}


def _engine_task(entry: dict) -> str:
    return str(entry.get("engine_task") or "")


def is_video_mode(entry: dict) -> bool:
    """True when the tier can produce a video artifact (video kind list)."""
    task = _engine_task(entry)
    return task.endswith(_FRAME_ENGINE_TASK_SUFFIX) or task.endswith(_VIDEO_ENGINE_TASK_SUFFIXES) or any(keyword in task for keyword in _VIDEO_ENGINE_TASK_KEYWORDS)


def is_image_mode(entry: dict) -> bool:
    """True when the tier can produce an image artifact (image kind list).

    Complement of the motion-only families: pure image backends plus the
    ``*2va`` frame-extraction families. wan-/ltx-style motion tiers are
    excluded — their output is a video, not frames.
    """
    task = _engine_task(entry)
    if task.endswith(_FRAME_ENGINE_TASK_SUFFIX) or task in _PURE_IMAGE_ENGINE_TASKS:
        return True
    if task.endswith(_VIDEO_ENGINE_TASK_SUFFIXES) or any(keyword in task for keyword in _VIDEO_ENGINE_TASK_KEYWORDS):
        return False
    # Unknown engine_task: keep the tier visible in the image list (frame
    # extraction is the gateway-wide default via ``default_frame_policy``)
    # rather than silently dropping it from both lists.
    return True


# ── same-family merging (ONE model item per engine family) ────────────────
# The gateway exposes one mode per quality rung of the same engine (real
# payload ``quality`` values: preview / draft / balanced / high / ultra / 2k —
# e.g. h3-frame-* and h3-clip-* are two name spellings of the same t2va
# engine's rungs). The user-facing lists show ONE merged model per engine
# family; the resolution rung is chosen at submit time via the ``quality``
# parameter (std default / hq / ultra / 2k), not via the model name. Grouping
# is driven by the gateway's own ``engine_task`` + ``quality`` fields; the
# name-suffix strip below only normalises the display name.
_FAMILY_QUALITY_SUFFIXES = ("-draft", "-fast", "-preview", "-std", "-hq", "-high", "-ultra", "-2k", "-mini", "-turbo", "-pro", "-max")
#: Display rung → acceptable gateway ``quality`` values (best first).
_RUNG_SOURCE_QUALITIES = {"std": ("balanced", "preview", "draft", "fast"), "hq": ("high",), "ultra": ("ultra",), "2k": ("2k",)}
#: When a family lacks the requested rung, fall down this ladder (2k → ultra → hq → std).
_RUNG_FALLBACK = {"std": ("std",), "hq": ("std",), "ultra": ("hq", "std"), "2k": ("ultra", "hq", "std")}
#: Family display names keyed by engine_task. t2va covers both the h3-frame*
#: and h3-clip* name spellings (user-confirmed: same engine, different rung
#: names); h3-t2v = 文生视频·音画，可抽帧当生图. fl2va → h3-i2v (图生视频,
#: user-corrected terminology); ref2va → h3-ref. Anything else derives from the
#: mode name with the quality rung stripped (moss-image-std → moss-image,
#: wan-t2v-std → wan-t2v, ...).
_FAMILY_DISPLAY_NAMES = {"t2va": "h3-t2v", "fl2va": "h3-i2v", "ref2va": "h3-ref"}


def _family_name(name: str) -> str:
    for suffix in _FAMILY_QUALITY_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _entry_rung_rank(entry: dict) -> tuple[int, int]:
    params = entry.get("params") if isinstance(entry.get("params"), dict) else {}
    short_edge = params.get("short_edge") if isinstance(params.get("short_edge"), (int, float)) else 0
    duration = params.get("duration_seconds") if isinstance(params.get("duration_seconds"), (int, float)) else 0
    return int(short_edge), int(duration)


def _pick_rung_entry(entries: list[dict], qualities: tuple[str, ...]) -> dict | None:
    """Best entry among the given quality rungs (largest canvas, then duration)."""
    # Best quality value first (the tuple is preference-ordered: balanced
    # beats preview/draft/fast for the std rung), then largest canvas/duration.
    for quality in qualities:
        candidates = [entry for entry in entries if entry.get("quality") == quality]
        if candidates:
            return max(candidates, key=_entry_rung_rank)
    return None


def merge_same_family_tiers(entries: list[dict]) -> tuple[list[dict], dict[str, dict[str, str]]]:
    """Collapse same-engine quality ladders into ONE merged model per family.

    Returns ``(models, aliases)`` where ``aliases[display_name]`` maps each
    resolution rung (``std`` / ``hq`` / ``ultra`` / ``2k``, only those the
    family actually has) to the real gateway mode to submit.
    """
    families: dict[str, list[dict]] = {}
    for entry in entries:
        task = _engine_task(entry) or _family_name(str(entry.get("name") or ""))
        families.setdefault(task, []).append(entry)

    models: list[dict] = []
    aliases: dict[str, dict[str, str]] = {}
    for task, tiers in families.items():
        rungs = {rung: picked for rung, qualities in _RUNG_SOURCE_QUALITIES.items() if (picked := _pick_rung_entry(tiers, qualities)) is not None}
        if not rungs:
            continue
        std_entry = rungs.get("std") or tiers[0]
        base = _FAMILY_DISPLAY_NAMES.get(task) or _family_name(str(std_entry.get("name") or ""))
        if base in aliases:
            continue  # defensive: two engine tasks mapping to one display name

        bits = []
        if task == "t2va":
            bits.append("文生视频·音画，可抽帧当生图")
        if task:
            bits.append(f"engine={task}")
        rung_bits = "、".join(f"{rung}={_entry_rung_rank(entry)[0]}p" for rung, entry in rungs.items() if rung != "std") or None
        if rung_bits:
            bits.append(f"quality 档：std（默认）、{rung_bits}，提交时用 quality 参数选档")
        bits.append(f"可用 short_edge 自定义短边（{_SHORT_EDGE_MIN}–{_SHORT_EDGE_MAX}，如 512），不传用档位默认")
        # Reference requirement of the family (from the std representative).
        if std_entry.get("needs_reference"):
            bits.append("必须提供参考图")
        elif std_entry.get("accepts_reference_images") or std_entry.get("supports_reference"):
            bits.append("可选参考图")
        summary = f"（{'；'.join(bits)}）" if bits else ""
        base_description = std_entry.get("description") if isinstance(std_entry.get("description"), str) else ""
        models.append({"name": base, "display_name": base, "description": f"{base_description}{summary}".strip() or None})
        aliases[base] = {rung: str(entry.get("name")) for rung, entry in rungs.items()}
    return models, aliases


def resolve_merged_model_alias(display_name: str, quality: str | None, available_modes) -> str:
    """Map a merged family name (+ optional ``quality`` rung) to a real mode.

    Identity for real mode names. Unknown rungs fall down
    :data:`_RUNG_FALLBACK`; an unresolvable name is returned unchanged so the
    caller's not-offered error stays accurate.
    """
    available = set(available_modes)
    if display_name in available:
        return display_name
    rung_map = cached_media_gateway_model_aliases().get(display_name)
    if not rung_map:
        return display_name
    requested = (quality or "").strip().lower() or "std"
    for rung in (requested, *_RUNG_FALLBACK.get(requested, ("std",))):
        resolved = rung_map.get(rung)
        if resolved and resolved in available:
            return resolved
    fallback = rung_map.get("std")
    return fallback if fallback and fallback in available else display_name


async def _fetch_modes_body(base_url: str, admin_token: str | None) -> dict:
    """One ``GET /v1/images/modes`` against the gateway. Test seam."""
    client = MediaGatewayClient(base_url, admin_token)
    try:
        return await client.get(MODES_PATH)
    finally:
        await client.aclose()


def clear_media_gateway_models_cache() -> None:
    """Reset the enumeration cache (tests / operator-forced refresh)."""
    _models_cache.clear()


def _mgw_connection() -> tuple[str | None, str | None]:
    """Resolve the gateway base URL + admin token for enumeration.

    Primary source is the process environment (the production launcher sources
    ``.env`` into the worker env). Falls back to the settings singleton —
    which is dotenv-backed — so processes that configure MGW via ``.env``
    without exporting it still enumerate instead of silently returning an
    empty snapshot.
    """
    base_url = os.getenv("MEDIA_GATEWAY_BASE_URL")
    admin_token = os.getenv("MEDIA_GATEWAY_ADMIN_TOKEN")
    if base_url:
        return base_url, admin_token
    try:
        settings = get_media_gateway_settings()
    except Exception:
        return None, None
    return settings.base_url, settings.admin_token


async def refresh_media_gateway_models(*, ttl: float = MODES_CACHE_TTL_SECONDS, force: bool = False) -> bool:
    """Refresh the per-kind model snapshot from the gateway's modes API.

    Returns ``True`` when a snapshot (fresh cache or a successful fetch) is
    available. Never raises: a failed fetch keeps any previous snapshot and
    leaves a negative-cache marker so the registries fall back to the
    env-default tier without re-hitting an unreachable gateway every request.
    """
    base_url, admin_token = _mgw_connection()
    if not base_url:
        return False
    base_url = base_url.rstrip("/")
    now = time.monotonic()
    cached = _models_cache.get(base_url)
    if cached is not None and not force and now - cached[0] < ttl:
        return cached[1] is not None

    try:
        body = await _fetch_modes_body(base_url, admin_token)
        raw_entries = body.get("modes") if isinstance(body.get("modes"), list) else body.get("models") if isinstance(body.get("models"), list) else body.get("data") if isinstance(body.get("data"), list) else []
        kinds_entries: dict[str, list[dict]] = {"image": [], "video": []}
        for entry in raw_entries:
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            if entry.get("available") is False:
                continue  # unavailable tiers 400 at submit time — never list them
            # A ``*2va`` tier is video-capable and frame-capable, so it is
            # listed under both kinds (see the classification comment above).
            if is_video_mode(entry):
                kinds_entries["video"].append(entry)
            if is_image_mode(entry):
                kinds_entries["image"].append(entry)

        # Merge same-engine quality ladders into one model per family, then
        # drop the merged item whose std rung IS the env-default tier (the
        # registry prepends the env default itself, so it must not duplicate).
        by_kind: dict[str, list[dict]] = {}
        aliases: dict[str, dict[str, str]] = {}
        env_defaults = {"image": os.getenv("MEDIA_GATEWAY_IMAGE_MODEL"), "video": os.getenv("MEDIA_GATEWAY_VIDEO_MODEL")}
        for kind, kind_entries in kinds_entries.items():
            merged, merged_aliases = merge_same_family_tiers(kind_entries)
            aliases.update(merged_aliases)
            default = env_defaults.get(kind)
            by_kind[kind] = [model for model in merged if not (default and merged_aliases.get(model["name"], {}).get("std") == default)]

        _models_cache[base_url] = (now, {**by_kind, "aliases": aliases})
        return True
    except Exception:
        logger.warning("media gateway model enumeration failed; falling back to env-default tier", exc_info=True)
        # Negative cache: keep a previous good snapshot's models by refreshing
        # its timestamp so callers do not retry inside the TTL window, but only
        # when there actually was one; otherwise remember the failure itself.
        if cached is not None and cached[1] is not None:
            _models_cache[base_url] = (now, cached[1])
        else:
            _models_cache[base_url] = (now, None)
        return False


def cached_media_gateway_models(kind: str) -> list[dict]:
    """The last successfully enumerated models for ``kind`` (``[]`` when none)."""
    base_url = _mgw_connection()[0]
    if not base_url:
        return []
    cached = _models_cache.get(base_url.rstrip("/"))
    if cached is None or cached[1] is None:
        return []
    return list(cached[1].get(kind, []))


def cached_media_gateway_model_aliases() -> dict[str, dict[str, str]]:
    """``{merged_family_name: {rung: real gateway mode}}`` from the last refresh."""
    base_url = _mgw_connection()[0]
    if not base_url:
        return {}
    cached = _models_cache.get(base_url.rstrip("/"))
    if cached is None or cached[1] is None:
        return {}
    aliases = cached[1].get("aliases")
    return dict(aliases) if isinstance(aliases, dict) else {}
