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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from deerflow.media_gateway.client import MediaGatewayClient, MediaGatewayError

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

# MGW status strings that map onto the normalised states. Anything unknown is
# treated as pending so a future MGW status cannot fail a running job.
_PENDING_STATUSES = {"pending", "queued", "running", "processing"}
_SUCCEEDED_STATUSES = {"succeeded", "success", "completed"}
_FAILED_STATUSES = {"failed", "error", "cancelled", "canceled"}

TokenProvider = Callable[[], Awaitable[str]]


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
        if entry.get("supports_reference"):
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
        )
    return capabilities


def check_params_supported(capability: ModelCapability, params: dict[str, Any]) -> None:
    """Reject parameters outside the model's capability before any HTTP call."""
    unsupported = sorted(k for k, v in params.items() if v is not None and k not in capability.supported_params)
    if unsupported:
        raise UnsupportedParameterError(f"model={capability.name} on media_gateway does not support parameter(s): {', '.join(unsupported)}")
    if capability.ratios and isinstance(params.get("ratio"), str) and params["ratio"] not in capability.ratios:
        raise UnsupportedParameterError(f"model={capability.name} on media_gateway does not support ratio={params['ratio']!r} (supported: {', '.join(capability.ratios)})")


def normalize_status(raw: Any) -> str:
    status = str(raw or "").lower()
    if status in _SUCCEEDED_STATUSES:
        return STATUS_SUCCEEDED
    if status in _FAILED_STATUSES:
        return STATUS_FAILED
    return STATUS_PENDING


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
        capability = capabilities.get(model)
        if capability is None:
            raise UnsupportedParameterError(f"model={model} is not offered by media_gateway ({self.kind})")
        check_params_supported(capability, params)

        payload: dict[str, Any] = {"mode": model, "prompt": prompt, **{k: v for k, v in params.items() if v is not None}}
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

    async def wait_for_result(self, task_id: str, *, max_attempts: int | None = None) -> dict:
        """Poll until a terminal state; returns the succeeded raw result."""
        attempts = max_attempts if max_attempts is not None else self.poll_max_attempts
        for attempt in range(attempts):
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
    ) -> str:
        """Submit + poll; returns the deer-flow proxied artifact URL."""
        task_id = await self.submit(prompt, model=model, params=params, idempotency_key=idempotency_key)
        result = await self.wait_for_result(task_id, max_attempts=max_attempts)
        artifact = _extract_artifact_url(result, kind=self.kind)
        if not artifact:
            raise MediaGatewayError(status=0, code="bad_response", message=f"media gateway task {task_id} succeeded without an artifact URL")
        return proxy_media_url(artifact)
