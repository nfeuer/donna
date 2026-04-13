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
from typing import Any

import structlog

from donna.agents.base import AgentContext, AgentResult
from donna.models.router import ContextOverflowError
from donna.tasks.database import TaskRow

logger = structlog.get_logger()

_TASK_TYPE = "challenge_task"
_TIMEOUT_SECONDS = 120  # 2 minutes


class ChallengerAgent:
    """Probes task quality and asks follow-up questions when context is thin."""

    @property
    def name(self) -> str:
        return "challenger"

    @property
    def allowed_tools(self) -> list[str]:
        return ["task_db_read"]

    @property
    def timeout_seconds(self) -> int:
        return _TIMEOUT_SECONDS

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
