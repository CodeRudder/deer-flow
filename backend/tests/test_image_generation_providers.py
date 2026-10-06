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
openai_image_module = _load_module(
    "image_generation_openai_image",
    SCRIPT_DIR / "providers" / "openai_image.py",
)
h3_image_module = _load_module(
    "image_generation_h3_image",
    SCRIPT_DIR / "providers" / "h3_image.py",
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
        "n": 1,
    }


def test_openai_image_build_payload_includes_response_format_only_when_configured(monkeypatch):
    monkeypatch.delenv("OPENAI_IMAGE_QUALITY", raising=False)
    monkeypatch.delenv("OPENAI_IMAGE_OUTPUT_FORMAT", raising=False)
    monkeypatch.setenv("OPENAI_IMAGE_RESPONSE_FORMAT", "b64_json")

    payload = openai_image_module._build_payload(
        prompt_text="A quiet bookstore",
        model="gpt-image-2",
        aspect_ratio="16:9",
    )

    assert payload["response_format"] == "b64_json"


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
    monkeypatch.delenv("OPENAI_IMAGE_API_VERSION", raising=False)

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
    assert "response_format" not in session.post.call_args.kwargs["json"]
    session.get.assert_called_once_with("https://cdn.example/result.png", timeout=(10, 60))
    assert output_file.read_bytes() == b"fake-png-bytes"
    assert "provider=openai_image" in result


def test_openai_image_posts_with_optional_api_version(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_IMAGE_API_KEY", "test-key")
    monkeypatch.setenv(
        "OPENAI_IMAGE_BASE_URL",
        "https://jlc-ai-codeing-image.openai.azure.com/openai/deployments/gpt-image-2",
    )
    monkeypatch.setenv("OPENAI_IMAGE_API_VERSION", "2024-02-01")
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
            prompt_text="一只橘猫戴着橙色围巾抱着水獭，温暖插画风格",
            reference_images=[],
            output_file=str(output_file),
            aspect_ratio="16:9",
            model="gpt-image-2",
        )

    session.post.assert_called_once()
    assert session.post.call_args.args[0] == ("https://jlc-ai-codeing-image.openai.azure.com/openai/deployments/gpt-image-2/images/generations")
    assert session.post.call_args.kwargs["params"] == {"api-version": "2024-02-01"}
    assert session.post.call_args.kwargs["headers"]["Authorization"] == "Bearer test-key"
    assert session.post.call_args.kwargs["json"]["size"] == "3840x2160"
    assert output_file.read_bytes() == b"fake-png-bytes"


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

    prompt_text, negative_prompt = generate_module._read_prompt(str(prompt_file), "openai_image")

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

    prompt_text, negative_prompt = generate_module._read_prompt(str(prompt_file), "qwen_image")

    assert prompt_text == raw_prompt
    assert negative_prompt is None


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
            provider="openai_image",
        )

    message = str(exc.value)
    assert "not enabled in config.yaml" in message
    assert "Enabled providers: qwen_image" in message


def test_env_model_beats_env_provider(monkeypatch, tmp_path):
    # The env pair must not cross-pair: IMAGE_GENERATION_MODEL resolves its owning
    # provider via config; IMAGE_GENERATION_PROVIDER only fills in when no model is pinned.
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
    monkeypatch.setenv("DEER_FLOW_CONFIG_PATH", str(config_file))
    monkeypatch.setenv("IMAGE_GENERATION_PROVIDER", "openai_image")
    monkeypatch.setenv("IMAGE_GENERATION_MODEL", "qwen-image-2.0-pro")
    # Other test files (video skill tests) may have repointed the shared
    # "providers" module name; give this routing test a self-contained PROVIDERS.
    monkeypatch.setattr(generate_module, "PROVIDERS", {"qwen_image": object(), "openai_image": object()})

    config = generate_module._load_image_generation_config()

    assert generate_module._resolve_target(config, None, None) == (
        "qwen_image",
        "qwen-image-2.0-pro",
    )


# --- h3_image: self-hosted H3 image gateway (frame-extraction modes) ----------


@pytest.fixture(autouse=True)
def _clean_h3_env(monkeypatch):
    for k in (
        "H3_IMAGE_AUTH_TOKEN",
        "H3IMG_AUTH_TOKEN",
        "H3_IMAGE_BASE_URL",
        "H3_IMAGE_MODEL",
        "H3_IMAGE_POLL_INTERVAL_SECONDS",
        "H3_IMAGE_POLL_TIMEOUT_SECONDS",
        "H3_IMAGE_SEED",
        "H3_IMAGE_FRAME_POLICY",
        "H3_IMAGE_SHORT_EDGE",
        "H3_IMAGE_NO_IDEMPOTENCY",
    ):
        monkeypatch.delenv(k, raising=False)
    # Keep the poll loop instant in tests.
    monkeypatch.setenv("H3_IMAGE_POLL_INTERVAL_SECONDS", "0")


def _h3_resp(payload, status_code=200, headers=None):
    r = Mock()
    r.status_code = status_code
    r.ok = status_code < 400
    r.json.return_value = payload
    r.text = json.dumps(payload)
    r.headers = headers or {}
    return r


def _h3_png_b64(data=b"PNGDATA"):
    return base64.b64encode(data).decode()


def test_h3_image_submits_polls_and_writes_png(monkeypatch, tmp_path):
    monkeypatch.setenv("H3_IMAGE_AUTH_TOKEN", "tok")
    posts, gets = [], []

    def fake_post(url, headers=None, json=None, **kw):
        posts.append({"url": url, "headers": headers, "json": json})
        return _h3_resp({"id": "img_abc", "status": "queued"}, 202)

    def fake_get(url, headers=None, **kw):
        gets.append(url)
        return _h3_resp(
            {
                "id": "img_abc",
                "status": "completed",
                "progress": 100,
                "data": [{"b64_json": _h3_png_b64(), "meta": {"mode": "h3-frame-fast"}}],
            }
        )

    monkeypatch.setattr(h3_image_module.requests, "post", fake_post)
    monkeypatch.setattr(h3_image_module.requests, "get", fake_get)

    out = tmp_path / "out.png"
    msg = h3_image_module.generate(
        prompt_text="a red fox",
        reference_images=[],
        output_file=str(out),
        aspect_ratio="16:9",
        model="h3-frame-fast",
    )

    assert out.read_bytes() == b"PNGDATA"
    assert "provider=h3_image" in msg and "h3-frame-fast" in msg
    assert posts[0]["url"].endswith("/v1/images/generations")
    assert posts[0]["json"]["mode"] == "h3-frame-fast"
    assert posts[0]["json"]["aspect_ratio"] == "16:9"
    assert posts[0]["json"]["wait"] is False  # submit-only; the provider polls
    assert gets[0].endswith("/v1/images/jobs/img_abc")


def test_h3_image_sends_bearer_auth(monkeypatch, tmp_path):
    monkeypatch.setenv("H3_IMAGE_AUTH_TOKEN", "secret-tok")
    seen = {}

    def fake_post(url, headers=None, json=None, **kw):
        seen["headers"] = headers
        return _h3_resp({"id": "img_1"}, 202)

    monkeypatch.setattr(h3_image_module.requests, "post", fake_post)
    monkeypatch.setattr(
        h3_image_module.requests,
        "get",
        lambda url, headers=None, **kw: _h3_resp({"status": "completed", "data": [{"b64_json": _h3_png_b64()}]}),
    )
    h3_image_module.generate(prompt_text="x", reference_images=[], output_file=str(tmp_path / "o.png"))
    assert seen["headers"]["Authorization"] == "Bearer secret-tok"


def test_h3_image_accepts_gateway_native_token_env_name(monkeypatch, tmp_path):
    # The gateway's own env name works as a fallback so operators can copy it verbatim.
    monkeypatch.setenv("H3IMG_AUTH_TOKEN", "native-tok")
    monkeypatch.setattr(h3_image_module.requests, "post", lambda *a, **k: _h3_resp({"id": "i"}, 202))
    monkeypatch.setattr(
        h3_image_module.requests,
        "get",
        lambda *a, **k: _h3_resp({"status": "completed", "data": [{"b64_json": _h3_png_b64()}]}),
    )
    msg = h3_image_module.generate(prompt_text="x", reference_images=[], output_file=str(tmp_path / "o.png"))
    assert "provider=h3_image" in msg


def test_h3_image_defaults_to_std_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("H3_IMAGE_AUTH_TOKEN", "t")
    seen = {}

    def fake_post(url, headers=None, json=None, **kw):
        seen["json"] = json
        return _h3_resp({"id": "i"}, 202)

    monkeypatch.setattr(h3_image_module.requests, "post", fake_post)
    monkeypatch.setattr(
        h3_image_module.requests,
        "get",
        lambda *a, **k: _h3_resp({"status": "completed", "data": [{"b64_json": _h3_png_b64()}]}),
    )
    h3_image_module.generate(prompt_text="x", reference_images=[], output_file=str(tmp_path / "o.png"))
    assert seen["json"]["mode"] == "h3-frame-std"


def test_h3_image_unknown_mode_rejected_locally(monkeypatch, tmp_path):
    monkeypatch.setenv("H3_IMAGE_AUTH_TOKEN", "t")
    with pytest.raises(ValueError, match="unknown h3_image mode"):
        h3_image_module.generate(
            prompt_text="x",
            reference_images=[],
            output_file=str(tmp_path / "o.png"),
            model="h3-frame-nope",
        )


def test_h3_image_missing_token_soft_fails(monkeypatch, tmp_path):
    msg = h3_image_module.generate(prompt_text="x", reference_images=[], output_file=str(tmp_path / "o.png"))
    assert "is not set" in msg and "H3_IMAGE_AUTH_TOKEN" in msg


def test_h3_image_rejects_reference_images(monkeypatch, tmp_path):
    monkeypatch.setenv("H3_IMAGE_AUTH_TOKEN", "t")
    with pytest.raises(ValueError, match="reference images are not supported"):
        h3_image_module.generate(
            prompt_text="x",
            reference_images=["/tmp/a.png"],
            output_file=str(tmp_path / "o.png"),
        )


def test_h3_image_surfaces_structured_job_error(monkeypatch, tmp_path):
    monkeypatch.setenv("H3_IMAGE_AUTH_TOKEN", "t")
    monkeypatch.setattr(h3_image_module.requests, "post", lambda *a, **k: _h3_resp({"id": "i"}, 202))
    monkeypatch.setattr(
        h3_image_module.requests,
        "get",
        lambda *a, **k: _h3_resp(
            {
                "status": "failed",
                "error": {"code": "engine_error", "message": "boom", "retryable": False},
            }
        ),
    )
    with pytest.raises(RuntimeError, match="engine_error.*boom"):
        h3_image_module.generate(prompt_text="x", reference_images=[], output_file=str(tmp_path / "o.png"))


def test_h3_image_surfaces_http_error_envelope(monkeypatch, tmp_path):
    monkeypatch.setenv("H3_IMAGE_AUTH_TOKEN", "t")
    monkeypatch.setattr(
        h3_image_module.requests,
        "post",
        lambda *a, **k: _h3_resp({"error": {"code": "invalid_request", "message": "unknown mode"}}, 400),
    )
    with pytest.raises(RuntimeError, match="invalid_request.*unknown mode"):
        h3_image_module.generate(prompt_text="x", reference_images=[], output_file=str(tmp_path / "o.png"))


def test_h3_image_timeout_points_at_reattach_and_job_retrieval(monkeypatch, tmp_path):
    # The sandbox kills a bash command at 600s; the provider must fail first and
    # hand the caller a way back in: re-running the same command re-attaches via
    # Idempotency-Key, and artifacts persist gateway-side for direct retrieval.
    monkeypatch.setenv("H3_IMAGE_AUTH_TOKEN", "t")
    monkeypatch.setenv("H3_IMAGE_POLL_TIMEOUT_SECONDS", "0")
    monkeypatch.setattr(h3_image_module.requests, "post", lambda *a, **k: _h3_resp({"id": "img_slow"}, 202))
    monkeypatch.setattr(h3_image_module.requests, "get", lambda *a, **k: _h3_resp({"status": "running"}))
    with pytest.raises(RuntimeError, match="img_slow"):
        h3_image_module.generate(prompt_text="x", reference_images=[], output_file=str(tmp_path / "o.png"))
    with pytest.raises(RuntimeError, match="content\\?variant="):
        h3_image_module.generate(prompt_text="x", reference_images=[], output_file=str(tmp_path / "o.png"))
    with pytest.raises(RuntimeError, match="re-attach"):
        h3_image_module.generate(prompt_text="x", reference_images=[], output_file=str(tmp_path / "o.png"))


def test_h3_image_warns_on_unsupported_params(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("H3_IMAGE_AUTH_TOKEN", "t")
    monkeypatch.setattr(h3_image_module.requests, "post", lambda *a, **k: _h3_resp({"id": "i"}, 202))
    monkeypatch.setattr(
        h3_image_module.requests,
        "get",
        lambda *a, **k: _h3_resp({"status": "completed", "data": [{"b64_json": _h3_png_b64()}]}),
    )
    h3_image_module.generate(
        prompt_text="x",
        reference_images=[],
        output_file=str(tmp_path / "o.png"),
        negative_prompt="blurry",
        watermark=True,
    )
    out = capsys.readouterr().out
    assert "negative_prompt" in out and "watermark" in out


def test_h3_image_falls_back_to_url_when_b64_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("H3_IMAGE_AUTH_TOKEN", "t")
    monkeypatch.setattr(h3_image_module.requests, "post", lambda *a, **k: _h3_resp({"id": "i"}, 202))

    def fake_get(url, headers=None, **kw):
        # NOTE: the download URL also contains "/jobs/", so discriminate on
        # "/content" — the poll URL is the one without it.
        if "/content" in url:
            r = Mock()
            r.status_code = 200
            r.ok = True
            r.content = b"URLPNG"
            return r
        return _h3_resp({"status": "completed", "data": [{"url": "/v1/images/jobs/i/content?variant=0"}]})

    monkeypatch.setattr(h3_image_module.requests, "get", fake_get)
    out = tmp_path / "o.png"
    h3_image_module.generate(prompt_text="x", reference_images=[], output_file=str(out))
    assert out.read_bytes() == b"URLPNG"


def test_h3_image_honors_base_url_override(monkeypatch, tmp_path):
    monkeypatch.setenv("H3_IMAGE_AUTH_TOKEN", "t")
    monkeypatch.setenv("H3_IMAGE_BASE_URL", "http://gw.local:9999")
    seen = {}

    def fake_post(url, headers=None, json=None, **kw):
        seen["url"] = url
        return _h3_resp({"id": "i"}, 202)

    monkeypatch.setattr(h3_image_module.requests, "post", fake_post)
    monkeypatch.setattr(
        h3_image_module.requests,
        "get",
        lambda *a, **k: _h3_resp({"status": "completed", "data": [{"b64_json": _h3_png_b64()}]}),
    )
    h3_image_module.generate(prompt_text="x", reference_images=[], output_file=str(tmp_path / "o.png"))
    assert seen["url"] == "http://gw.local:9999/v1/images/generations"


def _h3_completes(monkeypatch, posts=None):
    """Wire the two HTTP calls the provider makes: submit then poll-to-completed."""
    monkeypatch.setattr(
        h3_image_module.requests,
        "post",
        lambda url, headers=None, json=None, **kw: (posts.append({"headers": headers, "json": json}) if posts is not None else None) or _h3_resp({"id": "i"}, 202),
    )
    monkeypatch.setattr(
        h3_image_module.requests,
        "get",
        lambda *a, **k: _h3_resp(
            {
                "status": "completed",
                "data": [{"b64_json": _h3_png_b64(), "meta": {"engine": {"seed": 1234}}}],
            }
        ),
    )


def test_h3_image_quality_tiers_match_the_gateway_mode_list():
    # Mirrors GET /v1/images/modes after the 2026-10-07 update: four tiers,
    # ordered low→high quality, and the default stays the balanced tier.
    assert h3_image_module.KNOWN_MODES == (
        "h3-frame-draft",
        "h3-frame-fast",
        "h3-frame-std",
        "h3-frame-hq",
    )
    assert h3_image_module.DEFAULT_MODE == "h3-frame-std"


def test_h3_image_submits_draft_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("H3_IMAGE_AUTH_TOKEN", "t")
    posts = []
    _h3_completes(monkeypatch, posts)
    msg = h3_image_module.generate(
        prompt_text="x",
        reference_images=[],
        output_file=str(tmp_path / "o.png"),
        model="h3-frame-draft",
    )
    assert posts[0]["json"]["mode"] == "h3-frame-draft"
    assert "h3-frame-draft" in msg


def test_h3_image_sends_content_derived_idempotency_key(monkeypatch, tmp_path):
    # The key is what turns a timeout retry into a re-attach instead of a fresh
    # GPU run on a single-card queue: identical requests must yield one key.
    monkeypatch.setenv("H3_IMAGE_AUTH_TOKEN", "t")
    posts = []
    _h3_completes(monkeypatch, posts)

    def run(prompt, **kw):
        h3_image_module.generate(prompt_text=prompt, reference_images=[], output_file=str(tmp_path / "o.png"), **kw)

    run("a red fox")
    run("a red fox")
    run("a blue fox")
    run("a red fox", model="h3-frame-hq")

    keys = [p["headers"].get("Idempotency-Key") for p in posts]
    assert all(keys), "every submit must carry an Idempotency-Key"
    assert keys[0] == keys[1], "same request must re-attach to the same job"
    assert keys[0] != keys[2], "different prompt must be a different job"
    assert keys[0] != keys[3], "different mode must be a different job"


def test_h3_image_idempotency_can_be_disabled(monkeypatch, tmp_path):
    # Deliberate re-rolls (same prompt, want a second variant) must escape the
    # 24h same-key window, otherwise a caller can never get a fresh image.
    monkeypatch.setenv("H3_IMAGE_AUTH_TOKEN", "t")
    monkeypatch.setenv("H3_IMAGE_NO_IDEMPOTENCY", "1")
    posts = []
    _h3_completes(monkeypatch, posts)
    h3_image_module.generate(prompt_text="x", reference_images=[], output_file=str(tmp_path / "o.png"))
    assert "Idempotency-Key" not in posts[0]["headers"]


def test_h3_image_passes_sampling_params_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("H3_IMAGE_AUTH_TOKEN", "t")
    monkeypatch.setenv("H3_IMAGE_SEED", "42")
    monkeypatch.setenv("H3_IMAGE_FRAME_POLICY", "at:0.25")
    monkeypatch.setenv("H3_IMAGE_SHORT_EDGE", "512")
    posts = []
    _h3_completes(monkeypatch, posts)
    h3_image_module.generate(prompt_text="x", reference_images=[], output_file=str(tmp_path / "o.png"))
    assert posts[0]["json"]["seed"] == 42
    assert posts[0]["json"]["frame_policy"] == "at:0.25"
    assert posts[0]["json"]["short_edge"] == 512


def test_h3_image_omits_unset_sampling_params(monkeypatch, tmp_path):
    # Unset means "let the gateway apply its tier defaults" — do not send nulls.
    monkeypatch.setenv("H3_IMAGE_AUTH_TOKEN", "t")
    posts = []
    _h3_completes(monkeypatch, posts)
    h3_image_module.generate(prompt_text="x", reference_images=[], output_file=str(tmp_path / "o.png"))
    for key in ("seed", "frame_policy", "short_edge"):
        assert key not in posts[0]["json"]


@pytest.mark.parametrize("value", ["first", "last", "middle", "at:0", "at:1", "at:0.25"])
def test_h3_image_accepts_valid_frame_policy(monkeypatch, tmp_path, value):
    monkeypatch.setenv("H3_IMAGE_AUTH_TOKEN", "t")
    monkeypatch.setenv("H3_IMAGE_FRAME_POLICY", value)
    posts = []
    _h3_completes(monkeypatch, posts)
    h3_image_module.generate(prompt_text="x", reference_images=[], output_file=str(tmp_path / "o.png"))
    assert posts[0]["json"]["frame_policy"] == value


@pytest.mark.parametrize("value", ["centre", "at:1.5", "at:-0.1", "at:", "AT:0.5"])
def test_h3_image_rejects_invalid_frame_policy(monkeypatch, tmp_path, value):
    monkeypatch.setenv("H3_IMAGE_AUTH_TOKEN", "t")
    monkeypatch.setenv("H3_IMAGE_FRAME_POLICY", value)
    monkeypatch.setattr(h3_image_module.requests, "post", lambda *a, **k: pytest.fail("must not submit"))
    with pytest.raises(ValueError, match="H3_IMAGE_FRAME_POLICY"):
        h3_image_module.generate(prompt_text="x", reference_images=[], output_file=str(tmp_path / "o.png"))


@pytest.mark.parametrize("value", ["127", "2049", "0", "-8", "abc"])
def test_h3_image_rejects_out_of_range_short_edge(monkeypatch, tmp_path, value):
    monkeypatch.setenv("H3_IMAGE_AUTH_TOKEN", "t")
    monkeypatch.setenv("H3_IMAGE_SHORT_EDGE", value)
    monkeypatch.setattr(h3_image_module.requests, "post", lambda *a, **k: pytest.fail("must not submit"))
    with pytest.raises(ValueError, match="H3_IMAGE_SHORT_EDGE"):
        h3_image_module.generate(prompt_text="x", reference_images=[], output_file=str(tmp_path / "o.png"))


def test_h3_image_rejects_malformed_seed(monkeypatch, tmp_path):
    monkeypatch.setenv("H3_IMAGE_AUTH_TOKEN", "t")
    monkeypatch.setenv("H3_IMAGE_SEED", "not-a-number")
    monkeypatch.setattr(h3_image_module.requests, "post", lambda *a, **k: pytest.fail("must not submit"))
    with pytest.raises(ValueError, match="H3_IMAGE_SEED"):
        h3_image_module.generate(prompt_text="x", reference_images=[], output_file=str(tmp_path / "o.png"))


def test_h3_image_yield_failure_tells_caller_to_retry(monkeypatch, tmp_path):
    # Video-first yielding: the gateway gives up after H3IMG_YIELD_MAX_S with
    # retryable=true. That is "try again shortly", not a hard failure.
    monkeypatch.setenv("H3_IMAGE_AUTH_TOKEN", "t")
    monkeypatch.setattr(h3_image_module.requests, "post", lambda *a, **k: _h3_resp({"id": "i"}, 202))
    monkeypatch.setattr(
        h3_image_module.requests,
        "get",
        lambda *a, **k: _h3_resp(
            {
                "status": "failed",
                "error": {
                    "code": "engine_error",
                    "message": "engine busy with video jobs for 1800s",
                    "retryable": True,
                },
            }
        ),
    )
    with pytest.raises(RuntimeError, match="video jobs") as exc:
        h3_image_module.generate(prompt_text="x", reference_images=[], output_file=str(tmp_path / "o.png"))
    assert "retry" in str(exc.value).lower()


def test_h3_image_reports_503_engine_unavailable_with_retry_after(monkeypatch, tmp_path):
    monkeypatch.setenv("H3_IMAGE_AUTH_TOKEN", "t")
    monkeypatch.setattr(
        h3_image_module.requests,
        "post",
        lambda *a, **k: _h3_resp(
            {"error": {"code": "engine_unavailable", "message": "engine down", "retryable": True}},
            503,
            headers={"Retry-After": "30"},
        ),
    )
    with pytest.raises(RuntimeError, match="engine_unavailable") as exc:
        h3_image_module.generate(prompt_text="x", reference_images=[], output_file=str(tmp_path / "o.png"))
    msg = str(exc.value)
    assert "Retry-After: 30" in msg
    assert "retryable=True" in msg


def test_h3_image_surfaces_gateway_note_while_waiting(monkeypatch, tmp_path, capsys):
    # The gateway reports yielding via `note`; long waits must not look like a hang.
    monkeypatch.setenv("H3_IMAGE_AUTH_TOKEN", "t")
    monkeypatch.setenv("H3_IMAGE_POLL_TIMEOUT_SECONDS", "0")
    monkeypatch.setattr(h3_image_module.requests, "post", lambda *a, **k: _h3_resp({"id": "i"}, 202))
    monkeypatch.setattr(
        h3_image_module.requests,
        "get",
        lambda *a, **k: _h3_resp({"status": "running", "note": "yielding: 1 video job(s) on engine"}),
    )
    with pytest.raises(RuntimeError):
        h3_image_module.generate(prompt_text="x", reference_images=[], output_file=str(tmp_path / "o.png"))
    assert "yielding" in capsys.readouterr().out


def test_h3_image_prints_actual_seed_for_reproducibility(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("H3_IMAGE_AUTH_TOKEN", "t")
    _h3_completes(monkeypatch)
    h3_image_module.generate(prompt_text="x", reference_images=[], output_file=str(tmp_path / "o.png"))
    assert "1234" in capsys.readouterr().out
