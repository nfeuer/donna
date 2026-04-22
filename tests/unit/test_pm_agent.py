"""Tests for the PM Agent."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from donna.agents.base import AgentContext
from donna.agents.pm_agent import PMAgent
from donna.agents.tool_registry import ToolRegistry
from donna.config import TaskTypeEntry, TaskTypesConfig
from donna.models.types import CompletionMetadata


def _make_task(**overrides: object) -> MagicMock:
    """Create a mock TaskRow with sensible defaults."""
    defaults = {
        "id": "task-001",
        "user_id": "nick",
        "title": "Refactor API module",
        "description": "Refactor the API module for better testability",
        "domain": "work",
        "priority": 3,
        "deadline": None,
        "estimated_duration": 120,
        "tags": "[]",
        "prep_work_instructions": None,
        "prep_work_flag": False,
        "agent_eligible": True,
    }
    defaults.update(overrides)
    task = MagicMock()
    for k, v in defaults.items():
        setattr(task, k, v)
    return task


def _make_context(router_result: dict, metadata: CompletionMetadata | None = None) -> AgentContext:
    """Create a mock AgentContext."""
    if metadata is None:
        metadata = CompletionMetadata(
            latency_ms=100,
            tokens_in=200,
            tokens_out=100,
            cost_usd=0.003,
            model_actual="anthropic/claude-sonnet-4-20250514",
        )

    router = AsyncMock()
    router.complete = AsyncMock(return_value=(router_result, metadata))

    db = AsyncMock()
    tool_registry = ToolRegistry(
        TaskTypesConfig(task_types={
            "task_decompose": TaskTypeEntry(
                description="d", model="reasoner",
                prompt_template="p", output_schema="s",
            )
        })
    )

    return AgentContext(
        router=router,
        db=db,
        user_id="nick",
        project_root=Path("/tmp"),
        tool_registry=tool_registry,
    )


class TestPMAgent:
    async def test_complete_task_returns_agent_recommendation(self) -> None:
        agent = PMAgent()
        llm_result = {
            "assessment": "ready",
            "missing_information": [],
            "suggested_approach": "Schedule and execute",
            "suggested_agent": "scheduler",
            "subtasks": [],
            "total_estimated_hours": 2.0,
            "suggested_deadline_feasible": True,
        }
        context = _make_context(llm_result)
        task = _make_task()

        result = await agent.execute(task, context)

        assert result.status == "complete"
        assert result.output["recommended_agent"] == "scheduler"
        assert result.duration_ms >= 0

    async def test_incomplete_task_returns_questions(self) -> None:
        agent = PMAgent()
        llm_result = {
            "assessment": "needs_info",
            "missing_information": [
                {"field": "description", "question": "Which API endpoints?"},
                {"field": "scope", "question": "Backward compatibility?"},
            ],
            "subtasks": [],
            "total_estimated_hours": 0,
        }
        context = _make_context(llm_result)
        task = _make_task(description=None)

        result = await agent.execute(task, context)

        assert result.status == "needs_input"
        assert len(result.questions) == 2
        assert "Which API endpoints?" in result.questions

    async def test_llm_failure_returns_failed(self) -> None:
        agent = PMAgent()
        context = _make_context({})
        context.router.complete = AsyncMock(side_effect=RuntimeError("API down"))
        task = _make_task()

        result = await agent.execute(task, context)

        assert result.status == "failed"
        assert "API down" in result.error

    def test_agent_properties(self) -> None:
        agent = PMAgent()
        assert agent.name == "pm"
        assert "task_db_read" in agent.allowed_tools
        assert agent.timeout_seconds == 300
