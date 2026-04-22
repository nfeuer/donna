"""Tests for ManualDraftPoller (F-W1-D)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
from alembic.config import Config

from alembic import command


@pytest.mark.asyncio
async def test_poller_picks_up_and_clears_manual_draft_at(tmp_path):
    from donna.skills.manual_draft_poller import ManualDraftPoller

    db = tmp_path / "t.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    command.upgrade(cfg, "head")

    async with aiosqlite.connect(db) as conn:
        now = datetime.now(UTC).isoformat()
        cand_id = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO skill_candidate_report (id, capability_name, "
            "expected_savings_usd, volume_30d, variance_score, status, "
            "reported_at, manual_draft_at) "
            "VALUES (?, 'parse_task', 5.0, 100, 0.1, 'new', ?, ?)",
            (cand_id, now, now),
        )
        await conn.commit()

        auto_drafter = MagicMock()
        auto_drafter.draft_one = AsyncMock(
            return_value=MagicMock(candidate_id=cand_id, outcome="succeeded"),
        )
        candidate_repo = MagicMock()
        candidate_repo.get = AsyncMock(return_value=MagicMock(id=cand_id))

        poller = ManualDraftPoller(
            connection=conn,
            auto_drafter=auto_drafter,
            candidate_repo=candidate_repo,
        )
        picked = await poller.run_once()
        assert picked == 1
        auto_drafter.draft_one.assert_called_once()

        cursor = await conn.execute(
            "SELECT manual_draft_at FROM skill_candidate_report WHERE id = ?",
            (cand_id,),
        )
        row = await cursor.fetchone()
        assert row[0] is None


@pytest.mark.asyncio
async def test_poller_skips_non_new_status(tmp_path):
    from donna.skills.manual_draft_poller import ManualDraftPoller

    db = tmp_path / "t2.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    command.upgrade(cfg, "head")

    async with aiosqlite.connect(db) as conn:
        now = datetime.now(UTC).isoformat()
        cand_id = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO skill_candidate_report (id, capability_name, "
            "expected_savings_usd, volume_30d, variance_score, status, "
            "reported_at, manual_draft_at) "
            "VALUES (?, 'parse_task', 5.0, 100, 0.1, 'drafted', ?, ?)",
            (cand_id, now, now),
        )
        await conn.commit()

        poller = ManualDraftPoller(
            connection=conn,
            auto_drafter=MagicMock(draft_one=AsyncMock()),
            candidate_repo=MagicMock(get=AsyncMock()),
        )
        assert await poller.run_once() == 0


@pytest.mark.asyncio
async def test_poller_clears_column_even_on_draft_failure(tmp_path):
    from donna.skills.manual_draft_poller import ManualDraftPoller

    db = tmp_path / "t3.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    command.upgrade(cfg, "head")

    async with aiosqlite.connect(db) as conn:
        now = datetime.now(UTC).isoformat()
        cand_id = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO skill_candidate_report (id, capability_name, "
            "expected_savings_usd, volume_30d, variance_score, status, "
            "reported_at, manual_draft_at) "
            "VALUES (?, 'parse_task', 5.0, 100, 0.1, 'new', ?, ?)",
            (cand_id, now, now),
        )
        await conn.commit()

        auto_drafter = MagicMock()
        auto_drafter.draft_one = AsyncMock(side_effect=RuntimeError("boom"))
        candidate_repo = MagicMock()
        candidate_repo.get = AsyncMock(return_value=MagicMock(id=cand_id))

        poller = ManualDraftPoller(
            connection=conn,
            auto_drafter=auto_drafter,
            candidate_repo=candidate_repo,
        )
        picked = await poller.run_once()
        assert picked == 1  # counts as handled even on failure

        cursor = await conn.execute(
            "SELECT manual_draft_at FROM skill_candidate_report WHERE id = ?",
            (cand_id,),
        )
        row = await cursor.fetchone()
        assert row[0] is None  # cleared to prevent infinite retry
