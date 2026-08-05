import json
import logging
from typing import Any

import httpx

from deerflow.config.vision_model_config import VisionConfig, VisionModelConfig

logger = logging.getLogger(__name__)

VISION_UNDERSTANDING_ERROR_MESSAGE = "多模态的工具理解调用异常"


class VisionUnderstandingError(RuntimeError):
    """Raised when the independent vision model cannot produce text output."""


class VisionClient:
    """HTTP client for the configured independent vision model."""

    def __init__(self, model_config: VisionModelConfig, vision_config: VisionConfig) -> None:
        self.model_config = model_config
        self.vision_config = vision_config

    def _build_headers(self) -> dict[str, str]:
        headers = {
            "content-type": "application/json",
        }
        if self.model_config.api_key:
            headers["authorization"] = f"Bearer {self.model_config.api_key}"
        if self.model_config.headers:
            headers.update(self.model_config.headers)
        return headers

    def _build_payload(self, *, image_base64: str, mime_type: str) -> dict[str, Any]:
        return {
            "model": self.model_config.model,
            "max_tokens": self.model_config.max_tokens,
            "stream": self.model_config.stream,
            "system": self.vision_config.system_prompt,
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
                            "text": self.vision_config.prompt,
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

    def _extract_anthropic_text(self, body: str) -> str:
        parts: list[str] = []
        for raw in body.splitlines():
            line = raw.strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if not data:
                continue
            try:
                event = json.loads(data)
            except (ValueError, TypeError):
                continue
            if not isinstance(event, dict) or event.get("type") != "content_block_delta":
                continue
            delta = event.get("delta") or {}
            if delta.get("type") != "text_delta":
                continue
            text = delta.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
        if not parts:
            raise VisionUnderstandingError("Vision model (anthropic, stream) response had no text content")
        return "".join(parts)

    def _build_openai_payload(self, *, image_base64: str, mime_type: str) -> dict[str, Any]:
        data_url = f"data:{mime_type};base64,{image_base64}"
        return {
            "model": self.model_config.model,
            "max_tokens": self.model_config.max_tokens,
            "stream": self.model_config.stream,
            "messages": [
                {"role": "system", "content": self.vision_config.system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": self.vision_config.prompt},
                    ],
                },
            ],
        }

    def _extract_openai_text(self, body: str) -> str:
        parts: list[str] = []
        for raw in body.splitlines():
            line = raw.strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if not data or data == "[DONE]":
                continue
            try:
                chunk = json.loads(data)
            except (ValueError, TypeError):
                continue
            if not isinstance(chunk, dict):
                continue
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            content = delta.get("content")
            if isinstance(content, str) and content:
                parts.append(content)
        if parts:
            return "".join(parts)
        # Fallback: some endpoints ignore stream and return a single JSON object.
        try:
            chunk = json.loads(body)
        except (ValueError, TypeError):
            chunk = None
        if isinstance(chunk, dict):
            choices = chunk.get("choices") or []
            if choices:
                message = choices[0].get("message") or {}
                content = message.get("content")
                if isinstance(content, str) and content:
                    return content
        raise VisionUnderstandingError("Vision model (openai) response had no text content")

    async def understand_image_base64(
        self,
        *,
        image_base64: str,
        mime_type: str,
        image_path: str,
    ) -> str:
        """Call the configured vision endpoint and return text image understanding."""
        is_openai = self.model_config.api_style == "openai"
        payload = self._build_openai_payload(image_base64=image_base64, mime_type=mime_type) if is_openai else self._build_payload(image_base64=image_base64, mime_type=mime_type)
        headers = self._build_headers()
        try:
            async with httpx.AsyncClient(timeout=self.model_config.timeout) as client:
                response = await client.post(self.model_config.base_url, headers=headers, json=payload)
                response.raise_for_status()
                if is_openai:
                    return self._extract_openai_text(response.text)
                if self.model_config.stream:
                    return self._extract_anthropic_text(response.text)
                return self._extract_text_content(response.json())
        except VisionUnderstandingError:
            logger.exception("Vision understanding returned unusable response: model=%s image_path=%s", self.model_config.name, image_path)
            raise
        except Exception as exc:
            logger.exception("Vision understanding request failed: model=%s image_path=%s error=%s", self.model_config.name, image_path, exc)
            raise VisionUnderstandingError(str(exc)) from exc
