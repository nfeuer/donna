"""Cost aggregation from the invocation_log table.

Queries cost_usd from invocation_log for daily/monthly aggregations,
grouped by task_type or model_alias. Used by BudgetGuard and the
morning digest for cost reporting.

See docs/model-layer.md Section 4.3 and config/donna_models.yaml for
budget thresholds.
"""

from __future__ import annotations

import calendar as _calendar
import dataclasses
from datetime import date, datetime, timedelta
from typing import Any

import aiosqlite
import structlog

logger = structlog.get_logger()


@dataclasses.dataclass(frozen=True)
class CostSummary:
    """Aggregated cost data for a time window."""

    total_usd: float
    call_count: int
    breakdown: dict[str, float]  # keyed by task_type or model_alias


class CostTracker:
    """Queries invocation_log for cost aggregations.

    All methods default date arguments to today / current month when
    None is passed.
    """

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def get_daily_cost(
        self,
        for_date: date | None = None,
        exclude_task_types: list[str] | None = None,
    ) -> CostSummary:
        """Total cost and per-task-type breakdown for a single day.

        Args:
            for_date: The date to query. Defaults to today (UTC).
            exclude_task_types: Task types to exclude from the cost sum.
        """
        target = for_date or date.today()
        day_start = datetime(target.year, target.month, target.day, 0, 0, 0).isoformat()
        day_end = datetime(target.year, target.month, target.day, 23, 59, 59, 999999).isoformat()

        total, count = await self._sum_range(day_start, day_end, exclude_task_types)
        breakdown = await self._breakdown_by_task_type(day_start, day_end, exclude_task_types)

        logger.debug("cost_tracker_daily", date=str(target), total_usd=total, call_count=count)
        return CostSummary(total_usd=total, call_count=count, breakdown=breakdown)

    async def get_monthly_cost(
        self,
        year: int | None = None,
        month: int | None = None,
    ) -> CostSummary:
        """Total cost and per-task-type breakdown for a calendar month.

        Args:
            year: 4-digit year. Defaults to current year (UTC).
            month: 1–12. Defaults to current month (UTC).
        """
        today = date.today()
        y = year or today.year
        m = month or today.month
        _, last_day = _calendar.monthrange(y, m)

        month_start = datetime(y, m, 1, 0, 0, 0).isoformat()
        month_end = datetime(y, m, last_day, 23, 59, 59, 999999).isoformat()

        total, count = await self._sum_range(month_start, month_end)
        breakdown = await self._breakdown_by_task_type(month_start, month_end)

        logger.debug(
            "cost_tracker_monthly",
            year=y,
            month=m,
            total_usd=total,
            call_count=count,
        )
        return CostSummary(total_usd=total, call_count=count, breakdown=breakdown)

    async def get_cost_by_task_type(
        self, start: date, end: date
    ) -> dict[str, float]:
        """Cost grouped by task_type for a date range (inclusive)."""
        start_str = datetime(start.year, start.month, start.day, 0, 0, 0).isoformat()
        end_str = datetime(end.year, end.month, end.day, 23, 59, 59, 999999).isoformat()
        return await self._breakdown_by_task_type(start_str, end_str)

    async def get_cost_by_agent(
        self, start: date, end: date
    ) -> dict[str, float]:
        """Cost grouped by model_alias for a date range (inclusive)."""
        start_str = datetime(start.year, start.month, start.day, 0, 0, 0).isoformat()
        end_str = datetime(end.year, end.month, end.day, 23, 59, 59, 999999).isoformat()

        cursor = await self._conn.execute(
            """SELECT model_alias, SUM(cost_usd)
               FROM invocation_log
               WHERE timestamp >= ? AND timestamp <= ?
               GROUP BY model_alias""",
            (start_str, end_str),
        )
        rows = await cursor.fetchall()
        return {row[0]: float(row[1]) for row in rows}

    async def get_projected_monthly_spend(self) -> float:
        """Estimate full-month spend based on a 7-day rolling average.

        Returns daily_avg × days_in_month.
        """
        today = date.today()
        window_start = today - timedelta(days=6)  # 7-day window including today

        start_str = datetime(
            window_start.year, window_start.month, window_start.day, 0, 0, 0,
        ).isoformat()
        end_str = datetime(today.year, today.month, today.day, 23, 59, 59, 999999).isoformat()

        total, _ = await self._sum_range(start_str, end_str)
        daily_avg = total / 7.0

        _, days_in_month = _calendar.monthrange(today.year, today.month)
        projected = daily_avg * days_in_month

        logger.debug(
            "cost_tracker_projected",
            daily_avg=daily_avg,
            days_in_month=days_in_month,
            projected_usd=projected,
        )
        return projected

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _sum_range(
        self,
        start: str,
        end: str,
        exclude_task_types: list[str] | None = None,
    ) -> tuple[float, int]:
        """Return (total_cost, call_count) for a timestamp range."""
        where = "timestamp >= ? AND timestamp <= ?"
        params: list[Any] = [start, end]
        if exclude_task_types:
            placeholders = ", ".join("?" for _ in exclude_task_types)
            where += f" AND task_type NOT IN ({placeholders})"
            params.extend(exclude_task_types)
        cursor = await self._conn.execute(
            f"SELECT COALESCE(SUM(cost_usd), 0.0), COUNT(*) FROM invocation_log WHERE {where}",
            params,
        )
        row = await cursor.fetchone()
        if row is None:
            return 0.0, 0
        return float(row[0]), int(row[1])

    async def _breakdown_by_task_type(
        self,
        start: str,
        end: str,
        exclude_task_types: list[str] | None = None,
    ) -> dict[str, float]:
        """Cost grouped by task_type for a timestamp range."""
        where = "timestamp >= ? AND timestamp <= ?"
        params: list[Any] = [start, end]
        if exclude_task_types:
            placeholders = ", ".join("?" for _ in exclude_task_types)
            where += f" AND task_type NOT IN ({placeholders})"
            params.extend(exclude_task_types)
        cursor = await self._conn.execute(
            f"SELECT task_type, SUM(cost_usd) FROM invocation_log WHERE {where} GROUP BY task_type",
            params,
        )
        rows = await cursor.fetchall()
        return {row[0]: float(row[1]) for row in rows}
