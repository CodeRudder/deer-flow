"""Legacy MiniMax Hailuo V1 adapter — migrated from generate.py:107-131.

Kept for compatibility, NOT recommended for new work (use minimax_h3). V1 differs
from H3 V2 in every step: flat `prompt` string (no content[]), first_frame_image
as a data URL, poll via ?task_id= query param, and a mandatory extra
files/retrieve step to turn the returned file_id into a download URL.
"""

import os

import requests

from .base import (
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SUCCEEDED,
    BaseVideoProvider,
    to_data_url,
)

DEFAULT_HOST = "https://api.minimaxi.com"
DEFAULT_MODEL = "MiniMax-Hailuo-2.3"


def _check_base_resp(payload: dict) -> None:
    base = payload.get("base_resp") or {}
    if base.get("status_code", 0) != 0:
        raise Exception(
            f"MiniMax error {base.get('status_code')}: {base.get('status_msg')}"
        )


class MiniMaxV1Provider(BaseVideoProvider):
    name = "minimax_v1"
    supported_params: set[str] = (
        set()
    )  # V1 uses resolution/duration on the model side; aspect_ratio ignored
    default_model = DEFAULT_MODEL
    api_key_envs = ("MINIMAX_VIDEO_API_KEY", "MINIMAX_API_KEY")
    known_models = (DEFAULT_MODEL,)
    poll_interval = 3

    def api_key(self) -> str | None:
        # Same resolution as minimax_h3: dedicated video key, else shared key.
        return os.getenv("MINIMAX_VIDEO_API_KEY") or os.getenv("MINIMAX_API_KEY")

    def _host(self) -> str:
        return os.getenv("MINIMAX_API_HOST", DEFAULT_HOST).rstrip("/")

    def create_task(
        self, prompt_text: str, reference_images: list[str], params: dict
    ) -> str:
        body: dict = {
            "model": self.model or os.getenv("MINIMAX_VIDEO_MODEL", DEFAULT_MODEL),
            "prompt": prompt_text,
        }
        if reference_images:
            body["first_frame_image"] = to_data_url(reference_images[0])
        resp = requests.post(
            f"{self._host()}/v1/video_generation",
            headers={**self.auth_headers(), "Content-Type": "application/json"},
            json=body,
            timeout=60,
        )
        resp.raise_for_status()
        payload = resp.json()
        _check_base_resp(payload)
        return payload["task_id"]

    def poll_once(self, handle: str) -> tuple[str, dict]:
        resp = requests.get(
            f"{self._host()}/v1/query/video_generation",
            headers=self.auth_headers(),
            params={"task_id": handle},
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        status = payload.get("status")
        if status == "Success":
            return STATUS_SUCCEEDED, payload
        if status == "Fail":
            return STATUS_FAILED, payload
        # Surface query-level errors (bad task_id, auth) that arrive as a non-zero
        # base_resp without a terminal status, then keep polling.
        _check_base_resp(payload)
        return STATUS_PENDING, payload

    def extract_video_url(self, handle: str, result: dict) -> str:
        # V1 extra step: poll returns a file_id; exchange it for a download URL.
        file_id = result["file_id"]
        resp = requests.get(
            f"{self._host()}/v1/files/retrieve",
            headers=self.auth_headers(),
            params={"file_id": file_id},
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        _check_base_resp(payload)
        return payload["file"]["download_url"]


PROVIDER = MiniMaxV1Provider
