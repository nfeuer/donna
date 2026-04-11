"""Tests for budget separation between internal and external LLM calls."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from donna.cost.tracker import CostTracker


class TestCostTrackerExclusion:
    async def test_get_daily_cost_excludes_task_types(self) -> None:
        conn = AsyncMock()

        # Mock: total including external = $25, without external = $15
        cursor_total = AsyncMock()
        cursor_total.fetchone = AsyncMock(return_value=(15.0, 50))
        cursor_breakdown = AsyncMock()
        cursor_breakdown.fetchall = AsyncMock(return_value=[
            ("parse_task", 10.0),
            ("generate_digest", 5.0),
        ])
        conn.execute = AsyncMock(side_effect=[cursor_total, cursor_breakdown])

        tracker = CostTracker(conn)
        result = await tracker.get_daily_cost(
            exclude_task_types=["external_llm_call"]
        )

        # Verify the SQL included the exclusion
        first_call_sql = conn.execute.call_args_list[0][0][0]
        assert "task_type NOT IN" in first_call_sql
        assert result.total_usd == 15.0

    async def test_get_daily_cost_no_exclusion_by_default(self) -> None:
        conn = AsyncMock()
        cursor_total = AsyncMock()
        cursor_total.fetchone = AsyncMock(return_value=(25.0, 80))
        cursor_breakdown = AsyncMock()
        cursor_breakdown.fetchall = AsyncMock(return_value=[])
        conn.execute = AsyncMock(side_effect=[cursor_total, cursor_breakdown])

        tracker = CostTracker(conn)
        result = await tracker.get_daily_cost()

        first_call_sql = conn.execute.call_args_list[0][0][0]
        assert "NOT IN" not in first_call_sql
        assert result.total_usd == 25.0
