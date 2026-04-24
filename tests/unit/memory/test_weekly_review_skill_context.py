"""Slice 16 — :class:`WeeklyReviewSkill` context + ISO-week computation."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from donna.capabilities.weekly_review_skill import (
    WeeklyReviewSkill,
    _iso_week_range,
    _week_start_for,
)
from donna.config import WeeklyReviewContextLimits, WeeklyReviewSkillConfig
from donna.integrations.vault import VaultReadError
from donna.memory.store import Document, MemoryStore


def test_week_start_for_normalises_to_monday() -> None:
    # Friday 2026-04-24 → Monday 2026-04-20.
    assert _week_start_for(date(2026, 4, 24)) == date(2026, 4, 20)
    # Already Monday.
    assert _week_start_for(date(2026, 4, 20)) == date(2026, 4, 20)
    # Sunday → previous Monday (ISO weeks are Mon-Sun).
    assert _week_start_for(date(2026, 4, 26)) == date(2026, 4, 20)


def test_iso_week_range_labels_and_end_exclusive() -> None:
    end, label = _iso_week_range(date(2026, 4, 20))
    assert label == "2026-W17"
    assert end == date(2026, 4, 27)


@pytest.mark.asyncio
async def test_gather_context_windowed_to_week(
    memory_db: aiosqlite.Connection,
    memory_store: MemoryStore,
) -> None:
    week_start = date(2026, 4, 20)  # Monday
    midnight = datetime.combine(week_start, datetime.min.time(), tzinfo=UTC).replace(
        tzinfo=None
    )

    for doc in [
        Document(
            user_id="nick",
            source_type="vault",
            source_id="Meetings/2026-04-22-sync.md",
            title="Mid-week sync",
            uri=None,
            content="# Sync\n",
            metadata={"type": "meeting"},
        ),
        Document(
            user_id="nick",
            source_type="vault",
            source_id="Meetings/2026-04-13-old.md",
            title="Previous week",
            uri=None,
            content="# Old\n",
            metadata={"type": "meeting"},
        ),
        Document(
            user_id="nick",
            source_type="vault",
            source_id="Commitments/2026-04-22.md",
            title="Wed commitments",
            uri=None,
            content="# Commitments\n",
            metadata={"type": "commitment_log"},
        ),
    ]:
        await memory_store.upsert(doc)

    await memory_db.execute(
        "UPDATE memory_documents SET updated_at=? "
        "WHERE source_id='Meetings/2026-04-22-sync.md'",
        ((midnight + timedelta(days=2, hours=12)).isoformat(),),
    )
    await memory_db.execute(
        "UPDATE memory_documents SET updated_at=? "
        "WHERE source_id='Meetings/2026-04-13-old.md'",
        ((midnight - timedelta(days=5)).isoformat(),),
    )
    await memory_db.execute(
        "UPDATE memory_documents SET updated_at=? "
        "WHERE source_id='Commitments/2026-04-22.md'",
        ((midnight + timedelta(days=2, hours=20)).isoformat(),),
    )
    await memory_db.commit()

    vault_client = MagicMock()
    vault_client.read = AsyncMock(side_effect=VaultReadError("missing: no prior"))

    skill = WeeklyReviewSkill(
        writer=MagicMock(),
        memory_store=memory_store,
        vault_client=vault_client,
        connection=memory_db,
        config=WeeklyReviewSkillConfig(
            context_limits=WeeklyReviewContextLimits(
                completed_tasks=5, meetings=5, commitments=5, chat_highlights=5
            )
        ),
        user_id="nick",
    )

    ctx = await skill._gather_context(week_start)

    assert ctx["week"]["iso"] == "2026-W17"
    assert ctx["week"]["start"] == "2026-04-20"
    assert ctx["week"]["end"] == "2026-04-26"
    assert len(ctx["meetings"]) == 1
    assert ctx["meetings"][0]["source_id"] == "Meetings/2026-04-22-sync.md"
    assert len(ctx["commitments"]) == 1
    assert ctx["prior_review"] is None


@pytest.mark.asyncio
async def test_run_for_week_delegates_to_writer() -> None:
    writer = MagicMock()
    writer.run = AsyncMock(
        return_value=MagicMock(skipped=False, sha="a" * 40, path="x", reason=None)
    )
    skill = WeeklyReviewSkill(
        writer=writer,
        memory_store=MagicMock(),
        vault_client=MagicMock(),
        connection=MagicMock(),
        config=WeeklyReviewSkillConfig(autonomy_level="medium"),
        user_id="nick",
    )
    skill._gather_context = AsyncMock(return_value={"week": {"iso": "2026-W17"}})

    # Pass in a Friday — skill should normalise to Monday for ISO week.
    await skill.run_for_week(date(2026, 4, 24))

    kwargs = writer.run.call_args.kwargs
    assert kwargs["template"] == "weekly_review.md.j2"
    assert kwargs["task_type"] == "draft_weekly_review"
    assert kwargs["target_path"] == "WeeklyReview/2026-W17.md"
    assert kwargs["idempotency_key"] == "2026-W17"
