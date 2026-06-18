"""Agent framework base types.

Defines the shared result/record/context dataclasses used by Donna's live
agents (Challenger, NoveltyJudge, Prep). See docs/domain/agents.md for the live
agent execution flow.

R3 (§7.2 resolution —
``docs/superpowers/specs/2026-06-17-subagent-72-resolution-design.md``) stripped
the unused ``db`` and ``tool_registry`` fields from :class:`AgentContext`. The
live agents only ever read ``router`` / ``user_id`` (and ``project_root``); the
raw ``db`` handle let an agent bypass the tool-validation seam, contradicting
CLAUDE.md principle #6. The separate, dead agent-layer ``ToolRegistry`` it
referenced was deleted in the same slice — the *live* registry is
:class:`donna.skills.tool_registry.ToolRegistry`.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from donna.models.router import ModelRouter


@dataclasses.dataclass(frozen=True)
class ToolCallRecord:
    """Record of a single tool call made by an agent."""

    tool_name: str
    params: dict[str, Any]
    result: dict[str, Any]
    allowed: bool


@dataclasses.dataclass(frozen=True)
class AgentContext:
    """Everything a live agent needs to do its work.

    Carries only what the live agents (Challenger, NoveltyJudge) read: the
    model ``router``, the ``user_id``, and the ``project_root``. Agents act on
    the world through the model router and validated tool dispatch, never a raw
    DB handle (CLAUDE.md principle #6).
    """

    router: ModelRouter
    user_id: str
    project_root: Path


@dataclasses.dataclass
class AgentResult:
    """Outcome of an agent execution."""

    status: str  # "complete", "failed", "needs_input", "escalated"
    output: dict[str, Any]
    tool_calls_made: list[ToolCallRecord] = dataclasses.field(default_factory=list)
    duration_ms: int = 0
    error: str | None = None
    questions: list[str] | None = None  # populated when status == "needs_input"
