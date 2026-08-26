"""MiniMax H3 (V2) adapter — validated end-to-end against api.minimaxi.com.

V2 is two-step (create -> poll) and takes the video URL directly from
task.content.url; it is NOT the legacy Hailuo V1 three-step flow (see
minimax_v1.py). Confirmed by live probe: success responses carry no base_resp,
so errors come from the HTTP status and task.status/error.
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
    video_ref,
)

DEFAULT_HOST = "https://api.minimaxi.com"  # 国内站；国际站 https://api.minimax.io
DEFAULT_MODEL = "MiniMax-H3"
# Keep in sync with the aspect-ratio row in SKILL.md's output-settings table.
SUPPORTED_RATIOS = ("16:9", "9:16", "1:1", "4:3", "3:4", "21:9")


class MiniMaxH3Provider(BaseVideoProvider):
    name = "minimax_h3"
    supported_params = {"resolution", "duration", "ratio", "image_role", "upscale_video"}
    default_model = DEFAULT_MODEL
    api_key_envs = ("MINIMAX_VIDEO_API_KEY", "MINIMAX_API_KEY")
    known_models = (DEFAULT_MODEL,)

    def api_key(self) -> str | None:
        # Video-dedicated key preferred; falls back to the shared MINIMAX_API_KEY
        # (also used by music/podcast skills) so existing setups keep working.
        return os.getenv("MINIMAX_VIDEO_API_KEY") or os.getenv("MINIMAX_API_KEY")

    def _host(self) -> str:
        return os.getenv("MINIMAX_API_HOST", DEFAULT_HOST).rstrip("/")

    def _build_content(
        self,
        prompt_text: str,
        reference_images: list[str],
        image_role: str,
        explicit_ratio: str | None = None,
    ) -> tuple[list[dict], bool]:
        """Build the V2 content[] array for the requested mode.

        Returns (content, send_ratio). T2V requires `ratio`; frame modes are
        always adaptive (the API ignores an explicit ratio there); reference
        mode defaults to adaptive and honors `explicit_ratio`.
        """
        content: list[dict] = [{"type": "text", "text": prompt_text}]
        images = reference_images or []

        def image_item(path: str, role: str) -> dict:
            return {
                "type": "image_url",
                "image_url": {"url": image_ref(path)},
                "role": role,
            }

        def warn_extra(limit: int) -> None:
            # Frame modes take a fixed number of images; extras are dropped, so say so
            # instead of failing silently.
            if len(images) > limit:
                print(
                    f"Warning: provider=minimax_h3 {image_role} mode uses only the first "
                    f"{limit} image(s); ignoring {len(images) - limit} extra"
                )

        if not images:
            return content, True  # T2V — ratio is required

        if image_role == "reference":
            # Ref2VA: identity/style transfer (not a frame). All images pass
            # through; upstream accepts up to 9 and rejects the rest.
            for path in images:
                content.append(image_item(path, "reference_image"))
            # r2va: adaptive by default, explicit ratio honored.
            return content, bool(explicit_ratio)
        elif image_role == "last_frame":
            warn_extra(1)
            content.append(image_item(images[0], "last_frame"))
        elif image_role == "first_last":
            warn_extra(2)
            # First image = opening frame; second (if given) = closing frame.
            content.append(image_item(images[0], "first_frame"))
            if len(images) > 1:
                content.append(image_item(images[1], "last_frame"))
        else:
            # Default: first_frame (I2V).
            warn_extra(1)
            content.append(image_item(images[0], "first_frame"))

        return content, False  # frame modes: always adaptive

    def create_task(
        self, prompt_text: str, reference_images: list[str], params: dict
    ) -> str:
        upscale_video = params.get("upscale_video")
        if upscale_video:
            return self._create_regeneration_task(
                upscale_video, prompt_text, reference_images, params
            )
        image_role = params.get("image_role")
        if image_role is None:
            image_role = "first_frame"
        content, send_ratio = self._build_content(
            prompt_text, reference_images, image_role, params.get("ratio")
        )
        duration = params.get("duration")
        if duration is not None and not (4 <= duration <= 15):
            raise ValueError(f"duration must be 4-15 seconds, got {duration}")
        resolution = params.get("resolution")
        if resolution is not None and resolution not in ("768P", "2K"):
            raise ValueError(f"unsupported resolution {resolution!r}; expected 768P or 2K")
        ratio = params.get("ratio")
        if ratio is not None and ratio not in SUPPORTED_RATIOS:
            raise ValueError(f"unsupported aspect ratio {ratio!r}; expected one of {', '.join(SUPPORTED_RATIOS)}")
        body: dict = {
            "model": self.model or os.getenv("MINIMAX_VIDEO_MODEL", DEFAULT_MODEL),
            "content": content,
            "resolution": resolution if resolution is not None else "768P",
            # 5s (=120 frames) keeps the 2K-regeneration source floor (107
            # frames) reachable on the default draft.
            "duration": duration if duration is not None else 5,
        }
        # T2V requires a ratio (default 16:9); reference mode sends it only
        # when explicitly set; frame modes let the image fix it.
        if send_ratio:
            body["ratio"] = params.get("ratio") if params.get("ratio") is not None else "16:9"

        resp = requests.post(
            f"{self._host()}/v2/video_generation",
            headers={**self.auth_headers(), "Content-Type": "application/json"},
            json=body,
            timeout=60,
        )
        resp.raise_for_status()
        payload = resp.json()
        task_id = payload.get("task_id")
        if not task_id:
            raise Exception(
                f"provider=minimax_h3 no task_id in response: {json.dumps(payload, ensure_ascii=False)}"
            )
        return task_id

    def _create_regeneration_task(
        self,
        upscale_video: str,
        prompt_text: str,
        reference_images: list[str],
        params: dict,
    ) -> str:
        """768P -> 2K regeneration (official /v2/video_regeneration, base_video
        mode): replay the ENTIRE original content (final prompt + original
        reference inputs, same order) plus exactly one role=base_video source
        item; resolution is fixed 2K and duration/ratio are not part of the body."""
        image_role = params.get("image_role")
        if image_role is None:
            image_role = "first_frame"
        content, _ = self._build_content(prompt_text, reference_images, image_role)
        content.append(
            {
                "type": "video_url",
                "video_url": {"url": video_ref(upscale_video)},
                "role": "base_video",
            }
        )
        body: dict = {
            "model": self.model or os.getenv("MINIMAX_VIDEO_MODEL", DEFAULT_MODEL),
            "content": content,
            "resolution": "2K",
        }
        resp = requests.post(
            f"{self._host()}/v2/video_regeneration",
            headers={**self.auth_headers(), "Content-Type": "application/json"},
            json=body,
            timeout=60,
        )
        resp.raise_for_status()
        payload = resp.json()
        task_id = payload.get("task_id")
        if not task_id:
            raise Exception(
                f"provider=minimax_h3 no task_id in response: {json.dumps(payload, ensure_ascii=False)}"
            )
        return task_id

    def poll_once(self, handle: str) -> tuple[str, dict]:
        resp = requests.get(
            f"{self._host()}/v2/query/video_generation/{handle}",
            headers=self.auth_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        task = resp.json().get("task") or {}
        status = task.get("status")
        if status == "succeeded":
            return STATUS_SUCCEEDED, task
        if status in ("failed", "cancelled"):
            return STATUS_FAILED, task
        return STATUS_PENDING, task

    def extract_video_url(self, handle: str, result: dict) -> str:
        url = (result.get("content") or {}).get("url")
        if not url:
            raise Exception(
                f"provider=minimax_h3 succeeded but no content.url: {json.dumps(result, ensure_ascii=False)}"
            )
        return url

    def cancel(self, handle: str, output_file: str | None = None) -> str:
        """Cancel a queued task. The upstream DELETE auto-dispatches by state
        (queued -> cancelled, not billed; succeeded/failed -> record DELETED;
        running -> error), so check state first: a cancel request must never
        delete a finished task's record."""
        resp = requests.get(
            f"{self._host()}/v2/query/video_generation/{handle}",
            headers=self.auth_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        status = (resp.json().get("task") or {}).get("status")
        if status == "queued":
            resp = requests.delete(
                f"{self._host()}/v2/video_generation/{handle}",
                headers=self.auth_headers(),
                timeout=30,
            )
            resp.raise_for_status()
            if output_file:
                set_task_status(output_file, handle, "cancelled")
            return f"task {handle} cancelled (queued tasks are not billed)"
        if status == "running":
            return f"task {handle} is running and cannot be cancelled; wait for it to finish"
        if status in ("succeeded", "failed"):
            return (
                f"task {handle} already {status}; nothing cancelled (the upstream "
                "endpoint would DELETE the task record)"
            )
        if status == "cancelled":
            return f"task {handle} was already cancelled"
        return f"task {handle} is in unknown state {status!r}; nothing done"


PROVIDER = MiniMaxH3Provider
