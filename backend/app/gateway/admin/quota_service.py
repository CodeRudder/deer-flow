"""Admin usage statistics and quota-control services."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import and_, case, func, or_, select, true, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.gateway.admin.periods import APP_TZ, PeriodWindow, period_response
from deerflow.config.quota_control_config import QuotaControlConfig
from deerflow.persistence.quota.model import UserQuotaPeriodRow
from deerflow.persistence.user.model import UserRow


@dataclass(frozen=True)
class QuotaExceeded:
    quota_type: Literal["model_tokens", "model_requests", "image_generations"]
    used: int
    limit: int
    period_start: datetime
    period_end: datetime


class QuotaExceededError(Exception):
    def __init__(self, exceeded: QuotaExceeded) -> None:
        self.exceeded = exceeded
        super().__init__(f"{exceeded.quota_type} quota exceeded")


def _month_start(year: int, month: int) -> datetime:
    return datetime(year, month, 1, tzinfo=APP_TZ).astimezone(UTC)


def _month_end(year: int, month: int) -> datetime:
    if month == 12:
        return _month_start(year + 1, 1)
    return _month_start(year, month + 1)


def _month_window(year: int, month: int) -> PeriodWindow:
    return PeriodWindow(
        period="monthly",
        period_start=_month_start(year, month),
        period_end=_month_end(year, month),
        label=f"{year:04d}-{month:02d}",
    )


def quota_period_window(period: str = "this_month", period_start: str | None = None) -> PeriodWindow:
    now = datetime.now(APP_TZ)
    if period == "last_month":
        year = now.year
        month = now.month - 1
        if month == 0:
            year -= 1
            month = 12
        return _month_window(year, month)
    if period == "custom":
        if not period_start:
            raise HTTPException(status_code=400, detail="period_start is required when period=custom")
        try:
            parsed = datetime.fromisoformat(period_start)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="period_start must be YYYY-MM-01") from exc
        if parsed.day != 1:
            raise HTTPException(status_code=400, detail="period_start must be the first day of a month")
        return _month_window(parsed.year, parsed.month)
    return _month_window(now.year, now.month)


def _quota_limit(cfg: QuotaControlConfig, name: str) -> tuple[bool, int | None]:
    item = getattr(cfg.defaults, name)
    return bool(item.enabled), item.limit_value


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class QuotaService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], config: QuotaControlConfig) -> None:
        self._sf = session_factory
        self._config = config

    def is_enabled(self) -> bool:
        return bool(self._config.enabled)

    def is_dimension_enabled(self, quota_type: str) -> bool:
        return bool(getattr(self._config.defaults, quota_type).enabled)

    async def ensure_user_period(
        self,
        user_id: str,
        *,
        window: PeriodWindow | None = None,
        updated_by: str | None = None,
    ) -> UserQuotaPeriodRow:
        window = window or quota_period_window("this_month")
        async with self._sf() as session:
            row = await self._ensure_user_period(session, user_id, window=window, updated_by=updated_by)
            await session.commit()
            return row

    async def _ensure_user_period(
        self,
        session: AsyncSession,
        user_id: str,
        *,
        window: PeriodWindow,
        updated_by: str | None = None,
    ) -> UserQuotaPeriodRow:
        lookup = select(UserQuotaPeriodRow).where(
            UserQuotaPeriodRow.user_id == user_id,
            UserQuotaPeriodRow.period == "monthly",
            UserQuotaPeriodRow.period_start == window.period_start,
        )
        existing = (await session.execute(lookup)).scalar_one_or_none()
        if existing is not None:
            return existing

        values = self._new_period_values(user_id, window=window, updated_by=updated_by)
        await session.execute(self._period_insert(session, values))
        return (await session.execute(lookup)).scalar_one()

    def _new_period_values(self, user_id: str, *, window: PeriodWindow, updated_by: str | None = None) -> dict[str, Any]:
        token_enabled, token_limit = _quota_limit(self._config, "model_tokens")
        request_enabled, request_limit = _quota_limit(self._config, "model_requests")
        image_enabled, image_limit = _quota_limit(self._config, "image_generations")
        now = datetime.now(UTC)
        return dict(
            id=str(uuid.uuid4()),
            user_id=user_id,
            period="monthly",
            period_start=window.period_start,
            period_end=window.period_end,
            model_tokens_limit=token_limit,
            model_tokens_used=0,
            model_tokens_enabled=token_enabled,
            model_requests_limit=request_limit,
            model_requests_used=0,
            model_requests_enabled=request_enabled,
            image_generations_limit=image_limit,
            image_generations_used=0,
            image_generations_enabled=image_enabled,
            created_at=now,
            updated_at=now,
            updated_by=updated_by,
        )

    @staticmethod
    def _period_insert(session: AsyncSession, values: dict[str, Any] | list[dict[str, Any]]):
        dialect_name = session.get_bind().dialect.name
        if dialect_name == "postgresql":
            return postgresql_insert(UserQuotaPeriodRow).values(values).on_conflict_do_nothing(index_elements=["user_id", "period", "period_start"])
        return sqlite_insert(UserQuotaPeriodRow).values(values).on_conflict_do_nothing(index_elements=["user_id", "period", "period_start"])

    @staticmethod
    def _maybe_exceeded(row: UserQuotaPeriodRow, quota_type: Literal["model_tokens", "model_requests", "image_generations"]) -> QuotaExceeded | None:
        enabled = bool(getattr(row, f"{quota_type}_enabled"))
        limit = getattr(row, f"{quota_type}_limit")
        used = int(getattr(row, f"{quota_type}_used") or 0)
        if enabled and limit is not None and used >= int(limit):
            return QuotaExceeded(quota_type, used, int(limit), _as_utc(row.period_start), _as_utc(row.period_end))
        return None

    async def check_run_creation(self, user_id: str) -> None:
        if not self._config.enabled:
            return
        row = await self.ensure_user_period(user_id)
        if not self._config.enforce_on_run_create:
            return
        for quota_type in ("model_tokens", "model_requests"):
            if not self.is_dimension_enabled(quota_type):
                continue
            exceeded = self._maybe_exceeded(row, quota_type)  # type: ignore[arg-type]
            if exceeded is not None:
                raise QuotaExceededError(exceeded)

    async def consume_image_generations(self, user_id: str, *, count: int = 1) -> int:
        """Atomically track image operations, enforcing limits when enabled."""
        count = int(count)
        if count <= 0 or not self._config.enabled:
            return 0
        window = quota_period_window("this_month")
        async with self._sf() as session:
            await self._ensure_user_period(session, user_id, window=window)
            result = await session.execute(
                update(UserQuotaPeriodRow)
                .where(
                    UserQuotaPeriodRow.user_id == user_id,
                    UserQuotaPeriodRow.period == "monthly",
                    UserQuotaPeriodRow.period_start == window.period_start,
                    (
                        or_(
                            UserQuotaPeriodRow.image_generations_enabled.is_(False),
                            UserQuotaPeriodRow.image_generations_limit.is_(None),
                            UserQuotaPeriodRow.image_generations_used + count <= UserQuotaPeriodRow.image_generations_limit,
                        )
                        if self.is_dimension_enabled("image_generations")
                        else true()
                    ),
                )
                .values(
                    image_generations_used=UserQuotaPeriodRow.image_generations_used + count,
                    updated_at=datetime.now(UTC),
                )
                .returning(UserQuotaPeriodRow.image_generations_used)
            )
            new_used = result.scalar_one_or_none()
            if new_used is not None:
                await session.commit()
                return int(new_used)

            row = (
                await session.execute(
                    select(UserQuotaPeriodRow).where(
                        UserQuotaPeriodRow.user_id == user_id,
                        UserQuotaPeriodRow.period == "monthly",
                        UserQuotaPeriodRow.period_start == window.period_start,
                    )
                )
            ).scalar_one()
            exceeded = self._maybe_exceeded(row, "image_generations")
            if exceeded is None:
                # A multi-image command can exceed the remaining allowance
                # even when the current used value itself is below the limit.
                exceeded = QuotaExceeded(
                    "image_generations",
                    int(row.image_generations_used or 0),
                    int(row.image_generations_limit or 0),
                    _as_utc(row.period_start),
                    _as_utc(row.period_end),
                )
            raise QuotaExceededError(exceeded)

    async def release_image_generations(self, user_id: str, *, count: int = 1) -> None:
        """Release a reservation when command dispatch never started."""
        count = int(count)
        if count <= 0 or not self._config.enabled:
            return
        window = quota_period_window("this_month")
        async with self._sf() as session:
            await session.execute(
                update(UserQuotaPeriodRow)
                .where(
                    UserQuotaPeriodRow.user_id == user_id,
                    UserQuotaPeriodRow.period == "monthly",
                    UserQuotaPeriodRow.period_start == window.period_start,
                )
                .values(
                    image_generations_used=case(
                        (UserQuotaPeriodRow.image_generations_used >= count, UserQuotaPeriodRow.image_generations_used - count),
                        else_=0,
                    ),
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()

    async def list_user_quotas(
        self,
        *,
        window: PeriodWindow,
        page: int = 1,
        page_size: int = 50,
        keyword: str | None = None,
        status: str = "all",
    ) -> dict[str, Any]:
        page = max(1, page)
        page_size = min(max(1, page_size), 100)
        async with self._sf() as session:
            user_ids_stmt = select(UserRow.id)
            if keyword:
                like = f"%{keyword.strip().lower()}%"
                predicate = func.lower(UserRow.email).like(like)
                user_ids_stmt = user_ids_stmt.where(predicate)
            user_ids = [str(value) for value in (await session.execute(user_ids_stmt)).scalars()]
            if user_ids:
                await session.execute(
                    self._period_insert(
                        session,
                        [self._new_period_values(user_id, window=window) for user_id in user_ids],
                    )
                )

            unlimited = and_(
                or_(UserQuotaPeriodRow.model_tokens_enabled.is_(False), UserQuotaPeriodRow.model_tokens_limit.is_(None)),
                or_(UserQuotaPeriodRow.model_requests_enabled.is_(False), UserQuotaPeriodRow.model_requests_limit.is_(None)),
                or_(UserQuotaPeriodRow.image_generations_enabled.is_(False), UserQuotaPeriodRow.image_generations_limit.is_(None)),
            )
            exceeded = or_(
                and_(UserQuotaPeriodRow.model_tokens_enabled.is_(True), UserQuotaPeriodRow.model_tokens_limit.is_not(None), UserQuotaPeriodRow.model_tokens_used >= UserQuotaPeriodRow.model_tokens_limit),
                and_(UserQuotaPeriodRow.model_requests_enabled.is_(True), UserQuotaPeriodRow.model_requests_limit.is_not(None), UserQuotaPeriodRow.model_requests_used >= UserQuotaPeriodRow.model_requests_limit),
                and_(UserQuotaPeriodRow.image_generations_enabled.is_(True), UserQuotaPeriodRow.image_generations_limit.is_not(None), UserQuotaPeriodRow.image_generations_used >= UserQuotaPeriodRow.image_generations_limit),
            )
            warning = and_(
                ~unlimited,
                ~exceeded,
                or_(
                    and_(UserQuotaPeriodRow.model_tokens_enabled.is_(True), UserQuotaPeriodRow.model_tokens_limit > 0, UserQuotaPeriodRow.model_tokens_used * 10 >= UserQuotaPeriodRow.model_tokens_limit * 8),
                    and_(UserQuotaPeriodRow.model_requests_enabled.is_(True), UserQuotaPeriodRow.model_requests_limit > 0, UserQuotaPeriodRow.model_requests_used * 10 >= UserQuotaPeriodRow.model_requests_limit * 8),
                    and_(UserQuotaPeriodRow.image_generations_enabled.is_(True), UserQuotaPeriodRow.image_generations_limit > 0, UserQuotaPeriodRow.image_generations_used * 10 >= UserQuotaPeriodRow.image_generations_limit * 8),
                ),
            )
            status_predicates = {
                "unlimited": unlimited,
                "exceeded": exceeded,
                "warning": warning,
                "normal": and_(~unlimited, ~exceeded, ~warning),
            }
            base_predicates = [
                UserQuotaPeriodRow.period == "monthly",
                UserQuotaPeriodRow.period_start == window.period_start,
            ]
            if keyword:
                base_predicates.append(predicate)
            if status in status_predicates:
                base_predicates.append(status_predicates[status])
            count_stmt = select(func.count(UserRow.id)).join(UserQuotaPeriodRow, UserQuotaPeriodRow.user_id == UserRow.id).where(*base_predicates)
            total = int((await session.execute(count_stmt)).scalar_one() or 0)
            rows_stmt = (
                select(UserRow, UserQuotaPeriodRow).join(UserQuotaPeriodRow, UserQuotaPeriodRow.user_id == UserRow.id).where(*base_predicates).order_by(UserRow.created_at.desc(), UserRow.id).offset((page - 1) * page_size).limit(page_size)
            )
            pairs = (await session.execute(rows_stmt)).all()
            rows = [quota_row_to_response(user, quota) for user, quota in pairs]
            await session.commit()
            return {"items": rows, "total": total, "page": page, "page_size": page_size, "period": period_response(window)}

    async def get_user_quota(self, user_id: str, *, window: PeriodWindow) -> dict[str, Any]:
        async with self._sf() as session:
            user = await session.get(UserRow, user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="User not found")
            quota = await self._ensure_user_period(session, user_id, window=window)
            await session.commit()
            return quota_row_to_response(user, quota)

    async def update_user_quota(self, user_id: str, *, payload: dict[str, Any], window: PeriodWindow, updated_by: str | None) -> dict[str, Any]:
        async with self._sf() as session:
            user = await session.get(UserRow, user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="User not found")
            row = await self._ensure_user_period(session, user_id, window=window, updated_by=updated_by)
            for quota_type in ("model_tokens", "model_requests", "image_generations"):
                value = payload.get(quota_type)
                if not isinstance(value, dict):
                    continue
                if "enabled" in value:
                    setattr(row, f"{quota_type}_enabled", bool(value["enabled"]))
                if "limit" in value:
                    limit = value["limit"]
                    setattr(row, f"{quota_type}_limit", None if limit is None else max(0, int(limit)))
            row.updated_by = updated_by
            row.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(row)
            return quota_row_to_response(user, row)


def quota_row_to_response(user: UserRow, row: UserQuotaPeriodRow) -> dict[str, Any]:
    metrics = {
        "model_tokens": _metric(row, "model_tokens"),
        "model_requests": _metric(row, "model_requests"),
        "image_generations": _metric(row, "image_generations"),
    }
    status = "normal"
    if all((not item["enabled"]) or item["limit"] is None for item in metrics.values()):
        status = "unlimited"
    elif any(item["enabled"] and item["limit"] is not None and item["used"] >= item["limit"] for item in metrics.values()):
        status = "exceeded"
    elif any(item["enabled"] and item["limit"] is not None and item["limit"] > 0 and item["used"] / item["limit"] >= 0.8 for item in metrics.values()):
        status = "warning"
    return {
        "user_id": str(user.id),
        "email": user.email,
        "role": user.system_role,
        "status": status,
        "period": period_response(
            PeriodWindow(
                row.period,
                _as_utc(row.period_start),
                _as_utc(row.period_end),
                _as_utc(row.period_start).astimezone(APP_TZ).strftime("%Y-%m"),
            )
        ),
        **metrics,
    }


def _metric(row: UserQuotaPeriodRow, quota_type: str) -> dict[str, Any]:
    used = int(getattr(row, f"{quota_type}_used") or 0)
    limit = getattr(row, f"{quota_type}_limit")
    enabled = bool(getattr(row, f"{quota_type}_enabled"))
    remaining = None if limit is None else max(0, int(limit) - used)
    return {"enabled": enabled, "used": used, "limit": limit, "remaining": remaining}


def quota_exceeded_http_error(exc: QuotaExceededError) -> HTTPException:
    exceeded = exc.exceeded
    return HTTPException(
        status_code=429,
        detail={
            "code": "quota_exceeded",
            "message": "当前用户本月额度已用尽",
            "exceeded_quota": exceeded.quota_type,
            "period": {
                "period_start": exceeded.period_start.isoformat(),
                "period_end": exceeded.period_end.isoformat(),
                "timezone": "Asia/Shanghai",
            },
            "used": exceeded.used,
            "limit": exceeded.limit,
        },
    )
