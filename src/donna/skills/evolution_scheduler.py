"""EvolutionScheduler — iterates degraded skills and invokes Evolver for each.

Runs as part of the nightly cron BEFORE auto-drafting (spec §6.5 budget
ordering: evolution of active problems outranks speculative drafting).
"""

from __future__ import annotations

from typing import Any

import aiosqlite
import structlog

from donna.config import SkillSystemConfig
from donna.skills.evolution import EvolutionReport

logger = structlog.get_logger()


class EvolutionScheduler:
    def __init__(
        self,
        connection: aiosqlite.Connection,
        evolver: Any,
        config: SkillSystemConfig,
    ) -> None:
        self._conn = connection
        self._evolver = evolver
        self._config = config

    async def run(self, remaining_budget_usd: float) -> list[EvolutionReport]:
        """Evolve every eligible degraded skill within daily_cap + budget."""
        skill_ids = await self._list_degraded_skills()
        if not skill_ids:
            return []

        budget = remaining_budget_usd
        per_cost = self._config.evolution_estimated_cost_usd
        reports: list[EvolutionReport] = []

        for skill_id in skill_ids[: self._config.evolution_daily_cap]:
            if budget < per_cost:
                logger.info(
                    "evolution_scheduler_budget_exhausted",
                    skill_id=skill_id, remaining=budget,
                )
                break
            try:
                report = await self._evolver.evolve_one(
                    skill_id=skill_id, triggered_by="nightly",
                )
            except Exception as exc:
                logger.exception(
                    "evolution_scheduler_unexpected_error",
                    skill_id=skill_id,
                )
                report = EvolutionReport(
                    skill_id=skill_id, outcome="error",
                    rationale=str(exc),
                )
            reports.append(report)
            logger.info(
                "skill_evolution_outcome",
                skill_id=report.skill_id,
                outcome=report.outcome,
                cost_usd=report.cost_usd,
                latency_ms=report.latency_ms,
                new_version_id=report.new_version_id,
                rationale=report.rationale,
            )
            if report.outcome not in ("budget_exhausted", "skipped"):
                budget -= per_cost
            if report.outcome == "budget_exhausted":
                break

        return reports

    async def _list_degraded_skills(self) -> list[str]:
        cursor = await self._conn.execute(
            "SELECT id FROM skill WHERE state = 'degraded' ORDER BY updated_at ASC"
        )
        rows = await cursor.fetchall()
        return [r[0] for r in rows]
