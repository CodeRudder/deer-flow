"""API contracts for the administrator dashboard."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class PeriodInfo(BaseModel):
    period: str
    label: str
    period_start: str
    period_end: str
    timezone: str


class UsageSummaryResponse(BaseModel):
    period: PeriodInfo
    total_tokens: int
    total_input_tokens: int
    total_output_tokens: int
    model_requests: int
    image_generations: int
    run_count: int
    active_users: int
    running_runs: int


class UsageTrendPoint(BaseModel):
    label: str
    tokens: int
    requests: int
    images: int


class UsageTrendsResponse(BaseModel):
    period: PeriodInfo
    bucket: Literal["hour", "day"]
    items: list[UsageTrendPoint]


class UsageSession(BaseModel):
    thread_id: str
    user_id: str | None
    email: str
    title: str
    value: int


class UsageSessionsResponse(BaseModel):
    period: PeriodInfo
    metric: Literal["tokens", "requests", "images"]
    items: list[UsageSession]


class UsageModel(BaseModel):
    model: str
    type: Literal["LLM"]
    requests: int | None
    tokens: int
    runs: int
    share: float


class UsageModelsResponse(BaseModel):
    period: PeriodInfo
    items: list[UsageModel]


class QuotaMetricResponse(BaseModel):
    enabled: bool
    used: int
    limit: int | None
    remaining: int | None


class QuotaUserResponse(BaseModel):
    user_id: str
    email: str
    role: str
    status: Literal["normal", "warning", "exceeded", "unlimited"]
    period: PeriodInfo
    model_tokens: QuotaMetricResponse
    model_requests: QuotaMetricResponse
    image_generations: QuotaMetricResponse


class QuotaUsersResponse(BaseModel):
    items: list[QuotaUserResponse]
    total: int
    page: int
    page_size: int
    period: PeriodInfo
