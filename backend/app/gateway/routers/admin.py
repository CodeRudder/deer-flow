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
    UsageModelsResponse,
    UsageSessionsResponse,
    UsageSummaryResponse,
    UsageTrendsResponse,
)
from app.gateway.admin.usage_service import AdminUsageService
from app.gateway.deps import get_config, require_admin_user
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


async def _require_admin(request: Request) -> None:
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)


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
