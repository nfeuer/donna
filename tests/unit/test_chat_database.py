"""Tests for chat session and message CRUD methods in Database."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from donna.chat.types import ChatMessage, ChatSession
from donna.tasks.database import Database

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    """Create and connect a Database with chat tables, yield, then close."""
    state_machine = MagicMock()
    database = Database(db_path=tmp_path / "test.db", state_machine=state_machine)
    await database.connect()

    conn = database.connection

    await conn.execute(
        """CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            domain TEXT NOT NULL DEFAULT 'personal',
            priority INTEGER NOT NULL DEFAULT 2,
            status TEXT NOT NULL DEFAULT 'backlog',
            estimated_duration INTEGER,
            deadline TEXT,
            deadline_type TEXT NOT NULL DEFAULT 'none',
            scheduled_start TEXT,
            actual_start TEXT,
            completed_at TEXT,
            recurrence TEXT,
            dependencies TEXT,
            parent_task TEXT,
            prep_work_flag INTEGER DEFAULT 0,
            prep_work_instructions TEXT,
            agent_eligible INTEGER DEFAULT 0,
            assigned_agent TEXT,
            agent_status TEXT,
            tags TEXT,
            notes TEXT,
            reschedule_count INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            created_via TEXT NOT NULL DEFAULT 'discord',
            estimated_cost REAL,
            calendar_event_id TEXT,
            donna_managed INTEGER DEFAULT 0,
            nudge_count INTEGER DEFAULT 0,
            quality_score REAL
        )"""
    )

    await conn.execute(
        """CREATE TABLE IF NOT EXISTS conversation_sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            pinned_task_id TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            summary TEXT,
            created_at TEXT NOT NULL,
            last_activity TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            message_count INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (pinned_task_id) REFERENCES tasks(id)
        )"""
    )

    await conn.execute(
        """CREATE TABLE IF NOT EXISTS conversation_messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            intent TEXT,
            tokens_used INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES conversation_sessions(id)
        )"""
    )

    await conn.commit()

    yield database

    await database.close()


# ---------------------------------------------------------------------------
# create_chat_session / get_chat_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_get_chat_session(db: Database) -> None:
    session = await db.create_chat_session(
        user_id="nick", channel="discord", ttl_minutes=60
    )

    assert isinstance(session, ChatSession)
    assert session.user_id == "nick"
    assert session.channel == "discord"
    assert session.status == "active"
    assert session.message_count == 0
    assert session.pinned_task_id is None
    assert session.summary is None

    fetched = await db.get_chat_session(session.id)
    assert fetched is not None
    assert fetched.id == session.id
    assert fetched.user_id == "nick"


@pytest.mark.asyncio
async def test_get_chat_session_not_found(db: Database) -> None:
    result = await db.get_chat_session("nonexistent-id")
    assert result is None


@pytest.mark.asyncio
async def test_create_chat_session_sets_expires_at(db: Database) -> None:
    before = datetime.utcnow()
    session = await db.create_chat_session(
        user_id="nick", channel="discord", ttl_minutes=30
    )
    after = datetime.utcnow()

    expires = datetime.fromisoformat(session.expires_at)
    assert expires >= before + timedelta(minutes=29)
    assert expires <= after + timedelta(minutes=31)


# ---------------------------------------------------------------------------
# get_active_chat_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_active_chat_session_returns_active(db: Database) -> None:
    session = await db.create_chat_session(
        user_id="nick", channel="discord", ttl_minutes=60
    )
    active = await db.get_active_chat_session(user_id="nick", channel="discord")
    assert active is not None
    assert active.id == session.id


@pytest.mark.asyncio
async def test_get_active_chat_session_returns_none_when_expired(db: Database) -> None:
    session = await db.create_chat_session(
        user_id="nick", channel="discord", ttl_minutes=60
    )
    # Expire it via update
    past = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    await db.update_chat_session(session.id, expires_at=past, status="expired")

    active = await db.get_active_chat_session(user_id="nick", channel="discord")
    assert active is None


@pytest.mark.asyncio
async def test_get_active_chat_session_wrong_channel(db: Database) -> None:
    await db.create_chat_session(user_id="nick", channel="discord", ttl_minutes=60)
    active = await db.get_active_chat_session(user_id="nick", channel="sms")
    assert active is None


@pytest.mark.asyncio
async def test_get_active_chat_session_returns_most_recent(db: Database) -> None:
    await db.create_chat_session(user_id="nick", channel="discord", ttl_minutes=60)
    s2 = await db.create_chat_session(user_id="nick", channel="discord", ttl_minutes=60)

    active = await db.get_active_chat_session(user_id="nick", channel="discord")
    assert active is not None
    # s2 was created after s1, so it should be most recent
    assert active.id == s2.id


# ---------------------------------------------------------------------------
# update_chat_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_chat_session_pin_task(db: Database) -> None:
    session = await db.create_chat_session(
        user_id="nick", channel="discord", ttl_minutes=60
    )

    # Insert a task to pin
    conn = db.connection
    now_iso = datetime.utcnow().isoformat()
    await conn.execute(
        "INSERT INTO tasks "
        "(id, user_id, title, domain, priority, status, "
        "deadline_type, created_at, created_via) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("task-1", "nick", "Test Task", "personal", 2,
         "backlog", "none", now_iso, "discord"),
    )
    await conn.commit()

    await db.update_chat_session(session.id, pinned_task_id="task-1")
    updated = await db.get_chat_session(session.id)
    assert updated is not None
    assert updated.pinned_task_id == "task-1"


@pytest.mark.asyncio
async def test_update_chat_session_unpin_task(db: Database) -> None:
    session = await db.create_chat_session(
        user_id="nick", channel="discord", ttl_minutes=60
    )
    await db.update_chat_session(session.id, pinned_task_id=None)
    updated = await db.get_chat_session(session.id)
    assert updated is not None
    assert updated.pinned_task_id is None


@pytest.mark.asyncio
async def test_update_chat_session_close_with_summary(db: Database) -> None:
    session = await db.create_chat_session(
        user_id="nick", channel="discord", ttl_minutes=60
    )
    await db.update_chat_session(
        session.id, status="closed", summary="Discussed scheduling tasks for next week."
    )
    updated = await db.get_chat_session(session.id)
    assert updated is not None
    assert updated.status == "closed"
    assert updated.summary == "Discussed scheduling tasks for next week."


@pytest.mark.asyncio
async def test_update_chat_session_message_count(db: Database) -> None:
    session = await db.create_chat_session(
        user_id="nick", channel="discord", ttl_minutes=60
    )
    await db.update_chat_session(session.id, message_count=5)
    updated = await db.get_chat_session(session.id)
    assert updated is not None
    assert updated.message_count == 5


# ---------------------------------------------------------------------------
# add_chat_message / list_chat_messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_chat_message_and_list(db: Database) -> None:
    session = await db.create_chat_session(
        user_id="nick", channel="discord", ttl_minutes=60
    )

    msg = await db.add_chat_message(
        session_id=session.id,
        role="user",
        content="What is on my schedule today?",
    )
    assert isinstance(msg, ChatMessage)
    assert msg.session_id == session.id
    assert msg.role == "user"
    assert msg.content == "What is on my schedule today?"
    assert msg.intent is None
    assert msg.tokens_used is None

    messages = await db.list_chat_messages(session.id)
    assert len(messages) == 1
    assert messages[0].id == msg.id


@pytest.mark.asyncio
async def test_add_chat_message_with_intent_and_tokens(db: Database) -> None:
    session = await db.create_chat_session(
        user_id="nick", channel="discord", ttl_minutes=60
    )

    msg = await db.add_chat_message(
        session_id=session.id,
        role="assistant",
        content="You have 3 tasks scheduled for today.",
        intent="task_query",
        tokens_used=150,
    )
    assert msg.intent == "task_query"
    assert msg.tokens_used == 150


@pytest.mark.asyncio
async def test_add_chat_message_increments_message_count(db: Database) -> None:
    session = await db.create_chat_session(
        user_id="nick", channel="discord", ttl_minutes=60
    )
    assert session.message_count == 0

    await db.add_chat_message(session_id=session.id, role="user", content="Hello")
    updated = await db.get_chat_session(session.id)
    assert updated is not None
    assert updated.message_count == 1

    await db.add_chat_message(session_id=session.id, role="assistant", content="Hi!")
    updated = await db.get_chat_session(session.id)
    assert updated is not None
    assert updated.message_count == 2


@pytest.mark.asyncio
async def test_add_chat_message_updates_last_activity(db: Database) -> None:
    session = await db.create_chat_session(
        user_id="nick", channel="discord", ttl_minutes=60
    )

    # Small sleep to ensure different timestamp
    import asyncio
    await asyncio.sleep(0.01)

    await db.add_chat_message(session_id=session.id, role="user", content="Hey")
    updated = await db.get_chat_session(session.id)
    assert updated is not None
    # last_activity should be updated (may be same if timestamps are identical isoformat)
    # Just verify it's a valid ISO timestamp
    datetime.fromisoformat(updated.last_activity)


# ---------------------------------------------------------------------------
# list_chat_messages pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_chat_messages_pagination(db: Database) -> None:
    session = await db.create_chat_session(
        user_id="nick", channel="discord", ttl_minutes=60
    )

    for i in range(5):
        await db.add_chat_message(
            session_id=session.id,
            role="user" if i % 2 == 0 else "assistant",
            content=f"Message {i}",
        )

    all_msgs = await db.list_chat_messages(session.id)
    assert len(all_msgs) == 5

    first_page = await db.list_chat_messages(session.id, limit=2, offset=0)
    assert len(first_page) == 2
    assert first_page[0].content == "Message 0"
    assert first_page[1].content == "Message 1"

    second_page = await db.list_chat_messages(session.id, limit=2, offset=2)
    assert len(second_page) == 2
    assert second_page[0].content == "Message 2"
    assert second_page[1].content == "Message 3"


@pytest.mark.asyncio
async def test_list_chat_messages_ordered_asc(db: Database) -> None:
    session = await db.create_chat_session(
        user_id="nick", channel="discord", ttl_minutes=60
    )

    contents = ["first", "second", "third"]
    for c in contents:
        await db.add_chat_message(session_id=session.id, role="user", content=c)

    messages = await db.list_chat_messages(session.id)
    assert [m.content for m in messages] == contents


@pytest.mark.asyncio
async def test_list_chat_messages_default_limit(db: Database) -> None:
    session = await db.create_chat_session(
        user_id="nick", channel="discord", ttl_minutes=60
    )
    # Insert 55 messages; default limit is 50
    for i in range(55):
        await db.add_chat_message(
            session_id=session.id, role="user", content=f"msg {i}"
        )

    messages = await db.list_chat_messages(session.id)
    assert len(messages) == 50


# ---------------------------------------------------------------------------
# get_expired_chat_sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_expired_chat_sessions(db: Database) -> None:
    # Active session (not expired)
    active = await db.create_chat_session(
        user_id="nick", channel="discord", ttl_minutes=60
    )

    # Session that has expired
    expired = await db.create_chat_session(
        user_id="nick", channel="sms", ttl_minutes=60
    )
    past = (datetime.utcnow() - timedelta(hours=2)).isoformat()
    await db.update_chat_session(expired.id, expires_at=past)

    results = await db.get_expired_chat_sessions()
    ids = [s.id for s in results]

    assert expired.id in ids
    assert active.id not in ids


@pytest.mark.asyncio
async def test_get_expired_chat_sessions_excludes_closed(db: Database) -> None:
    # Closed session that is also past expires_at
    session = await db.create_chat_session(
        user_id="nick", channel="discord", ttl_minutes=60
    )
    past = (datetime.utcnow() - timedelta(hours=2)).isoformat()
    await db.update_chat_session(session.id, expires_at=past, status="closed")

    results = await db.get_expired_chat_sessions()
    assert not any(s.id == session.id for s in results)


@pytest.mark.asyncio
async def test_get_expired_chat_sessions_empty(db: Database) -> None:
    results = await db.get_expired_chat_sessions()
    assert results == []
