"""Tests for invocation_log read tool handlers.

Covers query_invocations, get_invocation_detail, and query_invocation_stats.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.chat.tools import ToolContext, ToolResult
from donna.chat.tools.invocations import (
    get_invocation_detail,
    query_invocation_stats,
    query_invocations,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_INVOCATION_ROW: dict = {
    "id": "inv-001",
    "task_type": "parse_task",
    "model_alias": "claude-sonnet",
    "model_actual": "claude-sonnet-4-20250514",
    "tokens_in": 1000,
    "tokens_out": 200,
    "cost_usd": 0.003,
    "latency_ms": 500,
    "quality_score": 0.9,
    "timestamp": "2026-05-01T10:00:00Z",
    "has_error": False,
}

_DETAIL_ROW: dict = {
    **_INVOCATION_ROW,
    "input_hash": "abc123",
    "task_id": "task-001",
    "output": '{"title": "Buy milk"}',
    "is_shadow": False,
    "payload_path": None,
    "trace_id": "trace-abc",
    "user_id": "nick",
    "skill_id": None,
    "escalation_request_id": None,
    "estimated_tokens_in": 1480,
    "overflow_escalated": False,
}


def _make_ctx(rows: list[dict] | None = None, count: int | None = None) -> ToolContext:
    """Build a ToolContext with a mock DB.

    The mock execute_sql returns *rows* on the first call (data query) and a
    count row ``[{"count": count}]`` on the second call when count is given.
    When only rows is given, every call returns those rows.
    """
    db = MagicMock()
    if count is not None:
        db.execute_sql = AsyncMock(
            side_effect=[
                [{"count": count}],  # COUNT(*) query
                rows or [],           # data query
            ]
        )
    else:
        db.execute_sql = AsyncMock(return_value=rows or [])
    return ToolContext(db=db, user_id="nick", session_id="sess-1")


# ---------------------------------------------------------------------------
# TestQueryInvocations
# ---------------------------------------------------------------------------


class TestQueryInvocations:
    @pytest.mark.asyncio
    async def test_returns_tool_result(self) -> None:
        ctx = _make_ctx(rows=[_INVOCATION_ROW], count=1)
        result = await query_invocations({}, ctx)
        assert isinstance(result, ToolResult)
        assert result.total_count == 1
        assert len(result.results) == 1
        assert result.results[0]["id"] == "inv-001"

    @pytest.mark.asyncio
    async def test_applies_date_filters(self) -> None:
        ctx = _make_ctx(rows=[], count=0)
        await query_invocations(
            {"date_from": "2026-05-01", "date_to": "2026-05-15"},
            ctx,
        )
        # First call is the COUNT(*) query
        count_sql: str = ctx.db.execute_sql.call_args_list[0][0][0]
        assert "timestamp" in count_sql
        count_params: list = ctx.db.execute_sql.call_args_list[0][0][1]
        assert "2026-05-01" in count_params
        assert "2026-05-15" in count_params

    @pytest.mark.asyncio
    async def test_default_limit_25(self) -> None:
        ctx = _make_ctx(rows=[], count=0)
        await query_invocations({}, ctx)
        # Data query (second call) should include LIMIT 25
        data_sql: str = ctx.db.execute_sql.call_args_list[1][0][0]
        assert "LIMIT" in data_sql.upper()
        data_params: list = ctx.db.execute_sql.call_args_list[1][0][1]
        assert 25 in data_params

    @pytest.mark.asyncio
    async def test_limit_capped_at_100(self) -> None:
        ctx = _make_ctx(rows=[], count=0)
        await query_invocations({"limit": 999}, ctx)
        data_params: list = ctx.db.execute_sql.call_args_list[1][0][1]
        assert 100 in data_params

    @pytest.mark.asyncio
    async def test_filter_by_task_type(self) -> None:
        ctx = _make_ctx(rows=[], count=0)
        await query_invocations({"task_type": "parse_task"}, ctx)
        count_sql: str = ctx.db.execute_sql.call_args_list[0][0][0]
        assert "task_type" in count_sql
        count_params: list = ctx.db.execute_sql.call_args_list[0][0][1]
        assert "parse_task" in count_params

    @pytest.mark.asyncio
    async def test_filter_by_model(self) -> None:
        ctx = _make_ctx(rows=[], count=0)
        await query_invocations({"model": "claude-sonnet"}, ctx)
        count_sql: str = ctx.db.execute_sql.call_args_list[0][0][0]
        assert "model_alias" in count_sql

    @pytest.mark.asyncio
    async def test_filter_by_min_cost(self) -> None:
        ctx = _make_ctx(rows=[], count=0)
        await query_invocations({"min_cost": 0.01}, ctx)
        count_sql: str = ctx.db.execute_sql.call_args_list[0][0][0]
        assert "cost_usd" in count_sql

    @pytest.mark.asyncio
    async def test_filter_by_min_latency(self) -> None:
        ctx = _make_ctx(rows=[], count=0)
        await query_invocations({"min_latency": 200}, ctx)
        count_sql: str = ctx.db.execute_sql.call_args_list[0][0][0]
        assert "latency_ms" in count_sql

    @pytest.mark.asyncio
    async def test_filter_by_has_error(self) -> None:
        ctx = _make_ctx(rows=[], count=0)
        await query_invocations({"has_error": True}, ctx)
        count_sql: str = ctx.db.execute_sql.call_args_list[0][0][0]
        assert "has_error" in count_sql

    @pytest.mark.asyncio
    async def test_sort_by_cost_desc(self) -> None:
        ctx = _make_ctx(rows=[], count=0)
        await query_invocations({"sort": "cost", "sort_dir": "desc"}, ctx)
        data_sql: str = ctx.db.execute_sql.call_args_list[1][0][0]
        assert "cost_usd" in data_sql.lower()
        assert "desc" in data_sql.lower()

    @pytest.mark.asyncio
    async def test_sort_by_latency_asc(self) -> None:
        ctx = _make_ctx(rows=[], count=0)
        await query_invocations({"sort": "latency", "sort_dir": "asc"}, ctx)
        data_sql: str = ctx.db.execute_sql.call_args_list[1][0][0]
        assert "latency_ms" in data_sql.lower()
        assert "asc" in data_sql.lower()

    @pytest.mark.asyncio
    async def test_result_row_shape(self) -> None:
        ctx = _make_ctx(rows=[_INVOCATION_ROW], count=1)
        result = await query_invocations({}, ctx)
        row = result.results[0]
        expected_keys = {
            "id", "task_type", "model_alias", "model_actual",
            "tokens_in", "tokens_out", "cost_usd", "latency_ms",
            "quality_score", "timestamp", "has_error",
        }
        assert expected_keys.issubset(set(row.keys()))

    @pytest.mark.asyncio
    async def test_truncated_flag_set_when_results_less_than_total(self) -> None:
        # total_count > len(results) → truncated should be True
        ctx = _make_ctx(rows=[_INVOCATION_ROW], count=50)
        result = await query_invocations({"limit": 1}, ctx)
        assert result.truncated is True

    @pytest.mark.asyncio
    async def test_not_truncated_when_all_results_returned(self) -> None:
        ctx = _make_ctx(rows=[_INVOCATION_ROW], count=1)
        result = await query_invocations({}, ctx)
        assert result.truncated is False


# ---------------------------------------------------------------------------
# TestGetInvocationDetail
# ---------------------------------------------------------------------------


class TestGetInvocationDetail:
    @pytest.mark.asyncio
    async def test_returns_single_invocation(self) -> None:
        ctx = _make_ctx(rows=[_DETAIL_ROW])
        result = await get_invocation_detail({"invocation_id": "inv-001"}, ctx)
        assert isinstance(result, ToolResult)
        assert result.total_count == 1
        assert len(result.results) == 1
        row = result.results[0]
        assert row["id"] == "inv-001"
        assert row["trace_id"] == "trace-abc"

    @pytest.mark.asyncio
    async def test_not_found(self) -> None:
        ctx = _make_ctx(rows=[])
        result = await get_invocation_detail({"invocation_id": "missing"}, ctx)
        assert result.total_count == 0
        assert result.results == []

    @pytest.mark.asyncio
    async def test_passes_invocation_id_as_param(self) -> None:
        ctx = _make_ctx(rows=[_DETAIL_ROW])
        await get_invocation_detail({"invocation_id": "inv-001"}, ctx)
        _, params = ctx.db.execute_sql.call_args[0]
        assert "inv-001" in params

    @pytest.mark.asyncio
    async def test_detail_row_includes_payload_path(self) -> None:
        row = {**_DETAIL_ROW, "payload_path": "/payloads/inv-001.json"}
        ctx = _make_ctx(rows=[row])
        result = await get_invocation_detail({"invocation_id": "inv-001"}, ctx)
        assert result.results[0]["payload_path"] == "/payloads/inv-001.json"

    @pytest.mark.asyncio
    async def test_missing_invocation_id_raises(self) -> None:
        ctx = _make_ctx(rows=[])
        with pytest.raises((KeyError, ValueError)):
            await get_invocation_detail({}, ctx)


# ---------------------------------------------------------------------------
# TestQueryInvocationStats
# ---------------------------------------------------------------------------


class TestQueryInvocationStats:
    @pytest.mark.asyncio
    async def test_grouped_by_task_type(self) -> None:
        stats_rows = [
            {
                "group_key": "parse_task",
                "count": 42,
                "total_cost": 0.126,
                "avg_cost": 0.003,
                "avg_latency": 480.0,
                "avg_quality": 0.88,
                "total_tokens_in": 42000,
                "total_tokens_out": 8400,
            }
        ]
        ctx = _make_ctx(rows=stats_rows)
        result = await query_invocation_stats({"group_by": "task_type"}, ctx)
        assert isinstance(result, ToolResult)
        assert len(result.results) == 1
        row = result.results[0]
        assert row["group_key"] == "parse_task"
        assert row["count"] == 42

    @pytest.mark.asyncio
    async def test_grouped_by_model(self) -> None:
        ctx = _make_ctx(rows=[])
        await query_invocation_stats({"group_by": "model"}, ctx)
        sql: str = ctx.db.execute_sql.call_args[0][0]
        assert "model_alias" in sql

    @pytest.mark.asyncio
    async def test_grouped_by_date(self) -> None:
        ctx = _make_ctx(rows=[])
        await query_invocation_stats({"group_by": "date"}, ctx)
        sql: str = ctx.db.execute_sql.call_args[0][0]
        # Should group by date portion of timestamp
        assert "date" in sql.lower() or "timestamp" in sql.lower()

    @pytest.mark.asyncio
    async def test_applies_date_filters(self) -> None:
        ctx = _make_ctx(rows=[])
        await query_invocation_stats(
            {"group_by": "task_type", "date_from": "2026-05-01", "date_to": "2026-05-15"},
            ctx,
        )
        sql: str = ctx.db.execute_sql.call_args[0][0]
        params: list = ctx.db.execute_sql.call_args[0][1]
        assert "timestamp" in sql
        assert "2026-05-01" in params
        assert "2026-05-15" in params

    @pytest.mark.asyncio
    async def test_missing_group_by_raises(self) -> None:
        ctx = _make_ctx(rows=[])
        with pytest.raises((KeyError, ValueError)):
            await query_invocation_stats({}, ctx)

    @pytest.mark.asyncio
    async def test_invalid_group_by_raises(self) -> None:
        ctx = _make_ctx(rows=[])
        with pytest.raises(ValueError):
            await query_invocation_stats({"group_by": "injected; DROP TABLE"}, ctx)

    @pytest.mark.asyncio
    async def test_result_shape(self) -> None:
        stats_row = {
            "group_key": "parse_task",
            "count": 10,
            "total_cost": 0.03,
            "avg_cost": 0.003,
            "avg_latency": 400.0,
            "avg_quality": None,
            "total_tokens_in": 10000,
            "total_tokens_out": 2000,
        }
        ctx = _make_ctx(rows=[stats_row])
        result = await query_invocation_stats({"group_by": "task_type"}, ctx)
        row = result.results[0]
        expected_keys = {
            "group_key", "count", "total_cost", "avg_cost",
            "avg_latency", "avg_quality", "total_tokens_in", "total_tokens_out",
        }
        assert expected_keys.issubset(set(row.keys()))

    @pytest.mark.asyncio
    async def test_total_count_equals_row_count(self) -> None:
        rows = [
            {"group_key": "a", "count": 1, "total_cost": 0.001, "avg_cost": 0.001,
             "avg_latency": 100.0, "avg_quality": None,
             "total_tokens_in": 100, "total_tokens_out": 20},
            {"group_key": "b", "count": 2, "total_cost": 0.002, "avg_cost": 0.001,
             "avg_latency": 200.0, "avg_quality": 0.9,
             "total_tokens_in": 200, "total_tokens_out": 40},
        ]
        ctx = _make_ctx(rows=rows)
        result = await query_invocation_stats({"group_by": "task_type"}, ctx)
        assert result.total_count == 2
