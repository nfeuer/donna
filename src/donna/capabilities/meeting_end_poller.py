"""Slice 15 — background poller that triggers :class:`MeetingNoteSkill`.

Runs as an asyncio task in the main run loop (wired from
``src/donna/cli_wiring.py``). Every ``poll_interval_seconds`` it queries
``calendar_mirror`` for events whose ``end_time`` fell within the last
``lookback_minutes`` and which do NOT already have a corresponding
meeting note indexed in ``memory_documents``. Each hit is dispatched
sequentially (no concurrent LLM calls) so cost is predictable and the
circuit breaker behaves normally.

The exclusion uses ``json_extract(metadata_json, '$.key')`` rather than
SQLite's ``->>`` operator — works on every SQLite build Donna has ever
shipped against.

This is NOT a DSL skill; see :mod:`donna.capabilities.meeting_note_skill`
for the companion discussion.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

import aiosqlite
import structlog

from donna.capabilities.meeting_note_skill import (
    CalendarEventRow,
    MeetingNoteSkill,
)

if TYPE_CHECKING:
    from donna.config import MeetingNoteSkillConfig

logger = structlog.get_logger()


_SELECT_SQL = """
SELECT event_id, user_id, calendar_id, summary, start_time, end_time, attendees
FROM calendar_mirror
WHERE datetime(end_time) BETWEEN datetime('now', ?) AND datetime('now')
  AND user_id = ?
  AND event_id NOT IN (
      SELECT json_extract(metadata_json, '$.calendar_event_id')
      FROM memory_documents
      WHERE source_type = 'vault'
        AND json_extract(metadata_json, '$.type') = 'meeting'
  )
""".strip()


def _parse_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


class MeetingEndPoller:
    """Periodically scans ``calendar_mirror`` for just-ended events."""

    def __init__(
        self,
        *,
        connection: aiosqlite.Connection,
        skill: MeetingNoteSkill,
        config: MeetingNoteSkillConfig,
        user_id: str,
    ) -> None:
        self._conn = connection
        self._skill = skill
        self._config = config
        self._user_id = user_id

    async def run_once(self) -> int:
        """Process all currently-eligible events. Returns the hit count."""
        lookback = f"-{self._config.lookback_minutes} minutes"
        async with self._conn.execute(
            _SELECT_SQL, (lookback, self._user_id)
        ) as cur:
            rows = await cur.fetchall()

        processed = 0
        for row in rows:
            event_id, user_id, calendar_id, summary, start_s, end_s, attendees = (
                row
            )
            event = CalendarEventRow(
                event_id=event_id,
                user_id=user_id,
                calendar_id=calendar_id,
                summary=summary,
                start_time=_parse_dt(start_s),
                end_time=_parse_dt(end_s),
                attendees=attendees,
            )
            logger.info(
                "meeting_end_detected",
                event_id=event.event_id,
                user_id=event.user_id,
                summary=event.summary,
                end_time=event.end_time.isoformat(),
            )
            try:
                await self._skill.run_for_event(event)
            except Exception as exc:
                # Skill's writer already catches and logs; this is a
                # belt-and-suspenders guard so a surprise failure in the
                # dispatch path never kills the poll loop.
                logger.warning(
                    "meeting_end_dispatch_failed",
                    event_id=event.event_id,
                    reason=str(exc),
                    exc_type=type(exc).__name__,
                )
            processed += 1

        return processed

    async def run_forever(self) -> None:
        """Long-running loop. Cancellation-safe."""
        interval = max(1, self._config.poll_interval_seconds)
        logger.info(
            "meeting_end_poller_start",
            interval_seconds=interval,
            lookback_minutes=self._config.lookback_minutes,
            user_id=self._user_id,
        )
        try:
            while True:
                try:
                    await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "meeting_end_poller_cycle_failed",
                        reason=str(exc),
                        exc_type=type(exc).__name__,
                    )
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("meeting_end_poller_cancelled", user_id=self._user_id)
            raise
