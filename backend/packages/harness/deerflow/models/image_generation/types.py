from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ImageGenerationPreference:
    """Runtime image generation preference. Only the model is carried: the
    provider is derived from the model name via config.yaml inside the skill."""

    model: str | None = None

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> ImageGenerationPreference:
        if not values:
            return cls()

        model = values.get("image_generation_model")
        return cls(model=model if isinstance(model, str) and model else None)

    @property
    def is_empty(self) -> bool:
        return not self.model

    def as_configurable(self) -> dict[str, str]:
        return {"image_generation_model": self.model} if self.model else {}
