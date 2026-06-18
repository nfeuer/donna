"""Agent framework base types.

Defines the shared result/record/context dataclasses used by Donna's live
agents (Challenger, NoveltyJudge, Prep) and the tool registry. See
docs/domain/agents.md for the live agent execution flow.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING, Any

from donna.models.router import ModelRouter
from donna.tasks.database import Database

if TYPE_CHECKING:
    from donna.agents.tool_registry import ToolRegistry


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
    tool_registry: ToolRegistry


@dataclasses.dataclass
class AgentResult:
    """Outcome of an agent execution."""

    status: str  # "complete", "failed", "needs_input", "escalated"
    output: dict[str, Any]
    tool_calls_made: list[ToolCallRecord] = dataclasses.field(default_factory=list)
    duration_ms: int = 0
    error: str | None = None
    questions: list[str] | None = None  # populated when status == "needs_input"
