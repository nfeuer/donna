"""Tests for chat context assembly."""

from donna.chat.context import (
    build_session_context,
    build_intent_context,
    render_chat_prompt,
)
from donna.chat.types import ChatIntent, ChatMessage


class TestBuildSessionContext:
    def test_empty_history(self) -> None:
        result = build_session_context(messages=[], pinned_task=None)
        assert result == ""

    def test_with_messages(self) -> None:
        messages = [
            ChatMessage(
                id="m1", session_id="s1", role="user",
                content="What's on my schedule?", created_at="2026-04-12T10:00:00",
            ),
            ChatMessage(
                id="m2", session_id="s1", role="assistant",
                content="You have 3 tasks today.", created_at="2026-04-12T10:00:05",
            ),
        ]
        result = build_session_context(messages=messages, pinned_task=None)
        assert "User: What's on my schedule?" in result
        assert "Assistant: You have 3 tasks today." in result

    def test_with_pinned_task(self) -> None:
        pinned = {
            "title": "Fix login bug",
            "description": "Auth fails on mobile",
            "status": "in_progress",
            "priority": 4,
            "notes": '["Checked JWT flow"]',
        }
        result = build_session_context(messages=[], pinned_task=pinned)
        assert "Fix login bug" in result
        assert "Auth fails on mobile" in result


class TestBuildIntentContext:
    def test_task_query_context(self) -> None:
        tasks = [
            {"title": "Buy groceries", "status": "backlog", "priority": 2, "domain": "personal"},
            {"title": "Code review", "status": "scheduled", "priority": 3, "domain": "work"},
        ]
        result = build_intent_context(ChatIntent.TASK_QUERY, tasks=tasks)
        assert "Buy groceries" in result
        assert "Code review" in result

    def test_planning_context(self) -> None:
        result = build_intent_context(
            ChatIntent.PLANNING,
            tasks=[{"title": "Deploy", "status": "scheduled", "priority": 4, "domain": "work"}],
            schedule_summary="2 tasks scheduled today",
            open_task_count=5,
        )
        assert "2 tasks scheduled today" in result
        assert "5" in result

    def test_freeform_returns_empty(self) -> None:
        result = build_intent_context(ChatIntent.FREEFORM)
        assert result == ""


class TestRenderChatPrompt:
    def test_renders_template_vars(self) -> None:
        template = "Date: {{ current_date }}\nUser: {{ user_name }}\n{{ user_input }}"
        result = render_chat_prompt(
            template=template,
            user_input="Hello Donna",
            user_name="Nick",
        )
        assert "Nick" in result
        assert "Hello Donna" in result
        # current_date is injected automatically
        assert "2026" in result
