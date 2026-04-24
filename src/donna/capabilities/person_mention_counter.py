"""Slice 16 — count bare ``[[Name]]`` and ``[[People/Name]]`` mentions.

Pure SQL sweep over ``memory_chunks`` joined on ``memory_documents``
filtered to a look-back window. Returns names with mention counts
exceeding a threshold — the trigger-A path for
:class:`donna.capabilities.person_profile_skill.PersonProfileSkill`.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

import aiosqlite
import structlog

logger = structlog.get_logger()


_MENTION_RE = re.compile(r"\[\[(?:People/)?([^\[\]/|#]+)(?:\|[^\[\]]*)?\]\]")


class PersonMentionCounter:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._conn = connection

    async def scan(
        self,
        *,
        user_id: str,
        lookback_days: int,
        now: datetime | None = None,
    ) -> dict[str, int]:
        """Return ``{name: mention_count}`` over the lookback window.

        Scans every ``memory_chunks.content`` whose parent document is
        ``user_id``'s, not soft-deleted, and ``updated_at`` within the
        window. Aggregates both ``[[People/X]]`` and ``[[X]]`` forms
        under a single name key.
        """
        end = now or datetime.utcnow()
        start = end - timedelta(days=lookback_days)
        async with self._conn.execute(
            "SELECT c.content FROM memory_chunks c "
            "JOIN memory_documents d ON d.id = c.document_id "
            "WHERE d.user_id=? AND d.deleted_at IS NULL "
            "  AND d.updated_at >= ? AND d.updated_at < ?",
            (user_id, start.isoformat(), end.isoformat()),
        ) as cur:
            rows = await cur.fetchall()

        counts: dict[str, int] = {}
        for (content,) in rows:
            if not content or "[[" not in content:
                continue
            for match in _MENTION_RE.finditer(content):
                name = match.group(1).strip()
                if not name:
                    continue
                counts[name] = counts.get(name, 0) + 1
        return counts


def names_above_threshold(
    counts: dict[str, int], threshold: int
) -> list[tuple[str, int]]:
    """Return names at or above ``threshold`` sorted desc by count."""
    return sorted(
        [(n, c) for n, c in counts.items() if c >= threshold],
        key=lambda t: (-t[1], t[0]),
    )


__all__: list[Any] = ["PersonMentionCounter", "names_above_threshold"]
