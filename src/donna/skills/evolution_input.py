"""EvolutionInputBuilder — assembles the Claude input package for a degraded skill.

Spec §6.6 lists the 7 sections: capability definition, current skill version,
divergence case studies, correction log, statistical summary, prior evolution
log, fixture library.
"""

from __future__ import annotations

import json
from typing import Any

import aiosqlite
import structlog

from donna.config import SkillSystemConfig

logger = structlog.get_logger()


class EvolutionInputBuilder:
    def __init__(
        self,
        connection: aiosqlite.Connection,
        config: SkillSystemConfig,
    ) -> None:
        self._conn = connection
        self._config = config

    async def build(self, skill_id: str) -> dict[str, Any]:
        """Assemble the evolution input package.

        Raises:
            LookupError: If the skill row is missing.
            ValueError: If fewer than evolution_min_divergence_cases
                        divergence rows exist (not enough signal).
        """
        skill = await self._fetch_skill(skill_id)
        if skill is None:
            raise LookupError(f"skill not found: {skill_id!r}")

        capability = await self._fetch_capability(skill["capability_name"])
        current_version = await self._fetch_version(skill["current_version_id"])

        divergences = await self._fetch_divergences(
            skill_id=skill_id,
            limit=self._config.evolution_max_divergence_cases,
        )
        if len(divergences) < self._config.evolution_min_divergence_cases:
            raise ValueError(
                f"insufficient divergence data for skill {skill_id!r}: "
                f"{len(divergences)} < {self._config.evolution_min_divergence_cases}"
            )

        corrections = await self._fetch_correction_log(skill["capability_name"])
        prior_log = await self._fetch_prior_evolution_log(skill_id)
        fixtures = await self._fetch_fixtures(skill_id)
        stats = await self._fetch_stats(skill_id, skill["baseline_agreement"])

        return {
            "capability": capability,
            "current_version": current_version,
            "divergence_cases": divergences,
            "correction_log": corrections,
            "prior_evolution_log": prior_log,
            "fixture_library": fixtures,
            "stats": stats,
        }

    async def _fetch_skill(self, skill_id: str) -> dict | None:
        cursor = await self._conn.execute(
            "SELECT id, capability_name, current_version_id, state, "
            "requires_human_gate, baseline_agreement "
            "FROM skill WHERE id = ?",
            (skill_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "id": row[0], "capability_name": row[1],
            "current_version_id": row[2], "state": row[3],
            "requires_human_gate": bool(row[4]),
            "baseline_agreement": row[5],
        }

    async def _fetch_capability(self, name: str) -> dict:
        cursor = await self._conn.execute(
            "SELECT id, name, description, input_schema, trigger_type "
            "FROM capability WHERE name = ?",
            (name,),
        )
        row = await cursor.fetchone()
        if row is None:
            return {"name": name, "description": "", "input_schema": {}}
        return {
            "id": row[0], "name": row[1], "description": row[2] or "",
            "input_schema": json.loads(row[3]) if row[3] else {},
            "trigger_type": row[4],
        }

    async def _fetch_version(self, version_id: str | None) -> dict | None:
        if not version_id:
            return None
        cursor = await self._conn.execute(
            "SELECT id, version_number, yaml_backbone, step_content, "
            "output_schemas FROM skill_version WHERE id = ?",
            (version_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "id": row[0], "version_number": row[1],
            "yaml_backbone": row[2],
            "step_content": json.loads(row[3]) if row[3] else {},
            "output_schemas": json.loads(row[4]) if row[4] else {},
        }

    async def _fetch_divergences(
        self, skill_id: str, limit: int,
    ) -> list[dict]:
        cursor = await self._conn.execute(
            """
            SELECT d.id, d.skill_run_id, d.overall_agreement,
                   d.diff_summary, d.created_at,
                   r.state_object, r.final_output
              FROM skill_divergence d
              JOIN skill_run r ON d.skill_run_id = r.id
             WHERE r.skill_id = ?
             ORDER BY d.created_at DESC
             LIMIT ?
            """,
            (skill_id, limit),
        )
        rows = await cursor.fetchall()
        result: list[dict] = []
        for row in rows:
            result.append({
                "divergence_id": row[0],
                "run_id": row[1],
                "agreement": row[2],
                "diff_summary": json.loads(row[3]) if row[3] else None,
                "created_at": row[4],
                "state_object": json.loads(row[5]) if row[5] else {},
                "final_output": json.loads(row[6]) if row[6] else None,
            })
        return result

    async def _fetch_correction_log(self, capability_name: str) -> list[dict]:
        cursor = await self._conn.execute(
            "SELECT id, timestamp, task_id, field_corrected, "
            "original_value, corrected_value "
            "FROM correction_log WHERE task_type = ? "
            "ORDER BY timestamp DESC LIMIT 50",
            (capability_name,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0], "timestamp": r[1], "task_id": r[2],
                "field_corrected": r[3], "original_value": r[4],
                "corrected_value": r[5],
            }
            for r in rows
        ]

    async def _fetch_prior_evolution_log(self, skill_id: str) -> list[dict]:
        cursor = await self._conn.execute(
            "SELECT id, from_version_id, to_version_id, triggered_by, "
            "outcome, at FROM skill_evolution_log "
            "WHERE skill_id = ? ORDER BY at DESC LIMIT 10",
            (skill_id,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0], "from_version_id": r[1], "to_version_id": r[2],
                "triggered_by": r[3], "outcome": r[4], "at": r[5],
            }
            for r in rows
        ]

    async def _fetch_fixtures(self, skill_id: str) -> list[dict]:
        cursor = await self._conn.execute(
            "SELECT id, case_name, input, expected_output_shape, source "
            "FROM skill_fixture WHERE skill_id = ?",
            (skill_id,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0], "case_name": r[1],
                "input": json.loads(r[2]) if r[2] else {},
                "expected_output_shape": (
                    json.loads(r[3]) if r[3] else None
                ),
                "source": r[4],
            }
            for r in rows
        ]

    async def _fetch_stats(
        self, skill_id: str, baseline_agreement: float | None,
    ) -> dict:
        # Current rolling mean agreement.
        cursor = await self._conn.execute(
            """
            SELECT AVG(d.overall_agreement), COUNT(*)
              FROM skill_divergence d
              JOIN skill_run r ON d.skill_run_id = r.id
             WHERE r.skill_id = ?
            """,
            (skill_id,),
        )
        row = await cursor.fetchone()
        current_mean = float(row[0]) if row and row[0] is not None else 0.0
        total_samples = int(row[1]) if row and row[1] is not None else 0

        # Skill-run failure count.
        cursor = await self._conn.execute(
            "SELECT COUNT(*) FROM skill_run "
            "WHERE skill_id = ? AND status != 'succeeded'",
            (skill_id,),
        )
        row = await cursor.fetchone()
        failure_count = int(row[0]) if row else 0

        return {
            "baseline_agreement": baseline_agreement,
            "current_mean_agreement": current_mean,
            "total_divergence_samples": total_samples,
            "skill_run_failure_count": failure_count,
        }
