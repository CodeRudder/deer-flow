from __future__ import annotations

import json

import httpx
import pytest

from deerflow.config.vision_model_config import VisionModelConfig
from deerflow.vision import vision_client as vision_module


def _vision_config() -> VisionModelConfig:
    return VisionModelConfig(
        name="company-vision",
        model="kimi-k2.6",
        base_url="https://vision.example.test/api/v1/messages",
        api_key="test-key",
    )


def test_extract_text_content_ignores_thinking() -> None:
    text = vision_module.VisionClient(_vision_config())._extract_text_content(
        {
            "content": [
                {"type": "thinking", "thinking": "hidden chain of thought"},
                {"type": "text", "text": "图片显示一面红旗。"},
            ]
        }
    )

    assert text == "图片显示一面红旗。"
    assert "hidden" not in text


def test_extract_text_content_requires_text() -> None:
    with pytest.raises(vision_module.VisionUnderstandingError, match="no text"):
        vision_module.VisionClient(_vision_config())._extract_text_content({"content": [{"type": "thinking", "thinking": "..."}]})


def test_build_payload_uses_base64_source() -> None:
    payload = vision_module.VisionClient(_vision_config())._build_payload(
        image_base64="BASE64",
        mime_type="image/png",
    )

    image_part = payload["messages"][0]["content"][0]
    assert image_part == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": "BASE64",
        },
    }


def test_build_payload_includes_default_top_level_system_prompt() -> None:
    payload = vision_module.VisionClient(_vision_config())._build_payload(
        image_base64="BASE64",
        mime_type="image/png",
    )

    assert payload["system"]
    assert "多模态视觉理解模型" in payload["system"]


def test_build_payload_uses_configured_top_level_system_prompt() -> None:
    config = VisionModelConfig(
        name="doubao-vision",
        model="doubao-seed-2.0-pro",
        base_url="https://vision.example.test/api/v1/messages",
        system_prompt="自定义视觉系统提示词",
    )

    payload = vision_module.VisionClient(config)._build_payload(
        image_base64="BASE64",
        mime_type="image/png",
    )

    assert payload["system"] == "自定义视觉系统提示词"


def test_understand_image_base64_posts_payload_and_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    original_client = httpx.Client

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["content_type"] = request.headers.get("content-type")
        captured["payload"] = json.loads(request.read().decode())
        return httpx.Response(200, json={"content": [{"type": "text", "text": "图片里有日落。"}]})

    monkeypatch.setattr(httpx, "Client", lambda timeout: original_client(transport=httpx.MockTransport(_handler), timeout=timeout))

    result = vision_module.VisionClient(_vision_config()).understand_image_base64(
        image_base64="BASE64",
        mime_type="image/png",
        image_path="/mnt/user-data/uploads/sunset.png",
    )

    assert result == "图片里有日落。"
    assert captured["url"] == "https://vision.example.test/api/v1/messages"
    assert captured["authorization"] == "Bearer test-key"
    assert captured["content_type"] == "application/json"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["system"]
    assert payload["messages"][0]["content"][0]["source"]["type"] == "base64"


def test_understand_image_base64_omits_authorization_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    original_client = httpx.Client

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"content": [{"type": "text", "text": "图片里有日落。"}]})

    monkeypatch.setattr(httpx, "Client", lambda timeout: original_client(transport=httpx.MockTransport(_handler), timeout=timeout))

    config = VisionModelConfig(
        name="company-vision",
        model="kimi-k2.6",
        base_url="https://vision.example.test/api/v1/messages",
    )

    result = vision_module.VisionClient(config).understand_image_base64(
        image_base64="BASE64",
        mime_type="image/png",
        image_path="/mnt/user-data/uploads/sunset.png",
    )

    assert result == "图片里有日落。"
    assert captured["authorization"] is None


def test_understand_image_base64_normalizes_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    original_client = httpx.Client

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    monkeypatch.setattr(httpx, "Client", lambda timeout: original_client(transport=httpx.MockTransport(_handler), timeout=timeout))

    with pytest.raises(vision_module.VisionUnderstandingError):
        vision_module.VisionClient(_vision_config()).understand_image_base64(
            image_base64="BASE64",
            mime_type="image/png",
            image_path="/mnt/user-data/uploads/broken.png",
        )


def test_understand_image_base64_normalizes_unusable_body(monkeypatch: pytest.MonkeyPatch) -> None:
    original_client = httpx.Client

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": [{"type": "thinking", "thinking": "..."}]})

    monkeypatch.setattr(httpx, "Client", lambda timeout: original_client(transport=httpx.MockTransport(_handler), timeout=timeout))

    with pytest.raises(vision_module.VisionUnderstandingError, match="no text"):
        vision_module.VisionClient(_vision_config()).understand_image_base64(
            image_base64="BASE64",
            mime_type="image/png",
            image_path="/mnt/user-data/uploads/no-text.png",
        )


def test_understand_image_base64_normalizes_missing_content_list(monkeypatch: pytest.MonkeyPatch) -> None:
    original_client = httpx.Client

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": "not-a-list"})

    monkeypatch.setattr(httpx, "Client", lambda timeout: original_client(transport=httpx.MockTransport(_handler), timeout=timeout))

    with pytest.raises(vision_module.VisionUnderstandingError, match="no content list"):
        vision_module.VisionClient(_vision_config()).understand_image_base64(
            image_base64="BASE64",
            mime_type="image/png",
            image_path="/mnt/user-data/uploads/no-list.png",
        )
