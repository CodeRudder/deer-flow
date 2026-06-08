import base64
import importlib.util
import json
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


def test_openai_image_build_chat_completions_payload(monkeypatch):
    monkeypatch.delenv("OPENAI_IMAGE_CHAT_IMAGE_SIZE", raising=False)

    payload = openai_image_module._build_chat_completions_payload(
        prompt_text="生成图片：马斯克在抖音直播",
        model="gpt-image-2",
        aspect_ratio="1:1",
    )

    assert payload == {
        "model": "gpt-image-2",
        "messages": [
            {
                "role": "user",
                "content": "生成图片：马斯克在抖音直播",
            }
        ],
        "modalities": ["image"],
        "image_config": {
            "aspect_ratio": "1:1",
            "image_size": "1K",
        },
        "stream": False,
    }


def test_openai_image_extracts_markdown_image_url_from_chat_completion():
    response = {"choices": [{"message": {"content": ("![image](https://image-videofile.oss-accelerate.aliyuncs.com/codex/result.png)\n\n已为你生成图片。")}}]}

    assert openai_image_module._extract_chat_completions_image_url(response) == ("https://image-videofile.oss-accelerate.aliyuncs.com/codex/result.png")


def test_openai_image_session_disables_system_proxy_by_default(monkeypatch):
    monkeypatch.delenv("OPENAI_IMAGE_TRUST_ENV", raising=False)

    session = openai_image_module._build_session()

    assert session.trust_env is False


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
    response = {"output": {"choices": [{"message": {"content": [{"image": "https://example.com/result.png?Expires=1"}]}}]}}

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
        "output": {"choices": [{"message": {"content": [{"image": "https://cdn.example/result.png?Expires=1"}]}}]},
    }
    get_response = Mock()
    get_response.ok = True
    get_response.content = b"fake-png-bytes"
    output_file = tmp_path / "out.png"

    with patch.object(qwen_module.requests, "post", return_value=post_response) as post, patch.object(qwen_module.requests, "get", return_value=get_response) as get:
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
    post_response.text = '{"request_id":"req-inspection","code":"DataInspectionFailed","message":"Green net check failed for input text"}'
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
        "output": {"choices": [{"message": {"content": [{"image": "https://cdn.example/result.png?Expires=1"}]}}]},
    }
    get_response = Mock()
    get_response.ok = True
    get_response.content = b"fake-png-bytes"

    with patch.object(qwen_module.requests, "post", return_value=post_response) as post, patch.object(qwen_module.requests, "get", return_value=get_response):
        qwen_module.generate(
            prompt_text="prompt",
            reference_images=[],
            output_file=str(tmp_path / "out.png"),
            aspect_ratio="1:1",
        )

    assert post.call_args.args[0] == ("https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation")


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
    monkeypatch.delenv("OPENAI_IMAGE_MODE", raising=False)

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

    session = Mock()
    session.post.return_value = post_response
    session.get.return_value = get_response
    with patch.object(openai_image_module, "_build_session", return_value=session):
        result = openai_image_module.generate(
            prompt_text="prompt",
            reference_images=[],
            output_file=str(output_file),
            aspect_ratio="16:9",
            model="gpt-image-2",
        )

    session.post.assert_called_once()
    assert session.post.call_args.args[0] == "https://www.packyapi.com/v1/images/generations"
    assert session.post.call_args.kwargs["headers"]["Authorization"] == "Bearer test-key"
    assert session.post.call_args.kwargs["json"]["model"] == "gpt-image-2"
    assert session.post.call_args.kwargs["json"]["size"] == "3840x2160"
    assert session.post.call_args.kwargs["json"]["quality"] == "high"
    assert session.post.call_args.kwargs["json"]["response_format"] == "url"
    session.get.assert_called_once_with("https://cdn.example/result.png", timeout=(10, 60))
    assert output_file.read_bytes() == b"fake-png-bytes"
    assert "provider=openai_image" in result


def test_openai_image_uses_openai_api_key_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_IMAGE_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_IMAGE_BASE_URL", "https://api.example/v1")
    monkeypatch.delenv("OPENAI_IMAGE_MODE", raising=False)

    post_response = Mock()
    post_response.ok = True
    post_response.json.return_value = {
        "data": [
            {
                "b64_json": base64.b64encode(b"fake-png-bytes").decode("ascii"),
            }
        ]
    }

    session = Mock()
    session.post.return_value = post_response
    with patch.object(openai_image_module, "_build_session", return_value=session):
        openai_image_module.generate(
            prompt_text="prompt",
            reference_images=[],
            output_file=str(tmp_path / "out.png"),
            aspect_ratio="1:1",
        )

    assert session.post.call_args.kwargs["headers"]["Authorization"] == "Bearer openai-key"


def test_openai_image_writes_b64_json(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_IMAGE_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_IMAGE_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_IMAGE_MODE", raising=False)

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

    session = Mock()
    session.post.return_value = post_response
    with patch.object(openai_image_module, "_build_session", return_value=session):
        openai_image_module.generate(
            prompt_text="prompt",
            reference_images=[],
            output_file=str(output_file),
            aspect_ratio="1:1",
            model="gpt-image-2",
        )

    assert session.post.call_args.args[0] == "https://api.openai.com/v1/images/generations"
    assert output_file.read_bytes() == b"fake-png-bytes"


def test_openai_image_chat_completions_mode_downloads_markdown_image(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_IMAGE_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_IMAGE_BASE_URL", "https://www.duckcoding.ai/v1")
    monkeypatch.setenv("OPENAI_IMAGE_MODE", "chat_completions")

    post_response = Mock()
    post_response.ok = True
    post_response.json.return_value = {"choices": [{"message": {"content": "![image](https://cdn.example/duck.png)\n\n已为你生成图片。"}}]}
    get_response = Mock()
    get_response.ok = True
    get_response.content = b"duck-png-bytes"
    output_file = tmp_path / "out.png"

    session = Mock()
    session.post.return_value = post_response
    session.get.return_value = get_response
    with patch.object(openai_image_module, "_build_session", return_value=session):
        result = openai_image_module.generate(
            prompt_text="生成图片：马斯克在抖音直播",
            reference_images=[],
            output_file=str(output_file),
            aspect_ratio="1:1",
            model="gpt-image-2",
        )

    session.post.assert_called_once()
    assert session.post.call_args.args[0] == "https://www.duckcoding.ai/v1/chat/completions"
    payload = session.post.call_args.kwargs["json"]
    assert payload["messages"][0]["content"] == "生成图片：马斯克在抖音直播"
    assert payload["modalities"] == ["image"]
    assert payload["image_config"] == {"aspect_ratio": "1:1", "image_size": "1K"}
    session.get.assert_called_once_with("https://cdn.example/duck.png", timeout=(10, 60))
    assert output_file.read_bytes() == b"duck-png-bytes"
    assert "mode=chat_completions" in result


def test_generate_reads_qwen_prompt_and_negative_prompt(tmp_path):
    prompt_file = tmp_path / "prompt.json"
    prompt_file.write_text(
        json.dumps({"prompt": "A red kite", "negative_prompt": "blurry"}),
        encoding="utf-8",
    )

    prompt_text, negative_prompt = generate_module._read_prompt(str(prompt_file), "qwen_image")

    assert prompt_text == "A red kite"
    assert negative_prompt == "blurry"


def test_generate_reads_json_prompt_for_all_providers(tmp_path):
    prompt_file = tmp_path / "prompt.json"
    prompt_file.write_text(
        json.dumps({"prompt": "A red kite", "negative_prompt": "blurry"}),
        encoding="utf-8",
    )

    prompt_text, negative_prompt = generate_module._read_prompt(str(prompt_file), "gemini")

    assert prompt_text == "A red kite"
    assert negative_prompt == "blurry"


def test_generate_preserves_original_chinese_prompt_without_metadata(tmp_path):
    prompt_file = tmp_path / "prompt.json"
    original_prompt = "画一只坐在窗边看雨的橘猫，水彩风格，画面温暖一点。"
    prompt_file.write_text(
        json.dumps(
            {
                "prompt": original_prompt,
                "style": "水彩风格",
                "composition": "窗边",
                "technical": {"aspect_ratio": "16:9"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    prompt_text, negative_prompt = generate_module._read_prompt(str(prompt_file), "openai_image")

    assert prompt_text == original_prompt
    assert "composition" not in prompt_text
    assert "technical" not in prompt_text
    assert negative_prompt is None


def test_generate_keeps_json_without_prompt_backward_compatible(tmp_path):
    prompt_file = tmp_path / "prompt.json"
    raw_prompt = json.dumps({"style": "watercolor", "composition": "window side"})
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
    monkeypatch.setenv("DEER_FLOW_CONFIG_PATH", str(tmp_path / "missing-config.yaml"))
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


def test_generate_image_refuses_to_overwrite_existing_output(monkeypatch, tmp_path):
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("prompt", encoding="utf-8")
    output_file = tmp_path / "out.png"
    output_file.write_bytes(b"existing-image")
    provider = Mock(return_value="should-not-run")

    monkeypatch.setitem(generate_module.PROVIDERS, "fake-provider", provider)
    monkeypatch.setenv("DEER_FLOW_CONFIG_PATH", str(tmp_path / "missing-config.yaml"))

    with pytest.raises(FileExistsError) as exc:
        generate_module.generate_image(
            prompt_file=str(prompt_file),
            reference_images=[],
            output_file=str(output_file),
            provider="fake-provider",
        )

    assert "will not be overwritten" in str(exc.value)
    assert output_file.read_bytes() == b"existing-image"
    provider.assert_not_called()


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
  providers:
    - name: env-provider
      models:
        - env-model-v1
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


def test_configured_providers_act_as_allowlist(monkeypatch, tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
image_generation:
  providers:
    - name: qwen_image
      models:
        - qwen-image-2.0-pro
""",
        encoding="utf-8",
    )
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("prompt", encoding="utf-8")

    monkeypatch.setenv("DEER_FLOW_CONFIG_PATH", str(config_file))

    with pytest.raises(ValueError) as exc:
        generate_module.generate_image(
            prompt_file=str(prompt_file),
            reference_images=[],
            output_file=str(tmp_path / "out.png"),
            provider="gemini",
        )

    message = str(exc.value)
    assert "not enabled in config.yaml" in message
    assert "Enabled providers: qwen_image" in message
