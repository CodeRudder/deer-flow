"""Runtime registry for image generation providers exposed by the built-in skill."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from deerflow.config.app_config import get_app_config
from deerflow.config.extensions_config import get_extensions_config

logger = logging.getLogger(__name__)


class ImageGenerationModel(BaseModel):
    name: str = Field(..., description="Provider model identifier passed to the image-generation skill")
    display_name: str = Field(..., description="Human-readable model name")
    description: str | None = Field(default=None, description="Model selection guidance shown in the frontend")


class ImageGenerationProvider(BaseModel):
    name: str = Field(..., description="Provider identifier passed to the image-generation skill")
    display_name: str = Field(..., description="Human-readable provider name")
    configured: bool = Field(default=False, description="Whether the provider has the required runtime configuration")
    models: list[ImageGenerationModel] = Field(default_factory=list)


class ImageGenerationProvidersResponse(BaseModel):
    skill_enabled: bool = Field(..., description="Whether the public image-generation skill is enabled")
    providers: list[ImageGenerationProvider] = Field(default_factory=list)


@dataclass(frozen=True)
class _ProviderDefinition:
    name: str
    display_name: str
    api_key_env: str
    models: tuple[ImageGenerationModel, ...]
    fallback_api_key_envs: tuple[str, ...] = ()


_PROVIDERS: tuple[_ProviderDefinition, ...] = (
    _ProviderDefinition(
        name="qwen_image",
        display_name="Qwen Image",
        api_key_env="QWEN_IMAGE_API_KEY",
        models=(
            ImageGenerationModel(
                name="qwen-image-2.0-pro",
                display_name="Qwen Image 2.0 Pro",
            ),
        ),
    ),
    _ProviderDefinition(
        name="openai_image",
        display_name="ChatGPT Image",
        api_key_env="OPENAI_IMAGE_API_KEY",
        fallback_api_key_envs=("OPENAI_API_KEY",),
        models=(
            ImageGenerationModel(
                name="gpt-image-2",
                display_name="GPT Image 2",
                description="通过 OpenAI-compatible Image API 生成图片；当前可接中转站，后续可切换官方兼容渠道。",
            ),
        ),
    ),
    _ProviderDefinition(
        name="h3_image",
        display_name="H3 生图（自建网关）",
        api_key_env="H3_IMAGE_AUTH_TOKEN",
        # The gateway's own env name, so an operator can copy it verbatim.
        fallback_api_key_envs=("H3IMG_AUTH_TOKEN",),
        models=(
            ImageGenerationModel(
                name="h3-frame-draft",
                display_name="H3 Frame Draft（预览）",
                description="4 步 / 256p（448×256），约 9 秒。提示词试错与批量预览首选",
            ),
            ImageGenerationModel(
                name="h3-frame-fast",
                display_name="H3 Frame Fast（草稿）",
                description="8 步 / 256p（448×256），约 16 秒。快速可用小图",
            ),
            ImageGenerationModel(
                name="h3-frame-std",
                display_name="H3 Frame Std（均衡，默认）",
                description="4 步 / 768p（1344×768），约 115 秒。常规出图",
            ),
            ImageGenerationModel(
                name="h3-frame-hq",
                display_name="H3 Frame HQ（成品）",
                description="8 步 / 768p（1344×768），约 225 秒。实测接近摄影级观感，成品出图选它",
            ),
        ),
    ),
)


def _image_generation_config() -> dict[str, Any]:
    try:
        extra = get_app_config().model_extra or {}
    except Exception:
        logger.exception("Failed to load image_generation config; falling back to built-in providers")
        return {}

    config = extra.get("image_generation", {})
    return config if isinstance(config, dict) else {}


def _is_skill_enabled() -> bool:
    return get_extensions_config().is_skill_enabled("image-generation", "public")


def _builtin_provider_by_name(provider_name: str) -> _ProviderDefinition | None:
    return next((definition for definition in _PROVIDERS if definition.name == provider_name), None)


def _model_from_config(value: Any) -> ImageGenerationModel | None:
    if isinstance(value, str) and value:
        return ImageGenerationModel(name=value, display_name=value)
    if not isinstance(value, dict):
        return None

    name = value.get("name") or value.get("model")
    if not isinstance(name, str) or not name:
        return None

    display_name = value.get("display_name")
    description = value.get("description")
    return ImageGenerationModel(
        name=name,
        display_name=display_name if isinstance(display_name, str) and display_name else name,
        description=description if isinstance(description, str) and description else None,
    )


def _models_from_config(provider_config: dict[str, Any]) -> list[ImageGenerationModel]:
    models_config = provider_config.get("models")
    if not isinstance(models_config, list):
        return []
    return [model for model in (_model_from_config(value) for value in models_config) if model is not None]


def _api_key_configured(provider_config: dict[str, Any], builtin: _ProviderDefinition | None) -> bool:
    api_key = provider_config.get("api_key")
    if isinstance(api_key, str):
        if api_key.startswith("$"):
            if os.getenv(api_key[1:]):
                return True
            return bool(builtin and any(os.getenv(env_name) for env_name in builtin.fallback_api_key_envs))
        if api_key:
            return True
        # Empty value means the referenced `$VAR` resolved to nothing at config
        # load; retry the provider key chain so this stays consistent with the
        # skill-side credential fallback (dedicated key, else shared key).
        if builtin:
            return bool(os.getenv(builtin.api_key_env) or any(os.getenv(env_name) for env_name in builtin.fallback_api_key_envs))
        return False

    if builtin:
        return bool(os.getenv(builtin.api_key_env) or any(os.getenv(env_name) for env_name in builtin.fallback_api_key_envs))

    return True


def _provider_from_config(provider_config: dict[str, Any]) -> ImageGenerationProvider | None:
    name = provider_config.get("name")
    if not isinstance(name, str) or not name:
        return None

    builtin = _builtin_provider_by_name(name)
    models = _models_from_config(provider_config)
    if not models and builtin:
        models = list(builtin.models)
    if not models:
        return None

    display_name = provider_config.get("display_name")

    return ImageGenerationProvider(
        name=name,
        display_name=display_name if isinstance(display_name, str) and display_name else (builtin.display_name if builtin else name),
        configured=_api_key_configured(provider_config, builtin),
        models=models,
    )


def _providers_from_config(config: dict[str, Any]) -> list[ImageGenerationProvider]:
    providers = config.get("providers")
    if not isinstance(providers, list):
        return []
    return [provider for provider in (_provider_from_config(value) for value in providers if isinstance(value, dict)) if provider is not None]


def _builtin_providers() -> list[ImageGenerationProvider]:
    return [
        ImageGenerationProvider(
            name=definition.name,
            display_name=definition.display_name,
            configured=bool(os.getenv(definition.api_key_env) or any(os.getenv(env_name) for env_name in definition.fallback_api_key_envs)),
            models=list(definition.models),
        )
        for definition in _PROVIDERS
    ]


def get_image_generation_providers() -> ImageGenerationProvidersResponse:
    """Return selectable image generation providers for the frontend."""

    if not _is_skill_enabled():
        return ImageGenerationProvidersResponse(skill_enabled=False, providers=[])

    config = _image_generation_config()
    providers = _providers_from_config(config) or _builtin_providers()

    return ImageGenerationProvidersResponse(
        skill_enabled=True,
        providers=providers,
    )
