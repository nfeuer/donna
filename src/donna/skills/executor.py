"""SkillExecutor — multi-step skill execution with tool dispatch,
DSL, triage, and escalate-signal handling.

See spec §6.4 and Phase 2 plan Task 9.

This replaces the Phase 1 single-step executor. Single-step llm skills
behave identically to Phase 1 when no triage is configured so that the
Phase 1 test suite keeps passing.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import jinja2
import structlog
import yaml

from donna.skills.dsl import DSLError, expand_for_each
from donna.skills.models import SkillRow, SkillVersionRow
from donna.skills.state import StateObject
from donna.skills.tool_dispatch import (
    ToolDispatcher,
    ToolInvocationError,
    ToolInvocationSpec,
)
from donna.skills.tool_registry import ToolRegistry
from donna.skills.triage import (
    TriageAgent,
    TriageDecision,
    TriageInput,
    TriageResult,
)
from donna.skills.validation import SchemaValidationError, validate_output

if TYPE_CHECKING:
    from donna.skills.shadow import ShadowSampler

logger = structlog.get_logger()


@dataclass(slots=True)
class StepResultRecord:
    """In-memory per-step record for the SkillRunResult."""

    step_name: str
    step_index: int
    step_kind: str
    output: dict | None = None
    tool_calls: list | None = None
    latency_ms: int = 0
    validation_status: str = "valid"
    error: str | None = None
    invocation_id: str | None = None


@dataclass(slots=True)
class SkillRunResult:
    """Result of a skill execution.

    Extends the Phase 1 shape with ``step_results`` and ``tool_result_cache``.
    Phase 1 callers continue to read the original fields unchanged.
    """

    status: str  # succeeded | failed | escalated
    final_output: Any = None
    state: dict[str, Any] = field(default_factory=dict)
    escalation_reason: str | None = None
    error: str | None = None
    invocation_ids: list[str] = field(default_factory=list)
    total_latency_ms: int = 0
    total_cost_usd: float = 0.0
    step_results: list[StepResultRecord] = field(default_factory=list)
    tool_result_cache: dict = field(default_factory=dict)


_WHOLE_EXPR_RE = re.compile(r"^\s*\{\{\s*(.+?)\s*\}\}\s*$")


class SkillExecutor:
    """Execute a skill version's multi-step YAML backbone."""

    def __init__(
        self,
        model_router: Any,
        tool_registry: ToolRegistry | None = None,
        triage: TriageAgent | None = None,
        run_repository: Any | None = None,
        run_sink: Any | None = None,
        shadow_sampler: "ShadowSampler | None" = None,
    ) -> None:
        self._router = model_router
        self._tool_registry = tool_registry or ToolRegistry()
        self._tool_dispatcher = ToolDispatcher(self._tool_registry)
        self._triage = triage
        # run_sink overrides run_repository when both are provided.
        self._run_repository = run_sink if run_sink is not None else run_repository
        self._shadow_sampler = shadow_sampler
        self._jinja = jinja2.Environment(
            autoescape=False,
            undefined=jinja2.StrictUndefined,
        )

    async def execute(
        self,
        skill: SkillRow,
        version: SkillVersionRow,
        inputs: dict,
        user_id: str,
        **_ignored_kwargs: Any,
    ) -> SkillRunResult:
        state = StateObject()
        start = time.monotonic()
        retry_count = 0

        try:
            backbone = yaml.safe_load(version.yaml_backbone) if version.yaml_backbone else {}
        except yaml.YAMLError as exc:
            return SkillRunResult(status="failed", error=f"yaml_parse: {exc}")

        steps = backbone.get("steps") or []
        if not steps:
            return SkillRunResult(status="succeeded", final_output={}, state={})

        skill_run_id: str | None = None
        if self._run_repository is not None:
            skill_run_id = await self._run_repository.start_run(
                skill_id=skill.id, skill_version_id=version.id,
                inputs=inputs, user_id=user_id,
                task_id=None, automation_run_id=None,
            )

        step_results: list[StepResultRecord] = []
        invocation_ids: list[str] = []
        total_cost = 0.0

        idx = 0
        prompt_additions: str | None = None

        while idx < len(steps):
            step = steps[idx]
            step_name = step.get("name") or f"step_{idx}"
            step_kind = step.get("kind", "llm")
            allowed_tools = step.get("tools", [])

            step_start = time.monotonic()
            record = StepResultRecord(
                step_name=step_name, step_index=idx, step_kind=step_kind,
            )

            try:
                if step_kind == "tool":
                    collected = await self._run_tool_invocations(
                        step.get("tool_invocations", []),
                        state=state, inputs=inputs,
                        allowed_tools=allowed_tools,
                    )
                    state[step_name] = collected
                    record.output = collected
                    record.tool_calls = list(collected.keys())

                elif step_kind == "mixed":
                    collected = await self._run_tool_invocations(
                        step.get("tool_invocations", []),
                        state=state, inputs=inputs,
                        allowed_tools=allowed_tools,
                    )
                    state[step_name + "_tool_results"] = collected
                    record.tool_calls = list(collected.keys())

                    llm_output, inv_id, cost = await self._run_llm_step(
                        step=step, step_name=step_name, version=version,
                        state=state, inputs=inputs,
                        user_id=user_id, skill=skill,
                        prompt_additions=prompt_additions,
                    )
                    total_cost += cost
                    invocation_ids.append(inv_id)
                    record.invocation_id = inv_id

                    if self._has_escalate(llm_output):
                        reason = self._extract_escalate_reason(llm_output)
                        record.validation_status = "escalate_signal"
                        record.output = llm_output
                        record.latency_ms = int((time.monotonic() - step_start) * 1000)
                        step_results.append(record)
                        await self._persist_step_if_repo(skill_run_id, record)
                        result = SkillRunResult(
                            status="escalated", state=state.to_dict(),
                            escalation_reason=reason,
                            invocation_ids=invocation_ids,
                            total_latency_ms=int((time.monotonic() - start) * 1000),
                            total_cost_usd=total_cost,
                            step_results=step_results,
                        )
                        await self._finish_run_if_repo(skill_run_id, result)
                        return result

                    schema = version.output_schemas.get(step_name, {})
                    validate_output(llm_output, schema)
                    state[step_name] = llm_output
                    record.output = llm_output

                else:  # kind == "llm"
                    llm_output, inv_id, cost = await self._run_llm_step(
                        step=step, step_name=step_name, version=version,
                        state=state, inputs=inputs,
                        user_id=user_id, skill=skill,
                        prompt_additions=prompt_additions,
                    )
                    total_cost += cost
                    invocation_ids.append(inv_id)
                    record.invocation_id = inv_id

                    if self._has_escalate(llm_output):
                        reason = self._extract_escalate_reason(llm_output)
                        record.validation_status = "escalate_signal"
                        record.output = llm_output
                        record.latency_ms = int((time.monotonic() - step_start) * 1000)
                        step_results.append(record)
                        await self._persist_step_if_repo(skill_run_id, record)
                        result = SkillRunResult(
                            status="escalated", state=state.to_dict(),
                            escalation_reason=reason,
                            invocation_ids=invocation_ids,
                            total_latency_ms=int((time.monotonic() - start) * 1000),
                            total_cost_usd=total_cost,
                            step_results=step_results,
                        )
                        await self._finish_run_if_repo(skill_run_id, result)
                        return result

                    schema = version.output_schemas.get(step_name, {})
                    validate_output(llm_output, schema)
                    state[step_name] = llm_output
                    record.output = llm_output

                record.latency_ms = int((time.monotonic() - step_start) * 1000)
                step_results.append(record)
                await self._persist_step_if_repo(skill_run_id, record)

                logger.info(
                    "skill_step_completed",
                    skill_id=skill.id,
                    capability_name=skill.capability_name,
                    step_name=step_name,
                    step_kind=step_kind,
                    latency_ms=record.latency_ms,
                )

                # Step succeeded — advance to the next step.
                idx += 1
                prompt_additions = None

            except (SchemaValidationError, ToolInvocationError, DSLError, jinja2.UndefinedError) as exc:
                record.error = str(exc)
                record.validation_status = (
                    "schema_invalid" if isinstance(exc, SchemaValidationError) else "tool_failed"
                )
                record.latency_ms = int((time.monotonic() - step_start) * 1000)
                step_results.append(record)
                await self._persist_step_if_repo(skill_run_id, record)

                # No triage configured → escalate typed failures inline.
                if self._triage is None:
                    error_type = {
                        SchemaValidationError: "schema_validation",
                        ToolInvocationError: "tool_exhausted",
                        DSLError: "dsl_error",
                        jinja2.UndefinedError: "template_error",
                    }.get(type(exc), "unknown")
                    result = SkillRunResult(
                        status="escalated",
                        state=state.to_dict(),
                        escalation_reason=f"{error_type}: {exc}",
                        invocation_ids=invocation_ids,
                        total_latency_ms=int((time.monotonic() - start) * 1000),
                        total_cost_usd=total_cost,
                        step_results=step_results,
                    )
                    await self._finish_run_if_repo(skill_run_id, result)
                    return result

                triage_result = await self._consult_triage(
                    skill=skill, step_name=step_name, exc=exc,
                    state=state, version=version, user_id=user_id,
                    retry_count=retry_count,
                )

                if triage_result.decision == TriageDecision.RETRY_STEP:
                    retry_count += 1
                    prompt_additions = triage_result.modified_prompt_additions
                    logger.info(
                        "skill_step_triage_retry",
                        skill_id=skill.id, step=step_name,
                        retry_count=retry_count,
                        modifications=bool(prompt_additions),
                    )
                    continue  # Re-loop at same idx, with prompt_additions set.

                if triage_result.decision == TriageDecision.SKIP_STEP:
                    state[step_name] = {}
                    idx += 1
                    prompt_additions = None
                    continue

                # ESCALATE_TO_CLAUDE, ALERT_USER, MARK_SKILL_DEGRADED all terminate.
                status = (
                    "escalated"
                    if triage_result.decision == TriageDecision.ESCALATE_TO_CLAUDE
                    else "failed"
                )
                escalation_reason = (
                    triage_result.rationale
                    if triage_result.decision == TriageDecision.ESCALATE_TO_CLAUDE
                    else None
                )
                result = SkillRunResult(
                    status=status, state=state.to_dict(),
                    escalation_reason=escalation_reason,
                    error=str(exc),
                    invocation_ids=invocation_ids,
                    total_latency_ms=int((time.monotonic() - start) * 1000),
                    total_cost_usd=total_cost,
                    step_results=step_results,
                )
                await self._finish_run_if_repo(skill_run_id, result)
                return result

            except Exception as exc:
                record.error = str(exc)
                record.validation_status = "tool_failed"
                record.latency_ms = int((time.monotonic() - step_start) * 1000)
                step_results.append(record)
                await self._persist_step_if_repo(skill_run_id, record)
                logger.exception(
                    "skill_executor_unexpected_failure",
                    skill_id=skill.id, step=step_name,
                )
                result = SkillRunResult(
                    status="failed", state=state.to_dict(),
                    error=f"unexpected: {exc}",
                    invocation_ids=invocation_ids,
                    total_latency_ms=int((time.monotonic() - start) * 1000),
                    total_cost_usd=total_cost,
                    step_results=step_results,
                )
                await self._finish_run_if_repo(skill_run_id, result)
                return result

        # All steps succeeded. Render final_output.
        final_output_expr = backbone.get("final_output")
        final_output = self._render_final_output(
            final_output_expr, state=state, default=state.to_dict(),
        )

        result = SkillRunResult(
            status="succeeded",
            final_output=final_output,
            state=state.to_dict(),
            invocation_ids=invocation_ids,
            total_latency_ms=int((time.monotonic() - start) * 1000),
            total_cost_usd=total_cost,
            step_results=step_results,
        )
        await self._finish_run_if_repo(skill_run_id, result)

        # Fire-and-forget shadow sampling — only when a run row exists to link
        # the divergence to and the sampler is configured.
        if self._shadow_sampler is not None and skill_run_id is not None:
            claude_prompt = json.dumps(
                {"capability": skill.capability_name, "inputs": inputs},
                sort_keys=True,
            )
            skill_output_dict = (
                result.final_output
                if isinstance(result.final_output, dict)
                else {"output": result.final_output}
            )
            asyncio.create_task(
                self._shadow_sampler.sample_if_applicable(
                    skill=skill,
                    skill_run_id=skill_run_id,
                    inputs=inputs,
                    skill_output=skill_output_dict,
                    claude_task_type=skill.capability_name,
                    claude_prompt=claude_prompt,
                )
            )

        return result

    async def _persist_step_if_repo(
        self, skill_run_id: str | None, record: StepResultRecord,
    ) -> None:
        if skill_run_id is None or self._run_repository is None:
            return
        try:
            await self._run_repository.record_step(
                skill_run_id=skill_run_id,
                step_name=record.step_name,
                step_index=record.step_index,
                step_kind=record.step_kind,
                output=record.output,
                latency_ms=record.latency_ms,
                validation_status=record.validation_status,
                invocation_log_id=record.invocation_id,
                tool_calls=record.tool_calls,
                error=record.error,
            )
        except Exception:
            logger.exception("skill_run_persistence_step_failed", skill_run_id=skill_run_id)

    async def _finish_run_if_repo(
        self, skill_run_id: str | None, result: SkillRunResult,
    ) -> None:
        if skill_run_id is None or self._run_repository is None:
            return
        try:
            await self._run_repository.finish_run(
                skill_run_id=skill_run_id,
                status=result.status,
                final_output=result.final_output,
                state_object=result.state,
                tool_result_cache=result.tool_result_cache,
                total_latency_ms=result.total_latency_ms,
                total_cost_usd=result.total_cost_usd,
                escalation_reason=result.escalation_reason,
                error=result.error,
            )
        except Exception:
            logger.exception("skill_run_persistence_finish_failed", skill_run_id=skill_run_id)

    async def _run_llm_step(
        self, step: dict, step_name: str, version: SkillVersionRow,
        state: StateObject, inputs: dict, user_id: str, skill: SkillRow,
        prompt_additions: str | None = None,
    ) -> tuple[Any, str, float]:
        prompt_template = version.step_content.get(step_name, "")
        rendered = self._jinja.from_string(prompt_template).render(
            inputs=inputs, state=state.to_dict(),
        )
        if prompt_additions:
            rendered = rendered + "\n\n" + prompt_additions
        schema = version.output_schemas.get(step_name, {})

        output, meta = await self._router.complete(
            prompt=rendered,
            schema=schema,
            model_alias="local_parser",
            task_type=f"skill_step::{skill.capability_name}::{step_name}",
            user_id=user_id,
        )

        return output, meta.invocation_id, getattr(meta, "cost_usd", 0.0)

    async def _run_tool_invocations(
        self, invocations: list[dict], state: StateObject,
        inputs: dict, allowed_tools: list[str],
    ) -> dict:
        """Resolve DSL (for_each) and run all tool invocations for a step."""
        collected: dict = {}
        state_dict = state.to_dict()

        for raw_spec in invocations:
            if "for_each" in raw_spec:
                specs = expand_for_each(raw_spec, state=state_dict, inputs=inputs)
            else:
                specs = [ToolInvocationSpec(
                    tool=raw_spec["tool"],
                    args=raw_spec.get("args", {}),
                    store_as=raw_spec.get("store_as", "result"),
                    retry=raw_spec.get("retry", {}),
                )]

            for spec in specs:
                result = await self._tool_dispatcher.run_invocation(
                    spec=spec, state=state_dict, inputs=inputs,
                    allowed_tools=allowed_tools,
                )
                collected.update(result)

        return collected

    async def _consult_triage(
        self, skill: SkillRow, step_name: str, exc: Exception,
        state: StateObject, version: SkillVersionRow, user_id: str,
        retry_count: int,
    ) -> TriageResult:
        assert self._triage is not None  # caller has already checked
        error_type = {
            SchemaValidationError: "schema_validation",
            ToolInvocationError: "tool_exhausted",
            DSLError: "dsl_error",
            jinja2.UndefinedError: "template_error",
        }.get(type(exc), "unknown")

        return await self._triage.handle_failure(TriageInput(
            skill_id=skill.id,
            step_name=step_name,
            error_type=error_type,
            error_message=str(exc),
            state=state.to_dict(),
            skill_yaml_preview=version.yaml_backbone,
            user_id=user_id,
            retry_count=retry_count,
        ))

    def _render_final_output(
        self, expr: str | None, state: StateObject, default: Any,
    ) -> Any:
        """Render a final_output template. Preserves Python types if the
        whole expression is ``{{ ... }}``."""
        if not expr:
            return default

        whole_expr = _WHOLE_EXPR_RE.match(expr)
        if whole_expr:
            try:
                compiled = self._jinja.compile_expression(whole_expr.group(1))
                return compiled(state=state.to_dict(), inputs={})
            except Exception as e:
                logger.warning("final_output_eval_failed", error=str(e))
                return default

        try:
            return self._jinja.from_string(expr).render(
                state=state.to_dict(), inputs={},
            )
        except Exception as e:
            logger.warning("final_output_render_failed", error=str(e))
            return default

    @staticmethod
    def _has_escalate(output: Any) -> bool:
        return isinstance(output, dict) and "escalate" in output

    @staticmethod
    def _extract_escalate_reason(output: Any) -> str:
        esc = output.get("escalate") if isinstance(output, dict) else None
        if isinstance(esc, dict):
            return esc.get("reason", "unspecified")
        return str(esc) if esc is not None else "unspecified"
