"""Evolver — single-skill evolution attempt orchestrator.

Spec §6.6. Assembles the input package, calls Claude, parses output,
runs four validation gates, persists or rejects.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import aiosqlite
import structlog
import uuid6

from donna.config import SkillSystemConfig
from donna.cost.budget import BudgetPausedError
from donna.skills.evolution_gates import (
    EvolutionGates,
    GateResult,
    run_structural_gate,
)
from donna.skills.evolution_input import EvolutionInputBuilder
from donna.skills.evolution_log import SkillEvolutionLogRepository
from donna.skills.lifecycle import (
    IllegalTransitionError,
    SkillLifecycleManager,
)
from donna.tasks.db_models import SkillState

logger = structlog.get_logger()

TASK_TYPE = "skill_evolution"


@dataclass(slots=True)
class EvolutionReport:
    skill_id: str
    outcome: str        # success | rejected_validation | malformed_output | budget_exhausted | skipped | error
    new_version_id: str | None = None
    rationale: str | None = None


class Evolver:
    def __init__(
        self,
        connection: aiosqlite.Connection,
        model_router: Any,
        budget_guard: Any,
        lifecycle_manager: SkillLifecycleManager,
        config: SkillSystemConfig,
        executor_factory: Callable[[], Any],
    ) -> None:
        self._conn = connection
        self._router = model_router
        self._budget_guard = budget_guard
        self._lifecycle = lifecycle_manager
        self._config = config
        self._executor_factory = executor_factory
        self._input_builder = EvolutionInputBuilder(connection, config)
        self._log_repo = SkillEvolutionLogRepository(connection)

    async def evolve_one(
        self, skill_id: str, triggered_by: str,
    ) -> EvolutionReport:
        skill = await self._fetch_skill(skill_id)
        if skill is None:
            return EvolutionReport(
                skill_id=skill_id, outcome="skipped",
                rationale="skill not found",
            )
        if skill["state"] != "degraded":
            return EvolutionReport(
                skill_id=skill_id, outcome="skipped",
                rationale=f"skill state is {skill['state']!r}, not 'degraded'",
            )

        # Budget check.
        try:
            if self._budget_guard is not None:
                await self._budget_guard.check_pre_call(user_id="system")
        except BudgetPausedError:
            return EvolutionReport(
                skill_id=skill_id, outcome="budget_exhausted",
            )

        # Assemble input package.
        try:
            package = await self._input_builder.build(skill_id=skill_id)
        except (LookupError, ValueError) as exc:
            logger.warning(
                "skill_evolution_input_failed",
                skill_id=skill_id, error=str(exc),
            )
            return EvolutionReport(
                skill_id=skill_id, outcome="error",
                rationale=str(exc),
            )

        # Call Claude.
        try:
            parsed, metadata = await self._router.complete(
                prompt=self._build_prompt(package),
                task_type=TASK_TYPE,
                task_id=None,
                user_id="system",
            )
        except BudgetPausedError:
            return EvolutionReport(
                skill_id=skill_id, outcome="budget_exhausted",
            )
        except Exception as exc:
            logger.warning(
                "skill_evolution_llm_failed",
                skill_id=skill_id, error=str(exc),
            )
            return EvolutionReport(
                skill_id=skill_id, outcome="error",
                rationale=f"llm call failed: {exc}",
            )

        invocation_id = getattr(metadata, "invocation_id", None)

        # Validate output shape.
        required_keys = ("diagnosis", "new_skill_version", "changelog", "targeted_failure_cases")
        if not (isinstance(parsed, dict) and all(k in parsed for k in required_keys)):
            await self._log_repo.record(
                skill_id=skill_id, from_version_id=skill["current_version_id"],
                to_version_id=None, triggered_by=triggered_by,
                claude_invocation_id=invocation_id,
                diagnosis=None, targeted_case_ids=None,
                validation_results={"malformed_output": True},
                outcome="rejected_validation",
            )
            await self._maybe_demote_after_failure(skill_id)
            return EvolutionReport(
                skill_id=skill_id, outcome="rejected_validation",
                rationale="malformed llm output",
            )

        new_version = parsed["new_skill_version"]
        targeted = parsed["targeted_failure_cases"] or []
        diagnosis = parsed.get("diagnosis")

        # Run the four gates.
        executor = self._executor_factory()
        gates = EvolutionGates(self._conn, self._config, executor)

        gate_results: dict[str, GateResult] = {}
        structural = run_structural_gate(new_version)
        gate_results["structural"] = structural
        if not structural.passed:
            return await self._record_rejection(
                skill_id=skill_id, from_version_id=skill["current_version_id"],
                triggered_by=triggered_by, invocation_id=invocation_id,
                diagnosis=diagnosis, targeted=targeted,
                gate_results=gate_results,
                rationale=f"structural gate failed: {structural.failure_reason}",
            )

        targeted_result = await gates.run_targeted_case_gate(
            new_version=new_version, skill_id=skill_id,
            targeted_case_ids=targeted,
        )
        gate_results["targeted"] = targeted_result
        if not targeted_result.passed:
            return await self._record_rejection(
                skill_id=skill_id, from_version_id=skill["current_version_id"],
                triggered_by=triggered_by, invocation_id=invocation_id,
                diagnosis=diagnosis, targeted=targeted,
                gate_results=gate_results,
                rationale="targeted case gate failed",
            )

        fixture_result = await gates.run_fixture_regression_gate(
            new_version=new_version, skill_id=skill_id,
        )
        gate_results["fixture_regression"] = fixture_result
        if not fixture_result.passed:
            return await self._record_rejection(
                skill_id=skill_id, from_version_id=skill["current_version_id"],
                triggered_by=triggered_by, invocation_id=invocation_id,
                diagnosis=diagnosis, targeted=targeted,
                gate_results=gate_results,
                rationale="fixture regression gate failed",
            )

        recent_result = await gates.run_recent_success_gate(
            new_version=new_version, skill_id=skill_id,
        )
        gate_results["recent_success"] = recent_result
        if not recent_result.passed:
            return await self._record_rejection(
                skill_id=skill_id, from_version_id=skill["current_version_id"],
                triggered_by=triggered_by, invocation_id=invocation_id,
                diagnosis=diagnosis, targeted=targeted,
                gate_results=gate_results,
                rationale="recent success gate failed",
            )

        # All gates passed: persist new version + transition.
        new_version_id = await self._persist_new_version(
            skill_id=skill_id,
            current_version_id=skill["current_version_id"],
            new_version=new_version,
            changelog=parsed.get("changelog", ""),
        )

        # Destination state: sandbox unless requires_human_gate → draft.
        to_state = (
            SkillState.DRAFT if skill["requires_human_gate"]
            else SkillState.SANDBOX
        )

        # Two-hop: degraded → draft (evolution creates a draft),
        # then (if not requires_human_gate) draft → sandbox human_approval.
        # But spec says degraded → draft with reason=gate_passed.
        await self._lifecycle.transition(
            skill_id=skill_id, to_state=SkillState.DRAFT,
            reason="gate_passed", actor="system",
            notes=f"evolution {new_version_id}",
        )
        if to_state == SkillState.SANDBOX:
            # For non-gated skills, also flip draft → sandbox.
            try:
                await self._lifecycle.transition(
                    skill_id=skill_id, to_state=SkillState.SANDBOX,
                    reason="gate_passed", actor="system",
                    notes=f"evolution {new_version_id}",
                )
            except IllegalTransitionError:
                # draft → sandbox requires human_approval in the table.
                # For automated evolution path, we accept the skill staying in draft.
                pass

        await self._log_repo.record(
            skill_id=skill_id,
            from_version_id=skill["current_version_id"],
            to_version_id=new_version_id,
            triggered_by=triggered_by,
            claude_invocation_id=invocation_id,
            diagnosis=diagnosis,
            targeted_case_ids=targeted,
            validation_results={
                name: {"passed": g.passed, **g.details}
                for name, g in gate_results.items()
            },
            outcome="success",
        )

        return EvolutionReport(
            skill_id=skill_id, outcome="success",
            new_version_id=new_version_id,
            rationale="all 4 gates passed",
        )

    async def _record_rejection(
        self,
        skill_id: str,
        from_version_id: str,
        triggered_by: str,
        invocation_id: str | None,
        diagnosis: Any,
        targeted: list[str],
        gate_results: dict[str, GateResult],
        rationale: str,
    ) -> EvolutionReport:
        await self._log_repo.record(
            skill_id=skill_id,
            from_version_id=from_version_id,
            to_version_id=None,
            triggered_by=triggered_by,
            claude_invocation_id=invocation_id,
            diagnosis=diagnosis,
            targeted_case_ids=targeted,
            validation_results={
                name: {"passed": g.passed, **g.details}
                for name, g in gate_results.items()
            },
            outcome="rejected_validation",
        )
        await self._maybe_demote_after_failure(skill_id)
        return EvolutionReport(
            skill_id=skill_id, outcome="rejected_validation",
            rationale=rationale,
        )

    async def _maybe_demote_after_failure(self, skill_id: str) -> None:
        """If the last N consecutive attempts are rejected_validation, demote."""
        n = self._config.evolution_max_consecutive_failures
        outcomes = await self._log_repo.last_n_outcomes(skill_id=skill_id, n=n)
        if len(outcomes) < n:
            return
        if all(o == "rejected_validation" for o in outcomes):
            try:
                await self._lifecycle.transition(
                    skill_id=skill_id,
                    to_state=SkillState.CLAUDE_NATIVE,
                    reason="evolution_failed",
                    actor="system",
                    notes=f"{n} consecutive rejected evolution attempts",
                )
            except IllegalTransitionError as exc:
                logger.warning(
                    "skill_evolution_demotion_failed",
                    skill_id=skill_id, error=str(exc),
                )

    async def _fetch_skill(self, skill_id: str) -> dict | None:
        cursor = await self._conn.execute(
            "SELECT id, capability_name, current_version_id, state, "
            "requires_human_gate FROM skill WHERE id = ?",
            (skill_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "id": row[0], "capability_name": row[1],
            "current_version_id": row[2], "state": row[3],
            "requires_human_gate": bool(row[4]),
        }

    async def _persist_new_version(
        self,
        skill_id: str,
        current_version_id: str,
        new_version: dict,
        changelog: str,
    ) -> str:
        new_version_id = str(uuid6.uuid7())
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self._conn.execute(
            "SELECT COALESCE(MAX(version_number), 0) "
            "FROM skill_version WHERE skill_id = ?",
            (skill_id,),
        )
        row = await cursor.fetchone()
        next_vnum = (int(row[0]) if row else 0) + 1

        await self._conn.execute(
            "INSERT INTO skill_version (id, skill_id, version_number, "
            "yaml_backbone, step_content, output_schemas, created_by, "
            "changelog, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_version_id, skill_id, next_vnum,
                new_version.get("yaml_backbone", ""),
                json.dumps(new_version.get("step_content", {})),
                json.dumps(new_version.get("output_schemas", {})),
                "claude_evolution", changelog, now,
            ),
        )
        await self._conn.execute(
            "UPDATE skill SET current_version_id = ?, updated_at = ? WHERE id = ?",
            (new_version_id, now, skill_id),
        )
        await self._conn.commit()
        return new_version_id

    def _build_prompt(self, package: dict) -> str:
        return (
            "You are evolving a Donna skill. Use the divergence cases + "
            "correction log to diagnose the problem and produce a repaired "
            "YAML + step prompts + schemas.\n\n"
            f"Capability: {json.dumps(package['capability'], indent=2)}\n\n"
            f"Current version:\n{json.dumps(package['current_version'], indent=2)}\n\n"
            f"Divergence cases ({len(package['divergence_cases'])}):\n"
            f"{json.dumps(package['divergence_cases'][:10], indent=2)}\n"
            f"(…showing first 10; total provided context: {len(package['divergence_cases'])})\n\n"
            f"Correction log ({len(package['correction_log'])} entries):\n"
            f"{json.dumps(package['correction_log'], indent=2)}\n\n"
            f"Fixture library ({len(package['fixture_library'])} cases):\n"
            f"{json.dumps(package['fixture_library'][:20], indent=2)}\n\n"
            f"Stats: {json.dumps(package['stats'], indent=2)}\n\n"
            f"Prior evolution log: {json.dumps(package['prior_evolution_log'], indent=2)}\n\n"
            "Return strict JSON matching the evolution output schema."
        )
