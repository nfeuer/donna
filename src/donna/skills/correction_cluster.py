"""CorrectionClusterDetector — fast-path trigger to flagged_for_review when
users issue multiple corrections on a skill's outputs in a short window.

Spec §6.6: ground truth corrections are stronger signal than shadow opinion.
This detector flags trusted/shadow_primary skills as soon as a cluster appears,
without waiting for the EOD digest.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import aiosqlite
import structlog

from donna.config import SkillSystemConfig
from donna.skills.lifecycle import (
    IllegalTransitionError,
    SkillLifecycleManager,
)
from donna.tasks.db_models import SkillState

logger = structlog.get_logger()

ELIGIBLE_STATES = ("trusted", "shadow_primary")


class CorrectionClusterDetector:
    def __init__(
        self,
        connection: aiosqlite.Connection,
        lifecycle_manager: SkillLifecycleManager,
        notifier: Callable[[str], Awaitable[None]],
        config: SkillSystemConfig,
    ) -> None:
        self._conn = connection
        self._lifecycle = lifecycle_manager
        self._notifier = notifier
        self._config = config

    async def scan_once(self) -> list[dict[str, Any]]:
        placeholders = ",".join("?" * len(ELIGIBLE_STATES))
        cursor = await self._conn.execute(
            f"SELECT id, capability_name FROM skill "
            f"WHERE state IN ({placeholders})",
            ELIGIBLE_STATES,
        )
        eligible = [(r[0], r[1]) for r in await cursor.fetchall()]
        if not eligible:
            return []

        flagged: list[dict[str, Any]] = []
        for skill_id, capability_name in eligible:
            fired = await self._check_skill(
                skill_id=skill_id, capability_name=capability_name,
            )
            if fired is not None:
                flagged.append(fired)
        return flagged

    async def scan_for_capability(
        self, capability_name: str,
    ) -> dict[str, Any] | None:
        """Scan recent corrections for any trusted/shadow_primary skill for
        this capability. Fires urgent flag+notification if the threshold is
        exceeded. Called synchronously from the correction-log write path
        (F-7 fast path) in addition to the nightly :meth:`scan_once`.
        """
        placeholders = ",".join("?" * len(ELIGIBLE_STATES))
        cursor = await self._conn.execute(
            f"SELECT id FROM skill WHERE capability_name = ? "
            f"AND state IN ({placeholders})",
            (capability_name, *ELIGIBLE_STATES),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return await self._check_skill(
            skill_id=row[0], capability_name=capability_name,
        )

    async def _check_skill(
        self, skill_id: str, capability_name: str,
    ) -> dict[str, Any] | None:
        cursor = await self._conn.execute(
            "SELECT id, started_at FROM skill_run "
            "WHERE skill_id = ? ORDER BY started_at DESC LIMIT ?",
            (skill_id, self._config.correction_cluster_window_runs),
        )
        recent_runs = list(await cursor.fetchall())
        if not recent_runs:
            return None

        oldest_at = recent_runs[-1][1]
        cursor = await self._conn.execute(
            "SELECT COUNT(*) FROM correction_log "
            "WHERE task_type = ? AND timestamp >= ?",
            (capability_name, oldest_at),
        )
        row = await cursor.fetchone()
        correction_count = int(row[0]) if row else 0

        if correction_count < self._config.correction_cluster_threshold:
            return None

        try:
            await self._lifecycle.transition(
                skill_id=skill_id,
                to_state=SkillState.FLAGGED_FOR_REVIEW,
                reason="degradation",
                actor="system",
                notes=(
                    f"correction_cluster: {correction_count} corrections "
                    f"over last {len(recent_runs)} runs"
                ),
            )
        except IllegalTransitionError as exc:
            logger.info(
                "correction_cluster_transition_skipped",
                skill_id=skill_id, error=str(exc),
            )
            return None

        message = (
            f"Skill '{capability_name}' flagged for review: "
            f"{correction_count} user corrections in the last "
            f"{len(recent_runs)} runs. Review at /admin/skills/{skill_id}."
        )
        try:
            await self._notifier(message)
        except Exception:
            logger.exception("correction_cluster_notifier_failed", skill_id=skill_id)

        logger.info(
            "correction_cluster_flagged",
            skill_id=skill_id,
            capability_name=capability_name,
            correction_count=correction_count,
            window_runs=len(recent_runs),
        )

        return {
            "skill_id": skill_id,
            "capability_name": capability_name,
            "correction_count": correction_count,
        }
