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
from dataclasses import dataclass

import requests

from .base import (
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SUCCEEDED,
    BaseVideoProvider,
    image_ref,
    set_task_status,
)

DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_MODEL = "doubao-seedance-2-5-260628"
# Keep in sync with the ratio row in SKILL.md's output-settings table.
SUPPORTED_RATIOS = ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16", "adaptive")


@dataclass(frozen=True)
class _ModelSpec:
    """Per-model value ranges (source: feat-df-6 survey, capability matrix)."""

    duration_max: int
    resolutions: tuple[str, ...]
    max_ref_images: int
    max_ref_videos: int
    max_ref_audios: int
    # Seedance 2.5 locks frame tasks to ratio=adaptive / duration=-1 (official
    # rule, async failure when violated). The 2.0 family documents no such lock.
    locks_frame_params: bool

    @property
    def duration_range(self) -> tuple[int, int]:
        return (4, self.duration_max)


MODEL_SPECS: dict[str, _ModelSpec] = {
    "doubao-seedance-2-5-260628": _ModelSpec(
        duration_max=30,
        resolutions=("480p", "720p"),
        max_ref_images=30,
        max_ref_videos=10,
        max_ref_audios=10,
        locks_frame_params=True,
    ),
    "doubao-seedance-2-0-260128": _ModelSpec(
        duration_max=15,
        resolutions=("480p", "720p", "1080p", "4k"),
        max_ref_images=9,
        max_ref_videos=3,
        max_ref_audios=3,
        locks_frame_params=False,
    ),
    "doubao-seedance-2-0-fast-260128": _ModelSpec(
        duration_max=15,
        resolutions=("480p", "720p"),
        max_ref_images=9,
        max_ref_videos=3,
        max_ref_audios=3,
        locks_frame_params=False,
    ),
    "doubao-seedance-2-0-mini-260615": _ModelSpec(
        duration_max=15,
        resolutions=("480p", "720p"),
        max_ref_images=9,
        max_ref_videos=3,
        max_ref_audios=3,
        locks_frame_params=False,
    ),
}


class SeedanceProvider(BaseVideoProvider):
    name = "seedance"
    supported_params = {
        "resolution",
        "duration",
        "ratio",
        "image_role",
        "reference_videos",
        "reference_audios",
    }
    default_model = DEFAULT_MODEL
    api_key_envs = ("SEEDANCE_VIDEO_API_KEY", "ARK_API_KEY")
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

    def _model_and_spec(self) -> tuple[str, _ModelSpec]:
        model = self.model or os.getenv("SEEDANCE_VIDEO_MODEL", DEFAULT_MODEL)
        spec = MODEL_SPECS.get(model)
        if spec is None:
            raise ValueError(
                f"unknown Seedance model {model!r}; known: {', '.join(MODEL_SPECS)}"
            )
        return model, spec

    def _validate(self, spec: _ModelSpec, params: dict) -> None:
        resolution = params.get("resolution")
        if resolution is not None and resolution not in spec.resolutions:
            raise ValueError(
                f"model {self._model_and_spec()[0]} does not support resolution "
                f"{resolution!r}; supported: {', '.join(spec.resolutions)}"
            )
        duration = params.get("duration")
        if duration is not None and duration != -1 and not (
            spec.duration_range[0] <= duration <= spec.duration_range[1]
        ):
            raise ValueError(
                f"duration must be {spec.duration_range[0]}-{spec.duration_range[1]} "
                f"or -1 (auto), got {duration}"
            )
        ratio = params.get("ratio")
        if ratio is not None and ratio not in SUPPORTED_RATIOS:
            raise ValueError(
                f"unsupported aspect ratio {ratio!r}; expected one of "
                f"{', '.join(SUPPORTED_RATIOS)}"
            )

    def _build_content(
        self,
        prompt_text: str,
        reference_images: list[str],
        image_role: str,
        spec: _ModelSpec,
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
                raise ValueError(
                    f"too many reference {kind}s: {len(items)} > {limit} for model "
                    f"{self._model_and_spec()[0]}"
                )
            for item in items:
                if not item.startswith(("http://", "https://")):
                    raise ValueError(
                        f"reference {kind} must be a public URL (Ark rejects base64 "
                        f"and cannot reach local paths): {item!r}"
                    )
                field = f"{kind}_url"
                content.append(
                    {"type": field, field: {"url": item}, "role": f"reference_{kind}"}
                )

        def image_item(path: str, role: str) -> dict:
            return {
                "type": "image_url",
                "image_url": {"url": image_ref(path)},
                "role": role,
            }

        def warn_extra(limit: int) -> None:
            if len(images) > limit:
                print(
                    f"Warning: provider=seedance {image_role} mode uses only the "
                    f"first {limit} image(s); ignoring {len(images) - limit} extra"
                )

        if not images:
            # T2V or reference-with-video-only; image_role consistency with the
            # materials is enforced in create_task / generate.py.
            return content, False

        if image_role == "reference":
            if len(images) > spec.max_ref_images:
                raise ValueError(
                    f"too many reference images: {len(images)} > "
                    f"{spec.max_ref_images} for model {self._model_and_spec()[0]}"
                )
            for path in images:
                content.append(image_item(path, "reference_image"))
            return content, False
        elif image_role == "last_frame":
            warn_extra(1)
            content.append(image_item(images[0], "last_frame"))
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

    def create_task(
        self, prompt_text: str, reference_images: list[str], params: dict
    ) -> str:
        model, spec = self._model_and_spec()
        self._validate(spec, params)
        if params.get("upscale_video"):
            raise ValueError(
                "provider=seedance does not support --upscale-video; upgrade by "
                "regenerating with the original materials at a higher resolution"
            )

        image_role = params.get("image_role")
        if image_role is None:
            image_role = "reference" if (
                params.get("reference_videos") or params.get("reference_audios")
            ) else "first_frame"
        if image_role != "reference" and (
            params.get("reference_videos") or params.get("reference_audios")
        ):
            raise ValueError(
                "frame roles and reference materials are mutually exclusive; pass "
                "--image-role reference when using --reference-videos/--reference-audios"
            )

        images = reference_images or []
        videos = params.get("reference_videos") or []
        audios = params.get("reference_audios") or []
        content, frame_mode = self._build_content(
            prompt_text, reference_images, image_role, spec, params
        )
        body: dict = {"model": model, "content": content}
        if frame_mode and spec.locks_frame_params:
            # 2.5 frame tasks follow the source images: explicit ratio/duration
            # would fail asynchronously, so they are refused here and locked.
            for value, label in (
                (params.get("ratio"), "--aspect-ratio"),
                (params.get("duration"), "--duration"),
            ):
                if value is not None and value not in ("adaptive", -1):
                    raise ValueError(
                        f"{label}={value!r} is not supported for frame modes on "
                        f"{model}: the output follows the frame images "
                        "(ratio=adaptive, duration=-1)"
                    )
            body["ratio"] = "adaptive"
            body["duration"] = -1
        else:
            body["resolution"] = params.get("resolution") or "720p"
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
        resp.raise_for_status()
        payload = resp.json()
        task_id = payload.get("id")
        if not task_id:
            raise Exception(
                f"provider=seedance no id in response: {json.dumps(payload, ensure_ascii=False)}"
            )
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
            raise Exception(
                f"provider=seedance succeeded but no content.video_url: "
                f"{json.dumps(result, ensure_ascii=False)}"
            )
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
            return (
                f"task {handle} already {status}; nothing cancelled (the upstream "
                "endpoint would DELETE the task record)"
            )
        if status == "cancelled":
            return f"task {handle} was already cancelled"
        return f"task {handle} is in unknown state {status!r}; nothing done"


PROVIDER = SeedanceProvider
