import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

from deerflow.config.extensions_config import ExtensionsConfig
from deerflow.models.video_generation import registry

# --- skill-side parity (refactor-df-4) ----------------------------------------
# The video-generation skill is not a package; load its providers package by
# path. Skill code uses intra-package relative imports, so it must be imported
# as a package with the skill scripts dir on sys.path (same pattern as
# tests/skills/test_video_generation.py). Purge cached `providers*` modules
# first — the package name is generic and may collide across skills.
_SKILL_SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "public" / "video-generation" / "scripts"

# legacy minimax_v1 is intentionally absent from the registry (frontend does
# not offer it; reachable via env fallback only).
_REGISTRY_EXCLUDED_PROVIDERS = {"minimax_v1"}
_REGISTRY_EXCLUDED_MODELS = {"MiniMax-Hailuo-2.3"}


def _load_skill_providers():
    for name in [m for m in sys.modules if m == "providers" or m.startswith("providers.")]:
        del sys.modules[name]
    sys.path.insert(0, str(_SKILL_SCRIPTS))
    try:
        return importlib.import_module("providers")
    finally:
        sys.path.remove(str(_SKILL_SCRIPTS))


def _skill_models_by_provider():
    providers = _load_skill_providers()
    return {name: set(cls.known_models or (cls.default_model,)) for name, cls in providers.PROVIDERS.items()}


def test_skill_provider_names_match_backend_registry():
    skill_by_name = _skill_models_by_provider()
    backend_by_name = {d.name for d in registry._PROVIDERS}

    assert set(skill_by_name) - _REGISTRY_EXCLUDED_PROVIDERS == backend_by_name


def test_skill_models_match_backend_registry_catalog():
    skill_by_name = _skill_models_by_provider()
    backend_by_name = {d.name: {m.name for m in d.models} for d in registry._PROVIDERS}

    for name, backend_models in backend_by_name.items():
        assert skill_by_name[name] - _REGISTRY_EXCLUDED_MODELS == backend_models


def test_registry_returns_disabled_response_when_skill_disabled(monkeypatch):
    monkeypatch.setattr(
        registry,
        "get_extensions_config",
        lambda: ExtensionsConfig(skills={"video-generation": {"enabled": False}}),
    )

    response = registry.get_video_generation_providers()

    assert response.skill_enabled is False
    assert response.providers == []


def test_registry_uses_builtin_providers_when_config_is_absent(monkeypatch):
    monkeypatch.setattr(
        registry,
        "get_extensions_config",
        lambda: ExtensionsConfig(skills={}),
    )
    monkeypatch.setattr(
        registry,
        "get_app_config",
        lambda: SimpleNamespace(model_extra={}),
    )
    monkeypatch.setenv("MINIMAX_VIDEO_API_KEY", "test-key")
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)

    response = registry.get_video_generation_providers()
    providers = {provider.name: provider for provider in response.providers}

    assert response.skill_enabled is True
    assert [provider.name for provider in response.providers] == ["minimax_h3", "seedance"]
    assert providers["minimax_h3"].configured is True
    assert providers["minimax_h3"].models[0].name == "MiniMax-H3"


def test_registry_uses_minimax_api_key_as_minimax_video_fallback(monkeypatch):
    monkeypatch.setattr(
        registry,
        "get_extensions_config",
        lambda: ExtensionsConfig(skills={}),
    )
    monkeypatch.setattr(
        registry,
        "get_app_config",
        lambda: SimpleNamespace(
            model_extra={
                "video_generation": {
                    "providers": [
                        {
                            "name": "minimax_h3",
                            "api_key": "$MINIMAX_VIDEO_API_KEY",
                            "models": ["MiniMax-H3"],
                        },
                    ],
                }
            }
        ),
    )
    monkeypatch.delenv("MINIMAX_VIDEO_API_KEY", raising=False)
    monkeypatch.setenv("MINIMAX_API_KEY", "fallback-key")

    response = registry.get_video_generation_providers()

    assert response.providers[0].configured is True


def test_registry_empty_resolved_api_key_falls_back_to_shared_key(monkeypatch):
    # An api_key resolved to "" (referenced $VAR missing at config load) retries
    # the provider key chain, matching the skill-side runtime credential fallback.
    monkeypatch.setattr(
        registry,
        "get_extensions_config",
        lambda: ExtensionsConfig(skills={}),
    )
    monkeypatch.setattr(
        registry,
        "get_app_config",
        lambda: SimpleNamespace(
            model_extra={
                "video_generation": {
                    "providers": [
                        {"name": "minimax_h3", "api_key": "", "models": ["MiniMax-H3"]},
                    ],
                }
            }
        ),
    )
    monkeypatch.delenv("MINIMAX_VIDEO_API_KEY", raising=False)
    monkeypatch.setenv("MINIMAX_API_KEY", "shared-key")

    response = registry.get_video_generation_providers()

    assert response.providers[0].configured is True

    monkeypatch.delenv("MINIMAX_API_KEY")
    response = registry.get_video_generation_providers()

    assert response.providers[0].configured is False


def test_registry_reads_selectable_models_from_config_in_order(monkeypatch):
    monkeypatch.setattr(
        registry,
        "get_extensions_config",
        lambda: ExtensionsConfig(skills={}),
    )
    monkeypatch.setattr(
        registry,
        "get_app_config",
        lambda: SimpleNamespace(
            model_extra={
                "video_generation": {
                    "providers": [
                        {
                            "name": "custom_provider",
                            "display_name": "Custom Provider",
                            "api_key": "$CUSTOM_VIDEO_KEY",
                            "models": [
                                {
                                    "name": "custom-video-v1",
                                    "display_name": "Custom Video V1",
                                    "description": "Best for custom video generation.",
                                },
                                "custom-video-v2",
                            ],
                        },
                        {
                            "name": "minimax_h3",
                            "api_key": "$MINIMAX_VIDEO_API_KEY",
                            "models": ["MiniMax-H3"],
                        },
                    ],
                }
            }
        ),
    )
    monkeypatch.setenv("CUSTOM_VIDEO_KEY", "test-key")
    monkeypatch.delenv("MINIMAX_VIDEO_API_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)

    response = registry.get_video_generation_providers()

    assert [provider.name for provider in response.providers] == ["custom_provider", "minimax_h3"]
    provider = response.providers[0]
    assert provider.name == "custom_provider"
    assert provider.display_name == "Custom Provider"
    assert provider.configured is True
    assert [(model.name, model.display_name) for model in provider.models] == [
        ("custom-video-v1", "Custom Video V1"),
        ("custom-video-v2", "custom-video-v2"),
    ]
    assert provider.models[0].description == "Best for custom video generation."
    assert provider.models[1].description is None
    assert response.providers[1].configured is False


def test_registry_treats_resolved_api_key_value_as_configured(monkeypatch):
    monkeypatch.setattr(
        registry,
        "get_extensions_config",
        lambda: ExtensionsConfig(skills={}),
    )
    monkeypatch.setattr(
        registry,
        "get_app_config",
        lambda: SimpleNamespace(
            model_extra={
                "video_generation": {
                    "providers": [
                        {
                            "name": "custom_provider",
                            "api_key": "resolved-secret-value",
                            "models": ["custom-video-v1"],
                        },
                    ],
                }
            }
        ),
    )

    response = registry.get_video_generation_providers()

    assert response.providers[0].configured is True


def test_registry_builtin_providers_use_fallback_key_chain(monkeypatch):
    # P2-11: the builtin path must honor fallback_api_key_envs, matching the
    # config-path and skill-side credential resolution.
    monkeypatch.setattr(
        registry,
        "get_extensions_config",
        lambda: ExtensionsConfig(skills={}),
    )
    monkeypatch.setattr(
        registry,
        "get_app_config",
        lambda: SimpleNamespace(model_extra={}),
    )
    monkeypatch.delenv("MINIMAX_VIDEO_API_KEY", raising=False)
    monkeypatch.setenv("MINIMAX_API_KEY", "shared-key")

    response = registry.get_video_generation_providers()
    providers = {provider.name: provider for provider in response.providers}

    assert providers["minimax_h3"].configured is True


def _enabled(monkeypatch, model_extra=None):
    monkeypatch.setattr(
        registry,
        "get_extensions_config",
        lambda: ExtensionsConfig(skills={}),
    )
    if model_extra is not None:
        monkeypatch.setattr(
            registry,
            "get_app_config",
            lambda: SimpleNamespace(model_extra=model_extra),
        )
    for env in ("MINIMAX_VIDEO_API_KEY", "MINIMAX_API_KEY"):
        monkeypatch.delenv(env, raising=False)


def test_registry_falls_back_to_builtin_when_config_load_fails(monkeypatch):
    # A broken config must not 500 the /providers endpoint.
    def boom():
        raise RuntimeError("config exploded")

    _enabled(monkeypatch)
    monkeypatch.setattr(registry, "get_app_config", boom)

    response = registry.get_video_generation_providers()

    assert response.skill_enabled is True
    assert [p.name for p in response.providers] == ["minimax_h3", "seedance"]


def test_registry_drops_provider_with_no_models_and_no_builtin_match(monkeypatch):
    # A custom provider without models cannot render; drop it, keep the rest.
    _enabled(
        monkeypatch,
        {"video_generation": {"providers": [{"name": "mystery"}, {"name": "minimax_h3"}]}},
    )

    response = registry.get_video_generation_providers()

    assert [p.name for p in response.providers] == ["minimax_h3"]


def test_registry_drops_provider_with_missing_or_invalid_name(monkeypatch):
    _enabled(
        monkeypatch,
        {"video_generation": {"providers": [{}, {"name": ""}, {"name": 123}, {"name": "minimax_h3"}]}},
    )

    response = registry.get_video_generation_providers()

    assert [p.name for p in response.providers] == ["minimax_h3"]


def test_registry_skips_non_dict_provider_entries(monkeypatch):
    _enabled(
        monkeypatch,
        {"video_generation": {"providers": ["oops", 42, {"name": "minimax_h3"}]}},
    )

    response = registry.get_video_generation_providers()

    assert [p.name for p in response.providers] == ["minimax_h3"]


def test_registry_ignores_non_list_providers(monkeypatch):
    # A malformed providers value degrades to the builtin table, not an error.
    _enabled(monkeypatch, {"video_generation": {"providers": "oops"}})

    response = registry.get_video_generation_providers()

    assert response.skill_enabled is True
    assert [p.name for p in response.providers] == ["minimax_h3", "seedance"]


def test_registry_ignores_non_list_models(monkeypatch):
    # Invalid models fall back to the builtin provider's model list.
    _enabled(
        monkeypatch,
        {"video_generation": {"providers": [{"name": "minimax_h3", "models": "oops"}]}},
    )

    response = registry.get_video_generation_providers()

    assert [m.name for m in response.providers[0].models] == ["MiniMax-H3"]


def test_registry_ignores_non_dict_video_generation_section(monkeypatch):
    _enabled(monkeypatch, {"video_generation": "oops"})

    response = registry.get_video_generation_providers()

    assert response.skill_enabled is True
    assert [p.name for p in response.providers] == ["minimax_h3", "seedance"]


def test_registry_supports_model_key_alias(monkeypatch):
    # Config may write `model:` instead of `name:` for a model entry.
    _enabled(
        monkeypatch,
        {"video_generation": {"providers": [{"name": "minimax_v1", "models": [{"model": "veo-custom"}]}]}},
    )

    response = registry.get_video_generation_providers()

    assert [m.name for m in response.providers[0].models] == ["veo-custom"]


def test_skill_known_models_derive_from_manifest():
    # The routing/validation/describe surfaces must never split: known_models
    # is the routing truth, manifest.models is the capability truth — bind them.
    providers = _load_skill_providers()
    for name, cls in providers.PROVIDERS.items():
        manifest_names = tuple(m.name for m in providers.MANIFESTS[name].models)
        assert tuple(cls.known_models or ()) == manifest_names


def test_skill_model_descriptions_match_registry_catalog():
    providers = _load_skill_providers()
    backend = {d.name: {m.name: m.description for m in d.models} for d in registry._PROVIDERS}
    for name, backend_models in backend.items():
        manifest_by_name = {m.name: m.description for m in providers.MANIFESTS[name].models}
        for model_name, desc in backend_models.items():
            assert manifest_by_name[model_name] == desc, f"{name}/{model_name} catalog copy drifted"
