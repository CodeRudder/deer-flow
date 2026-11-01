from types import SimpleNamespace

from deerflow.config.extensions_config import ExtensionsConfig
from deerflow.models.image_generation import registry


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
    monkeypatch.delenv("OPENAI_IMAGE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("H3_IMAGE_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("H3IMG_AUTH_TOKEN", raising=False)

    response = registry.get_image_generation_providers()
    providers = {provider.name: provider for provider in response.providers}

    assert response.skill_enabled is True
    assert [provider.name for provider in response.providers] == [
        "qwen_image",
        "openai_image",
        "h3_image",
    ]
    assert providers["qwen_image"].configured is True
    assert providers["openai_image"].configured is False
    assert providers["qwen_image"].models[0].name == "qwen-image-2.0-pro"


def test_registry_uses_openai_api_key_as_openai_image_fallback(monkeypatch):
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
                            "name": "openai_image",
                            "api_key": "$OPENAI_IMAGE_API_KEY",
                            "models": ["gpt-image-2"],
                        },
                    ],
                }
            }
        ),
    )
    monkeypatch.delenv("OPENAI_IMAGE_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "fallback-key")

    response = registry.get_image_generation_providers()

    assert response.providers[0].configured is True


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
                                    "description": "Best for custom image generation.",
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
    assert provider.models[0].description == "Best for custom image generation."
    assert provider.models[1].description is None
    assert response.providers[1].configured is False


def test_registry_builtin_providers_use_fallback_key_chain(monkeypatch):
    # The builtin path must honor fallback_api_key_envs, matching the config path
    # and the skill-side credential fallback.
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
    monkeypatch.delenv("QWEN_IMAGE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_IMAGE_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "shared-key")

    response = registry.get_image_generation_providers()
    providers = {provider.name: provider for provider in response.providers}

    assert providers["openai_image"].configured is True
    assert providers["qwen_image"].configured is False


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
                "image_generation": {
                    "providers": [
                        {"name": "openai_image", "api_key": "", "models": ["gpt-image-2"]},
                    ],
                }
            }
        ),
    )
    monkeypatch.delenv("OPENAI_IMAGE_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "shared-key")

    response = registry.get_image_generation_providers()

    assert response.providers[0].configured is True

    monkeypatch.delenv("OPENAI_API_KEY")
    response = registry.get_image_generation_providers()

    assert response.providers[0].configured is False


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
