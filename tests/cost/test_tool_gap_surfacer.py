"""Unit tests for ToolGapSurfacer (slice 22)."""

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
from donna.cost.tool_gap_surfacer import ToolGapSurfacer
from donna.cost.tool_request_repository import ToolRequestRepository

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
CREATE TABLE invocation_log (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    task_type TEXT NOT NULL,
    task_id TEXT,
    model_alias TEXT NOT NULL,
    model_actual TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    latency_ms INTEGER NOT NULL,
    tokens_in INTEGER NOT NULL,
    tokens_out INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    output TEXT,
    is_shadow INTEGER NOT NULL DEFAULT 0,
    spot_check_queued INTEGER NOT NULL DEFAULT 0,
    user_id TEXT,
    escalation_request_id INTEGER
);
"""


@pytest.fixture
async def conn():
    async with aiosqlite.connect(":memory:") as c:
        await c.executescript(_SCHEMA)
        await c.commit()
        yield c


def _high() -> ToolGap:
    return ToolGap(
        tool_name="web_fetch",
        user_id="nick",
        severity=SEVERITY_HIGH,
        blocking_capability_id="news_check",
        rationale="cap blocked",
        proposed_signature=None,
        detection_point=DETECTION_SCHEDULER,
    )


def _spec() -> ToolGap:
    return ToolGap(
        tool_name="web_fetch",
        user_id="nick",
        severity=SEVERITY_SPECULATIVE,
        blocking_capability_id=None,
        rationale="proposed in skill draft",
        proposed_signature=None,
        detection_point=DETECTION_BOOT_CHECK,
    )


async def _audit_events(conn) -> list[str]:
    cursor = await conn.execute(
        "SELECT output FROM invocation_log WHERE task_type = 'tool_gap_lifecycle'"
    )
    rows = await cursor.fetchall()
    import json as _json
    return [_json.loads(r[0])["event"] for r in rows]


async def test_speculative_records_row_audits_no_ping(conn):
    repo = ToolRequestRepository(conn)
    posted: list[int] = []

    async def fake_poster(row):
        posted.append(row.id)
        return True

    surfacer = ToolGapSurfacer(repository=repo, conn=conn, ping_poster=fake_poster)
    row = await surfacer.surface(_spec())

    assert row.severity == "speculative"
    assert posted == []  # no ping for speculative
    events = await _audit_events(conn)
    assert events == ["tool_gap_detected"]


async def test_high_records_row_audits_and_pings(conn):
    repo = ToolRequestRepository(conn)
    posted: list[int] = []

    async def fake_poster(row):
        posted.append(row.id)
        return True

    surfacer = ToolGapSurfacer(repository=repo, conn=conn, ping_poster=fake_poster)
    row = await surfacer.surface(_high())

    assert row.severity == "high"
    assert posted == [row.id]
    events = await _audit_events(conn)
    assert "tool_gap_detected" in events
    assert "tool_request_filed" in events


async def test_high_dedup_within_cooldown_suppresses_reping(conn):
    repo = ToolRequestRepository(conn)
    posted: list[int] = []

    async def fake_poster(row):
        posted.append(row.id)
        return True

    surfacer = ToolGapSurfacer(
        repository=repo, conn=conn, ping_poster=fake_poster,
        reping_cooldown_seconds=3600,
    )
    base = datetime.now(tz=UTC)
    await surfacer.surface(_high(), now=base)
    await surfacer.surface(_high(), now=base + timedelta(minutes=10))
    assert posted == [posted[0]]  # only one ping despite two surfaces


async def test_high_reping_after_cooldown(conn):
    repo = ToolRequestRepository(conn)
    posted: list[int] = []

    async def fake_poster(row):
        posted.append(row.id)
        return True

    surfacer = ToolGapSurfacer(
        repository=repo, conn=conn, ping_poster=fake_poster,
        reping_cooldown_seconds=60,
    )
    base = datetime.now(tz=UTC)
    await surfacer.surface(_high(), now=base)
    await surfacer.surface(_high(), now=base + timedelta(minutes=5))
    assert len(posted) == 2


async def test_high_no_poster_records_row_only(conn):
    repo = ToolRequestRepository(conn)
    surfacer = ToolGapSurfacer(repository=repo, conn=conn, ping_poster=None)
    row = await surfacer.surface(_high())
    assert row.severity == "high"
    events = await _audit_events(conn)
    assert events == ["tool_gap_detected"]


async def test_poster_failure_does_not_raise(conn):
    repo = ToolRequestRepository(conn)

    async def bad_poster(row):
        raise RuntimeError("discord down")

    surfacer = ToolGapSurfacer(repository=repo, conn=conn, ping_poster=bad_poster)
    row = await surfacer.surface(_high())
    assert row.severity == "high"
    # Audit row exists; no tool_request_filed because the post failed.
    events = await _audit_events(conn)
    assert events == ["tool_gap_detected"]
