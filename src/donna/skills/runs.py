"""Dataclasses and row mappers for skill_run and skill_step_result."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

SKILL_RUN_COLUMNS = (
    "id", "skill_id", "skill_version_id", "task_id", "automation_run_id",
    "status", "total_latency_ms", "total_cost_usd",
    "state_object", "tool_result_cache", "final_output",
    "escalation_reason", "error", "user_id",
    "started_at", "finished_at",
)
SELECT_SKILL_RUN = ", ".join(SKILL_RUN_COLUMNS)

SKILL_STEP_RESULT_COLUMNS = (
    "id", "skill_run_id", "step_name", "step_index", "step_kind",
    "invocation_log_id", "prompt_tokens", "output", "tool_calls",
    "latency_ms", "validation_status", "error", "created_at",
)
SELECT_SKILL_STEP_RESULT = ", ".join(SKILL_STEP_RESULT_COLUMNS)


@dataclass(slots=True)
class SkillRunRow:
    id: str
    skill_id: str
    skill_version_id: str
    task_id: str | None
    automation_run_id: str | None
    status: str
    total_latency_ms: int | None
    total_cost_usd: float | None
    state_object: dict[str, Any]
    tool_result_cache: dict[str, Any] | None
    final_output: dict[str, Any] | None
    escalation_reason: str | None
    error: str | None
    user_id: str
    started_at: datetime
    finished_at: datetime | None


@dataclass(slots=True)
class SkillStepResultRow:
    id: str
    skill_run_id: str
    step_name: str
    step_index: int
    step_kind: str
    invocation_log_id: str | None
    prompt_tokens: int | None
    output: dict[str, Any] | None
    tool_calls: list[Any] | None
    latency_ms: int | None
    validation_status: str
    error: str | None
    created_at: datetime


def row_to_skill_run(row: Sequence[Any]) -> SkillRunRow:
    return SkillRunRow(
        id=row[0], skill_id=row[1], skill_version_id=row[2],
        task_id=row[3], automation_run_id=row[4],
        status=row[5], total_latency_ms=row[6], total_cost_usd=row[7],
        state_object=_parse_json(row[8]) or {},
        tool_result_cache=_parse_json(row[9]),
        final_output=_parse_json(row[10]),
        escalation_reason=row[11], error=row[12], user_id=row[13],
        started_at=_parse_dt(row[14]),
        finished_at=_parse_dt(row[15]) if row[15] is not None else None,
    )


def row_to_step_result(row: Sequence[Any]) -> SkillStepResultRow:
    return SkillStepResultRow(
        id=row[0], skill_run_id=row[1], step_name=row[2],
        step_index=row[3], step_kind=row[4],
        invocation_log_id=row[5], prompt_tokens=row[6],
        output=_parse_json(row[7]),
        tool_calls=_parse_json_list(row[8]),
        latency_ms=row[9], validation_status=row[10], error=row[11],
        created_at=_parse_dt(row[12]),
    )


def _parse_json(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    return cast(dict[str, Any], json.loads(value))


def _parse_json_list(value: Any) -> list[Any] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return value
    return cast(list[Any], json.loads(value))


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)
