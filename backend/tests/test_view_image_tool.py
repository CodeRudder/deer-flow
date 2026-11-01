import asyncio
import base64
import importlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from deerflow.config.app_config import AppConfig
from deerflow.config.model_config import ModelConfig
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.config.vision_model_config import VisionConfig, VisionModelConfig
from deerflow.tools.builtins.view_image_tool import view_image_tool

view_image_module = importlib.import_module("deerflow.tools.builtins.view_image_tool")

PNG_BYTES = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def _make_thread_data(tmp_path: Path) -> dict[str, str]:
    user_data = tmp_path / "threads" / "thread-1" / "user-data"
    workspace = user_data / "workspace"
    uploads = user_data / "uploads"
    outputs = user_data / "outputs"
    for directory in (workspace, uploads, outputs):
        directory.mkdir(parents=True)

    return {
        "workspace_path": str(workspace),
        "uploads_path": str(uploads),
        "outputs_path": str(outputs),
    }


def _make_runtime(thread_data: dict[str, str], *, app_config=None, vision_model_name: str | None = None) -> SimpleNamespace:
    context = {"thread_id": "thread-1"}
    if app_config is not None:
        context["app_config"] = app_config
    if vision_model_name is not None:
        context["vision_model_name"] = vision_model_name
    return SimpleNamespace(
        state={"thread_data": thread_data},
        context=context,
        config={},
    )


def _make_app_config(
    *,
    vision_model: VisionModelConfig | None = None,
    vision_models: list[VisionModelConfig] | None = None,
) -> AppConfig:
    configured_vision_models = vision_models if vision_models is not None else ([vision_model] if vision_model else [])
    return AppConfig(
        models=[
            ModelConfig(
                name="text-model",
                display_name="text-model",
                description=None,
                use="langchain_openai:ChatOpenAI",
                model="text-model",
                supports_vision=False,
            )
        ],
        vision=VisionConfig(models=configured_vision_models),
        sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"),
    )


def _message_content(result) -> str:
    return result.update["messages"][0].content


async def _call_view_image(*, runtime, image_path: str, tool_call_id: str):
    return await view_image_tool.coroutine(
        runtime=runtime,
        image_path=image_path,
        tool_call_id=tool_call_id,
    )


@pytest.mark.asyncio
async def test_view_image_rejects_external_absolute_path(tmp_path: Path) -> None:
    thread_data = _make_thread_data(tmp_path)
    outside_image = tmp_path / "outside.png"
    outside_image.write_bytes(PNG_BYTES)

    result = await _call_view_image(
        runtime=_make_runtime(thread_data),
        image_path=str(outside_image),
        tool_call_id="tc-external",
    )

    assert "Only image paths under /mnt/user-data" in _message_content(result)
    assert "viewed_images" not in result.update


@pytest.mark.asyncio
async def test_view_image_reads_virtual_uploads_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(view_image_module, "get_app_config", lambda: _make_app_config())
    thread_data = _make_thread_data(tmp_path)
    image_path = Path(thread_data["uploads_path"]) / "sample.png"
    image_path.write_bytes(PNG_BYTES)

    result = await _call_view_image(
        runtime=_make_runtime(thread_data),
        image_path="/mnt/user-data/uploads/sample.png",
        tool_call_id="tc-uploads",
    )

    assert _message_content(result) == "Successfully read image"
    viewed_image = result.update["viewed_images"]["/mnt/user-data/uploads/sample.png"]
    assert viewed_image["base64"] == base64.b64encode(PNG_BYTES).decode("utf-8")
    assert viewed_image["mime_type"] == "image/png"


@pytest.mark.asyncio
async def test_view_image_offloads_file_loading_to_thread(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(view_image_module, "get_app_config", lambda: _make_app_config())
    thread_data = _make_thread_data(tmp_path)
    image_path = Path(thread_data["uploads_path"]) / "sample.png"
    image_path.write_bytes(PNG_BYTES)
    to_thread_calls: list[object] = []
    original_to_thread = asyncio.to_thread

    async def _spy_to_thread(func, /, *args, **kwargs):
        to_thread_calls.append(func)
        return await original_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(view_image_module.asyncio, "to_thread", _spy_to_thread)

    result = await _call_view_image(
        runtime=_make_runtime(thread_data),
        image_path="/mnt/user-data/uploads/sample.png",
        tool_call_id="tc-uploads",
    )

    assert _message_content(result) == "Successfully read image"
    assert view_image_module._load_image_file_sync in to_thread_calls


@pytest.mark.asyncio
async def test_view_image_uses_independent_vision_model_without_viewed_images(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vision_model = VisionModelConfig(
        name="company-vision",
        model="kimi-k2.6",
        base_url="https://vision.example.test/api/v1/messages",
        api_key="test-key",
    )
    app_config = _make_app_config(vision_model=vision_model)

    def _raise_global_config():
        raise AssertionError("runtime app_config should be used when present")

    monkeypatch.setattr(view_image_module, "get_app_config", _raise_global_config)

    captured: dict[str, object] = {}

    async def _fake_understand(self, *, image_base64: str, mime_type: str, image_path: str):
        captured["model_config"] = self.model_config
        captured["vision_config"] = self.vision_config
        captured["image_base64"] = image_base64
        captured["mime_type"] = mime_type
        captured["image_path"] = image_path
        return "图片里有一面红旗。"

    monkeypatch.setattr(view_image_module.VisionClient, "understand_image_base64", _fake_understand)
    thread_data = _make_thread_data(tmp_path)
    image_path = Path(thread_data["uploads_path"]) / "sample.png"
    image_path.write_bytes(PNG_BYTES)

    result = await _call_view_image(
        runtime=_make_runtime(thread_data, app_config=app_config),
        image_path="/mnt/user-data/uploads/sample.png",
        tool_call_id="tc-vision",
    )

    assert _message_content(result) == "图片里有一面红旗。"
    assert result.update["viewed_images"] == {}
    assert captured == {
        "model_config": vision_model,
        "vision_config": app_config.vision,
        "image_base64": base64.b64encode(PNG_BYTES).decode("utf-8"),
        "mime_type": "image/png",
        "image_path": "/mnt/user-data/uploads/sample.png",
    }


@pytest.mark.asyncio
async def test_view_image_uses_selected_vision_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    default_model = VisionModelConfig(
        name="default-vision",
        model="kimi-k2.6",
        base_url="https://vision.example.test/api/v1/messages",
    )
    selected_model = VisionModelConfig(
        name="doubao-vision",
        model="doubao-seed-2.0-pro",
        base_url="https://vision.example.test/api/v1/messages",
    )
    app_config = _make_app_config(vision_models=[default_model, selected_model])
    captured: dict[str, object] = {}

    async def _fake_understand(self, *, image_base64: str, mime_type: str, image_path: str):
        captured["model_config"] = self.model_config
        return "选中的视觉模型结果"

    monkeypatch.setattr(view_image_module.VisionClient, "understand_image_base64", _fake_understand)
    thread_data = _make_thread_data(tmp_path)
    image_path = Path(thread_data["uploads_path"]) / "sample.png"
    image_path.write_bytes(PNG_BYTES)

    result = await _call_view_image(
        runtime=_make_runtime(thread_data, app_config=app_config, vision_model_name="doubao-vision"),
        image_path="/mnt/user-data/uploads/sample.png",
        tool_call_id="tc-selected-vision",
    )

    assert _message_content(result) == "选中的视觉模型结果"
    assert result.update["viewed_images"] == {}
    assert captured["model_config"] == selected_model


@pytest.mark.asyncio
async def test_view_image_reports_invalid_selected_vision_model(tmp_path: Path) -> None:
    app_config = _make_app_config(
        vision_model=VisionModelConfig(
            name="default-vision",
            model="kimi-k2.6",
            base_url="https://vision.example.test/api/v1/messages",
        )
    )
    thread_data = _make_thread_data(tmp_path)
    image_path = Path(thread_data["uploads_path"]) / "sample.png"
    image_path.write_bytes(PNG_BYTES)

    result = await _call_view_image(
        runtime=_make_runtime(thread_data, app_config=app_config, vision_model_name="missing-vision"),
        image_path="/mnt/user-data/uploads/sample.png",
        tool_call_id="tc-missing-vision",
    )

    assert _message_content(result) == "多模态的工具理解调用异常"
    assert result.update["viewed_images"] == {}


@pytest.mark.asyncio
async def test_view_image_reports_independent_vision_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vision_model = VisionModelConfig(
        name="company-vision",
        model="kimi-k2.6",
        base_url="https://vision.example.test/api/v1/messages",
        api_key="test-key",
    )
    monkeypatch.setattr(view_image_module, "get_app_config", lambda: _make_app_config(vision_model=vision_model))

    async def _raise_understand(*args, **kwargs):
        raise view_image_module.VisionUnderstandingError("boom")

    monkeypatch.setattr(view_image_module.VisionClient, "understand_image_base64", _raise_understand)
    thread_data = _make_thread_data(tmp_path)
    image_path = Path(thread_data["uploads_path"]) / "sample.png"
    image_path.write_bytes(PNG_BYTES)

    result = await _call_view_image(
        runtime=_make_runtime(thread_data),
        image_path="/mnt/user-data/uploads/sample.png",
        tool_call_id="tc-vision-fail",
    )

    assert _message_content(result) == "多模态的工具理解调用异常"
    assert result.update["viewed_images"] == {}


@pytest.mark.asyncio
async def test_view_image_rejects_spoofed_extension(tmp_path: Path) -> None:
    thread_data = _make_thread_data(tmp_path)
    image_path = Path(thread_data["uploads_path"]) / "not-really.png"
    image_path.write_bytes(b"not an image")

    result = await _call_view_image(
        runtime=_make_runtime(thread_data),
        image_path="/mnt/user-data/uploads/not-really.png",
        tool_call_id="tc-spoofed",
    )

    assert "contents do not match" in _message_content(result)
    assert "viewed_images" not in result.update


@pytest.mark.asyncio
async def test_view_image_rejects_mismatched_magic_bytes(tmp_path: Path) -> None:
    thread_data = _make_thread_data(tmp_path)
    image_path = Path(thread_data["uploads_path"]) / "jpeg-named-png.png"
    image_path.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

    result = await _call_view_image(
        runtime=_make_runtime(thread_data),
        image_path="/mnt/user-data/uploads/jpeg-named-png.png",
        tool_call_id="tc-mismatch",
    )

    assert "file extension indicates image/png" in _message_content(result)
    assert "viewed_images" not in result.update


@pytest.mark.asyncio
async def test_view_image_rejects_oversized_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    thread_data = _make_thread_data(tmp_path)
    image_path = Path(thread_data["uploads_path"]) / "sample.png"
    image_path.write_bytes(PNG_BYTES)
    monkeypatch.setattr(view_image_module, "_MAX_IMAGE_BYTES", len(PNG_BYTES) - 1)

    result = await _call_view_image(
        runtime=_make_runtime(thread_data),
        image_path="/mnt/user-data/uploads/sample.png",
        tool_call_id="tc-oversized",
    )

    assert "Image file is too large" in _message_content(result)
    assert "viewed_images" not in result.update


@pytest.mark.asyncio
async def test_view_image_sanitizes_read_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    thread_data = _make_thread_data(tmp_path)
    image_path = Path(thread_data["uploads_path"]) / "sample.png"
    image_path.write_bytes(PNG_BYTES)

    def _open(*args, **kwargs):
        raise PermissionError(f"permission denied: {image_path}")

    monkeypatch.setattr("builtins.open", _open)

    result = await _call_view_image(
        runtime=_make_runtime(thread_data),
        image_path="/mnt/user-data/uploads/sample.png",
        tool_call_id="tc-read-error",
    )

    message = _message_content(result)
    assert "Error reading image file" in message
    assert str(image_path) not in message
    assert str(Path(thread_data["uploads_path"])) not in message
    assert "/mnt/user-data/uploads/sample.png" in message
    assert "viewed_images" not in result.update


@pytest.mark.skipif(os.name == "nt", reason="symlink semantics differ on Windows")
@pytest.mark.asyncio
async def test_view_image_rejects_uploads_symlink_escape(tmp_path: Path) -> None:
    thread_data = _make_thread_data(tmp_path)
    outside_image = tmp_path / "outside-target.png"
    outside_image.write_bytes(PNG_BYTES)

    link_path = Path(thread_data["uploads_path"]) / "escape.png"
    try:
        link_path.symlink_to(outside_image)
    except OSError as exc:
        pytest.skip(f"symlink creation failed: {exc}")

    result = await _call_view_image(
        runtime=_make_runtime(thread_data),
        image_path="/mnt/user-data/uploads/escape.png",
        tool_call_id="tc-symlink",
    )

    assert "path traversal" in _message_content(result)
    assert "viewed_images" not in result.update
