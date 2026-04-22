"""Unit tests for the admin agents endpoints."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from donna.api.routes.admin_agents import (
    _build_agent_task_map,
    _load_yaml,
    get_agent_detail,
    list_agents,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cursor(fetchall: list | None = None, fetchone: tuple | None = None) -> AsyncMock:
    c = AsyncMock()
    c.fetchall = AsyncMock(return_value=fetchall or [])
    c.fetchone = AsyncMock(return_value=fetchone)
    return c


def _make_agents_yaml() -> dict:
    return {
        "agents": {
            "pm": {
                "enabled": True,
                "timeout_seconds": 30,
                "autonomy": "medium",
                "allowed_tools": ["create_task", "update_task"],
            },
            "scheduler": {
                "enabled": True,
                "timeout_seconds": 60,
                "autonomy": "low",
                "allowed_tools": ["read_calendar"],
            },
        }
    }


def _make_task_types_yaml() -> dict:
    return {
        "task_types": {
            "parse_task": {"tools": ["create_task"]},
            "generate_reminder": {"tools": ["read_calendar"]},
        }
    }


# ---------------------------------------------------------------------------
# _load_yaml
# ---------------------------------------------------------------------------


class TestLoadYaml:
    def test_loads_existing_file(self, tmp_path: Path) -> None:
        (tmp_path / "test.yaml").write_text("key: value")
        result = _load_yaml(tmp_path / "test.yaml")
        assert result == {"key": "value"}

    def test_returns_empty_for_missing_file(self, tmp_path: Path) -> None:
        result = _load_yaml(tmp_path / "nonexistent.yaml")
        assert result == {}

    def test_returns_empty_for_empty_file(self, tmp_path: Path) -> None:
        (tmp_path / "empty.yaml").write_text("")
        result = _load_yaml(tmp_path / "empty.yaml")
        assert result == {}


# ---------------------------------------------------------------------------
# _build_agent_task_map
# ---------------------------------------------------------------------------


class TestBuildAgentTaskMap:
    def test_well_known_mappings(self, tmp_path: Path) -> None:
        (tmp_path / "agents.yaml").write_text("agents:\n  pm:\n    allowed_tools: []")
        (tmp_path / "task_types.yaml").write_text("task_types: {}")
        result = _build_agent_task_map(tmp_path)
        assert "parse_task" in result.get("pm", [])

    def test_tool_overlap_detection(self, tmp_path: Path) -> None:
        import yaml
        (tmp_path / "agents.yaml").write_text(yaml.dump(_make_agents_yaml()))
        (tmp_path / "task_types.yaml").write_text(yaml.dump(_make_task_types_yaml()))
        result = _build_agent_task_map(tmp_path)
        # pm has create_task → matches parse_task
        assert "parse_task" in result["pm"]
        # scheduler has read_calendar → matches generate_reminder
        assert "generate_reminder" in result["scheduler"]

    def test_empty_config(self, tmp_path: Path) -> None:
        (tmp_path / "agents.yaml").write_text("agents: {}")
        (tmp_path / "task_types.yaml").write_text("task_types: {}")
        result = _build_agent_task_map(tmp_path)
        assert result == {}


# ---------------------------------------------------------------------------
# list_agents
# ---------------------------------------------------------------------------


class TestListAgents:
    async def test_agents_with_metrics(self, mock_request: tuple) -> None:
        request, conn = mock_request
        metrics_row = (10, 500.0, 0.05, "2026-04-01T10:00:00Z")
        conn.execute = AsyncMock(return_value=_cursor(fetchone=metrics_row))

        with patch("donna.api.routes.admin_agents._load_yaml") as mock_yaml, \
             patch("donna.api.routes.admin_agents._build_agent_task_map") as mock_map:
            mock_yaml.return_value = _make_agents_yaml()["agents"]
            # Wrap: _load_yaml is called as .get("agents", {})
            mock_yaml.return_value = {"agents": _make_agents_yaml()["agents"]}
            mock_yaml.return_value = _make_agents_yaml()
            mock_map.return_value = {"pm": ["parse_task"], "scheduler": ["generate_reminder"]}

            result = await list_agents(request)

        assert len(result["agents"]) == 2
        pm = next(a for a in result["agents"] if a["name"] == "pm")
        assert pm["enabled"] is True
        assert pm["total_calls"] == 10

    async def test_agents_with_no_invocations(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(return_value=_cursor(fetchone=(0, 0.0, 0.0, None)))

        with patch("donna.api.routes.admin_agents._load_yaml") as mock_yaml, \
             patch("donna.api.routes.admin_agents._build_agent_task_map") as mock_map:
            mock_yaml.return_value = _make_agents_yaml()
            mock_map.return_value = {"pm": ["parse_task"], "scheduler": []}

            result = await list_agents(request)

        sched = next(a for a in result["agents"] if a["name"] == "scheduler")
        assert sched["total_calls"] == 0


# ---------------------------------------------------------------------------
# get_agent_detail
# ---------------------------------------------------------------------------


class TestGetAgentDetail:
    async def test_not_found_raises_404(self, mock_request: tuple) -> None:
        request, _conn = mock_request
        with patch("donna.api.routes.admin_agents._load_yaml") as mock_yaml:
            mock_yaml.return_value = _make_agents_yaml()
            with pytest.raises(HTTPException) as exc_info:
                await get_agent_detail(request, name="nonexistent")
            assert exc_info.value.status_code == 404

    async def test_agent_with_no_task_types(self, mock_request: tuple) -> None:
        request, _conn = mock_request
        with patch("donna.api.routes.admin_agents._load_yaml") as mock_yaml, \
             patch("donna.api.routes.admin_agents._build_agent_task_map") as mock_map:
            mock_yaml.return_value = _make_agents_yaml()
            mock_map.return_value = {"pm": [], "scheduler": []}

            result = await get_agent_detail(request, name="pm")

        assert result["recent_invocations"] == []
        assert result["cost_summary"]["total_calls"] == 0

    async def test_agent_with_full_metrics(self, mock_request: tuple) -> None:
        request, conn = mock_request
        inv_row = (
            "inv-1", "2026-04-01", "parse_task", "claude-sonnet",
            500, 1000, 200, 0.003, 0, "task-001",
        )
        latency_row = ("2026-04-01", 450.5, 5)
        cost_row = (10, 0.03, 0.003)

        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchall=[inv_row]),   # recent invocations
                _cursor(fetchall=[latency_row]),  # daily latency
                _cursor(fetchall=[('{"tools_called": ["create_task"]}',)]),  # tool usage
                _cursor(fetchone=cost_row),  # cost summary
            ]
        )

        with patch("donna.api.routes.admin_agents._load_yaml") as mock_yaml, \
             patch("donna.api.routes.admin_agents._build_agent_task_map") as mock_map:
            mock_yaml.return_value = _make_agents_yaml()
            mock_map.return_value = {"pm": ["parse_task"], "scheduler": []}

            result = await get_agent_detail(request, name="pm")

        assert len(result["recent_invocations"]) == 1
        assert result["daily_latency"][0]["avg_latency_ms"] == 450.5
        assert result["cost_summary"]["total_calls"] == 10
