"""SkillDivergenceRepository — reads/writes skill_divergence rows."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiosqlite
import structlog
import uuid6

logger = structlog.get_logger()

SKILL_DIVERGENCE_COLUMNS = (
    "id", "skill_run_id", "shadow_invocation_id",
    "overall_agreement", "diff_summary", "flagged_for_evolution", "created_at",
)
SELECT_SKILL_DIVERGENCE = ", ".join(SKILL_DIVERGENCE_COLUMNS)


@dataclass(slots=True)
class SkillDivergenceRow:
    id: str
    skill_run_id: str
    shadow_invocation_id: str
    overall_agreement: float
    diff_summary: dict | None
    flagged_for_evolution: bool
    created_at: datetime


def row_to_divergence(row: tuple) -> SkillDivergenceRow:
    return SkillDivergenceRow(
        id=row[0], skill_run_id=row[1], shadow_invocation_id=row[2],
        overall_agreement=row[3],
        diff_summary=_parse_json(row[4]),
        flagged_for_evolution=bool(row[5]),
        created_at=_parse_dt(row[6]),
    )


class SkillDivergenceRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._conn = connection

    async def record(
        self,
        skill_run_id: str,
        shadow_invocation_id: str,
        overall_agreement: float,
        diff_summary: dict | None,
        flagged_for_evolution: bool = False,
    ) -> str:
        div_id = str(uuid6.uuid7())
        now = datetime.now(timezone.utc).isoformat()

        await self._conn.execute(
            f"""
            INSERT INTO skill_divergence ({SELECT_SKILL_DIVERGENCE})
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                div_id, skill_run_id, shadow_invocation_id,
                overall_agreement,
                json.dumps(diff_summary) if diff_summary is not None else None,
                1 if flagged_for_evolution else 0,
                now,
            ),
        )
        await self._conn.commit()
        return div_id

    async def recent_by_run_ids(self, run_ids: list[str], limit: int = 100) -> list[SkillDivergenceRow]:
        if not run_ids:
            return []
        placeholders = ",".join("?" * len(run_ids))
        cursor = await self._conn.execute(
            f"""
            SELECT {SELECT_SKILL_DIVERGENCE}
              FROM skill_divergence
             WHERE skill_run_id IN ({placeholders})
             ORDER BY created_at DESC LIMIT ?
            """,
            (*run_ids, limit),
        )
        rows = await cursor.fetchall()
        return [row_to_divergence(r) for r in rows]

    async def recent_for_skill(
        self, skill_id: str, limit: int = 100,
    ) -> list[SkillDivergenceRow]:
        """Join through skill_run to list divergences for a skill."""
        cursor = await self._conn.execute(
            f"""
            SELECT {', '.join(f'd.{c}' for c in SKILL_DIVERGENCE_COLUMNS)}
              FROM skill_divergence d
              JOIN skill_run r ON d.skill_run_id = r.id
             WHERE r.skill_id = ?
             ORDER BY d.created_at DESC
             LIMIT ?
            """,
            (skill_id, limit),
        )
        rows = await cursor.fetchall()
        return [row_to_divergence(r) for r in rows]


def _parse_json(value: Any) -> dict | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    return json.loads(value)


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)
