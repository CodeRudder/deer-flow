import logging
from typing import Any

import httpx

from deerflow.config.vision_model_config import VisionModelConfig

logger = logging.getLogger(__name__)

VISION_UNDERSTANDING_ERROR_MESSAGE = "多模态的工具理解调用异常"


class VisionUnderstandingError(RuntimeError):
    """Raised when the independent vision model cannot produce text output."""


class VisionClient:
    """HTTP client for the configured independent vision model."""

    def __init__(self, config: VisionModelConfig) -> None:
        self.config = config

    def _build_headers(self) -> dict[str, str]:
        headers = {
            "content-type": "application/json",
        }
        if self.config.api_key:
            headers["authorization"] = f"Bearer {self.config.api_key}"
        if self.config.headers:
            headers.update(self.config.headers)
        return headers

    def _build_payload(self, *, image_base64: str, mime_type: str) -> dict[str, Any]:
        return {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "stream": self.config.stream,
            "system": self.config.system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime_type,
                                "data": image_base64,
                            },
                        },
                        {
                            "type": "text",
                            "text": self.config.prompt,
                        },
                    ],
                }
            ],
        }

    def _extract_text_content(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            raise VisionUnderstandingError("Vision model response is not a JSON object")
        content = payload.get("content")
        if not isinstance(content, list):
            raise VisionUnderstandingError("Vision model response has no content list")

        text_parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") != "text":
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                text_parts.append(text.strip())

        if not text_parts:
            raise VisionUnderstandingError("Vision model response has no text content")
        return "\n\n".join(text_parts)

    async def understand_image_base64(
        self,
        *,
        image_base64: str,
        mime_type: str,
        image_path: str,
    ) -> str:
        """Call the configured vision endpoint and return text image understanding."""
        payload = self._build_payload(image_base64=image_base64, mime_type=mime_type)
        headers = self._build_headers()
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout) as client:
                response = await client.post(self.config.base_url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
            return self._extract_text_content(data)
        except VisionUnderstandingError:
            logger.exception("Vision understanding returned unusable response: model=%s image_path=%s", self.config.name, image_path)
            raise
        except Exception as exc:
            logger.exception("Vision understanding request failed: model=%s image_path=%s error=%s", self.config.name, image_path, exc)
            raise VisionUnderstandingError(str(exc)) from exc
