"""Unit tests for ToolRequestRepository (slice 22)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import aiosqlite
import pytest

from donna.cost.tool_gap import (
    DETECTION_BOOT_CHECK,
    DETECTION_SCHEDULER,
    SEVERITY_HIGH,
    SEVERITY_SPECULATIVE,
    ToolGap,
)
from donna.cost.tool_request_repository import ToolRequestRepository

# Mirrors the alembic migration without dragging Alembic into unit tests.
_SCHEMA = """
CREATE TABLE tool_request (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    proposed_signature TEXT,
    rationale TEXT,
    blocking_capability_id TEXT,
    priority INTEGER NOT NULL DEFAULT 3,
    status TEXT NOT NULL DEFAULT 'open',
    severity TEXT NOT NULL DEFAULT 'speculative',
    detection_point TEXT,
    snoozed_until TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    resolved_branch TEXT,
    escalation_request_id INTEGER,
    last_pinged_at TEXT
);
CREATE UNIQUE INDEX ix_tool_request_open_user_tool
    ON tool_request(user_id, tool_name) WHERE status = 'open';
CREATE INDEX ix_tool_request_status_severity
    ON tool_request(status, severity);
CREATE INDEX ix_tool_request_blocking_capability
    ON tool_request(blocking_capability_id);
"""


def _high_gap(
    *,
    tool="web_fetch",
    user_id="nick",
    capability="news_check",
    rationale="cap blocked",
) -> ToolGap:
    return ToolGap(
        tool_name=tool,
        user_id=user_id,
        severity=SEVERITY_HIGH,
        blocking_capability_id=capability,
        rationale=rationale,
        proposed_signature=None,
        detection_point=DETECTION_SCHEDULER,
    )


def _spec_gap(*, tool="web_fetch", user_id="nick") -> ToolGap:
    return ToolGap(
        tool_name=tool,
        user_id=user_id,
        severity=SEVERITY_SPECULATIVE,
        blocking_capability_id=None,
        rationale="proposed by skill draft",
        proposed_signature={"name": tool, "params": []},
        detection_point=DETECTION_BOOT_CHECK,
    )


@pytest.fixture
async def conn():
    async with aiosqlite.connect(":memory:") as c:
        await c.executescript(_SCHEMA)
        await c.commit()
        yield c


async def test_record_inserts_fresh_row(conn):
    repo = ToolRequestRepository(conn)
    result = await repo.record(_high_gap())
    assert result.is_new is True
    assert result.row.tool_name == "web_fetch"
    assert result.row.severity == "high"
    assert result.row.status == "open"
    assert result.row.priority == 3


async def test_record_dedups_open_row_and_bumps_priority(conn):
    repo = ToolRequestRepository(conn)
    first = await repo.record(_high_gap())
    second = await repo.record(
        ToolGap(
            tool_name="web_fetch",
            user_id="nick",
            severity=SEVERITY_HIGH,
            blocking_capability_id="news_check",
            rationale="updated rationale",
            proposed_signature=None,
            detection_point=DETECTION_SCHEDULER,
            priority=5,
        )
    )
    assert second.is_new is False
    assert second.row.id == first.row.id
    assert second.row.priority == 5  # max(3, 5)
    assert second.row.rationale == "updated rationale"


async def test_record_promotes_severity_speculative_to_high(conn):
    repo = ToolRequestRepository(conn)
    await repo.record(_spec_gap())
    res = await repo.record(_high_gap())
    assert res.is_new is False
    assert res.row.severity == "high"


async def test_resolved_row_allows_new_emission(conn):
    repo = ToolRequestRepository(conn)
    first = await repo.record(_high_gap())
    await repo.mark_completed(first.row.id, branch_name="b/branch-1")
    second = await repo.record(_high_gap())
    assert second.is_new is True
    assert second.row.id != first.row.id


async def test_snooze_sets_until_only_for_open_row(conn):
    repo = ToolRequestRepository(conn)
    res = await repo.record(_spec_gap())
    base = datetime.now(tz=UTC)
    ok = await repo.snooze(res.row.id, seconds=3600, now=base)
    assert ok is True
    refreshed = await repo.get(res.row.id)
    assert refreshed.snoozed_until is not None
    delta = refreshed.snoozed_until - base
    assert abs(delta - timedelta(seconds=3600)) < timedelta(seconds=2)

    await repo.mark_rejected(res.row.id)
    again = await repo.snooze(res.row.id, seconds=60)
    assert again is False  # already terminal


async def test_mark_in_progress_links_escalation(conn):
    repo = ToolRequestRepository(conn)
    res = await repo.record(_high_gap())
    ok = await repo.mark_in_progress(res.row.id, escalation_request_id=42)
    assert ok is True
    refreshed = await repo.get(res.row.id)
    assert refreshed.status == "in_progress"
    assert refreshed.escalation_request_id == 42


async def test_mark_completed_records_branch_from_in_progress(conn):
    repo = ToolRequestRepository(conn)
    res = await repo.record(_high_gap())
    await repo.mark_in_progress(res.row.id, escalation_request_id=99)
    ok = await repo.mark_completed(res.row.id, branch_name="escalation/abcd-tool")
    assert ok is True
    refreshed = await repo.get(res.row.id)
    assert refreshed.status == "completed"
    assert refreshed.resolved_branch == "escalation/abcd-tool"
    assert refreshed.resolved_at is not None


async def test_list_open_speculative_filters_severity_status_and_snooze(conn):
    repo = ToolRequestRepository(conn)
    spec_open = await repo.record(_spec_gap(tool="alpha"))
    high_open = await repo.record(_high_gap(tool="beta"))
    spec_snoozed = await repo.record(_spec_gap(tool="gamma"))
    spec_resolved = await repo.record(_spec_gap(tool="delta"))

    base = datetime.now(tz=UTC)
    await repo.snooze(spec_snoozed.row.id, seconds=3600, now=base)
    await repo.mark_completed(spec_resolved.row.id, branch_name="b/x")

    rows = await repo.list_open_speculative(now=base)
    names = {r.tool_name for r in rows}
    assert names == {"alpha"}
    # high is excluded; snoozed is excluded; resolved is excluded.
    assert spec_open.row.tool_name == "alpha"
    assert high_open.row.tool_name == "beta"


async def test_list_open_speculative_includes_expired_snooze(conn):
    repo = ToolRequestRepository(conn)
    res = await repo.record(_spec_gap(tool="alpha"))
    past = datetime.now(tz=UTC) - timedelta(hours=1)
    # Manually set snoozed_until to past so list returns the row.
    await conn.execute(
        "UPDATE tool_request SET snoozed_until = ? WHERE id = ?",
        (past.isoformat(), res.row.id),
    )
    await conn.commit()
    rows = await repo.list_open_speculative()
    assert any(r.id == res.row.id for r in rows)


async def test_mark_pinged_stamps_last_pinged_at(conn):
    repo = ToolRequestRepository(conn)
    res = await repo.record(_high_gap())
    assert res.row.last_pinged_at is None
    await repo.mark_pinged(res.row.id)
    refreshed = await repo.get(res.row.id)
    assert refreshed.last_pinged_at is not None
