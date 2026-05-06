"""Tests for MorningDigest tool-gap aggregation (slice 22)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

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
from donna.notifications.digest import MorningDigest


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
"""


@pytest.fixture
async def conn():
    async with aiosqlite.connect(":memory:") as c:
        await c.executescript(_SCHEMA)
        await c.commit()
        yield c


def _make_digest(repo) -> MorningDigest:
    """Construct a MorningDigest with bare deps; only _assemble_data path is exercised."""
    db = MagicMock()
    db.list_tasks = AsyncMock(return_value=[])
    cur = AsyncMock()
    cur.fetchone = AsyncMock(return_value=(0,))
    db.connection = MagicMock()
    db.connection.execute = AsyncMock(return_value=cur)

    service = MagicMock()
    router = MagicMock()
    router._models_config = MagicMock()
    router._models_config.cost.monthly_budget_usd = 100.0
    cal = MagicMock()
    cal.list_events = AsyncMock(return_value=[])
    return MorningDigest(
        db=db, service=service, router=router,
        calendar_client=cal, calendar_id="cal", user_id="nick",
        project_root=MagicMock(),
        tool_request_repo=repo,
    )


@pytest.mark.asyncio
async def test_digest_includes_open_speculative_gaps(conn):
    repo = ToolRequestRepository(conn)
    await repo.record(
        ToolGap(
            tool_name="web_fetch",
            user_id="nick",
            severity=SEVERITY_SPECULATIVE,
            blocking_capability_id="news_check",
            rationale="proposed",
            proposed_signature=None,
            detection_point=DETECTION_BOOT_CHECK,
        )
    )
    digest = _make_digest(repo)
    data = await digest._assemble_data(datetime.now(tz=UTC))
    assert "web_fetch" in data["tool_gaps"]


@pytest.mark.asyncio
async def test_digest_excludes_high_severity(conn):
    repo = ToolRequestRepository(conn)
    await repo.record(
        ToolGap(
            tool_name="alpha_high",
            user_id="nick",
            severity=SEVERITY_HIGH,
            blocking_capability_id="news_check",
            rationale="cap blocked",
            proposed_signature=None,
            detection_point=DETECTION_SCHEDULER,
        )
    )
    digest = _make_digest(repo)
    data = await digest._assemble_data(datetime.now(tz=UTC))
    assert data["tool_gaps"] == "None."


@pytest.mark.asyncio
async def test_digest_excludes_snoozed(conn):
    repo = ToolRequestRepository(conn)
    res = await repo.record(
        ToolGap(
            tool_name="snoozed_tool",
            user_id="nick",
            severity=SEVERITY_SPECULATIVE,
            blocking_capability_id=None,
            rationale="x",
            proposed_signature=None,
            detection_point=DETECTION_BOOT_CHECK,
        )
    )
    base = datetime.now(tz=UTC)
    await repo.snooze(res.row.id, seconds=86400, now=base)
    digest = _make_digest(repo)
    data = await digest._assemble_data(base)
    assert "snoozed_tool" not in data["tool_gaps"]


@pytest.mark.asyncio
async def test_digest_excludes_resolved(conn):
    repo = ToolRequestRepository(conn)
    res = await repo.record(
        ToolGap(
            tool_name="resolved_tool",
            user_id="nick",
            severity=SEVERITY_SPECULATIVE,
            blocking_capability_id=None,
            rationale="x",
            proposed_signature=None,
            detection_point=DETECTION_BOOT_CHECK,
        )
    )
    await repo.mark_completed(res.row.id, branch_name="b/x")
    digest = _make_digest(repo)
    data = await digest._assemble_data(datetime.now(tz=UTC))
    assert "resolved_tool" not in data["tool_gaps"]


@pytest.mark.asyncio
async def test_digest_includes_expired_snooze(conn):
    repo = ToolRequestRepository(conn)
    res = await repo.record(
        ToolGap(
            tool_name="expired_tool",
            user_id="nick",
            severity=SEVERITY_SPECULATIVE,
            blocking_capability_id=None,
            rationale="x",
            proposed_signature=None,
            detection_point=DETECTION_BOOT_CHECK,
        )
    )
    past = datetime.now(tz=UTC) - timedelta(hours=1)
    await conn.execute(
        "UPDATE tool_request SET snoozed_until = ? WHERE id = ?",
        (past.isoformat(), res.row.id),
    )
    await conn.commit()
    digest = _make_digest(repo)
    data = await digest._assemble_data(datetime.now(tz=UTC))
    assert "expired_tool" in data["tool_gaps"]


@pytest.mark.asyncio
async def test_digest_no_repo_returns_none_string(conn):
    digest = _make_digest(repo=None)
    data = await digest._assemble_data(datetime.now(tz=UTC))
    assert data["tool_gaps"] == "None."
