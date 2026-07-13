"""Read-only administrator session tracing queries."""

from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.gateway.admin.display import format_output_preview, format_user_input
from app.gateway.admin.periods import APP_TZ
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.thread_meta.model import ThreadMetaRow
from deerflow.persistence.user.model import UserRow
from deerflow.runtime.events.store.base import RunEventStore


class SessionTraceNotFoundError(LookupError):
    """Requested user or run does not exist in the requested scope."""


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _cursor_offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        prefix, value = raw.split(":", 1)
        if prefix != "offset":
            raise ValueError
        return max(0, int(value))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid user search cursor") from exc


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"offset:{offset}".encode()).decode()


class SessionTraceService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_store: RunEventStore,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._sf = session_factory
        self._events = event_store
        self._now = now or (lambda: datetime.now(UTC))

    async def list_users(self, *, keyword: str | None, limit: int, cursor: str | None) -> dict[str, Any]:
        offset = _cursor_offset(cursor)
        page_limit = min(max(1, limit), 50)
        last_active = select(RunRow.user_id, func.max(RunRow.created_at).label("last_active_at")).group_by(RunRow.user_id).subquery()
        stmt = select(UserRow.id, UserRow.email, last_active.c.last_active_at).outerjoin(last_active, last_active.c.user_id == UserRow.id)
        normalized = (keyword or "").strip()
        if normalized:
            stmt = stmt.where(or_(func.lower(UserRow.email).contains(normalized.lower()), UserRow.id.startswith(normalized)))
        stmt = stmt.order_by(last_active.c.last_active_at.desc().nulls_last(), UserRow.id.asc()).offset(offset).limit(page_limit + 1)
        async with self._sf() as session:
            rows = (await session.execute(stmt)).all()
        has_more = len(rows) > page_limit
        rows = rows[:page_limit]
        return {
            "items": [{"user_id": user_id, "email": email, "last_active_at": _iso(active)} for user_id, email, active in rows],
            "next_cursor": _encode_cursor(offset + page_limit) if has_more else None,
        }

    def _overview_window(self) -> tuple[datetime, datetime]:
        local_now = self._now().astimezone(APP_TZ)
        period_end = datetime.combine(local_now.date() + timedelta(days=1), time.min, tzinfo=APP_TZ)
        return period_end - timedelta(days=30), period_end

    async def user_overview(self, user_id: str) -> dict[str, Any]:
        start, end = self._overview_window()
        async with self._sf() as session:
            user = (await session.execute(select(UserRow.id, UserRow.email).where(UserRow.id == user_id))).one_or_none()
            if user is None:
                raise SessionTraceNotFoundError("User not found")
            where = (RunRow.user_id == user_id, RunRow.created_at >= start, RunRow.created_at < end)
            summary = (
                await session.execute(
                    select(
                        func.count(func.distinct(RunRow.thread_id)),
                        func.count(RunRow.run_id),
                        func.coalesce(func.sum(RunRow.total_tokens), 0),
                        func.coalesce(func.sum(RunRow.llm_call_count), 0),
                        func.coalesce(func.sum(RunRow.image_generation_count), 0),
                        func.max(RunRow.created_at),
                    ).where(*where)
                )
            ).one()
            rows = (await session.execute(select(RunRow.created_at, RunRow.total_tokens, RunRow.llm_call_count, RunRow.image_generation_count, RunRow.model_name, RunRow.token_usage_by_model).where(*where))).all()

        thread_count, run_count, tokens, requests, images, last_active = summary
        buckets: dict[str, dict[str, int]] = {}
        by_model: dict[str, int] = {}
        for created_at, run_tokens, calls, run_images, model_name, usage in rows:
            aware = created_at.replace(tzinfo=UTC) if created_at.tzinfo is None else created_at
            key = aware.astimezone(APP_TZ).date().isoformat()
            bucket = buckets.setdefault(key, {"tokens": 0, "model_requests": 0, "image_generations": 0})
            bucket["tokens"] += int(run_tokens or 0)
            bucket["model_requests"] += int(calls or 0)
            bucket["image_generations"] += int(run_images or 0)
            if usage:
                for model, data in usage.items():
                    by_model[model or "unknown"] = by_model.get(model or "unknown", 0) + int(data.get("total_tokens") or 0)
            else:
                by_model[model_name or "unknown"] = by_model.get(model_name or "unknown", 0) + int(run_tokens or 0)
        trends = []
        for index in range(30):
            key = (start.date() + timedelta(days=index)).isoformat()
            trends.append({"date": key, **buckets.get(key, {"tokens": 0, "model_requests": 0, "image_generations": 0})})
        model_total = sum(by_model.values()) or 1
        models = [{"model": model, "tokens": value, "share": value / model_total} for model, value in by_model.items()]
        models.sort(key=lambda item: item["tokens"], reverse=True)
        return {
            "user": {"user_id": user.id, "email": user.email, "last_active_at": _iso(last_active)},
            "period": {"days": 30, "period_start": start.isoformat(), "period_end": end.isoformat(), "timezone": str(APP_TZ)},
            "summary": {
                "thread_count": int(thread_count or 0),
                "run_count": int(run_count or 0),
                "total_tokens": int(tokens or 0),
                "model_requests": int(requests or 0),
                "image_generations": int(images or 0),
            },
            "trends": trends,
            "models": models,
        }

    async def list_runs(self, *, user_id: str | None, thread_id: str | None, run_id: str | None, page: int, page_size: int) -> dict[str, Any]:
        conditions = []
        if user_id:
            conditions.append(RunRow.user_id == user_id)
        if thread_id:
            conditions.append(RunRow.thread_id == thread_id)
        if run_id:
            conditions.append(RunRow.run_id == run_id)
        size = min(max(1, page_size), 100)
        number = max(1, page)
        async with self._sf() as session:
            total = await session.scalar(select(func.count()).select_from(RunRow).where(*conditions)) or 0
            rows = (
                await session.execute(
                    select(RunRow, UserRow.email, ThreadMetaRow.display_name)
                    .outerjoin(UserRow, UserRow.id == RunRow.user_id)
                    .outerjoin(ThreadMetaRow, ThreadMetaRow.thread_id == RunRow.thread_id)
                    .where(*conditions)
                    .order_by(RunRow.created_at.desc(), RunRow.run_id.desc())
                    .offset((number - 1) * size)
                    .limit(size)
                )
            ).all()
        items = []
        for row, email, title in rows:
            duration = None
            if row.created_at and row.updated_at:
                duration = max(0, int((row.updated_at - row.created_at).total_seconds() * 1000))
            items.append(
                {
                    "run_id": row.run_id,
                    "thread_id": row.thread_id,
                    "thread_title": title or row.thread_id,
                    "user_id": row.user_id,
                    "email": email,
                    "status": row.status,
                    "model_name": row.model_name,
                    "created_at": _iso(row.created_at),
                    "updated_at": _iso(row.updated_at),
                    "duration_ms": duration,
                    "duration_approximate": True,
                    "first_human_message": row.first_human_message,
                    "last_ai_message": row.last_ai_message,
                    "input_preview": format_user_input(row.first_human_message),
                    "output_preview": format_output_preview(row.last_ai_message),
                    "total_input_tokens": row.total_input_tokens,
                    "total_output_tokens": row.total_output_tokens,
                    "total_tokens": row.total_tokens,
                    "llm_call_count": row.llm_call_count,
                    "image_generation_count": row.image_generation_count,
                    "token_usage_by_model": row.token_usage_by_model or {},
                    "error": row.error,
                }
            )
        return {"items": items, "total": int(total), "page": number, "page_size": size}

    async def run_events(self, run_id: str, *, thread_id: str, limit: int) -> dict[str, Any]:
        async with self._sf() as session:
            exists = await session.scalar(select(func.count()).select_from(RunRow).where(RunRow.run_id == run_id, RunRow.thread_id == thread_id))
        if not exists:
            raise SessionTraceNotFoundError("Run not found")
        capped = min(max(1, limit), 500)
        events = await self._events.list_events(thread_id, run_id, limit=capped + 1)
        return {
            "run_id": run_id,
            "thread_id": thread_id,
            "items": events[:capped],
            "returned": min(len(events), capped),
            "limit": capped,
            "truncated": len(events) > capped,
        }
