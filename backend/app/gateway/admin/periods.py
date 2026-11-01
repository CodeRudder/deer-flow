"""Time-window parsing for administrator usage and quota APIs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException

APP_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class PeriodWindow:
    period: str
    period_start: datetime
    period_end: datetime
    label: str


def usage_range_window(range_value: str = "month", *, start: str | None = None, end: str | None = None) -> PeriodWindow:
    now = datetime.now(APP_TZ)
    if range_value == "day":
        local_start = datetime(now.year, now.month, now.day, tzinfo=APP_TZ)
        local_end = local_start + timedelta(days=1)
        label = local_start.strftime("%Y-%m-%d")
    elif range_value == "week":
        local_start = datetime(now.year, now.month, now.day, tzinfo=APP_TZ) - timedelta(days=now.weekday())
        local_end = local_start + timedelta(days=7)
        label = f"{local_start.date().isoformat()}~{(local_end - timedelta(days=1)).date().isoformat()}"
    elif range_value == "custom":
        if not start or not end:
            raise HTTPException(status_code=400, detail="start and end are required when range=custom")
        try:
            start_date = datetime.fromisoformat(start).date()
            end_date = datetime.fromisoformat(end).date()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="start/end must be YYYY-MM-DD") from exc
        if end_date < start_date:
            raise HTTPException(status_code=400, detail="end must be greater than or equal to start")
        local_start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=APP_TZ)
        local_end = datetime(end_date.year, end_date.month, end_date.day, tzinfo=APP_TZ) + timedelta(days=1)
        label = f"{start_date.isoformat()}~{end_date.isoformat()}"
    else:
        local_start = datetime(now.year, now.month, 1, tzinfo=APP_TZ)
        local_end = datetime(now.year + (now.month == 12), 1 if now.month == 12 else now.month + 1, 1, tzinfo=APP_TZ)
        label = local_start.strftime("%Y-%m")

    return PeriodWindow(
        period=range_value,
        period_start=local_start.astimezone(UTC),
        period_end=local_end.astimezone(UTC),
        label=label,
    )


def period_response(window: PeriodWindow) -> dict[str, str]:
    return {
        "period": window.period,
        "label": window.label,
        "period_start": window.period_start.isoformat(),
        "period_end": window.period_end.isoformat(),
        "timezone": "Asia/Shanghai",
    }
