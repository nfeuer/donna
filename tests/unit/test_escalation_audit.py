"""Unit tests for escalation_audit.write_escalation_event."""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pytest

from donna.cost.escalation_audit import (
    ESCALATION_TASK_TYPE,
    EVENT_OFFERED,
    EVENT_RESOLVED,
    write_escalation_event,
)

_SCHEMA = """
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
    quality_score REAL,
    is_shadow INTEGER DEFAULT 0,
    eval_session_id TEXT,
    spot_check_queued INTEGER DEFAULT 0,
    user_id TEXT NOT NULL,
    skill_id TEXT,
    escalation_request_id INTEGER
);
"""


@pytest.fixture
async def conn(tmp_path: Path):
    c = await aiosqlite.connect(str(tmp_path / "audit.db"))
    await c.executescript(_SCHEMA)
    await c.commit()
    yield c
    await c.close()


async def test_offered_row_shape(conn: aiosqlite.Connection) -> None:
    new_id = await write_escalation_event(
        conn,
        event=EVENT_OFFERED,
        escalation_request_id=42,
        correlation_id="corr-abc-1234567890",
        user_id="nick",
        task_id="t1",
        payload={
            "task_type": "skill_draft",
            "estimate_usd": 7.5,
            "daily_remaining_usd": 2.0,
            "modes": ["pause", "cancel"],
        },
    )
    row = await (
        await conn.execute(
            "SELECT task_type, model_alias, cost_usd, "
            "escalation_request_id, output, user_id, task_id, input_hash "
            "FROM invocation_log WHERE id = ?",
            (new_id,),
        )
    ).fetchone()
    assert row is not None
    assert row[0] == ESCALATION_TASK_TYPE
    assert row[1] == "audit"
    assert row[2] == 0.0
    assert row[3] == 42
    body = json.loads(row[4])
    assert body["event"] == EVENT_OFFERED
    assert body["modes"] == ["pause", "cancel"]
    assert row[5] == "nick"
    assert row[6] == "t1"
    # input_hash carries the truncated correlation_id.
    assert row[7] == "corr-abc-1234567"


async def test_resolved_row_carries_event(conn: aiosqlite.Connection) -> None:
    await write_escalation_event(
        conn,
        event=EVENT_RESOLVED,
        escalation_request_id=1,
        correlation_id="c1",
        user_id="nick",
        task_id=None,
        payload={"mode": "pause", "resolved_by": "user"},
    )
    cursor = await conn.execute(
        "SELECT output FROM invocation_log WHERE escalation_request_id = ?",
        (1,),
    )
    row = await cursor.fetchone()
    assert row is not None
    body = json.loads(row[0])
    assert body == {
        "event": EVENT_RESOLVED,
        "mode": "pause",
        "resolved_by": "user",
    }
