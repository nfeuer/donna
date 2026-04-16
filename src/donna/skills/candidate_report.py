"""SkillCandidateRepository — reads/writes skill_candidate_report rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiosqlite
import structlog
import uuid6

logger = structlog.get_logger()

SKILL_CANDIDATE_REPORT_COLUMNS = (
    "id", "capability_name", "task_pattern_hash",
    "expected_savings_usd", "volume_30d", "variance_score",
    "status", "reported_at", "resolved_at",
)
SELECT_SKILL_CANDIDATE_REPORT = ", ".join(SKILL_CANDIDATE_REPORT_COLUMNS)


@dataclass(slots=True)
class SkillCandidateReportRow:
    id: str
    capability_name: str | None
    task_pattern_hash: str | None
    expected_savings_usd: float
    volume_30d: int
    variance_score: float | None
    status: str
    reported_at: datetime
    resolved_at: datetime | None


def row_to_candidate_report(row: tuple) -> SkillCandidateReportRow:
    return SkillCandidateReportRow(
        id=row[0],
        capability_name=row[1],
        task_pattern_hash=row[2],
        expected_savings_usd=row[3],
        volume_30d=row[4],
        variance_score=row[5],
        status=row[6],
        reported_at=_parse_dt(row[7]),
        resolved_at=_parse_dt(row[8]) if row[8] is not None else None,
    )


class SkillCandidateRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._conn = connection

    async def create(
        self,
        capability_name: str | None,
        task_pattern_hash: str | None,
        expected_savings_usd: float,
        volume_30d: int,
        variance_score: float | None,
    ) -> str:
        """Create a new candidate report row with status='new'; return the new id."""
        candidate_id = str(uuid6.uuid7())
        now = datetime.now(timezone.utc).isoformat()

        await self._conn.execute(
            f"""
            INSERT INTO skill_candidate_report ({SELECT_SKILL_CANDIDATE_REPORT})
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id, capability_name, task_pattern_hash,
                expected_savings_usd, volume_30d, variance_score,
                "new", now, None,
            ),
        )
        await self._conn.commit()
        return candidate_id

    async def list_new(self, limit: int = 100) -> list[SkillCandidateReportRow]:
        """Return status=='new' rows ordered by expected_savings_usd DESC then reported_at DESC."""
        cursor = await self._conn.execute(
            f"""
            SELECT {SELECT_SKILL_CANDIDATE_REPORT}
              FROM skill_candidate_report
             WHERE status = 'new'
             ORDER BY expected_savings_usd DESC, reported_at DESC
             LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [row_to_candidate_report(r) for r in rows]

    async def mark_drafted(self, candidate_id: str) -> None:
        """Update status to 'drafted' and set resolved_at=now."""
        await self._update_status(candidate_id, "drafted")

    async def mark_dismissed(self, candidate_id: str) -> None:
        """Update status to 'dismissed' and set resolved_at=now."""
        await self._update_status(candidate_id, "dismissed")

    async def mark_stale(self, candidate_id: str) -> None:
        """Update status to 'stale' and set resolved_at=now."""
        await self._update_status(candidate_id, "stale")

    async def _update_status(self, candidate_id: str, status: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            """
            UPDATE skill_candidate_report
               SET status = ?, resolved_at = ?
             WHERE id = ?
            """,
            (status, now, candidate_id),
        )
        await self._conn.commit()


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)
