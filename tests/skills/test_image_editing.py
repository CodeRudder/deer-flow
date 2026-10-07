import base64
import importlib.util
import json
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
h3_i2i_module = _load_module(
    "image_editing_h3_i2i",
    SCRIPT_DIR / "providers" / "h3_i2i.py",
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


# --- h3_i2i: self-hosted H3 gateway image-to-image ----------------------------


@pytest.fixture(autouse=True)
def _clean_h3_env(monkeypatch):
    for key in (
        "H3_IMAGE_AUTH_TOKEN",
        "H3IMG_AUTH_TOKEN",
        "H3_IMAGE_BASE_URL",
        "H3_IMAGE_MODEL",
        "H3_IMAGE_POLL_INTERVAL_SECONDS",
        "H3_IMAGE_POLL_TIMEOUT_SECONDS",
        "H3_IMAGE_NO_IDEMPOTENCY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("H3_IMAGE_POLL_INTERVAL_SECONDS", "0")


def _h3_resp(payload, status_code=200):
    response = Mock()
    response.status_code = status_code
    response.ok = status_code < 400
    response.json.return_value = payload
    response.text = json.dumps(payload)
    response.headers = {}
    return response


def _h3_completes(monkeypatch, posts):
    monkeypatch.setattr(
        h3_i2i_module.requests,
        "post",
        lambda url, headers=None, json=None, **kw: (
            posts.append({"headers": headers, "json": json}) or _h3_resp({"id": "img_x"}, 202)
        ),
    )
    monkeypatch.setattr(
        h3_i2i_module.requests,
        "get",
        lambda *a, **k: _h3_resp(
            {
                "status": "completed",
                "data": [{"b64_json": base64.b64encode(b"EDITED").decode(), "meta": {"engine": {"seed": 7}}}],
            }
        ),
    )


def _h3_kwargs(image: Path, out: Path, **overrides):
    kwargs = dict(
        prompt_text="Switch the scene to summer",
        reference_images=[str(image)],
        output_file=str(out),
        authorization="",
        base_url="http://gw.local:8000",
        timeout_seconds=300,
        size="auto",
        model="h3-i2i-hq",
        quality="high",
        output_format="png",
        api_version=None,
    )
    kwargs.update(overrides)
    return kwargs


def test_h3_i2i_declares_it_does_not_need_config_authorization():
    # edit.py gates every provider on a non-empty Authorization; this provider
    # builds its own Bearer header from the shared H3 token env instead.
    assert h3_i2i_module.edit.REQUIRES_AUTHORIZATION is False


def test_h3_i2i_submits_the_reference_image_and_writes_the_result(monkeypatch, tmp_path):
    image, out = tmp_path / "in.png", tmp_path / "out.png"
    _make_png(image)
    monkeypatch.setenv("H3_IMAGE_AUTH_TOKEN", "tok")
    posts = []
    _h3_completes(monkeypatch, posts)

    result = h3_i2i_module.edit(**_h3_kwargs(image, out))

    assert out.read_bytes() == b"EDITED"
    assert posts[0]["headers"]["Authorization"] == "Bearer tok"
    body = posts[0]["json"]
    assert body["mode"] == "h3-i2i-hq"
    assert body["wait"] is False
    assert base64.b64decode(body["reference_image"]) == image.read_bytes()
    assert "h3-i2i-hq" in result


def test_h3_i2i_accepts_the_gateway_native_token_env_name(monkeypatch, tmp_path):
    image, out = tmp_path / "in.png", tmp_path / "out.png"
    _make_png(image)
    monkeypatch.setenv("H3IMG_AUTH_TOKEN", "native-tok")
    posts = []
    _h3_completes(monkeypatch, posts)
    h3_i2i_module.edit(**_h3_kwargs(image, out))
    assert posts[0]["headers"]["Authorization"] == "Bearer native-tok"


def test_h3_i2i_soft_fails_without_a_token(tmp_path):
    image, out = tmp_path / "in.png", tmp_path / "out.png"
    _make_png(image)
    result = h3_i2i_module.edit(**_h3_kwargs(image, out))
    assert "is not set" in result and "H3_IMAGE_AUTH_TOKEN" in result


def test_h3_i2i_requires_exactly_one_reference_image(monkeypatch, tmp_path):
    image, out = tmp_path / "in.png", tmp_path / "out.png"
    _make_png(image)
    monkeypatch.setenv("H3_IMAGE_AUTH_TOKEN", "tok")
    with pytest.raises(ValueError, match="exactly one"):
        h3_i2i_module.edit(**_h3_kwargs(image, out, reference_images=[]))
    with pytest.raises(ValueError, match="exactly one"):
        h3_i2i_module.edit(**_h3_kwargs(image, out, reference_images=[str(image), str(image)]))


def test_h3_i2i_rejects_an_unknown_model(monkeypatch, tmp_path):
    image, out = tmp_path / "in.png", tmp_path / "out.png"
    _make_png(image)
    monkeypatch.setenv("H3_IMAGE_AUTH_TOKEN", "tok")
    with pytest.raises(ValueError, match="unknown h3_i2i model"):
        h3_i2i_module.edit(**_h3_kwargs(image, out, model="h3-clip-std"))


def test_h3_i2i_defaults_to_the_hq_tier(monkeypatch, tmp_path):
    image, out = tmp_path / "in.png", tmp_path / "out.png"
    _make_png(image)
    monkeypatch.setenv("H3_IMAGE_AUTH_TOKEN", "tok")
    posts = []
    _h3_completes(monkeypatch, posts)
    h3_i2i_module.edit(**_h3_kwargs(image, out, model=None))
    assert posts[0]["json"]["mode"] == "h3-i2i-hq"


def test_edit_runs_h3_i2i_without_authorization_in_config(monkeypatch, tmp_path):
    # The whole point: this deployment has no OpenAI credential, so the H3 path
    # must be reachable with no Authorization configured at all.
    image = tmp_path / "in.png"
    _make_png(image)
    config = tmp_path / "config.yaml"
    config.write_text(
        """
image_editing:
  enabled: true
  default_provider: h3_i2i
  providers:
    - name: h3_i2i
      base_url: http://gw.local:8000
      models:
        - name: h3-i2i-hq
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("DEER_FLOW_CONFIG_PATH", str(config))
    monkeypatch.setenv("H3_IMAGE_AUTH_TOKEN", "tok")
    posts = []
    _h3_completes(monkeypatch, posts)

    out = tmp_path / "edited.png"
    result = edit_module.edit_image([str(image)], "Switch to summer", str(out))

    assert out.read_bytes() == b"EDITED"
    assert posts[0]["json"]["mode"] == "h3-i2i-hq"
    assert "h3-i2i-hq" in result


def test_edit_still_requires_authorization_for_a_provider_that_needs_one(monkeypatch, tmp_path):
    # Regression guard for the relaxed gate: providers that do not opt out must
    # keep failing loudly instead of sending an unauthenticated request.
    image = tmp_path / "in.png"
    _make_png(image)
    config = tmp_path / "config.yaml"
    config.write_text(
        """
image_editing:
  default_provider: openai_image_edit
  providers:
    - name: openai_image_edit
      base_url: https://example.com/deployments/gpt-image-2
      models:
        - name: gpt-image-2
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("DEER_FLOW_CONFIG_PATH", str(config))
    monkeypatch.setattr(edit_module, "PROVIDERS", {"openai_image_edit": lambda **kw: "ok"})

    with pytest.raises(ValueError, match="is missing Authorization"):
        edit_module.edit_image([str(image)], "prompt", str(tmp_path / "out.png"))
