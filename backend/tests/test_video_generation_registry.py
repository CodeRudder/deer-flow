from types import SimpleNamespace

from deerflow.config.extensions_config import ExtensionsConfig
from deerflow.video_generation import registry


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
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    response = registry.get_video_generation_providers()
    providers = {provider.name: provider for provider in response.providers}

    assert response.skill_enabled is True
    assert [provider.name for provider in response.providers] == ["minimax_h3", "gemini"]
    assert providers["minimax_h3"].configured is True
    assert providers["gemini"].configured is False
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
    assert providers["gemini"].configured is False
