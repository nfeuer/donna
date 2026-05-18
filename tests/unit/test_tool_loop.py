"""Tests for the tool-use agent loop in ConversationEngine."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from donna.chat.config import ChatConfig
from donna.chat.engine import ConversationEngine
from donna.chat.tools import ToolRegistry
from donna.chat.types import ChatResponse


def _make_chat_tools_yaml(tmp_path: Path) -> Path:
    """Write a minimal chat_tools.yaml with one read tool."""
    config = {
        "tools": {
            "query_tasks": {
                "description": "List or search tasks",
                "domain": "tasks",
                "type": "read",
                "handler": "donna.chat.tools.tasks.query_tasks",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["backlog", "scheduled", "in_progress", "done"],
                        },
                    },
                    "required": [],
                },
            },
        },
    }
    path = tmp_path / "chat_tools.yaml"
    path.write_text(yaml.dump(config))
    return path


def _make_prompt_dir(tmp_path: Path) -> None:
    """Write a minimal tool_agent_system.md prompt template."""
    prompt_dir = tmp_path / "prompts" / "chat"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    (prompt_dir / "tool_agent_system.md").write_text(
        "System: {{ current_date }} {{ current_time }} {{ user_name }}\n"
        "{{ page_context }}\n"
        "{{ tool_schemas }}\n"
        "{{ conversation_history }}\n"
    )


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock()
    db.get_chat_session.return_value = None
    db.get_active_chat_session.return_value = None
    db.create_chat_session.return_value = MagicMock(
        id="sess-1",
        user_id="nick",
        channel="discord",
        status="active",
        created_at="2026-05-17T10:00:00",
        last_activity="2026-05-17T10:00:00",
        expires_at="2026-05-17T12:00:00",
        message_count=0,
        pinned_task_id=None,
        summary=None,
        pending_action=None,
    )
    db.add_chat_message.return_value = MagicMock(
        id="msg-1",
        session_id="sess-1",
        role="user",
        content="test",
        created_at="2026-05-17T10:00:00",
    )
    db.list_chat_messages.return_value = []
    db.update_chat_session.return_value = None
    db.execute_sql.return_value = []
    return db


@pytest.fixture
def mock_router() -> AsyncMock:
    router = AsyncMock()
    metadata = MagicMock(
        tokens_in=100,
        tokens_out=50,
        cost_usd=0.001,
        latency_ms=200,
        invocation_id="inv-001",
    )
    router.complete.return_value = (
        {"type": "text", "response_text": "Hello from tool loop!"},
        metadata,
    )
    return router


@pytest.fixture
def config() -> ChatConfig:
    return ChatConfig()


@pytest.fixture
def tool_registry(tmp_path: Path) -> ToolRegistry:
    yaml_path = _make_chat_tools_yaml(tmp_path)
    return ToolRegistry.from_yaml(yaml_path)


@pytest.fixture
def engine(
    mock_db: AsyncMock,
    mock_router: AsyncMock,
    config: ChatConfig,
    tool_registry: ToolRegistry,
    tmp_path: Path,
) -> ConversationEngine:
    _make_prompt_dir(tmp_path)
    return ConversationEngine(
        db=mock_db,
        router=mock_router,
        config=config,
        project_root=tmp_path,
        tool_registry=tool_registry,
    )


class TestToolLoop:
    """Tests for the tool-use agent loop path."""

    async def test_text_response_returns_directly(
        self,
        engine: ConversationEngine,
        mock_router: AsyncMock,
    ) -> None:
        """Router returns a text response — should return it directly."""
        mock_router.complete.return_value = (
            {"type": "text", "response_text": "Here are your tasks."},
            MagicMock(
                tokens_in=100, tokens_out=50,
                cost_usd=0.001, latency_ms=200,
                invocation_id="inv-001",
            ),
        )
        resp = await engine.handle_message(
            session_id=None,
            user_id="nick",
            text="What are my tasks?",
            channel="discord",
        )
        assert isinstance(resp, ChatResponse)
        assert resp.text == "Here are your tasks."
        assert resp.session_id == "sess-1"
        # Only one call to the router (no classify step in tool loop path)
        assert mock_router.complete.call_count == 1

    async def test_tool_call_executes_and_loops(
        self,
        engine: ConversationEngine,
        mock_router: AsyncMock,
        mock_db: AsyncMock,
    ) -> None:
        """Router returns a tool_call first, then text — should loop twice."""
        metadata = MagicMock(
            tokens_in=100, tokens_out=50,
            cost_usd=0.001, latency_ms=200,
            invocation_id="inv-001",
        )
        mock_router.complete.side_effect = [
            # First call: tool_call
            (
                {
                    "type": "tool_call",
                    "tool": "query_tasks",
                    "params": {"status": "in_progress"},
                },
                metadata,
            ),
            # Second call: text response
            (
                {"type": "text", "response_text": "You have 3 tasks in progress."},
                MagicMock(
                    tokens_in=200, tokens_out=80,
                    cost_usd=0.002, latency_ms=300,
                    invocation_id="inv-002",
                ),
            ),
        ]

        # Mock the tool execution (we patch execute on the registry)
        from donna.chat.tools import ToolResult

        original_execute = engine._tool_registry.execute
        execute_mock = AsyncMock(
            return_value=ToolResult(
                results=[{"id": "t1", "title": "Test task", "status": "in_progress"}],
                total_count=1,
            )
        )
        engine._tool_registry.execute = execute_mock  # type: ignore[assignment]

        resp = await engine.handle_message(
            session_id=None,
            user_id="nick",
            text="What tasks am I working on?",
            channel="discord",
        )

        assert resp.text == "You have 3 tasks in progress."
        assert mock_router.complete.call_count == 2
        execute_mock.assert_called_once()

        # Restore
        engine._tool_registry.execute = original_execute  # type: ignore[assignment]

    async def test_malformed_json_retries_once(
        self,
        engine: ConversationEngine,
        mock_router: AsyncMock,
    ) -> None:
        """First call returns garbage, second returns valid text — should work."""
        metadata = MagicMock(
            tokens_in=100, tokens_out=50,
            cost_usd=0.001, latency_ms=200,
            invocation_id="inv-001",
        )
        mock_router.complete.side_effect = [
            # First call: malformed (dict but no recognized structure)
            (
                {"garbage": "not a valid response"},
                metadata,
            ),
            # Second call: valid text
            (
                {"type": "text", "response_text": "Sorry about that. Here's the answer."},
                MagicMock(
                    tokens_in=150, tokens_out=60,
                    cost_usd=0.001, latency_ms=250,
                    invocation_id="inv-002",
                ),
            ),
        ]

        resp = await engine.handle_message(
            session_id=None,
            user_id="nick",
            text="Hello",
            channel="discord",
        )
        assert resp.text == "Sorry about that. Here's the answer."
        assert mock_router.complete.call_count == 2

    async def test_max_tool_calls_terminates_loop(
        self,
        engine: ConversationEngine,
        mock_router: AsyncMock,
    ) -> None:
        """Router always returns tool_call — loop should stop at limit."""
        metadata = MagicMock(
            tokens_in=100, tokens_out=50,
            cost_usd=0.001, latency_ms=200,
            invocation_id="inv-001",
        )
        # Always return a tool_call
        mock_router.complete.return_value = (
            {
                "type": "tool_call",
                "tool": "query_tasks",
                "params": {"status": "in_progress"},
            },
            metadata,
        )

        from donna.chat.tools import ToolResult

        execute_mock = AsyncMock(
            return_value=ToolResult(
                results=[{"id": "t1", "title": "Task", "status": "in_progress"}],
                total_count=1,
            )
        )
        engine._tool_registry.execute = execute_mock  # type: ignore[assignment]

        resp = await engine.handle_message(
            session_id=None,
            user_id="nick",
            text="Keep querying",
            channel="discord",
        )

        # Should terminate with an error/fallback message
        assert isinstance(resp, ChatResponse)
        assert resp.session_id == "sess-1"
        # 10 tool calls + 1 final attempt = 11, but the loop cap is 10 tool calls
        # then it returns a fallback. The router is called once per iteration.
        # After 10 tool executions, the loop breaks and returns fallback text.
        assert mock_router.complete.call_count == 10


class TestForceNewSession:
    """Tests for the force_new parameter on handle_message."""

    async def test_force_new_skips_active_session_lookup(
        self,
        engine: ConversationEngine,
        mock_db: AsyncMock,
    ) -> None:
        """With force_new=True, should NOT look up existing active session."""
        existing_session = MagicMock(
            id="existing-sess",
            user_id="nick",
            channel="discord",
            status="active",
            message_count=3,
            pinned_task_id=None,
            expires_at="2026-05-17T12:00:00",
            pending_action=None,
        )
        mock_db.get_active_chat_session.return_value = existing_session

        resp = await engine.handle_message(
            session_id=None,
            user_id="nick",
            text="Start fresh",
            channel="discord",
            force_new=True,
        )

        # Should NOT have called get_active_chat_session
        mock_db.get_active_chat_session.assert_not_called()
        # Should have created a new session
        mock_db.create_chat_session.assert_called_once()
        assert resp.session_id == "sess-1"

    async def test_without_force_new_reuses_session(
        self,
        engine: ConversationEngine,
        mock_db: AsyncMock,
    ) -> None:
        """Without force_new, should reuse existing active session."""
        existing_session = MagicMock(
            id="existing-sess",
            user_id="nick",
            channel="discord",
            status="active",
            message_count=3,
            pinned_task_id=None,
            expires_at="2026-05-17T12:00:00",
            pending_action=None,
        )
        mock_db.get_active_chat_session.return_value = existing_session

        resp = await engine.handle_message(
            session_id=None,
            user_id="nick",
            text="Continue",
            channel="discord",
        )

        mock_db.get_active_chat_session.assert_called_once()
        mock_db.create_chat_session.assert_not_called()
        assert resp.session_id == "existing-sess"
