"""In-memory sink for SkillExecutor's run_repository protocol.

When passed as ``run_sink`` on :class:`SkillExecutor` (Task 7), the
executor delegates all persistence calls here. The sink captures them in
memory, returns synthetic IDs, and writes nothing to disk. Used by
:class:`ValidationExecutor` so fixture runs produce no production rows.

Must implement the same method signatures as
:class:`donna.skills.run_persistence.SkillRunRepository`. If that
contract changes, this class must change too.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any


@dataclass
class _StepRecord:
    skill_run_id: str
    step_name: str
    step_index: int
    step_kind: str
    output: dict | None
    latency_ms: int
    validation_status: str
    invocation_log_id: str | None
    tool_calls: list | None
    prompt_tokens: int | None
    error: str | None


class ValidationRunSink:
    """Absorbs SkillRunRepository-shaped calls in-memory; no DB writes."""

    def __init__(self) -> None:
        self.run_id: str | None = None
        self.skill_id: str | None = None
        self.skill_version_id: str | None = None
        self.inputs: dict | None = None
        self.user_id: str | None = None
        self.task_id: str | None = None
        self.automation_run_id: str | None = None
        self.step_records: list[_StepRecord] = []
        self.final_status: str | None = None
        self.final_output: Any = None
        self.state_object: dict | None = None
        self.tool_result_cache: dict | None = None
        self.total_latency_ms: int = 0
        self.total_cost_usd: float = 0.0
        self.escalation_reason: str | None = None
        self.error: str | None = None

    async def start_run(
        self,
        skill_id: str,
        skill_version_id: str,
        inputs: dict,
        user_id: str,
        task_id: str | None,
        automation_run_id: str | None,
    ) -> str:
        self.run_id = f"validation-run-{uuid.uuid4().hex[:12]}"
        self.skill_id = skill_id
        self.skill_version_id = skill_version_id
        self.inputs = inputs
        self.user_id = user_id
        self.task_id = task_id
        self.automation_run_id = automation_run_id
        return self.run_id

    async def record_step(
        self,
        skill_run_id: str,
        step_name: str,
        step_index: int,
        step_kind: str,
        output: dict | None,
        latency_ms: int,
        validation_status: str,
        invocation_log_id: str | None = None,
        tool_calls: list | None = None,
        prompt_tokens: int | None = None,
        error: str | None = None,
    ) -> str:
        step_id = f"validation-step-{uuid.uuid4().hex[:12]}"
        self.step_records.append(_StepRecord(
            skill_run_id=skill_run_id,
            step_name=step_name,
            step_index=step_index,
            step_kind=step_kind,
            output=output,
            latency_ms=latency_ms,
            validation_status=validation_status,
            invocation_log_id=invocation_log_id,
            tool_calls=tool_calls,
            prompt_tokens=prompt_tokens,
            error=error,
        ))
        return step_id

    async def finish_run(
        self,
        skill_run_id: str,
        status: str,
        final_output: Any,
        state_object: dict,
        tool_result_cache: dict,
        total_latency_ms: int,
        total_cost_usd: float,
        escalation_reason: str | None,
        error: str | None,
    ) -> None:
        self.final_status = status
        self.final_output = final_output
        self.state_object = state_object
        self.tool_result_cache = tool_result_cache
        self.total_latency_ms = total_latency_ms
        self.total_cost_usd = total_cost_usd
        self.escalation_reason = escalation_reason
        self.error = error
