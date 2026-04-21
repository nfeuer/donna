"""cost_summary — thin read-only wrapper around CostTracker.

Registered into DEFAULT_TOOL_REGISTRY via donna.skills.tools.register_default_tools.
Only registered when a CostTracker handle is available at boot.

Read-only by construction.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import structlog

logger = structlog.get_logger()


class CostSummaryError(Exception):
    """Raised when a cost_summary invocation fails."""


async def cost_summary(
    *,
    client: Any,
    scope: str = "daily",
    for_date: str | None = None,
    year: int | None = None,
    month: int | None = None,
    exclude_task_types: list[str] | None = None,
) -> dict:
    """Return total spend + per-task-type breakdown for the requested scope.

    ``scope="daily"`` uses ``for_date`` (ISO ``YYYY-MM-DD``; defaults to today).
    ``scope="monthly"`` uses ``year`` and ``month`` (defaults to current UTC month).
    """
    if scope == "daily":
        try:
            day = date.fromisoformat(for_date) if for_date else None
        except ValueError as exc:
            raise CostSummaryError(f"invalid for_date: {exc}") from exc
        try:
            summary = await client.get_daily_cost(
                for_date=day, exclude_task_types=exclude_task_types,
            )
        except Exception as exc:
            logger.warning(
                "cost_summary_daily_failed",
                for_date=for_date, error=str(exc),
            )
            raise CostSummaryError(f"get_daily_cost: {exc}") from exc
    elif scope == "monthly":
        if exclude_task_types:
            raise CostSummaryError(
                "exclude_task_types is not supported for scope='monthly'"
            )
        try:
            summary = await client.get_monthly_cost(year=year, month=month)
        except Exception as exc:
            logger.warning(
                "cost_summary_monthly_failed",
                year=year, month=month, error=str(exc),
            )
            raise CostSummaryError(f"get_monthly_cost: {exc}") from exc
    else:
        raise CostSummaryError(
            f"scope must be 'daily' or 'monthly', got {scope!r}"
        )

    return {
        "ok": True,
        "scope": scope,
        "total_usd": float(summary.total_usd),
        "call_count": int(summary.call_count),
        "breakdown": dict(summary.breakdown),
    }
