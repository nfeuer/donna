"""Unit tests for the admin shadow scoring endpoints."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.api.routes.admin_shadow import (
    _invocation_dict,
    _COMPARISON_COLS,
    _SUMMARY_COLS,
    list_shadow_comparisons,
    list_spot_checks,
    shadow_stats,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cursor(fetchall: list | None = None, fetchone: tuple | None = None) -> AsyncMock:
    c = AsyncMock()
    c.fetchall = AsyncMock(return_value=fetchall or [])
    c.fetchone = AsyncMock(return_value=fetchone)
    return c


def _make_comparison_row(
    primary_id: str = "inv-p1",
    shadow_id: str = "inv-s1",
    p_quality: float | None = 0.9,
    s_quality: float | None = 0.85,
) -> tuple:
    """Build a joined primary+shadow row for comparisons query."""
    base = {
        "id": primary_id, "timestamp": "2026-04-01T10:00:00Z",
        "task_type": "parse_task", "task_id": "task-001",
        "model_alias": "claude-sonnet", "model_actual": "claude-sonnet-4",
        "input_hash": "hash123", "latency_ms": 500,
        "tokens_in": 1000, "tokens_out": 200,
        "cost_usd": 0.003, "output": json.dumps({"title": "Buy milk"}),
        "quality_score": p_quality, "is_shadow": 0,
        "spot_check_queued": 0, "user_id": "nick",
    }
    shadow = {
        "id": shadow_id, "timestamp": "2026-04-01T10:00:01Z",
        "task_type": "parse_task", "task_id": "task-001",
        "model_alias": "qwen-local", "model_actual": "qwen2.5:32b",
        "input_hash": "hash123", "latency_ms": 800,
        "tokens_in": 1000, "tokens_out": 250,
        "cost_usd": 0.0, "output": json.dumps({"title": "Buy milk"}),
        "quality_score": s_quality, "is_shadow": 1,
        "spot_check_queued": 0, "user_id": "nick",
    }
    return tuple(base.values()) + tuple(shadow.values())


# ---------------------------------------------------------------------------
# _invocation_dict
# ---------------------------------------------------------------------------


class TestInvocationDict:
    def test_basic_conversion(self) -> None:
        row = ("inv-1", "2026-04-01", "parse_task", "task-1", "claude-sonnet",
               "claude-sonnet-4", "hash1", 500, 1000, 200, 0.003,
               json.dumps({"key": "val"}), 0.9, 0, 0, "nick")
        result = _invocation_dict(row, _COMPARISON_COLS)
        assert result["id"] == "inv-1"
        assert result["cost_usd"] == 0.003
        assert result["output"] == {"key": "val"}
        assert result["is_shadow"] is False
        assert result["quality_score"] == 0.9

    def test_null_quality_score(self) -> None:
        row = ("inv-1", "2026-04-01", "parse_task", "task-1", "claude-sonnet",
               "claude-sonnet-4", "hash1", 500, 1000, 200, 0.0,
               None, None, 0, 0, "nick")
        result = _invocation_dict(row, _COMPARISON_COLS)
        assert result["quality_score"] is None
        assert result["output"] is None

    def test_invalid_json_output(self) -> None:
        row = ("inv-1", "2026-04-01", "parse_task", "task-1", "claude-sonnet",
               "claude-sonnet-4", "hash1", 500, 1000, 200, 0.0,
               "not-json", 0.8, 1, 1, "nick")
        result = _invocation_dict(row, _COMPARISON_COLS)
        assert result["output"] == {"raw": "not-json"}
        assert result["is_shadow"] is True
        assert result["spot_check_queued"] is True


# ---------------------------------------------------------------------------
# list_shadow_comparisons
# ---------------------------------------------------------------------------


class TestListShadowComparisons:
    async def test_empty_db(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(),  # input_hash join
                _cursor(),  # task_id proximity join
            ]
        )
        result = await list_shadow_comparisons(request, task_type=None, days=30, limit=50)
        assert result["comparisons"] == []
        assert result["total"] == 0

    async def test_input_hash_pairing(self, mock_request: tuple) -> None:
        request, conn = mock_request
        row = _make_comparison_row(p_quality=0.9, s_quality=0.85)
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchall=[row]),  # input_hash match
                _cursor(),  # proximity (not needed, already at limit)
            ]
        )
        result = await list_shadow_comparisons(request, task_type=None, days=30, limit=1)
        assert len(result["comparisons"]) == 1
        comp = result["comparisons"][0]
        assert comp["primary"]["id"] == "inv-p1"
        assert comp["shadow"]["id"] == "inv-s1"
        assert comp["quality_delta"] == pytest.approx(-0.05, abs=0.001)

    async def test_quality_delta_with_null_scores(self, mock_request: tuple) -> None:
        request, conn = mock_request
        row = _make_comparison_row(p_quality=None, s_quality=0.85)
        conn.execute = AsyncMock(
            side_effect=[_cursor(fetchall=[row]), _cursor()]
        )
        result = await list_shadow_comparisons(request, task_type=None, days=30, limit=50)
        assert result["comparisons"][0]["quality_delta"] is None

    async def test_task_type_filter(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[_cursor(), _cursor()]
        )
        await list_shadow_comparisons(request, task_type="parse_task", days=30, limit=50)
        sql = conn.execute.call_args_list[0][0][0]
        assert "p.task_type = ?" in sql


# ---------------------------------------------------------------------------
# shadow_stats
# ---------------------------------------------------------------------------


class TestShadowStats:
    async def test_empty_db(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(None, None, 0, 0, 0, 0)),  # avg quality
                _cursor(fetchone=(0, 0, 0)),  # win/loss/tie
                _cursor(),  # trend
            ]
        )
        result = await shadow_stats(request, days=30)
        assert result["primary_avg_quality"] is None
        assert result["shadow_avg_quality"] is None
        assert result["avg_delta"] is None
        assert result["wins"] == 0

    async def test_with_data(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(0.9, 0.85, 0.05, 0.01, 10, 10)),
                _cursor(fetchone=(3, 5, 2)),  # 3 wins, 5 losses, 2 ties
                _cursor(fetchall=[("2026-04-01", 0.84, 5)]),
            ]
        )
        result = await shadow_stats(request, days=30)
        assert result["primary_avg_quality"] == 0.9
        assert result["shadow_avg_quality"] == 0.85
        assert result["avg_delta"] == pytest.approx(-0.05, abs=0.001)
        assert result["wins"] == 3
        assert result["losses"] == 5
        assert result["trend"][0]["date"] == "2026-04-01"


# ---------------------------------------------------------------------------
# list_spot_checks
# ---------------------------------------------------------------------------


class TestListSpotChecks:
    async def test_empty(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(0,)),  # count
                _cursor(),  # rows
            ]
        )
        result = await list_spot_checks(request)
        assert result["items"] == []
        assert result["total"] == 0

    async def test_returns_flagged_invocations(self, mock_request: tuple) -> None:
        request, conn = mock_request
        # _SUMMARY_COLS row (14 cols)
        row = ("inv-1", "2026-04-01", "parse_task", "task-1",
               "claude-sonnet", "claude-sonnet-4",
               500, 1000, 200, 0.003,
               0.5, 0, 1, "nick")
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(1,)),
                _cursor(fetchall=[row]),
            ]
        )
        result = await list_spot_checks(request)
        assert result["total"] == 1
        assert result["items"][0]["quality_score"] == 0.5
        assert result["items"][0]["spot_check_queued"] is True

    async def test_pagination(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(50,)),
                _cursor(),
            ]
        )
        result = await list_spot_checks(request, limit=10, offset=20)
        assert result["limit"] == 10
        assert result["offset"] == 20
        assert result["total"] == 50
