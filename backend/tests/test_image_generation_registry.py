from types import SimpleNamespace

from deerflow.config.extensions_config import ExtensionsConfig
from deerflow.image_generation import registry


def test_registry_returns_disabled_response_when_skill_disabled(monkeypatch):
    monkeypatch.setattr(
        registry,
        "get_extensions_config",
        lambda: ExtensionsConfig(skills={"image-generation": {"enabled": False}}),
    )

    response = registry.get_image_generation_providers()

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
    monkeypatch.setenv("QWEN_IMAGE_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    response = registry.get_image_generation_providers()
    providers = {provider.name: provider for provider in response.providers}

    assert response.skill_enabled is True
    assert [provider.name for provider in response.providers] == ["qwen_image", "gemini"]
    assert providers["qwen_image"].configured is True
    assert providers["gemini"].configured is False
    assert providers["qwen_image"].models[0].name == "qwen-image-2.0-pro"


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
                "image_generation": {
                    "providers": [
                        {
                            "name": "custom_provider",
                            "display_name": "Custom Provider",
                            "api_key": "$CUSTOM_IMAGE_KEY",
                            "models": [
                                {
                                    "name": "custom-image-v1",
                                    "display_name": "Custom Image V1",
                                },
                                "custom-image-v2",
                            ],
                        },
                        {
                            "name": "qwen_image",
                            "api_key": "$QWEN_IMAGE_API_KEY",
                            "models": ["qwen-image-2.0-pro"],
                        },
                    ],
                }
            }
        ),
    )
    monkeypatch.setenv("CUSTOM_IMAGE_KEY", "test-key")
    monkeypatch.delenv("QWEN_IMAGE_API_KEY", raising=False)

    response = registry.get_image_generation_providers()

    assert [provider.name for provider in response.providers] == ["custom_provider", "qwen_image"]
    provider = response.providers[0]
    assert provider.name == "custom_provider"
    assert provider.display_name == "Custom Provider"
    assert provider.configured is True
    assert [(model.name, model.display_name) for model in provider.models] == [
        ("custom-image-v1", "Custom Image V1"),
        ("custom-image-v2", "custom-image-v2"),
    ]
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
                "image_generation": {
                    "providers": [
                        {
                            "name": "custom_provider",
                            "api_key": "resolved-secret-value",
                            "models": ["custom-image-v1"],
                        },
                    ],
                }
            }
        ),
    )

    response = registry.get_image_generation_providers()

    assert response.providers[0].configured is True
