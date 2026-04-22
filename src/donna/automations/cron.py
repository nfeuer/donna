"""CronScheduleCalculator — thin wrapper over croniter for next-run arithmetic."""

from __future__ import annotations

from datetime import UTC, datetime

from croniter import CroniterBadCronError, croniter


class InvalidCronExpressionError(ValueError):
    """Raised when the cron expression cannot be parsed."""


class CronScheduleCalculator:
    def next_run(self, *, expression: str, after: datetime) -> datetime:
        """Compute the next execution time strictly AFTER *after* (timezone-aware UTC)."""
        if after.tzinfo is None:
            after = after.replace(tzinfo=UTC)
        try:
            it = croniter(expression, after)
        except (CroniterBadCronError, ValueError, KeyError) as exc:
            raise InvalidCronExpressionError(
                f"invalid cron expression {expression!r}: {exc}"
            ) from exc
        nxt = it.get_next(datetime)
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=UTC)
        return nxt.astimezone(UTC)
