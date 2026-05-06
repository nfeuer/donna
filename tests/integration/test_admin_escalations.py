"""Integration tests for the slice 19 escalation workspace endpoints.

Spins up a real aiosqlite connection with the slice 17 schema +
slice 19 column additions, mounts the FastAPI router with the admin
auth dependency stubbed, and exercises list / detail / submit through
``httpx.ASGITransport``. This avoids the brittleness of fully mocking
``conn.execute`` for every query in a single endpoint.
"""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from donna.api.auth.router_factory import _admin_dep
from donna.api.routes import admin_escalations
from donna.cost.escalation_audit import write_escalation_event

# Self-contained schema: slice 17 columns + slice 19 additions.
# Mirrors c7d8e9f0a1b2 + d8e9f0a1b2c3 without invoking Alembic so the
# tests run fast in unit/integration modes alike.
_SCHEMA = """
CREATE TABLE escalation_request (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL UNIQUE,
    task_id TEXT,
    task_type TEXT NOT NULL,
    estimate_usd REAL NOT NULL,
    daily_remaining_usd REAL NOT NULL,
    offered_modes TEXT NOT NULL,
    resolution TEXT,
    resolved_by TEXT,
    resolved_at TEXT,
    prompt_path TEXT,
    prompt_body TEXT,
    summary TEXT,
    mode TEXT,
    result TEXT,
    validation_result TEXT,
    branch_name TEXT,
    iteration INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    submitted_at TEXT,
    validated_at TEXT,
    priority INTEGER NOT NULL DEFAULT 2,
    delivery_status TEXT,
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    last_delivery_attempt_at TEXT,
    parent_escalation_id INTEGER REFERENCES escalation_request(id)
);

CREATE TABLE invocation_log (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    task_type TEXT NOT NULL,
    task_id TEXT,
    model_alias TEXT NOT NULL,
    model_actual TEXT NOT NULL,
    input_hash TEXT,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    output TEXT,
    is_shadow INTEGER NOT NULL DEFAULT 0,
    spot_check_queued INTEGER NOT NULL DEFAULT 0,
    user_id TEXT,
    escalation_request_id INTEGER REFERENCES escalation_request(id)
);
"""


async def _make_row(
    conn: aiosqlite.Connection,
    *,
    correlation_id: str,
    user_id: str = "nick",
    task_type: str = "skill_draft",
    status: str = "resolved",
    mode: str | None = "claude_code",
    prompt_body: str | None = "Hello from the prompt body.",
    summary: str | None = "Build the foo skill",
    estimate_usd: float = 7.5,
    daily_remaining_usd: float = 0.0,
    offered_modes: list[str] | None = None,
    iteration: int = 1,
) -> int:
    """Insert one escalation_request row and return its id."""
    offered = json.dumps(offered_modes or ["api_extended", "claude_code", "pause", "cancel"])
    cur = await conn.execute(
        """
        INSERT INTO escalation_request (
            user_id, correlation_id, task_id, task_type,
            estimate_usd, daily_remaining_usd, offered_modes,
            resolution, resolved_by, resolved_at,
            prompt_body, summary, mode,
            iteration, status, created_at, priority,
            delivery_status, delivery_attempts
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            correlation_id,
            "task-1",
            task_type,
            estimate_usd,
            daily_remaining_usd,
            offered,
            mode if status != "open" else None,
            "user" if status != "open" else None,
            "2026-05-06T01:00:00+00:00" if status != "open" else None,
            prompt_body,
            summary,
            mode,
            iteration,
            status,
            "2026-05-06T00:00:00+00:00",
            2,
            "sent",
            1,
        ),
    )
    await conn.commit()
    new_id = cur.lastrowid
    assert new_id is not None
    return int(new_id)


@pytest.fixture
async def app_and_conn(tmp_path: Path):
    db_path = tmp_path / "esc_workspace.db"
    conn = await aiosqlite.connect(str(db_path))
    await conn.executescript(_SCHEMA)
    await conn.commit()

    app = FastAPI()
    app.state.db = type("DB", (), {"connection": conn})()
    app.include_router(admin_escalations.router, prefix="/admin")
    app.dependency_overrides[_admin_dep] = lambda: "admin"

    yield app, conn

    await conn.close()


@pytest.fixture
async def client(app_and_conn):
    app, _conn = app_and_conn
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestList:
    async def test_empty(self, client: AsyncClient) -> None:
        r = await client.get("/admin/escalations")
        assert r.status_code == 200
        body = r.json()
        assert body["items"] == []
        assert body["total"] == 0
        assert body["status_counts"] == {}

    async def test_lists_and_counts_by_status(
        self, app_and_conn, client: AsyncClient
    ) -> None:
        _app, conn = app_and_conn
        await _make_row(conn, correlation_id="a", status="open", mode=None)
        await _make_row(conn, correlation_id="b", status="resolved", mode="chat")
        await _make_row(conn, correlation_id="c", status="submitted", mode="claude_code")

        r = await client.get("/admin/escalations")
        body = r.json()
        assert r.status_code == 200
        assert body["total"] == 3
        assert body["status_counts"] == {"open": 1, "resolved": 1, "submitted": 1}
        # open rows render first per ORDER BY in the route.
        assert body["items"][0]["correlation_id"] == "a"

    async def test_status_filter(self, app_and_conn, client: AsyncClient) -> None:
        _app, conn = app_and_conn
        await _make_row(conn, correlation_id="x", status="resolved", mode="chat")
        await _make_row(conn, correlation_id="y", status="submitted", mode="chat")

        r = await client.get("/admin/escalations", params={"status": "submitted"})
        body = r.json()
        assert r.status_code == 200
        assert [i["correlation_id"] for i in body["items"]] == ["y"]

    async def test_invalid_status_400(self, client: AsyncClient) -> None:
        r = await client.get("/admin/escalations", params={"status": "bogus"})
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invalid_status"


class TestDetail:
    async def test_includes_prompt_body_and_timeline(
        self, app_and_conn, client: AsyncClient
    ) -> None:
        _app, conn = app_and_conn
        rid = await _make_row(
            conn,
            correlation_id="det-1",
            status="resolved",
            mode="chat",
            prompt_body="Full prompt body content.",
        )
        await write_escalation_event(
            conn,
            event="escalation_offered",
            escalation_request_id=rid,
            correlation_id="det-1",
            user_id="nick",
            task_id="task-1",
            payload={"offered": ["chat", "pause"]},
        )
        await write_escalation_event(
            conn,
            event="escalation_resolved",
            escalation_request_id=rid,
            correlation_id="det-1",
            user_id="nick",
            task_id="task-1",
            payload={"mode": "chat", "resolved_by": "user"},
        )

        r = await client.get("/admin/escalations/det-1")
        body = r.json()
        assert r.status_code == 200
        assert body["escalation"]["correlation_id"] == "det-1"
        assert body["escalation"]["prompt_body"] == "Full prompt body content."
        assert body["escalation"]["mode"] == "chat"
        events = [e["event"] for e in body["timeline"]]
        assert events == ["escalation_offered", "escalation_resolved"]

    async def test_404_when_missing(self, client: AsyncClient) -> None:
        r = await client.get("/admin/escalations/does-not-exist")
        assert r.status_code == 404
        assert r.json()["detail"]["error"] == "not_found"


class TestSubmit:
    async def test_chat_submission_writes_result(
        self, app_and_conn, client: AsyncClient
    ) -> None:
        _app, conn = app_and_conn
        await _make_row(
            conn,
            correlation_id="sub-1",
            status="resolved",
            mode="chat",
            task_type="chat_escalation",
        )

        answer = "x" * 60
        r = await client.post(
            "/admin/escalations/sub-1/submit",
            json={"mode": "chat", "answer": answer},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "submitted"

        cur = await conn.execute(
            "SELECT status, result, mode FROM escalation_request WHERE correlation_id=?",
            ("sub-1",),
        )
        row = await cur.fetchone()
        assert row[0] == "submitted"
        assert json.loads(row[1])["answer"] == answer
        assert row[2] == "chat"

    async def test_claude_code_submission_sets_branch(
        self, app_and_conn, client: AsyncClient
    ) -> None:
        _app, conn = app_and_conn
        await _make_row(
            conn,
            correlation_id="sub-cc",
            status="resolved",
            mode="claude_code",
        )

        r = await client.post(
            "/admin/escalations/sub-cc/submit",
            json={
                "mode": "claude_code",
                "branch": "manual/escalation-sub-cc",
                "sha": "deadbeef",
            },
        )
        assert r.status_code == 200

        cur = await conn.execute(
            "SELECT branch_name FROM escalation_request WHERE correlation_id=?",
            ("sub-cc",),
        )
        row = await cur.fetchone()
        assert row[0] == "manual/escalation-sub-cc"

    async def test_chat_short_answer_rejected(
        self, app_and_conn, client: AsyncClient
    ) -> None:
        _app, conn = app_and_conn
        await _make_row(conn, correlation_id="sub-short", status="resolved", mode="chat")

        r = await client.post(
            "/admin/escalations/sub-short/submit",
            json={"mode": "chat", "answer": "too short"},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "schema_validation_failed"

    async def test_mode_mismatch_rejected(
        self, app_and_conn, client: AsyncClient
    ) -> None:
        _app, conn = app_and_conn
        await _make_row(conn, correlation_id="mm", status="resolved", mode="chat")

        r = await client.post(
            "/admin/escalations/mm/submit",
            json={"mode": "claude_code", "branch": "x"},
        )
        assert r.status_code == 409
        assert r.json()["detail"]["error"] == "mode_mismatch"

    async def test_open_status_rejects_submit(
        self, app_and_conn, client: AsyncClient
    ) -> None:
        _app, conn = app_and_conn
        await _make_row(conn, correlation_id="op", status="open", mode=None)

        r = await client.post(
            "/admin/escalations/op/submit",
            json={"mode": "chat", "answer": "x" * 60},
        )
        assert r.status_code == 409
        assert r.json()["detail"]["error"] == "not_awaiting_submission"

    async def test_resubmit_after_failure_increments_iteration(
        self, app_and_conn, client: AsyncClient
    ) -> None:
        _app, conn = app_and_conn
        await _make_row(
            conn,
            correlation_id="rs",
            status="failed",
            mode="claude_code",
            iteration=1,
        )

        r = await client.post(
            "/admin/escalations/rs/submit",
            json={"mode": "claude_code", "branch": "redo"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "submitted"
        assert body["iteration"] == 2

    async def test_404_when_missing(self, client: AsyncClient) -> None:
        r = await client.post(
            "/admin/escalations/missing/submit",
            json={"mode": "chat", "answer": "x" * 60},
        )
        assert r.status_code == 404
