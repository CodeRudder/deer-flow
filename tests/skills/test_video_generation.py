"""Tests for the refactored video-generation skill (registry + config-driven).

The three skills (video-generation, image-generation, image-editing) each ship a
`providers` package imported bare as `from providers import PROVIDERS`. pytest
loads test_image_editing first, caching *its* providers in sys.modules. So this
module loads video's generate.py through a loader that purges any cached
`providers*` and puts video's scripts dir at sys.path[0], guaranteeing the bare
import resolves to video's package.

HTTP lives in providers/*.py and base.py, all via `import requests` (one shared
module object), so monkeypatching `requests.post`/`requests.get` covers create,
poll, and download across every adapter at once.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from skill_loader import FakeResp  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPO_ROOT / "skills" / "public" / "video-generation" / "scripts"


def _load_video_generate():
    """Load video's generate.py with its own providers package (no cross-skill bleed)."""
    for name in [
        n for n in sys.modules if n == "providers" or n.startswith("providers.")
    ]:
        del sys.modules[name]
    sys.path.insert(0, str(SCRIPT_DIR))
    spec = importlib.util.spec_from_file_location(
        "video_generation_generate", SCRIPT_DIR / "generate.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["video_generation_generate"] = module
    spec.loader.exec_module(module)
    return module


vid = _load_video_generate()
PROVIDERS = vid.PROVIDERS


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in [
        "MINIMAX_API_KEY",
        "MINIMAX_VIDEO_API_KEY",
        "VIDEO_GENERATION_PROVIDER",
        "VIDEO_GENERATION_MODEL",
        "MINIMAX_API_HOST",
        "MINIMAX_VIDEO_MODEL",
        "DEER_FLOW_CONFIG_PATH",
    ]:
        monkeypatch.delenv(k, raising=False)
    # Isolate from the repo-root config.yaml (which may declare a real
    # video_generation block): point at a nonexistent file so resolution tests
    # see the "no config" environment regardless of local runtime config.
    monkeypatch.setenv(
        "DEER_FLOW_CONFIG_PATH",
        str(Path(__file__).resolve().parent / "no_such_config.yaml"),
    )
    # Skip the poll sleep in base.
    import providers.base as base

    monkeypatch.setattr(base.time, "sleep", lambda *_: None)


# --- provider resolution (config + env + alias + fallback) ---


def test_registry_has_expected_providers():
    assert set(PROVIDERS) == {"minimax_h3", "minimax_v1"}


_H3_CONFIG = {
    "providers": [
        {"name": "minimax_h3", "models": [{"name": "MiniMax-H3"}]},
        {"name": "minimax_v1", "models": [{"name": "MiniMax-Hailuo-2.3"}]},
    ]
}


def test_model_name_routes_to_its_provider():
    # The model name is the routing key: provider is reverse-looked-up from config.
    assert vid._resolve_target(_H3_CONFIG, None, "MiniMax-H3") == ("minimax_h3", "MiniMax-H3")
    assert vid._resolve_target(_H3_CONFIG, None, "MiniMax-Hailuo-2.3") == ("minimax_v1", "MiniMax-Hailuo-2.3")


def test_model_not_declared_in_config_rejected():
    with pytest.raises(ValueError, match="is not declared in config.yaml"):
        vid._resolve_target(_H3_CONFIG, None, "no-such-model")


def test_model_without_config_falls_back_to_credential(monkeypatch):
    # No config block: nothing to reverse-look-up, so the credential decides and
    # the model is passed through untouched.
    monkeypatch.setenv("MINIMAX_VIDEO_API_KEY", "v")
    assert vid._resolve_target({}, None, "MiniMax-H3") == ("minimax_h3", "MiniMax-H3")


def test_explicit_provider_wins_and_minimax_aliases_to_v1():
    # Legacy "minimax" must keep routing to V1, not the new H3 (billing/behavior differ).
    assert vid._resolve_target({}, "minimax", None)[0] == "minimax_v1"
    assert vid._resolve_target({}, "minimax_h3", None)[0] == "minimax_h3"


def test_explicit_provider_overrides_model_lookup():
    # Escape hatch: --provider skips the reverse lookup entirely.
    assert vid._resolve_target(_H3_CONFIG, "minimax_v1", "MiniMax-H3") == ("minimax_v1", "MiniMax-H3")


def test_env_var_selects_provider(monkeypatch):
    monkeypatch.setenv("VIDEO_GENERATION_PROVIDER", "minimax_h3")
    assert vid._resolve_target({}, None, None)[0] == "minimax_h3"


def test_alias_enabled_check_accepts_legacy_config_names():
    # P0-3: config.yaml may declare the legacy "minimax" provider name;
    # the enabled check must compare after alias normalization.
    cfg = {"providers": [{"name": "minimax", "models": ["MiniMax-Hailuo-2.3"]}]}
    assert vid._resolve_target(cfg, "minimax", None)[0] == "minimax_v1"
    assert vid._resolve_target(cfg, None, "MiniMax-Hailuo-2.3") == (
        "minimax_v1",
        "MiniMax-Hailuo-2.3",
    )


def test_explicit_model_beats_env_provider(monkeypatch):
    # P0-4: VIDEO_GENERATION_PROVIDER must not preempt the --model reverse lookup.
    monkeypatch.setenv("VIDEO_GENERATION_PROVIDER", "minimax_h3")
    assert vid._resolve_target(_H3_CONFIG, None, "MiniMax-Hailuo-2.3") == ("minimax_v1", "MiniMax-Hailuo-2.3")


def test_env_model_beats_env_provider(monkeypatch):
    # The env pair must not cross-pair either: VIDEO_GENERATION_MODEL resolves its
    # owning provider; VIDEO_GENERATION_PROVIDER only fills in when no model is pinned.
    monkeypatch.setenv("VIDEO_GENERATION_PROVIDER", "minimax_v1")
    monkeypatch.setenv("VIDEO_GENERATION_MODEL", "MiniMax-H3")
    assert vid._resolve_target({}, None, None) == ("minimax_h3", "MiniMax-H3")



def test_model_without_config_routes_to_its_provider_not_credential(monkeypatch):
    # P0-2: no config block + frontend-picked model — the model's owning provider
    # wins over credential priority, even with a foreign credential present.
    monkeypatch.setenv("MINIMAX_API_KEY", "shared")
    monkeypatch.setenv("MINIMAX_VIDEO_API_KEY", "v")
    assert vid._resolve_target({}, None, "MiniMax-H3") == ("minimax_h3", "MiniMax-H3")
    monkeypatch.delenv("MINIMAX_VIDEO_API_KEY")
    assert vid._resolve_target({}, None, "MiniMax-H3") == ("minimax_h3", "MiniMax-H3")


def test_config_first_provider_and_model():
    assert vid._resolve_target(_H3_CONFIG, None, None) == ("minimax_h3", "MiniMax-H3")


def test_credential_fallback_prefers_video_key_then_shared(monkeypatch):
    monkeypatch.setenv("MINIMAX_VIDEO_API_KEY", "v")
    assert vid._resolve_target({}, None, None)[0] == "minimax_h3"
    monkeypatch.delenv("MINIMAX_VIDEO_API_KEY")
    monkeypatch.setenv("MINIMAX_API_KEY", "m")
    assert vid._resolve_target({}, None, None)[0] == "minimax_v1"


def test_credential_fallback_dedicated_key_prefers_h3(monkeypatch):
    # A video-dedicated key implies H3; it also wins over the shared key.
    monkeypatch.setenv("MINIMAX_VIDEO_API_KEY", "v")
    monkeypatch.setenv("MINIMAX_API_KEY", "shared")
    assert vid._resolve_target({}, None, None)[0] == "minimax_h3"


def test_minimax_video_key_prefers_dedicated_over_shared(monkeypatch):
    import providers.minimax_h3 as h3
    import providers.minimax_v1 as v1

    monkeypatch.setenv("MINIMAX_API_KEY", "shared")
    monkeypatch.setenv("MINIMAX_VIDEO_API_KEY", "dedicated")
    assert h3.PROVIDER().api_key() == "dedicated"
    assert v1.PROVIDER().api_key() == "dedicated"
    monkeypatch.delenv("MINIMAX_VIDEO_API_KEY")
    assert h3.PROVIDER().api_key() == "shared"
    assert v1.PROVIDER().api_key() == "shared"


def test_no_credential_no_config_raises():
    with pytest.raises(ValueError, match="No video provider"):
        vid._resolve_target({}, None, None)


def test_provider_not_in_enabled_list_rejected():
    cfg = {"providers": [{"name": "minimax_h3"}]}
    with pytest.raises(ValueError, match="not enabled"):
        vid._resolve_target(cfg, "minimax_v1", None)


def test_unknown_provider_rejected():
    with pytest.raises(ValueError, match="Unknown video generation provider"):
        vid._resolve_target({}, "nonexistent", None)


# --- prompt handling (AC-8) ---


def test_read_prompt_extracts_natural_language_from_json(tmp_path):
    pf = tmp_path / "p.json"
    pf.write_text(
        '{"prompt": "a cat stretching", "camera": "push-in", "audio": "ambient"}',
        encoding="utf-8",
    )
    assert vid._read_prompt(str(pf)) == "a cat stretching"


def test_read_prompt_passthrough_plain_text(tmp_path):
    pf = tmp_path / "p.txt"
    pf.write_text("plain text prompt", encoding="utf-8")
    assert vid._read_prompt(str(pf)) == "plain text prompt"


# --- registry dispatch: new adapter added without touching generate (AC-4) ---


def test_generate_dispatches_via_registry(monkeypatch, tmp_path):
    import providers.base as base

    class FakeProvider(base.BaseVideoProvider):
        name = "fake"

        def api_key(self):
            return "k"

        def generate(self, prompt_text, reference_images, output_file, params, **kw):
            Path(output_file).write_text(f"dispatched:{prompt_text}", encoding="utf-8")
            return "ok fake"

    monkeypatch.setitem(PROVIDERS, "fake", FakeProvider)
    monkeypatch.setenv("VIDEO_GENERATION_PROVIDER", "fake")
    pf = tmp_path / "p.txt"
    pf.write_text("hello", encoding="utf-8")
    out = tmp_path / "v.mp4"
    msg = vid.generate_video(str(pf), [], str(out))
    assert msg == "ok fake"
    assert out.read_text(encoding="utf-8") == "dispatched:hello"


# --- MiniMax H3 (V2) payload + full flow ---


def _h3():
    import providers.minimax_h3 as h3

    return h3


def test_h3_t2v_payload_includes_ratio(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, **kw):
        captured["url"] = url
        captured["json"] = json
        return FakeResp({"task_id": "T1"})

    monkeypatch.setenv("MINIMAX_API_KEY", "m")
    monkeypatch.setattr(requests, "post", fake_post)
    p = _h3().PROVIDER(model=None)
    p.create_task("a cat", [], {"resolution": "768P", "duration": 4, "ratio": "16:9"})
    assert captured["url"].endswith("/v2/video_generation")
    assert captured["json"]["ratio"] == "16:9"
    assert captured["json"]["content"] == [{"type": "text", "text": "a cat"}]


def test_h3_t2v_accepts_portrait_3_4_ratio(monkeypatch):
    captured = _capture_post(monkeypatch)
    _h3().PROVIDER(model=None).create_task(
        "a cat", [], {"resolution": "768P", "duration": 4, "ratio": "3:4"}
    )
    assert captured["json"]["ratio"] == "3:4"


def test_h3_i2v_omits_ratio_and_adds_first_frame(monkeypatch, tmp_path):
    # I2V ratio is fixed by the first frame; the API ignores an explicit ratio.
    captured = {}

    def fake_post(url, headers=None, json=None, **kw):
        captured["json"] = json
        return FakeResp({"task_id": "T1"})

    ref = tmp_path / "f.jpg"
    ref.write_bytes(b"\xff\xd8img")
    monkeypatch.setenv("MINIMAX_API_KEY", "m")
    monkeypatch.setattr(requests, "post", fake_post)
    p = _h3().PROVIDER(model=None)
    p.create_task(
        "turn to camera",
        [str(ref)],
        {"resolution": "768P", "duration": 4, "ratio": "16:9"},
    )
    assert "ratio" not in captured["json"]
    frame = captured["json"]["content"][1]
    assert frame["role"] == "first_frame"
    assert frame["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_h3_public_url_first_frame_passthrough(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, **kw):
        captured["json"] = json
        return FakeResp({"task_id": "T1"})

    monkeypatch.setenv("MINIMAX_API_KEY", "m")
    monkeypatch.setattr(requests, "post", fake_post)
    p = _h3().PROVIDER(model=None)
    p.create_task("x", ["https://cdn/x.png"], {})
    assert captured["json"]["content"][1]["image_url"]["url"] == "https://cdn/x.png"


def _capture_post(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, **kw):
        captured["json"] = json
        return FakeResp({"task_id": "T1"})

    monkeypatch.setenv("MINIMAX_API_KEY", "m")
    monkeypatch.setattr(requests, "post", fake_post)
    return captured


def test_h3_last_frame_role(monkeypatch):
    captured = _capture_post(monkeypatch)
    _h3().PROVIDER(model=None).create_task(
        "end here",
        ["https://cdn/end.png"],
        {"image_role": "last_frame", "ratio": "16:9"},
    )
    assert "ratio" not in captured["json"]  # frame mode: ratio fixed by image
    frame = captured["json"]["content"][1]
    assert frame["role"] == "last_frame"
    assert frame["image_url"]["url"] == "https://cdn/end.png"


def test_h3_first_last_frame_role(monkeypatch):
    captured = _capture_post(monkeypatch)
    _h3().PROVIDER(model=None).create_task(
        "grow up",
        ["https://cdn/a.png", "https://cdn/b.png"],
        {"image_role": "first_last"},
    )
    roles = [c.get("role") for c in captured["json"]["content"]]
    assert roles == [None, "first_frame", "last_frame"]
    assert "ratio" not in captured["json"]


def test_h3_first_last_with_single_image_is_first_frame_only(monkeypatch):
    captured = _capture_post(monkeypatch)
    _h3().PROVIDER(model=None).create_task(
        "x", ["https://cdn/a.png"], {"image_role": "first_last"}
    )
    roles = [c.get("role") for c in captured["json"]["content"]]
    assert roles == [None, "first_frame"]


def test_extra_image_prints_warning_and_is_dropped(monkeypatch, capsys):
    captured = _capture_post(monkeypatch)
    _h3().PROVIDER(model=None).create_task(
        "x",
        ["https://cdn/a.png", "https://cdn/b.png"],
        {"image_role": "first_frame"},
    )
    assert "uses only the first 1 image(s); ignoring 1 extra" in capsys.readouterr().out
    roles = [c.get("role") for c in captured["json"]["content"]]
    assert roles == [None, "first_frame"]


def test_h3_reference_role_honors_explicit_ratio(monkeypatch):
    captured = _capture_post(monkeypatch)
    _h3().PROVIDER(model=None).create_task(
        "dance like the refs",
        ["https://cdn/1.png", "https://cdn/2.png", "https://cdn/3.png"],
        {"image_role": "reference", "ratio": "9:16"},
    )
    roles = [c.get("role") for c in captured["json"]["content"]]
    assert roles == [None, "reference_image", "reference_image", "reference_image"]
    assert captured["json"]["ratio"] == "9:16"


def test_h3_reference_role_default_omits_ratio(monkeypatch):
    # Without an explicit ratio the API stays adaptive for r2va.
    captured = _capture_post(monkeypatch)
    _h3().PROVIDER(model=None).create_task(
        "x", ["https://cdn/1.png"], {"image_role": "reference"}
    )
    assert "ratio" not in captured["json"]


def test_h3_reference_role_passes_through_more_than_five_images(monkeypatch):
    captured = _capture_post(monkeypatch)
    _h3().PROVIDER(model=None).create_task(
        "x",
        [f"https://cdn/{i}.png" for i in range(6)],
        {"image_role": "reference"},
    )
    roles = [c.get("role") for c in captured["json"]["content"]]
    assert roles == [None] + ["reference_image"] * 6


def test_h3_full_flow_downloads_video(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "m")

    def fake_post(url, headers=None, json=None, **kw):
        return FakeResp({"task_id": "T1"})

    def fake_get(url, headers=None, params=None, **kw):
        if "/v2/query/video_generation/" in url:
            return FakeResp(
                {
                    "task": {
                        "status": "succeeded",
                        "content": {"url": "https://dl/v.mp4"},
                    }
                }
            )
        return FakeResp(content=b"H3VIDEO")

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)
    out = tmp_path / "v.mp4"
    pf = tmp_path / "p.txt"
    pf.write_text("a cat", encoding="utf-8")
    msg = vid.generate_video(str(pf), [], str(out), provider="minimax_h3")
    assert out.read_bytes() == b"H3VIDEO"
    assert "successfully" in msg.lower()


def test_h3_task_failed_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "m")
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResp({"task_id": "T1"}))
    monkeypatch.setattr(
        requests, "get", lambda *a, **k: FakeResp({"task": {"status": "failed"}})
    )
    pf = tmp_path / "p.txt"
    pf.write_text("x", encoding="utf-8")
    with pytest.raises(Exception, match="failed"):
        vid.generate_video(str(pf), [], str(tmp_path / "v.mp4"), provider="minimax_h3")


# --- ignored-param warning (AC-11) ---


def test_unsupported_param_warns(monkeypatch, capsys):
    import providers.base as base

    base.warn_ignored(
        "minimax_h3",
        {"resolution": "2K", "seed": 42},
        {"resolution", "duration", "ratio"},
    )
    assert "ignores unsupported params: seed" in capsys.readouterr().out


def test_supported_params_do_not_warn(capsys):
    import providers.base as base

    base.warn_ignored(
        "minimax_h3",
        {"resolution": "2K", "duration": 5},
        {"resolution", "duration", "ratio"},
    )
    assert capsys.readouterr().out == ""


def test_image_role_rejected_on_providers_without_support(monkeypatch, tmp_path):
    # P0-5: --image-role semantics differ per provider; unsupported must raise,
    # not warn-and-continue with a silently changed meaning.
    monkeypatch.setenv("MINIMAX_API_KEY", "m")
    pf = tmp_path / "p.txt"
    pf.write_text("x", encoding="utf-8")
    ref = tmp_path / "f.jpg"
    ref.write_bytes(b"\xff\xd8img")
    with pytest.raises(ValueError, match="does not support --image-role"):
        vid.generate_video(
            str(pf),
            [str(ref)],
            str(tmp_path / "v.mp4"),
            provider="minimax_v1",
            image_role="reference",
        )


def test_missing_credential_raises_naming_env_vars(tmp_path):
    # P0-1: missing credential must raise (stderr + exit 1 upstream) and name
    # the env vars to set, not return a success-shaped stdout line with exit 0.
    pf = tmp_path / "p.txt"
    pf.write_text("x", encoding="utf-8")
    with pytest.raises(Exception, match="MINIMAX_VIDEO_API_KEY"):
        vid.generate_video(str(pf), [], str(tmp_path / "v.mp4"), provider="minimax_h3")


def test_image_role_without_images_rejected(monkeypatch, tmp_path):
    # P2-2: --image-role with no reference images must not silently degrade to T2V.
    monkeypatch.setenv("MINIMAX_API_KEY", "m")
    pf = tmp_path / "p.txt"
    pf.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="requires --reference-images"):
        vid.generate_video(
            str(pf),
            [],
            str(tmp_path / "v.mp4"),
            provider="minimax_h3",
            image_role="first_frame",
        )


def test_h3_duration_and_resolution_validated():
    # P2-4: out-of-range values must raise instead of being sent as-is or
    # silently replaced by defaults (0 -> 4, "" -> 768P).
    p = _h3().PROVIDER(model=None)
    with pytest.raises(ValueError, match="duration must be 4-15"):
        p.create_task("x", [], {"duration": 0})
    with pytest.raises(ValueError, match="duration must be 4-15"):
        p.create_task("x", [], {"duration": 100})
    with pytest.raises(ValueError, match="unsupported resolution"):
        p.create_task("x", [], {"resolution": "4K"})


def test_h3_aspect_ratio_validated():
    # P2-4 remainder: the ratio enum in SKILL.md's output-settings table was
    # documented but never enforced.
    p = _h3().PROVIDER(model=None)
    with pytest.raises(ValueError, match="unsupported aspect ratio"):
        p.create_task("x", [], {"ratio": "3:2"})


# --- input-image spec validation (out-of-spec must fail locally, not on the API) ---


def _make_image(tmp_path, w, h, name="img.png"):
    from PIL import Image

    p = tmp_path / name
    Image.new("RGB", (w, h)).save(p)
    return p


def test_h3_local_image_out_of_ratio_blocked_locally(tmp_path):
    # A 2.573 banner exceeded the [0.4, 2.5] reference range and failed AFTER
    # task creation upstream (billed); the local check must catch it first.
    p = _h3().PROVIDER(model=None)
    img = _make_image(tmp_path, 898, 349)
    with pytest.raises(ValueError, match="check_materials.py"):
        p._validate_image_specs([str(img)])


def test_h3_local_image_out_of_side_range_blocked_locally(tmp_path):
    p = _h3().PROVIDER(model=None)
    img = _make_image(tmp_path, 8000, 4000)
    with pytest.raises(ValueError, match="side"):
        p._validate_image_specs([str(img)])


def test_h3_local_image_within_spec_passes(tmp_path):
    p = _h3().PROVIDER(model=None)
    img = _make_image(tmp_path, 1674, 875)
    p._validate_image_specs([str(img)])  # must not raise


def test_h3_spec_validation_skips_urls_and_undecodable(tmp_path):
    # URLs cannot be inspected locally; undecodable bytes are left to the API.
    p = _h3().PROVIDER(model=None)
    p._validate_image_specs(["https://cdn/wide.png"])
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"\xff\xd8img")
    p._validate_image_specs([str(bad)])


def test_h3_create_task_blocks_out_of_spec_before_post(monkeypatch, tmp_path):
    # End-to-end: the POST (and therefore a billed task) must never happen
    # for an out-of-spec local image.
    calls = {"post": 0}

    def fake_post(url, headers=None, json=None, **kw):
        calls["post"] += 1
        return FakeResp({"task_id": "T1"})

    monkeypatch.setenv("MINIMAX_API_KEY", "m")
    monkeypatch.setattr(requests, "post", fake_post)
    img = _make_image(tmp_path, 898, 349)
    p = _h3().PROVIDER(model=None)
    with pytest.raises(ValueError, match="check_materials.py"):
        p.create_task("x", [str(img)], {"image_role": "reference"})
    assert calls["post"] == 0


# --- check_materials.py spec preflight (auto-fix, never calls the provider) ---


def _load_check_materials():
    spec = importlib.util.spec_from_file_location(
        "vg_check_materials", SCRIPT_DIR / "check_materials.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["vg_check_materials"] = module
    spec.loader.exec_module(module)
    return module


def test_check_materials_crops_wide_image_to_spec(tmp_path):
    cm = _load_check_materials()
    src = _make_image(tmp_path, 898, 349, "wide.png")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    out_path, ops = cm.fix_image(str(src), str(out_dir))
    assert ops == ["crop-width"]
    from PIL import Image

    with Image.open(out_path) as im:
        w, h = im.size
    assert h == 349  # height kept, width cropped
    assert 0.4 <= w / h <= 2.5


def test_check_materials_crops_tall_image_to_spec(tmp_path):
    cm = _load_check_materials()
    src = _make_image(tmp_path, 300, 900, "tall.png")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    out_path, ops = cm.fix_image(str(src), str(out_dir))
    assert ops == ["crop-height"]
    from PIL import Image

    with Image.open(out_path) as im:
        w, h = im.size
    assert w == 300  # width kept, height cropped
    assert 0.4 <= w / h <= 2.5


def test_check_materials_downscales_huge_image(tmp_path):
    cm = _load_check_materials()
    src = _make_image(tmp_path, 8000, 4000, "huge.png")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    out_path, ops = cm.fix_image(str(src), str(out_dir))
    assert ops == ["downscale-to-5760"]
    from PIL import Image

    with Image.open(out_path) as im:
        w, h = im.size
    assert max(w, h) == 5760


def test_check_materials_keeps_in_spec_image_untouched(tmp_path):
    cm = _load_check_materials()
    src = _make_image(tmp_path, 1674, 875, "ok.png")
    out_path, ops = cm.fix_image(str(src), str(tmp_path))
    assert ops == []
    assert out_path == str(src)  # no copy produced


def test_check_materials_converts_unsupported_format(tmp_path):
    # In-spec dims but .gif: the API would reject the format, so the preflight
    # must convert instead of passing it through as [ok].
    from PIL import Image

    src = tmp_path / "anim.gif"
    Image.new("RGB", (1024, 768)).save(src, "GIF")
    cm = _load_check_materials()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    out_path, ops = cm.fix_image(str(src), str(out_dir))
    assert ops == ["convert-format"]
    assert out_path.endswith(".png")
    with Image.open(out_path) as im:
        assert im.size == (1024, 768)  # dims unchanged, format converted


def test_h3_local_image_unsupported_format_blocked_locally(tmp_path):
    p = _h3().PROVIDER(model=None)
    img = _make_image(tmp_path, 1024, 768, name="anim.gif")
    with pytest.raises(ValueError, match="unsupported format"):
        p._validate_image_specs([str(img)])


def test_output_file_is_not_overwritten(monkeypatch, tmp_path):
    # Align with image-generation: a finished (paid) video must not be clobbered.
    monkeypatch.setenv("MINIMAX_API_KEY", "m")
    pf = tmp_path / "p.txt"
    pf.write_text("x", encoding="utf-8")
    existing = tmp_path / "v.mp4"
    existing.write_bytes(b"old")
    with pytest.raises(FileExistsError, match="already exists"):
        vid.generate_video(str(pf), [], str(existing), provider="minimax_h3")
    assert existing.read_bytes() == b"old"


def test_model_providers_covers_every_known_model():
    # MODEL_PROVIDERS is the no-config routing truth; it must span known_models,
    # not just each adapter's default_model.
    for name, cls in vid.PROVIDERS.items():
        for model in cls.known_models:
            assert vid.MODEL_PROVIDERS[model] == name


def test_unknown_model_without_config_rejected(monkeypatch):
    # P0-2 nail: an unowned model must not be paired with whichever credential
    # happens to be set. --provider stays the escape hatch.
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    with pytest.raises(ValueError, match="is not a known model"):
        vid._resolve_target({}, None, "veo-9-unreleased")
    assert vid._resolve_target({}, "minimax_h3", "veo-9-unreleased") == ("minimax_h3", "veo-9-unreleased")


def test_missing_credential_without_env_names_omits_hint():
    # An adapter that forgot to declare api_key_envs must not print "set one of: ".
    from providers.base import BaseVideoProvider

    class NoEnvs(BaseVideoProvider):
        name = "no_envs"

        def api_key(self):
            return None

    with pytest.raises(Exception, match=r"provider=no_envs credential is not set$"):
        NoEnvs().generate("x", [], "out.mp4", {})


# --- legacy MiniMax V1 migration: behavior not regressed (three-step) ---


def test_v1_full_flow_uses_files_retrieve(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "m")
    posts = {}

    def fake_post(url, headers=None, json=None, **kw):
        posts["url"] = url
        posts["json"] = json
        return FakeResp({"task_id": "T1", "base_resp": {"status_code": 0}})

    def fake_get(url, headers=None, params=None, **kw):
        if url.endswith("/v1/query/video_generation"):
            assert params["task_id"] == "T1"
            return FakeResp(
                {"status": "Success", "file_id": "F1", "base_resp": {"status_code": 0}}
            )
        if url.endswith("/v1/files/retrieve"):
            assert params["file_id"] == "F1"
            return FakeResp(
                {
                    "file": {"download_url": "https://dl/v.mp4"},
                    "base_resp": {"status_code": 0},
                }
            )
        return FakeResp(content=b"V1VIDEO")

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)
    out = tmp_path / "v.mp4"
    pf = tmp_path / "p.txt"
    pf.write_text("a cat runs", encoding="utf-8")
    msg = vid.generate_video(str(pf), [], str(out), provider="minimax_v1")
    assert out.read_bytes() == b"V1VIDEO"
    assert posts["url"].endswith("/v1/video_generation")
    assert posts["json"]["model"] == "MiniMax-Hailuo-2.3"
    assert "successfully" in msg.lower()


def test_v1_first_frame_as_data_url(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "m")
    posts = {}

    def fake_post(url, headers=None, json=None, **kw):
        posts["json"] = json
        return FakeResp({"task_id": "T1", "base_resp": {"status_code": 0}})

    def fake_get(url, headers=None, params=None, **kw):
        if url.endswith("/v1/query/video_generation"):
            return FakeResp(
                {"status": "Success", "file_id": "F1", "base_resp": {"status_code": 0}}
            )
        if url.endswith("/v1/files/retrieve"):
            return FakeResp(
                {
                    "file": {"download_url": "https://dl/v.mp4"},
                    "base_resp": {"status_code": 0},
                }
            )
        return FakeResp(content=b"X")

    ref = tmp_path / "f.jpg"
    ref.write_bytes(b"\xff\xd8img")
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)
    pf = tmp_path / "p.txt"
    pf.write_text("x", encoding="utf-8")
    vid.generate_video(
        str(pf), [str(ref)], str(tmp_path / "v.mp4"), provider="minimax_v1"
    )
    assert posts["json"]["first_frame_image"].startswith("data:image/jpeg;base64,")


def test_v1_task_fail_keeps_context(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "m")
    monkeypatch.setattr(
        requests,
        "post",
        lambda *a, **k: FakeResp({"task_id": "T1", "base_resp": {"status_code": 0}}),
    )
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **k: FakeResp(
            {
                "status": "Fail",
                "base_resp": {"status_code": 1027, "status_msg": "blocked"},
            }
        ),
    )
    pf = tmp_path / "p.txt"
    pf.write_text("x", encoding="utf-8")
    with pytest.raises(Exception, match="T1 failed"):
        vid.generate_video(str(pf), [], str(tmp_path / "v.mp4"), provider="minimax_v1")


def test_poll_timeout_raises_with_handle(monkeypatch, tmp_path):
    # P2-5: timeout must raise (stderr + exit 1 upstream) and keep the task
    # handle in the message so a paid in-flight task stays recoverable.
    monkeypatch.setenv("MINIMAX_VIDEO_API_KEY", "v")
    monkeypatch.setattr(
        requests, "post", lambda *a, **k: FakeResp({"task_id": "task9"})
    )
    monkeypatch.setattr(
        requests, "get", lambda *a, **k: FakeResp({"task": {"status": "pending"}})
    )
    pf = tmp_path / "p.txt"
    pf.write_text("x", encoding="utf-8")
    with pytest.raises(Exception, match=r"task9 timed out after"):
        vid.generate_video(str(pf), [], str(tmp_path / "v.mp4"), provider="minimax_h3")


def test_h3_poll_window_not_shorter_than_sandbox_kill():
    # P2-5: the sandbox kills a run at 600s; the poll window must not give up first.
    import providers.minimax_h3 as h3

    p = h3.PROVIDER
    assert p.poll_interval * p.poll_max_attempts >= 600


def test_v1_poll_window_not_shorter_than_sandbox_kill():
    # Same contract as H3: the sandbox kills a run at 600s; never give up first.
    import providers.minimax_v1 as v1

    p = v1.PROVIDER
    assert p.poll_interval * p.poll_max_attempts >= 600


def test_h3_extra_images_warned(capsys):
    # P2-1: frame modes drop extra images — say so instead of failing silently.
    p = _h3().PROVIDER(model=None)
    urls = ["https://e/a.jpg", "https://e/b.jpg", "https://e/c.jpg"]

    content, _ = p._build_content("x", urls, "first_frame")
    assert len(content) == 2  # text + one frame
    assert "ignoring 2 extra" in capsys.readouterr().out

    content, _ = p._build_content("x", urls, "first_last")
    assert len(content) == 3  # text + first + last
    assert "ignoring 1 extra" in capsys.readouterr().out


# --- 768P -> 2K upscale (regeneration) + cancel + sidecar (feat-df-5) ---


def _capture_h3_post(captured):
    def fake_post(url, headers=None, json=None, **kw):
        captured["url"] = url
        captured["json"] = json
        return FakeResp({"task_id": "R1"})

    return fake_post


def test_upscale_replays_content_with_base_video(monkeypatch):
    captured = {}
    monkeypatch.setenv("MINIMAX_API_KEY", "m")
    monkeypatch.setattr(requests, "post", _capture_h3_post(captured))
    _h3().PROVIDER(model=None).create_task(
        "same prompt",
        ["https://cdn/a.png", "https://cdn/b.png"],
        {"image_role": "reference", "upscale_video": "https://dl/draft.mp4"},
    )
    assert captured["url"].endswith("/v2/video_regeneration")
    body = captured["json"]
    assert body["resolution"] == "2K"
    assert "duration" not in body
    assert "ratio" not in body
    assert body["content"][0] == {"type": "text", "text": "same prompt"}
    roles = [c.get("role") for c in body["content"]]
    assert roles == [None, "reference_image", "reference_image", "base_video"]
    assert body["content"][-1]["type"] == "video_url"
    assert body["content"][-1]["video_url"]["url"] == "https://dl/draft.mp4"


def test_upscale_local_source_becomes_data_url(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setenv("MINIMAX_API_KEY", "m")
    monkeypatch.setattr(requests, "post", _capture_h3_post(captured))
    draft = tmp_path / "draft.mp4"
    draft.write_bytes(b"MP4DATA")
    _h3().PROVIDER(model=None).create_task("p", [], {"upscale_video": str(draft)})
    url = captured["json"]["content"][-1]["video_url"]["url"]
    assert url.startswith("data:video/mp4;base64,")


def test_h3_default_duration_is_five(monkeypatch):
    # 5s (=120 frames) keeps the 2K-regeneration source floor (107 frames)
    # reachable on the default draft.
    captured = {}
    monkeypatch.setenv("MINIMAX_API_KEY", "m")
    monkeypatch.setattr(requests, "post", _capture_h3_post(captured))
    _h3().PROVIDER(model=None).create_task("p", [], {})
    assert captured["json"]["duration"] == 5


def test_upscale_rejects_generation_only_params(tmp_path):
    pf = tmp_path / "p.txt"
    pf.write_text("x", encoding="utf-8")
    out = tmp_path / "v2k.mp4"
    with pytest.raises(ValueError, match="regeneration endpoint"):
        vid.generate_video(
            str(pf), [], str(out), provider="minimax_h3",
            upscale_video="https://dl/d.mp4", duration=8,
        )
    with pytest.raises(ValueError, match="regeneration endpoint"):
        vid.generate_video(
            str(pf), [], str(out), provider="minimax_h3",
            upscale_video="https://dl/d.mp4", aspect_ratio="16:9",
        )
    with pytest.raises(ValueError, match="regeneration endpoint"):
        vid.generate_video(
            str(pf), [], str(out), provider="minimax_h3",
            upscale_video="https://dl/d.mp4", resolution="768P",
        )


def test_upscale_missing_source_rejected(tmp_path):
    pf = tmp_path / "p.txt"
    pf.write_text("x", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="source not found"):
        vid.generate_video(
            str(pf), [], str(tmp_path / "v2k.mp4"),
            provider="minimax_h3", upscale_video=str(tmp_path / "nope.mp4"),
        )


def test_upscale_source_over_limit_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(vid, "_UPSCALE_MAX_SOURCE_BYTES", 8)
    pf = tmp_path / "p.txt"
    pf.write_text("x", encoding="utf-8")
    big = tmp_path / "big.mp4"
    big.write_bytes(b"x" * 16)
    with pytest.raises(ValueError, match="64 MB"):
        vid.generate_video(
            str(pf), [], str(tmp_path / "v2k.mp4"),
            provider="minimax_h3", upscale_video=str(big),
        )


def test_upscale_output_must_be_new(tmp_path):
    pf = tmp_path / "p.txt"
    pf.write_text("x", encoding="utf-8")
    existing = tmp_path / "v2k.mp4"
    existing.write_bytes(b"old")
    with pytest.raises(FileExistsError, match="already exists"):
        vid.generate_video(
            str(pf), [], str(existing),
            provider="minimax_h3", upscale_video="https://dl/d.mp4",
        )


def test_upscale_rejected_on_provider_without_support(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "m")
    pf = tmp_path / "p.txt"
    pf.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="does not support --upscale-video"):
        vid.generate_video(
            str(pf), [], str(tmp_path / "v2k.mp4"),
            provider="minimax_v1", upscale_video="https://dl/d.mp4",
        )


def test_upscale_full_flow_writes_sidecar(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "m")
    captured = {}

    def fake_post(url, headers=None, json=None, **kw):
        captured["url"] = url
        return FakeResp({"task_id": "R1"})

    def fake_get(url, headers=None, **kw):
        if "/v2/query/video_generation/" in url:
            return FakeResp(
                {"task": {"status": "succeeded", "content": {"url": "https://dl/v2k.mp4"}}}
            )
        return FakeResp(content=b"2KVIDEO")

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)
    draft = tmp_path / "draft.mp4"
    draft.write_bytes(b"768P")
    pf = tmp_path / "p.txt"
    pf.write_text("same prompt", encoding="utf-8")
    out = tmp_path / "v2k.mp4"
    msg = vid.generate_video(
        str(pf), [], str(out), provider="minimax_h3", upscale_video=str(draft)
    )
    assert out.read_bytes() == b"2KVIDEO"
    assert "successfully" in msg.lower()
    assert captured["url"].endswith("/v2/video_regeneration")
    record = json.loads((tmp_path / "v2k.task.json").read_text(encoding="utf-8"))
    assert record["provider"] == "minimax_h3"
    assert record["task_id"] == "R1"
    assert record["prompt_file"] == str(pf)
    assert record["status"] == "succeeded"


def _capture_cancel(monkeypatch, status):
    calls = {"delete": []}

    def fake_get(url, headers=None, **kw):
        return FakeResp({"task": {"status": status}})

    def fake_delete(url, headers=None, **kw):
        calls["delete"].append(url)
        return FakeResp({"task_id": "T1", "action": "cancelled", "status": "cancelled"})

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "delete", fake_delete)
    return calls


def test_cancel_queued_sends_delete(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "m")
    calls = _capture_cancel(monkeypatch, "queued")
    msg = _h3().PROVIDER(model=None).cancel("T1")
    assert "cancelled" in msg
    assert calls["delete"][0].endswith("/v2/video_generation/T1")


def test_cancel_succeeded_never_deletes(monkeypatch):
    # P0-2: the DELETE endpoint record-deletes finished tasks; a cancel
    # request must never do that.
    monkeypatch.setenv("MINIMAX_API_KEY", "m")
    calls = _capture_cancel(monkeypatch, "succeeded")
    msg = _h3().PROVIDER(model=None).cancel("T1")
    assert "already succeeded" in msg
    assert calls["delete"] == []


def test_cancel_running_never_deletes(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "m")
    calls = _capture_cancel(monkeypatch, "running")
    msg = _h3().PROVIDER(model=None).cancel("T1")
    assert "running" in msg
    assert calls["delete"] == []


def test_cancel_updates_sidecar(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "m")
    _capture_cancel(monkeypatch, "queued")
    import providers.base as base

    out = tmp_path / "v.mp4"
    base.write_task_record(str(out), {"provider": "minimax_h3", "task_id": "T1"})
    _h3().PROVIDER(model=None).cancel("T1", output_file=str(out))
    record = json.loads((tmp_path / "v.task.json").read_text(encoding="utf-8"))
    assert record["status"] == "cancelled"


def test_cancel_task_via_generate(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "m")
    calls = _capture_cancel(monkeypatch, "queued")
    msg = vid.cancel_task("T1", provider="minimax_h3")
    assert "cancelled" in msg
    assert calls["delete"]


# --- read-only --query (timeout recovery) ---


def test_query_task_reports_succeeded_with_url(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "m")

    def fake_get(url, headers=None, params=None, **kw):
        return FakeResp(
            {"task": {"status": "succeeded", "content": {"url": "https://dl/v.mp4"}}}
        )

    monkeypatch.setattr(requests, "get", fake_get)
    msg = vid.query_task("T1", provider="minimax_h3")
    assert "succeeded" in msg
    assert "https://dl/v.mp4" in msg


def test_query_task_is_read_only_and_updates_sidecar(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "m")
    side_effects = {"delete": 0}

    def fake_get(url, headers=None, params=None, **kw):
        return FakeResp({"task": {"status": "failed"}})

    def fake_delete(*a, **kw):
        side_effects["delete"] += 1

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "delete", fake_delete)
    import providers.base as base

    out = tmp_path / "v.mp4"
    base.write_task_record(
        str(out), {"provider": "minimax_h3", "task_id": "T1", "status": "timeout"}
    )
    msg = vid.query_task("T1", provider="minimax_h3", output_file=str(out))
    assert "failed" in msg
    assert side_effects["delete"] == 0  # read-only: never cancels
    record = json.loads((tmp_path / "v.task.json").read_text(encoding="utf-8"))
    assert record["status"] == "failed"


def test_query_task_pending_without_url(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "m")

    def fake_get(url, headers=None, params=None, **kw):
        return FakeResp({"task": {"status": "running"}})

    monkeypatch.setattr(requests, "get", fake_get)
    msg = vid.query_task("T1", provider="minimax_h3")
    assert "pending" in msg
    assert "http" not in msg


def test_v1_cancel_unsupported(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "m")
    import providers.minimax_v1 as v1

    with pytest.raises(NotImplementedError, match="does not support --cancel"):
        v1.PROVIDER(model=None).cancel("T1")


def test_poll_prints_elapsed(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MINIMAX_API_KEY", "m")
    polls = {"n": 0}

    def fake_get(url, headers=None, **kw):
        if "/v2/query/video_generation/" in url:
            polls["n"] += 1
            if polls["n"] == 1:
                return FakeResp({"task": {"status": "pending"}})
            return FakeResp(
                {"task": {"status": "succeeded", "content": {"url": "https://dl/v.mp4"}}}
            )
        return FakeResp(content=b"V")

    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResp({"task_id": "T1"}))
    monkeypatch.setattr(requests, "get", fake_get)
    pf = tmp_path / "p.txt"
    pf.write_text("x", encoding="utf-8")
    vid.generate_video(str(pf), [], str(tmp_path / "v.mp4"), provider="minimax_h3")
    assert "s elapsed" in capsys.readouterr().out


def test_timeout_marks_sidecar(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_VIDEO_API_KEY", "v")
    monkeypatch.setattr(
        requests, "post", lambda *a, **k: FakeResp({"task_id": "task9"})
    )
    monkeypatch.setattr(
        requests, "get", lambda *a, **k: FakeResp({"task": {"status": "pending"}})
    )
    pf = tmp_path / "p.txt"
    pf.write_text("x", encoding="utf-8")
    out = tmp_path / "v.mp4"
    with pytest.raises(Exception, match="timed out"):
        vid.generate_video(str(pf), [], str(out), provider="minimax_h3")
    record = json.loads((tmp_path / "v.task.json").read_text(encoding="utf-8"))
    assert record["status"] == "timeout"


def test_failed_marks_sidecar(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "m")
    monkeypatch.setattr(
        requests, "post", lambda *a, **k: FakeResp({"task_id": "T1"})
    )
    monkeypatch.setattr(
        requests, "get", lambda *a, **k: FakeResp({"task": {"status": "failed"}})
    )
    pf = tmp_path / "p.txt"
    pf.write_text("x", encoding="utf-8")
    out = tmp_path / "v.mp4"
    with pytest.raises(Exception, match="failed"):
        vid.generate_video(str(pf), [], str(out), provider="minimax_h3")
    record = json.loads((tmp_path / "v.task.json").read_text(encoding="utf-8"))
    assert record["status"] == "failed"
