"""Model-group quota policies, per-user period usage, and runtime accounting."""

from __future__ import annotations

import asyncio
import copy
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import and_, case, func, literal, or_, select, union_all, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.gateway.admin.periods import APP_TZ, PeriodWindow
from deerflow.persistence.quota.model import QuotaScopeRow, UserQuotaUsagePeriodRow
from deerflow.persistence.user.model import UserRow

QuotaMetricName = Literal["model_requests", "image_generations", "video_generations"]
_SCOPE_CODE_PATTERN = re.compile(r"^[a-z0-9_]{2,64}$")
_STATUS_ORDER = {"unlimited": 0, "normal": 1, "warning": 2, "exceeded": 3}
# Video generation is billed exclusively in points.
_VIDEO_BILLING_MODE = "points"
_VIDEO_MINOR_UNIT_SCALE = 100


@dataclass(frozen=True)
class QuotaExceeded:
    metric: QuotaMetricName
    used: int
    limit: int
    period_type: str
    period_start: datetime
    period_end: datetime
    scope_id: str
    scope_code: str
    scope_name: str
    model: str | None = None
    reserved: int = 0
    unit: str = "count"
    scale: int = 1
    requested: int | None = None


class QuotaExceededError(Exception):
    def __init__(self, exceeded: QuotaExceeded) -> None:
        self.exceeded = exceeded
        super().__init__(f"{exceeded.metric} quota exceeded for {exceeded.scope_code}")


@dataclass(frozen=True)
class ModelQuotaReservation:
    reservation_id: str
    user_id: str
    model: str
    matched_scope_id: str | None
    usage_period_id: str | None
    request_reserved: bool
    request_used: int


@dataclass(frozen=True)
class ImageQuotaReservation:
    reservation_id: str
    user_id: str
    matched_scope_id: str | None
    usage_period_id: str | None
    count: int
    image_used: int


@dataclass(frozen=True)
class VideoPointsReservation:
    """A persisted video-points reservation for one generation request."""

    reservation_id: str
    record_id: str
    user_id: str
    model: str
    resolution: str
    duration_seconds: int
    matched_scope_id: str | None
    usage_period_id: str | None
    reserved_minor_units: int
    video_used: int
    billing_mode: str = "points"
    price_fen_per_second: int = 0
    idempotency_key: str = ""
    status: str = "reserved"
    reused: bool = False

    @property
    def estimated_minor_units(self) -> int:
        return self.reserved_minor_units


@dataclass(frozen=True)
class ModelScopeSnapshot:
    """Immutable model-scope policy captured for one Run."""

    id: str
    code: str
    name: str
    exact_models: tuple[str, ...]
    model_prefixes: tuple[str, ...]
    period_type: str
    request_enforced: bool
    request_limit: int | None
    policy_version: int
    image_enforced: bool = False
    image_limit: int | None = None
    video_enforced: bool = False
    video_limit: int | None = None


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _points_to_minor(value: Any) -> int:
    """Convert an API points value to integer RMB fen without float math."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid_video_points", "message": "视频积分必须是非负数字"}) from exc
    if not amount.is_finite() or amount < 0 or amount * _VIDEO_MINOR_UNIT_SCALE != (amount * _VIDEO_MINOR_UNIT_SCALE).to_integral_value():
        raise HTTPException(status_code=400, detail={"code": "invalid_video_points", "message": "视频积分最多保留两位小数"})
    return int(amount * _VIDEO_MINOR_UNIT_SCALE)


def _minor_to_points(value: int | None) -> float | None:
    if value is None:
        return None
    return float(Decimal(int(value)) / _VIDEO_MINOR_UNIT_SCALE)


def _video_rule_models(rules: Any) -> dict[str, Any]:
    if not isinstance(rules, dict):
        return {}
    models = rules.get("models")
    if models is None:
        # Accept a plain model map as a small convenience for admin JSON.
        models = {key: value for key, value in rules.items() if key not in {"schema_version", "currency", "point_to_yuan", "minor_unit_scale"}}
    return models if isinstance(models, dict) else {}


def _mapping_value(mapping: dict[str, Any], key: str) -> Any:
    if key in mapping:
        return mapping[key]
    lowered = key.lower()
    for candidate, value in mapping.items():
        if str(candidate).lower() == lowered:
            return value
    return None


def _price_to_minor(value: Any) -> int | None:
    if isinstance(value, dict):
        if "price_fen_per_second" in value:
            raw = value["price_fen_per_second"]
            try:
                amount = int(raw)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail={"code": "invalid_video_billing_rules", "message": "price_fen_per_second 必须是整数"}) from exc
            if amount <= 0:
                raise HTTPException(status_code=400, detail={"code": "invalid_video_billing_rules", "message": "视频费率必须大于 0"})
            return amount
        for key in ("price_yuan_per_second", "price_per_second", "price"):
            if key in value:
                return _positive_price_to_minor(value[key])
        return None
    return _positive_price_to_minor(value)


def _positive_price_to_minor(value: Any) -> int:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid_video_billing_rules", "message": "视频费率必须是数字"}) from exc
    if not amount.is_finite() or amount <= 0 or amount * _VIDEO_MINOR_UNIT_SCALE != (amount * _VIDEO_MINOR_UNIT_SCALE).to_integral_value():
        raise HTTPException(status_code=400, detail={"code": "invalid_video_billing_rules", "message": "视频费率必须大于 0 且最多保留两位小数"})
    return int(amount * _VIDEO_MINOR_UNIT_SCALE)


def _lookup_video_price(rules: Any, model: str, resolution: str, duration_seconds: int) -> int:
    models = _video_rule_models(rules)
    model_config = _mapping_value(models, model)
    if not isinstance(model_config, dict):
        raise HTTPException(status_code=400, detail={"code": "video_billing_rule_not_found", "message": f"未配置模型 {model} 的视频费率"})
    resolutions = model_config.get("resolutions", model_config)
    if not isinstance(resolutions, dict):
        raise HTTPException(status_code=400, detail={"code": "video_billing_rule_not_found", "message": f"未配置模型 {model} 的分辨率费率"})
    resolution_config = _mapping_value(resolutions, resolution)
    if resolution_config is None:
        raise HTTPException(status_code=400, detail={"code": "video_billing_rule_not_found", "message": f"未配置 {model}/{resolution} 的视频费率"})
    # A resolution may provide optional duration-specific entries. The common
    # case remains one RMB/second price for the whole supported duration range.
    if isinstance(resolution_config, dict) and isinstance(resolution_config.get("durations"), dict):
        duration_config = _mapping_value(resolution_config["durations"], str(duration_seconds))
        if duration_config is not None:
            resolution_config = duration_config
    price = _price_to_minor(resolution_config)
    if price is None:
        raise HTTPException(status_code=400, detail={"code": "video_billing_rule_not_found", "message": f"未配置 {model}/{resolution} 的每秒费率"})
    if isinstance(resolution_config, dict):
        minimum = resolution_config.get("min_duration", resolution_config.get("min_seconds", 4))
        maximum = resolution_config.get("max_duration", resolution_config.get("max_seconds"))
        if int(duration_seconds) < int(minimum) or (maximum is not None and int(duration_seconds) > int(maximum)):
            raise HTTPException(status_code=400, detail={"code": "video_duration_not_supported", "message": f"视频时长需在 {minimum} 秒至 {maximum or '无上限'} 秒之间"})
    return price


def _validate_video_billing_rules(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not _video_rule_models(value):
        raise HTTPException(status_code=400, detail={"code": "invalid_video_billing_rules", "message": "积分模式必须配置 models 费率 JSON"})
    if value.get("currency", "CNY") != "CNY" or value.get("point_to_yuan", 1) != 1:
        raise HTTPException(status_code=400, detail={"code": "invalid_video_billing_rules", "message": "视频积分规则必须使用 CNY 且 1 积分等于 1 元"})
    # Validate every configured rate once so malformed admin JSON fails early.
    for model_config in _video_rule_models(value).values():
        if not isinstance(model_config, dict):
            raise HTTPException(status_code=400, detail={"code": "invalid_video_billing_rules", "message": "models 配置格式无效"})
        resolutions = model_config.get("resolutions", model_config)
        if not isinstance(resolutions, dict) or not resolutions:
            raise HTTPException(status_code=400, detail={"code": "invalid_video_billing_rules", "message": "每个模型至少需要一个分辨率费率"})
        for resolution_config in resolutions.values():
            if isinstance(resolution_config, dict) and isinstance(resolution_config.get("durations"), dict):
                entries = resolution_config["durations"].values()
            else:
                entries = (resolution_config,)
            for entry in entries:
                if _price_to_minor(entry) is None:
                    raise HTTPException(status_code=400, detail={"code": "invalid_video_billing_rules", "message": "分辨率费率配置无效"})
    return value


def _resolution_rate_range(resolution_config: Any) -> tuple[float, float] | None:
    """分辨率费率区间（元/秒），兼容直接单价与按秒覆盖（durations 取区间）两种写法。"""
    if isinstance(resolution_config, dict) and isinstance(resolution_config.get("durations"), dict):
        minors = []
        for entry in resolution_config["durations"].values():
            try:
                minor = _price_to_minor(entry)
            except HTTPException:
                continue
            minors.append(minor)
        if not minors:
            return None
        return min(minors) / _VIDEO_MINOR_UNIT_SCALE, max(minors) / _VIDEO_MINOR_UNIT_SCALE
    try:
        minor = _price_to_minor(resolution_config)
    except HTTPException:
        return None
    if minor is None:
        return None
    value = minor / _VIDEO_MINOR_UNIT_SCALE
    return value, value


def _video_billing_summary(rules: Any) -> dict[str, Any]:
    """从费率规则构建模型价目摘要（key 为小写模型名），供用户侧价目展示。"""
    summary: dict[str, Any] = {}
    for model_name, model_config in _video_rule_models(rules).items():
        if not isinstance(model_config, dict):
            continue
        resolutions_map = model_config.get("resolutions", model_config)
        if not isinstance(resolutions_map, dict):
            continue
        resolutions = []
        min_seconds: int | None = None
        max_seconds: int | None = None
        for resolution, config in resolutions_map.items():
            rate_range = _resolution_rate_range(config)
            if rate_range is None:
                continue
            resolutions.append(
                {
                    "resolution": str(resolution),
                    "yuan_per_second_min": rate_range[0],
                    "yuan_per_second_max": rate_range[1],
                }
            )
            if isinstance(config, dict):
                low = config.get("min_duration", config.get("min_seconds"))
                high = config.get("max_duration", config.get("max_seconds"))
                if low is not None:
                    min_seconds = int(low) if min_seconds is None else min(min_seconds, int(low))
                if high is not None:
                    max_seconds = int(high) if max_seconds is None else max(max_seconds, int(high))
        if not resolutions:
            continue
        summary[str(model_name).lower()] = {
            "resolutions": resolutions,
            "min_duration_seconds": min_seconds,
            "max_duration_seconds": max_seconds,
        }
    return summary


def _video_metric(enforced: bool, limit_minor: int | None, used_minor: int, reserved_minor: int) -> dict[str, Any]:
    limit = _minor_to_points(limit_minor)
    used = _minor_to_points(used_minor) or 0.0
    reserved = _minor_to_points(reserved_minor) or 0.0
    effective = used + reserved
    if not enforced or limit is None:
        return {
            "enforced": bool(enforced),
            "limit": limit,
            "used": used,
            "reserved": reserved,
            "remaining": None if limit is None else max(0.0, limit - effective),
            "ratio": None,
            "status": "unlimited",
        }
    ratio = None if limit == 0 else effective / limit
    status = "exceeded" if effective >= limit else "warning" if ratio is not None and ratio >= 0.8 else "normal"
    return {
        "enforced": True,
        "limit": limit,
        "used": used,
        "reserved": reserved,
        "remaining": max(0.0, limit - effective),
        "ratio": ratio,
        "status": status,
    }


def quota_period_window(period_type: str, at: datetime | None = None) -> PeriodWindow:
    """Return a natural week/month window in Asia/Shanghai, stored as UTC."""
    instant = at or datetime.now(UTC)
    local = _as_utc(instant).astimezone(APP_TZ)
    if period_type == "weekly":
        local_start = datetime(local.year, local.month, local.day, tzinfo=APP_TZ) - timedelta(days=local.weekday())
        local_end = local_start + timedelta(days=7)
        label = f"{local_start.date().isoformat()}~{(local_end - timedelta(days=1)).date().isoformat()}"
    elif period_type == "monthly":
        local_start = datetime(local.year, local.month, 1, tzinfo=APP_TZ)
        if local.month == 12:
            local_end = datetime(local.year + 1, 1, 1, tzinfo=APP_TZ)
        else:
            local_end = datetime(local.year, local.month + 1, 1, tzinfo=APP_TZ)
        label = local_start.strftime("%Y-%m")
    else:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_quota_period", "message": "period_type must be weekly or monthly"},
        )
    return PeriodWindow(
        period=period_type,
        period_start=local_start.astimezone(UTC),
        period_end=local_end.astimezone(UTC),
        label=label,
    )


def quota_period_response(window: PeriodWindow) -> dict[str, str]:
    return {
        "period_type": window.period,
        "label": window.label,
        "period_start": window.period_start.isoformat(),
        "period_end": window.period_end.isoformat(),
        "timezone": "Asia/Shanghai",
    }


def _normalize_rules(value: Any) -> dict[str, list[str]]:
    rules = value if isinstance(value, dict) else {}
    normalized: dict[str, list[str]] = {}
    for key in ("exact", "prefix"):
        raw = rules.get(key, [])
        if not isinstance(raw, list):
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_scope_rules", "message": f"match_rules.{key} must be an array"},
            )
        values = sorted({str(item).strip() for item in raw if str(item).strip()})
        normalized[key] = values
    return normalized


def _rules_match(rules: dict[str, list[str]], model: str) -> bool:
    return model in rules.get("exact", []) or any(model.startswith(prefix) for prefix in rules.get("prefix", []))


def _snapshot_rules_match(scope: ModelScopeSnapshot, model: str) -> bool:
    return model in scope.exact_models or any(model.startswith(prefix) for prefix in scope.model_prefixes)


def _rules_overlap(left: dict[str, list[str]], right: dict[str, list[str]]) -> bool:
    candidates = set(left.get("exact", [])) | set(right.get("exact", []))
    if any(_rules_match(left, model) and _rules_match(right, model) for model in candidates):
        return True
    return any(a.startswith(b) or b.startswith(a) for a in left.get("prefix", []) for b in right.get("prefix", []))


def _metric(enforced: bool, limit: int | None, used: int) -> dict[str, Any]:
    if not enforced or limit is None:
        return {
            "enforced": bool(enforced),
            "limit": limit,
            "used": used,
            "remaining": None if limit is None else max(0, int(limit) - used),
            "ratio": None,
            "status": "unlimited",
        }
    ratio = None if limit == 0 else used / int(limit)
    status = "exceeded" if used >= int(limit) else "warning" if ratio is not None and ratio >= 0.8 else "normal"
    return {
        "enforced": True,
        "limit": int(limit),
        "used": used,
        "remaining": max(0, int(limit) - used),
        "ratio": ratio,
        "status": status,
    }


def _scope_status_rank_expression(scope: QuotaScopeRow):
    """Build the SQL status rank for one user/scope current-period row."""
    row_missing = UserQuotaUsagePeriodRow.id.is_(None)
    if scope.resource_type == "model":
        enforced = case(
            (row_missing, literal(bool(scope.request_enforced))),
            else_=UserQuotaUsagePeriodRow.request_enforced_snapshot,
        )
        limit = case(
            (row_missing, literal(scope.request_limit)),
            else_=UserQuotaUsagePeriodRow.request_limit_snapshot,
        )
        used = func.coalesce(UserQuotaUsagePeriodRow.request_used, 0)
    elif scope.resource_type == "video_generation":
        enforced = case(
            (row_missing, literal(bool(scope.video_enforced))),
            else_=UserQuotaUsagePeriodRow.video_enforced_snapshot,
        )
        limit = case(
            (row_missing, literal(scope.video_limit)),
            else_=UserQuotaUsagePeriodRow.video_limit_snapshot,
        )
        used = func.coalesce(UserQuotaUsagePeriodRow.video_used, 0)
        used = used + func.coalesce(UserQuotaUsagePeriodRow.video_reserved, 0)
    else:
        enforced = case(
            (row_missing, literal(bool(scope.image_enforced))),
            else_=UserQuotaUsagePeriodRow.image_enforced_snapshot,
        )
        limit = case(
            (row_missing, literal(scope.image_limit)),
            else_=UserQuotaUsagePeriodRow.image_limit_snapshot,
        )
        used = func.coalesce(UserQuotaUsagePeriodRow.image_used, 0)

    return case(
        (or_(enforced.is_(False), limit.is_(None)), _STATUS_ORDER["unlimited"]),
        (used >= limit, _STATUS_ORDER["exceeded"]),
        (and_(limit > 0, used * 10 >= limit * 8), _STATUS_ORDER["warning"]),
        else_=_STATUS_ORDER["normal"],
    )


class QuotaService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        configured_models: list[Any] | None = None,
    ) -> None:
        self._sf = session_factory
        self._models: list[dict[str, str]] = []
        self._model_scope_snapshots: tuple[ModelScopeSnapshot, ...] | None = None
        self._model_scope_cache: dict[str, ModelScopeSnapshot | None] = {}
        self._model_scope_cache_lock = asyncio.Lock()
        for item in configured_models or []:
            if isinstance(item, str):
                self._models.append({"name": item, "display_name": item, "model": item})
            else:
                model = str(getattr(item, "model", "") or "").strip()
                if model:
                    self._models.append(
                        {
                            "name": str(getattr(item, "name", model)),
                            "display_name": str(getattr(item, "display_name", model) or model),
                            "model": model,
                        }
                    )

    @staticmethod
    def _insert_for(session: AsyncSession, model: type, values: dict[str, Any] | list[dict[str, Any]], *, conflict: list[str]):
        insert = postgresql_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
        return insert(model).values(values).on_conflict_do_nothing(index_elements=conflict)

    async def _validate_scope_rules(
        self,
        session: AsyncSession,
        rules: dict[str, list[str]],
        *,
        enabled: bool,
        exclude_scope_id: str | None = None,
    ) -> None:
        if not rules["exact"] and not rules["prefix"]:
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_scope_rules", "message": "exact and prefix cannot both be empty"},
            )
        configured = {item["model"] for item in self._models}
        missing = [model for model in rules["exact"] if configured and model not in configured]
        if missing:
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_scope_rules", "message": f"Unknown configured model: {missing[0]}"},
            )
        if not enabled:
            return
        stmt = select(QuotaScopeRow).where(
            QuotaScopeRow.resource_type == "model",
            QuotaScopeRow.enabled.is_(True),
        )
        if exclude_scope_id:
            stmt = stmt.where(QuotaScopeRow.id != exclude_scope_id)
        conflicts = []
        for scope in (await session.execute(stmt)).scalars():
            other = _normalize_rules(scope.match_rules)
            if _rules_overlap(rules, other):
                conflicts.append({"scope_id": scope.id, "scope_name": scope.name})
        if conflicts:
            raise HTTPException(
                status_code=409,
                detail={"code": "quota_scope_conflict", "message": "模型组与现有模型组冲突", "conflicts": conflicts},
            )

    async def create_scope(self, payload: dict[str, Any], *, updated_by: str | None) -> dict[str, Any]:
        code = str(payload.get("code") or "").strip()
        if not _SCOPE_CODE_PATTERN.fullmatch(code):
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_scope_rules", "message": "code must use lowercase letters, digits, and underscores"},
            )
        resource_type = str(payload.get("resource_type") or "model")
        if resource_type not in {"model", "image_generation", "video_generation"}:
            raise HTTPException(status_code=400, detail={"code": "invalid_scope_rules", "message": "Invalid resource type"})
        if resource_type in {"image_generation", "video_generation"} and code != resource_type:
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_scope_rules", "message": f"{resource_type} scope code must be {resource_type}"},
            )
        rules = _normalize_rules(payload.get("match_rules")) if resource_type == "model" else {"exact": [], "prefix": []}
        enabled = bool(payload.get("enabled", True))
        video_billing_rules: dict[str, Any] = {}
        video_enforced = False
        video_limit = payload.get("video_limit") if resource_type == "video_generation" else None
        if resource_type == "video_generation":
            video_enforced = bool(payload.get("video_enforced", False))
            raw_video_rules = payload.get("video_billing_rules")
            # An initially disabled scope may be created before its rate JSON
            # is entered; generation remains blocked until rates are present.
            video_billing_rules = {} if not raw_video_rules and not video_enforced else _validate_video_billing_rules(raw_video_rules)
            video_limit = _points_to_minor(video_limit) if video_limit is not None else None
        async with self._sf() as session:
            if (await session.execute(select(QuotaScopeRow.id).where(QuotaScopeRow.code == code))).scalar_one_or_none():
                raise HTTPException(status_code=409, detail={"code": "quota_scope_code_exists", "message": "Scope code already exists"})
            if resource_type in {"image_generation", "video_generation"}:
                existing_scope = (await session.execute(select(QuotaScopeRow.id).where(QuotaScopeRow.resource_type == resource_type))).scalar_one_or_none()
                if existing_scope is not None:
                    raise HTTPException(
                        status_code=409,
                        detail={"code": "quota_scope_code_exists", "message": f"{resource_type} scope already exists"},
                    )
            else:
                await self._validate_scope_rules(session, rules, enabled=enabled)
            now = datetime.now(UTC)
            scope = QuotaScopeRow(
                id=str(uuid.uuid4()),
                code=code,
                name=str(payload.get("name") or code).strip(),
                resource_type=resource_type,
                match_rules=rules,
                enabled=enabled,
                is_system=resource_type in {"image_generation", "video_generation"},
                period_type=str(payload.get("period_type") or "weekly"),
                request_enforced=bool(payload.get("request_enforced", False)) if resource_type == "model" else False,
                request_limit=payload.get("request_limit") if resource_type == "model" else None,
                image_enforced=bool(payload.get("image_enforced", False)) if resource_type == "image_generation" else False,
                image_limit=payload.get("image_limit") if resource_type == "image_generation" else None,
                video_enforced=video_enforced if resource_type == "video_generation" else False,
                video_limit=video_limit,
                video_billing_rules=video_billing_rules,
                policy_version=1,
                created_at=now,
                updated_at=now,
                updated_by=updated_by,
            )
            session.add(scope)
            await session.commit()
            await session.refresh(scope)
            return await self._scope_response(session, scope)

    async def update_scope(self, scope_id: str, payload: dict[str, Any], *, updated_by: str | None) -> dict[str, Any]:
        async with self._sf() as session:
            scope = await session.get(QuotaScopeRow, scope_id)
            if scope is None:
                raise HTTPException(status_code=404, detail={"code": "quota_scope_not_found", "message": "Quota scope not found"})
            old_period_type = scope.period_type
            if scope.is_system:
                name = str(payload.get("name") or scope.name).strip()
                enabled = bool(payload.get("enabled", True))
                period_type = str(payload.get("period_type") or scope.period_type)
                definition_changed = (scope.name, scope.enabled) != (name, enabled)
                if scope.resource_type == "video_generation":
                    video_enforced = bool(payload.get("video_enforced", scope.video_enforced))
                    raw_video_rules = payload.get("video_billing_rules", getattr(scope, "video_billing_rules", {}))
                    video_billing_rules = {} if not raw_video_rules and not video_enforced else _validate_video_billing_rules(raw_video_rules)
                    raw_video_limit = payload.get("video_limit", _minor_to_points(scope.video_limit))
                    video_limit = _points_to_minor(raw_video_limit) if raw_video_limit is not None else None
                    policy_changed = (
                        scope.period_type,
                        scope.video_enforced,
                        scope.video_limit,
                        getattr(scope, "video_billing_rules", {}),
                    ) != (
                        period_type,
                        video_enforced,
                        video_limit,
                        video_billing_rules,
                    )
                    scope.video_enforced = video_enforced
                    scope.video_limit = video_limit
                    scope.video_billing_rules = video_billing_rules
                else:
                    image_enforced = bool(payload.get("image_enforced", scope.image_enforced))
                    image_limit = payload.get("image_limit", scope.image_limit)
                    policy_changed = (scope.period_type, scope.image_enforced, scope.image_limit) != (
                        period_type,
                        image_enforced,
                        image_limit,
                    )
                    scope.image_enforced = image_enforced
                    scope.image_limit = image_limit
                scope.name = name
                scope.enabled = enabled
                scope.period_type = period_type
            else:
                rules = _normalize_rules(payload.get("match_rules"))
                enabled = bool(payload.get("enabled", True))
                await self._validate_scope_rules(session, rules, enabled=enabled, exclude_scope_id=scope.id)
                name = str(payload.get("name") or scope.name).strip()
                period_type = str(payload.get("period_type") or "weekly")
                request_enforced = bool(payload.get("request_enforced", False))
                request_limit = payload.get("request_limit")
                definition_changed = (scope.name, _normalize_rules(scope.match_rules), scope.enabled) != (
                    name,
                    rules,
                    enabled,
                )
                policy_changed = (scope.period_type, scope.request_enforced, scope.request_limit) != (
                    period_type,
                    request_enforced,
                    request_limit,
                )
                scope.name = name
                scope.match_rules = rules
                scope.enabled = enabled
                scope.period_type = period_type
                scope.request_enforced = request_enforced
                scope.request_limit = request_limit
            if scope.period_type not in {"weekly", "monthly"}:
                raise HTTPException(status_code=400, detail={"code": "invalid_quota_period", "message": "Invalid period type"})
            if not definition_changed and not policy_changed:
                return await self._scope_response(session, scope)

            if policy_changed:
                scope.policy_version += 1
            scope.updated_at = datetime.now(UTC)
            scope.updated_by = updated_by
            await session.flush()
            if policy_changed and old_period_type == scope.period_type:
                window = quota_period_window(scope.period_type)
                values: dict[str, Any] = {
                    "scope_policy_version_snapshot": scope.policy_version,
                    "updated_at": datetime.now(UTC),
                    "updated_by": updated_by,
                }
                if scope.resource_type == "model":
                    values.update(
                        request_enforced_snapshot=scope.request_enforced,
                        request_limit_snapshot=scope.request_limit,
                    )
                elif scope.resource_type == "video_generation":
                    values.update(
                        video_enforced_snapshot=scope.video_enforced,
                        video_limit_snapshot=scope.video_limit,
                    )
                else:
                    values.update(
                        image_enforced_snapshot=scope.image_enforced,
                        image_limit_snapshot=scope.image_limit,
                    )
                await session.execute(
                    update(UserQuotaUsagePeriodRow)
                    .where(
                        UserQuotaUsagePeriodRow.quota_scope_id == scope.id,
                        UserQuotaUsagePeriodRow.period_start == window.period_start,
                        UserQuotaUsagePeriodRow.is_overridden.is_(False),
                    )
                    .values(**values)
                )
            await session.commit()
            await session.refresh(scope)
            return await self._scope_response(session, scope)

    async def list_scopes(
        self,
        *,
        resource_type: str | None = None,
        include_disabled: bool = False,
        keyword: str | None = None,
    ) -> dict[str, Any]:
        async with self._sf() as session:
            stmt = select(QuotaScopeRow)
            if resource_type:
                stmt = stmt.where(QuotaScopeRow.resource_type == resource_type)
            if not include_disabled:
                stmt = stmt.where(QuotaScopeRow.enabled.is_(True))
            if keyword:
                like = f"%{keyword.strip().lower()}%"
                stmt = stmt.where(or_(func.lower(QuotaScopeRow.name).like(like), func.lower(QuotaScopeRow.code).like(like)))
            scopes = list((await session.execute(stmt.order_by(QuotaScopeRow.is_system, QuotaScopeRow.created_at))).scalars())
            items = [await self._scope_response(session, scope) for scope in scopes]
            enabled_model_scopes = list(
                (
                    await session.execute(
                        select(QuotaScopeRow).where(
                            QuotaScopeRow.enabled.is_(True),
                            QuotaScopeRow.resource_type == "model",
                        )
                    )
                ).scalars()
            )
            unmatched = [item for item in self._models if not any(_rules_match(_normalize_rules(scope.match_rules), item["model"]) for scope in enabled_model_scopes)]
            duplicate_ids = sorted({item["model"] for item in self._models if sum(candidate["model"] == item["model"] for candidate in self._models) > 1})
            return {
                "items": items,
                "unmatched_models": unmatched,
                "configuration_warnings": [{"code": "duplicate_model_identifier", "model": model} for model in duplicate_ids],
            }

    async def _scope_response(self, session: AsyncSession, scope: QuotaScopeRow) -> dict[str, Any]:
        rules = _normalize_rules(scope.match_rules) if scope.resource_type == "model" else {"exact": [], "prefix": []}
        current_window = quota_period_window(scope.period_type)
        user_count = int(
            (
                await session.execute(
                    select(func.count(UserQuotaUsagePeriodRow.id)).where(
                        UserQuotaUsagePeriodRow.quota_scope_id == scope.id,
                        UserQuotaUsagePeriodRow.period_start == current_window.period_start,
                    )
                )
            ).scalar_one()
            or 0
        )
        return {
            "id": scope.id,
            "code": scope.code,
            "name": scope.name,
            "resource_type": scope.resource_type,
            "match_rules": rules,
            "matched_models": [item for item in self._models if _rules_match(rules, item["model"])],
            "enabled": bool(scope.enabled),
            "is_system": bool(scope.is_system),
            "default_policy": {
                "period_type": scope.period_type,
                "requests": ({"enforced": bool(scope.request_enforced), "limit": scope.request_limit} if scope.resource_type == "model" else None),
                "images": ({"enforced": bool(scope.image_enforced), "limit": scope.image_limit} if scope.resource_type == "image_generation" else None),
                "videos": (
                    {
                        "enforced": bool(scope.video_enforced),
                        "limit": _minor_to_points(scope.video_limit),
                        "billing_mode": _VIDEO_BILLING_MODE,
                        "unit": "points",
                        "scale": _VIDEO_MINOR_UNIT_SCALE,
                        "billing_rules": getattr(scope, "video_billing_rules", {}) or {},
                    }
                    if scope.resource_type == "video_generation"
                    else None
                ),
                "policy_version": scope.policy_version,
            },
            # Keep these aliases for clients that do not unpack default_policy.
            "video_billing_mode": _VIDEO_BILLING_MODE,
            "video_billing_rules": getattr(scope, "video_billing_rules", {}) or {},
            "current_period_user_count": user_count,
            "updated_at": _as_utc(scope.updated_at).isoformat(),
            "updated_by": scope.updated_by,
        }

    async def _find_model_scope(self, session: AsyncSession, model: str) -> ModelScopeSnapshot | None:
        if model in self._model_scope_cache:
            return self._model_scope_cache[model]

        async with self._model_scope_cache_lock:
            if model in self._model_scope_cache:
                return self._model_scope_cache[model]
            if self._model_scope_snapshots is None:
                scopes = list(
                    (
                        await session.execute(
                            select(QuotaScopeRow).where(
                                QuotaScopeRow.resource_type == "model",
                                QuotaScopeRow.enabled.is_(True),
                            )
                        )
                    ).scalars()
                )
                snapshots = []
                for scope in scopes:
                    rules = _normalize_rules(scope.match_rules)
                    snapshots.append(
                        ModelScopeSnapshot(
                            id=scope.id,
                            code=scope.code,
                            name=scope.name,
                            exact_models=tuple(rules["exact"]),
                            model_prefixes=tuple(rules["prefix"]),
                            period_type=scope.period_type,
                            request_enforced=bool(scope.request_enforced),
                            request_limit=scope.request_limit,
                            policy_version=scope.policy_version,
                        )
                    )
                self._model_scope_snapshots = tuple(snapshots)
            matched = next(
                (scope for scope in self._model_scope_snapshots if _snapshot_rules_match(scope, model)),
                None,
            )
            self._model_scope_cache[model] = matched
            return matched

    async def _ensure_usage_period(
        self,
        session: AsyncSession,
        user_id: str,
        scope: QuotaScopeRow | ModelScopeSnapshot,
        *,
        at: datetime | None = None,
        updated_by: str | None = None,
    ) -> UserQuotaUsagePeriodRow:
        window = quota_period_window(scope.period_type, at)
        lookup = select(UserQuotaUsagePeriodRow).where(
            UserQuotaUsagePeriodRow.user_id == user_id,
            UserQuotaUsagePeriodRow.quota_scope_id == scope.id,
            UserQuotaUsagePeriodRow.period_type == scope.period_type,
            UserQuotaUsagePeriodRow.period_start == window.period_start,
        )
        existing = (await session.execute(lookup)).scalar_one_or_none()
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        values = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "quota_scope_id": scope.id,
            "period_type": scope.period_type,
            "period_start": window.period_start,
            "period_end": window.period_end,
            "scope_policy_version_snapshot": scope.policy_version,
            "token_used": 0,
            "request_enforced_snapshot": scope.request_enforced,
            "request_limit_snapshot": scope.request_limit,
            "request_used": 0,
            "image_enforced_snapshot": scope.image_enforced,
            "image_limit_snapshot": scope.image_limit,
            "image_used": 0,
            "video_enforced_snapshot": scope.video_enforced,
            "video_limit_snapshot": scope.video_limit,
            "video_used": 0,
            "video_reserved": 0,
            "video_billing_records": {},
            "is_overridden": False,
            "created_at": now,
            "updated_at": now,
            "updated_by": updated_by,
        }
        await session.execute(
            self._insert_for(
                session,
                UserQuotaUsagePeriodRow,
                values,
                conflict=["user_id", "quota_scope_id", "period_type", "period_start"],
            )
        )
        return (await session.execute(lookup)).scalar_one()

    async def reserve_model_request(
        self,
        user_id: str,
        model: str,
        *,
        at: datetime | None = None,
    ) -> ModelQuotaReservation:
        async with self._sf() as session:
            scope = await self._find_model_scope(session, model)
            if scope is None:
                return ModelQuotaReservation(str(uuid.uuid4()), user_id, model, None, None, False, 0)
            row = await self._ensure_usage_period(session, user_id, scope, at=at)
            result = await session.execute(
                update(UserQuotaUsagePeriodRow)
                .where(
                    UserQuotaUsagePeriodRow.id == row.id,
                    or_(
                        UserQuotaUsagePeriodRow.request_enforced_snapshot.is_(False),
                        UserQuotaUsagePeriodRow.request_limit_snapshot.is_(None),
                        UserQuotaUsagePeriodRow.request_used + 1 <= UserQuotaUsagePeriodRow.request_limit_snapshot,
                    ),
                )
                .values(
                    request_used=UserQuotaUsagePeriodRow.request_used + 1,
                    updated_at=datetime.now(UTC),
                )
                .returning(UserQuotaUsagePeriodRow.request_used)
            )
            used = result.scalar_one_or_none()
            if used is None:
                await session.refresh(row)
                raise QuotaExceededError(self._exceeded(scope, row, "model_requests", model=model))
            await session.commit()
            return ModelQuotaReservation(str(uuid.uuid4()), user_id, model, scope.id, row.id, True, int(used))

    async def record_model_tokens(self, reservation: ModelQuotaReservation, total_tokens: int) -> None:
        amount = max(0, int(total_tokens))
        if not reservation.usage_period_id or amount == 0:
            return
        async with self._sf() as session:
            await session.execute(
                update(UserQuotaUsagePeriodRow)
                .where(UserQuotaUsagePeriodRow.id == reservation.usage_period_id)
                .values(
                    token_used=UserQuotaUsagePeriodRow.token_used + amount,
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()

    async def release_undispatched_model_request(self, reservation: ModelQuotaReservation) -> None:
        if not reservation.request_reserved or not reservation.usage_period_id:
            return
        async with self._sf() as session:
            await session.execute(
                update(UserQuotaUsagePeriodRow)
                .where(UserQuotaUsagePeriodRow.id == reservation.usage_period_id)
                .values(
                    request_used=case(
                        (UserQuotaUsagePeriodRow.request_used > 0, UserQuotaUsagePeriodRow.request_used - 1),
                        else_=0,
                    ),
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()

    async def reserve_image_generations(
        self,
        user_id: str,
        *,
        count: int = 1,
        at: datetime | None = None,
    ) -> ImageQuotaReservation:
        count = int(count)
        if count <= 0:
            return ImageQuotaReservation(str(uuid.uuid4()), user_id, None, None, 0, 0)
        async with self._sf() as session:
            scope = (
                await session.execute(
                    select(QuotaScopeRow).where(
                        QuotaScopeRow.resource_type == "image_generation",
                        QuotaScopeRow.enabled.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if scope is None:
                return ImageQuotaReservation(str(uuid.uuid4()), user_id, None, None, 0, 0)
            row = await self._ensure_usage_period(session, user_id, scope, at=at)
            result = await session.execute(
                update(UserQuotaUsagePeriodRow)
                .where(
                    UserQuotaUsagePeriodRow.id == row.id,
                    or_(
                        UserQuotaUsagePeriodRow.image_enforced_snapshot.is_(False),
                        UserQuotaUsagePeriodRow.image_limit_snapshot.is_(None),
                        UserQuotaUsagePeriodRow.image_used + count <= UserQuotaUsagePeriodRow.image_limit_snapshot,
                    ),
                )
                .values(
                    image_used=UserQuotaUsagePeriodRow.image_used + count,
                    updated_at=datetime.now(UTC),
                )
                .returning(UserQuotaUsagePeriodRow.image_used)
            )
            used = result.scalar_one_or_none()
            if used is None:
                await session.refresh(row)
                raise QuotaExceededError(self._exceeded(scope, row, "image_generations"))
            await session.commit()
            return ImageQuotaReservation(str(uuid.uuid4()), user_id, scope.id, row.id, count, int(used))

    async def release_image_generations(self, reservation: ImageQuotaReservation) -> None:
        if not reservation.usage_period_id or reservation.count <= 0:
            return
        async with self._sf() as session:
            await session.execute(
                update(UserQuotaUsagePeriodRow)
                .where(UserQuotaUsagePeriodRow.id == reservation.usage_period_id)
                .values(
                    image_used=case(
                        (UserQuotaUsagePeriodRow.image_used >= reservation.count, UserQuotaUsagePeriodRow.image_used - reservation.count),
                        else_=0,
                    ),
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()

    async def reserve_video_generation(
        self,
        user_id: str,
        *,
        model: str | None = None,
        resolution: str | None = None,
        duration_seconds: int | None = None,
        idempotency_key: str | None = None,
        provider: str | None = None,
        run_id: str | None = None,
        thread_id: str | None = None,
        output_file: str | None = None,
        at: datetime | None = None,
    ) -> VideoPointsReservation:
        """Reserve one point-billed generation."""
        return await self.reserve_video_points(
            user_id,
            model=model or "",
            resolution=resolution or "",
            duration_seconds=duration_seconds,
            idempotency_key=idempotency_key,
            provider=provider,
            run_id=run_id,
            thread_id=thread_id,
            output_file=output_file,
            at=at,
        )

    @staticmethod
    def _video_records(row: UserQuotaUsagePeriodRow) -> dict[str, Any]:
        """Return a mutable, normalized container for period billing records."""
        raw = row.video_billing_records
        if isinstance(raw, dict) and isinstance(raw.get("items"), dict):
            return copy.deepcopy(raw)
        # Accept the initial flat-map shape so an early deployment can be
        # upgraded without losing records.
        return {"schema_version": 1, "items": copy.deepcopy(raw) if isinstance(raw, dict) else {}}

    @staticmethod
    def _video_record_for(
        records: dict[str, Any],
        *,
        reservation_id: str | None = None,
        record_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[str, dict[str, Any]] | None:
        items = records.get("items") if isinstance(records.get("items"), dict) else {}
        for key, value in items.items():
            if not isinstance(value, dict):
                continue
            if reservation_id and value.get("reservation_id") == reservation_id:
                return str(key), value
            if record_id and (value.get("record_id") == record_id or str(key) == record_id):
                return str(key), value
            if idempotency_key and value.get("idempotency_key") == idempotency_key:
                return str(key), value
        return None

    @staticmethod
    def _video_reservation_from_record(
        record: dict[str, Any],
        *,
        user_id: str,
        scope_id: str | None,
        usage_period_id: str | None,
        video_used: int = 0,
    ) -> VideoPointsReservation:
        return VideoPointsReservation(
            reservation_id=str(record.get("reservation_id") or record.get("record_id") or ""),
            record_id=str(record.get("record_id") or record.get("reservation_id") or ""),
            user_id=user_id,
            model=str(record.get("model") or ""),
            resolution=str(record.get("resolution") or ""),
            duration_seconds=int(record.get("requested_duration_seconds") or 0),
            matched_scope_id=scope_id,
            usage_period_id=usage_period_id,
            reserved_minor_units=int(record.get("reserved_minor_units") or 0),
            video_used=video_used,
            billing_mode="points",
            price_fen_per_second=int(record.get("price_fen_per_second") or 0),
            idempotency_key=str(record.get("idempotency_key") or ""),
            status=str(record.get("status") or "reserved"),
            reused=True,
        )

    async def reserve_video_points(
        self,
        user_id: str,
        *,
        model: str,
        resolution: str,
        duration_seconds: int | None = None,
        duration: int | None = None,
        idempotency_key: str | None = None,
        provider: str | None = None,
        run_id: str | None = None,
        thread_id: str | None = None,
        output_file: str | None = None,
        at: datetime | None = None,
    ) -> VideoPointsReservation:
        """按模型、分辨率和时长预占视频积分（内部单位为人民币分）。"""
        seconds = duration_seconds if duration_seconds is not None else duration
        try:
            seconds = int(seconds) if seconds is not None else 0
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail={"code": "invalid_video_duration", "message": "视频时长必须是整数秒"}) from exc
        if seconds < 4:
            raise HTTPException(status_code=400, detail={"code": "invalid_video_duration", "message": "视频最短时长为 4 秒"})
        model = str(model or "").strip()
        resolution = str(resolution or "").strip()
        if not model or not resolution:
            raise HTTPException(status_code=400, detail={"code": "invalid_video_billing_request", "message": "model、resolution 和 duration 为必填项"})
        async with self._sf() as session:
            scope = (
                await session.execute(
                    select(QuotaScopeRow).where(
                        QuotaScopeRow.resource_type == "video_generation",
                        QuotaScopeRow.enabled.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if scope is None:
                raise HTTPException(status_code=409, detail={"code": "video_billing_unavailable", "message": "视频积分额度尚未配置"})
            price = _lookup_video_price(scope.video_billing_rules, model, resolution, seconds)
            amount = price * seconds
            row = await self._ensure_usage_period(session, user_id, scope, at=at)
            # Production uses PostgreSQL; serialize reserve/settle transitions
            # on the current aggregate row.
            row = (await session.execute(select(UserQuotaUsagePeriodRow).where(UserQuotaUsagePeriodRow.id == row.id).with_for_update())).scalar_one()
            records = self._video_records(row)
            key = idempotency_key or str(uuid.uuid4())
            existing = self._video_record_for(records, idempotency_key=key)
            if existing is not None:
                _, old_record = existing
                if str(old_record.get("model")) != model or str(old_record.get("resolution")) != resolution or int(old_record.get("requested_duration_seconds") or 0) != seconds:
                    raise HTTPException(status_code=409, detail={"code": "video_billing_idempotency_conflict", "message": "重复请求的计费参数不一致"})
                return self._video_reservation_from_record(
                    old_record,
                    user_id=user_id,
                    scope_id=scope.id,
                    usage_period_id=row.id,
                    video_used=int(row.video_used or 0),
                )
            current_used = int(row.video_used or 0)
            current_reserved = int(row.video_reserved or 0)
            limit = row.video_limit_snapshot
            if row.video_enforced_snapshot and limit is not None and current_used + current_reserved + amount > int(limit):
                raise QuotaExceededError(
                    self._exceeded(
                        scope,
                        row,
                        "video_generations",
                        model=model,
                        requested=amount,
                        reserved=current_reserved,
                        unit="points",
                        scale=_VIDEO_MINOR_UNIT_SCALE,
                    )
                )
            reservation_id = str(uuid.uuid4())
            record_id = str(uuid.uuid4())
            now = datetime.now(UTC).isoformat()
            record = {
                "record_id": record_id,
                "record_type": "generation",
                "reservation_id": reservation_id,
                "idempotency_key": key,
                "usage_period_id": row.id,
                "status": "reserved",
                "user_id": user_id,
                "run_id": run_id,
                "thread_id": thread_id,
                "output_file": output_file,
                "provider": provider,
                "model": model,
                "resolution": resolution,
                "requested_duration_seconds": seconds,
                "billable_duration_seconds": None,
                "price_fen_per_second": price,
                "policy_version_snapshot": scope.policy_version,
                "reserved_minor_units": amount,
                "settled_minor_units": 0,
                "provider_task_id": None,
                "created_at": now,
                "updated_at": now,
            }
            records["items"][record_id] = record
            row.video_reserved = current_reserved + amount
            row.video_billing_records = records
            row.updated_at = datetime.now(UTC)
            await session.commit()
            return VideoPointsReservation(
                reservation_id=reservation_id,
                record_id=record_id,
                user_id=user_id,
                model=model,
                resolution=resolution,
                duration_seconds=seconds,
                matched_scope_id=scope.id,
                usage_period_id=row.id,
                reserved_minor_units=amount,
                video_used=current_used,
                billing_mode="points",
                price_fen_per_second=price,
                idempotency_key=key,
                status="reserved",
            )

    async def settle_video_points(
        self,
        reservation: VideoPointsReservation | str,
        *,
        usage_period_id: str | None = None,
        provider_task_id: str | None = None,
        billable_duration_seconds: int | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """将预占转为已用；重复结算直接返回已有终态。"""
        if isinstance(reservation, VideoPointsReservation):
            reservation_id = reservation.reservation_id
            usage_period_id = usage_period_id or reservation.usage_period_id
            user_id = user_id or reservation.user_id
        else:
            reservation_id = str(reservation)
        if not usage_period_id:
            raise HTTPException(status_code=400, detail={"code": "video_billing_reservation_invalid", "message": "缺少 usage_period_id"})
        async with self._sf() as session:
            row = (await session.execute(select(UserQuotaUsagePeriodRow).where(UserQuotaUsagePeriodRow.id == usage_period_id).with_for_update())).scalar_one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail={"code": "video_billing_reservation_not_found", "message": "视频积分预占不存在"})
            records = self._video_records(row)
            found = self._video_record_for(records, reservation_id=reservation_id, record_id=reservation_id)
            if found is None:
                raise HTTPException(status_code=404, detail={"code": "video_billing_reservation_not_found", "message": "视频积分预占不存在"})
            key, record = found
            if user_id and record.get("user_id") not in (None, user_id):
                raise HTTPException(status_code=403, detail={"code": "video_billing_reservation_forbidden", "message": "无权操作该视频积分预占"})
            if record.get("status") in {"settled", "released"}:
                return {"transitioned": False, "record": record}
            if record.get("status") not in {"reserved", "pending"}:
                raise HTTPException(status_code=409, detail={"code": "video_billing_state_conflict", "message": "视频积分状态不可结算"})
            requested_seconds = int(record.get("requested_duration_seconds") or 0)
            if billable_duration_seconds is None:
                seconds = requested_seconds
            else:
                try:
                    supplied_seconds = int(billable_duration_seconds)
                except (TypeError, ValueError) as exc:
                    raise HTTPException(status_code=400, detail={"code": "invalid_video_duration", "message": "视频计费时长必须是整数秒"}) from exc
                # This release bills the explicit duration sent to Provider;
                # do not let an untrusted sidecar arbitrarily change charge.
                seconds = requested_seconds if supplied_seconds != requested_seconds else supplied_seconds
            if seconds < 4:
                raise HTTPException(status_code=400, detail={"code": "invalid_video_duration", "message": "视频时长必须至少为 4 秒"})
            amount = int(record.get("price_fen_per_second") or 0) * int(seconds)
            reserved = int(record.get("reserved_minor_units") or 0)
            row.video_reserved = max(0, int(row.video_reserved or 0) - reserved)
            row.video_used = int(row.video_used or 0) + amount
            record.update(
                status="settled",
                billable_duration_seconds=int(seconds),
                settled_minor_units=amount,
                provider_task_id=provider_task_id or record.get("provider_task_id"),
                updated_at=datetime.now(UTC).isoformat(),
            )
            records["items"][key] = record
            row.video_billing_records = records
            row.updated_at = datetime.now(UTC)
            await session.commit()
            return {"transitioned": True, "record": record, "video_used": int(row.video_used), "video_reserved": int(row.video_reserved)}

    async def release_video_points(
        self,
        reservation: VideoPointsReservation | str,
        *,
        usage_period_id: str | None = None,
        reason: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """释放明确失败的预占；幂等地保留已结算状态。"""
        if isinstance(reservation, VideoPointsReservation):
            reservation_id = reservation.reservation_id
            usage_period_id = usage_period_id or reservation.usage_period_id
            user_id = user_id or reservation.user_id
        else:
            reservation_id = str(reservation)
        if not usage_period_id:
            raise HTTPException(status_code=400, detail={"code": "video_billing_reservation_invalid", "message": "缺少 usage_period_id"})
        async with self._sf() as session:
            row = (await session.execute(select(UserQuotaUsagePeriodRow).where(UserQuotaUsagePeriodRow.id == usage_period_id).with_for_update())).scalar_one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail={"code": "video_billing_reservation_not_found", "message": "视频积分预占不存在"})
            records = self._video_records(row)
            found = self._video_record_for(records, reservation_id=reservation_id, record_id=reservation_id)
            if found is None:
                raise HTTPException(status_code=404, detail={"code": "video_billing_reservation_not_found", "message": "视频积分预占不存在"})
            key, record = found
            if user_id and record.get("user_id") not in (None, user_id):
                raise HTTPException(status_code=403, detail={"code": "video_billing_reservation_forbidden", "message": "无权操作该视频积分预占"})
            if record.get("status") in {"released", "settled"}:
                return {"transitioned": False, "record": record}
            reserved = int(record.get("reserved_minor_units") or 0)
            row.video_reserved = max(0, int(row.video_reserved or 0) - reserved)
            record.update(status="released", release_reason=reason, settled_minor_units=0, updated_at=datetime.now(UTC).isoformat())
            records["items"][key] = record
            row.video_billing_records = records
            row.updated_at = datetime.now(UTC)
            await session.commit()
            return {"transitioned": True, "record": record, "video_used": int(row.video_used), "video_reserved": int(row.video_reserved)}

    async def mark_video_points_pending(
        self,
        reservation: VideoPointsReservation | str,
        *,
        usage_period_id: str | None = None,
        provider_task_id: str | None = None,
        reason: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """记录状态不明的任务，保留积分预占等待后续查询。"""
        if isinstance(reservation, VideoPointsReservation):
            reservation_id = reservation.reservation_id
            usage_period_id = usage_period_id or reservation.usage_period_id
        else:
            reservation_id = str(reservation)
        if not usage_period_id:
            raise HTTPException(status_code=400, detail={"code": "video_billing_reservation_invalid", "message": "缺少 usage_period_id"})
        async with self._sf() as session:
            row = (await session.execute(select(UserQuotaUsagePeriodRow).where(UserQuotaUsagePeriodRow.id == usage_period_id).with_for_update())).scalar_one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail={"code": "video_billing_reservation_not_found", "message": "视频积分预占不存在"})
            records = self._video_records(row)
            found = self._video_record_for(records, reservation_id=reservation_id, record_id=reservation_id)
            if found is None:
                raise HTTPException(status_code=404, detail={"code": "video_billing_reservation_not_found", "message": "视频积分预占不存在"})
            key, record = found
            if user_id and record.get("user_id") not in (None, user_id):
                raise HTTPException(status_code=403, detail={"code": "video_billing_reservation_forbidden", "message": "无权操作该视频积分预占"})
            if record.get("status") not in {"reserved", "pending"}:
                return {"transitioned": False, "record": record}
            record.update(status="pending", pending_reason=reason, provider_task_id=provider_task_id or record.get("provider_task_id"), updated_at=datetime.now(UTC).isoformat())
            records["items"][key] = record
            row.video_billing_records = records
            row.updated_at = datetime.now(UTC)
            await session.commit()
            return {"transitioned": True, "record": record}

    @staticmethod
    def _exceeded(
        scope: QuotaScopeRow | ModelScopeSnapshot,
        row: UserQuotaUsagePeriodRow,
        metric: QuotaMetricName,
        *,
        model: str | None = None,
        requested: int | None = None,
        reserved: int | None = None,
        unit: str | None = None,
        scale: int | None = None,
    ) -> QuotaExceeded:
        if metric == "model_requests":
            used, limit = int(row.request_used), int(row.request_limit_snapshot or 0)
        elif metric == "video_generations":
            used, limit = int(row.video_used) + int(getattr(row, "video_reserved", 0) or 0), int(row.video_limit_snapshot or 0)
        else:
            used, limit = int(row.image_used), int(row.image_limit_snapshot or 0)
        return QuotaExceeded(
            metric=metric,
            used=used,
            limit=limit,
            period_type=row.period_type,
            period_start=_as_utc(row.period_start),
            period_end=_as_utc(row.period_end),
            scope_id=scope.id,
            scope_code=scope.code,
            scope_name=scope.name,
            model=model,
            reserved=(int(getattr(row, "video_reserved", 0) or 0) if reserved is None else int(reserved)) if metric == "video_generations" else 0,
            unit=unit or ("points" if metric == "video_generations" else "count"),
            scale=scale or (_VIDEO_MINOR_UNIT_SCALE if metric == "video_generations" else 1),
            requested=requested,
        )

    async def override_user_current_period(
        self,
        user_id: str,
        scope_id: str,
        *,
        request_enforced: bool | None,
        request_limit: int | None,
        image_enforced: bool | None,
        image_limit: int | None,
        reason: str | None,
        updated_by: str | None,
        at: datetime | None = None,
        video_enforced: bool | None = None,
        video_limit: int | None = None,
    ) -> dict[str, Any]:
        async with self._sf() as session:
            user = await session.get(UserRow, user_id)
            if user is None:
                raise HTTPException(status_code=404, detail={"code": "user_not_found", "message": "User not found"})
            scope = await session.get(QuotaScopeRow, scope_id)
            if scope is None:
                raise HTTPException(status_code=404, detail={"code": "quota_scope_not_found", "message": "Quota scope not found"})
            if not scope.enabled:
                raise HTTPException(status_code=409, detail={"code": "quota_scope_disabled", "message": "Quota scope is disabled"})
            row = await self._ensure_usage_period(session, user_id, scope, at=at, updated_by=updated_by)
            if scope.resource_type == "model":
                if request_enforced is None:
                    raise HTTPException(status_code=400, detail={"code": "invalid_quota_policy", "message": "requests is required"})
                row.request_enforced_snapshot = request_enforced
                row.request_limit_snapshot = request_limit
            elif scope.resource_type == "video_generation":
                if video_enforced is None:
                    raise HTTPException(status_code=400, detail={"code": "invalid_quota_policy", "message": "videos is required"})
                row.video_enforced_snapshot = video_enforced
                row.video_limit_snapshot = _points_to_minor(video_limit) if video_limit is not None else None
            else:
                if image_enforced is None:
                    raise HTTPException(status_code=400, detail={"code": "invalid_quota_policy", "message": "images is required"})
                row.image_enforced_snapshot = image_enforced
                row.image_limit_snapshot = image_limit
            row.is_overridden = True
            row.overridden_at = datetime.now(UTC)
            row.overridden_by = updated_by
            row.override_reason = reason
            row.updated_at = datetime.now(UTC)
            row.updated_by = updated_by
            await session.commit()
            await session.refresh(row)
            return self._usage_item(scope, row, at=at)

    async def restore_user_current_period(
        self,
        user_id: str,
        scope_id: str,
        *,
        updated_by: str | None = None,
        at: datetime | None = None,
    ) -> dict[str, Any]:
        async with self._sf() as session:
            scope = await session.get(QuotaScopeRow, scope_id)
            if scope is None:
                raise HTTPException(status_code=404, detail={"code": "quota_scope_not_found", "message": "Quota scope not found"})
            window = quota_period_window(scope.period_type, at)
            row = (
                await session.execute(
                    select(UserQuotaUsagePeriodRow).where(
                        UserQuotaUsagePeriodRow.user_id == user_id,
                        UserQuotaUsagePeriodRow.quota_scope_id == scope_id,
                        UserQuotaUsagePeriodRow.period_start == window.period_start,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return self._usage_item(scope, None, at=at)
            row.scope_policy_version_snapshot = scope.policy_version
            row.request_enforced_snapshot = scope.request_enforced
            row.request_limit_snapshot = scope.request_limit
            row.image_enforced_snapshot = scope.image_enforced
            row.image_limit_snapshot = scope.image_limit
            row.video_enforced_snapshot = scope.video_enforced
            row.video_limit_snapshot = scope.video_limit
            row.is_overridden = False
            row.overridden_at = None
            row.overridden_by = None
            row.override_reason = None
            row.updated_at = datetime.now(UTC)
            row.updated_by = updated_by
            await session.commit()
            await session.refresh(row)
            return self._usage_item(scope, row, at=at)

    def _usage_item(
        self,
        scope: QuotaScopeRow,
        row: UserQuotaUsagePeriodRow | None,
        *,
        at: datetime | None = None,
    ) -> dict[str, Any]:
        window = quota_period_window(scope.period_type, at)
        request_enforced = bool(row.request_enforced_snapshot) if row else bool(scope.request_enforced)
        request_limit = row.request_limit_snapshot if row else scope.request_limit
        request_used = int(row.request_used) if row else 0
        image_enforced = bool(row.image_enforced_snapshot) if row else bool(scope.image_enforced)
        image_limit = row.image_limit_snapshot if row else scope.image_limit
        image_used = int(row.image_used) if row else 0
        video_enforced = bool(row.video_enforced_snapshot) if row else bool(scope.video_enforced)
        video_limit = row.video_limit_snapshot if row else scope.video_limit
        video_used = int(row.video_used) if row else 0
        video_reserved = int(getattr(row, "video_reserved", 0) or 0) if row else 0
        requests = _metric(request_enforced, request_limit, request_used) if scope.resource_type == "model" else None
        images = _metric(image_enforced, image_limit, image_used) if scope.resource_type == "image_generation" else None
        videos = _video_metric(video_enforced, video_limit, video_used, video_reserved) if scope.resource_type == "video_generation" else None
        if videos is not None:
            videos["billing_mode"] = _VIDEO_BILLING_MODE
            videos["unit"] = "points"
            videos["scale"] = _VIDEO_MINOR_UNIT_SCALE
        status = (requests or images or videos or {"status": "unlimited"})["status"]
        overridden = bool(row and row.is_overridden)
        return {
            "usage_period_id": row.id if row else None,
            "source": "temporary_override" if overridden else "scope_default",
            "scope": {
                "id": scope.id,
                "code": scope.code,
                "name": scope.name,
                "resource_type": scope.resource_type,
                "is_system": bool(scope.is_system),
            },
            "period_type": scope.period_type,
            "period": quota_period_response(
                PeriodWindow(
                    period=scope.period_type,
                    period_start=_as_utc(row.period_start) if row else window.period_start,
                    period_end=_as_utc(row.period_end) if row else window.period_end,
                    label=window.label,
                )
            ),
            "token_observation": {"used": int(row.token_used) if row else 0} if scope.resource_type == "model" else None,
            "requests": requests,
            "images": images,
            "videos": videos,
            "scope_policy_version": row.scope_policy_version_snapshot if row else scope.policy_version,
            "temporary_override": (
                {
                    "overridden_at": _as_utc(row.overridden_at).isoformat() if row and row.overridden_at else None,
                    "overridden_by": row.overridden_by if row else None,
                    "reason": row.override_reason if row else None,
                }
                if overridden
                else None
            ),
            "status": status,
            "updated_at": _as_utc(row.updated_at).isoformat() if row else _as_utc(scope.updated_at).isoformat(),
            "updated_by": row.updated_by if row else scope.updated_by,
        }

    async def get_user_quota(self, user_id: str, *, at: datetime | None = None) -> dict[str, Any]:
        async with self._sf() as session:
            user = await session.get(UserRow, user_id)
            if user is None:
                raise HTTPException(status_code=404, detail={"code": "user_not_found", "message": "User not found"})
            scopes = list((await session.execute(select(QuotaScopeRow).where(QuotaScopeRow.enabled.is_(True)))).scalars())
            items = []
            for scope in scopes:
                window = quota_period_window(scope.period_type, at)
                row = (
                    await session.execute(
                        select(UserQuotaUsagePeriodRow).where(
                            UserQuotaUsagePeriodRow.user_id == user_id,
                            UserQuotaUsagePeriodRow.quota_scope_id == scope.id,
                            UserQuotaUsagePeriodRow.period_start == window.period_start,
                        )
                    )
                ).scalar_one_or_none()
                items.append(self._usage_item(scope, row, at=at))
            status = max((item["status"] for item in items), key=lambda value: _STATUS_ORDER[value], default="unlimited")
            return {
                "user": {"user_id": str(user.id), "email": user.email, "role": user.system_role},
                "reference_at": (at or datetime.now(UTC)).astimezone(APP_TZ).date().isoformat(),
                "timezone": "Asia/Shanghai",
                "status": status,
                "items": items,
            }

    async def get_video_billing_summary(self) -> dict[str, Any] | None:
        """启用中的视频额度范围价目摘要（小写模型名 → 分辨率元/秒 + 时长范围），未配置返回 None。"""
        async with self._sf() as session:
            scope = (
                await session.execute(
                    select(QuotaScopeRow)
                    .where(QuotaScopeRow.resource_type == "video_generation", QuotaScopeRow.enabled.is_(True))
                    .order_by(QuotaScopeRow.id)
                )
            ).scalars().first()
            if scope is None or not scope.video_billing_rules:
                return None
            return _video_billing_summary(scope.video_billing_rules)

    async def _page_quota_users(
        self,
        session: AsyncSession,
        scopes: list[QuotaScopeRow],
        *,
        at: datetime | None,
        page: int,
        page_size: int,
        keyword: str | None,
        status: str,
    ) -> tuple[list[UserRow], int]:
        eligible_users = select(UserRow.id.label("user_id"))
        if keyword:
            eligible_users = eligible_users.where(func.lower(UserRow.email).like(f"%{keyword.strip().lower()}%"))
        eligible_users = eligible_users.cte("eligible_quota_users")

        if scopes:
            status_selects = []
            for scope in scopes:
                window = quota_period_window(scope.period_type, at)
                status_selects.append(
                    select(
                        eligible_users.c.user_id,
                        _scope_status_rank_expression(scope).label("status_rank"),
                    ).outerjoin(
                        UserQuotaUsagePeriodRow,
                        and_(
                            UserQuotaUsagePeriodRow.user_id == eligible_users.c.user_id,
                            UserQuotaUsagePeriodRow.quota_scope_id == scope.id,
                            UserQuotaUsagePeriodRow.period_type == scope.period_type,
                            UserQuotaUsagePeriodRow.period_start == window.period_start,
                        ),
                    )
                )
            status_rows = (status_selects[0] if len(status_selects) == 1 else union_all(*status_selects)).subquery("quota_scope_statuses")
            user_statuses = (
                select(
                    status_rows.c.user_id,
                    func.max(status_rows.c.status_rank).label("status_rank"),
                )
                .group_by(status_rows.c.user_id)
                .subquery("quota_user_statuses")
            )
        else:
            user_statuses = select(
                eligible_users.c.user_id,
                literal(_STATUS_ORDER["unlimited"]).label("status_rank"),
            ).subquery("quota_user_statuses")

        filtered_statuses = select(user_statuses.c.user_id, user_statuses.c.status_rank)
        if status != "all":
            filtered_statuses = filtered_statuses.where(user_statuses.c.status_rank == _STATUS_ORDER[status])
        filtered_statuses = filtered_statuses.subquery("filtered_quota_users")

        total = int((await session.execute(select(func.count()).select_from(filtered_statuses))).scalar_one())
        users = list((await session.execute(select(UserRow).join(filtered_statuses, filtered_statuses.c.user_id == UserRow.id).order_by(UserRow.created_at.desc(), UserRow.id).offset((page - 1) * page_size).limit(page_size))).scalars())
        return users, total

    async def list_user_quotas(
        self,
        *,
        at: datetime | None = None,
        page: int = 1,
        page_size: int = 50,
        keyword: str | None = None,
        status: str = "all",
    ) -> dict[str, Any]:
        page = max(1, page)
        page_size = min(max(1, page_size), 100)
        async with self._sf() as session:
            scopes = list((await session.execute(select(QuotaScopeRow).where(QuotaScopeRow.enabled.is_(True)))).scalars())
            users, total = await self._page_quota_users(
                session,
                scopes,
                at=at,
                page=page,
                page_size=page_size,
                keyword=keyword,
                status=status,
            )
            user_ids = [str(user.id) for user in users]
            current_period_conditions = [
                and_(
                    UserQuotaUsagePeriodRow.quota_scope_id == scope.id,
                    UserQuotaUsagePeriodRow.period_type == scope.period_type,
                    UserQuotaUsagePeriodRow.period_start == quota_period_window(scope.period_type, at).period_start,
                )
                for scope in scopes
            ]
            rows = (
                list(
                    (
                        await session.execute(
                            select(UserQuotaUsagePeriodRow).where(
                                UserQuotaUsagePeriodRow.user_id.in_(user_ids),
                                or_(*current_period_conditions),
                            )
                        )
                    ).scalars()
                )
                if user_ids and current_period_conditions
                else []
            )
            row_map = {(row.user_id, row.quota_scope_id, _as_utc(row.period_start)): row for row in rows}
            results = []
            for user in users:
                items = []
                for scope in scopes:
                    window = quota_period_window(scope.period_type, at)
                    items.append(self._usage_item(scope, row_map.get((str(user.id), scope.id, window.period_start)), at=at))
                overall = max((item["status"] for item in items), key=lambda value: _STATUS_ORDER[value], default="unlimited")
                image = next((item for item in items if item["scope"]["resource_type"] == "image_generation"), None)
                video = next((item for item in items if item["scope"]["resource_type"] == "video_generation"), None)
                models = [item for item in items if item["scope"]["resource_type"] == "model"]
                results.append(
                    {
                        "user_id": str(user.id),
                        "email": user.email,
                        "role": user.system_role,
                        "status": overall,
                        "image_generation": image,
                        "video_generation": video,
                        "model_groups": {
                            "enabled": len(models),
                            "warning": sum(item["status"] == "warning" for item in models),
                            "exceeded": sum(item["status"] == "exceeded" for item in models),
                            "overridden": sum(item["source"] == "temporary_override" for item in models),
                            "items": [
                                {
                                    "scope_id": item["scope"]["id"],
                                    "name": item["scope"]["name"],
                                    "period_type": item["period_type"],
                                    "period": item["period"],
                                    "source": item["source"],
                                    "status": item["status"],
                                    "requests": item["requests"],
                                    "token_observation": item["token_observation"],
                                }
                                for item in models
                            ],
                        },
                    }
                )
            return {
                "items": results,
                "total": total,
                "page": page,
                "page_size": page_size,
                "reference_at": (at or datetime.now(UTC)).astimezone(APP_TZ).date().isoformat(),
                "timezone": "Asia/Shanghai",
            }

    async def list_usage_periods(
        self,
        user_id: str,
        scope_id: str,
        *,
        limit: int = 20,
        start: datetime | None = None,
        end: datetime | None = None,
        cursor: datetime | None = None,
    ) -> dict[str, Any]:
        async with self._sf() as session:
            scope = await session.get(QuotaScopeRow, scope_id)
            if scope is None:
                raise HTTPException(status_code=404, detail={"code": "quota_scope_not_found", "message": "Quota scope not found"})
            stmt = select(UserQuotaUsagePeriodRow).where(
                UserQuotaUsagePeriodRow.user_id == user_id,
                UserQuotaUsagePeriodRow.quota_scope_id == scope_id,
            )
            if start is not None:
                stmt = stmt.where(UserQuotaUsagePeriodRow.period_start >= _as_utc(start))
            if end is not None:
                stmt = stmt.where(UserQuotaUsagePeriodRow.period_start < _as_utc(end))
            if cursor is not None:
                stmt = stmt.where(UserQuotaUsagePeriodRow.period_start < _as_utc(cursor))
            page_limit = min(max(1, limit), 100)
            rows = list((await session.execute(stmt.order_by(UserQuotaUsagePeriodRow.period_start.desc()).limit(page_limit + 1))).scalars())
            has_more = len(rows) > page_limit
            page_rows = rows[:page_limit]
            return {
                "user_id": user_id,
                "scope": {"id": scope.id, "code": scope.code, "name": scope.name},
                "items": [self._usage_item(scope, row, at=_as_utc(row.period_start)) for row in page_rows],
                "next_cursor": _as_utc(page_rows[-1].period_start).isoformat() if has_more and page_rows else None,
            }


def quota_exceeded_payload(exceeded: QuotaExceeded) -> dict[str, Any]:
    points_mode = exceeded.metric == "video_generations" and exceeded.unit == "points"
    payload: dict[str, Any] = {
        "code": "quota_exceeded",
        "message": f"{exceeded.scope_name}额度已用尽",
        "scope": {"id": exceeded.scope_id, "code": exceeded.scope_code, "name": exceeded.scope_name},
        "metric": exceeded.metric,
        "model": exceeded.model,
        "period": {
            "period_type": exceeded.period_type,
            "period_start": exceeded.period_start.isoformat(),
            "period_end": exceeded.period_end.isoformat(),
            "timezone": "Asia/Shanghai",
        },
        "used": exceeded.used,
        "limit": exceeded.limit,
        "retryable": False,
    }
    if points_mode:
        # Keep the legacy used/limit keys in internal units for compatibility,
        # while exposing human-facing point values for new video callers.
        payload.update(
            {
                "billing_mode": "points",
                "unit": "points",
                "scale": exceeded.scale,
                "used_points": _minor_to_points(exceeded.used),
                "limit_points": _minor_to_points(exceeded.limit),
                "reserved_points": _minor_to_points(exceeded.reserved),
                "requested_points": _minor_to_points(exceeded.requested),
                "remaining_points": _minor_to_points(max(0, exceeded.limit - exceeded.used)),
            }
        )
        requested = _minor_to_points(exceeded.requested)
        remaining = _minor_to_points(max(0, exceeded.limit - exceeded.used))
        if requested is not None and remaining is not None:
            payload["message"] = f"视频生成预计需要 {requested:g} 积分，当前仅剩余 {remaining:g} 积分"
    return payload


def quota_exceeded_http_error(exc: QuotaExceededError) -> HTTPException:
    return HTTPException(status_code=429, detail=quota_exceeded_payload(exc.exceeded))
