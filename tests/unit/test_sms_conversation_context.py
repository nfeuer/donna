"""Unit tests for SmsRouter conversation context routing.

Tests inbound SMS routing to active contexts, disambiguation,
new task creation, and context expiry — all with in-memory SQLite.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from donna.config import SmsConfig, SmsConversationContextConfig
from donna.integrations.sms_router import SmsRouter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_conn():
    """In-memory SQLite with conversation_context and tasks tables."""
    conn = await aiosqlite.connect(":memory:")
    await conn.executescript("""
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL
        );

        CREATE TABLE conversation_context (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            task_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            questions_asked TEXT,
            responses_received TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            hard_expires_at TEXT,
            last_activity TEXT NOT NULL
        );
    """)
    await conn.commit()
    yield conn
    await conn.close()


def _make_config() -> SmsConfig:
    return SmsConfig(
        conversation_context=SmsConversationContextConfig(
            sliding_ttl_hours=24,
            hard_cap_hours=72,
        )
    )


def _future(hours: int = 24) -> str:
    return (datetime.now(tz=UTC) + timedelta(hours=hours)).isoformat()


def _past(hours: int = 1) -> str:
    return (datetime.now(tz=UTC) - timedelta(hours=hours)).isoformat()


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


async def _insert_context(
    conn,
    ctx_id: str,
    task_id: str,
    task_title: str,
    status: str = "active",
    expires_offset_hours: int = 24,
    hard_expires_offset_hours: int | None = None,
) -> None:
    await conn.execute(
        "INSERT INTO tasks (id, title) VALUES (?, ?)",
        (task_id, task_title),
    )
    hard_exp = (
        (datetime.now(tz=UTC) + timedelta(hours=hard_expires_offset_hours)).isoformat()
        if hard_expires_offset_hours is not None
        else None
    )
    await conn.execute(
        """
        INSERT INTO conversation_context
          (id, user_id, channel, task_id, agent_id, questions_asked,
           responses_received, status, created_at, expires_at, hard_expires_at, last_activity)
        VALUES (?, 'u1', 'sms', ?, 'agent-1', NULL, NULL, ?, ?, ?, ?, ?)
        """,
        (
            ctx_id,
            task_id,
            status,
            _now(),
            (datetime.now(tz=UTC) + timedelta(hours=expires_offset_hours)).isoformat(),
            hard_exp,
            _now(),
        ),
    )
    await conn.commit()


def _make_router(db_conn, sms_send_result: bool = True) -> tuple[SmsRouter, MagicMock, MagicMock]:
    mock_db = MagicMock()
    mock_db.connection = db_conn

    mock_input_parser = MagicMock()
    mock_input_parser.parse = AsyncMock(return_value=MagicMock())

    mock_sms = MagicMock()
    mock_sms.send = AsyncMock(return_value=sms_send_result)

    router = SmsRouter(
        db=mock_db,
        input_parser=mock_input_parser,
        sms=mock_sms,
        sms_config=_make_config(),
        user_id="u1",
        user_phone="+15555550001",
    )
    return router, mock_input_parser, mock_sms


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNoContext:
    async def test_inbound_no_context_creates_new_task(self, db_conn) -> None:
        router, mock_input_parser, _ = _make_router(db_conn)

        await router.route_inbound(from_number="+15555550001", body="buy milk")

        mock_input_parser.parse.assert_called_once_with(
            raw_text="buy milk",
            user_id="u1",
            channel="sms",
        )

    async def test_unknown_sender_is_rejected(self, db_conn) -> None:
        router, mock_input_parser, _ = _make_router(db_conn)

        await router.route_inbound(from_number="+19999999999", body="buy milk")

        mock_input_parser.parse.assert_not_called()


class TestSingleActiveContext:
    async def test_inbound_routes_to_active_context(self, db_conn) -> None:
        await _insert_context(db_conn, "ctx-1", "task-1", "Buy Milk")
        router, mock_input_parser, _ = _make_router(db_conn)

        await router.route_inbound(from_number="+15555550001", body="yes I'm done")

        # Should NOT create a new task.
        mock_input_parser.parse.assert_not_called()

        # Response appended to context.
        cursor = await db_conn.execute(
            "SELECT responses_received FROM conversation_context WHERE id = 'ctx-1'"
        )
        row = await cursor.fetchone()
        responses = json.loads(row[0])
        assert len(responses) == 1
        assert responses[0]["text"] == "yes I'm done"

    async def test_sliding_ttl_updated_on_reply(self, db_conn) -> None:
        await _insert_context(db_conn, "ctx-1", "task-1", "Buy Milk", expires_offset_hours=1)
        router, _, _ = _make_router(db_conn)

        # Record old expires_at.
        cursor = await db_conn.execute(
            "SELECT expires_at FROM conversation_context WHERE id = 'ctx-1'"
        )
        old_expires = (await cursor.fetchone())[0]

        await router.route_inbound(from_number="+15555550001", body="reply")

        cursor = await db_conn.execute(
            "SELECT expires_at FROM conversation_context WHERE id = 'ctx-1'"
        )
        new_expires = (await cursor.fetchone())[0]

        # New expires_at should be later than old one.
        assert new_expires > old_expires


class TestMultipleContexts:
    async def test_inbound_multiple_contexts_sends_disambiguation(self, db_conn) -> None:
        await _insert_context(db_conn, "ctx-1", "task-1", "Buy Milk")
        await _insert_context(db_conn, "ctx-2", "task-2", "Call Doctor")
        router, mock_input_parser, mock_sms = _make_router(db_conn)

        await router.route_inbound(from_number="+15555550001", body="done")

        # Should NOT create a new task.
        mock_input_parser.parse.assert_not_called()
        # Should send disambiguation SMS.
        mock_sms.send.assert_called_once()
        call_body: str = mock_sms.send.call_args.kwargs["body"]
        assert "Which task" in call_body


class TestContextExpiry:
    async def test_expired_context_not_matched(self, db_conn) -> None:
        # Insert a context that expires in the past.
        await _insert_context(db_conn, "ctx-1", "task-1", "Buy Milk", expires_offset_hours=-1)
        router, mock_input_parser, _ = _make_router(db_conn)

        await router.route_inbound(from_number="+15555550001", body="reply")

        # Expired context ignored — treated as new task.
        mock_input_parser.parse.assert_called_once()

    async def test_hard_expires_at_marks_context_expired(self, db_conn) -> None:
        # Context has future expires_at but past hard_expires_at.
        await _insert_context(
            db_conn,
            "ctx-1",
            "task-1",
            "Buy Milk",
            expires_offset_hours=24,
            hard_expires_offset_hours=-1,  # already hard-expired
        )
        router, mock_input_parser, _ = _make_router(db_conn)

        await router.route_inbound(from_number="+15555550001", body="reply")

        # Should be treated as new task (hard_expires_at in the past).
        mock_input_parser.parse.assert_called_once()
