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
import os
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import jinja2
import structlog
import yaml

from donna.skills.alerting import FallbackAlert, emit_fallback_alert
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


class StepFailedError(Exception):
    """Raised when a tool_invocation's ``on_failure=fail_step`` policy fires.

    Executor treats the step as terminally failed — subsequent steps are
    skipped and the skill run completes with status=``failed``. No triage
    or Claude escalation is attempted.
    """

    def __init__(self, step_name: str, cause: Exception | None = None) -> None:
        super().__init__(f"step failed: {step_name}")
        self.step_name = step_name
        self.cause = cause


class SkillFailedError(Exception):
    """Raised when a tool_invocation's ``on_failure=fail_skill`` policy fires.

    Executor aborts the entire skill run with status=``failed``. No triage
    or Claude escalation is attempted.
    """

    def __init__(self, step_name: str, cause: Exception | None = None) -> None:
        super().__init__(f"skill failed at step: {step_name}")
        self.step_name = step_name
        self.cause = cause


@dataclass(slots=True)
class StepResultRecord:
    """In-memory per-step record for the SkillRunResult."""

    step_name: str
    step_index: int
    step_kind: str
    output: dict[str, Any] | None = None
    tool_calls: list[Any] | None = None
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
    tool_result_cache: dict[str, Any] = field(default_factory=dict)
    run_id: str | None = None  # Wave 2 F-2: populated from SkillRunRepository.start_run


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
        shadow_sampler: ShadowSampler | None = None,
        config: Any | None = None,               # Wave 2: SkillSystemConfig for validation timeouts
        task_type_prefix: str | None = None,     # Wave 2: override "skill_step" default
        tool_gap_surfacer: Any | None = None,    # Slice 22: emit high gap on missing tool
        fallback_alert: FallbackAlert | None = None,  # Fable #7: alert on persist loss
    ) -> None:
        self._router = model_router
        if tool_registry is not None:
            self._tool_registry = tool_registry
        else:
            # Wave 2: fall through to the module-level default registry that
            # the orchestrator populates at startup via register_default_tools.
            # Tests that pass an explicit registry keep their own isolation.
            from donna.skills.tools import DEFAULT_TOOL_REGISTRY
            self._tool_registry = DEFAULT_TOOL_REGISTRY
        self._tool_dispatcher = ToolDispatcher(self._tool_registry)
        self._triage = triage
        # run_sink overrides run_repository when both are provided.
        self._run_repository = run_sink if run_sink is not None else run_repository
        self._run_sink = run_sink
        self._config = config
        self._task_type_prefix = task_type_prefix
        self._shadow_sampler = shadow_sampler
        # Strong references to fire-and-forget shadow sampling tasks so
        # they are not garbage-collected before completion.
        self._shadow_sampling_tasks: set[asyncio.Task[None]] = set()
        self._jinja = jinja2.Environment(
            autoescape=False,
            undefined=jinja2.StrictUndefined,
        )
        # Slice 22 — optional defensive trip-wire. When wired, the
        # executor surfaces a high-severity ToolGap on any tool dispatch
        # against a name not in the registry before the normal
        # ToolNotFoundError path runs. Catches the case where boot-check
        # + scheduler-pre-run both missed (mid-run config edit, etc.).
        self._tool_gap_surfacer = tool_gap_surfacer
        # Fable #7: alert on run-persistence write failures. A lost skill_run /
        # skill_step_result row silently starves the promotion + degradation
        # gates of evidence, so a write failure must not be swallowed.
        self._fallback_alert = fallback_alert

    async def execute(
        self,
        skill: SkillRow,
        version: SkillVersionRow,
        inputs: dict[str, Any],
        user_id: str,
        task_id: str | None = None,
        automation_run_id: str | None = None,
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
                task_id=task_id, automation_run_id=automation_run_id,
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

            # Evaluate condition if present
            condition = step.get("condition")
            if condition:
                try:
                    cond_result = self._jinja.compile_expression(condition)(
                        state=state.to_dict(), inputs=inputs,
                    )
                except Exception:
                    logger.warning(
                        "skill_step_condition_error",
                        skill_id=skill.id,
                        step_name=step_name,
                        condition=condition,
                        exc_info=True,
                    )
                    cond_result = False

                if not cond_result:
                    logger.info(
                        "skill_step_skipped_condition",
                        skill_id=skill.id,
                        step_name=step_name,
                        condition=condition,
                    )
                    if step_name not in state:
                        state[step_name] = {"success": False, "skipped": True}
                    idx += 1
                    continue

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
                        user_id=user_id,
                        capability_name=skill.capability_name,
                    )
                    if isinstance(collected, dict):
                        collected["success"] = True
                    state[step_name] = collected
                    record.output = collected
                    record.tool_calls = list(collected.keys())

                elif step_kind == "mixed":
                    collected = await self._run_tool_invocations(
                        step.get("tool_invocations", []),
                        state=state, inputs=inputs,
                        allowed_tools=allowed_tools,
                        user_id=user_id,
                        capability_name=skill.capability_name,
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
                    if inv_id is not None:
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

                    schema = self._resolve_step_schema(step, step_name, version.output_schemas)
                    validate_output(llm_output, schema)
                    if isinstance(llm_output, dict):
                        llm_output["success"] = True
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
                    if inv_id is not None:
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

                    schema = self._resolve_step_schema(step, step_name, version.output_schemas)
                    validate_output(llm_output, schema)
                    if isinstance(llm_output, dict):
                        llm_output["success"] = True
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

            except StepFailedError as exc:
                # on_failure=fail_step — terminally fail the step, skip the
                # rest of the skill, no triage/escalation. We don't write to
                # `state[step_name]` here (no subsequent step reads it) —
                # symmetry with the fail_skill branch below.
                cause_msg = str(exc.cause) if exc.cause else "step failed"
                record.error = cause_msg
                record.validation_status = "step_failed"
                record.output = {"tool_error": cause_msg}
                record.latency_ms = int((time.monotonic() - step_start) * 1000)
                step_results.append(record)
                await self._persist_step_if_repo(skill_run_id, record)
                logger.info(
                    "skill_step_failed_terminal",
                    skill_id=skill.id, step=step_name, error=cause_msg,
                )
                result = SkillRunResult(
                    status="failed", state=state.to_dict(),
                    error=f"step_failed: {cause_msg}",
                    invocation_ids=invocation_ids,
                    total_latency_ms=int((time.monotonic() - start) * 1000),
                    total_cost_usd=total_cost,
                    step_results=step_results,
                )
                await self._finish_run_if_repo(skill_run_id, result)
                return result

            except SkillFailedError as exc:
                # on_failure=fail_skill — abort the whole skill run, no
                # triage/escalation.
                cause_msg = str(exc.cause) if exc.cause else "skill failed"
                record.error = cause_msg
                record.validation_status = "skill_failed"
                record.output = {"tool_error": cause_msg}
                record.latency_ms = int((time.monotonic() - step_start) * 1000)
                step_results.append(record)
                await self._persist_step_if_repo(skill_run_id, record)
                logger.info(
                    "skill_run_failed_abort",
                    skill_id=skill.id, step=step_name, error=cause_msg,
                )
                result = SkillRunResult(
                    status="failed", state=state.to_dict(),
                    final_output={"tool_error": cause_msg},
                    error=f"skill_failed: {cause_msg}",
                    invocation_ids=invocation_ids,
                    total_latency_ms=int((time.monotonic() - start) * 1000),
                    total_cost_usd=total_cost,
                    step_results=step_results,
                )
                await self._finish_run_if_repo(skill_run_id, result)
                return result

            except (
                SchemaValidationError, ToolInvocationError, DSLError, jinja2.UndefinedError,
            ) as exc:
                record.error = str(exc)
                record.validation_status = (
                    "schema_invalid" if isinstance(exc, SchemaValidationError) else "tool_failed"
                )
                record.latency_ms = int((time.monotonic() - step_start) * 1000)
                step_results.append(record)
                await self._persist_step_if_repo(skill_run_id, record)

                # on_failure=continue — absorb the error and advance.
                step_on_failure = step.get("on_failure")
                if step_on_failure == "continue":
                    state[step_name] = {"success": False, "error": str(exc)}
                    record.validation_status = "continued"
                    logger.info(
                        "skill_step_continued",
                        skill_id=skill.id, step=step_name, error=str(exc),
                    )
                    idx += 1
                    prompt_additions = None
                    continue

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

                # on_failure=continue — absorb the error and advance.
                step_on_failure = step.get("on_failure")
                if step_on_failure == "continue":
                    state[step_name] = {"success": False, "error": str(exc)}
                    record.validation_status = "continued"
                    logger.info(
                        "skill_step_continued",
                        skill_id=skill.id, step=step_name, error=str(exc),
                    )
                    idx += 1
                    prompt_additions = None
                    continue

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
            shadow_task = asyncio.create_task(
                self._shadow_sampler.sample_if_applicable(
                    skill=skill,
                    skill_run_id=skill_run_id,
                    inputs=inputs,
                    skill_output=skill_output_dict,
                    claude_task_type=skill.capability_name,
                    claude_prompt=claude_prompt,
                )
            )
            self._shadow_sampling_tasks.add(shadow_task)
            shadow_task.add_done_callback(self._shadow_sampling_tasks.discard)

        return result

    @staticmethod
    def _resolve_step_schema(
        step: dict[str, Any], step_name: str, output_schemas: dict[str, Any],
    ) -> dict[str, Any]:
        schema_ref = step.get("output_schema", "")
        if isinstance(schema_ref, dict):
            return dict(schema_ref)
        if schema_ref:
            schema_key = os.path.splitext(os.path.basename(schema_ref))[0]
            schema: dict[str, Any] = output_schemas.get(schema_key, {})
            if schema:
                return schema
        return dict(output_schemas.get(step_name, {}))

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
        except Exception as exc:
            logger.exception("skill_run_persistence_step_failed", skill_run_id=skill_run_id)
            await emit_fallback_alert(
                self._fallback_alert,
                component="skill_executor",
                error=f"skill_step_result persistence failed: {exc}",
                fallback="step evidence lost; gate inputs incomplete",
                context={"skill_run_id": skill_run_id, "step_name": record.step_name},
            )

    async def _finish_run_if_repo(
        self, skill_run_id: str | None, result: SkillRunResult,
    ) -> None:
        # Wave 2 F-2: expose the persisted skill_run.id back on the result so
        # callers (e.g. AutomationDispatcher) can populate automation_run.skill_run_id.
        if skill_run_id is not None:
            result.run_id = skill_run_id
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
        except Exception as exc:
            logger.exception("skill_run_persistence_finish_failed", skill_run_id=skill_run_id)
            await emit_fallback_alert(
                self._fallback_alert,
                component="skill_executor",
                error=f"skill_run finish persistence failed: {exc}",
                fallback="run evidence lost; promotion/degradation gates starved",
                context={"skill_run_id": skill_run_id, "status": result.status},
            )

    async def _run_llm_step(
        self, step: dict[str, Any], step_name: str, version: SkillVersionRow,
        state: StateObject, inputs: dict[str, Any], user_id: str, skill: SkillRow,
        prompt_additions: str | None = None,
    ) -> tuple[Any, str | None, float]:
        prompt_ref = step.get("prompt", "")
        if prompt_ref:
            content_key = os.path.splitext(os.path.basename(prompt_ref))[0]
            prompt_template = (
                version.step_content.get(content_key) or version.step_content.get(step_name, "")
            )
        else:
            prompt_template = version.step_content.get(step_name, "")
        rendered = self._jinja.from_string(prompt_template).render(
            inputs=inputs, state=state.to_dict(),
        )
        if prompt_additions:
            rendered = rendered + "\n\n" + prompt_additions

        step_model = step.get("model") or step.get("gpu_model")
        if step_model:
            prefix = f"skill_step__{step_model}"
        else:
            prefix = self._task_type_prefix or "skill_step"
        task_type = f"{prefix}::{skill.capability_name}::{step_name}"

        step_tools = step.get("tools", [])
        tool_definitions = None
        if step_tools and step.get("kind") == "llm":
            from donna.skills.tool_schemas import resolve_tool_definitions
            tool_definitions = resolve_tool_definitions(step_tools)

        output, meta, total_cost = await self._complete_with_tool_loop(
            rendered, task_type, user_id, step_tools, tool_definitions,
        )

        inv_id = getattr(meta, "invocation_id", None)
        return output, inv_id, total_cost

    async def _complete_with_tool_loop(
        self,
        prompt: str,
        task_type: str,
        user_id: str,
        tool_names: list[str],
        tool_definitions: list[dict[str, Any]] | None,
        max_rounds: int = 5,
    ) -> tuple[Any, Any, float]:
        """Call the router, handling tool_use loops when tools are provided."""
        total_cost = 0.0
        last_meta: Any = None

        if not tool_definitions:
            if self._run_sink is not None and self._config is not None:
                timeout_s = getattr(self._config, "validation_per_step_timeout_s", 60)
                output, meta = await asyncio.wait_for(
                    self._router.complete(prompt=prompt, task_type=task_type, user_id=user_id),
                    timeout=timeout_s,
                )
            else:
                output, meta = await self._router.complete(
                    prompt=prompt, task_type=task_type, user_id=user_id,
                )
            return output, meta, getattr(meta, "cost_usd", 0.0)

        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]

        for _ in range(max_rounds):
            output, meta = await self._router.complete(
                prompt=prompt,
                task_type=task_type,
                user_id=user_id,
                tools=tool_definitions,
                messages=messages,
            )
            total_cost += getattr(meta, "cost_usd", 0.0)
            last_meta = meta

            if not isinstance(output, dict) or "_tool_use" not in output:
                return output, last_meta, total_cost

            tool_calls = output["_tool_use"]
            raw_content = output.get("_content", [])
            messages.append({"role": "assistant", "content": raw_content})

            for call in tool_calls:
                try:
                    result = await self._tool_registry.dispatch(
                        call["name"], call["input"], tool_names,
                    )
                    messages.append({
                        "role": "user",
                        "content": [{
                            "type": "tool_result",
                            "tool_use_id": call["id"],
                            "content": json.dumps(result),
                        }],
                    })
                except Exception as exc:
                    messages.append({
                        "role": "user",
                        "content": [{
                            "type": "tool_result",
                            "tool_use_id": call["id"],
                            "content": f"Error: {exc}",
                            "is_error": True,
                        }],
                    })

        raise RuntimeError(f"tool_use loop exceeded {max_rounds} rounds")

    async def _run_tool_invocations(
        self, invocations: list[dict[str, Any]], state: StateObject,
        inputs: dict[str, Any], allowed_tools: list[str],
        user_id: str | None = None,
        capability_name: str | None = None,
    ) -> dict[str, Any]:
        """Resolve DSL (for_each) and run all tool invocations for a step.

        Slice 22 — defensive trip-wire: when ``self._tool_gap_surfacer``
        is wired, dispatching to a name not in the registry surfaces a
        high-severity ToolGap before the normal ToolNotFoundError path
        runs. Catches mid-run config edits / dynamic capability
        registration that boot-check + scheduler-pre-run both missed.
        """
        collected: dict[str, Any] = {}
        state_dict = state.to_dict()
        registered_names: set[str] | None = None
        if self._tool_gap_surfacer is not None:
            registered_names = set(self._tool_registry.list_tool_names())

        for raw_spec in invocations:
            if "for_each" in raw_spec:
                specs = expand_for_each(raw_spec, state=state_dict, inputs=inputs)
            else:
                specs = [ToolInvocationSpec(
                    tool=raw_spec["tool"],
                    args=raw_spec.get("args", {}),
                    store_as=raw_spec.get("store_as", "result"),
                    retry=raw_spec.get("retry", {}),
                    on_failure=raw_spec.get("on_failure", "escalate"),
                )]

            for spec in specs:
                if (
                    registered_names is not None
                    and spec.tool not in registered_names
                ):
                    await self._surface_runtime_tool_gap(
                        tool_name=spec.tool,
                        user_id=user_id or "system",
                        capability_name=capability_name,
                    )
                result = await self._tool_dispatcher.run_invocation(
                    spec=spec, state=state_dict, inputs=inputs,
                    allowed_tools=allowed_tools,
                )
                collected.update(result)

        return collected

    async def _surface_runtime_tool_gap(
        self,
        *,
        tool_name: str,
        user_id: str,
        capability_name: str | None,
    ) -> None:
        """Slice 22 — file a high-severity ToolGap from runtime dispatch.

        Best-effort; never raises. The downstream ToolNotFoundError /
        ToolInvocationError still propagates as-is.
        """
        if self._tool_gap_surfacer is None:
            return
        try:
            from donna.cost.tool_gap import (
                DETECTION_RUNTIME_DISPATCH,
                SEVERITY_HIGH,
                ToolGap,
            )
            await self._tool_gap_surfacer.surface(
                ToolGap(
                    tool_name=tool_name,
                    user_id=user_id,
                    severity=SEVERITY_HIGH,
                    blocking_capability_id=capability_name,
                    rationale=(
                        f"Skill execution dispatch attempted to call tool "
                        f"'{tool_name}' but it is not registered "
                        + (
                            f"(capability '{capability_name}')."
                            if capability_name
                            else "(unknown capability)."
                        )
                    ),
                    proposed_signature=None,
                    detection_point=DETECTION_RUNTIME_DISPATCH,
                )
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "runtime_tool_gap_surface_failed",
                tool_name=tool_name,
                capability_name=capability_name,
            )

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
            return str(esc.get("reason", "unspecified"))
        return str(esc) if esc is not None else "unspecified"
