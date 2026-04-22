"""Unit tests for the admin tasks endpoints."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from donna.api.routes.admin_tasks import get_task_admin, list_tasks_admin

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cursor(fetchall: list | None = None, fetchone: tuple | None = None) -> AsyncMock:
    c = AsyncMock()
    c.fetchall = AsyncMock(return_value=fetchall or [])
    c.fetchone = AsyncMock(return_value=fetchone)
    return c


def _make_task_list_row(**overrides: object) -> tuple:
    """Build a task row for list_tasks_admin (26 columns)."""
    defaults = {
        "id": "task-001",
        "user_id": "nick",
        "title": "Buy milk",
        "description": "From the store",
        "domain": "personal",
        "priority": 1,
        "status": "backlog",
        "estimated_duration": 15,
        "deadline": None,
        "deadline_type": "none",
        "scheduled_start": None,
        "actual_start": None,
        "completed_at": None,
        "parent_task": None,
        "prep_work_flag": 0,
        "agent_eligible": 0,
        "assigned_agent": None,
        "agent_status": None,
        "tags": json.dumps(["shopping"]),
        "notes": None,
        "reschedule_count": 0,
        "created_at": "2026-04-01T10:00:00Z",
        "created_via": "discord",
        "nudge_count": 0,
        "quality_score": None,
        "donna_managed": 0,
    }
    defaults.update(overrides)
    return tuple(defaults.values())


def _make_task_detail_row(**overrides: object) -> tuple:
    """Build a task row for get_task_admin (31 columns)."""
    defaults = {
        "id": "task-001",
        "user_id": "nick",
        "title": "Buy milk",
        "description": "From the store",
        "domain": "personal",
        "priority": 1,
        "status": "done",
        "estimated_duration": 15,
        "deadline": "2026-04-05",
        "deadline_type": "hard",
        "scheduled_start": "2026-04-02T09:00:00Z",
        "actual_start": "2026-04-02T09:15:00Z",
        "completed_at": "2026-04-02T09:30:00Z",
        "recurrence": None,
        "dependencies": json.dumps(["task-000"]),
        "parent_task": None,
        "prep_work_flag": 0,
        "prep_work_instructions": None,
        "agent_eligible": 1,
        "assigned_agent": "pm",
        "agent_status": "completed",
        "tags": json.dumps(["shopping"]),
        "notes": json.dumps([{"text": "Got 2% milk"}]),
        "reschedule_count": 1,
        "created_at": "2026-04-01T10:00:00Z",
        "created_via": "discord",
        "estimated_cost": 0.01,
        "calendar_event_id": "cal-123",
        "donna_managed": 1,
        "nudge_count": 2,
        "quality_score": 0.95,
    }
    defaults.update(overrides)
    return tuple(defaults.values())


# ---------------------------------------------------------------------------
# list_tasks_admin
# ---------------------------------------------------------------------------


class TestListTasksAdmin:
    async def test_empty_result(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[_cursor(fetchone=(0,)), _cursor()]
        )
        result = await list_tasks_admin(request)
        assert result["total"] == 0
        assert result["tasks"] == []

    async def test_returns_tasks_with_parsed_tags(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(1,)),
                _cursor(fetchall=[_make_task_list_row()]),
            ]
        )
        result = await list_tasks_admin(request)
        assert len(result["tasks"]) == 1
        assert result["tasks"][0]["tags"] == ["shopping"]
        assert result["tasks"][0]["prep_work_flag"] is False

    async def test_filter_by_status(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[_cursor(fetchone=(0,)), _cursor()]
        )
        await list_tasks_admin(request, status="done")
        sql = conn.execute.call_args_list[0][0][0]
        assert "status = ?" in sql

    async def test_filter_by_search(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[_cursor(fetchone=(0,)), _cursor()]
        )
        await list_tasks_admin(request, search="milk")
        sql = conn.execute.call_args_list[0][0][0]
        assert "title LIKE ?" in sql

    async def test_filter_by_agent(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[_cursor(fetchone=(0,)), _cursor()]
        )
        await list_tasks_admin(request, agent="pm")
        sql = conn.execute.call_args_list[0][0][0]
        assert "assigned_agent = ?" in sql

    async def test_invalid_tags_json(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(1,)),
                _cursor(fetchall=[_make_task_list_row(tags="not-json")]),
            ]
        )
        result = await list_tasks_admin(request)
        assert result["tasks"][0]["tags"] is None


# ---------------------------------------------------------------------------
# get_task_admin
# ---------------------------------------------------------------------------


class TestGetTaskAdmin:
    async def test_not_found_raises_404(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(return_value=_cursor(fetchone=None))
        with pytest.raises(HTTPException) as exc_info:
            await get_task_admin(request, task_id="nonexistent")
        assert exc_info.value.status_code == 404

    async def test_found_with_linked_entities(self, mock_request: tuple) -> None:
        request, conn = mock_request
        inv_row = ("inv-1", "2026-04-02", "parse_task", "claude-sonnet", 500, 1000, 200, 0.003, 0)
        nudge_row = ("nudge-1", "reminder", "discord", 1, "Hey!", 0, "2026-04-02")
        correction_row = ("corr-1", "2026-04-02", "title", "Milk", "Buy milk", "parse_task", "get milk")
        subtask_row = ("task-002", "Get 2% milk", "done", 1, "pm", "completed")

        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=_make_task_detail_row()),  # task
                _cursor(fetchall=[inv_row]),      # invocations
                _cursor(fetchall=[nudge_row]),    # nudges
                _cursor(fetchall=[correction_row]),  # corrections
                _cursor(fetchall=[subtask_row]),  # subtasks
            ]
        )
        result = await get_task_admin(request, task_id="task-001")
        assert result["id"] == "task-001"
        assert result["dependencies"] == ["task-000"]
        assert result["notes"] == [{"text": "Got 2% milk"}]
        assert len(result["invocations"]) == 1
        assert len(result["nudge_events"]) == 1
        assert len(result["corrections"]) == 1
        assert len(result["subtasks"]) == 1

    async def test_found_with_empty_linked_entities(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=_make_task_detail_row()),
                _cursor(),  # no invocations
                _cursor(),  # no nudges
                _cursor(),  # no corrections
                _cursor(),  # no subtasks
            ]
        )
        result = await get_task_admin(request, task_id="task-001")
        assert result["invocations"] == []
        assert result["nudge_events"] == []
        assert result["corrections"] == []
        assert result["subtasks"] == []
