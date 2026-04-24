"""Slice 16 — :class:`CommitmentLogSkill` context gathering + delegation."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from donna.capabilities.commitment_log_skill import CommitmentLogSkill
from donna.config import CommitmentLogContextLimits, CommitmentLogSkillConfig
from donna.memory.store import Document, MemoryStore


@pytest.mark.asyncio
async def test_context_gathers_only_today(
    memory_db: aiosqlite.Connection,
    memory_store: MemoryStore,
) -> None:
    today = date(2026, 4, 24)
    midnight = datetime(2026, 4, 24, tzinfo=UTC).replace(tzinfo=None)

    for doc in [
        Document(
            user_id="nick",
            source_type="chat",
            source_id="session-1:msg-10-msg-11",
            title="t",
            uri=None,
            content="I'll send the Alice deck by Friday.",
        ),
        Document(
            user_id="nick",
            source_type="chat",
            source_id="session-1:old",
            title="old chat",
            uri=None,
            content="yesterday's noise",
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
    ]:
        await memory_store.upsert(doc)

    await memory_db.execute(
        "UPDATE memory_documents SET updated_at=? "
        "WHERE source_id IN ('session-1:msg-10-msg-11', 'task:t-1')",
        ((midnight + timedelta(hours=14)).isoformat(),),
    )
    await memory_db.execute(
        "UPDATE memory_documents SET updated_at=? "
        "WHERE source_id='session-1:old'",
        ((midnight - timedelta(hours=6)).isoformat(),),
    )
    await memory_db.commit()

    skill = CommitmentLogSkill(
        writer=MagicMock(),
        memory_store=memory_store,
        connection=memory_db,
        config=CommitmentLogSkillConfig(
            context_limits=CommitmentLogContextLimits(chat_hits=20, task_hits=20)
        ),
        user_id="nick",
    )

    ctx = await skill._gather_context(today)

    assert ctx["day"]["iso"] == "2026-04-24"
    assert len(ctx["chat_signals"]) == 1
    assert ctx["chat_signals"][0]["source_id"] == "session-1:msg-10-msg-11"
    assert len(ctx["task_signals"]) == 1


@pytest.mark.asyncio
async def test_run_for_day_delegates_to_writer() -> None:
    writer = MagicMock()
    writer.run = AsyncMock(
        return_value=MagicMock(skipped=False, sha="a" * 40, path="x", reason=None)
    )
    skill = CommitmentLogSkill(
        writer=writer,
        memory_store=MagicMock(),
        connection=MagicMock(),
        config=CommitmentLogSkillConfig(autonomy_level="medium"),
        user_id="nick",
    )
    skill._gather_context = AsyncMock(return_value={"day": {"iso": "2026-04-24"}})

    await skill.run_for_day(date(2026, 4, 24))

    kwargs = writer.run.call_args.kwargs
    assert kwargs["template"] == "commitment_log.md.j2"
    assert kwargs["task_type"] == "extract_commitments"
    assert kwargs["target_path"] == "Commitments/2026-04-24.md"
    assert kwargs["idempotency_key"] == "2026-04-24"
