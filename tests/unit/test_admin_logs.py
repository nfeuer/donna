"""Unit tests for the admin logs endpoints.

Mocks Loki HTTP calls with aioresponses and DB connection with AsyncMock.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from donna.api.routes.admin_logs import (
    EVENT_TYPE_TREE,
    _query_invocation_log_fallback,
    get_event_types,
    get_logs,
    get_trace,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cursor(fetchall: list | None = None, fetchone: tuple | None = None) -> AsyncMock:
    c = AsyncMock()
    c.fetchall = AsyncMock(return_value=fetchall or [])
    c.fetchone = AsyncMock(return_value=fetchone)
    return c


# ---------------------------------------------------------------------------
# get_event_types
# ---------------------------------------------------------------------------


class TestGetEventTypes:
    async def test_returns_static_tree(self) -> None:
        result = await get_event_types()
        assert result == EVENT_TYPE_TREE
        assert "task" in result
        assert "api" in result
        assert "system" in result

    async def test_tree_has_expected_categories(self) -> None:
        result = await get_event_types()
        expected_categories = {"task", "api", "agent", "scheduler", "notification", "preference", "system", "cost", "sync"}
        assert set(result.keys()) == expected_categories


# ---------------------------------------------------------------------------
# get_trace (fallback path — Loki mocked to fail)
# ---------------------------------------------------------------------------


class TestGetTrace:
    async def test_loki_failure_falls_back_to_invocation_log(self, mock_request: tuple) -> None:
        request, conn = mock_request
        inv_row = ("inv-1", "2026-04-01T10:00:00Z", "parse_task", "claude-sonnet", 500, 1000, 200, 0.003, "task-001")
        conn.execute = AsyncMock(return_value=_cursor(fetchall=[inv_row]))

        with patch("donna.api.routes.admin_logs._query_loki_trace", side_effect=Exception("Loki down")):
            result = await get_trace(request, correlation_id="corr-123")

        assert result["source"] == "invocation_log_fallback"
        assert result["correlation_id"] == "corr-123"
        assert len(result["entries"]) == 1
        assert result["entries"][0]["extra"]["task_type"] == "parse_task"

    async def test_empty_fallback(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(return_value=_cursor())

        with patch("donna.api.routes.admin_logs._query_loki_trace", side_effect=Exception("Loki down")):
            result = await get_trace(request, correlation_id="unknown")

        assert result["count"] == 0
        assert result["entries"] == []

    async def test_loki_success_returns_loki_source(self, mock_request: tuple) -> None:
        request, _conn = mock_request
        loki_entries = [
            {"timestamp": "2026-04-01T10:00:00Z", "event_type": "agent.dispatched", "level": "INFO"},
        ]
        with patch("donna.api.routes.admin_logs._query_loki_trace", return_value=loki_entries):
            result = await get_trace(request, correlation_id="corr-123")

        assert result["source"] == "loki"
        assert result["count"] == 1


# ---------------------------------------------------------------------------
# get_logs (fallback path)
# ---------------------------------------------------------------------------


class TestGetLogsFallback:
    async def test_loki_failure_uses_invocation_log_fallback(self, mock_request: tuple) -> None:
        request, conn = mock_request
        inv_row = ("inv-1", "2026-04-01T10:00:00Z", "parse_task", "claude-sonnet", 500, 1000, 200, 0.003, "task-001", 0.9, 0)
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(1,)),  # count
                _cursor(fetchall=[inv_row]),  # rows
            ]
        )

        with patch("donna.api.routes.admin_logs._query_loki", side_effect=Exception("Loki down")):
            result = await get_logs(
                request, event_type=None, level=None, service=None,
                search=None, correlation_id=None, task_id=None,
                start=None, end=None, limit=50, offset=0,
            )

        assert result["source"] == "invocation_log_fallback"
        assert len(result["entries"]) == 1

    async def test_loki_success_returns_loki_entries(self, mock_request: tuple) -> None:
        request, _conn = mock_request
        entries = [
            {"timestamp": "2026-04-01T10:00:00Z", "event_type": "task.created"},
            {"timestamp": "2026-04-01T10:01:00Z", "event_type": "agent.dispatched"},
        ]
        with patch("donna.api.routes.admin_logs._query_loki", return_value=entries):
            result = await get_logs(
                request, event_type=None, level=None, service=None,
                search=None, correlation_id=None, task_id=None,
                start=None, end=None, limit=50, offset=0,
            )

        assert result["source"] == "loki"
        assert len(result["entries"]) == 2

    async def test_loki_with_offset(self, mock_request: tuple) -> None:
        request, _conn = mock_request
        entries = [{"event_type": f"entry-{i}"} for i in range(10)]
        with patch("donna.api.routes.admin_logs._query_loki", return_value=entries):
            result = await get_logs(
                request, event_type=None, level=None, service=None,
                search=None, correlation_id=None, task_id=None,
                start=None, end=None, limit=3, offset=5,
            )

        assert result["source"] == "loki"
        assert len(result["entries"]) == 3
        assert result["offset"] == 5


# ---------------------------------------------------------------------------
# _query_invocation_log_fallback (direct)
# ---------------------------------------------------------------------------


class TestInvocationLogFallback:
    async def test_filter_by_task_id(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[_cursor(fetchone=(0,)), _cursor()]
        )
        result = await _query_invocation_log_fallback(
            request, event_type=None, level=None, service=None,
            search=None, task_id="task-001", start=None, end=None,
            limit=50, offset=0,
        )
        sql = conn.execute.call_args_list[0][0][0]
        assert "task_id = ?" in sql
        assert result["source"] == "invocation_log_fallback"

    async def test_filter_by_search(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[_cursor(fetchone=(0,)), _cursor()]
        )
        await _query_invocation_log_fallback(
            request, event_type=None, level=None, service=None,
            search="parse", task_id=None, start=None, end=None,
            limit=50, offset=0,
        )
        sql = conn.execute.call_args_list[0][0][0]
        assert "task_type LIKE ?" in sql

    async def test_filter_by_time_range(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[_cursor(fetchone=(0,)), _cursor()]
        )
        await _query_invocation_log_fallback(
            request, event_type=None, level=None, service=None,
            search=None, task_id=None,
            start="2026-04-01", end="2026-04-05",
            limit=50, offset=0,
        )
        sql = conn.execute.call_args_list[0][0][0]
        assert "timestamp >= ?" in sql
        assert "timestamp <= ?" in sql
