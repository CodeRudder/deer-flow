"""Quota-control configuration loaded from ``config.yaml``."""

from __future__ import annotations

from pydantic import BaseModel, Field


class QuotaLimitConfig(BaseModel):
    """Default limit for a single quota dimension."""

    enabled: bool = Field(
        default=True,
        description="System-level enforcement switch and default for new user periods; usage is still tracked when disabled.",
    )
    limit_value: int | None = Field(default=None, ge=0, description="Monthly limit; null means unlimited.")


class QuotaDefaultsConfig(BaseModel):
    """Default monthly quota limits copied into new user period rows."""

    model_tokens: QuotaLimitConfig = Field(default_factory=lambda: QuotaLimitConfig(limit_value=1_000_000))
    model_requests: QuotaLimitConfig = Field(default_factory=lambda: QuotaLimitConfig(limit_value=500))
    image_generations: QuotaLimitConfig = Field(default_factory=lambda: QuotaLimitConfig(limit_value=50))


class QuotaControlConfig(BaseModel):
    """Configuration for admin quota control."""

    enabled: bool = Field(default=False, description="Master switch for quota checks.")
    period: str = Field(default="monthly", description="Quota period type. Current implementation supports monthly.")
    enforce_on_run_create: bool = Field(default=True, description="Check model quotas before creating the next run.")
    defaults: QuotaDefaultsConfig = Field(default_factory=QuotaDefaultsConfig)
