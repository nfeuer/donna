"""Capability gap tracking for the Universal Reply Handler.

Logs user requests that Donna cannot handle, deduplicates by
Jaccard similarity, and surfaces frequently-requested capabilities
for skill candidate promotion.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
import uuid6

logger = structlog.get_logger()

_JACCARD_THRESHOLD = 0.6


def _jaccard(a: str, b: str) -> float:
    """Word-level Jaccard similarity between two strings."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


class CapabilityGapTracker:
    """Track capability gaps and surface promotable candidates.

    Args:
        conn: aiosqlite connection with capability_gap table.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def log_gap(
        self,
        user_request: str,
        description: str,
        context_type: str | None = None,
        task_id: str | None = None,
    ) -> None:
        """Log a capability gap, deduplicating by Jaccard similarity."""
        now = datetime.now(tz=UTC).isoformat()
        norm_desc = description.lower().strip()

        cursor = await self._conn.execute(
            "SELECT id, description, hit_count FROM capability_gap WHERE status = 'logged'"
        )
        existing = await cursor.fetchall()

        for row in existing:
            existing_desc = row[1].lower().strip()
            similar = _jaccard(existing_desc, norm_desc) >= _JACCARD_THRESHOLD
            if existing_desc == norm_desc or similar:
                await self._conn.execute(
                    "UPDATE capability_gap SET hit_count = hit_count + 1, "
                    "last_hit_at = ? WHERE id = ?",
                    (now, row[0]),
                )
                await self._conn.commit()
                logger.info("capability_gap_deduped", gap_id=row[0], hit_count=row[2] + 1)
                return

        gap_id = str(uuid6.uuid7())
        await self._conn.execute(
            "INSERT INTO capability_gap "
            "(id, user_request, description, context_type, task_id, "
            "hit_count, status, created_at, last_hit_at) "
            "VALUES (?, ?, ?, ?, ?, 1, 'logged', ?, ?)",
            (gap_id, user_request, description, context_type, task_id, now, now),
        )
        await self._conn.commit()
        logger.info("capability_gap_logged", gap_id=gap_id, description=description[:80])

    async def get_promotable(self, min_hits: int = 3) -> list[dict[str, Any]]:
        """Return gaps with hit_count >= min_hits and status 'logged'."""
        cursor = await self._conn.execute(
            "SELECT id, description, hit_count, created_at, last_hit_at "
            "FROM capability_gap WHERE status = 'logged' AND hit_count >= ?",
            (min_hits,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0], "description": r[1], "hit_count": r[2],
                "created_at": r[3], "last_hit_at": r[4],
            }
            for r in rows
        ]

    async def mark_promoted(self, gap_id: str) -> None:
        """Mark a gap as promoted to skill candidate."""
        await self._conn.execute(
            "UPDATE capability_gap SET status = 'candidate_created' WHERE id = ?",
            (gap_id,),
        )
        await self._conn.commit()
