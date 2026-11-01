"""Seedance (Volcano Ark) adapter — one adapter for the whole 2.x family.

All four models share one endpoint and parameter set; per-model differences
(duration/resolution ranges, reference-material caps, frame-task locks) live in
MODEL_SPECS and are enforced locally before submission. Local front-running
matters: Ark validates the 2.5 frame/edit-task locks asynchronously (the task
queues, starts, and only then fails), so an invalid request must never reach
the API. Reference video/audio materials are URL-only (Ark rejects base64 and
caps request bodies at 64 MB). Validated end-to-end against
ark.cn-beijing.volces.com (mini, 480p, 4 s T2V — see feat-df-6 survey 3.10).
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

DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_MODEL = "doubao-seedance-2-5-260628"

# Image roles this adapter accepts; Seedance has no single-tail-frame mode
# (see _build_content). Consumed by --describe-provider rendering.
IMAGE_ROLES = ("first_frame", "first_last", "reference")

# Capability manifest — the single source of truth for Seedance capability
# data. Runtime validation, check_materials.py, and --describe-provider all
# read from here; doc tables are projections, not sources.
PROVIDER_MANIFEST = ProviderManifest(
    name="seedance",
    display_name="Seedance（火山方舟）",
    description="火山方舟 doubao-seedance-2.x 系列，一个适配器服务四档模型",
    api_key_envs=("SEEDANCE_VIDEO_API_KEY", "ARK_API_KEY"),
    default_model=DEFAULT_MODEL,
    supported_params=frozenset(
        {"resolution", "duration", "ratio", "image_role", "reference_videos", "reference_audios"}
    ),
    ratios=("21:9", "16:9", "4:3", "1:1", "3:4", "9:16", "adaptive"),
    prompt_format_files={
        mode: "references/providers/seedance/prompt-format.md"
        for mode in ("t2v", "first_frame", "last_frame", "first_last", "reference")
    },
    image_spec=ImageSpec(
        side_min=300,
        side_max=6000,
        ratio_min=0.4,
        ratio_max=2.5,
        extensions=frozenset(
            {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".gif", ".heic", ".heif"}
        ),
        label="Seedance",
    ),
    models=(
        ModelSpec(
            name="doubao-seedance-2-5-260628",
            display_name="Seedance 2.5",
            description="30秒长叙事+全模态参考(30图/10视频/10音频)，输出480p/720p/1080p(10bit)，时长4-30s",
            default_resolution="720p",
            resolutions=("480p", "720p", "1080p"),
            duration_range=(4, 30),
            max_ref_images=30,
            max_ref_videos=10,
            max_ref_audios=10,
            # Seedance 2.5 locks frame tasks to ratio=adaptive (the output
            # follows the frame image). duration is NOT locked — only the
            # (upstream) video-edit task locks duration=-1. edit_duration_-1
            # documents upstream semantics; this skill exposes no edit task.
            locks=("frame_ratio_adaptive", "edit_duration_-1"),
            audio_only_ok=True,
        ),
        ModelSpec(
            name="doubao-seedance-2-0-260128",
            display_name="Seedance 2.0",
            description="唯一支持4k输出的版本(480p/720p/1080p/4k，唯一含4k)，时长4-15s，高清成片首选",
            default_resolution="720p",
            resolutions=("480p", "720p", "1080p", "4k"),
            duration_range=(4, 15),
            max_ref_images=9,
            max_ref_videos=3,
            max_ref_audios=3,
        ),
        ModelSpec(
            name="doubao-seedance-2-0-fast-260128",
            display_name="Seedance 2.0 Fast",
            description="速度与成本折中，输出480p/720p，时长4-15s",
            default_resolution="720p",
            resolutions=("480p", "720p"),
            duration_range=(4, 15),
            max_ref_images=9,
            max_ref_videos=3,
            max_ref_audios=3,
        ),
        ModelSpec(
            name="doubao-seedance-2-0-mini-260615",
            display_name="Seedance 2.0 Mini",
            description="最低成本(约为标准版一半)，输出480p/720p，批量出片首选",
            default_resolution="720p",
            resolutions=("480p", "720p"),
            duration_range=(4, 15),
            max_ref_images=9,
            max_ref_videos=3,
            max_ref_audios=3,
        ),
    ),
)

MODEL_SPECS: dict[str, ModelSpec] = {m.name: m for m in PROVIDER_MANIFEST.models}


class SeedanceProvider(BaseVideoProvider):
    name = "seedance"
    supported_params = PROVIDER_MANIFEST.supported_params
    default_model = DEFAULT_MODEL
    api_key_envs = PROVIDER_MANIFEST.api_key_envs
    known_models = tuple(MODEL_SPECS)
    poll_interval = 10
    poll_max_attempts = 180  # 30 min: headroom for 2.5 30s tasks (mini 4s took ~105s live)

    def api_key(self) -> str | None:
        # Video-dedicated key preferred; falls back to the shared ARK_API_KEY
        # (same credential family as chat models on the Volcano Ark console).
        return os.getenv("SEEDANCE_VIDEO_API_KEY") or os.getenv("ARK_API_KEY")

    def _base_url(self) -> str:
        # Overridable for the BytePlus international endpoint.
        return os.getenv("SEEDANCE_API_BASE_URL", DEFAULT_BASE_URL).rstrip("/")

    def _model_and_spec(self) -> tuple[str, ModelSpec]:
        model = self.model or os.getenv("SEEDANCE_VIDEO_MODEL", DEFAULT_MODEL)
        spec = MODEL_SPECS.get(model)
        if spec is None:
            raise ValueError(f"unknown Seedance model {model!r}; known: {', '.join(MODEL_SPECS)}")
        return model, spec

    def _validate(self, model: str, spec: ModelSpec, params: dict) -> None:
        resolution = params.get("resolution")
        if resolution is not None and resolution not in spec.resolutions:
            raise ValueError(f"model {model} does not support resolution {resolution!r}; supported: {', '.join(spec.resolutions)}")
        duration = params.get("duration")
        if duration is not None and duration != -1 and not (spec.duration_range[0] <= duration <= spec.duration_range[1]):
            raise ValueError(f"duration must be {spec.duration_range[0]}-{spec.duration_range[1]} or -1 (auto), got {duration}")
        ratio = params.get("ratio")
        if ratio is not None and ratio not in PROVIDER_MANIFEST.ratios:
            raise ValueError(f"unsupported aspect ratio {ratio!r}; expected one of {', '.join(PROVIDER_MANIFEST.ratios)}")

    def _build_content(
        self,
        prompt_text: str,
        reference_images: list[str],
        image_role: str,
        model: str,
        spec: ModelSpec,
        params: dict,
    ) -> tuple[list[dict], bool]:
        """Build the Ark content[] array. Returns (content, is_frame_mode).

        Frame roles and reference roles are mutually exclusive upstream, so
        video/audio references are only allowed in reference mode.
        """
        content: list[dict] = [{"type": "text", "text": prompt_text}]
        images = reference_images or []
        videos = params.get("reference_videos") or []
        audios = params.get("reference_audios") or []

        for kind, items, limit in (
            ("video", videos, spec.max_ref_videos),
            ("audio", audios, spec.max_ref_audios),
        ):
            if len(items) > limit:
                raise ValueError(f"too many reference {kind}s: {len(items)} > {limit} for model {model}")
            for item in items:
                if not item.startswith(("http://", "https://")):
                    raise ValueError(f"reference {kind} must be a public URL (Ark rejects base64 and cannot reach local paths): {item!r}")
                field = f"{kind}_url"
                content.append({"type": field, field: {"url": item}, "role": f"reference_{kind}"})

        def image_item(path: str, role: str) -> dict:
            return {
                "type": "image_url",
                "image_url": {"url": image_ref(path)},
                "role": role,
            }

        def warn_extra(limit: int) -> None:
            if len(images) > limit:
                print(f"Warning: provider=seedance {image_role} mode uses only the first {limit} image(s); ignoring {len(images) - limit} extra")

        if audios and not images and not videos and not spec.audio_only_ok:
            raise ValueError("Seedance 2.0 family does not accept audio-only input; include at least one reference image or video (only 2.5 supports audio alone).")
        if not images:
            # T2V or reference-with-video-only; image_role consistency with the
            # materials is enforced in create_task / generate.py.
            return content, False

        if image_role == "reference":
            if len(images) > spec.max_ref_images:
                raise ValueError(f"too many reference images: {len(images)} > {spec.max_ref_images} for model {model}")
            for path in images:
                content.append(image_item(path, "reference_image"))
            return content, False
        elif image_role == "last_frame":
            # 官方仅定义首帧(单图)与首尾帧(双图); 单独尾帧无任务形态, H3-only.
            raise ValueError("Seedance has no single-tail-frame mode; use first_frame (single image) or first_last (two images, opening + closing). --image-role last_frame is MiniMax H3 only.")
        elif image_role == "first_last":
            warn_extra(2)
            content.append(image_item(images[0], "first_frame"))
            if len(images) > 1:
                content.append(image_item(images[1], "last_frame"))
        else:
            # Default: first_frame (I2V).
            warn_extra(1)
            content.append(image_item(images[0], "first_frame"))
        return content, True

    def create_task(self, prompt_text: str, reference_images: list[str], params: dict) -> str:
        model, spec = self._model_and_spec()
        self._validate(model, spec, params)
        if params.get("upscale_video"):
            raise ValueError("provider=seedance does not support --upscale-video; upgrade by regenerating with the original materials at a higher resolution")

        image_role = params.get("image_role")
        if image_role is None:
            image_role = "reference" if (params.get("reference_videos") or params.get("reference_audios")) else "first_frame"
        if image_role != "reference" and (params.get("reference_videos") or params.get("reference_audios")):
            raise ValueError("frame roles and reference materials are mutually exclusive; pass --image-role reference when using --reference-videos/--reference-audios")

        images = reference_images or []
        videos = params.get("reference_videos") or []
        audios = params.get("reference_audios") or []
        content, frame_mode = self._build_content(prompt_text, reference_images, image_role, model, spec, params)
        body: dict = {"model": model, "content": content}
        if frame_mode and "frame_ratio_adaptive" in spec.locks:
            # 2.5 frame tasks lock ratio=adaptive (the output follows the first
            # frame image's aspect ratio; an explicit non-adaptive ratio would
            # fail asynchronously upstream). duration is NOT frame-locked —
            # only video-edit locks duration=-1 (API Ref) — so the user may set
            # [4,30] or -1; default -1 lets the model pick a length for the frames.
            ratio = params.get("ratio")
            if ratio is not None and ratio != "adaptive":
                raise ValueError(f"--aspect-ratio={ratio!r} is not supported for frame modes on {model}: the output ratio follows the frame image (adaptive)")
            body["ratio"] = "adaptive"
            body["duration"] = params.get("duration") if params.get("duration") is not None else -1
            # resolution is not frame-locked either; carry the user's validated
            # value or the manifest default so it is not silently dropped.
            body["resolution"] = params.get("resolution") or spec.default_resolution
        else:
            body["resolution"] = params.get("resolution") or spec.default_resolution
            body["duration"] = params.get("duration") if params.get("duration") is not None else 5
            # T2V sends a concrete ratio (default 16:9); modes with materials
            # (reference or 2.0 frame) stay adaptive unless an explicit ratio is set.
            if params.get("ratio") is not None:
                body["ratio"] = params["ratio"]
            elif not (images or videos or audios):
                body["ratio"] = "16:9"

        resp = requests.post(
            f"{self._base_url()}/contents/generations/tasks",
            headers={**self.auth_headers(), "Content-Type": "application/json"},
            json=body,
            timeout=60,
        )
        if resp.status_code >= 400:
            # 同步拒绝（如实人肖像审核 400）也要让 agent 看到 error.code/message，
            # raise_for_status 会把响应体丢成一行状态。
            try:
                err = resp.json().get("error")
            except ValueError:
                err = None
            detail = f"{err.get('code')}: {err.get('message')}" if isinstance(err, dict) else resp.text[:500]
            raise requests.HTTPError(
                f"provider=seedance create task: HTTP {resp.status_code} {detail}", response=resp
            )
        payload = resp.json()
        task_id = payload.get("id")
        if not task_id:
            raise Exception(f"provider=seedance no id in response: {json.dumps(payload, ensure_ascii=False)}")
        return task_id

    def poll_once(self, handle: str) -> tuple[str, dict]:
        resp = requests.get(
            f"{self._base_url()}/contents/generations/tasks/{handle}",
            headers=self.auth_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        task = resp.json()
        status = task.get("status")
        if status == STATUS_SUCCEEDED:
            return STATUS_SUCCEEDED, task
        if status in ("failed", "cancelled", "expired"):
            return STATUS_FAILED, task
        return STATUS_PENDING, task

    def extract_video_url(self, handle: str, result: dict) -> str:
        url = (result.get("content") or {}).get("video_url")
        if not url:
            raise Exception(f"provider=seedance succeeded but no content.video_url: {json.dumps(result, ensure_ascii=False)}")
        return url

    def cancel(self, handle: str, output_file: str | None = None) -> str:
        """Cancel a queued task. State is checked first: Ark's DELETE is
        'cancel or delete record', and a finished task's record must never be
        deleted (it feeds regeneration/audit lookups)."""
        resp = requests.get(
            f"{self._base_url()}/contents/generations/tasks/{handle}",
            headers=self.auth_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        status = resp.json().get("status")
        if status == "queued":
            resp = requests.delete(
                f"{self._base_url()}/contents/generations/tasks/{handle}",
                headers=self.auth_headers(),
                timeout=30,
            )
            resp.raise_for_status()
            if output_file:
                set_task_status(output_file, handle, "cancelled")
            return f"task {handle} cancelled (queued tasks are not billed)"
        if status == "running":
            return f"task {handle} is running and cannot be cancelled; wait for it to finish"
        if status in ("succeeded", "failed", "expired"):
            return f"task {handle} already {status}; nothing cancelled (the upstream endpoint would DELETE the task record)"
        if status == "cancelled":
            return f"task {handle} was already cancelled"
        return f"task {handle} is in unknown state {status!r}; nothing done"


PROVIDER = SeedanceProvider
