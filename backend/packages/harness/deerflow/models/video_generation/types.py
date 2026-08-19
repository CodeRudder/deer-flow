from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class VideoGenerationPreference:
    """Runtime video generation preference. Only the model is carried: the
    provider is derived from the model name via config.yaml inside the skill."""

    model: str | None = None

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> VideoGenerationPreference:
        if not values:
            return cls()

        model = values.get("video_generation_model")
        return cls(model=model if isinstance(model, str) and model else None)

    @property
    def is_empty(self) -> bool:
        return not self.model

    def as_configurable(self) -> dict[str, str]:
        return {"video_generation_model": self.model} if self.model else {}
