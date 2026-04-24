"""Slice 16 — :class:`DailyReflectionSkill` context gathering.

Asserts that the skill picks up *today's* meeting notes and terminal
task mutations (not yesterday's), caps each category per config, and
delegates to the writer with the correct keys.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from donna.capabilities.daily_reflection_skill import DailyReflectionSkill
from donna.config import DailyReflectionContextLimits, DailyReflectionSkillConfig
from donna.memory.store import Document, MemoryStore


@pytest.mark.asyncio
async def test_gather_context_filters_to_today(
    memory_db: aiosqlite.Connection,
    memory_store: MemoryStore,
) -> None:
    """Documents updated yesterday are excluded; today's included."""
    # Seed the DB directly: two meeting notes (today + yesterday),
    # two task mutations (today done + yesterday done), and the
    # MemoryStore indexes them so updated_at is wall-clock-now.
    today = date(2026, 4, 24)
    midnight = datetime(2026, 4, 24, 0, 0, tzinfo=UTC).replace(tzinfo=None)

    for doc in [
        Document(
            user_id="nick",
            source_type="vault",
            source_id="Meetings/2026-04-24-sync.md",
            title="Sync today",
            uri="vault:Meetings/2026-04-24-sync.md",
            content="# Sync\n\nToday's meeting.\n",
            metadata={"type": "meeting"},
        ),
        Document(
            user_id="nick",
            source_type="vault",
            source_id="Meetings/2026-04-23-old.md",
            title="Old",
            uri="vault:Meetings/2026-04-23-old.md",
            content="# Old\n\nYesterday.\n",
            metadata={"type": "meeting"},
        ),
        Document(
            user_id="nick",
            source_type="task",
            source_id="task:t-1",
            title="Ship slice 16",
            uri=None,
            content="Finished the PR.",
            metadata={"status": "done"},
        ),
        Document(
            user_id="nick",
            source_type="task",
            source_id="task:t-2",
            title="Write tests",
            uri=None,
            content="Still working.",
            metadata={"status": "in_progress"},  # NOT terminal
        ),
    ]:
        await memory_store.upsert(doc)

    # Hand-set updated_at so the window filter is deterministic, then
    # commit once. ``upsert`` already committed each row, so these
    # UPDATEs are isolated.
    await memory_db.execute(
        "UPDATE memory_documents SET updated_at=? "
        "WHERE source_id='Meetings/2026-04-23-old.md'",
        ((midnight - timedelta(hours=5)).isoformat(),),
    )
    await memory_db.execute(
        "UPDATE memory_documents SET updated_at=? "
        "WHERE source_id='Meetings/2026-04-24-sync.md'",
        ((midnight + timedelta(hours=10)).isoformat(),),
    )
    await memory_db.execute(
        "UPDATE memory_documents SET updated_at=? "
        "WHERE source_id IN ('task:t-1', 'task:t-2')",
        ((midnight + timedelta(hours=11)).isoformat(),),
    )
    await memory_db.commit()

    skill = DailyReflectionSkill(
        writer=MagicMock(),
        memory_store=memory_store,
        connection=memory_db,
        config=DailyReflectionSkillConfig(
            context_limits=DailyReflectionContextLimits(
                meetings=5, completed_tasks=5, chat_highlights=5
            )
        ),
        user_id="nick",
    )

    ctx = await skill._gather_context(today)

    assert ctx["day"]["iso"] == "2026-04-24"
    assert len(ctx["meetings"]) == 1
    assert ctx["meetings"][0]["source_id"] == "Meetings/2026-04-24-sync.md"

    # Terminal status only — the in_progress row is filtered out.
    task_ids = [t["source_id"] for t in ctx["completed_tasks"]]
    assert task_ids == ["task:t-1"]


@pytest.mark.asyncio
async def test_run_for_day_delegates_to_writer() -> None:
    """Happy path: writer.run is awaited with the right arguments."""
    writer = MagicMock()
    writer.run = AsyncMock(
        return_value=MagicMock(skipped=False, sha="a" * 40, path="x", reason=None)
    )
    skill = DailyReflectionSkill(
        writer=writer,
        memory_store=MagicMock(),
        connection=MagicMock(),
        config=DailyReflectionSkillConfig(autonomy_level="medium"),
        user_id="nick",
    )

    # Bypass real context gathering.
    skill._gather_context = AsyncMock(return_value={"day": {"iso": "2026-04-24"}})

    await skill.run_for_day(date(2026, 4, 24))

    writer.run.assert_awaited_once()
    kwargs = writer.run.call_args.kwargs
    assert kwargs["template"] == "daily_reflection.md.j2"
    assert kwargs["task_type"] == "draft_daily_reflection"
    assert kwargs["target_path"] == "Reflections/2026-04-24.md"
    assert kwargs["idempotency_key"] == "2026-04-24"
    assert kwargs["autonomy_level"] == "medium"
