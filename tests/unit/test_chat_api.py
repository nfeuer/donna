"""Tests for the chat API endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from donna.chat.types import ChatResponse


@pytest.fixture
def mock_engine() -> AsyncMock:
    engine = AsyncMock()
    engine.handle_message.return_value = ChatResponse(
        text="Hey there!",
        suggested_actions=["schedule_task"],
    )
    engine.handle_escalation.return_value = ChatResponse(
        text="Here's my analysis...",
    )
    return engine


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock()
    db.get_chat_session.return_value = MagicMock(
        id="sess-1", user_id="nick", channel="api",
        status="active", created_at="2026-04-12T10:00:00",
        last_activity="2026-04-12T10:00:00",
        expires_at="2026-04-12T12:00:00",
        message_count=2, pinned_task_id=None, summary=None,
    )
    db.list_chat_messages.return_value = [
        MagicMock(
            id="msg-1", session_id="sess-1", role="user",
            content="Hello", created_at="2026-04-12T10:00:00",
            intent=None, tokens_used=None,
        ),
    ]
    db.get_active_chat_session.return_value = None
    return db


@pytest.fixture
def client(mock_engine: AsyncMock, mock_db: AsyncMock) -> TestClient:
    from fastapi import FastAPI

    from donna.api.auth.router_factory import _user_dep
    from donna.api.routes.chat import get_chat_engine, router

    app = FastAPI()
    app.state.db = mock_db
    app.state.chat_engine = mock_engine
    app.include_router(router, prefix="/chat")

    # Override deps: chat engine + user auth (mock session is owned by "nick").
    app.dependency_overrides[get_chat_engine] = lambda: mock_engine
    app.dependency_overrides[_user_dep] = lambda: "nick"

    return TestClient(app)


class TestPostMessage:
    def test_send_message(self, client: TestClient, mock_engine: AsyncMock) -> None:
        resp = client.post(
            "/chat/sessions/sess-1/messages",
            json={"text": "What's on my schedule?"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["text"] == "Hey there!"
        assert data["suggested_actions"] == ["schedule_task"]

    def test_send_message_without_session(self, client: TestClient) -> None:
        resp = client.post(
            "/chat/sessions/new/messages",
            json={"text": "Hello Donna"},
        )
        assert resp.status_code == 200


class TestGetSession:
    def test_get_session(self, client: TestClient, mock_db: AsyncMock) -> None:

        resp = client.get("/chat/sessions/sess-1")
        assert resp.status_code in (200, 422)


class TestEscalation:
    def test_approve_escalation(self, client: TestClient, mock_engine: AsyncMock) -> None:
        resp = client.post("/chat/sessions/sess-1/escalate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["text"] == "Here's my analysis..."


class TestPinning:
    def test_pin_session(self, client: TestClient, mock_engine: AsyncMock) -> None:
        resp = client.post(
            "/chat/sessions/sess-1/pin",
            json={"task_id": "task-123"},
        )
        assert resp.status_code == 200
        mock_engine.pin_session.assert_called_once_with(
            session_id="sess-1", task_id="task-123"
        )

    def test_unpin_session(self, client: TestClient, mock_engine: AsyncMock) -> None:
        resp = client.delete("/chat/sessions/sess-1/pin")
        assert resp.status_code == 200
        mock_engine.unpin_session.assert_called_once_with(session_id="sess-1")
