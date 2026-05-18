"""Unit tests for the Claude Inspector admin endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from donna.api.routes.admin_claude import get_calls, get_insights, get_payload

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cursor(fetchall: list | None = None, fetchone: tuple | None = None) -> AsyncMock:
    c = AsyncMock()
    c.fetchall = AsyncMock(return_value=fetchall or [])
    c.fetchone = AsyncMock(return_value=fetchone)
    return c


def _make_call_row(**overrides: object) -> tuple:
    """Build an invocation_log row tuple matching the get_calls SELECT."""
    defaults = {
        "id": "inv-001",
        "timestamp": "2026-04-01T10:00:00Z",
        "task_type": "parse_task",
        "task_id": "task-001",
        "model_alias": "claude-sonnet",
        "model_actual": "claude-sonnet-4-20250514",
        "latency_ms": 500,
        "tokens_in": 1000,
        "tokens_out": 200,
        "cost_usd": 0.003,
        "quality_score": 0.9,
        "is_shadow": 0,
        "user_id": "nick",
        "estimated_tokens_in": 1480,
        "overflow_escalated": 0,
        "payload_path": "2026/04/inv-001.json",
    }
    defaults.update(overrides)
    return tuple(defaults.values())


def _mock_request_with_payload(payload_dir: Path) -> tuple[MagicMock, AsyncMock]:
    """Build a mock request that includes payload_dir on app.state."""
    conn = AsyncMock()
    conn.commit = AsyncMock()
    request = MagicMock()
    request.app.state.db.connection = conn
    request.app.state.payload_dir = payload_dir
    return request, conn


# ---------------------------------------------------------------------------
# get_calls
# ---------------------------------------------------------------------------


class TestGetCalls:
    async def test_empty_result(self, tmp_path: Path) -> None:
        request, conn = _mock_request_with_payload(tmp_path)
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(0,)),  # count
                _cursor(),  # rows
            ]
        )
        result = await get_calls(request)
        assert result["total"] == 0
        assert result["calls"] == []

    async def test_returns_paginated_calls_with_has_payload(self, tmp_path: Path) -> None:
        # Create the payload file so has_payload is True
        payload_dir = tmp_path
        (payload_dir / "2026" / "04").mkdir(parents=True)
        (payload_dir / "2026" / "04" / "inv-001.json").write_text("{}")

        request, conn = _mock_request_with_payload(payload_dir)
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(1,)),
                _cursor(fetchall=[_make_call_row()]),
            ]
        )
        result = await get_calls(request)
        assert result["total"] == 1
        call = result["calls"][0]
        assert call["id"] == "inv-001"
        assert call["cost_usd"] == 0.003
        assert call["has_payload"] is True

    async def test_has_payload_false_when_file_missing(self, tmp_path: Path) -> None:
        request, conn = _mock_request_with_payload(tmp_path)
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(1,)),
                _cursor(fetchall=[_make_call_row(payload_path="missing/file.json")]),
            ]
        )
        result = await get_calls(request)
        assert result["calls"][0]["has_payload"] is False

    async def test_has_payload_false_when_path_null(self, tmp_path: Path) -> None:
        request, conn = _mock_request_with_payload(tmp_path)
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(1,)),
                _cursor(fetchall=[_make_call_row(payload_path=None)]),
            ]
        )
        result = await get_calls(request)
        assert result["calls"][0]["has_payload"] is False

    async def test_filter_by_task_type(self, tmp_path: Path) -> None:
        request, conn = _mock_request_with_payload(tmp_path)
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(1,)),
                _cursor(fetchall=[_make_call_row()]),
            ]
        )
        await get_calls(request, task_type="parse_task")
        call_args = conn.execute.call_args_list[0]
        assert "task_type = ?" in call_args[0][0]

    async def test_filter_by_model(self, tmp_path: Path) -> None:
        request, conn = _mock_request_with_payload(tmp_path)
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(0,)),
                _cursor(),
            ]
        )
        await get_calls(request, model="claude-sonnet")
        call_args = conn.execute.call_args_list[0]
        assert "model_alias = ?" in call_args[0][0]

    async def test_filter_by_date_range(self, tmp_path: Path) -> None:
        request, conn = _mock_request_with_payload(tmp_path)
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(0,)),
                _cursor(),
            ]
        )
        await get_calls(request, date_from="2026-04-01", date_to="2026-04-30")
        call_args = conn.execute.call_args_list[0]
        sql = call_args[0][0]
        assert "timestamp >= ?" in sql
        assert "timestamp <= ?" in sql

    async def test_filter_by_min_cost(self, tmp_path: Path) -> None:
        request, conn = _mock_request_with_payload(tmp_path)
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(0,)),
                _cursor(),
            ]
        )
        await get_calls(request, min_cost=0.01)
        call_args = conn.execute.call_args_list[0]
        assert "cost_usd >= ?" in call_args[0][0]

    async def test_filter_by_quality_score_below(self, tmp_path: Path) -> None:
        request, conn = _mock_request_with_payload(tmp_path)
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(0,)),
                _cursor(),
            ]
        )
        await get_calls(request, quality_score_below=0.5)
        call_args = conn.execute.call_args_list[0]
        assert "quality_score < ?" in call_args[0][0]

    async def test_sort_by_cost(self, tmp_path: Path) -> None:
        request, conn = _mock_request_with_payload(tmp_path)
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(0,)),
                _cursor(),
            ]
        )
        await get_calls(request, sort="cost", sort_dir="asc")
        # Check the data query (second call) for ORDER BY cost_usd ASC
        call_args = conn.execute.call_args_list[1]
        assert "ORDER BY cost_usd ASC" in call_args[0][0]

    async def test_invalid_sort_falls_back_to_timestamp(self, tmp_path: Path) -> None:
        request, conn = _mock_request_with_payload(tmp_path)
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(0,)),
                _cursor(),
            ]
        )
        await get_calls(request, sort="invalid_col")
        call_args = conn.execute.call_args_list[1]
        assert "ORDER BY timestamp" in call_args[0][0]

    async def test_pagination(self, tmp_path: Path) -> None:
        request, conn = _mock_request_with_payload(tmp_path)
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(100,)),
                _cursor(fetchall=[_make_call_row()]),
            ]
        )
        result = await get_calls(request, limit=10, offset=20)
        assert result["limit"] == 10
        assert result["offset"] == 20
        assert result["total"] == 100


# ---------------------------------------------------------------------------
# get_payload
# ---------------------------------------------------------------------------


class TestGetPayload:
    async def test_returns_payload_contents(self, tmp_path: Path) -> None:
        payload_dir = tmp_path
        (payload_dir / "2026" / "04").mkdir(parents=True)
        payload_data = {"request": {"prompt": "hello"}, "response": {"text": "world"}}
        (payload_dir / "2026" / "04" / "inv-001.json").write_text(
            json.dumps(payload_data)
        )

        request, conn = _mock_request_with_payload(payload_dir)
        conn.execute = AsyncMock(
            return_value=_cursor(fetchone=("2026/04/inv-001.json",))
        )
        result = await get_payload(request, invocation_id="inv-001")
        assert result == payload_data

    async def test_404_when_invocation_not_found(self, tmp_path: Path) -> None:
        request, conn = _mock_request_with_payload(tmp_path)
        conn.execute = AsyncMock(return_value=_cursor(fetchone=None))
        with pytest.raises(HTTPException) as exc_info:
            await get_payload(request, invocation_id="nonexistent")
        assert exc_info.value.status_code == 404
        assert "not found" in exc_info.value.detail.lower()

    async def test_404_when_payload_path_null(self, tmp_path: Path) -> None:
        request, conn = _mock_request_with_payload(tmp_path)
        conn.execute = AsyncMock(return_value=_cursor(fetchone=(None,)))
        with pytest.raises(HTTPException) as exc_info:
            await get_payload(request, invocation_id="inv-001")
        assert exc_info.value.status_code == 404
        assert "null" in exc_info.value.detail.lower()

    async def test_404_when_file_evicted(self, tmp_path: Path) -> None:
        request, conn = _mock_request_with_payload(tmp_path)
        conn.execute = AsyncMock(
            return_value=_cursor(fetchone=("evicted/file.json",))
        )
        with pytest.raises(HTTPException) as exc_info:
            await get_payload(request, invocation_id="inv-001")
        assert exc_info.value.status_code == 404
        assert "not found" in exc_info.value.detail.lower()


# ---------------------------------------------------------------------------
# get_insights
# ---------------------------------------------------------------------------


class TestGetInsights:
    async def test_delegates_to_compute_insights(self, tmp_path: Path) -> None:
        request, conn = _mock_request_with_payload(tmp_path)
        expected = {"total_cost": 1.23, "call_count": 42}
        mock_compute = AsyncMock(return_value=expected)

        # The endpoint does a lazy import: from donna.insights.engine import compute_insights
        # We inject a fake module into sys.modules so the import succeeds.
        fake_engine = MagicMock()
        fake_engine.compute_insights = mock_compute

        import sys
        modules = {"donna.insights": MagicMock(), "donna.insights.engine": fake_engine}
        with patch.dict(sys.modules, modules):
            result = await get_insights(request, days=14)

        assert result == expected
        mock_compute.assert_awaited_once_with(
            conn=conn, payload_dir=tmp_path, days=14
        )
