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
        "GEMINI_API_KEY",
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
    assert set(PROVIDERS) == {"minimax_h3", "gemini", "minimax_v1"}


_H3_CONFIG = {
    "providers": [
        {"name": "minimax_h3", "models": [{"name": "MiniMax-H3"}]},
        {"name": "gemini", "models": [{"name": "veo-3"}]},
    ]
}


def test_model_name_routes_to_its_provider():
    # The model name is the routing key: provider is reverse-looked-up from config.
    assert vid._resolve_target(_H3_CONFIG, None, "MiniMax-H3") == ("minimax_h3", "MiniMax-H3")
    assert vid._resolve_target(_H3_CONFIG, None, "veo-3") == ("gemini", "veo-3")


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
    assert vid._resolve_target({}, "google", None)[0] == "gemini"
    assert vid._resolve_target({}, "minimax_h3", None)[0] == "minimax_h3"


def test_explicit_provider_overrides_model_lookup():
    # Escape hatch: --provider skips the reverse lookup entirely.
    assert vid._resolve_target(_H3_CONFIG, "gemini", "MiniMax-H3") == ("gemini", "MiniMax-H3")


def test_env_var_selects_provider(monkeypatch):
    monkeypatch.setenv("VIDEO_GENERATION_PROVIDER", "minimax_h3")
    assert vid._resolve_target({}, None, None)[0] == "minimax_h3"


def test_alias_enabled_check_accepts_legacy_config_names():
    # P0-3: config.yaml may declare the legacy "minimax"/"google" provider names;
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
    assert vid._resolve_target(_H3_CONFIG, None, "veo-3") == ("gemini", "veo-3")


def test_config_first_provider_and_model():
    assert vid._resolve_target(_H3_CONFIG, None, None) == ("minimax_h3", "MiniMax-H3")


def test_credential_fallback_prefers_gemini_then_v1(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    assert vid._resolve_target({}, None, None)[0] == "gemini"
    monkeypatch.delenv("GEMINI_API_KEY")
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


def test_h3_i2v_omits_ratio_and_adds_first_frame(monkeypatch, tmp_path):
    # I2V ratio is fixed by the first frame; sending ratio errors on that path.
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
    assert "ratio" not in captured["json"]  # image-bearing mode omits ratio
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


def test_h3_reference_role_multi_image_omits_ratio(monkeypatch):
    captured = _capture_post(monkeypatch)
    _h3().PROVIDER(model=None).create_task(
        "dance like the refs",
        ["https://cdn/1.png", "https://cdn/2.png", "https://cdn/3.png"],
        {"image_role": "reference", "ratio": "16:9"},
    )
    roles = [c.get("role") for c in captured["json"]["content"]]
    assert roles == [None, "reference_image", "reference_image", "reference_image"]
    assert "ratio" not in captured["json"]


def test_h3_reference_role_rejects_more_than_five_images(monkeypatch):
    _capture_post(monkeypatch)
    with pytest.raises(ValueError, match="at most 5 images"):
        _h3().PROVIDER(model=None).create_task(
            "x",
            [f"https://cdn/{i}.png" for i in range(6)],
            {"image_role": "reference"},
        )


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
    with pytest.raises(ValueError, match="does not support --image-role"):
        vid.generate_video(
            str(pf),
            [],
            str(tmp_path / "v.mp4"),
            provider="minimax_v1",
            image_role="reference",
        )


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


# --- Gemini migration: non-Bearer auth + operation flow, behavior not regressed ---


def test_gemini_uses_non_bearer_auth(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    import providers.gemini as gemini

    assert gemini.PROVIDER().auth_headers() == {"x-goog-api-key": "g"}


def test_gemini_full_flow_attaches_key_on_download(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    download_headers = {}

    def fake_post(url, headers=None, json=None, **kw):
        assert url.endswith(":predictLongRunning")
        return FakeResp({"name": "operations/op1"})

    def fake_get(url, headers=None, **kw):
        if url.endswith("operations/op1"):
            return FakeResp(
                {
                    "done": True,
                    "response": {
                        "generateVideoResponse": {
                            "generatedSamples": [{"video": {"uri": "https://dl/v.mp4"}}]
                        }
                    },
                }
            )
        download_headers.update(headers or {})
        return FakeResp(content=b"VEOVIDEO")

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)
    out = tmp_path / "v.mp4"
    pf = tmp_path / "p.txt"
    pf.write_text("a cat", encoding="utf-8")
    msg = vid.generate_video(str(pf), [], str(out), provider="gemini")
    assert out.read_bytes() == b"VEOVIDEO"
    assert download_headers == {"x-goog-api-key": "g"}
    assert "successfully" in msg.lower()
