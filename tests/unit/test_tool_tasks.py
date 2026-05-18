"""Tests for tasks read tool handlers.

Covers query_tasks and get_task_detail.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.chat.tools import ToolContext, ToolResult
from donna.chat.tools.tasks import (
    get_task_detail,
    query_tasks,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TASK_ROW: dict = {
    "id": "task-001",
    "title": "Buy groceries",
    "description": "Milk, eggs, bread",
    "status": "open",
    "priority": 2,
    "domain": "personal",
    "notes": None,
    "created_at": "2026-05-01T10:00:00Z",
    "updated_at": "2026-05-01T10:00:00Z",
    "scheduled_start": None,
    "deadline": None,
}


def _make_ctx(rows: list[dict] | None = None, count: int | None = None) -> ToolContext:
    """Build a ToolContext with a mock DB."""
    db = MagicMock()
    if count is not None:
        db.execute_sql = AsyncMock(
            side_effect=[
                [{"count": count}],  # COUNT(*) query
                rows or [],          # data query
            ]
        )
    else:
        db.execute_sql = AsyncMock(return_value=rows or [])
    return ToolContext(db=db, user_id="nick", session_id="sess-1")


# ---------------------------------------------------------------------------
# TestQueryTasks
# ---------------------------------------------------------------------------


class TestQueryTasks:
    @pytest.mark.asyncio
    async def test_returns_tool_result(self) -> None:
        ctx = _make_ctx(rows=[_TASK_ROW], count=1)
        result = await query_tasks({}, ctx)
        assert isinstance(result, ToolResult)
        assert result.total_count == 1
        assert len(result.results) == 1
        assert result.results[0]["id"] == "task-001"

    @pytest.mark.asyncio
    async def test_title_search_uses_like(self) -> None:
        ctx = _make_ctx(rows=[], count=0)
        await query_tasks({"title_search": "groceries"}, ctx)
        count_sql: str = ctx.db.execute_sql.call_args_list[0][0][0]
        count_params: list = ctx.db.execute_sql.call_args_list[0][0][1]
        assert "LIKE" in count_sql.upper()
        assert any("groceries" in str(p) for p in count_params)

    @pytest.mark.asyncio
    async def test_default_sort_by_priority(self) -> None:
        ctx = _make_ctx(rows=[], count=0)
        await query_tasks({}, ctx)
        data_sql: str = ctx.db.execute_sql.call_args_list[1][0][0]
        assert "priority" in data_sql.lower()
        assert "ORDER BY" in data_sql.upper()

    @pytest.mark.asyncio
    async def test_default_limit_25(self) -> None:
        ctx = _make_ctx(rows=[], count=0)
        await query_tasks({}, ctx)
        data_params: list = ctx.db.execute_sql.call_args_list[1][0][1]
        assert 25 in data_params

    @pytest.mark.asyncio
    async def test_limit_capped_at_100(self) -> None:
        ctx = _make_ctx(rows=[], count=0)
        await query_tasks({"limit": 999}, ctx)
        data_params: list = ctx.db.execute_sql.call_args_list[1][0][1]
        assert 100 in data_params

    @pytest.mark.asyncio
    async def test_filter_by_status(self) -> None:
        ctx = _make_ctx(rows=[], count=0)
        await query_tasks({"status": "open"}, ctx)
        count_sql: str = ctx.db.execute_sql.call_args_list[0][0][0]
        assert "status" in count_sql
        count_params: list = ctx.db.execute_sql.call_args_list[0][0][1]
        assert "open" in count_params

    @pytest.mark.asyncio
    async def test_filter_by_priority(self) -> None:
        ctx = _make_ctx(rows=[], count=0)
        await query_tasks({"priority": 1}, ctx)
        count_sql: str = ctx.db.execute_sql.call_args_list[0][0][0]
        assert "priority" in count_sql

    @pytest.mark.asyncio
    async def test_filter_by_domain(self) -> None:
        ctx = _make_ctx(rows=[], count=0)
        await query_tasks({"domain": "personal"}, ctx)
        count_sql: str = ctx.db.execute_sql.call_args_list[0][0][0]
        assert "domain" in count_sql
        count_params: list = ctx.db.execute_sql.call_args_list[0][0][1]
        assert "personal" in count_params

    @pytest.mark.asyncio
    async def test_filter_by_created_after(self) -> None:
        ctx = _make_ctx(rows=[], count=0)
        await query_tasks({"created_after": "2026-05-01"}, ctx)
        count_sql: str = ctx.db.execute_sql.call_args_list[0][0][0]
        assert "created_at" in count_sql
        count_params: list = ctx.db.execute_sql.call_args_list[0][0][1]
        assert "2026-05-01" in count_params

    @pytest.mark.asyncio
    async def test_filter_by_updated_after(self) -> None:
        ctx = _make_ctx(rows=[], count=0)
        await query_tasks({"updated_after": "2026-05-01"}, ctx)
        count_sql: str = ctx.db.execute_sql.call_args_list[0][0][0]
        assert "updated_at" in count_sql

    @pytest.mark.asyncio
    async def test_user_id_condition_always_present(self) -> None:
        ctx = _make_ctx(rows=[], count=0)
        await query_tasks({}, ctx)
        count_sql: str = ctx.db.execute_sql.call_args_list[0][0][0]
        assert "user_id" in count_sql
        count_params: list = ctx.db.execute_sql.call_args_list[0][0][1]
        assert "nick" in count_params

    @pytest.mark.asyncio
    async def test_truncated_flag_set_when_more_results_exist(self) -> None:
        ctx = _make_ctx(rows=[_TASK_ROW], count=50)
        result = await query_tasks({"limit": 1}, ctx)
        assert result.truncated is True

    @pytest.mark.asyncio
    async def test_not_truncated_when_all_results_returned(self) -> None:
        ctx = _make_ctx(rows=[_TASK_ROW], count=1)
        result = await query_tasks({}, ctx)
        assert result.truncated is False

    @pytest.mark.asyncio
    async def test_sort_by_created_at(self) -> None:
        ctx = _make_ctx(rows=[], count=0)
        await query_tasks({"sort": "created_at"}, ctx)
        data_sql: str = ctx.db.execute_sql.call_args_list[1][0][0]
        assert "created_at" in data_sql.lower()


# ---------------------------------------------------------------------------
# TestGetTaskDetail
# ---------------------------------------------------------------------------


class TestGetTaskDetail:
    @pytest.mark.asyncio
    async def test_returns_single_task(self) -> None:
        ctx = _make_ctx(rows=[_TASK_ROW])
        result = await get_task_detail({"task_id": "task-001"}, ctx)
        assert isinstance(result, ToolResult)
        assert result.total_count == 1
        assert len(result.results) == 1
        assert result.results[0]["id"] == "task-001"

    @pytest.mark.asyncio
    async def test_not_found(self) -> None:
        ctx = _make_ctx(rows=[])
        result = await get_task_detail({"task_id": "missing"}, ctx)
        assert result.total_count == 0
        assert result.results == []

    @pytest.mark.asyncio
    async def test_passes_task_id_as_param(self) -> None:
        ctx = _make_ctx(rows=[_TASK_ROW])
        await get_task_detail({"task_id": "task-001"}, ctx)
        _, params = ctx.db.execute_sql.call_args[0]
        assert "task-001" in params

    @pytest.mark.asyncio
    async def test_missing_task_id_raises(self) -> None:
        ctx = _make_ctx(rows=[])
        with pytest.raises((KeyError, ValueError)):
            await get_task_detail({}, ctx)

    @pytest.mark.asyncio
    async def test_detail_includes_all_columns(self) -> None:
        ctx = _make_ctx(rows=[_TASK_ROW])
        result = await get_task_detail({"task_id": "task-001"}, ctx)
        row = result.results[0]
        expected_keys = {
            "id", "title", "description", "status", "priority",
            "domain", "notes", "created_at", "updated_at",
            "scheduled_start", "deadline",
        }
        assert expected_keys.issubset(set(row.keys()))
