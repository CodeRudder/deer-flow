import importlib.util
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPO_ROOT / "skills" / "public" / "image-editing" / "scripts"
IMAGE_EDITING_SKILL = SCRIPT_DIR.parent / "SKILL.md"
IMAGE_GENERATION_SKILL = REPO_ROOT / "skills" / "public" / "image-generation" / "SKILL.md"


def _load_module(module_name: str, path: Path):
    sys.path.insert(0, str(SCRIPT_DIR))
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


edit_module = _load_module("image_editing_edit", SCRIPT_DIR / "edit.py")
provider_module = _load_module(
    "image_editing_openai_image_edit",
    SCRIPT_DIR / "providers" / "openai_image_edit.py",
)


def test_skill_routing_covers_design_drawing_to_realistic_product():
    editing_content = IMAGE_EDITING_SKILL.read_text(encoding="utf-8")
    generation_content = IMAGE_GENERATION_SKILL.read_text(encoding="utf-8")

    assert "even if the user says \"generate\" or \"create\"" in editing_content
    assert "design drawing, sketch, blueprint, CAD-style image" in editing_content
    assert "turn this design into a real product photo" in generation_content
    assert "use the `image-editing` skill instead" in generation_content


def _make_png(path: Path) -> None:
    from PIL import Image

    img = Image.new("RGBA", (4, 4), (255, 0, 0, 255))
    img.save(path)


def _make_jpg(path: Path) -> None:
    from PIL import Image

    img = Image.new("RGB", (4, 4), (255, 0, 0, 255))
    img.save(path)


def test_edit_rejects_mask(tmp_path):
    with pytest.raises(ValueError, match="mask is not supported yet"):
        edit_module.edit_image(["a.png"], "prompt", str(tmp_path / "out.png"), mask="mask.png")


def test_edit_refuses_to_overwrite_existing_output(tmp_path):
    image = tmp_path / "in.png"
    _make_png(image)
    out = tmp_path / "out.png"
    out.write_bytes(b"existing")

    with pytest.raises(FileExistsError, match="will not be overwritten"):
        edit_module.edit_image([str(image)], "prompt", str(out))


def test_edit_reads_config_and_passes_authorization(monkeypatch, tmp_path):
    image = tmp_path / "in.png"
    _make_png(image)
    config = tmp_path / "config.yaml"
    config.write_text(
        """
image_editing:
  default_provider: openai_image_edit
  providers:
    - name: openai_image_edit
      Authorization: $OPENAI_IMAGE_AUTHORIZATION
      base_url: https://example.com/deployments/gpt-image-2
      api_version: "2024-02-01"
      timeout: 12
      size: auto
      quality: high
      output_format: png
      models:
        - name: gpt-image-2
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("DEER_FLOW_CONFIG_PATH", str(config))
    monkeypatch.setenv("OPENAI_IMAGE_AUTHORIZATION", "raw-auth")
    captured = {}

    def fake_edit(**kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setitem(edit_module.PROVIDERS, "openai_image_edit", fake_edit)

    result = edit_module.edit_image([str(image)], "Edit the image", str(tmp_path / "edited.png"))

    assert result == "ok"
    assert captured["authorization"] == "raw-auth"
    assert captured["base_url"] == "https://example.com/deployments/gpt-image-2"
    assert captured["api_version"] == "2024-02-01"
    assert captured["timeout_seconds"] == 12


def test_edit_rejects_empty_prompt(tmp_path):
    image = tmp_path / "in.png"
    _make_png(image)

    with pytest.raises(ValueError, match="Prompt cannot be empty"):
        edit_module.edit_image([str(image)], "   ", str(tmp_path / "out.png"))


def test_provider_sends_raw_authorization_and_downloads_url(monkeypatch, tmp_path):
    image = tmp_path / "in.png"
    _make_png(image)
    output = tmp_path / "out.png"
    post_response = Mock()
    post_response.ok = True
    post_response.json.return_value = {"data": [{"url": "https://cdn.example/result.png"}]}
    get_response = Mock()
    get_response.ok = True
    get_response.content = b"fake-bytes"

    with patch.object(provider_module.requests, "Session") as session_factory:
        session = Mock()
        session_factory.return_value = session
        session.post.return_value = post_response
        session.get.return_value = get_response

        result = provider_module.edit(
            prompt_text="Edit the image",
            reference_images=[str(image)],
            output_file=str(output),
            authorization="raw-auth",
            base_url="https://example.com/deployments/gpt-image-2",
            api_version="2024-02-01",
        )

    session.post.assert_called_once()
    assert session.post.call_args.kwargs["headers"]["Authorization"] == "raw-auth"
    assert output.read_bytes() == b"fake-bytes"
    assert "Successfully edited image" in result


def test_provider_rejects_missing_input_images():
    with pytest.raises(ValueError, match="at least one input image is required"):
        provider_module.edit(
            prompt_text="Edit",
            reference_images=[],
            output_file="/tmp/out.png",
            authorization="raw-auth",
            base_url="https://example.com",
        )


def test_provider_maps_jpg_extension_to_image_jpeg_mime(tmp_path):
    image = tmp_path / "in.jpg"
    _make_jpg(image)
    output = tmp_path / "out.png"
    post_response = Mock()
    post_response.ok = True
    post_response.json.return_value = {"data": [{"b64_json": "AAAA"}]}

    with patch.object(provider_module.requests, "Session") as session_factory:
        session = Mock()
        session_factory.return_value = session
        session.post.return_value = post_response

        provider_module.edit(
            prompt_text="Edit the image",
            reference_images=[str(image)],
            output_file=str(output),
            authorization="raw-auth",
            base_url="https://example.com",
        )

    _, (filename, _, mime_type) = session.post.call_args.kwargs["files"][0]
    assert filename == "in.jpg"
    assert mime_type == "image/jpeg"
