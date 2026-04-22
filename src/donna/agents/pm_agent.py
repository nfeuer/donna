"""Project Manager agent — Phase 3.

Evaluates task completeness, generates targeted questions for missing
information, packages tasks with full context, and recommends dispatch
to the appropriate execution agent.

See docs/agents.md — Agent Execution Flow.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from donna.agents.base import AgentContext, AgentResult
from donna.models.router import ContextOverflowError
from donna.tasks.database import TaskRow

logger = structlog.get_logger()

_TASK_TYPE = "task_decompose"  # reuse the reasoner model for PM assessment
_TIMEOUT_SECONDS = 300  # 5 minutes


class PMAgent:
    """Project Manager agent.

    Assesses task completeness, asks targeted questions if information
    is missing, and packages the task for dispatch to an execution agent.
    """

    @property
    def name(self) -> str:
        return "pm"

    @property
    def allowed_tools(self) -> list[str]:
        return ["task_db_read", "task_db_write"]

    @property
    def timeout_seconds(self) -> int:
        return _TIMEOUT_SECONDS

    async def execute(self, task: TaskRow, context: AgentContext) -> AgentResult:
        """Assess task and either request more info or package for dispatch."""
        start = time.monotonic()

        prompt = self._build_assessment_prompt(task)

        try:
            result, _metadata = await context.router.complete(
                prompt, task_type=_TASK_TYPE, user_id=context.user_id
            )
        except ContextOverflowError:
            raise
        except Exception as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            logger.error("pm_agent_llm_failed", task_id=task.id, error=str(exc))
            return AgentResult(
                status="failed",
                output={},
                duration_ms=elapsed,
                error=str(exc),
            )

        elapsed = int((time.monotonic() - start) * 1000)

        # Determine if the task needs more information.
        missing = result.get("missing_information", [])
        if missing:
            questions = [item.get("question", str(item)) for item in missing]
            logger.info(
                "pm_agent_needs_input",
                task_id=task.id,
                question_count=len(questions),
            )
            return AgentResult(
                status="needs_input",
                output=result,
                duration_ms=elapsed,
                questions=questions,
            )

        # Task is complete — recommend an execution agent.
        recommended_agent = self._recommend_agent(task, result)

        logger.info(
            "pm_agent_assessed",
            task_id=task.id,
            recommended_agent=recommended_agent,
            duration_ms=elapsed,
        )

        return AgentResult(
            status="complete",
            output={
                **result,
                "recommended_agent": recommended_agent,
                "task_id": task.id,
            },
            duration_ms=elapsed,
        )

    def _build_assessment_prompt(self, task: TaskRow) -> str:
        """Build a prompt for task completeness assessment."""
        return f"""Assess the following task for completeness. Identify any missing information
that would be needed to execute it. If information is missing, list specific
questions to ask the user. If the task is ready, describe what needs to be done.

Task:
- Title: {task.title}
- Description: {task.description or 'None provided'}
- Domain: {task.domain}
- Priority: {task.priority}
- Deadline: {task.deadline or 'None'}
- Estimated duration: {task.estimated_duration or 'Unknown'}
- Tags: {task.tags or '[]'}
- Prep work instructions: {task.prep_work_instructions or 'None'}

Respond with JSON:
{{
  "assessment": "ready" or "needs_info",
  "missing_information": [
    {{"field": "description", "question": "What specific API endpoints need refactoring?"}}
  ],
  "suggested_approach": "Brief description of how to execute this task",
  "suggested_agent": "scheduler" or "research" or "coding" or "communication",
  "subtasks": [],
  "total_estimated_hours": 0,
  "suggested_deadline_feasible": true
}}"""

    def _recommend_agent(self, task: TaskRow, assessment: dict[str, Any]) -> str:
        """Determine which execution agent should handle this task."""
        suggested = assessment.get("suggested_agent", "")
        if suggested in ("scheduler", "research", "coding", "communication"):
            return suggested

        # Fallback heuristics based on task properties.
        if task.prep_work_flag:
            return "research"
        if task.agent_eligible:
            return "scheduler"
        return "scheduler"
