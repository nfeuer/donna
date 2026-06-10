from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.automations.reschedule import recompute_next_runs


def _auto(id_: str, trigger_type: str, schedule: str | None):
    a = MagicMock()
    a.id = id_
    a.trigger_type = trigger_type
    a.schedule = schedule
    return a


@pytest.mark.asyncio
async def test_recompute_only_scheduled_automations():
    repo = MagicMock()
    repo.list_all = AsyncMock(return_value=[
        _auto("a", "on_schedule", "0 9 * * *"),
        _auto("b", "manual", None),
        _auto("c", "on_schedule", None),  # no schedule -> skip
    ])
    repo.update_fields = AsyncMock()
    cron = MagicMock()
    cron.next_run.return_value = datetime(2026, 6, 10, 13, 0, tzinfo=UTC)
    now = datetime(2026, 6, 10, 7, 0, tzinfo=UTC)

    count = await recompute_next_runs(repo, cron, now)

    assert count == 1
    repo.update_fields.assert_awaited_once_with(
        "a", next_run_at=datetime(2026, 6, 10, 13, 0, tzinfo=UTC)
    )
    repo.list_all.assert_awaited_once_with(status="active", limit=1000)


@pytest.mark.asyncio
async def test_recompute_skips_invalid_cron_without_raising():
    repo = MagicMock()
    repo.list_all = AsyncMock(return_value=[
        _auto("a", "on_schedule", "not a cron"),
        _auto("b", "on_schedule", "0 9 * * *"),
    ])
    repo.update_fields = AsyncMock()
    cron = MagicMock()
    cron.next_run.side_effect = [ValueError("bad cron"), datetime(2026, 6, 10, 13, 0, tzinfo=UTC)]
    now = datetime(2026, 6, 10, 7, 0, tzinfo=UTC)

    count = await recompute_next_runs(repo, cron, now)

    assert count == 1  # only the valid one updated; invalid skipped, no raise
    repo.update_fields.assert_awaited_once_with(
        "b", next_run_at=datetime(2026, 6, 10, 13, 0, tzinfo=UTC)
    )
