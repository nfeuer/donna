"""Tests for the AgentDispatcher."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.agents.base import AgentResult
from donna.agents.tool_registry import ToolRegistry
from donna.config import TaskTypesConfig
from donna.orchestrator.dispatcher import AgentDispatcher


def _make_task(**overrides: object) -> MagicMock:
    defaults = {
        "id": "task-001",
        "user_id": "nick",
        "title": "Test task",
        "description": "A test",
        "domain": "personal",
        "priority": 2,
    }
    defaults.update(overrides)
    task = MagicMock()
    for k, v in defaults.items():
        setattr(task, k, v)
    return task


def _make_agent(name: str, result: AgentResult) -> MagicMock:
    agent = MagicMock()
    agent.name = name
    agent.allowed_tools = []
    agent.timeout_seconds = 60
    agent.execute = AsyncMock(return_value=result)
    return agent


class TestAgentDispatcher:
    async def test_full_dispatch_flow(self) -> None:
        pm_result = AgentResult(
            status="complete",
            output={"recommended_agent": "scheduler"},
        )
        sched_result = AgentResult(
            status="complete",
            output={"slot_start": "2026-04-03T09:00:00"},
            duration_ms=50,
        )

        pm = _make_agent("pm", pm_result)
        sched = _make_agent("scheduler", sched_result)

        dispatcher = AgentDispatcher(
            agents={"pm": pm, "scheduler": sched},
            tool_registry=ToolRegistry(TaskTypesConfig(task_types={})),
            router=AsyncMock(),
            db=AsyncMock(),
            project_root=Path("/tmp"),
        )

        result = await dispatcher.dispatch(_make_task(), user_id="nick")

        assert result.status == "complete"
        assert result.output["slot_start"] == "2026-04-03T09:00:00"
        pm.execute.assert_called_once()
        sched.execute.assert_called_once()

    async def test_pm_needs_input_stops_dispatch(self) -> None:
        pm_result = AgentResult(
            status="needs_input",
            output={},
            questions=["What's the deadline?"],
        )
        pm = _make_agent("pm", pm_result)
        sched = _make_agent("scheduler", AgentResult(status="complete", output={}))

        dispatcher = AgentDispatcher(
            agents={"pm": pm, "scheduler": sched},
            tool_registry=ToolRegistry(TaskTypesConfig(task_types={})),
            router=AsyncMock(),
            db=AsyncMock(),
            project_root=Path("/tmp"),
        )

        result = await dispatcher.dispatch(_make_task(), user_id="nick")

        assert result.status == "needs_input"
        assert "What's the deadline?" in result.questions
        sched.execute.assert_not_called()

    async def test_pm_failure_stops_dispatch(self) -> None:
        pm_result = AgentResult(
            status="failed",
            output={},
            error="LLM timeout",
        )
        pm = _make_agent("pm", pm_result)

        dispatcher = AgentDispatcher(
            agents={"pm": pm},
            tool_registry=ToolRegistry(TaskTypesConfig(task_types={})),
            router=AsyncMock(),
            db=AsyncMock(),
            project_root=Path("/tmp"),
        )

        result = await dispatcher.dispatch(_make_task(), user_id="nick")
        assert result.status == "failed"
        assert "LLM timeout" in result.error

    async def test_no_pm_agent_returns_failure(self) -> None:
        dispatcher = AgentDispatcher(
            agents={},
            tool_registry=ToolRegistry(TaskTypesConfig(task_types={})),
            router=AsyncMock(),
            db=AsyncMock(),
            project_root=Path("/tmp"),
        )

        result = await dispatcher.dispatch(_make_task(), user_id="nick")
        assert result.status == "failed"
        assert "PM agent" in result.error

    async def test_fallback_to_scheduler_when_recommended_missing(self) -> None:
        pm_result = AgentResult(
            status="complete",
            output={"recommended_agent": "coding"},
        )
        sched_result = AgentResult(status="complete", output={"fallback": True})

        pm = _make_agent("pm", pm_result)
        sched = _make_agent("scheduler", sched_result)

        dispatcher = AgentDispatcher(
            agents={"pm": pm, "scheduler": sched},
            tool_registry=ToolRegistry(TaskTypesConfig(task_types={})),
            router=AsyncMock(),
            db=AsyncMock(),
            project_root=Path("/tmp"),
        )

        result = await dispatcher.dispatch(_make_task(), user_id="nick")
        assert result.status == "complete"
        assert result.output["fallback"] is True
