"""Administrator usage dashboard and quota-control endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.gateway.admin.periods import APP_TZ, usage_range_window
from app.gateway.admin.quota_service import QuotaService
from app.gateway.admin.schemas import (
    QuotaScopesResponse,
    QuotaUserResponse,
    QuotaUsersResponse,
    TraceEventsResponse,
    TraceRunsResponse,
    TraceUserOverviewResponse,
    TraceUsersResponse,
    UsageModelsResponse,
    UsageSessionsResponse,
    UsageSummaryResponse,
    UsageTrendsResponse,
    UsageUsersResponse,
)
from app.gateway.admin.session_trace_service import SessionTraceNotFoundError, SessionTraceService
from app.gateway.admin.usage_service import AdminUsageService
from app.gateway.deps import get_config, get_run_event_store, require_admin_user
from deerflow.persistence.engine import get_session_factory

router = APIRouter(prefix="/api/admin", tags=["admin"])
_ADMIN_REQUIRED_DETAIL = "Admin privileges required to access admin dashboard."


class QuotaMetricPolicy(BaseModel):
    enforced: bool
    limit: int | None = Field(default=None, ge=0)


class QuotaDefaultPolicy(BaseModel):
    period_type: Literal["weekly", "monthly"] = "weekly"
    requests: QuotaMetricPolicy | None = None
    images: QuotaMetricPolicy | None = None


class QuotaScopeCreateRequest(BaseModel):
    code: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    resource_type: Literal["model", "image_generation"] = "model"
    match_rules: dict[str, list[str]]
    default_policy: QuotaDefaultPolicy
    enabled: bool = True


class QuotaScopeUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    match_rules: dict[str, list[str]] = Field(default_factory=dict)
    default_policy: QuotaDefaultPolicy
    enabled: bool = True


class QuotaOverrideRequest(BaseModel):
    requests: QuotaMetricPolicy | None = None
    images: QuotaMetricPolicy | None = None
    reason: str | None = Field(default=None, max_length=512)


def _quota_service() -> QuotaService:
    sf = get_session_factory()
    if sf is None:
        raise HTTPException(status_code=503, detail="Database persistence is required for admin quotas")
    config = get_config()
    return QuotaService(sf, configured_models=config.models)


def _quota_reference_at(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_quota_period", "message": "at must be YYYY-MM-DD"},
        ) from exc
    if parsed.time() != datetime.min.time():
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_quota_period", "message": "at must be YYYY-MM-DD"},
        )
    return parsed.replace(tzinfo=APP_TZ).astimezone(UTC)


def _scope_payload(body: QuotaScopeCreateRequest | QuotaScopeUpdateRequest) -> dict[str, Any]:
    policy = body.default_policy
    requests = policy.requests
    images = policy.images
    payload = body.model_dump(exclude={"default_policy"})
    payload.update(
        period_type=policy.period_type,
        request_enforced=requests.enforced if requests else False,
        request_limit=requests.limit if requests else None,
        image_enforced=images.enforced if images else False,
        image_limit=images.limit if images else None,
    )
    return payload


def _usage_service() -> AdminUsageService:
    sf = get_session_factory()
    if sf is None:
        raise HTTPException(status_code=503, detail="Database persistence is required for admin usage statistics")
    return AdminUsageService(sf)


def _trace_service(request: Request) -> SessionTraceService:
    sf = get_session_factory()
    if sf is None:
        raise HTTPException(status_code=503, detail="Database persistence is required for session tracing")
    return SessionTraceService(sf, get_run_event_store(request))


async def _require_admin(request: Request) -> None:
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)


@router.get("/session-traces/users", response_model=TraceUsersResponse)
async def trace_users(
    request: Request,
    keyword: str | None = Query(default=None, max_length=320),
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = None,
) -> dict[str, Any]:
    await _require_admin(request)
    try:
        return await _trace_service(request).list_users(keyword=keyword, limit=limit, cursor=cursor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/session-traces/users/{user_id}/overview", response_model=TraceUserOverviewResponse)
async def trace_user_overview(user_id: str, request: Request) -> dict[str, Any]:
    await _require_admin(request)
    try:
        return await _trace_service(request).user_overview(user_id)
    except SessionTraceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/session-traces/runs", response_model=TraceRunsResponse)
async def trace_runs(
    request: Request,
    user_id: str | None = Query(default=None, max_length=64),
    thread_id: str | None = Query(default=None, max_length=64),
    run_id: str | None = Query(default=None, max_length=64),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=15, ge=1, le=100),
) -> dict[str, Any]:
    await _require_admin(request)
    return await _trace_service(request).list_runs(user_id=user_id, thread_id=thread_id, run_id=run_id, page=page, page_size=page_size)


@router.get("/session-traces/runs/{run_id}/events", response_model=TraceEventsResponse)
async def trace_run_events(
    run_id: str,
    request: Request,
    thread_id: str = Query(..., max_length=64),
    limit: int = Query(default=500, ge=1, le=500),
) -> dict[str, Any]:
    await _require_admin(request)
    try:
        return await _trace_service(request).run_events(run_id, thread_id=thread_id, limit=limit)
    except SessionTraceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/usage/summary", response_model=UsageSummaryResponse)
async def usage_summary(
    request: Request,
    range: Literal["day", "week", "month", "custom"] = Query(default="month"),
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    await _require_admin(request)
    return await _usage_service().usage_summary(window=usage_range_window(range, start=start, end=end))


@router.get("/usage/trends", response_model=UsageTrendsResponse)
async def usage_trends(
    request: Request,
    range: Literal["day", "week", "month", "custom"] = Query(default="month"),
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    await _require_admin(request)
    return await _usage_service().usage_trends(window=usage_range_window(range, start=start, end=end))


# Retained session-dimension endpoint. The overview UI now uses /usage/users.
@router.get("/usage/sessions", response_model=UsageSessionsResponse)
async def usage_sessions(
    request: Request,
    range: Literal["day", "week", "month", "custom"] = Query(default="month"),
    metric: Literal["tokens", "requests", "images"] = Query(default="tokens"),
    limit: int = Query(default=20, ge=1, le=50),
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    await _require_admin(request)
    return await _usage_service().usage_sessions(window=usage_range_window(range, start=start, end=end), metric=metric, limit=limit)


@router.get("/usage/users", response_model=UsageUsersResponse)
async def usage_users(
    request: Request,
    range: Literal["day", "week", "month", "custom"] = Query(default="month"),
    limit: int = Query(default=20, ge=1, le=50),
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    await _require_admin(request)
    return await _usage_service().usage_users(window=usage_range_window(range, start=start, end=end), limit=limit)


@router.get("/usage/models", response_model=UsageModelsResponse)
async def usage_models(
    request: Request,
    range: Literal["day", "week", "month", "custom"] = Query(default="month"),
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    await _require_admin(request)
    return await _usage_service().usage_models(window=usage_range_window(range, start=start, end=end))


@router.get("/quotas/scopes", response_model=QuotaScopesResponse)
async def list_quota_scopes(
    request: Request,
    resource_type: Literal["model", "image_generation"] | None = None,
    include_disabled: bool = False,
    keyword: str | None = Query(default=None, max_length=128),
) -> dict[str, Any]:
    await _require_admin(request)
    return await _quota_service().list_scopes(
        resource_type=resource_type,
        include_disabled=include_disabled,
        keyword=keyword,
    )


@router.post("/quotas/scopes", status_code=201)
async def create_quota_scope(body: QuotaScopeCreateRequest, request: Request) -> dict[str, Any]:
    await _require_admin(request)
    user = getattr(request.state, "user", None)
    return await _quota_service().create_scope(
        _scope_payload(body),
        updated_by=str(getattr(user, "id", "")) or None,
    )


@router.put("/quotas/scopes/{scope_id}")
async def update_quota_scope(scope_id: str, body: QuotaScopeUpdateRequest, request: Request) -> dict[str, Any]:
    await _require_admin(request)
    user = getattr(request.state, "user", None)
    return await _quota_service().update_scope(
        scope_id,
        _scope_payload(body),
        updated_by=str(getattr(user, "id", "")) or None,
    )


@router.get("/quotas/users", response_model=QuotaUsersResponse)
async def list_user_quotas(
    request: Request,
    at: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    status: Literal["all", "normal", "warning", "exceeded", "unlimited"] = Query(default="all"),
    keyword: str | None = Query(default=None, max_length=320),
) -> dict[str, Any]:
    await _require_admin(request)
    return await _quota_service().list_user_quotas(
        at=_quota_reference_at(at),
        page=page,
        page_size=page_size,
        keyword=keyword,
        status=status,
    )


@router.get("/quotas/users/{user_id}", response_model=QuotaUserResponse)
async def get_user_quota(user_id: str, request: Request, at: str | None = None) -> dict[str, Any]:
    await _require_admin(request)
    return await _quota_service().get_user_quota(user_id, at=_quota_reference_at(at))


@router.put("/quotas/users/{user_id}/scopes/{scope_id}/current-period")
async def override_user_quota(
    user_id: str,
    scope_id: str,
    body: QuotaOverrideRequest,
    request: Request,
) -> dict[str, Any]:
    await _require_admin(request)
    user = getattr(request.state, "user", None)
    return await _quota_service().override_user_current_period(
        user_id,
        scope_id,
        request_enforced=body.requests.enforced if body.requests else None,
        request_limit=body.requests.limit if body.requests else None,
        image_enforced=body.images.enforced if body.images else None,
        image_limit=body.images.limit if body.images else None,
        reason=body.reason,
        updated_by=str(getattr(user, "id", "")) or None,
    )


@router.delete("/quotas/users/{user_id}/scopes/{scope_id}/current-period/override")
async def restore_user_quota(user_id: str, scope_id: str, request: Request) -> dict[str, Any]:
    await _require_admin(request)
    user = getattr(request.state, "user", None)
    return await _quota_service().restore_user_current_period(
        user_id,
        scope_id,
        updated_by=str(getattr(user, "id", "")) or None,
    )


@router.get("/quotas/users/{user_id}/scopes/{scope_id}/periods")
async def list_user_quota_periods(
    user_id: str,
    scope_id: str,
    request: Request,
    start: str | None = None,
    end: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = None,
) -> dict[str, Any]:
    await _require_admin(request)
    cursor_at: datetime | None = None
    if cursor:
        try:
            cursor_at = datetime.fromisoformat(cursor)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_quota_period", "message": "Invalid history cursor"},
            ) from exc
    start_at = _quota_reference_at(start)
    end_at = _quota_reference_at(end)
    if start_at and end_at and end_at < start_at:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_quota_period", "message": "end must be on or after start"},
        )
    return await _quota_service().list_usage_periods(
        user_id,
        scope_id,
        limit=limit,
        start=start_at,
        end=end_at + timedelta(days=1) if end_at else None,
        cursor=cursor_at,
    )
