"""Smoke test for the chat interface — full path through engine."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine

from donna.chat.config import ChatConfig
from donna.chat.engine import ConversationEngine
from donna.tasks.database import Database
from donna.tasks.db_models import Base
from donna.tasks.state_machine import StateMachine


@pytest.fixture
def db(tmp_path: Path, state_machine: StateMachine) -> Database:
    return Database(tmp_path / "test.db", state_machine)


@pytest.fixture
def connected_db(db: Database, tmp_path: Path) -> Database:
    asyncio.get_event_loop().run_until_complete(db.connect())
    # Create tables via SQLAlchemy metadata (faster than Alembic for tests).
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    engine.dispose()
    yield db
    asyncio.get_event_loop().run_until_complete(db.close())


@pytest.fixture
def mock_router() -> AsyncMock:
    router = AsyncMock()
    router.complete.side_effect = [
        # classify_chat_intent
        (
            {"intent": "freeform", "needs_escalation": False, "escalation_reason": None, "referenced_task_hint": None},
            MagicMock(tokens_in=50, tokens_out=20, cost_usd=0.0, latency_ms=100),
        ),
        # chat_respond
        (
            {"response_text": "Hey Nick, what's up?", "needs_escalation": False, "suggested_actions": [], "pin_suggestion": None, "action": None},
            MagicMock(tokens_in=100, tokens_out=30, cost_usd=0.0, latency_ms=300),
        ),
    ]
    return router


def test_full_chat_flow(connected_db: Database, mock_router: AsyncMock, tmp_path: Path) -> None:
    """End-to-end: send a message, get a response, verify session was created."""

    async def _test() -> None:
        config = ChatConfig()
        engine = ConversationEngine(
            db=connected_db,
            router=mock_router,
            config=config,
            project_root=tmp_path,
        )

        # First message — should create session
        resp = await engine.handle_message(
            session_id=None, user_id="nick", text="Hey Donna", channel="api"
        )
        assert resp.text == "Hey Nick, what's up?"
        assert resp.needs_escalation is False

        # Verify session was persisted
        session = await connected_db.get_active_chat_session("nick", "api")
        assert session is not None
        assert session.message_count == 2  # user + assistant

        # Verify messages were persisted
        messages = await connected_db.list_chat_messages(session.id)
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[0].content == "Hey Donna"
        assert messages[1].role == "assistant"
        assert messages[1].content == "Hey Nick, what's up?"

    asyncio.get_event_loop().run_until_complete(_test())


def test_session_close_with_summary(
    connected_db: Database, mock_router: AsyncMock, tmp_path: Path
) -> None:
    """Close a session and verify summary is generated."""

    async def _test() -> None:
        config = ChatConfig()
        engine = ConversationEngine(
            db=connected_db,
            router=mock_router,
            config=config,
            project_root=tmp_path,
        )

        # Create a session with a message
        mock_router.complete.side_effect = [
            ({"intent": "freeform", "needs_escalation": False, "escalation_reason": None, "referenced_task_hint": None}, MagicMock(tokens_in=50, tokens_out=20, cost_usd=0.0, latency_ms=100)),
            ({"response_text": "Hello!", "needs_escalation": False, "suggested_actions": [], "pin_suggestion": None, "action": None}, MagicMock(tokens_in=100, tokens_out=30, cost_usd=0.0, latency_ms=300)),
        ]
        await engine.handle_message(None, "nick", "Hi", "api")

        session = await connected_db.get_active_chat_session("nick", "api")

        # Close with summary
        mock_router.complete.side_effect = [
            ({"summary": "Brief greeting exchange."}, MagicMock(tokens_in=100, tokens_out=20, cost_usd=0.0, latency_ms=200)),
        ]
        summary = await engine.close_session(session.id)
        assert summary == "Brief greeting exchange."

        closed_session = await connected_db.get_chat_session(session.id)
        assert closed_session.status == "closed"
        assert closed_session.summary == "Brief greeting exchange."

    asyncio.get_event_loop().run_until_complete(_test())
