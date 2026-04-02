"""Agent framework base types and protocols.

Defines the execution contract for all Donna sub-agents (PM, Scheduler,
Research, Coding, Communication). See docs/agents.md for the hierarchy
and safety constraints.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from donna.models.router import ModelRouter
from donna.tasks.database import Database, TaskRow


@dataclasses.dataclass(frozen=True)
class ToolCallRecord:
    """Record of a single tool call made by an agent."""

    tool_name: str
    params: dict[str, Any]
    result: dict[str, Any]
    allowed: bool


@dataclasses.dataclass(frozen=True)
class AgentContext:
    """Everything an agent needs to do its work."""

    router: ModelRouter
    db: Database
    user_id: str
    project_root: Path
    tool_registry: ToolRegistry  # type: ignore[name-defined]  # forward ref


@dataclasses.dataclass
class AgentResult:
    """Outcome of an agent execution."""

    status: str  # "complete", "failed", "needs_input", "escalated"
    output: dict[str, Any]
    tool_calls_made: list[ToolCallRecord] = dataclasses.field(default_factory=list)
    duration_ms: int = 0
    error: str | None = None
    questions: list[str] | None = None  # populated when status == "needs_input"


@runtime_checkable
class Agent(Protocol):
    """Protocol for all Donna sub-agents."""

    @property
    def name(self) -> str: ...

    @property
    def allowed_tools(self) -> list[str]: ...

    @property
    def timeout_seconds(self) -> int: ...

    async def execute(self, task: TaskRow, context: AgentContext) -> AgentResult: ...


# Resolve the forward reference for AgentContext.tool_registry
from donna.agents.tool_registry import ToolRegistry as _ToolRegistry  # noqa: E402

AgentContext.__annotations__["tool_registry"] = _ToolRegistry
