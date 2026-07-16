"""Model-group quota policies, per-user period usage, and runtime accounting."""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import and_, case, func, literal, or_, select, union_all, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.gateway.admin.periods import APP_TZ, PeriodWindow
from deerflow.persistence.quota.model import QuotaScopeRow, UserQuotaUsagePeriodRow
from deerflow.persistence.user.model import UserRow

QuotaMetricName = Literal["model_requests", "image_generations"]
_SCOPE_CODE_PATTERN = re.compile(r"^[a-z0-9_]{2,64}$")
_STATUS_ORDER = {"unlimited": 0, "normal": 1, "warning": 2, "exceeded": 3}


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


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


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
        if resource_type not in {"model", "image_generation"}:
            raise HTTPException(status_code=400, detail={"code": "invalid_scope_rules", "message": "Invalid resource type"})
        if resource_type == "image_generation" and code != "image_generation":
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_scope_rules", "message": "Image scope code must be image_generation"},
            )
        rules = _normalize_rules(payload.get("match_rules")) if resource_type == "model" else {"exact": [], "prefix": []}
        enabled = bool(payload.get("enabled", True))
        async with self._sf() as session:
            if (await session.execute(select(QuotaScopeRow.id).where(QuotaScopeRow.code == code))).scalar_one_or_none():
                raise HTTPException(status_code=409, detail={"code": "quota_scope_code_exists", "message": "Scope code already exists"})
            if resource_type == "image_generation":
                existing_image_scope = (await session.execute(select(QuotaScopeRow.id).where(QuotaScopeRow.resource_type == "image_generation"))).scalar_one_or_none()
                if existing_image_scope is not None:
                    raise HTTPException(
                        status_code=409,
                        detail={"code": "quota_scope_code_exists", "message": "Image generation scope already exists"},
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
                is_system=resource_type == "image_generation",
                period_type=str(payload.get("period_type") or "weekly"),
                request_enforced=bool(payload.get("request_enforced", False)) if resource_type == "model" else False,
                request_limit=payload.get("request_limit") if resource_type == "model" else None,
                image_enforced=bool(payload.get("image_enforced", False)) if resource_type == "image_generation" else False,
                image_limit=payload.get("image_limit") if resource_type == "image_generation" else None,
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
                image_enforced = bool(payload.get("image_enforced", scope.image_enforced))
                image_limit = payload.get("image_limit", scope.image_limit)
                period_type = str(payload.get("period_type") or scope.period_type)
                definition_changed = (scope.name, scope.enabled) != (name, enabled)
                policy_changed = (scope.period_type, scope.image_enforced, scope.image_limit) != (
                    period_type,
                    image_enforced,
                    image_limit,
                )
                scope.name = name
                scope.enabled = enabled
                scope.image_enforced = image_enforced
                scope.image_limit = image_limit
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
                "policy_version": scope.policy_version,
            },
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

    @staticmethod
    def _exceeded(
        scope: QuotaScopeRow | ModelScopeSnapshot,
        row: UserQuotaUsagePeriodRow,
        metric: QuotaMetricName,
        *,
        model: str | None = None,
    ) -> QuotaExceeded:
        if metric == "model_requests":
            used, limit = int(row.request_used), int(row.request_limit_snapshot or 0)
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
        requests = _metric(request_enforced, request_limit, request_used) if scope.resource_type == "model" else None
        images = _metric(image_enforced, image_limit, image_used) if scope.resource_type == "image_generation" else None
        status = (requests or images or {"status": "unlimited"})["status"]
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
                models = [item for item in items if item["scope"]["resource_type"] == "model"]
                results.append(
                    {
                        "user_id": str(user.id),
                        "email": user.email,
                        "role": user.system_role,
                        "status": overall,
                        "image_generation": image,
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
    return {
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


def quota_exceeded_http_error(exc: QuotaExceededError) -> HTTPException:
    return HTTPException(status_code=429, detail=quota_exceeded_payload(exc.exceeded))
