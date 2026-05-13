"""Integration tests for Chat API endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from donna.chat.types import ChatResponse


@dataclass
class _FakeSession:
    id: str = "sess-1"
    user_id: str = "test-user"
    channel: str = "api"
    status: str = "active"
    pinned_task_id: str | None = None
    summary: str | None = None
    created_at: str = "2026-01-01T00:00:00Z"
    last_activity: str = "2026-01-01T00:00:00Z"
    message_count: int = 0


@dataclass
class _FakeMessage:
    id: str = "msg-1"
    role: str = "user"
    content: str = "hello"
    intent: str | None = None
    tokens_used: int = 10
    created_at: str = "2026-01-01T00:00:00Z"


@pytest.fixture
def mock_engine() -> AsyncMock:
    engine = AsyncMock()
    engine.handle_message.return_value = ChatResponse(
        text="Hi there!",
        needs_escalation=False,
        escalation_reason=None,
        estimated_cost=0.001,
        suggested_actions=[],
        pin_suggestion=None,
        session_pinned_task_id=None,
    )
    return engine


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock()
    db.get_chat_session.return_value = _FakeSession()
    db.list_chat_messages.return_value = [_FakeMessage()]
    return db


@pytest.fixture
async def client(mock_engine: AsyncMock, mock_db: AsyncMock) -> AsyncClient:
    from fastapi import FastAPI

    from donna.api.routes.chat import router

    app = FastAPI()
    app.include_router(router, prefix="/chat")

    app.state.chat_engine = mock_engine
    app.state.db = mock_db

    from donna.api.auth.router_factory import _user_dep

    async def _override_user() -> str:
        return "test-user"

    app.dependency_overrides[_user_dep] = _override_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestSendMessage:
    async def test_send_message_returns_response(
        self, client: AsyncClient, mock_engine: AsyncMock
    ) -> None:
        resp = await client.post(
            "/chat/sessions/new/messages",
            json={"text": "Hello Donna"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["text"] == "Hi there!"
        assert data["needs_escalation"] is False

    async def test_send_empty_text_returns_400(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/chat/sessions/sess-1/messages",
            json={"text": "  "},
        )
        assert resp.status_code == 400


class TestGetSession:
    async def test_get_session_returns_details(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        resp = await client.get("/chat/sessions/sess-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session"]["id"] == "sess-1"
        assert len(data["messages"]) == 1

    async def test_get_session_not_found(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        mock_db.get_chat_session.return_value = None
        resp = await client.get("/chat/sessions/unknown")
        assert resp.status_code == 404


class TestContextStatus:
    async def test_context_status_returns_token_info(
        self, client: AsyncClient, mock_db: AsyncMock
    ) -> None:
        resp = await client.get("/chat/sessions/sess-1/context-status")
        assert resp.status_code == 200
        data = resp.json()
        assert "used_tokens" in data
        assert "max_tokens" in data
        assert "compact_threshold" in data
