"""Challenger agent — probes newly created tasks for quality and context.

Unlike the PM Agent which checks for missing *fields*, the Challenger
evaluates whether the task description is rich enough to execute well.
It asks follow-up questions about success criteria, hidden dependencies,
and scope boundaries.

Runs on the local LLM (via ``challenge_task`` task type) to keep costs
at zero. Falls through silently if the task is already well-specified.

See docs/agents.md for the agent hierarchy.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog

from donna.agents.base import AgentContext, AgentResult
from donna.capabilities.matcher import CapabilityMatcher, MatchConfidence
from donna.capabilities.models import CapabilityRow
from donna.models.router import ContextOverflowError
from donna.tasks.database import TaskRow

logger = structlog.get_logger()

_TASK_TYPE = "challenge_task"
_TIMEOUT_SECONDS = 120  # 2 minutes


@dataclass(slots=True)
class ChallengerMatchResult:
    """Result of ChallengerAgent.match_and_extract."""
    status: str  # ready | needs_input | escalate_to_claude | ambiguous
    intent_kind: str = "task"  # task | automation | question | chat
    capability: CapabilityRow | None = None
    extracted_inputs: dict[str, Any] = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)
    clarifying_question: str | None = None
    match_score: float = 0.0
    # Wave 3 extensions
    schedule: dict[str, Any] | None = None  # {cron, human_readable} when intent_kind=automation
    deadline: datetime | None = None  # when intent_kind=task
    alert_conditions: dict[str, Any] | None = None  # {expression, channels}
    confidence: float = 0.0  # LLM self-assessed confidence 0..1
    low_quality_signals: list[str] = field(default_factory=list)


class ChallengerAgent:
    """Probes task quality and asks follow-up questions when context is thin."""

    def __init__(
        self,
        *,
        matcher: CapabilityMatcher | None = None,
        input_extractor: Any | None = None,
    ) -> None:
        self._matcher = matcher
        self._input_extractor = input_extractor

    @property
    def name(self) -> str:
        return "challenger"

    @property
    def allowed_tools(self) -> list[str]:
        return ["task_db_read"]

    @property
    def timeout_seconds(self) -> int:
        return _TIMEOUT_SECONDS

    async def match_and_extract(
        self,
        user_message: str,
        user_id: str,
    ) -> ChallengerMatchResult:
        """Match a user message against the capability registry and extract inputs."""
        if self._matcher is None:
            return ChallengerMatchResult(status="escalate_to_claude", match_score=0.0)

        match = await self._matcher.match(user_message)

        if match.confidence == MatchConfidence.LOW:
            return ChallengerMatchResult(
                status="escalate_to_claude",
                capability=None,
                match_score=match.best_score,
            )

        cap = match.best_match
        assert cap is not None

        if self._input_extractor is None:
            return ChallengerMatchResult(
                status="ready",
                capability=cap,
                match_score=match.best_score,
            )

        extracted = await self._input_extractor.extract(
            user_message=user_message,
            schema=cap.input_schema,
            user_id=user_id,
        )

        required = cap.input_schema.get("required", [])
        missing = [f for f in required if f not in extracted or extracted[f] in (None, "")]

        if missing:
            question = self._build_clarifying_question_for_fields(cap, missing)
            status = "needs_input" if match.confidence == MatchConfidence.HIGH else "ambiguous"
            return ChallengerMatchResult(
                status=status,
                capability=cap,
                extracted_inputs=extracted,
                missing_fields=missing,
                clarifying_question=question,
                match_score=match.best_score,
            )

        return ChallengerMatchResult(
            status="ready",
            capability=cap,
            extracted_inputs=extracted,
            missing_fields=[],
            match_score=match.best_score,
        )

    def _build_clarifying_question_for_fields(
        self, cap: CapabilityRow, missing: list[str]
    ) -> str:
        """Phase 1: simple templated question for missing fields."""
        props = cap.input_schema.get("properties", {})
        field_descriptions = []
        for f in missing:
            desc = props.get(f, {}).get("description", f)
            field_descriptions.append(f"- {f}: {desc}")

        return (
            f"I need a bit more to act on this as a {cap.name}:\n"
            + "\n".join(field_descriptions)
        )

    async def execute(self, task: TaskRow, context: AgentContext) -> AgentResult:
        """Evaluate task quality and return follow-up questions if needed."""
        start = time.monotonic()

        prompt = self._build_challenge_prompt(task)

        try:
            result, metadata = await context.router.complete(
                prompt, task_type=_TASK_TYPE, user_id=context.user_id
            )
        except ContextOverflowError:
            raise
        except Exception as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            logger.error("challenger_agent_llm_failed", task_id=task.id, error=str(exc))
            # On failure, let the task proceed — don't block on challenger errors.
            return AgentResult(
                status="complete",
                output={"challenger_skipped": True, "reason": str(exc)},
                duration_ms=elapsed,
            )

        elapsed = int((time.monotonic() - start) * 1000)

        needs_clarification = result.get("needs_clarification", False)
        questions = result.get("questions", [])

        if needs_clarification and questions:
            logger.info(
                "challenger_agent_needs_input",
                task_id=task.id,
                question_count=len(questions),
                reasoning=result.get("reasoning", ""),
            )
            return AgentResult(
                status="needs_input",
                output=result,
                duration_ms=elapsed,
                questions=questions,
            )

        logger.info(
            "challenger_agent_approved",
            task_id=task.id,
            duration_ms=elapsed,
        )

        return AgentResult(
            status="complete",
            output={**result, "task_id": task.id},
            duration_ms=elapsed,
        )

    def _build_challenge_prompt(self, task: TaskRow) -> str:
        """Build a prompt for task quality evaluation."""
        return f"""You are Donna's task quality reviewer. A new task has been created.
Evaluate if it has enough context to execute well.

Task:
- Title: {task.title}
- Description: {task.description or 'None provided'}
- Domain: {task.domain}
- Priority: {task.priority}
- Deadline: {task.deadline or 'None'}
- Estimated duration: {task.estimated_duration or 'Unknown'}
- Tags: {task.tags or '[]'}

Generate 1-3 follow-up questions ONLY if the task is vague or missing
critical context. Questions should probe:
- What "done" looks like (success criteria)
- Hidden dependencies or blockers
- Scope boundaries (what's NOT included)

If the task is clear and actionable as-is, return no questions.

Respond with JSON:
{{
  "needs_clarification": true or false,
  "questions": ["What does done look like for this?"],
  "reasoning": "Brief explanation of why questions are needed or why task is clear"
}}"""
