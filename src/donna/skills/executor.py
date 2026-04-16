"""SkillExecutor — minimal single-step implementation for Phase 1.

Phase 1 supports only llm-kind steps with no tool dispatch and no DSL.
Phase 2 expands to multi-step skills with tool invocations and flow control.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import jinja2
import structlog
import yaml

from donna.skills.models import SkillRow, SkillVersionRow
from donna.skills.state import StateObject
from donna.skills.validation import SchemaValidationError, validate_output

logger = structlog.get_logger()


@dataclass(slots=True)
class SkillRunResult:
    status: str  # succeeded | failed | escalated
    final_output: Any = None
    state: dict[str, Any] = field(default_factory=dict)
    escalation_reason: str | None = None
    error: str | None = None
    invocation_ids: list[str] = field(default_factory=list)
    total_latency_ms: int = 0
    total_cost_usd: float = 0.0


class SkillExecutor:
    """Executes a skill version against inputs.

    Phase 1: single-step llm skills only. Multi-step skills log a warning
    and only the first step runs.
    """

    def __init__(self, model_router: Any) -> None:
        self._router = model_router
        self._jinja_env = jinja2.Environment(
            autoescape=False,
            undefined=jinja2.StrictUndefined,
        )

    async def execute(
        self,
        skill: SkillRow,
        version: SkillVersionRow,
        inputs: dict,
        user_id: str,
    ) -> SkillRunResult:
        state = StateObject()
        start_time = time.monotonic()

        try:
            backbone = yaml.safe_load(version.yaml_backbone) if version.yaml_backbone else {}
        except yaml.YAMLError as exc:
            return SkillRunResult(status="failed", error=f"yaml_parse: {exc}")

        steps = backbone.get("steps", [])
        if not steps:
            return SkillRunResult(status="succeeded", final_output={}, state={})

        if len(steps) > 1:
            logger.warning(
                "skill_executor_phase_1_multistep_skipped",
                skill_id=skill.id,
                step_count=len(steps),
            )

        step = steps[0]
        step_name = step["name"]
        step_kind = step.get("kind", "llm")

        if step_kind != "llm":
            return SkillRunResult(
                status="failed",
                error=f"Phase 1 only supports llm steps; got kind={step_kind}",
            )

        prompt_template = version.step_content.get(step_name, "")
        schema = version.output_schemas.get(step_name, {})

        try:
            template = self._jinja_env.from_string(prompt_template)
            rendered = template.render(inputs=inputs, state=state.to_dict())
        except jinja2.UndefinedError as exc:
            return SkillRunResult(
                status="failed",
                error=f"prompt_render: undefined variable: {exc}",
            )

        try:
            output, meta = await self._router.complete(
                prompt=rendered,
                schema=schema,
                model_alias="local_parser",
                task_type=f"skill_step::{skill.capability_name}::{step_name}",
                user_id=user_id,
            )
        except Exception as exc:
            logger.exception(
                "skill_executor_model_call_failed",
                skill_id=skill.id,
                step_name=step_name,
            )
            return SkillRunResult(
                status="failed",
                error=f"model_call: {exc}",
            )

        total_latency_ms = int((time.monotonic() - start_time) * 1000)

        if isinstance(output, dict) and "escalate" in output:
            esc = output["escalate"]
            reason = esc.get("reason", "unspecified") if isinstance(esc, dict) else str(esc)
            logger.info(
                "skill_step_escalated",
                skill_id=skill.id,
                step_name=step_name,
                reason=reason,
            )
            return SkillRunResult(
                status="escalated",
                state=state.to_dict(),
                escalation_reason=reason,
                invocation_ids=[meta.invocation_id],
                total_latency_ms=total_latency_ms,
                total_cost_usd=meta.cost_usd,
            )

        try:
            validate_output(output, schema)
        except SchemaValidationError as exc:
            logger.warning(
                "skill_step_schema_invalid",
                skill_id=skill.id,
                step_name=step_name,
                error=str(exc),
            )
            return SkillRunResult(
                status="failed",
                state=state.to_dict(),
                error=f"schema_validation: {exc}",
                invocation_ids=[meta.invocation_id],
                total_latency_ms=total_latency_ms,
                total_cost_usd=meta.cost_usd,
            )

        state[step_name] = output

        logger.info(
            "skill_step_completed",
            skill_id=skill.id,
            step_name=step_name,
            latency_ms=total_latency_ms,
        )

        return SkillRunResult(
            status="succeeded",
            final_output=output,
            state=state.to_dict(),
            invocation_ids=[meta.invocation_id],
            total_latency_ms=total_latency_ms,
            total_cost_usd=meta.cost_usd,
        )
