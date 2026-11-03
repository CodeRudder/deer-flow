"""Thread-interjection feature configuration (docs/research/2026-11-01-thread-interjection/plan-b.md §6)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class InterjectionConfig(BaseModel):
    """Per-run message interjection (queue + inject at step boundaries).

    Disabled by default: with ``enabled=False`` an in-flight thread keeps the
    legacy ``409 Conflict`` behaviour, so the feature can be rolled back by
    flipping one config key.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False, description="Master switch; false keeps the legacy 409 behaviour on in-flight threads")
    max_pending: int = Field(default=8, ge=1, description="Per-run queue limit for pending interjections; excess requests get 429")
    im_channels: bool = Field(default=False, description="Independent IM-channel rollout switch (not yet wired; reserved)")
