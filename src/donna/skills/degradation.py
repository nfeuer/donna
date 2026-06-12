"""DegradationDetector — flags trusted skills whose agreement rate has regressed.

Uses Wilson score confidence intervals on recent shadow divergence data to detect
statistically significant degradation. Skills whose current CI upper bound falls below
the stored baseline_agreement are transitioned to FLAGGED_FOR_REVIEW.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

import aiosqlite
import structlog

from donna.config import SkillSystemConfig
from donna.skills.alerting import FallbackAlert, emit_fallback_alert
from donna.skills.divergence import SkillDivergenceRepository
from donna.skills.lifecycle import SkillLifecycleManager
from donna.tasks.db_models import SkillState

logger = structlog.get_logger()


@dataclass(slots=True)
class DegradationReport:
    skill_id: str
    outcome: str  # "flagged" | "no_degradation" | "insufficient_data"
    current_successes: int
    current_trials: int
    current_lower: float
    current_upper: float
    baseline: float | None = None
    notes: str | None = None


class DegradationDetector:
    """Evaluate every trusted skill for statistical degradation."""

    def __init__(
        self,
        connection: aiosqlite.Connection,
        divergence_repo: SkillDivergenceRepository,
        lifecycle_manager: SkillLifecycleManager,
        config: SkillSystemConfig,
        fallback_alert: FallbackAlert | None = None,
    ) -> None:
        self._conn = connection
        self._divergence_repo = divergence_repo
        self._lifecycle = lifecycle_manager
        self._config = config
        self._fallback_alert = fallback_alert

    async def run(self) -> list[DegradationReport]:
        """Evaluate every trusted skill; return per-skill reports.

        Each skill is evaluated inside its own try/except so that one skill's
        failure cannot abort the whole nightly sweep (Fable critique #2). A
        per-skill failure is alerted via ``dispatch_fallback_alert`` and the
        sweep continues with the next skill.
        """
        cursor = await self._conn.execute(
            "SELECT id, baseline_agreement FROM skill WHERE state = ?",
            (SkillState.TRUSTED.value,),
        )
        trusted_skills = await cursor.fetchall()

        reports: list[DegradationReport] = []

        for skill_id, baseline_agreement in trusted_skills:
            try:
                report = await self._evaluate_skill(skill_id, baseline_agreement)
                reports.append(report)
            except Exception as exc:
                logger.exception(
                    "degradation_skill_evaluation_failed",
                    skill_id=skill_id,
                )
                await emit_fallback_alert(
                    self._fallback_alert,
                    component="skill_degradation",
                    error=f"degradation evaluation failed for {skill_id}: {exc}",
                    fallback="skipped this skill; nightly sweep continued",
                    context={"skill_id": skill_id},
                )

        return reports

    async def _evaluate_skill(
        self,
        skill_id: str,
        baseline_agreement: float | None,
    ) -> DegradationReport:
        n = self._config.degradation_rolling_window
        divergences = await self._divergence_repo.recent_for_skill(skill_id, limit=n)

        # Not enough data yet
        if len(divergences) < n:
            logger.debug(
                "degradation_insufficient_data",
                skill_id=skill_id,
                have=len(divergences),
                need=n,
            )
            return DegradationReport(
                skill_id=skill_id,
                outcome="insufficient_data",
                current_successes=0,
                current_trials=len(divergences),
                current_lower=0.0,
                current_upper=1.0,
                baseline=baseline_agreement,
            )

        # No baseline stored
        if baseline_agreement is None:
            logger.debug("degradation_no_baseline", skill_id=skill_id)
            current_successes = sum(
                1 for d in divergences
                if d.overall_agreement >= self._config.degradation_agreement_threshold
            )
            current_lower, current_upper = self.wilson_score_ci(
                current_successes, n, self._config.degradation_ci_confidence
            )
            return DegradationReport(
                skill_id=skill_id,
                outcome="insufficient_data",
                current_successes=current_successes,
                current_trials=n,
                current_lower=current_lower,
                current_upper=current_upper,
                baseline=None,
            )

        current_successes = sum(
            1 for d in divergences
            if d.overall_agreement >= self._config.degradation_agreement_threshold
        )
        current_lower, current_upper = self.wilson_score_ci(
            current_successes, n, self._config.degradation_ci_confidence
        )

        # Degradation detected when the CI upper bound is below the baseline
        if current_upper < baseline_agreement:
            notes = json.dumps(
                {
                    "current_successes": current_successes,
                    "current_trials": n,
                    "current_ci_lower": current_lower,
                    "current_ci_upper": current_upper,
                    "baseline_agreement": baseline_agreement,
                }
            )
            await self._lifecycle.transition(
                skill_id=skill_id,
                to_state=SkillState.FLAGGED_FOR_REVIEW,
                reason="degradation",
                actor="system",
                notes=notes,
            )
            logger.info(
                "skill_degradation_flagged",
                skill_id=skill_id,
                current_upper=current_upper,
                baseline=baseline_agreement,
                ci_lower=current_lower,
                ci_upper=current_upper,
                successes=current_successes,
                trials=n,
            )
            # The user must be told when a trusted skill is demoted — silent
            # degradation defeats the §23.4 safety net (Fable critique #7).
            await emit_fallback_alert(
                self._fallback_alert,
                component="skill_degradation",
                error=(
                    f"trusted skill {skill_id} degraded "
                    f"(CI upper {current_upper:.2f} < baseline "
                    f"{baseline_agreement:.2f})"
                ),
                fallback="demoted to flagged_for_review pending human decision",
                context={
                    "skill_id": skill_id,
                    "ci_upper": round(current_upper, 4),
                    "baseline_agreement": round(baseline_agreement, 4),
                },
            )
            return DegradationReport(
                skill_id=skill_id,
                outcome="flagged",
                current_successes=current_successes,
                current_trials=n,
                current_lower=current_lower,
                current_upper=current_upper,
                baseline=baseline_agreement,
                notes=(
                    f"CI=[{current_lower:.2f}, {current_upper:.2f}], "
                    f"baseline={baseline_agreement:.2f}"
                ),
            )

        logger.debug(
            "degradation_no_degradation",
            skill_id=skill_id,
            current_upper=current_upper,
            baseline=baseline_agreement,
        )
        return DegradationReport(
            skill_id=skill_id,
            outcome="no_degradation",
            current_successes=current_successes,
            current_trials=n,
            current_lower=current_lower,
            current_upper=current_upper,
            baseline=baseline_agreement,
        )

    @staticmethod
    def wilson_score_ci(
        successes: int, trials: int, confidence: float = 0.95
    ) -> tuple[float, float]:
        """Wilson score 95% CI for a binomial proportion. Returns (lower, upper)."""
        if trials == 0:
            return (0.0, 1.0)
        z_by_confidence = {0.90: 1.645, 0.95: 1.96, 0.99: 2.576}
        z = z_by_confidence.get(confidence, 1.96)
        phat = successes / trials
        denom = 1 + z**2 / trials
        centre = phat + z**2 / (2 * trials)
        margin = z * math.sqrt((phat * (1 - phat) + z**2 / (4 * trials)) / trials)
        lower = max(0.0, (centre - margin) / denom)
        upper = min(1.0, (centre + margin) / denom)
        return (lower, upper)
