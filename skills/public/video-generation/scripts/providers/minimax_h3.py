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
)

DEFAULT_HOST = "https://api.minimaxi.com"  # 国内站；国际站 https://api.minimax.io
DEFAULT_MODEL = "MiniMax-H3"
# Keep in sync with the aspect-ratio row in SKILL.md's output-settings table.
SUPPORTED_RATIOS = ("16:9", "9:16", "1:1", "4:3", "21:9")


class MiniMaxH3Provider(BaseVideoProvider):
    name = "minimax_h3"
    supported_params = {"resolution", "duration", "ratio", "image_role"}
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
        self, prompt_text: str, reference_images: list[str], image_role: str
    ) -> tuple[list[dict], bool]:
        """Build the V2 content[] array for the requested mode.

        Returns (content, send_ratio). Per the official H3 examples, only pure
        text-to-video sends `ratio`; every mode that carries an image (first/
        last frame or reference) omits it — the image determines the aspect
        ratio, and sending `ratio` on those paths errors.
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
            return content, True  # T2V — the only mode that sends ratio

        if image_role == "reference":
            # Ref2VA: identity/style transfer (not a frame). All images pass
            # through; upstream accepts up to 9 and rejects the rest.
            for path in images:
                content.append(image_item(path, "reference_image"))
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

        return content, False

    def create_task(
        self, prompt_text: str, reference_images: list[str], params: dict
    ) -> str:
        image_role = params.get("image_role")
        if image_role is None:
            image_role = "first_frame"
        content, send_ratio = self._build_content(
            prompt_text, reference_images, image_role
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
            "duration": duration if duration is not None else 4,
        }
        # Only pure T2V takes a `ratio`; any image-bearing mode lets the image
        # fix the aspect ratio (sending `ratio` there errors).
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


PROVIDER = MiniMaxH3Provider
