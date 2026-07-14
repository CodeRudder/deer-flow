"""Administrator usage dashboard and quota-control endpoints."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.gateway.admin.periods import usage_range_window
from app.gateway.admin.quota_service import QuotaService, quota_period_window
from app.gateway.admin.schemas import (
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


class QuotaMetricUpdate(BaseModel):
    enabled: bool | None = None
    limit: int | None = Field(default=None, ge=0)


class QuotaUpdateRequest(BaseModel):
    period: Literal["this_month", "last_month", "custom"] = "this_month"
    period_start: str | None = None
    model_tokens: QuotaMetricUpdate | None = None
    model_requests: QuotaMetricUpdate | None = None
    image_generations: QuotaMetricUpdate | None = None


def _quota_service() -> QuotaService:
    sf = get_session_factory()
    if sf is None:
        raise HTTPException(status_code=503, detail="Database persistence is required for admin quotas")
    return QuotaService(sf, get_config().quota_control)


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


@router.get("/quotas/users", response_model=QuotaUsersResponse)
async def list_user_quotas(
    request: Request,
    period: Literal["this_month", "last_month", "custom"] = Query(default="this_month"),
    period_start: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    status: Literal["all", "normal", "warning", "exceeded", "unlimited"] = Query(default="all"),
    keyword: str | None = None,
) -> dict[str, Any]:
    await _require_admin(request)
    return await _quota_service().list_user_quotas(
        window=quota_period_window(period, period_start),
        page=page,
        page_size=page_size,
        keyword=keyword,
        status=status,
    )


@router.get("/quotas/users/{user_id}", response_model=QuotaUserResponse)
async def get_user_quota(
    user_id: str,
    request: Request,
    period: Literal["this_month", "last_month", "custom"] = Query(default="this_month"),
    period_start: str | None = None,
) -> dict[str, Any]:
    await _require_admin(request)
    return await _quota_service().get_user_quota(user_id, window=quota_period_window(period, period_start))


@router.put("/quotas/users/{user_id}", response_model=QuotaUserResponse)
async def update_user_quota(user_id: str, body: QuotaUpdateRequest, request: Request) -> dict[str, Any]:
    await _require_admin(request)
    user = getattr(request.state, "user", None)
    updated_by = str(getattr(user, "id", "")) or None
    payload = body.model_dump(exclude={"period", "period_start"}, exclude_unset=True)
    return await _quota_service().update_user_quota(
        user_id,
        payload=payload,
        window=quota_period_window(body.period, body.period_start),
        updated_by=updated_by,
    )
