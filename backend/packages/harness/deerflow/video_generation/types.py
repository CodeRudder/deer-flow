from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class VideoGenerationPreference:
    provider: str | None = None
    model: str | None = None

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> VideoGenerationPreference:
        if not values:
            return cls()

        provider = values.get("video_generation_provider")
        model = values.get("video_generation_model")
        return cls(
            provider=provider if isinstance(provider, str) and provider else None,
            model=model if isinstance(model, str) and model else None,
        )

    @property
    def is_empty(self) -> bool:
        return not self.provider and not self.model

    def as_configurable(self) -> dict[str, str]:
        values: dict[str, str] = {}
        if self.provider:
            values["video_generation_provider"] = self.provider
        if self.model:
            values["video_generation_model"] = self.model
        return values
