"""SkillCandidateRepository — reads/writes skill_candidate_report rows."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aiosqlite
import structlog
import uuid6

logger = structlog.get_logger()

SKILL_CANDIDATE_REPORT_COLUMNS = (
    "id", "capability_name", "task_pattern_hash",
    "expected_savings_usd", "volume_30d", "variance_score",
    "status", "reported_at", "resolved_at", "reasoning",
)
SELECT_SKILL_CANDIDATE_REPORT = ", ".join(SKILL_CANDIDATE_REPORT_COLUMNS)


def fingerprint_message(message: str) -> str:
    """Return a stable short hash for a user message.

    Whitespace is collapsed and the message is lowercased before hashing so
    that "every sunday review tax prep folder" and
    "  Every Sunday review tax prep folder  " map to the same fingerprint.
    """
    normalized = " ".join(message.lower().split())
    return hashlib.sha256(normalized.encode()).hexdigest()[:32]


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
    reasoning: str | None = None


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
        reasoning=row[9] if len(row) > 9 else None,
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
        now = datetime.now(UTC).isoformat()

        await self._conn.execute(
            f"""
            INSERT INTO skill_candidate_report ({SELECT_SKILL_CANDIDATE_REPORT})
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id, capability_name, task_pattern_hash,
                expected_savings_usd, volume_30d, variance_score,
                "new", now, None, None,
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

    async def list_claude_native_registered_capabilities(self) -> set[str]:
        """Return capability_name values for rows flagged claude_native_registered.

        These rows represent patterns that Claude has explicitly decided are
        NOT skill candidates (e.g. one-off, user-specific). The detector uses
        this set to avoid re-surfacing the same capability as a new candidate.
        """
        cursor = await self._conn.execute(
            "SELECT capability_name FROM skill_candidate_report "
            "WHERE status = 'claude_native_registered'"
        )
        return {row[0] for row in await cursor.fetchall() if row[0]}

    async def list_claude_native_registered_fingerprints(self) -> set[str]:
        """Return pattern_fingerprint values flagged claude_native_registered."""
        cursor = await self._conn.execute(
            "SELECT pattern_fingerprint FROM skill_candidate_report "
            "WHERE status = 'claude_native_registered' "
            "AND pattern_fingerprint IS NOT NULL"
        )
        return {row[0] for row in await cursor.fetchall() if row[0]}

    async def upsert_claude_native_registered(
        self,
        *,
        fingerprint: str,
        reasoning: str,
    ) -> str:
        """Insert or update a claude_native_registered row keyed by fingerprint.

        Idempotent: calling twice for the same fingerprint updates the
        existing row's reasoning/resolved_at rather than creating a duplicate.
        Returns the row id (either the newly-created one or the existing one).
        """
        now = datetime.now(UTC).isoformat()
        cursor = await self._conn.execute(
            "SELECT id FROM skill_candidate_report "
            "WHERE pattern_fingerprint = ? AND status = 'claude_native_registered'",
            (fingerprint,),
        )
        existing = await cursor.fetchone()
        if existing is not None:
            candidate_id = existing[0]
            await self._conn.execute(
                "UPDATE skill_candidate_report "
                "SET resolved_at = ?, reasoning = ? "
                "WHERE id = ?",
                (now, reasoning, candidate_id),
            )
            await self._conn.commit()
            return candidate_id

        candidate_id = str(uuid6.uuid7())
        # pattern_fingerprint hashes the user utterance; detector skip is
        # best-effort — fingerprint collisions across different utterances are
        # rare. The detector also computes fingerprint_message(task_type) and
        # consults list_claude_native_registered_fingerprints() before inserting
        # a new row, so task_type strings that happen to fingerprint identically
        # to a previously-registered user message will also be skipped.
        # task_pattern_hash stays NULL here — it was never a meaningful hash on
        # these rows; reasoning lives in its own column.
        await self._conn.execute(
            f"""
            INSERT INTO skill_candidate_report
                ({SELECT_SKILL_CANDIDATE_REPORT}, pattern_fingerprint)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                None,  # capability_name — unknown at escalate time
                None,  # task_pattern_hash — leave NULL; reasoning has its own col
                0.0,
                0,
                None,
                "claude_native_registered",
                now,
                now,  # resolved_at set immediately (terminal status)
                reasoning,
                fingerprint,
            ),
        )
        await self._conn.commit()
        logger.info(
            "claude_native_pattern_registered",
            candidate_id=candidate_id,
            fingerprint=fingerprint,
        )
        return candidate_id

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
        now = datetime.now(UTC).isoformat()
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
