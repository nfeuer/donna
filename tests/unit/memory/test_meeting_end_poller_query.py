"""Slice 15 — :class:`MeetingEndPoller` SQL exclusion test.

Seeds two rows in ``calendar_mirror`` whose ``end_time`` is 2 minutes
before "now" — one has a corresponding indexed meeting note in
``memory_documents`` and must be excluded; the other has no such row
and must be dispatched.

A seams-only test for the query: the skill is a stub that records the
events it was handed, so we verify the poller's SELECT logic in
isolation without a full vault/LLM round-trip.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from donna.capabilities.meeting_end_poller import MeetingEndPoller
from donna.capabilities.meeting_note_skill import CalendarEventRow
from donna.config import MeetingNoteSkillConfig


class _RecordingSkill:
    """Captures the events the poller dispatched to us."""

    def __init__(self, *, raise_for: set[str] | None = None) -> None:
        self.events: list[CalendarEventRow] = []
        self._raise_for = raise_for or set()

    async def run_for_event(self, event: CalendarEventRow) -> None:
        self.events.append(event)
        if event.event_id in self._raise_for:
            raise RuntimeError("boom")


async def _open_db() -> tuple[aiosqlite.Connection, Path]:
    tmp = Path(tempfile.mkstemp(prefix="donna_pollerq_", suffix=".db")[1])
    tmp.unlink(missing_ok=True)
    from alembic.config import Config as AlembicConfig

    from alembic import command

    cfg = AlembicConfig("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{tmp}")
    await asyncio.to_thread(command.upgrade, cfg, "head")
    conn = await aiosqlite.connect(str(tmp))
    await conn.execute("PRAGMA foreign_keys=ON")
    return conn, tmp


@pytest_asyncio.fixture
async def db_conn() -> AsyncIterator[aiosqlite.Connection]:
    conn, path = await _open_db()
    try:
        yield conn
    finally:
        await conn.close()
        path.unlink(missing_ok=True)


async def _seed_calendar_row(
    conn: aiosqlite.Connection,
    *,
    event_id: str,
    user_id: str,
    summary: str,
    end_time: datetime,
    attendees: list[dict] | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO calendar_mirror
            (event_id, user_id, calendar_id, summary, start_time, end_time,
             donna_managed, donna_task_id, etag, last_synced, attendees)
        VALUES (?, ?, 'primary', ?, ?, ?, 0, NULL, '', ?, ?)
        """,
        (
            event_id,
            user_id,
            summary,
            (end_time - timedelta(minutes=30)).isoformat(),
            end_time.isoformat(),
            datetime.utcnow().isoformat(),
            json.dumps(attendees) if attendees else None,
        ),
    )
    await conn.commit()


async def _seed_indexed_meeting_note(
    conn: aiosqlite.Connection,
    *,
    user_id: str,
    calendar_event_id: str,
) -> None:
    doc_id = str(uuid.uuid4())
    meta = {"calendar_event_id": calendar_event_id, "type": "meeting"}
    await conn.execute(
        """
        INSERT INTO memory_documents
            (id, user_id, source_type, source_id, title, uri, content_hash,
             created_at, updated_at, deleted_at, sensitive, metadata_json)
        VALUES (?, ?, 'vault', ?, ?, NULL, 'hash', datetime('now'),
                datetime('now'), NULL, 0, ?)
        """,
        (
            doc_id,
            user_id,
            f"Meetings/{calendar_event_id}.md",
            "Prior",
            json.dumps(meta),
        ),
    )
    await conn.commit()


@pytest.mark.asyncio
async def test_run_once_excludes_events_with_existing_notes(
    db_conn: aiosqlite.Connection,
) -> None:
    now = datetime.utcnow()
    two_min_ago = now - timedelta(minutes=2)
    await _seed_calendar_row(
        db_conn, event_id="E1", user_id="nick", summary="Sync", end_time=two_min_ago
    )
    await _seed_calendar_row(
        db_conn, event_id="E2", user_id="nick", summary="1:1", end_time=two_min_ago
    )
    # Only E1 has a matching indexed meeting note.
    await _seed_indexed_meeting_note(db_conn, user_id="nick", calendar_event_id="E1")

    skill = _RecordingSkill()
    cfg = MeetingNoteSkillConfig()  # 5-minute lookback default
    poller = MeetingEndPoller(
        connection=db_conn, skill=skill, config=cfg, user_id="nick"
    )

    n = await poller.run_once()
    assert n == 1
    assert [e.event_id for e in skill.events] == ["E2"]


@pytest.mark.asyncio
async def test_run_once_scopes_by_user(db_conn: aiosqlite.Connection) -> None:
    now = datetime.utcnow()
    two_min_ago = now - timedelta(minutes=2)
    await _seed_calendar_row(
        db_conn, event_id="E1", user_id="nick", summary="A", end_time=two_min_ago
    )
    await _seed_calendar_row(
        db_conn, event_id="E2", user_id="other", summary="B", end_time=two_min_ago
    )

    skill = _RecordingSkill()
    cfg = MeetingNoteSkillConfig()
    poller = MeetingEndPoller(
        connection=db_conn, skill=skill, config=cfg, user_id="nick"
    )

    await poller.run_once()
    assert [e.event_id for e in skill.events] == ["E1"]


@pytest.mark.asyncio
async def test_per_hit_exception_does_not_abort_loop(
    db_conn: aiosqlite.Connection,
) -> None:
    now = datetime.utcnow()
    two_min_ago = now - timedelta(minutes=2)
    await _seed_calendar_row(
        db_conn, event_id="E1", user_id="nick", summary="A", end_time=two_min_ago
    )
    await _seed_calendar_row(
        db_conn, event_id="E2", user_id="nick", summary="B", end_time=two_min_ago
    )

    skill = _RecordingSkill(raise_for={"E1"})
    cfg = MeetingNoteSkillConfig()
    poller = MeetingEndPoller(
        connection=db_conn, skill=skill, config=cfg, user_id="nick"
    )

    processed = await poller.run_once()
    # Both rows are attempted; the raising one is logged and skipped.
    assert processed == 2
    assert {e.event_id for e in skill.events} == {"E1", "E2"}
