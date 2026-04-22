"""EvolutionGates — four validation gates that a proposed new skill version
must pass before replacing the current version.

Spec §6.6:
  1. Structural validation.
  2. Targeted case improvement (>= 80%).
  3. Fixture regression (>= 95%).
  4. Recent-success sanity (all schema-valid).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite
import jsonschema
import structlog
import yaml

from donna.config import SkillSystemConfig
from donna.skills.mock_synthesis import cache_to_mocks
from donna.skills.models import SkillRow, SkillVersionRow

logger = structlog.get_logger()


@dataclass(slots=True)
class GateResult:
    name: str
    passed: bool
    details: dict[str, Any] = field(default_factory=dict)
    failure_reason: str | None = None


def run_structural_gate(new_version: dict[str, Any]) -> GateResult:
    """Gate 1: YAML parses, step_content/output_schemas complete, JSON schemas valid."""
    yaml_backbone = new_version.get("yaml_backbone", "")
    step_content = new_version.get("step_content", {})
    output_schemas = new_version.get("output_schemas", {})

    try:
        parsed = yaml.safe_load(yaml_backbone)
    except yaml.YAMLError as exc:
        return GateResult(
            name="structural", passed=False,
            failure_reason=f"yaml parse failed: {exc}",
        )
    if not isinstance(parsed, dict):
        return GateResult(
            name="structural", passed=False,
            failure_reason="yaml backbone did not parse to a dict",
        )

    steps = parsed.get("steps") or []
    if not isinstance(steps, list):
        return GateResult(
            name="structural", passed=False,
            failure_reason="'steps' must be a list",
        )

    for step in steps:
        name = step.get("name")
        if not name:
            return GateResult(
                name="structural", passed=False,
                failure_reason="step missing name",
            )
        kind = step.get("kind", "llm")
        if kind == "llm" and name not in step_content:
            return GateResult(
                name="structural", passed=False,
                failure_reason=f"missing step_content for step {name!r}",
            )
        if kind == "llm" and name not in output_schemas:
            return GateResult(
                name="structural", passed=False,
                failure_reason=f"missing output_schema for step {name!r}",
            )

    for step_name, schema in output_schemas.items():
        if not isinstance(schema, dict):
            return GateResult(
                name="structural", passed=False,
                failure_reason=f"schema for {step_name!r} is not a dict",
            )
        try:
            jsonschema.Draft7Validator.check_schema(schema)
        except jsonschema.exceptions.SchemaError as exc:
            return GateResult(
                name="structural", passed=False,
                failure_reason=f"invalid schema for {step_name!r}: {exc}",
            )

    return GateResult(
        name="structural", passed=True,
        details={"yaml_parsed": True, "step_count": len(steps)},
    )


class EvolutionGates:
    """Orchestrates the four validation gates against an injected executor."""

    def __init__(
        self,
        connection: aiosqlite.Connection,
        config: SkillSystemConfig,
        executor: Any,
    ) -> None:
        self._conn = connection
        self._config = config
        self._executor = executor

    def run_structural_gate(self, new_version: dict[str, Any]) -> GateResult:
        return run_structural_gate(new_version)

    async def run_targeted_case_gate(
        self,
        new_version: dict[str, Any],
        skill_id: str,
        targeted_case_ids: list[str],
    ) -> GateResult:
        if not targeted_case_ids:
            return GateResult(
                name="targeted",
                passed=True,
                details={"pass_rate": 1.0, "total": 0},
            )

        pass_count = 0
        total = len(targeted_case_ids)
        skill = _synthetic_skill(skill_id, new_version)
        version = _synthetic_version(skill_id, new_version)

        for run_id in targeted_case_ids:
            loaded = await self._load_inputs_and_mocks_for_run(run_id)
            if loaded is None:
                continue
            inputs, tool_mocks = loaded
            try:
                result = await self._executor.execute(
                    skill=skill, version=version,
                    inputs=inputs, user_id="evolution_harness",
                    tool_mocks=tool_mocks,
                )
                if result.status == "succeeded":
                    pass_count += 1
            except Exception:
                logger.warning(
                    "evolution_targeted_case_raised",
                    skill_id=skill_id, run_id=run_id,
                )

        rate = pass_count / total
        return GateResult(
            name="targeted",
            passed=rate >= self._config.evolution_targeted_case_pass_rate,
            details={"pass_rate": rate, "total": total, "passed": pass_count},
        )

    async def run_fixture_regression_gate(
        self,
        new_version: dict[str, Any],
        skill_id: str,
    ) -> GateResult:
        cursor = await self._conn.execute(
            "SELECT id, input, tool_mocks FROM skill_fixture WHERE skill_id = ?",
            (skill_id,),
        )
        rows = list(await cursor.fetchall())
        if not rows:
            return GateResult(
                name="fixture_regression",
                passed=True,
                details={"pass_rate": 1.0, "total": 0},
            )

        pass_count = 0
        skill = _synthetic_skill(skill_id, new_version)
        version = _synthetic_version(skill_id, new_version)

        for row in rows:
            fixture_id = row[0]
            fixture_input = json.loads(row[1]) if row[1] else {}
            mocks_json = row[2]
            tool_mocks = json.loads(mocks_json) if mocks_json else None
            try:
                result = await self._executor.execute(
                    skill=skill, version=version,
                    inputs=fixture_input, user_id="evolution_harness",
                    tool_mocks=tool_mocks,
                )
                if result.status == "succeeded":
                    pass_count += 1
            except Exception:
                logger.warning(
                    "evolution_fixture_raised",
                    skill_id=skill_id, fixture_id=fixture_id,
                )

        rate = pass_count / len(rows)
        return GateResult(
            name="fixture_regression",
            passed=rate >= self._config.evolution_fixture_regression_pass_rate,
            details={"pass_rate": rate, "total": len(rows), "passed": pass_count},
        )

    async def run_recent_success_gate(
        self,
        new_version: dict[str, Any],
        skill_id: str,
    ) -> GateResult:
        window_start = (
            datetime.now(UTC)
            - timedelta(days=self._config.evolution_recent_success_window_days)
        ).isoformat()
        cursor = await self._conn.execute(
            "SELECT id, state_object, tool_result_cache FROM skill_run "
            "WHERE skill_id = ? AND status = 'succeeded' "
            "AND started_at >= ? "
            "ORDER BY started_at DESC LIMIT ?",
            (skill_id, window_start, self._config.evolution_recent_success_count),
        )
        rows = list(await cursor.fetchall())
        if not rows:
            return GateResult(
                name="recent_success", passed=True,
                details={"pass_rate": 1.0, "total": 0},
            )

        pass_count = 0
        skill = _synthetic_skill(skill_id, new_version)
        version = _synthetic_version(skill_id, new_version)
        for row in rows:
            run_id = row[0]
            state = json.loads(row[1]) if row[1] else {}
            inputs = state.get("inputs", {}) if isinstance(state, dict) else {}
            cache_json = row[2]
            cache = json.loads(cache_json) if cache_json else {}
            try:
                tool_mocks = cache_to_mocks(cache) if cache else {}
            except Exception as exc:
                logger.warning(
                    "evolution_gate_mock_synthesis_failed",
                    skill_id=skill_id, run_id=run_id, error=str(exc),
                )
                tool_mocks = {}
            try:
                result = await self._executor.execute(
                    skill=skill, version=version,
                    inputs=inputs, user_id="evolution_harness",
                    tool_mocks=tool_mocks,
                )
                if result.status == "succeeded":
                    pass_count += 1
            except Exception:
                logger.warning(
                    "evolution_recent_success_raised",
                    skill_id=skill_id, run_id=run_id,
                )

        rate = pass_count / len(rows)
        return GateResult(
            name="recent_success",
            passed=rate == 1.0,
            details={"pass_rate": rate, "total": len(rows), "passed": pass_count},
        )

    async def _load_inputs_and_mocks_for_run(
        self, run_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        cursor = await self._conn.execute(
            "SELECT state_object, tool_result_cache FROM skill_run WHERE id = ?",
            (run_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        state = json.loads(row[0]) if row[0] else {}
        inputs = state.get("inputs", {}) if isinstance(state, dict) else {}
        cache_json = row[1]
        cache = json.loads(cache_json) if cache_json else {}
        try:
            tool_mocks = cache_to_mocks(cache) if cache else {}
        except Exception as exc:
            logger.warning(
                "evolution_gate_mock_synthesis_failed",
                run_id=run_id, error=str(exc),
            )
            tool_mocks = {}
        return inputs, tool_mocks


def _synthetic_skill(skill_id: str, new_version: dict[str, Any]) -> SkillRow:
    now = datetime.now(UTC)
    return SkillRow(
        id=skill_id,
        capability_name="__evolution_harness__",
        current_version_id="v_proposed",
        state="degraded",
        requires_human_gate=False,
        baseline_agreement=None,
        created_at=now, updated_at=now,
    )


def _synthetic_version(skill_id: str, new_version: dict[str, Any]) -> SkillVersionRow:
    now = datetime.now(UTC)
    return SkillVersionRow(
        id="v_proposed", skill_id=skill_id, version_number=999,
        yaml_backbone=new_version.get("yaml_backbone", ""),
        step_content=new_version.get("step_content", {}),
        output_schemas=new_version.get("output_schemas", {}),
        created_by="evolution", changelog="in-memory",
        created_at=now,
    )
