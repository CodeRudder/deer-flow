"""用户侧额度查询端点（feat-df-8）。

只读：把管理端"额度管控"同一套聚合（QuotaService.get_user_quota）以本人视角
透出给普通用户。门控收口在服务端：只下发已启用 scope 中的生图/视频维度
（含"仅记录"态），管理员身份与内部字段经 Pydantic 白名单裁剪。
"""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.gateway.admin.quota_service import QuotaService
from deerflow.persistence.engine import get_session_factory
from deerflow.runtime.user_context import get_effective_user_id

router = APIRouter(prefix="/api/quotas", tags=["quotas"])

# 与管理端"额度管控"对齐：配置了的维度（模型组/生图/视频）都下发，含仅记录态
_DISPLAY_RESOURCE_TYPES = ("model", "image_generation", "video_generation")


class QuotaMetric(BaseModel):
    enforced: bool
    limit: float | None = None
    used: float
    reserved: float | None = None
    remaining: float | None = None
    ratio: float | None = None
    status: str
    unit: str


class QuotaScopeItem(BaseModel):
    scope_id: str
    scope_code: str
    scope_name: str
    resource_type: str
    period_type: str
    period: dict[str, str]
    status: str
    source: str
    requests: QuotaMetric | None = None
    images: QuotaMetric | None = None
    videos: QuotaMetric | None = None


class QuotaMeResponse(BaseModel):
    user_id: str
    status: str
    items: list[QuotaScopeItem]


def _metric_view(metric: dict[str, Any] | None, *, unit: str) -> QuotaMetric | None:
    if metric is None:
        return None
    return QuotaMetric(
        enforced=bool(metric.get("enforced")),
        limit=metric.get("limit"),
        used=float(metric.get("used") or 0.0),
        reserved=metric.get("reserved"),
        remaining=metric.get("remaining"),
        ratio=metric.get("ratio"),
        status=str(metric.get("status") or "unlimited"),
        unit=unit,
    )


def _slim_item(item: dict[str, Any]) -> QuotaScopeItem:
    scope = item.get("scope") or {}
    return QuotaScopeItem(
        scope_id=str(scope.get("id")),
        scope_code=str(scope.get("code") or ""),
        scope_name=str(scope.get("name") or scope.get("code") or ""),
        resource_type=str(scope.get("resource_type")),
        period_type=str(item.get("period_type") or ""),
        period=item.get("period") or {},
        status=str(item.get("status") or "unlimited"),
        source=str(item.get("source") or "scope_default"),
        requests=_metric_view(item.get("requests"), unit="count"),
        images=_metric_view(item.get("images"), unit="count"),
        videos=_metric_view(item.get("videos"), unit="points"),
    )


def _slim_quota_response(result: dict[str, Any]) -> QuotaMeResponse:
    items = [_slim_item(item) for item in result.get("items", []) if (item.get("scope") or {}).get("resource_type") in _DISPLAY_RESOURCE_TYPES]
    return QuotaMeResponse(
        user_id=str((result.get("user") or {}).get("user_id", "")),
        status=str(result.get("status") or "unlimited"),
        items=items,
    )


def _empty_quota_response(user_id: str) -> QuotaMeResponse:
    """无鉴权模式 default 用户可能无 users 行；返回与正常响应同构的空账本。"""
    return QuotaMeResponse(user_id=user_id, status="unlimited", items=[])


@router.get("/me", response_model=QuotaMeResponse)
async def get_my_quota() -> QuotaMeResponse:
    """当前登录用户的额度（配了的维度才返回，含仅记录态）。"""
    user_id = get_effective_user_id()
    sf = get_session_factory()
    if sf is None:
        raise HTTPException(status_code=503, detail="Database persistence is required for quotas")
    service = QuotaService(sf)
    try:
        result = await service.get_user_quota(user_id)
    except HTTPException as exc:
        if exc.status_code == 404 and isinstance(exc.detail, dict) and exc.detail.get("code") == "user_not_found":
            return _empty_quota_response(user_id)
        raise
    return _slim_quota_response(result)
