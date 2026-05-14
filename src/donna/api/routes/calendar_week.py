from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from fastapi import Query, Request

from donna.api.auth import CurrentUser, user_router
from donna.tasks.db_models import TaskStatus

logger = structlog.get_logger()
router = user_router()


def _week_bounds(ref: date_type, tz: ZoneInfo) -> tuple[datetime, datetime]:
    monday = ref - timedelta(days=ref.weekday())
    sunday = monday + timedelta(days=6)
    start = datetime.combine(monday, time.min, tzinfo=tz)
    end = datetime.combine(sunday, time(23, 59, 59), tzinfo=tz)
    return start, end


def _is_all_day(start: datetime, end: datetime) -> bool:
    return (
        start.hour == 0
        and start.minute == 0
        and end.hour == 0
        and end.minute == 0
        and (end - start).days >= 1
    )


@router.get("/week")
async def get_calendar_week(
    request: Request,
    user_id: CurrentUser,
    ref_date: str | None = Query(default=None, alias="date"),
) -> dict[str, Any]:
    tz_name = getattr(request.app.state, "calendar_timezone", "UTC")
    tz = ZoneInfo(tz_name)

    ref = date_type.fromisoformat(ref_date) if ref_date else datetime.now(tz=tz).date()

    week_start, week_end = _week_bounds(ref, tz)

    events: list[dict[str, Any]] = []
    warnings: list[str] = []

    cal_client = getattr(request.app.state, "calendar_client", None)
    if cal_client is not None:
        calendar_ids: list[str] = getattr(request.app.state, "calendar_ids", [])
        for cal_id in calendar_ids:
            try:
                gcal_events = await cal_client.list_events(cal_id, week_start, week_end)
                for ev in gcal_events:
                    events.append({
                        "id": f"gcal_{ev.event_id}",
                        "title": ev.summary,
                        "start": ev.start.isoformat(),
                        "end": ev.end.isoformat(),
                        "source": "google",
                        "calendar_id": cal_id,
                        "all_day": _is_all_day(ev.start, ev.end),
                    })
            except Exception:
                logger.warning("calendar_fetch_failed", calendar_id=cal_id, exc_info=True)
                warnings.append(f"calendar_fetch_failed:{cal_id}")
    else:
        warnings.append("google_calendar_unavailable")

    db = request.app.state.db
    donna_tasks = []
    for status in (TaskStatus.SCHEDULED, TaskStatus.DONE):
        donna_tasks.extend(await db.list_tasks(user_id=user_id, status=status))
    for task in donna_tasks:
        if not task.scheduled_start:
            continue
        try:
            start = datetime.fromisoformat(task.scheduled_start)
            if start.tzinfo is None:
                start = start.replace(tzinfo=tz)
            if week_start <= start <= week_end:
                duration = task.estimated_duration or 3600
                end_dt = start + timedelta(seconds=duration)
                events.append({
                    "id": f"donna_{task.id}",
                    "title": task.title,
                    "start": start.isoformat(),
                    "end": end_dt.isoformat(),
                    "source": "donna",
                    "priority": task.priority,
                    "domain": task.domain,
                    "status": task.status,
                    "all_day": False,
                })
        except ValueError:
            continue

    events.sort(key=lambda e: e["start"])

    result: dict[str, Any] = {
        "user_id": user_id,
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "events": events,
        "count": len(events),
    }
    if warnings:
        result["warnings"] = warnings
    return result
