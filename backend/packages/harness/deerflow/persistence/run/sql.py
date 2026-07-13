"""SQLAlchemy-backed RunStore implementation.

Each method acquires and releases its own short-lived session.
Run status updates happen from background workers that may live
minutes -- we don't hold connections across long execution.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.quota.model import UserQuotaPeriodRow
from deerflow.persistence.run.model import RunRow
from deerflow.runtime.runs.store.base import RunStore
from deerflow.runtime.user_context import AUTO, _AutoSentinel, resolve_user_id
from deerflow.utils.time import coerce_iso


class RunRepository(RunStore):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    @staticmethod
    def _normalize_model_name(model_name: str | None) -> str | None:
        """Normalize model_name for storage: strip whitespace, truncate to 128 chars."""
        if model_name is None:
            return None
        if not isinstance(model_name, str):
            model_name = str(model_name)
        normalized = model_name.strip()
        if len(normalized) > 128:
            normalized = normalized[:128]
        return normalized

    @staticmethod
    def _safe_json(obj: Any) -> Any:
        """Ensure obj is JSON-serializable. Falls back to model_dump() or str()."""
        if obj is None:
            return None
        if isinstance(obj, (str, int, float, bool)):
            return obj
        if isinstance(obj, dict):
            return {k: RunRepository._safe_json(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [RunRepository._safe_json(v) for v in obj]
        if hasattr(obj, "model_dump"):
            try:
                return obj.model_dump()
            except Exception:
                pass
        if hasattr(obj, "dict"):
            try:
                return obj.dict()
            except Exception:
                pass
        try:
            json.dumps(obj)
            return obj
        except (TypeError, ValueError):
            return str(obj)

    @staticmethod
    def _row_to_dict(row: RunRow) -> dict[str, Any]:
        d = row.to_dict()
        # Remap JSON columns to match RunStore interface
        d["metadata"] = d.pop("metadata_json", {})
        d["kwargs"] = d.pop("kwargs_json", {})
        # Convert datetime to ISO string for consistency with MemoryRunStore.
        # SQLite drops tzinfo on read despite ``DateTime(timezone=True)`` —
        # ``coerce_iso`` normalizes naive datetimes as UTC.
        for key in ("created_at", "updated_at"):
            val = d.get(key)
            if isinstance(val, datetime):
                d[key] = coerce_iso(val)
        return d

    async def put(
        self,
        run_id,
        *,
        thread_id,
        assistant_id=None,
        user_id: str | None | _AutoSentinel = AUTO,
        model_name: str | None = None,
        status="pending",
        multitask_strategy="reject",
        metadata=None,
        kwargs=None,
        error=None,
        created_at=None,
        follow_up_to_run_id=None,
    ):
        """Insert or update a run row.

        ``RunManager`` retries ``put`` after transient SQLite failures.  Making
        this operation idempotent prevents a successful-but-unacknowledged first
        commit from turning the retry into a primary-key failure.
        """
        resolved_user_id = resolve_user_id(user_id, method_name="RunRepository.put")
        now = datetime.now(UTC)
        created = datetime.fromisoformat(created_at) if created_at else now
        values = {
            "thread_id": thread_id,
            "assistant_id": assistant_id,
            "user_id": resolved_user_id,
            "model_name": self._normalize_model_name(model_name),
            "status": status,
            "multitask_strategy": multitask_strategy,
            "metadata_json": self._safe_json(metadata) or {},
            "kwargs_json": self._safe_json(kwargs) or {},
            "error": error,
            "follow_up_to_run_id": follow_up_to_run_id,
            "updated_at": now,
        }
        async with self._sf() as session:
            row = await session.get(RunRow, run_id)
            if row is None:
                session.add(RunRow(run_id=run_id, created_at=created, **values))
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            await session.commit()

    async def get(
        self,
        run_id,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ):
        resolved_user_id = resolve_user_id(user_id, method_name="RunRepository.get")
        async with self._sf() as session:
            row = await session.get(RunRow, run_id)
            if row is None:
                return None
            if resolved_user_id is not None and row.user_id != resolved_user_id:
                return None
            return self._row_to_dict(row)

    async def list_by_thread(
        self,
        thread_id,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
        limit=100,
    ):
        resolved_user_id = resolve_user_id(user_id, method_name="RunRepository.list_by_thread")
        stmt = select(RunRow).where(RunRow.thread_id == thread_id)
        if resolved_user_id is not None:
            stmt = stmt.where(RunRow.user_id == resolved_user_id)
        stmt = stmt.order_by(RunRow.created_at.desc()).limit(limit)
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [self._row_to_dict(r) for r in result.scalars()]

    async def update_status(self, run_id, status, *, error=None) -> bool:
        values: dict[str, Any] = {"status": status, "updated_at": datetime.now(UTC)}
        if error is not None:
            values["error"] = error
        async with self._sf() as session:
            result = await session.execute(update(RunRow).where(RunRow.run_id == run_id).values(**values))
            await session.commit()
            return result.rowcount != 0

    async def update_model_name(self, run_id, model_name):
        async with self._sf() as session:
            await session.execute(update(RunRow).where(RunRow.run_id == run_id).values(model_name=self._normalize_model_name(model_name), updated_at=datetime.now(UTC)))
            await session.commit()

    async def delete(
        self,
        run_id,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ):
        resolved_user_id = resolve_user_id(user_id, method_name="RunRepository.delete")
        async with self._sf() as session:
            row = await session.get(RunRow, run_id)
            if row is None:
                return
            if resolved_user_id is not None and row.user_id != resolved_user_id:
                return
            await session.delete(row)
            await session.commit()

    async def list_pending(self, *, before=None):
        if before is None:
            before_dt = datetime.now(UTC)
        elif isinstance(before, datetime):
            before_dt = before
        else:
            before_dt = datetime.fromisoformat(before)
        stmt = select(RunRow).where(RunRow.status == "pending", RunRow.created_at <= before_dt).order_by(RunRow.created_at.asc())
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [self._row_to_dict(r) for r in result.scalars()]

    async def list_inflight(self, *, before=None):
        """Return persisted active runs for startup recovery."""
        if before is None:
            before_dt = datetime.now(UTC)
        elif isinstance(before, datetime):
            before_dt = before
        else:
            before_dt = datetime.fromisoformat(before)
        stmt = (
            select(RunRow)
            .where(
                RunRow.status.in_(("pending", "running")),
                RunRow.created_at <= before_dt,
            )
            .order_by(RunRow.created_at.asc())
        )
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [self._row_to_dict(r) for r in result.scalars()]

    async def update_run_completion(
        self,
        run_id: str,
        *,
        status: str,
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
        total_tokens: int = 0,
        llm_call_count: int = 0,
        lead_agent_tokens: int = 0,
        subagent_tokens: int = 0,
        middleware_tokens: int = 0,
        token_usage_by_model: dict[str, dict[str, int]] | None = None,
        image_generation_count: int = 0,
        message_count: int = 0,
        last_ai_message: str | None = None,
        first_human_message: str | None = None,
        error: str | None = None,
    ) -> bool:
        """Update status + token usage + convenience fields on run completion.

        Returns ``False`` when no run row matched the requested ``run_id``.
        """
        async with self._sf() as session:
            row = await session.get(RunRow, run_id)
            if row is None:
                return False
            period_start = await self._lock_quota_period(session, row.user_id)
            await session.refresh(row)
            await self._apply_model_quota_delta(
                session,
                row,
                total_tokens=total_tokens,
                llm_call_count=llm_call_count,
                period_start=period_start,
            )
            row.status = status
            for key, value in {
                "total_input_tokens": total_input_tokens,
                "total_output_tokens": total_output_tokens,
                "total_tokens": total_tokens,
                "llm_call_count": llm_call_count,
                "lead_agent_tokens": lead_agent_tokens,
                "subagent_tokens": subagent_tokens,
                "middleware_tokens": middleware_tokens,
                "image_generation_count": image_generation_count,
                "message_count": message_count,
            }.items():
                setattr(row, key, max(int(getattr(row, key) or 0), int(value or 0)))
            if token_usage_by_model is not None:
                row.token_usage_by_model = self._safe_json(token_usage_by_model) or {}
            if last_ai_message is not None:
                row.last_ai_message = last_ai_message[:2000]
            if first_human_message is not None:
                row.first_human_message = first_human_message[:2000]
            if error is not None:
                row.error = error
            row.updated_at = datetime.now(UTC)
            await session.commit()
            return True

    async def update_run_progress(
        self,
        run_id: str,
        *,
        total_input_tokens: int | None = None,
        total_output_tokens: int | None = None,
        total_tokens: int | None = None,
        llm_call_count: int | None = None,
        lead_agent_tokens: int | None = None,
        subagent_tokens: int | None = None,
        middleware_tokens: int | None = None,
        token_usage_by_model: dict[str, dict[str, int]] | None = None,
        image_generation_count: int | None = None,
        message_count: int | None = None,
        last_ai_message: str | None = None,
        first_human_message: str | None = None,
    ) -> None:
        """Update token usage + convenience fields while a run is still active."""
        async with self._sf() as session:
            row = await session.get(RunRow, run_id)
            if row is None or row.status != "running":
                return
            period_start = await self._lock_quota_period(session, row.user_id)
            await session.refresh(row)
            if row.status != "running":
                return
            await self._apply_model_quota_delta(
                session,
                row,
                total_tokens=total_tokens,
                llm_call_count=llm_call_count,
                period_start=period_start,
            )
            optional_counters = {
                "total_input_tokens": total_input_tokens,
                "total_output_tokens": total_output_tokens,
                "total_tokens": total_tokens,
                "llm_call_count": llm_call_count,
                "lead_agent_tokens": lead_agent_tokens,
                "subagent_tokens": subagent_tokens,
                "middleware_tokens": middleware_tokens,
                "image_generation_count": image_generation_count,
                "message_count": message_count,
            }
            for key, value in optional_counters.items():
                if value is not None:
                    setattr(row, key, max(int(getattr(row, key) or 0), int(value)))
            if token_usage_by_model is not None:
                row.token_usage_by_model = self._safe_json(token_usage_by_model) or {}
            if last_ai_message is not None:
                row.last_ai_message = last_ai_message[:2000]
            if first_human_message is not None:
                row.first_human_message = first_human_message[:2000]
            row.updated_at = datetime.now(UTC)
            await session.commit()

    @staticmethod
    def _current_period_start() -> datetime:
        local_now = datetime.now(ZoneInfo("Asia/Shanghai"))
        return datetime(local_now.year, local_now.month, 1, tzinfo=local_now.tzinfo).astimezone(UTC)

    async def _lock_quota_period(self, session: AsyncSession, user_id: str | None) -> datetime | None:
        if user_id is None:
            return None
        period_start = self._current_period_start()
        # The no-op update serializes progress/completion writers on the same
        # user-period row in both SQLite and Postgres before deltas are read.
        await session.execute(
            update(UserQuotaPeriodRow)
            .where(
                UserQuotaPeriodRow.user_id == user_id,
                UserQuotaPeriodRow.period == "monthly",
                UserQuotaPeriodRow.period_start == period_start,
            )
            .values(updated_at=UserQuotaPeriodRow.updated_at)
        )
        return period_start

    async def _apply_model_quota_delta(
        self,
        session: AsyncSession,
        row: RunRow,
        *,
        total_tokens: int | None,
        llm_call_count: int | None,
        period_start: datetime | None,
    ) -> None:
        if row.user_id is None or period_start is None:
            return
        token_delta = max(0, int(total_tokens or 0) - int(row.total_tokens or 0)) if total_tokens is not None else 0
        request_delta = max(0, int(llm_call_count or 0) - int(row.llm_call_count or 0)) if llm_call_count is not None else 0
        if token_delta == 0 and request_delta == 0:
            return
        await session.execute(
            update(UserQuotaPeriodRow)
            .where(
                UserQuotaPeriodRow.user_id == row.user_id,
                UserQuotaPeriodRow.period == "monthly",
                UserQuotaPeriodRow.period_start == period_start,
            )
            .values(
                model_tokens_used=UserQuotaPeriodRow.model_tokens_used + token_delta,
                model_requests_used=UserQuotaPeriodRow.model_requests_used + request_delta,
                updated_at=datetime.now(UTC),
            )
        )

    async def aggregate_tokens_by_thread(self, thread_id: str, *, include_active: bool = False) -> dict[str, Any]:
        """Aggregate token usage for a thread.

        ``by_model`` is reduced in Python from each row's ``token_usage_by_model``
        JSON column so subagent / middleware tokens land on the model that
        actually produced them (issue #3645). Rows written before that column
        existed fall back to ``RunRow.model_name`` + ``RunRow.total_tokens``,
        preserving the legacy lead-only behavior instead of dropping the data.

        Headline totals (``total_tokens``, ``total_input_tokens``,
        ``total_output_tokens``) and the ``by_caller`` bucket are summed from
        their own columns and are therefore unaffected by the JSON column being
        empty.
        """
        statuses = ("success", "error", "running") if include_active else ("success", "error")
        _completed = RunRow.status.in_(statuses)
        _thread = RunRow.thread_id == thread_id

        stmt = select(
            RunRow.model_name,
            RunRow.total_tokens,
            RunRow.total_input_tokens,
            RunRow.total_output_tokens,
            RunRow.lead_agent_tokens,
            RunRow.subagent_tokens,
            RunRow.middleware_tokens,
            RunRow.token_usage_by_model,
        ).where(_thread, _completed)

        async with self._sf() as session:
            rows = (await session.execute(stmt)).all()

        total_tokens = total_input = total_output = total_runs = 0
        lead_agent = subagent = middleware = 0
        by_model: dict[str, dict] = {}
        for r in rows:
            total_runs += 1
            total_tokens += r.total_tokens
            total_input += r.total_input_tokens
            total_output += r.total_output_tokens
            lead_agent += r.lead_agent_tokens
            subagent += r.subagent_tokens
            middleware += r.middleware_tokens

            # ``or {}`` covers rows written before ``token_usage_by_model``
            # existed (the column is NULL on a manual ALTER ADD COLUMN without
            # backfill); fresh rows always carry the journal-produced dict.
            usage_by_model = r.token_usage_by_model or {}
            if usage_by_model:
                for model, usage in usage_by_model.items():
                    entry = by_model.setdefault(model, {"tokens": 0, "runs": 0})
                    entry["tokens"] += usage.get("total_tokens", 0)
                    entry["runs"] += 1
            else:
                model = r.model_name or "unknown"
                entry = by_model.setdefault(model, {"tokens": 0, "runs": 0})
                entry["tokens"] += r.total_tokens
                entry["runs"] += 1

        return {
            "total_tokens": total_tokens,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_runs": total_runs,
            "by_model": by_model,
            "by_caller": {
                "lead_agent": lead_agent,
                "subagent": subagent,
                "middleware": middleware,
            },
        }
