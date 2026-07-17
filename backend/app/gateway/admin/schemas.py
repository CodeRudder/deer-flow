"""API contracts for the administrator dashboard."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, EmailStr

from app.gateway.auth.models import AccountStatus, ApprovalEmailStatus


class UserStatusFilter(StrEnum):
    """Account-status filter values accepted by the admin user list."""

    ACTIVE = AccountStatus.ACTIVE.value
    PENDING = AccountStatus.PENDING.value
    DISABLED = AccountStatus.DISABLED.value
    ALL = "all"


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


class QuotaUsersResponse(BaseModel):
    items: list[dict[str, Any]]
    total: int
    page: int
    page_size: int
    reference_at: str
    timezone: str


class QuotaUserResponse(BaseModel):
    user: dict[str, Any]
    reference_at: str
    timezone: str
    status: Literal["normal", "warning", "exceeded", "unlimited"]
    items: list[dict[str, Any]]


class QuotaScopesResponse(BaseModel):
    items: list[dict[str, Any]]
    unmatched_models: list[dict[str, str]]
    configuration_warnings: list[dict[str, Any]]


class UserManagementSummaryResponse(BaseModel):
    registration_approval_enabled: bool
    total: int
    active: int
    pending: int
    disabled: int


class UserManagementItem(BaseModel):
    id: str
    email: str
    role: Literal["admin", "user"]
    source: Literal["local", "oidc", "platform"]
    source_provider: str | None
    created_at: datetime
    account_status: AccountStatus
    registration_requested_at: datetime | None
    registration_approved_at: datetime | None
    registration_approved_by: str | None
    approval_email_status: ApprovalEmailStatus | None
    approval_email_last_attempt_at: datetime | None
    allowed_actions: list[Literal["edit", "approve", "disable", "enable", "retry_approval_email"]]


class UserManagementListResponse(BaseModel):
    items: list[UserManagementItem]
    total: int
    page: int
    page_size: int


class UserEmailUpdateRequest(BaseModel):
    email: EmailStr


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
