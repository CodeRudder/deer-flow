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


class MiniMaxH3Provider(BaseVideoProvider):
    name = "minimax_h3"
    supported_params = {"resolution", "duration", "ratio"}

    def api_key(self) -> str | None:
        # Video-dedicated key preferred; falls back to the shared MINIMAX_API_KEY
        # (also used by music/podcast skills) so existing setups keep working.
        return os.getenv("MINIMAX_VIDEO_API_KEY") or os.getenv("MINIMAX_API_KEY")

    def _host(self) -> str:
        return os.getenv("MINIMAX_API_HOST", DEFAULT_HOST).rstrip("/")

    def create_task(
        self, prompt_text: str, reference_images: list[str], params: dict
    ) -> str:
        first_frame = reference_images[0] if reference_images else None
        content: list[dict] = [{"type": "text", "text": prompt_text}]
        if first_frame:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": image_ref(first_frame)},
                    "role": "first_frame",
                }
            )
        body: dict = {
            "model": self.model or os.getenv("MINIMAX_VIDEO_MODEL", DEFAULT_MODEL),
            "content": content,
            "resolution": params.get("resolution") or "768P",
            "duration": params.get("duration") or 4,
        }
        # T2V requires a non-adaptive ratio; I2V ratio is fixed by the first frame,
        # so it is omitted (sending it errors on the I2V path).
        if not first_frame:
            body["ratio"] = params.get("ratio") or "16:9"

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
