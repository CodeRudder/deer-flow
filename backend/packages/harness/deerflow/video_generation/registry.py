"""Runtime registry for video generation providers exposed by the built-in skill."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from deerflow.config.app_config import get_app_config
from deerflow.config.extensions_config import get_extensions_config

logger = logging.getLogger(__name__)


class VideoGenerationModel(BaseModel):
    name: str = Field(..., description="Provider model identifier passed to the video-generation skill")
    display_name: str = Field(..., description="Human-readable model name")
    description: str | None = Field(default=None, description="Model selection guidance shown in the frontend")


class VideoGenerationProvider(BaseModel):
    name: str = Field(..., description="Provider identifier passed to the video-generation skill")
    display_name: str = Field(..., description="Human-readable provider name")
    configured: bool = Field(default=False, description="Whether the provider has the required runtime configuration")
    models: list[VideoGenerationModel] = Field(default_factory=list)


class VideoGenerationProvidersResponse(BaseModel):
    skill_enabled: bool = Field(..., description="Whether the public video-generation skill is enabled")
    providers: list[VideoGenerationProvider] = Field(default_factory=list)


@dataclass(frozen=True)
class _ProviderDefinition:
    name: str
    display_name: str
    api_key_env: str
    models: tuple[VideoGenerationModel, ...]
    fallback_api_key_envs: tuple[str, ...] = ()


_PROVIDERS: tuple[_ProviderDefinition, ...] = (
    _ProviderDefinition(
        name="minimax_h3",
        display_name="MiniMax H3",
        api_key_env="MINIMAX_VIDEO_API_KEY",
        fallback_api_key_envs=("MINIMAX_API_KEY",),
        models=(
            VideoGenerationModel(
                name="MiniMax-H3",
                display_name="MiniMax H3",
                description="V2 接口，768P/2K + 原生 32kHz 立体声，支持文生视频与首帧/尾帧/首尾帧/参考图生视频（参考图 ≤5 张）",
            ),
        ),
    ),
    _ProviderDefinition(
        name="gemini",
        display_name="Google Veo",
        api_key_env="GEMINI_API_KEY",
        models=(
            VideoGenerationModel(
                name="veo-3.1-generate-preview",
                display_name="Veo 3.1",
                description="支持文生视频与多图参考生视频",
            ),
        ),
    ),
)


def _video_generation_config() -> dict[str, Any]:
    try:
        extra = get_app_config().model_extra or {}
    except Exception:
        logger.exception("Failed to load video_generation config; falling back to built-in providers")
        return {}

    config = extra.get("video_generation", {})
    return config if isinstance(config, dict) else {}


def _is_skill_enabled() -> bool:
    return get_extensions_config().is_skill_enabled("video-generation", "public")


def _builtin_provider_by_name(provider_name: str) -> _ProviderDefinition | None:
    return next((definition for definition in _PROVIDERS if definition.name == provider_name), None)


def _model_from_config(value: Any) -> VideoGenerationModel | None:
    if isinstance(value, str) and value:
        return VideoGenerationModel(name=value, display_name=value)
    if not isinstance(value, dict):
        return None

    name = value.get("name") or value.get("model")
    if not isinstance(name, str) or not name:
        return None

    display_name = value.get("display_name")
    description = value.get("description")
    return VideoGenerationModel(
        name=name,
        display_name=display_name if isinstance(display_name, str) and display_name else name,
        description=description if isinstance(description, str) and description else None,
    )


def _models_from_config(provider_config: dict[str, Any]) -> list[VideoGenerationModel]:
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


def _provider_from_config(provider_config: dict[str, Any]) -> VideoGenerationProvider | None:
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

    return VideoGenerationProvider(
        name=name,
        display_name=display_name if isinstance(display_name, str) and display_name else (builtin.display_name if builtin else name),
        configured=_api_key_configured(provider_config, builtin),
        models=models,
    )


def _providers_from_config(config: dict[str, Any]) -> list[VideoGenerationProvider]:
    providers = config.get("providers")
    if not isinstance(providers, list):
        return []
    return [provider for provider in (_provider_from_config(value) for value in providers if isinstance(value, dict)) if provider is not None]


def _builtin_providers() -> list[VideoGenerationProvider]:
    return [
        VideoGenerationProvider(
            name=definition.name,
            display_name=definition.display_name,
            configured=bool(os.getenv(definition.api_key_env)),
            models=list(definition.models),
        )
        for definition in _PROVIDERS
    ]


def get_video_generation_providers() -> VideoGenerationProvidersResponse:
    """Return selectable video generation providers for the frontend."""

    if not _is_skill_enabled():
        return VideoGenerationProvidersResponse(skill_enabled=False, providers=[])

    config = _video_generation_config()
    providers = _providers_from_config(config) or _builtin_providers()

    return VideoGenerationProvidersResponse(
        skill_enabled=True,
        providers=providers,
    )
