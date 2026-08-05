from __future__ import annotations

import json

import httpx
import pytest

from deerflow.config.vision_model_config import VisionConfig, VisionModelConfig
from deerflow.vision import vision_client as vision_module


def _vision_config(stream: bool = False) -> VisionModelConfig:
    return VisionModelConfig(
        name="company-vision",
        model="kimi-k2.6",
        base_url="https://vision.example.test/api/v1/messages",
        api_key="test-key",
        stream=stream,
    )


def _vision_client(
    model_config: VisionModelConfig | None = None,
    vision_config: VisionConfig | None = None,
) -> vision_module.VisionClient:
    return vision_module.VisionClient(
        model_config or _vision_config(),
        vision_config or VisionConfig(),
    )


def test_extract_text_content_ignores_thinking() -> None:
    text = _vision_client()._extract_text_content(
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
        _vision_client()._extract_text_content({"content": [{"type": "thinking", "thinking": "..."}]})


def test_build_payload_uses_base64_source() -> None:
    payload = _vision_client()._build_payload(
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
    payload = _vision_client()._build_payload(
        image_base64="BASE64",
        mime_type="image/png",
    )

    assert payload["system"]
    assert "多模态视觉理解模型" in payload["system"]


def test_build_payload_uses_configured_top_level_system_prompt() -> None:
    config = VisionConfig(
        system_prompt="自定义视觉系统提示词",
    )
    payload = _vision_client(vision_config=config)._build_payload(
        image_base64="BASE64",
        mime_type="image/png",
    )

    assert payload["system"] == "自定义视觉系统提示词"


def test_build_payload_uses_configured_shared_prompt() -> None:
    config = VisionConfig(
        prompt="请提取图片中的核心内容。",
    )
    payload = _vision_client(vision_config=config)._build_payload(
        image_base64="BASE64",
        mime_type="image/png",
    )

    assert payload["messages"][0]["content"][1]["text"] == "请提取图片中的核心内容。"


@pytest.mark.asyncio
async def test_understand_image_base64_posts_payload_and_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    original_client = httpx.AsyncClient

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["content_type"] = request.headers.get("content-type")
        captured["payload"] = json.loads(request.read().decode())
        return httpx.Response(200, json={"content": [{"type": "text", "text": "图片里有日落。"}]})

    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout: original_client(transport=httpx.MockTransport(_handler), timeout=timeout))

    result = await _vision_client().understand_image_base64(
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


@pytest.mark.asyncio
async def test_understand_image_base64_omits_authorization_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    original_client = httpx.AsyncClient

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"content": [{"type": "text", "text": "图片里有日落。"}]})

    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout: original_client(transport=httpx.MockTransport(_handler), timeout=timeout))

    config = VisionModelConfig(
        name="doubao-vision",
        model="kimi-k2.6",
        base_url="https://vision.example.test/api/v1/messages",
    )

    result = await _vision_client(model_config=config).understand_image_base64(
        image_base64="BASE64",
        mime_type="image/png",
        image_path="/mnt/user-data/uploads/sunset.png",
    )

    assert result == "图片里有日落。"
    assert captured["authorization"] is None


@pytest.mark.asyncio
async def test_understand_image_base64_normalizes_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    original_client = httpx.AsyncClient

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout: original_client(transport=httpx.MockTransport(_handler), timeout=timeout))

    with pytest.raises(vision_module.VisionUnderstandingError):
        await _vision_client().understand_image_base64(
            image_base64="BASE64",
            mime_type="image/png",
            image_path="/mnt/user-data/uploads/broken.png",
        )


@pytest.mark.asyncio
async def test_understand_image_base64_normalizes_unusable_body(monkeypatch: pytest.MonkeyPatch) -> None:
    original_client = httpx.AsyncClient

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": [{"type": "thinking", "thinking": "..."}]})

    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout: original_client(transport=httpx.MockTransport(_handler), timeout=timeout))

    with pytest.raises(vision_module.VisionUnderstandingError, match="no text"):
        await _vision_client().understand_image_base64(
            image_base64="BASE64",
            mime_type="image/png",
            image_path="/mnt/user-data/uploads/no-text.png",
        )


@pytest.mark.asyncio
async def test_understand_image_base64_normalizes_missing_content_list(monkeypatch: pytest.MonkeyPatch) -> None:
    original_client = httpx.AsyncClient

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": "not-a-list"})

    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout: original_client(transport=httpx.MockTransport(_handler), timeout=timeout))

    with pytest.raises(vision_module.VisionUnderstandingError, match="no content list"):
        await _vision_client().understand_image_base64(
            image_base64="BASE64",
            mime_type="image/png",
            image_path="/mnt/user-data/uploads/no-list.png",
        )


def _openai_vision_config(stream: bool = True) -> VisionModelConfig:
    return VisionModelConfig(
        name="gpt-vision",
        model="gpt-5.5",
        base_url="https://vision.example.test/openai/v1/chat/completions",
        api_key="test-key",
        protocol="openai",
        stream=stream,
    )


def test_build_openai_payload_uses_image_url_and_honors_stream() -> None:
    payload = _vision_client(_openai_vision_config(stream=True))._build_openai_payload(
        image_base64="BASE64",
        mime_type="image/png",
    )

    assert payload["stream"] is True
    messages = payload["messages"]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == VisionConfig().system_prompt
    user_content = messages[1]["content"]
    assert user_content[0] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,BASE64"},
    }
    assert user_content[1] == {"type": "text", "text": VisionConfig().prompt}


def test_build_openai_payload_honors_stream_false() -> None:
    payload = _vision_client(_openai_vision_config(stream=False))._build_openai_payload(
        image_base64="BASE64",
        mime_type="image/png",
    )

    assert payload["stream"] is False


def test_build_payload_honors_stream_true() -> None:
    payload = _vision_client(_vision_config(stream=True))._build_payload(
        image_base64="BASE64",
        mime_type="image/png",
    )

    assert payload["stream"] is True


def test_extract_openai_text_aggregates_sse_deltas() -> None:
    body = 'data: {"choices":[{"delta":{"content":"A "}}]}\n\ndata: {"choices":[{"delta":{"content":"red "}}]}\n\ndata: {"choices":[{"delta":{"content":"square."}}]}\n\ndata: [DONE]\n\n'

    text = _vision_client(_openai_vision_config())._extract_openai_text(body)

    assert text == "A red square."


def test_extract_openai_text_falls_back_to_non_stream_json() -> None:
    body = json.dumps({"choices": [{"message": {"content": "A blue circle."}}]})

    text = _vision_client(_openai_vision_config())._extract_openai_text(body)

    assert text == "A blue circle."


def test_extract_openai_text_raises_when_no_text() -> None:
    with pytest.raises(vision_module.VisionUnderstandingError, match="no text"):
        _vision_client(_openai_vision_config())._extract_openai_text("data: [DONE]\n\n")


@pytest.mark.asyncio
async def test_understand_image_base64_openai_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    original_client = httpx.AsyncClient

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = json.loads(request.read().decode())
        sse = 'data: {"choices":[{"delta":{"content":"A "}}]}\n\ndata: {"choices":[{"delta":{"content":"small "}}]}\n\ndata: {"choices":[{"delta":{"content":"red square."}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, text=sse)

    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout: original_client(transport=httpx.MockTransport(_handler), timeout=timeout))

    result = await _vision_client(_openai_vision_config()).understand_image_base64(
        image_base64="BASE64",
        mime_type="image/png",
        image_path="/mnt/user-data/uploads/red.png",
    )

    assert result == "A small red square."
    assert captured["url"] == "https://vision.example.test/openai/v1/chat/completions"
    assert captured["payload"]["stream"] is True
    assert captured["payload"]["messages"][1]["content"][0]["type"] == "image_url"


@pytest.mark.asyncio
async def test_understand_image_base64_openai_normalizes_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    original_client = httpx.AsyncClient

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout: original_client(transport=httpx.MockTransport(_handler), timeout=timeout))

    with pytest.raises(vision_module.VisionUnderstandingError):
        await _vision_client(_openai_vision_config()).understand_image_base64(
            image_base64="BASE64",
            mime_type="image/png",
            image_path="/mnt/user-data/uploads/broken.png",
        )


def test_extract_anthropic_text_aggregates_content_block_deltas() -> None:
    body = (
        'event: message_start\ndata: {"type":"message_start","message":{"content":[]}}\n\n'
        'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"图片"}}\n\n'
        'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"是红色。"}}\n\n'
        'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    )

    text = _vision_client(_vision_config(stream=True))._extract_anthropic_text(body)

    assert text == "图片是红色。"


def test_extract_anthropic_text_ignores_thinking_delta() -> None:
    body = 'data: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"hidden"}}\n\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"可见内容"}}\n\n'

    text = _vision_client(_vision_config(stream=True))._extract_anthropic_text(body)

    assert text == "可见内容"
    assert "hidden" not in text


def test_extract_anthropic_text_raises_when_no_text() -> None:
    with pytest.raises(vision_module.VisionUnderstandingError, match="no text"):
        _vision_client(_vision_config(stream=True))._extract_anthropic_text('data: {"type":"message_stop"}\n\n')


@pytest.mark.asyncio
async def test_understand_image_base64_anthropic_stream_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    original_client = httpx.AsyncClient

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.read().decode())
        sse = 'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"一面"}}\n\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"红旗。"}}\n\ndata: {"type":"message_stop"}\n\n'
        return httpx.Response(200, text=sse)

    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout: original_client(transport=httpx.MockTransport(_handler), timeout=timeout))

    result = await _vision_client(_vision_config(stream=True)).understand_image_base64(
        image_base64="BASE64",
        mime_type="image/png",
        image_path="/mnt/user-data/uploads/flag.png",
    )

    assert result == "一面红旗。"
    assert captured["payload"]["stream"] is True


def test_extract_anthropic_text_ignores_non_object_data_lines() -> None:
    body = 'data: "ping"\ndata: [1, 2]\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"正文"}}\n\n'

    text = _vision_client(_vision_config(stream=True))._extract_anthropic_text(body)

    assert text == "正文"


def test_extract_openai_text_ignores_non_object_data_lines() -> None:
    body = 'data: "ping"\ndata: [1, 2]\ndata: {"choices":[{"delta":{"content":"正文"}}]}\n\ndata: [DONE]\n\n'

    text = _vision_client(_openai_vision_config())._extract_openai_text(body)

    assert text == "正文"
