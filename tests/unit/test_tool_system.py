"""Tests for system read tool handlers.

Covers get_system_health and query_preferences.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.chat.tools import ToolContext, ToolResult
from donna.chat.tools.system import (
    get_system_health,
    query_preferences,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HEALTH_RESPONSES = [
    [{"cnt": 3}],            # error count last hour
    [{"cnt": 2}],            # active sessions
    [{"size": 12.5}],        # DB size MB
]

_PREFERENCE_ROW: dict = {
    "id": "pref-001",
    "rule_type": "tone",
    "rule_text": "Be concise",
    "confidence": 0.95,
    "enabled": True,
    "correction_count": 5,
    "created_at": "2026-05-01T10:00:00Z",
}


def _make_ctx(rows: list[dict] | None = None, side_effect: list | None = None) -> ToolContext:
    """Build a ToolContext with a mock DB."""
    db = MagicMock()
    if side_effect is not None:
        db.execute_sql = AsyncMock(side_effect=side_effect)
    else:
        db.execute_sql = AsyncMock(return_value=rows or [])
    return ToolContext(db=db, user_id="nick", session_id="sess-1")


# ---------------------------------------------------------------------------
# TestGetSystemHealth
# ---------------------------------------------------------------------------


class TestGetSystemHealth:
    @pytest.mark.asyncio
    async def test_returns_system_metrics(self) -> None:
        ctx = _make_ctx(side_effect=list(_HEALTH_RESPONSES))
        result = await get_system_health({}, ctx)
        assert isinstance(result, ToolResult)
        assert result.total_count == 1
        assert len(result.results) == 1

    @pytest.mark.asyncio
    async def test_result_contains_error_count(self) -> None:
        ctx = _make_ctx(side_effect=list(_HEALTH_RESPONSES))
        result = await get_system_health({}, ctx)
        row = result.results[0]
        # Should contain some field related to errors
        assert any("error" in k for k in row)

    @pytest.mark.asyncio
    async def test_result_contains_active_sessions(self) -> None:
        ctx = _make_ctx(side_effect=list(_HEALTH_RESPONSES))
        result = await get_system_health({}, ctx)
        row = result.results[0]
        assert any("session" in k for k in row)

    @pytest.mark.asyncio
    async def test_result_contains_db_size(self) -> None:
        ctx = _make_ctx(side_effect=list(_HEALTH_RESPONSES))
        result = await get_system_health({}, ctx)
        row = result.results[0]
        assert any("size" in k or "db" in k for k in row)

    @pytest.mark.asyncio
    async def test_executes_three_sql_queries(self) -> None:
        ctx = _make_ctx(side_effect=list(_HEALTH_RESPONSES))
        await get_system_health({}, ctx)
        assert ctx.db.execute_sql.call_count == 3

    @pytest.mark.asyncio
    async def test_error_count_query_targets_invocation_log(self) -> None:
        ctx = _make_ctx(side_effect=list(_HEALTH_RESPONSES))
        await get_system_health({}, ctx)
        first_sql: str = ctx.db.execute_sql.call_args_list[0][0][0]
        assert "invocation_log" in first_sql

    @pytest.mark.asyncio
    async def test_active_sessions_query_targets_conversation_sessions(self) -> None:
        ctx = _make_ctx(side_effect=list(_HEALTH_RESPONSES))
        await get_system_health({}, ctx)
        second_sql: str = ctx.db.execute_sql.call_args_list[1][0][0]
        assert "conversation_sessions" in second_sql

    @pytest.mark.asyncio
    async def test_error_count_value_populated(self) -> None:
        responses = [
            [{"cnt": 7}],
            [{"cnt": 4}],
            [{"size": 8.2}],
        ]
        ctx = _make_ctx(side_effect=responses)
        result = await get_system_health({}, ctx)
        row = result.results[0]
        values = list(row.values())
        assert 7 in values


# ---------------------------------------------------------------------------
# TestQueryPreferences
# ---------------------------------------------------------------------------


class TestQueryPreferences:
    @pytest.mark.asyncio
    async def test_returns_rules(self) -> None:
        ctx = _make_ctx(rows=[_PREFERENCE_ROW])
        result = await query_preferences({}, ctx)
        assert isinstance(result, ToolResult)
        assert result.total_count == 1
        assert result.results[0]["id"] == "pref-001"

    @pytest.mark.asyncio
    async def test_enabled_only_filter_default_true(self) -> None:
        ctx = _make_ctx(rows=[])
        await query_preferences({}, ctx)
        sql: str = ctx.db.execute_sql.call_args[0][0]
        params: list = ctx.db.execute_sql.call_args[0][1]
        # Default enabled_only=True should filter enabled = 1
        assert "enabled" in sql
        assert 1 in params

    @pytest.mark.asyncio
    async def test_enabled_only_false_skips_filter(self) -> None:
        ctx = _make_ctx(rows=[])
        await query_preferences({"enabled_only": False}, ctx)
        sql: str = ctx.db.execute_sql.call_args[0][0]
        params: list = ctx.db.execute_sql.call_args[0][1]
        # When enabled_only=False, enabled filter should not add 1 to params
        assert 1 not in params or "enabled" not in sql

    @pytest.mark.asyncio
    async def test_filter_by_rule_type(self) -> None:
        ctx = _make_ctx(rows=[])
        await query_preferences({"rule_type": "tone", "enabled_only": False}, ctx)
        sql: str = ctx.db.execute_sql.call_args[0][0]
        params: list = ctx.db.execute_sql.call_args[0][1]
        assert "rule_type" in sql
        assert "tone" in params

    @pytest.mark.asyncio
    async def test_queries_preference_rules_table(self) -> None:
        ctx = _make_ctx(rows=[])
        await query_preferences({}, ctx)
        sql: str = ctx.db.execute_sql.call_args[0][0]
        assert "preference_rules" in sql

    @pytest.mark.asyncio
    async def test_result_row_shape(self) -> None:
        ctx = _make_ctx(rows=[_PREFERENCE_ROW])
        result = await query_preferences({}, ctx)
        row = result.results[0]
        expected_keys = {
            "id", "rule_type", "rule_text", "confidence",
            "enabled", "correction_count", "created_at",
        }
        assert expected_keys.issubset(set(row.keys()))

    @pytest.mark.asyncio
    async def test_empty_result(self) -> None:
        ctx = _make_ctx(rows=[])
        result = await query_preferences({}, ctx)
        assert result.results == []
        assert result.total_count == 0
