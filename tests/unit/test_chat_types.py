"""Tests for chat type definitions."""

from donna.chat.types import (
    ChatIntent,
    ChatMessage,
    ChatResponse,
    ChatSession,
    ChatSessionStatus,
    MessageRole,
)


class TestChatIntent:
    def test_all_intents_exist(self) -> None:
        assert ChatIntent.TASK_QUERY == "task_query"
        assert ChatIntent.TASK_ACTION == "task_action"
        assert ChatIntent.AGENT_OUTPUT_QUERY == "agent_output_query"
        assert ChatIntent.PLANNING == "planning"
        assert ChatIntent.FREEFORM == "freeform"
        assert ChatIntent.ESCALATION_REQUEST == "escalation_request"


class TestChatSessionStatus:
    def test_all_statuses_exist(self) -> None:
        assert ChatSessionStatus.ACTIVE == "active"
        assert ChatSessionStatus.EXPIRED == "expired"
        assert ChatSessionStatus.CLOSED == "closed"


class TestMessageRole:
    def test_roles(self) -> None:
        assert MessageRole.USER == "user"
        assert MessageRole.ASSISTANT == "assistant"


class TestChatResponse:
    def test_defaults(self) -> None:
        resp = ChatResponse(text="Hello")
        assert resp.text == "Hello"
        assert resp.needs_escalation is False
        assert resp.escalation_reason is None
        assert resp.estimated_cost is None
        assert resp.suggested_actions == []
        assert resp.session_pinned_task_id is None
        assert resp.pin_suggestion is None

    def test_escalation_fields(self) -> None:
        resp = ChatResponse(
            text="I need Claude for this.",
            needs_escalation=True,
            escalation_reason="Complex planning required",
            estimated_cost=0.03,
        )
        assert resp.needs_escalation is True
        assert resp.escalation_reason == "Complex planning required"
        assert resp.estimated_cost == 0.03


class TestChatSession:
    def test_creation(self) -> None:
        session = ChatSession(
            id="sess-1",
            user_id="nick",
            channel="discord",
            status=ChatSessionStatus.ACTIVE,
            created_at="2026-04-12T10:00:00",
            last_activity="2026-04-12T10:00:00",
            expires_at="2026-04-12T12:00:00",
            message_count=0,
        )
        assert session.id == "sess-1"
        assert session.pinned_task_id is None
        assert session.summary is None


class TestChatMessage:
    def test_creation(self) -> None:
        msg = ChatMessage(
            id="msg-1",
            session_id="sess-1",
            role=MessageRole.USER,
            content="What's on my schedule?",
            created_at="2026-04-12T10:00:00",
        )
        assert msg.intent is None
        assert msg.tokens_used is None
