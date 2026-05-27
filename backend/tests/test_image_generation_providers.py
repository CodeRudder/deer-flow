import base64
import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPO_ROOT / "skills" / "public" / "image-generation" / "scripts"


def _load_module(module_name: str, path: Path):
    sys.path.insert(0, str(SCRIPT_DIR))
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


generate_module = _load_module("image_generation_generate", SCRIPT_DIR / "generate.py")
qwen_module = _load_module(
    "image_generation_qwen_image",
    SCRIPT_DIR / "providers" / "qwen_image.py",
)
gemini_module = _load_module(
    "image_generation_gemini",
    SCRIPT_DIR / "providers" / "gemini.py",
)
openai_image_module = _load_module(
    "image_generation_openai_image",
    SCRIPT_DIR / "providers" / "openai_image.py",
)


def test_qwen_aspect_ratio_to_size():
    assert qwen_module._aspect_ratio_to_size("1:1") == "2048*2048"
    assert qwen_module._aspect_ratio_to_size("16:9") == "2048*1152"
    assert qwen_module._aspect_ratio_to_size("9:16") == "1152*2048"
    assert qwen_module._aspect_ratio_to_size("5:4") == "2048*1632"


def test_openai_image_aspect_ratio_to_size():
    assert openai_image_module._aspect_ratio_to_size("1:1") == "2048x2048"
    assert openai_image_module._aspect_ratio_to_size("16:9") == "3840x2160"
    assert openai_image_module._aspect_ratio_to_size("9:16") == "2160x3840"
    assert openai_image_module._aspect_ratio_to_size("5:4") == "2048x1632"


def test_openai_image_build_payload_uses_openai_compatible_shape(monkeypatch):
    monkeypatch.delenv("OPENAI_IMAGE_QUALITY", raising=False)
    monkeypatch.delenv("OPENAI_IMAGE_OUTPUT_FORMAT", raising=False)
    monkeypatch.delenv("OPENAI_IMAGE_RESPONSE_FORMAT", raising=False)

    payload = openai_image_module._build_payload(
        prompt_text="A quiet bookstore",
        model="gpt-image-2",
        aspect_ratio="16:9",
    )

    assert payload == {
        "model": "gpt-image-2",
        "prompt": "A quiet bookstore",
        "size": "3840x2160",
        "quality": "high",
        "output_format": "png",
        "response_format": "url",
        "n": 1,
    }


def test_qwen_build_payload_uses_expected_shape():
    payload = qwen_module._build_payload(
        prompt_text="A quiet bookstore",
        model="qwen-image-2.0-pro",
        aspect_ratio="1:1",
        negative_prompt="blurry",
        prompt_extend=False,
        watermark=True,
    )

    assert payload["model"] == "qwen-image-2.0-pro"
    assert payload["input"]["messages"][0]["role"] == "user"
    assert payload["input"]["messages"][0]["content"][0]["text"] == "A quiet bookstore"
    assert payload["parameters"]["negative_prompt"] == "blurry"
    assert payload["parameters"]["prompt_extend"] is False
    assert payload["parameters"]["watermark"] is True
    assert payload["parameters"]["size"] == "2048*2048"
    assert "n" not in payload["parameters"]


def test_qwen_extract_image_url():
    response = {
        "output": {
            "choices": [
                {
                    "message": {
                        "content": [{"image": "https://example.com/result.png?Expires=1"}]
                    }
                }
            ]
        }
    }

    assert qwen_module._extract_image_url(response) == "https://example.com/result.png?Expires=1"


def test_qwen_missing_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("QWEN_IMAGE_API_KEY", raising=False)

    result = qwen_module.generate(
        prompt_text="prompt",
        reference_images=[],
        output_file=str(tmp_path / "out.png"),
        aspect_ratio="1:1",
    )

    assert result == "QWEN_IMAGE_API_KEY is not set"


def test_qwen_rejects_reference_images(monkeypatch, tmp_path):
    monkeypatch.setenv("QWEN_IMAGE_API_KEY", "test-key")

    with pytest.raises(ValueError, match="reference images are not supported"):
        qwen_module.generate(
            prompt_text="prompt",
            reference_images=[str(tmp_path / "ref.png")],
            output_file=str(tmp_path / "out.png"),
            aspect_ratio="1:1",
        )


def test_qwen_generate_posts_and_downloads_image(monkeypatch, tmp_path):
    monkeypatch.setenv("QWEN_IMAGE_API_KEY", "test-key")
    monkeypatch.setenv("QWEN_IMAGE_BASE_URL", "https://qwen.example/api/v1")

    post_response = Mock()
    post_response.ok = True
    post_response.json.return_value = {
        "request_id": "req-123",
        "output": {
            "choices": [
                {
                    "message": {
                        "content": [{"image": "https://cdn.example/result.png?Expires=1"}]
                    }
                }
            ]
        },
    }
    get_response = Mock()
    get_response.ok = True
    get_response.content = b"fake-png-bytes"
    output_file = tmp_path / "out.png"

    with patch.object(qwen_module.requests, "post", return_value=post_response) as post, patch.object(
        qwen_module.requests, "get", return_value=get_response
    ) as get:
        result = qwen_module.generate(
            prompt_text="prompt",
            reference_images=[],
            output_file=str(output_file),
            aspect_ratio="1:1",
            model="qwen-image-2.0-pro",
            negative_prompt="bad",
            prompt_extend=True,
            watermark=False,
        )

    post.assert_called_once()
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer test-key"
    assert post.call_args.kwargs["json"]["parameters"]["negative_prompt"] == "bad"
    assert post.call_args.kwargs["json"]["parameters"]["size"] == "2048*2048"
    get.assert_called_once_with("https://cdn.example/result.png?Expires=1", timeout=(10, 60))
    assert output_file.read_bytes() == b"fake-png-bytes"
    assert "provider=qwen_image" in result
    assert "request_id=req-123" in result


def test_qwen_request_error_includes_response_summary(monkeypatch, tmp_path):
    monkeypatch.setenv("QWEN_IMAGE_API_KEY", "test-key")

    post_response = Mock()
    post_response.ok = False
    post_response.status_code = 400
    post_response.headers = {"X-Request-Id": "req-400"}
    post_response.text = '{"code":"InvalidParameter","message":"bad size"}'

    with patch.object(qwen_module.requests, "post", return_value=post_response):
        with pytest.raises(RuntimeError) as exc:
            qwen_module.generate(
                prompt_text="prompt",
                reference_images=[],
                output_file=str(tmp_path / "out.png"),
                aspect_ratio="16:9",
            )

    message = str(exc.value)
    assert "status=400" in message
    assert "request_id=req-400" in message
    assert "bad size" in message


def test_qwen_data_inspection_error_includes_rewrite_hint(monkeypatch, tmp_path):
    monkeypatch.setenv("QWEN_IMAGE_API_KEY", "test-key")

    post_response = Mock()
    post_response.ok = False
    post_response.status_code = 400
    post_response.headers = {"X-Request-Id": "req-inspection"}
    post_response.text = (
        '{"request_id":"req-inspection","code":"DataInspectionFailed",'
        '"message":"Green net check failed for input text"}'
    )
    post_response.json.return_value = {
        "request_id": "req-inspection",
        "code": "DataInspectionFailed",
        "message": "Green net check failed for input text",
    }

    with patch.object(qwen_module.requests, "post", return_value=post_response):
        with pytest.raises(RuntimeError) as exc:
            qwen_module.generate(
                prompt_text="prompt",
                reference_images=[],
                output_file=str(tmp_path / "out.png"),
                aspect_ratio="16:9",
            )

    message = str(exc.value)
    assert "DataInspectionFailed" in message
    assert "rewrite the prompt" in message
    assert "remove sensitive entities" in message


def test_qwen_uses_token_plan_base_url_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("QWEN_IMAGE_API_KEY", "test-key")
    monkeypatch.delenv("QWEN_IMAGE_BASE_URL", raising=False)

    post_response = Mock()
    post_response.ok = True
    post_response.json.return_value = {
        "output": {
            "choices": [
                {
                    "message": {
                        "content": [{"image": "https://cdn.example/result.png?Expires=1"}]
                    }
                }
            ]
        },
    }
    get_response = Mock()
    get_response.ok = True
    get_response.content = b"fake-png-bytes"

    with patch.object(qwen_module.requests, "post", return_value=post_response) as post, patch.object(
        qwen_module.requests, "get", return_value=get_response
    ):
        qwen_module.generate(
            prompt_text="prompt",
            reference_images=[],
            output_file=str(tmp_path / "out.png"),
            aspect_ratio="1:1",
        )

    assert post.call_args.args[0] == (
        "https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1"
        "/services/aigc/multimodal-generation/generation"
    )


def test_openai_image_missing_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_IMAGE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = openai_image_module.generate(
        prompt_text="prompt",
        reference_images=[],
        output_file=str(tmp_path / "out.png"),
        aspect_ratio="1:1",
    )

    assert result == "OPENAI_IMAGE_API_KEY is not set"


def test_openai_image_posts_and_downloads_url(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_IMAGE_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_IMAGE_BASE_URL", "https://www.packyapi.com/v1")

    post_response = Mock()
    post_response.ok = True
    post_response.json.return_value = {
        "data": [
            {
                "url": "https://cdn.example/result.png",
            }
        ]
    }
    get_response = Mock()
    get_response.ok = True
    get_response.content = b"fake-png-bytes"
    output_file = tmp_path / "out.png"

    with patch.object(openai_image_module.requests, "post", return_value=post_response) as post, patch.object(
        openai_image_module.requests, "get", return_value=get_response
    ) as get:
        result = openai_image_module.generate(
            prompt_text="prompt",
            reference_images=[],
            output_file=str(output_file),
            aspect_ratio="16:9",
            model="gpt-image-2",
        )

    post.assert_called_once()
    assert post.call_args.args[0] == "https://www.packyapi.com/v1/images/generations"
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer test-key"
    assert post.call_args.kwargs["json"]["model"] == "gpt-image-2"
    assert post.call_args.kwargs["json"]["size"] == "3840x2160"
    assert post.call_args.kwargs["json"]["quality"] == "high"
    assert post.call_args.kwargs["json"]["response_format"] == "url"
    get.assert_called_once_with("https://cdn.example/result.png", timeout=(10, 60))
    assert output_file.read_bytes() == b"fake-png-bytes"
    assert "provider=openai_image" in result


def test_openai_image_uses_openai_api_key_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_IMAGE_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_IMAGE_BASE_URL", "https://api.example/v1")

    post_response = Mock()
    post_response.ok = True
    post_response.json.return_value = {
        "data": [
            {
                "b64_json": base64.b64encode(b"fake-png-bytes").decode("ascii"),
            }
        ]
    }

    with patch.object(openai_image_module.requests, "post", return_value=post_response) as post:
        openai_image_module.generate(
            prompt_text="prompt",
            reference_images=[],
            output_file=str(tmp_path / "out.png"),
            aspect_ratio="1:1",
        )

    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer openai-key"


def test_openai_image_writes_b64_json(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_IMAGE_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_IMAGE_BASE_URL", raising=False)

    post_response = Mock()
    post_response.ok = True
    post_response.json.return_value = {
        "data": [
            {
                "b64_json": base64.b64encode(b"fake-png-bytes").decode("ascii"),
            }
        ]
    }
    output_file = tmp_path / "out.png"

    with patch.object(openai_image_module.requests, "post", return_value=post_response) as post:
        openai_image_module.generate(
            prompt_text="prompt",
            reference_images=[],
            output_file=str(output_file),
            aspect_ratio="1:1",
            model="gpt-image-2",
        )

    assert post.call_args.args[0] == "https://api.openai.com/v1/images/generations"
    assert output_file.read_bytes() == b"fake-png-bytes"


def test_generate_reads_qwen_prompt_and_negative_prompt(tmp_path):
    prompt_file = tmp_path / "prompt.json"
    prompt_file.write_text(
        json.dumps({"prompt": "A red kite", "negative_prompt": "blurry"}),
        encoding="utf-8",
    )

    prompt_text, negative_prompt = generate_module._read_prompt(str(prompt_file), "qwen_image")

    assert prompt_text == "A red kite"
    assert negative_prompt == "blurry"


def test_generate_leaves_gemini_prompt_file_raw(tmp_path):
    prompt_file = tmp_path / "prompt.json"
    raw_prompt = json.dumps({"prompt": "A red kite", "negative_prompt": "blurry"})
    prompt_file.write_text(raw_prompt, encoding="utf-8")

    prompt_text, negative_prompt = generate_module._read_prompt(str(prompt_file), "gemini")

    assert prompt_text == raw_prompt
    assert negative_prompt is None


def test_gemini_preserves_reference_images(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    prompt_text = "prompt"
    ref_file = tmp_path / "ref.jpg"
    ref_file.write_bytes(b"reference-bytes")
    output_file = tmp_path / "out.jpg"
    image_b64 = base64.b64encode(b"generated-bytes").decode("utf-8")

    response = Mock()
    response.raise_for_status = Mock()
    response.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [{"inlineData": {"data": image_b64}}],
                }
            }
        ]
    }

    with patch.object(gemini_module.requests, "post", return_value=response) as post:
        result = gemini_module.generate(
            prompt_text=prompt_text,
            reference_images=[str(ref_file)],
            output_file=str(output_file),
            aspect_ratio="16:9",
        )

    payload_parts = post.call_args.kwargs["json"]["contents"][0]["parts"]
    assert payload_parts[0]["inlineData"]["data"] == base64.b64encode(b"reference-bytes").decode("utf-8")
    assert payload_parts[-1]["text"] == prompt_text
    assert output_file.read_bytes() == b"generated-bytes"
    assert result == f"Successfully generated image to {output_file}"


def test_generate_uses_provider_registry(monkeypatch, tmp_path):
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("prompt", encoding="utf-8")
    calls = {}

    def fake_provider(**kwargs):
        calls.update(kwargs)
        return "ok"

    monkeypatch.setitem(generate_module.PROVIDERS, "fake-provider", fake_provider)
    monkeypatch.delenv("DEER_FLOW_CONFIG_PATH", raising=False)
    monkeypatch.delenv("IMAGE_GENERATION_PROVIDER", raising=False)

    result = generate_module.generate_image(
        prompt_file=str(prompt_file),
        reference_images=[],
        output_file=str(tmp_path / "out.png"),
        provider="fake-provider",
    )

    assert result == "ok"
    assert calls["prompt_text"] == "prompt"
    assert calls["reference_images"] == []


def test_generate_reads_provider_defaults_from_config(monkeypatch, tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
image_generation:
  providers:
    - name: fake-provider
      models:
        - fake-model-v1
""",
        encoding="utf-8",
    )
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("prompt", encoding="utf-8")
    calls = {}

    def fake_provider(**kwargs):
        calls.update(kwargs)
        return "ok"

    monkeypatch.setenv("DEER_FLOW_CONFIG_PATH", str(config_file))
    monkeypatch.delenv("IMAGE_GENERATION_PROVIDER", raising=False)
    monkeypatch.delenv("IMAGE_GENERATION_MODEL", raising=False)
    monkeypatch.delenv("IMAGE_GENERATION_PROMPT_EXTEND", raising=False)
    monkeypatch.delenv("IMAGE_GENERATION_WATERMARK", raising=False)
    monkeypatch.setitem(generate_module.PROVIDERS, "fake-provider", fake_provider)

    result = generate_module.generate_image(
        prompt_file=str(prompt_file),
        reference_images=[],
        output_file=str(tmp_path / "out.png"),
    )

    assert result == "ok"
    assert calls["model"] == "fake-model-v1"
    assert calls["prompt_extend"] is None
    assert calls["watermark"] is None


def test_env_provider_overrides_config(monkeypatch, tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
image_generation:
  provider: config-provider
""",
        encoding="utf-8",
    )
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("prompt", encoding="utf-8")
    calls = {}

    def fake_provider(**kwargs):
        calls.update(kwargs)
        return "ok"

    monkeypatch.setenv("DEER_FLOW_CONFIG_PATH", str(config_file))
    monkeypatch.setenv("IMAGE_GENERATION_PROVIDER", "env-provider")
    monkeypatch.setitem(generate_module.PROVIDERS, "config-provider", Mock(return_value="wrong"))
    monkeypatch.setitem(generate_module.PROVIDERS, "env-provider", fake_provider)

    result = generate_module.generate_image(
        prompt_file=str(prompt_file),
        reference_images=[],
        output_file=str(tmp_path / "out.png"),
    )

    assert result == "ok"
    assert calls["prompt_text"] == "prompt"
