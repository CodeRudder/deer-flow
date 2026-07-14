"""API contracts for the administrator dashboard."""

from __future__ import annotations

from typing import Any, Literal

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


class UsageUserRank(BaseModel):
    rank: int
    user_id: str
    email: str
    value: int


class UsageUsersResponse(BaseModel):
    period: PeriodInfo
    rankings: dict[Literal["tokens", "requests", "images"], list[UsageUserRank]]


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


class TraceUserOption(BaseModel):
    user_id: str
    email: str
    last_active_at: str | None


class TraceUsersResponse(BaseModel):
    items: list[TraceUserOption]
    next_cursor: str | None


class TraceOverviewPeriod(BaseModel):
    days: int
    period_start: str
    period_end: str
    timezone: str


class TraceOverviewSummary(BaseModel):
    thread_count: int
    run_count: int
    total_tokens: int
    model_requests: int
    image_generations: int


class TraceTrendPoint(BaseModel):
    date: str
    tokens: int
    model_requests: int
    image_generations: int


class TraceModelUsage(BaseModel):
    model: str
    tokens: int
    share: float


class TraceUserOverviewResponse(BaseModel):
    user: TraceUserOption
    period: TraceOverviewPeriod
    summary: TraceOverviewSummary
    trends: list[TraceTrendPoint]
    models: list[TraceModelUsage]


class TraceRunItem(BaseModel):
    run_id: str
    thread_id: str
    thread_title: str
    user_id: str | None
    email: str | None
    status: str
    model_name: str | None
    created_at: str | None
    updated_at: str | None
    duration_ms: int | None
    duration_approximate: bool
    first_human_message: str | None
    last_ai_message: str | None
    input_preview: str
    output_preview: str
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    llm_call_count: int
    image_generation_count: int
    token_usage_by_model: dict[str, Any]
    error: str | None


class TraceRunsResponse(BaseModel):
    items: list[TraceRunItem]
    total: int
    page: int
    page_size: int


class TraceEvent(BaseModel):
    thread_id: str
    run_id: str
    event_type: str
    category: str
    content: Any
    metadata: dict[str, Any]
    seq: int
    created_at: str


class TraceToolUsage(BaseModel):
    name: str
    call_count: int


class TraceToolSummary(BaseModel):
    total_calls: int
    tools: list[TraceToolUsage]
    complete: bool


class TraceEventsResponse(BaseModel):
    run_id: str
    thread_id: str
    items: list[TraceEvent]
    returned: int
    limit: int
    truncated: bool
    tool_summary: TraceToolSummary
