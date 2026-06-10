"""CronScheduleCalculator — thin wrapper over croniter for next-run arithmetic."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from croniter import CroniterBadCronError, croniter


class InvalidCronExpressionError(ValueError):
    """Raised when the cron expression cannot be parsed."""


class CronScheduleCalculator:
    def __init__(self, tz: ZoneInfo | None = None) -> None:
        """Args:
        tz: Zone in which cron fields are interpreted. When None, fields are
            interpreted in UTC (legacy behavior).
        """
        self._tz = tz

    def next_run(self, *, expression: str, after: datetime) -> datetime:
        """Compute the next execution time strictly AFTER *after*.

        Cron fields are interpreted in the configured timezone (or UTC when
        none was set). The returned datetime is timezone-aware UTC. DST is
        honored because croniter advances over a tz-aware base time.
        """
        zone = self._tz or UTC
        if after.tzinfo is None:
            after = after.replace(tzinfo=UTC)
        local_after = after.astimezone(zone)
        try:
            it = croniter(expression, local_after)
        except (CroniterBadCronError, ValueError, KeyError) as exc:
            raise InvalidCronExpressionError(
                f"invalid cron expression {expression!r}: {exc}"
            ) from exc
        nxt = it.get_next(datetime)
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=zone)
        return nxt.astimezone(UTC)
