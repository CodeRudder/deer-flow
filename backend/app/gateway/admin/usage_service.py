"""Read-only administrator usage statistics."""

from __future__ import annotations

from datetime import UTC
from typing import Any

from sqlalchemy import and_, case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.gateway.admin.display import format_user_input
from app.gateway.admin.periods import APP_TZ, PeriodWindow, period_response
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.user.model import UserRow


def _session_title(first_human_message: str | None, thread_id: str) -> str:
    return format_user_input(first_human_message, thread_id)


class AdminUsageService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def usage_summary(self, *, window: PeriodWindow) -> dict[str, Any]:
        async with self._sf() as session:
            totals_stmt = select(
                func.coalesce(func.sum(RunRow.total_tokens), 0),
                func.coalesce(func.sum(RunRow.total_input_tokens), 0),
                func.coalesce(func.sum(RunRow.total_output_tokens), 0),
                func.coalesce(func.sum(RunRow.llm_call_count), 0),
                func.coalesce(func.sum(RunRow.image_generation_count), 0),
                func.coalesce(func.sum(RunRow.video_generation_count), 0),
                func.count(RunRow.run_id),
                func.count(func.distinct(RunRow.user_id)),
                func.coalesce(func.sum(case((RunRow.status.in_(("pending", "running")), 1), else_=0)), 0),
            ).where(_runs_in_window(window))
            tokens, input_tokens, output_tokens, requests, images, videos, runs, active_users, running = (await session.execute(totals_stmt)).one()
        return {
            "period": period_response(window),
            "total_tokens": int(tokens or 0),
            "total_input_tokens": int(input_tokens or 0),
            "total_output_tokens": int(output_tokens or 0),
            "model_requests": int(requests or 0),
            "image_generations": int(images or 0),
            "video_generations": int(videos or 0),
            "run_count": int(runs or 0),
            "active_users": int(active_users or 0),
            "running_runs": int(running or 0),
        }

    async def usage_sessions(self, *, window: PeriodWindow, metric: str, limit: int) -> dict[str, Any]:
        metric_expr = {
            "requests": func.coalesce(func.sum(RunRow.llm_call_count), 0),
            "images": func.coalesce(func.sum(RunRow.image_generation_count), 0),
            "videos": func.coalesce(func.sum(RunRow.video_generation_count), 0),
        }.get(metric, func.coalesce(func.sum(RunRow.total_tokens), 0))
        async with self._sf() as session:
            rows = (
                await session.execute(
                    select(
                        RunRow.thread_id,
                        RunRow.user_id,
                        UserRow.email,
                        func.coalesce(func.max(RunRow.first_human_message), ""),
                        metric_expr.label("value"),
                    )
                    .join(UserRow, UserRow.id == RunRow.user_id, isouter=True)
                    .where(_runs_in_window(window))
                    .group_by(RunRow.thread_id, RunRow.user_id, UserRow.email)
                    .order_by(desc("value"))
                    .limit(min(max(1, limit), 50))
                )
            ).all()
        return {
            "period": period_response(window),
            "metric": metric,
            "items": [
                {
                    "thread_id": thread_id,
                    "user_id": user_id,
                    "email": email or "unknown",
                    "title": _session_title(first_human_message, thread_id),
                    "value": int(value or 0),
                }
                for thread_id, user_id, email, first_human_message, value in rows
            ],
        }

    async def usage_users(self, *, window: PeriodWindow, limit: int) -> dict[str, Any]:
        async with self._sf() as session:
            rows = (
                await session.execute(
                    select(
                        RunRow.user_id,
                        UserRow.email,
                        func.coalesce(func.sum(RunRow.total_tokens), 0),
                        func.coalesce(func.sum(RunRow.llm_call_count), 0),
                        func.coalesce(func.sum(RunRow.image_generation_count), 0),
                        func.coalesce(func.sum(RunRow.video_generation_count), 0),
                    )
                    .join(UserRow, UserRow.id == RunRow.user_id, isouter=True)
                    .where(_runs_in_window(window), RunRow.user_id.is_not(None))
                    .group_by(RunRow.user_id, UserRow.email)
                )
            ).all()

        users = []
        for user_id, email, tokens, requests, images, videos in rows:
            users.append(
                {
                    "user_id": user_id,
                    "email": email or user_id,
                    "tokens": int(tokens or 0),
                    "requests": int(requests or 0),
                    "images": int(images or 0),
                    "videos": int(videos or 0),
                }
            )

        size = min(max(1, limit), 50)
        rankings: dict[str, list[dict[str, Any]]] = {}
        for metric in ("tokens", "requests", "images", "videos"):
            ordered = sorted(users, key=lambda item: (-int(item[metric]), str(item["user_id"])))[:size]
            rankings[metric] = [
                {
                    "rank": index,
                    "user_id": item["user_id"],
                    "email": item["email"],
                    "value": item[metric],
                }
                for index, item in enumerate(ordered, start=1)
            ]
        return {"period": period_response(window), "rankings": rankings}

    async def usage_models(self, *, window: PeriodWindow) -> dict[str, Any]:
        async with self._sf() as session:
            rows = (await session.execute(select(RunRow.model_name, RunRow.total_tokens, RunRow.token_usage_by_model).where(_runs_in_window(window)))).all()
        by_model: dict[str, dict[str, int]] = {}
        models_with_request_data: set[str] = set()
        for model_name, total_tokens, usage_by_model in rows:
            usage = usage_by_model or {}
            if usage:
                for model, data in usage.items():
                    model_key = model or "unknown"
                    entry = by_model.setdefault(model_key, {"tokens": 0, "requests": 0, "runs": 0})
                    entry["tokens"] = int(entry["tokens"] or 0) + int(data.get("total_tokens") or 0)
                    call_count = data.get("call_count")
                    if call_count is not None:
                        entry["requests"] += int(call_count)
                        models_with_request_data.add(model_key)
                    entry["runs"] = int(entry["runs"] or 0) + 1
            else:
                entry = by_model.setdefault(model_name or "unknown", {"tokens": 0, "requests": 0, "runs": 0})
                entry["tokens"] = int(entry["tokens"] or 0) + int(total_tokens or 0)
                entry["runs"] = int(entry["runs"] or 0) + 1
        total = sum(int(item["tokens"] or 0) for item in by_model.values()) or 1
        items = [
            {
                "model": model,
                "type": "LLM",
                "requests": data["requests"] if model in models_with_request_data else None,
                "tokens": int(data["tokens"] or 0),
                "runs": int(data["runs"] or 0),
                "share": int(data["tokens"] or 0) / total,
            }
            for model, data in by_model.items()
        ]
        items.sort(key=lambda item: int(item["tokens"]), reverse=True)
        return {"period": period_response(window), "items": items}

    async def usage_trends(self, *, window: PeriodWindow) -> dict[str, Any]:
        async with self._sf() as session:
            rows = (await session.execute(select(RunRow.created_at, RunRow.total_tokens, RunRow.llm_call_count, RunRow.image_generation_count, RunRow.video_generation_count).where(_runs_in_window(window)))).all()
        buckets: dict[str, dict[str, int]] = {}
        for created_at, tokens, requests, images, videos in rows:
            local = created_at.astimezone(APP_TZ) if created_at.tzinfo else created_at.replace(tzinfo=UTC).astimezone(APP_TZ)
            key = local.strftime("%Y-%m-%d %H:00") if window.period == "day" else local.strftime("%Y-%m-%d")
            bucket = buckets.setdefault(key, {"tokens": 0, "requests": 0, "images": 0, "videos": 0})
            bucket["tokens"] += int(tokens or 0)
            bucket["requests"] += int(requests or 0)
            bucket["images"] += int(images or 0)
            bucket["videos"] += int(videos or 0)
        return {
            "period": period_response(window),
            "bucket": "hour" if window.period == "day" else "day",
            "items": [{"label": key, **value} for key, value in sorted(buckets.items())],
        }


def _runs_in_window(window: PeriodWindow):
    return and_(RunRow.created_at >= window.period_start, RunRow.created_at < window.period_end)
