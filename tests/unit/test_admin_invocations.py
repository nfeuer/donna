"""Unit tests for the admin invocations endpoints."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from donna.api.routes.admin_invocations import get_invocation, list_invocations

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cursor(fetchall: list | None = None, fetchone: tuple | None = None) -> AsyncMock:
    c = AsyncMock()
    c.fetchall = AsyncMock(return_value=fetchall or [])
    c.fetchone = AsyncMock(return_value=fetchone)
    return c


def _make_invocation_row(**overrides: object) -> tuple:
    """Build an invocation_log row tuple for list_invocations query."""
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
        "spot_check_queued": 0,
        "user_id": "nick",
        "estimated_tokens_in": 1480,
        "overflow_escalated": 0,
    }
    defaults.update(overrides)
    return tuple(defaults.values())


def _make_detail_row(**overrides: object) -> tuple:
    """Build a row for get_invocation detail query (includes input_hash, output, eval_session_id)."""
    defaults = {
        "id": "inv-001",
        "timestamp": "2026-04-01T10:00:00Z",
        "task_type": "parse_task",
        "task_id": "task-001",
        "model_alias": "claude-sonnet",
        "model_actual": "claude-sonnet-4-20250514",
        "input_hash": "abc123",
        "latency_ms": 500,
        "tokens_in": 1000,
        "tokens_out": 200,
        "cost_usd": 0.003,
        "output": json.dumps({"title": "Buy milk"}),
        "quality_score": 0.9,
        "is_shadow": 0,
        "eval_session_id": None,
        "spot_check_queued": 0,
        "user_id": "nick",
        "estimated_tokens_in": 1480,
        "overflow_escalated": 0,
    }
    defaults.update(overrides)
    return tuple(defaults.values())


# ---------------------------------------------------------------------------
# list_invocations
# ---------------------------------------------------------------------------


class TestListInvocations:
    async def test_no_filters_empty(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(0,)),  # count
                _cursor(),  # rows
            ]
        )
        result = await list_invocations(request)
        assert result["total"] == 0
        assert result["invocations"] == []

    async def test_returns_invocations(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(1,)),
                _cursor(fetchall=[_make_invocation_row()]),
            ]
        )
        result = await list_invocations(request)
        assert result["total"] == 1
        inv = result["invocations"][0]
        assert inv["id"] == "inv-001"
        assert inv["cost_usd"] == 0.003
        assert inv["is_shadow"] is False

    async def test_filter_by_task_type(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(1,)),
                _cursor(fetchall=[_make_invocation_row()]),
            ]
        )
        await list_invocations(request, task_type="parse_task")
        # Verify SQL got the task_type param
        call_args = conn.execute.call_args_list[0]
        assert "task_type = ?" in call_args[0][0]

    async def test_filter_by_shadow(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(0,)),
                _cursor(),
            ]
        )
        await list_invocations(request, is_shadow=True)
        call_args = conn.execute.call_args_list[0]
        assert "is_shadow = ?" in call_args[0][0]

    async def test_response_includes_context_budget_fields(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(1,)),
                _cursor(fetchall=[_make_invocation_row()]),
            ]
        )
        result = await list_invocations(request)
        inv = result["invocations"][0]
        assert inv["estimated_tokens_in"] == 1480
        assert inv["overflow_escalated"] is False

    async def test_filter_by_overflow_escalated_true(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(1,)),
                _cursor(
                    fetchall=[
                        _make_invocation_row(
                            id="inv-escalated",
                            estimated_tokens_in=8200,
                            overflow_escalated=1,
                        )
                    ]
                ),
            ]
        )
        result = await list_invocations(request, overflow_escalated=True)
        # Verify SQL got the overflow_escalated filter clause
        call_args = conn.execute.call_args_list[0]
        assert "overflow_escalated = ?" in call_args[0][0]
        inv = result["invocations"][0]
        assert inv["overflow_escalated"] is True
        assert inv["estimated_tokens_in"] == 8200

    async def test_no_filter_when_overflow_escalated_none(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(0,)),
                _cursor(),
            ]
        )
        await list_invocations(request, overflow_escalated=None)
        call_args = conn.execute.call_args_list[0]
        assert "overflow_escalated = ?" not in call_args[0][0]

    async def test_pagination(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(100,)),
                _cursor(fetchall=[_make_invocation_row()]),
            ]
        )
        result = await list_invocations(request, limit=10, offset=20)
        assert result["limit"] == 10
        assert result["offset"] == 20
        assert result["total"] == 100


# ---------------------------------------------------------------------------
# get_invocation
# ---------------------------------------------------------------------------


class TestGetInvocation:
    async def test_not_found_raises_404(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(return_value=_cursor(fetchone=None))
        with pytest.raises(HTTPException) as exc_info:
            await get_invocation(request, invocation_id="nonexistent")
        assert exc_info.value.status_code == 404

    async def test_found_with_json_output_no_linked_task(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=_make_detail_row()),  # invocation
                _cursor(fetchone=None),  # no linked task found
            ]
        )
        result = await get_invocation(request, invocation_id="inv-001")
        assert result["id"] == "inv-001"
        assert result["output"] == {"title": "Buy milk"}
        assert result["linked_task"] is None

    async def test_found_with_invalid_json_output(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            return_value=_cursor(fetchone=_make_detail_row(output="not-json"))
        )
        result = await get_invocation(request, invocation_id="inv-001")
        assert result["output"] == {"raw": "not-json"}

    async def test_found_with_linked_task(self, mock_request: tuple) -> None:
        request, conn = mock_request
        task_row = ("task-001", "Buy milk", "done", "personal", 1, "2026-04-01", "pm", "completed")
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=_make_detail_row()),  # invocation
                _cursor(fetchone=task_row),  # linked task
            ]
        )
        result = await get_invocation(request, invocation_id="inv-001")
        assert result["linked_task"]["id"] == "task-001"
        assert result["linked_task"]["title"] == "Buy milk"

    async def test_found_with_null_output(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            return_value=_cursor(fetchone=_make_detail_row(output=None))
        )
        result = await get_invocation(request, invocation_id="inv-001")
        assert result["output"] is None

    async def test_detail_includes_context_budget_fields(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=_make_detail_row(estimated_tokens_in=1480, overflow_escalated=1)),
                _cursor(fetchone=None),
            ]
        )
        result = await get_invocation(request, invocation_id="inv-001")
        assert result["estimated_tokens_in"] == 1480
        assert result["overflow_escalated"] is True
