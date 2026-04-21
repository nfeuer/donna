"""calendar_read — thin read-only wrapper around GoogleCalendarClient.list_events.

Registered into DEFAULT_TOOL_REGISTRY via donna.skills.tools.register_default_tools.
Only registered when a GoogleCalendarClient is available at boot.

Read-only by construction: never imports or references create/update/delete.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog

logger = structlog.get_logger()


class CalendarReadError(Exception):
    """Raised when a calendar_read invocation fails."""


async def calendar_read(
    *,
    client: Any,
    time_min: str,
    time_max: str,
    calendar_id: str = "primary",
) -> dict[str, Any]:
    """List events in [time_min, time_max] on *calendar_id*.

    ``time_min`` / ``time_max`` are ISO-8601 datetimes (naive is treated as UTC).
    """
    if not time_min or not time_max:
        raise CalendarReadError("time_min and time_max must be non-empty ISO-8601 datetimes")
    try:
        start = _parse_iso(time_min)
        end = _parse_iso(time_max)
    except ValueError as exc:
        raise CalendarReadError(f"invalid datetime: {exc}") from exc
    if end <= start:
        raise CalendarReadError("time_max must be greater than time_min")

    try:
        events = await client.list_events(
            calendar_id=calendar_id, time_min=start, time_max=end,
        )
    except Exception as exc:
        logger.warning(
            "calendar_read_failed",
            calendar_id=calendar_id,
            time_min=time_min,
            time_max=time_max,
            error=str(exc),
        )
        raise CalendarReadError(f"list_events: {exc}") from exc

    out = []
    for e in events:
        out.append({
            "event_id": e.event_id,
            "summary": e.summary,
            "start": e.start.isoformat() if e.start is not None else None,
            "end": e.end.isoformat() if e.end is not None else None,
            "donna_managed": bool(e.donna_managed),
            "donna_task_id": e.donna_task_id,
        })
    return {"ok": True, "events": out}


def _parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 string; accept trailing 'Z' for UTC."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)
