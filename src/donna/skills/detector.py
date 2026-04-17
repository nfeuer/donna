"""SkillCandidateDetector — surfaces high-value claude_native task types."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import aiosqlite
import structlog

from donna.config import SkillSystemConfig
from donna.skills.candidate_report import SkillCandidateRepository

logger = structlog.get_logger()


class SkillCandidateDetector:
    """Identify claude_native task types that are strong skill-draft candidates.

    Queries invocation_log over the last 30 days, computes volume, average cost,
    expected savings, and output-shape variance per task_type, then creates a
    skill_candidate_report row for each type that crosses the configured savings
    threshold and has no open (new/drafted) candidate already.
    """

    def __init__(
        self,
        connection: aiosqlite.Connection,
        candidate_repo: SkillCandidateRepository,
        config: SkillSystemConfig,
        skill_overhead_ratio: float = 0.15,
    ) -> None:
        self._conn = connection
        self._repo = candidate_repo
        self._config = config
        self._overhead = skill_overhead_ratio

    async def run(self) -> list[str]:
        """Analyze invocation_log and create candidate_report rows for top task_types.

        Returns a list of newly created candidate IDs.
        """
        window_start = (
            datetime.now(timezone.utc) - timedelta(days=30)
        ).isoformat()

        claude_native_task_types = await self._claude_native_task_types()
        if not claude_native_task_types:
            return []

        placeholders = ",".join("?" * len(claude_native_task_types))
        cursor = await self._conn.execute(
            f"""
            SELECT task_type, COUNT(*) AS volume, AVG(cost_usd) AS avg_cost
              FROM invocation_log
             WHERE timestamp >= ?
               AND task_type IN ({placeholders})
             GROUP BY task_type
            """,
            (window_start, *claude_native_task_types),
        )
        rows = await cursor.fetchall()

        if not rows:
            return []

        existing_capability_names = await self._existing_open_candidate_capabilities()

        created: list[str] = []
        for task_type, volume_raw, avg_cost_raw in rows:
            volume = int(volume_raw or 0)
            avg_cost = float(avg_cost_raw or 0.0)
            expected_savings = volume * avg_cost * (1.0 - self._overhead)

            if expected_savings < self._config.auto_draft_min_expected_savings_usd:
                continue
            if task_type in existing_capability_names:
                continue

            variance_score = await self._compute_variance_for_task_type(
                task_type, window_start
            )

            candidate_id = await self._repo.create(
                capability_name=task_type,
                task_pattern_hash=None,
                expected_savings_usd=expected_savings,
                volume_30d=volume,
                variance_score=variance_score,
            )
            created.append(candidate_id)
            logger.info(
                "skill_candidate_detected",
                task_type=task_type,
                volume_30d=volume,
                expected_savings_usd=expected_savings,
                variance_score=variance_score,
                candidate_id=candidate_id,
            )

        return created

    async def _claude_native_task_types(self) -> list[str]:
        """Return task_types that have no skill row or whose skill state is claude_native.

        Uses invocation_log task_types as the ground truth; cross-references
        against the skill table to exclude any with a non-claude_native skill.
        """
        cursor = await self._conn.execute(
            "SELECT DISTINCT task_type FROM invocation_log"
        )
        all_task_types = {row[0] for row in await cursor.fetchall() if row[0]}
        if not all_task_types:
            return []

        # Exclude task_types backed by a non-claude_native skill.
        cursor = await self._conn.execute(
            "SELECT capability_name FROM skill WHERE state != 'claude_native'"
        )
        non_native = {row[0] for row in await cursor.fetchall()}
        claude_native = all_task_types - non_native
        return sorted(claude_native)

    async def _existing_open_candidate_capabilities(self) -> set[str]:
        """Return capability names the detector must NOT re-surface.

        Includes capabilities with an open ('new'/'drafted') report AND
        capabilities Claude has already decided are NOT skill candidates
        ('claude_native_registered'). The latter is a permanent veto — once
        Claude has said "this is one-off / user-specific / low-value", the
        detector stops proposing the same task_type until the row is
        manually flipped.
        """
        cursor = await self._conn.execute(
            "SELECT capability_name FROM skill_candidate_report "
            "WHERE status IN ('new', 'drafted', 'claude_native_registered')"
        )
        return {row[0] for row in await cursor.fetchall() if row[0]}

    async def _compute_variance_for_task_type(
        self,
        task_type: str,
        window_start: str,
    ) -> float:
        """Compute 1 - unique_shapes/total as a repetitiveness score.

        Higher score means more repetitive output shapes — a better skill candidate.
        Returns 0.0 when there are no outputs or all shapes are unique.
        """
        cursor = await self._conn.execute(
            "SELECT output FROM invocation_log "
            "WHERE task_type = ? AND timestamp >= ? AND output IS NOT NULL",
            (task_type, window_start),
        )
        outputs = [row[0] for row in await cursor.fetchall()]
        if not outputs:
            return 0.0

        shapes: set[tuple] = set()
        for o in outputs:
            try:
                parsed = json.loads(o) if isinstance(o, str) else o
                if isinstance(parsed, dict):
                    shape: tuple = tuple(sorted(parsed.keys()))
                else:
                    shape = (type(parsed).__name__,)
            except (json.JSONDecodeError, TypeError):
                shape = ("unparseable",)
            shapes.add(shape)

        return max(0.0, 1.0 - (len(shapes) / len(outputs)))
