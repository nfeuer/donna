"""Tests for the ConversationEngine."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from donna.chat.config import ChatConfig
from donna.chat.engine import ConversationEngine
from donna.chat.types import ChatIntent, ChatResponse


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock()
    db.get_active_chat_session.return_value = None
    db.create_chat_session.return_value = MagicMock(
        id="sess-1", user_id="nick", channel="discord",
        status="active", created_at="2026-04-12T10:00:00",
        last_activity="2026-04-12T10:00:00",
        expires_at="2026-04-12T12:00:00", message_count=0,
        pinned_task_id=None, summary=None,
    )
    db.add_chat_message.return_value = MagicMock(
        id="msg-1", session_id="sess-1", role="user",
        content="test", created_at="2026-04-12T10:00:00",
        intent=None, tokens_used=None,
    )
    db.list_chat_messages.return_value = []
    db.list_tasks.return_value = []
    return db


@pytest.fixture
def mock_router() -> AsyncMock:
    router = AsyncMock()
    # Default: classify as freeform, then respond
    router.complete.side_effect = [
        # First call: intent classification
        (
            {"intent": "freeform", "needs_escalation": False, "escalation_reason": None, "referenced_task_hint": None},
            MagicMock(tokens_in=50, tokens_out=20, cost_usd=0.0, latency_ms=200),
        ),
        # Second call: chat response
        (
            {"response_text": "Hey there!", "needs_escalation": False, "suggested_actions": [], "pin_suggestion": None, "action": None},
            MagicMock(tokens_in=100, tokens_out=50, cost_usd=0.0, latency_ms=500),
        ),
    ]
    return router


@pytest.fixture
def config() -> ChatConfig:
    return ChatConfig()


@pytest.fixture
def engine(
    mock_db: AsyncMock,
    mock_router: AsyncMock,
    config: ChatConfig,
    tmp_path: Path,
) -> ConversationEngine:
    return ConversationEngine(
        db=mock_db,
        router=mock_router,
        config=config,
        project_root=tmp_path,
    )


class TestHandleMessage:
    def test_creates_session_if_none_active(self, engine: ConversationEngine, mock_db: AsyncMock) -> None:
        async def _test() -> None:
            resp = await engine.handle_message(
                session_id=None, user_id="nick", text="Hello Donna",
                channel="discord",
            )
            mock_db.create_chat_session.assert_called_once()
            assert isinstance(resp, ChatResponse)
            assert resp.text == "Hey there!"

        asyncio.get_event_loop().run_until_complete(_test())

    def test_reuses_active_session(self, engine: ConversationEngine, mock_db: AsyncMock) -> None:
        async def _test() -> None:
            mock_db.get_active_chat_session.return_value = MagicMock(
                id="existing-sess", user_id="nick", channel="discord",
                status="active", message_count=3, pinned_task_id=None,
                expires_at="2026-04-12T12:00:00",
            )
            resp = await engine.handle_message(
                session_id=None, user_id="nick", text="Hello",
                channel="discord",
            )
            mock_db.create_chat_session.assert_not_called()
            assert resp.text == "Hey there!"

        asyncio.get_event_loop().run_until_complete(_test())

    def test_escalation_detected(self, engine: ConversationEngine, mock_router: AsyncMock) -> None:
        async def _test() -> None:
            mock_router.complete.side_effect = [
                (
                    {"intent": "planning", "needs_escalation": True, "escalation_reason": "Complex planning needed", "referenced_task_hint": None},
                    MagicMock(tokens_in=50, tokens_out=20, cost_usd=0.0, latency_ms=200),
                ),
            ]
            resp = await engine.handle_message(
                session_id=None, user_id="nick", text="Should I take on this new project?",
                channel="api",
            )
            assert resp.needs_escalation is True
            assert "Complex planning" in resp.escalation_reason

        asyncio.get_event_loop().run_until_complete(_test())

    def test_stores_user_and_assistant_messages(
        self, engine: ConversationEngine, mock_db: AsyncMock
    ) -> None:
        async def _test() -> None:
            await engine.handle_message(
                session_id=None, user_id="nick", text="Hi",
                channel="discord",
            )
            # User message + assistant message = 2 calls
            assert mock_db.add_chat_message.call_count == 2
            # First call is user message
            first_call = mock_db.add_chat_message.call_args_list[0]
            assert first_call.kwargs["role"] == "user"
            # Second call is assistant message
            second_call = mock_db.add_chat_message.call_args_list[1]
            assert second_call.kwargs["role"] == "assistant"

        asyncio.get_event_loop().run_until_complete(_test())


class TestEscalationApproval:
    def test_handle_escalation_calls_claude(
        self, engine: ConversationEngine, mock_router: AsyncMock, mock_db: AsyncMock
    ) -> None:
        async def _test() -> None:
            mock_db.get_chat_session.return_value = MagicMock(
                id="sess-1", user_id="nick", channel="api",
                status="active", message_count=2, pinned_task_id=None,
                expires_at="2026-04-12T12:00:00",
            )
            mock_db.list_chat_messages.return_value = [
                MagicMock(role="user", content="Complex question"),
            ]
            mock_router.complete.side_effect = None
            mock_router.complete.return_value = (
                {"response_text": "Here's my analysis...", "needs_escalation": False, "suggested_actions": [], "pin_suggestion": None, "action": None},
                MagicMock(tokens_in=500, tokens_out=200, cost_usd=0.03, latency_ms=2000),
            )
            resp = await engine.handle_escalation(
                session_id="sess-1", user_id="nick"
            )
            assert resp.text == "Here's my analysis..."
            # Should use chat_escalation task type
            call_args = mock_router.complete.call_args
            assert call_args.kwargs.get("task_type") == "chat_escalation"

        asyncio.get_event_loop().run_until_complete(_test())


class TestSessionPinning:
    def test_pin_session(self, engine: ConversationEngine, mock_db: AsyncMock) -> None:
        async def _test() -> None:
            mock_db.get_chat_session.return_value = MagicMock(
                id="sess-1", user_id="nick", status="active",
            )
            await engine.pin_session(session_id="sess-1", task_id="task-123")
            mock_db.update_chat_session.assert_called_with(
                "sess-1", pinned_task_id="task-123"
            )

        asyncio.get_event_loop().run_until_complete(_test())

    def test_unpin_session(self, engine: ConversationEngine, mock_db: AsyncMock) -> None:
        async def _test() -> None:
            mock_db.get_chat_session.return_value = MagicMock(
                id="sess-1", user_id="nick", status="active",
            )
            await engine.unpin_session(session_id="sess-1")
            mock_db.update_chat_session.assert_called_with(
                "sess-1", pinned_task_id=None
            )

        asyncio.get_event_loop().run_until_complete(_test())
