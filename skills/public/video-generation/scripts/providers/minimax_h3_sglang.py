"""MiniMax H3 (self-hosted sglang) adapter — audio+video on a single GPU.

This is NOT the MiniMax cloud V2 API (see minimax_h3.py) and NOT
OpenAI-compatible: it is sglang's video API — a JSON body carrying an explicit
``task``, polling by job id, and the artifact downloaded from a content
endpoint. There is **no auth**, no negative prompt (the released checkpoint is
CFG-distilled), and jobs run **serially on one GPU**.

Tasks: ``t2va`` (text → audio+video; ``conditions`` must be empty) and ``fl2va``
(first/last keyframes + text). ``ref2va`` is NOT loaded on this deployment and is
rejected locally. Contract verified 2026-10-06 against the deployment
integration guide (projects/llm/minimax-h3/docs/integration/api-guide.md);
``/openapi.json`` on that service is wrong — do not consult it.
"""

import json
import os

import requests

from .base import (
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SUCCEEDED,
    BaseVideoProvider,
    image_ref,
    set_task_status,
)
from .manifest import ImageSpec, ModelSpec, ProviderManifest

DEFAULT_BASE_URL = "http://100.108.144.120:30010"
DEFAULT_MODEL = "MiniMax-H3-SGLang"

# The service is unauthenticated, but base.generate() gates on api_key() being
# truthy. This sentinel keeps that gate open and is never sent on the wire
# (see auth_headers).
_NO_AUTH_SENTINEL = "no-auth"

TASK_T2VA = "t2va"
TASK_FL2VA = "fl2va"

# `short_edge` is the resolution knob: short side + aspect ratio decide the
# frame (short_edge=384, 16:9 -> 672x384). Only the values verified on this
# deployment are accepted.
SHORT_EDGE_BY_RESOLUTION = {"384P": 384, "768P": 768}
DEFAULT_SHORT_EDGE = 768

# Image roles this adapter accepts. `reference` (ref2va) is deliberately absent:
# the checkpoint is loaded with the fl2va partition only, so a reference request
# would be accepted and then fail after occupying the GPU queue.
IMAGE_ROLES = ("first_frame", "last_frame", "first_last")

_DESCRIPTION = (
    "自建 sglang 单卡部署的 MiniMax H3 音视频模型：768P/384P、画面+立体声一次生成（4~15 秒），"
    "支持文生视频与首帧/尾帧/首尾帧图生视频；不支持参考图/参考音视频，不支持 2K 升格"
)

# Capability manifest — the single source of truth for this provider's
# capability data (runtime validation, check_materials.py, --describe-provider).
PROVIDER_MANIFEST = ProviderManifest(
    name="minimax_h3_sglang",
    display_name="MiniMax H3（自建 sglang）",
    description="自建 sglang 单卡部署的 MiniMax H3 音视频模型（t2va/fl2va），无鉴权、作业串行",
    # Not a credential: the endpoint override doubles as the frontend's
    # "configured" signal, since this service has no key to configure.
    api_key_envs=("SGLANG_H3_API_BASE_URL",),
    default_model=DEFAULT_MODEL,
    supported_params=frozenset({"resolution", "duration", "ratio", "image_role"}),
    ratios=("auto", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"),
    prompt_format_files={
        mode: "references/providers/minimax_h3_sglang/prompt-format.md"
        for mode in ("t2v", "first_frame", "last_frame", "first_last")
    },
    # Mirrors the MiniMax H3 cloud image spec (same model family); not re-measured
    # on this deployment — see spec.md. Declaring it enables check_materials.py.
    image_spec=ImageSpec(
        side_min=256,
        side_max=5760,
        ratio_min=0.4,
        ratio_max=2.5,
        extensions=frozenset({".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}),
        label="MiniMax-H3-SGLang",
    ),
    models=(
        ModelSpec(
            name=DEFAULT_MODEL,
            display_name="MiniMax H3 SGLang",
            description=_DESCRIPTION,
            default_resolution="768P",
            resolutions=("384P", "768P"),
            duration_range=(4, 15),
            max_ref_images=2,  # fl2va takes 1-2 keyframes
            supports_reference=False,  # ref2va not loaded
            supports_regeneration=False,  # no 2K endpoint
        ),
    ),
)

MODEL_SPECS: dict[str, ModelSpec] = {m.name: m for m in PROVIDER_MANIFEST.models}


def _raise_sync_rejection(resp: requests.Response, operation: str) -> None:
    """sglang reports request-contract violations as {"detail": "..."}."""
    if resp.status_code < 400:
        return
    try:
        detail = resp.json().get("detail")
    except ValueError:
        detail = None
    if not isinstance(detail, str) or not detail:
        detail = resp.text[:500]
    raise requests.HTTPError(
        f"provider=minimax_h3_sglang {operation}: HTTP {resp.status_code} {detail}",
        response=resp,
    )


class MiniMaxH3SglangProvider(BaseVideoProvider):
    name = "minimax_h3_sglang"
    supported_params = PROVIDER_MANIFEST.supported_params
    default_model = DEFAULT_MODEL
    api_key_envs = PROVIDER_MANIFEST.api_key_envs
    known_models = tuple(MODEL_SPECS)
    # The service recommends 5s; the window must outlast the 600s sandbox kill
    # (local_sandbox.py runs each bash command with timeout=600).
    poll_interval = 5
    poll_max_attempts = 240

    # --- auth: this service has none ---

    def api_key(self) -> str | None:
        return os.getenv("SGLANG_H3_API_BASE_URL") or _NO_AUTH_SENTINEL

    def auth_headers(self) -> dict:
        # Unconditionally empty: sending a stray Bearer token to this service
        # would be noise at best.
        return {}

    def _base_url(self) -> str:
        return os.getenv("SGLANG_H3_API_BASE_URL", DEFAULT_BASE_URL).rstrip("/")

    def _model_and_spec(self) -> tuple[str, ModelSpec]:
        model = self.model or os.getenv("SGLANG_H3_MODEL", DEFAULT_MODEL)
        spec = MODEL_SPECS.get(model)
        if spec is None:
            raise ValueError(
                f"unknown MiniMax H3 sglang model {model!r}; "
                f"known: {', '.join(MODEL_SPECS)}"
            )
        return model, spec

    # --- request building ---

    def _build_conditions(
        self, reference_images: list[str], image_role: str | None
    ) -> tuple[str, list[dict]]:
        images = reference_images or []

        def condition(path: str, frame_index: int) -> dict:
            # Only these four keys are accepted; any extra field is a 400.
            return {
                "type": "image",
                "role": "keyframe",
                "uri": image_ref(path),
                "frame_index": frame_index,
            }

        def warn_extra(limit: int) -> None:
            if len(images) > limit:
                print(
                    f"Warning: provider=minimax_h3_sglang {image_role or 'first_frame'} mode "
                    f"uses only the first {limit} image(s); ignoring {len(images) - limit} extra"
                )

        if not images:
            return TASK_T2VA, []  # t2va requires conditions == []

        if image_role == "reference":
            raise ValueError(
                "provider=minimax_h3_sglang does not support reference-mode generation "
                "(Ref2VA is not loaded on this deployment); "
                "use first_frame, last_frame, or first_last"
            )
        if image_role == "last_frame":
            warn_extra(1)
            return TASK_FL2VA, [condition(images[0], -1)]
        if image_role == "first_last":
            warn_extra(2)
            conditions = [condition(images[0], 0)]
            if len(images) > 1:
                conditions.append(condition(images[1], -1))
            return TASK_FL2VA, conditions
        # Default: first_frame (I2V).
        warn_extra(1)
        return TASK_FL2VA, [condition(images[0], 0)]

    def create_task(self, prompt_text: str, reference_images: list[str], params: dict) -> str:
        model, spec = self._model_and_spec()
        task, conditions = self._build_conditions(reference_images, params.get("image_role"))

        duration = params.get("duration")
        duration = 5 if duration is None else duration
        low, high = spec.duration_range
        if not low <= duration <= high:
            raise ValueError(f"target.duration_seconds must be in [{low}, {high}], got {duration}")

        resolution = params.get("resolution")
        if resolution is not None and resolution not in SHORT_EDGE_BY_RESOLUTION:
            raise ValueError(
                f"unsupported resolution {resolution!r}; "
                f"expected one of {', '.join(SHORT_EDGE_BY_RESOLUTION)}"
            )
        short_edge = SHORT_EDGE_BY_RESOLUTION.get(resolution, DEFAULT_SHORT_EDGE)

        ratio = params.get("ratio")
        if ratio is not None and ratio not in PROVIDER_MANIFEST.ratios:
            raise ValueError(
                f"unsupported aspect ratio {ratio!r}; "
                f"expected one of {', '.join(PROVIDER_MANIFEST.ratios)}"
            )
        if ratio is None:
            # t2va needs a concrete frame; a keyframe mode lets the model decide.
            ratio = "16:9" if task == TASK_T2VA else "auto"

        body: dict = {
            "model": model,
            "prompt": prompt_text,
            "task": task,
            "conditions": conditions,
            "target": {
                "short_edge": short_edge,
                "aspect_ratio": ratio,
                "duration_seconds": duration,
            },
            # The service requires >= 2 (its sigma schedules include both
            # interval endpoints); 8 is the documented sweet spot.
            "num_inference_steps": max(2, _int_env("SGLANG_H3_STEPS", 8)),
        }
        seed = os.getenv("SGLANG_H3_SEED")
        if seed is not None and seed.strip():
            body["seed"] = int(seed)

        resp = requests.post(
            f"{self._base_url()}/v1/videos",
            headers={"Content-Type": "application/json"},
            json=body,
            timeout=60,
        )
        _raise_sync_rejection(resp, "create task")
        payload = resp.json()
        job_id = payload.get("id")
        if not job_id:
            raise Exception(
                f"provider=minimax_h3_sglang no id in response: "
                f"{json.dumps(payload, ensure_ascii=False)}"
            )
        return job_id

    def poll_once(self, handle: str) -> tuple[str, dict]:
        resp = requests.get(f"{self._base_url()}/v1/videos/{handle}", timeout=30)
        if resp.status_code == 404:
            # The job record was deleted upstream (the list accumulates and is
            # cleaned by operators). Terminal, not a transient blip.
            return STATUS_FAILED, {
                "status": "deleted",
                "error": {"message": f"video job {handle} no longer exists upstream"},
            }
        resp.raise_for_status()
        job = resp.json()
        status = job.get("status")
        if status == "completed":
            return STATUS_SUCCEEDED, job
        if status in ("failed", "deleted"):
            return STATUS_FAILED, job
        return STATUS_PENDING, job  # queued and anything else non-terminal

    def extract_video_url(self, handle: str, result: dict) -> str:
        # The job record carries no HTTP url (`url` is always null and
        # `file_path` is container-internal). The artifact is served by the
        # content endpoint, and the inherited plain-GET download() suffices.
        return f"{self._base_url()}/v1/videos/{handle}/content"

    def cancel(self, handle: str, output_file: str | None = None) -> str:
        """Cancel a queued job — state is checked first.

        DELETE removes the job *and* its artifact upstream, so a finished job
        must never be deleted (its record feeds audit lookups)."""
        resp = requests.get(f"{self._base_url()}/v1/videos/{handle}", timeout=30)
        resp.raise_for_status()
        status = resp.json().get("status")
        if status == "queued":
            resp = requests.delete(f"{self._base_url()}/v1/videos/{handle}", timeout=30)
            resp.raise_for_status()
            if output_file:
                set_task_status(output_file, handle, "cancelled")
            return f"task {handle} cancelled (queued jobs have not run yet)"
        if status in ("completed", "failed"):
            return f"task {handle} already {status}; nothing cancelled (DELETE would remove the artifact)"
        if status == "deleted":
            return f"task {handle} was already deleted"
        return f"task {handle} is in unknown state {status!r}; nothing done"


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


PROVIDER = MiniMaxH3SglangProvider