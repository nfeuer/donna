"""SkillEvolutionLogRepository — reads/writes skill_evolution_log rows."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import aiosqlite
import structlog
import uuid6

logger = structlog.get_logger()


class SkillEvolutionLogRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._conn = connection

    async def record(
        self,
        skill_id: str,
        from_version_id: str,
        to_version_id: str | None,
        triggered_by: str,
        claude_invocation_id: str | None,
        diagnosis: dict | None,
        targeted_case_ids: list[str] | None,
        validation_results: dict | None,
        outcome: str,
    ) -> str:
        entry_id = str(uuid6.uuid7())
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            """
            INSERT INTO skill_evolution_log
                (id, skill_id, from_version_id, to_version_id,
                 triggered_by, claude_invocation_id,
                 diagnosis, targeted_case_ids, validation_results,
                 outcome, at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id, skill_id, from_version_id, to_version_id,
                triggered_by, claude_invocation_id,
                json.dumps(diagnosis) if diagnosis is not None else None,
                json.dumps(targeted_case_ids) if targeted_case_ids is not None else None,
                json.dumps(validation_results) if validation_results is not None else None,
                outcome, now,
            ),
        )
        await self._conn.commit()
        return entry_id

    async def last_n_outcomes(self, skill_id: str, n: int) -> list[str]:
        """Return outcomes of the last *n* log entries, newest first."""
        cursor = await self._conn.execute(
            "SELECT outcome FROM skill_evolution_log "
            "WHERE skill_id = ? ORDER BY at DESC LIMIT ?",
            (skill_id, n),
        )
        rows = await cursor.fetchall()
        return [r[0] for r in rows]
