"""TriageAgent — handles skill runtime failures with structured decisions."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger()

MAX_RETRY_COUNT = 3


class TriageDecision(str, enum.Enum):
    RETRY_STEP = "retry_step_with_modified_prompt"
    SKIP_STEP = "skip_step"
    ESCALATE_TO_CLAUDE = "escalate_to_claude"
    ALERT_USER = "alert_user"
    MARK_SKILL_DEGRADED = "mark_skill_degraded"


@dataclass(slots=True)
class TriageInput:
    skill_id: str
    step_name: str
    error_type: str
    error_message: str
    state: dict
    skill_yaml_preview: str
    user_id: str
    retry_count: int


@dataclass(slots=True)
class TriageResult:
    decision: TriageDecision
    rationale: str
    modified_prompt_additions: str | None = None
    alert_message: str | None = None


TRIAGE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": [d.value for d in TriageDecision],
        },
        "rationale": {"type": "string"},
        "modified_prompt_additions": {"type": ["string", "null"]},
        "alert_message": {"type": ["string", "null"]},
    },
    "required": ["decision", "rationale"],
}


class TriageAgent:
    def __init__(self, model_router: Any) -> None:
        self._router = model_router

    async def handle_failure(self, input_: TriageInput) -> TriageResult:
        """Return a structured decision for a failed skill step."""
        # Enforce retry cap up-front.
        if input_.retry_count >= MAX_RETRY_COUNT:
            return TriageResult(
                decision=TriageDecision.ESCALATE_TO_CLAUDE,
                rationale=(
                    f"retry cap ({MAX_RETRY_COUNT}) reached for skill={input_.skill_id}; "
                    f"escalating to Claude"
                ),
            )

        prompt = self._build_prompt(input_)

        try:
            output, _meta = await self._router.complete(
                prompt=prompt,
                task_type="triage_failure",
                user_id=input_.user_id,
            )
        except Exception as exc:
            logger.warning(
                "triage_llm_failed",
                skill_id=input_.skill_id,
                error=str(exc),
            )
            return TriageResult(
                decision=TriageDecision.ESCALATE_TO_CLAUDE,
                rationale=f"triage LLM failed: {exc}",
            )

        try:
            decision = TriageDecision(output["decision"])
        except (KeyError, ValueError):
            return TriageResult(
                decision=TriageDecision.ESCALATE_TO_CLAUDE,
                rationale="triage LLM returned invalid decision; escalating",
            )

        # Override retry if LLM asks for it but we're at the cap.
        if decision == TriageDecision.RETRY_STEP and input_.retry_count >= MAX_RETRY_COUNT - 1:
            logger.info(
                "triage_retry_overridden",
                skill_id=input_.skill_id,
                retry_count=input_.retry_count,
            )
            return TriageResult(
                decision=TriageDecision.ESCALATE_TO_CLAUDE,
                rationale=(
                    "LLM requested retry but retry cap imminent; escalating instead. "
                    f"Original rationale: {output.get('rationale', '')}"
                ),
            )

        return TriageResult(
            decision=decision,
            rationale=output.get("rationale", ""),
            modified_prompt_additions=output.get("modified_prompt_additions"),
            alert_message=output.get("alert_message"),
        )

    @staticmethod
    def _build_prompt(input_: TriageInput) -> str:
        return (
            "You are Donna's skill-failure triage agent. A skill step failed at "
            "runtime. Decide what should happen next.\n\n"
            f"Skill ID: {input_.skill_id}\n"
            f"Step: {input_.step_name}\n"
            f"Error type: {input_.error_type}\n"
            f"Error message: {input_.error_message}\n"
            f"Retries already consumed: {input_.retry_count}\n\n"
            f"Current state object:\n{input_.state}\n\n"
            f"Skill YAML (first part):\n{input_.skill_yaml_preview[:1000]}\n\n"
            "Available decisions:\n"
            "- retry_step_with_modified_prompt: the prompt could be improved and retrying might work\n"
            "- skip_step: the step was non-essential; continue with empty state for it\n"
            "- escalate_to_claude: substantive failure; hand the whole task to Claude\n"
            "- alert_user: needs user intervention; don't proceed\n"
            "- mark_skill_degraded: pattern suggests the skill is broken and needs evolution\n\n"
            "Return a JSON object with your decision and rationale."
        )
