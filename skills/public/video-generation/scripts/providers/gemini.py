"""Gemini Veo adapter — migrated from generate.py:145-184.

Gemini is the odd one out: auth is x-goog-api-key (not Bearer), the video URL
download also needs that header, polling is operation-based (done flag, no
explicit failed state), and reference images are passed as multiple asset
images rather than a single first frame.
"""

import base64
import os

import requests

from .base import STATUS_PENDING, STATUS_SUCCEEDED, BaseVideoProvider, ensure_output_dir

DEFAULT_MODEL = "veo-3.1-generate-preview"
API_ROOT = "https://generativelanguage.googleapis.com/v1beta"


class GeminiVideoProvider(BaseVideoProvider):
    name = "gemini"
    supported_params: set[str] = set()  # Gemini T2V/I2V here take no extra params
    default_model = DEFAULT_MODEL
    api_key_envs = ("GEMINI_API_KEY",)
    known_models = (DEFAULT_MODEL,)
    poll_interval = 3
    # Baseline polled uncapped; the sandbox kills a run at 600s anyway. Cap at
    # 3s x 200 = 600s so we never give up earlier than the environment forces.
    poll_max_attempts = 200

    def api_key(self) -> str | None:
        return os.getenv("GEMINI_API_KEY")

    def auth_headers(self) -> dict:
        return {"x-goog-api-key": self.api_key()}

    def create_task(
        self, prompt_text: str, reference_images: list[str], params: dict
    ) -> str:
        request_json: dict = {"instances": [{"prompt": prompt_text}]}
        reference_payload = []
        for reference_image in reference_images:
            with open(reference_image, "rb") as f:
                image_b64 = base64.b64encode(f.read()).decode("utf-8")
            reference_payload.append(
                {
                    "image": {
                        "mimeType": "image/jpeg",
                        "bytesBase64Encoded": image_b64,
                    },
                    "referenceType": "asset",
                }
            )
        if reference_payload:
            request_json["instances"][0]["referenceImages"] = reference_payload

        model = self.model or DEFAULT_MODEL
        resp = requests.post(
            f"{API_ROOT}/models/{model}:predictLongRunning",
            headers={**self.auth_headers(), "Content-Type": "application/json"},
            json=request_json,
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["name"]

    def poll_once(self, handle: str) -> tuple[str, dict]:
        resp = requests.get(
            f"{API_ROOT}/{handle}", headers=self.auth_headers(), timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("done", False):
            return STATUS_SUCCEEDED, data
        return STATUS_PENDING, data

    def extract_video_url(self, handle: str, result: dict) -> str:
        sample = result["response"]["generateVideoResponse"]["generatedSamples"][0]
        return sample["video"]["uri"]

    def download(self, url: str, output_file: str) -> None:
        resp = requests.get(url, headers=self.auth_headers(), timeout=300)
        resp.raise_for_status()
        ensure_output_dir(output_file)
        with open(output_file, "wb") as f:
            f.write(resp.content)


PROVIDER = GeminiVideoProvider
