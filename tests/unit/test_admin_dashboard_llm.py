"""Tests for the /admin/dashboard/llm-gateway endpoint."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.api.routes.admin_dashboard import get_llm_gateway_analytics


def _make_request(rows_daily=None, rows_caller=None) -> MagicMock:
    """Build a mock request with a mock DB connection."""
    request = MagicMock()
    conn = AsyncMock()

    cursors = []

    daily_cursor = AsyncMock()
    daily_cursor.fetchall = AsyncMock(return_value=rows_daily or [])
    cursors.append(daily_cursor)

    caller_cursor = AsyncMock()
    caller_cursor.fetchall = AsyncMock(return_value=rows_caller or [])
    cursors.append(caller_cursor)

    conn.execute = AsyncMock(side_effect=cursors)
    request.app.state.db.connection = conn
    return request


class TestLLMGatewayAnalytics:
    async def test_returns_empty_when_no_data(self) -> None:
        request = _make_request()
        result = await get_llm_gateway_analytics(request, days=7)

        assert result["summary"]["total_calls"] == 0
        assert result["summary"]["unique_callers"] == 0
        assert result["time_series"] == []
        assert result["by_caller"] == []
        assert result["days"] == 7

    async def test_aggregates_daily_data(self) -> None:
        daily_rows = [
            ("2026-04-10", 30, 10, 2, 2100),
            ("2026-04-11", 25, 8, 1, 1900),
        ]
        caller_rows = [
            ("receipt-scanner", 12, 2340, 98000, 44000, 2, 0),
            ("home-inventory", 6, 1820, 32000, 18000, 1, 0),
        ]
        request = _make_request(rows_daily=daily_rows, rows_caller=caller_rows)
        result = await get_llm_gateway_analytics(request, days=7)

        assert result["summary"]["total_calls"] == 73
        assert result["summary"]["internal_calls"] == 55
        assert result["summary"]["external_calls"] == 18
        assert result["summary"]["total_interrupted"] == 3
        assert result["summary"]["unique_callers"] == 2
        assert len(result["time_series"]) == 2
        assert result["time_series"][0]["date"] == "2026-04-10"
        assert len(result["by_caller"]) == 2
        assert result["by_caller"][0]["caller"] == "receipt-scanner"
