"""Verify advance_schedule applies status and failure_count overrides atomically."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from donna.automations.repository import AutomationRepository


@pytest.fixture
def repo() -> AutomationRepository:
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.commit = AsyncMock()
    return AutomationRepository(conn)


@pytest.mark.asyncio
async def test_advance_schedule_without_overrides(repo: AutomationRepository) -> None:
    """Baseline: overrides not passed, SQL has no status/failure_count clause."""
    now = datetime.now(UTC)
    await repo.advance_schedule(
        automation_id="a1",
        last_run_at=now,
        next_run_at=now,
        increment_run_count=True,
        increment_failure_count=False,
    )
    sql = repo._conn.execute.call_args[0][0]
    assert "status" not in sql
    assert repo._conn.commit.await_count == 1


@pytest.mark.asyncio
async def test_advance_schedule_with_overrides(repo: AutomationRepository) -> None:
    """When overrides provided, SQL includes status and failure_count clauses."""
    now = datetime.now(UTC)
    await repo.advance_schedule(
        automation_id="a1",
        last_run_at=now,
        next_run_at=now,
        increment_run_count=True,
        increment_failure_count=False,
        status_override="active",
        failure_count_override=0,
    )
    sql = repo._conn.execute.call_args[0][0]
    params = repo._conn.execute.call_args[0][1]
    assert "status = ?" in sql
    assert "failure_count = ?" in sql
    assert "active" in params
    assert 0 in params
    assert repo._conn.commit.await_count == 1
