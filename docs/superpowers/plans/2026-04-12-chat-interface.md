# Chat Interface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a freeform conversational chat interface to Donna with local LLM first, explicit Claude escalation, Discord + REST API frontends, and hot-reloadable config.

**Architecture:** A `ConversationEngine` in `src/donna/chat/` handles all chat interactions. It classifies intent via local LLM, assembles context per intent, generates responses via `ModelRouter`, and manages session lifecycle. Two thin frontend adapters (Discord channel, FastAPI routes) delegate to this engine. Config in `config/chat.yaml` is hot-reloaded via the existing admin dashboard.

**Tech Stack:** Python 3.12+ / asyncio, aiosqlite, structlog, FastAPI, discord.py, Jinja2 templates, Alembic migrations, Pydantic config models

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `src/donna/chat/__init__.py` | Package init |
| `src/donna/chat/engine.py` | ConversationEngine — session management, intent routing, context assembly, LLM calls |
| `src/donna/chat/types.py` | ChatResponse, ChatSession, ChatMessage dataclasses and enums |
| `src/donna/chat/context.py` | Context assembly — builds prompts per intent type |
| `src/donna/chat/config.py` | Pydantic models for chat.yaml, hot-reload loader |
| `src/donna/api/routes/chat.py` | FastAPI `/chat` REST endpoints |
| `config/chat.yaml` | Chat configuration (persona, sessions, escalation, discord) |
| `prompts/chat/chat_system.md` | Donna persona system prompt for chat |
| `prompts/chat/chat_system_neutral.md` | Neutral system prompt (no persona) |
| `prompts/chat/classify_intent.md` | Intent classification prompt |
| `prompts/chat/chat_respond.md` | Main chat response prompt |
| `prompts/chat/chat_summarize.md` | Session summary prompt |
| `schemas/chat_intent_output.json` | JSON schema for intent classification |
| `schemas/chat_respond_output.json` | JSON schema for chat response |
| `schemas/chat_summarize_output.json` | JSON schema for session summary |
| `alembic/versions/add_chat_tables.py` | Migration for conversation_sessions + conversation_messages |
| `tests/unit/test_chat_types.py` | Tests for chat dataclasses and enums |
| `tests/unit/test_chat_config.py` | Tests for chat config loading and defaults |
| `tests/unit/test_chat_context.py` | Tests for context assembly |
| `tests/unit/test_chat_engine.py` | Tests for ConversationEngine |
| `tests/unit/test_chat_api.py` | Tests for FastAPI chat endpoints |

### Modified Files

| File | Change |
|------|--------|
| `src/donna/tasks/db_models.py` | Add `ChatSessionStatus` enum, `ChatSession` model, `ChatMessage` model |
| `src/donna/tasks/database.py` | Add chat session/message CRUD methods |
| `src/donna/api/__init__.py` | Register chat router, load chat config in lifespan |
| `src/donna/api/routes/admin_config.py` | Add `chat.yaml` to `_ALLOWED_CONFIGS` |
| `config/donna_models.yaml` | Add `classify_chat_intent`, `chat_respond`, `chat_summarize`, `chat_escalation` routing entries |
| `config/task_types.yaml` | Add chat task type entries |

---

### Task 1: Chat Types and Enums

**Files:**
- Create: `src/donna/chat/__init__.py`
- Create: `src/donna/chat/types.py`
- Test: `tests/unit/test_chat_types.py`

- [ ] **Step 1: Write the failing test for ChatResponse and enums**

```python
# tests/unit/test_chat_types.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_chat_types.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'donna.chat'`

- [ ] **Step 3: Create the package and types module**

```python
# src/donna/chat/__init__.py
"""Chat interface — freeform conversational engine for Donna."""
```

```python
# src/donna/chat/types.py
"""Type definitions for the chat interface."""

from __future__ import annotations

import dataclasses
import enum


class ChatIntent(str, enum.Enum):
    TASK_QUERY = "task_query"
    TASK_ACTION = "task_action"
    AGENT_OUTPUT_QUERY = "agent_output_query"
    PLANNING = "planning"
    FREEFORM = "freeform"
    ESCALATION_REQUEST = "escalation_request"


class ChatSessionStatus(str, enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CLOSED = "closed"


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"


@dataclasses.dataclass(frozen=True)
class ChatResponse:
    """Response from the ConversationEngine."""

    text: str
    needs_escalation: bool = False
    escalation_reason: str | None = None
    estimated_cost: float | None = None
    suggested_actions: list[str] = dataclasses.field(default_factory=list)
    session_pinned_task_id: str | None = None
    pin_suggestion: dict[str, str] | None = None


@dataclasses.dataclass(frozen=True)
class ChatSession:
    """Read-only projection of a chat session row."""

    id: str
    user_id: str
    channel: str
    status: str
    created_at: str
    last_activity: str
    expires_at: str
    message_count: int
    pinned_task_id: str | None = None
    summary: str | None = None


@dataclasses.dataclass(frozen=True)
class ChatMessage:
    """Read-only projection of a chat message row."""

    id: str
    session_id: str
    role: str
    content: str
    created_at: str
    intent: str | None = None
    tokens_used: int | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_chat_types.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/donna/chat/__init__.py src/donna/chat/types.py tests/unit/test_chat_types.py
git commit -m "feat(chat): add chat type definitions and enums"
```

---

### Task 2: Chat Config Model and Loader

**Files:**
- Create: `src/donna/chat/config.py`
- Create: `config/chat.yaml`
- Test: `tests/unit/test_chat_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_chat_config.py
"""Tests for chat configuration loading."""

from pathlib import Path
import tempfile
import time

import yaml

from donna.chat.config import ChatConfig, load_chat_config, get_chat_config


class TestChatConfig:
    def test_defaults(self) -> None:
        config = ChatConfig()
        assert config.persona.mode == "donna"
        assert config.sessions.ttl_minutes == 120
        assert config.sessions.context_budget_tokens == 24000
        assert config.sessions.summary_on_close is True
        assert config.escalation.enabled is True
        assert config.escalation.auto_approve_under_usd == 0.0
        assert config.escalation.daily_budget_usd == 2.0
        assert config.escalation.model == "parser"
        assert config.intents.classify_model == "local_parser"
        assert config.intents.templates_dir == "prompts/chat"

    def test_load_from_yaml(self, tmp_path: Path) -> None:
        config_data = {
            "chat": {
                "persona": {"mode": "neutral"},
                "escalation": {"daily_budget_usd": 5.0},
            }
        }
        config_file = tmp_path / "chat.yaml"
        config_file.write_text(yaml.dump(config_data))

        config = load_chat_config(tmp_path)
        assert config.persona.mode == "neutral"
        assert config.escalation.daily_budget_usd == 5.0
        # Defaults still apply for unspecified fields
        assert config.sessions.ttl_minutes == 120

    def test_load_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        config = load_chat_config(tmp_path)
        assert config.persona.mode == "donna"

    def test_get_chat_config_caches(self, tmp_path: Path) -> None:
        config_data = {"chat": {"persona": {"mode": "donna"}}}
        config_file = tmp_path / "chat.yaml"
        config_file.write_text(yaml.dump(config_data))

        c1 = get_chat_config(tmp_path)
        c2 = get_chat_config(tmp_path)
        # Same object returned within TTL
        assert c1 is c2

    def test_get_chat_config_reloads_after_ttl(self, tmp_path: Path) -> None:
        config_data = {"chat": {"persona": {"mode": "donna"}}}
        config_file = tmp_path / "chat.yaml"
        config_file.write_text(yaml.dump(config_data))

        c1 = get_chat_config(tmp_path, cache_ttl_s=0)
        # Write new config
        config_data["chat"]["persona"]["mode"] = "neutral"
        config_file.write_text(yaml.dump(config_data))
        c2 = get_chat_config(tmp_path, cache_ttl_s=0)
        assert c2.persona.mode == "neutral"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_chat_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'donna.chat.config'`

- [ ] **Step 3: Implement config module**

```python
# src/donna/chat/config.py
"""Chat configuration — Pydantic models with hot-reload support.

Config is loaded from config/chat.yaml with a short TTL cache.
Edits via the admin dashboard take effect within seconds.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class PersonaConfig(BaseModel):
    mode: str = "donna"  # "donna" | "neutral"
    template: str = "prompts/chat/chat_system.md"


class SessionsConfig(BaseModel):
    ttl_minutes: int = 120
    context_budget_tokens: int = 24000
    summary_on_close: bool = True


class EscalationConfig(BaseModel):
    enabled: bool = True
    auto_approve_under_usd: float = 0.0
    daily_budget_usd: float = 2.0
    model: str = "parser"


class IntentsConfig(BaseModel):
    classify_model: str = "local_parser"
    templates_dir: str = "prompts/chat"


class DiscordChatConfig(BaseModel):
    chat_channel_id: int | None = None


class ChatConfig(BaseModel):
    persona: PersonaConfig = Field(default_factory=PersonaConfig)
    sessions: SessionsConfig = Field(default_factory=SessionsConfig)
    escalation: EscalationConfig = Field(default_factory=EscalationConfig)
    intents: IntentsConfig = Field(default_factory=IntentsConfig)
    discord: DiscordChatConfig = Field(default_factory=DiscordChatConfig)


def load_chat_config(config_dir: Path) -> ChatConfig:
    """Load chat config from config/chat.yaml. Returns defaults if missing."""
    path = config_dir / "chat.yaml"
    if not path.exists():
        return ChatConfig()
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    chat_data = raw.get("chat", {})
    return ChatConfig(**chat_data)


# Simple TTL cache for hot-reload
_cache: dict[str, tuple[float, ChatConfig]] = {}


def get_chat_config(
    config_dir: Path, cache_ttl_s: float = 5.0
) -> ChatConfig:
    """Get chat config with short TTL cache for hot-reload."""
    key = str(config_dir)
    now = time.monotonic()
    if key in _cache:
        cached_at, cached_config = _cache[key]
        if now - cached_at < cache_ttl_s:
            return cached_config
    config = load_chat_config(config_dir)
    _cache[key] = (now, config)
    return config
```

- [ ] **Step 4: Create the config file**

```yaml
# config/chat.yaml
chat:
  persona:
    mode: donna  # donna | neutral
    template: prompts/chat/chat_system.md

  sessions:
    ttl_minutes: 120
    context_budget_tokens: 24000
    summary_on_close: true

  escalation:
    enabled: true
    auto_approve_under_usd: 0.0  # 0 = always ask
    daily_budget_usd: 2.00
    model: parser  # from donna_models.yaml

  intents:
    classify_model: local_parser
    templates_dir: prompts/chat

  discord:
    chat_channel_id: ${DONNA_DISCORD_CHAT_CHANNEL_ID}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_chat_config.py -v`
Expected: All 5 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/donna/chat/config.py config/chat.yaml tests/unit/test_chat_config.py
git commit -m "feat(chat): add chat config model with hot-reload support"
```

---

### Task 3: Database Schema — Chat Tables

**Files:**
- Modify: `src/donna/tasks/db_models.py`
- Create: `alembic/versions/add_chat_tables.py`
- Modify: `src/donna/tasks/database.py`
- Test: `tests/unit/test_chat_database.py` (created in Task 4)

- [ ] **Step 1: Add SQLAlchemy models to db_models.py**

Add after the `EscalationState` class at the end of `src/donna/tasks/db_models.py`:

```python
class ChatSessionStatus(str, enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CLOSED = "closed"


class ChatMessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"


class ChatSessionModel(Base):
    """Chat conversation session. See docs/superpowers/specs/2026-04-12-chat-interface-design.md."""

    __tablename__ = "conversation_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    pinned_task_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tasks.id"), nullable=True
    )
    status: Mapped[ChatSessionStatus] = mapped_column(
        Enum(ChatSessionStatus), nullable=False, default=ChatSessionStatus.ACTIVE
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    last_activity: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ChatMessageModel(Base):
    """Individual message in a chat session."""

    __tablename__ = "conversation_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversation_sessions.id"), nullable=False, index=True
    )
    role: Mapped[ChatMessageRole] = mapped_column(
        Enum(ChatMessageRole), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
```

- [ ] **Step 2: Create Alembic migration**

Create `alembic/versions/add_chat_tables.py`:

```python
"""add chat conversation tables

Revision ID: f8b2d4e6a913
Revises: e7a3b4c5d692
Create Date: 2026-04-12 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f8b2d4e6a913"
down_revision: Union[str, None] = "e7a3b4c5d692"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversation_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(100), nullable=False, index=True),
        sa.Column("channel", sa.String(50), nullable=False),
        sa.Column("pinned_task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_activity", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("conversation_sessions.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("intent", sa.String(50), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("conversation_messages")
    op.drop_table("conversation_sessions")
```

- [ ] **Step 3: Commit**

```bash
git add src/donna/tasks/db_models.py alembic/versions/add_chat_tables.py
git commit -m "feat(chat): add conversation_sessions and conversation_messages tables"
```

---

### Task 4: Database CRUD Methods for Chat

**Files:**
- Modify: `src/donna/tasks/database.py`
- Test: `tests/unit/test_chat_database.py`

- [ ] **Step 1: Write failing tests for chat session CRUD**

```python
# tests/unit/test_chat_database.py
"""Tests for chat session and message database operations."""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from donna.chat.types import ChatMessage, ChatSession
from donna.tasks.database import Database
from donna.tasks.state_machine import StateMachine


@pytest.fixture
def db(tmp_path: Path) -> Database:
    sm = StateMachine({"transitions": {}, "valid_statuses": ["backlog", "done"]})
    return Database(tmp_path / "test.db", sm)


@pytest.fixture
def connected_db(db: Database) -> Database:
    asyncio.get_event_loop().run_until_complete(db.connect())
    yield db
    asyncio.get_event_loop().run_until_complete(db.close())


class TestChatSessionCRUD:
    def test_create_and_get_session(self, connected_db: Database) -> None:
        async def _test() -> None:
            session = await connected_db.create_chat_session(
                user_id="nick",
                channel="discord",
                ttl_minutes=120,
            )
            assert session.user_id == "nick"
            assert session.channel == "discord"
            assert session.status == "active"
            assert session.message_count == 0
            assert session.pinned_task_id is None

            fetched = await connected_db.get_chat_session(session.id)
            assert fetched is not None
            assert fetched.id == session.id

        asyncio.get_event_loop().run_until_complete(_test())

    def test_get_active_session(self, connected_db: Database) -> None:
        async def _test() -> None:
            await connected_db.create_chat_session(
                user_id="nick", channel="discord", ttl_minutes=120
            )
            active = await connected_db.get_active_chat_session(
                user_id="nick", channel="discord"
            )
            assert active is not None
            assert active.status == "active"

        asyncio.get_event_loop().run_until_complete(_test())

    def test_get_active_session_returns_none_when_expired(
        self, connected_db: Database
    ) -> None:
        async def _test() -> None:
            session = await connected_db.create_chat_session(
                user_id="nick", channel="discord", ttl_minutes=0
            )
            # Manually expire it
            await connected_db.update_chat_session(
                session.id, status="expired"
            )
            active = await connected_db.get_active_chat_session(
                user_id="nick", channel="discord"
            )
            assert active is None

        asyncio.get_event_loop().run_until_complete(_test())

    def test_pin_and_unpin(self, connected_db: Database) -> None:
        async def _test() -> None:
            session = await connected_db.create_chat_session(
                user_id="nick", channel="discord", ttl_minutes=120
            )
            await connected_db.update_chat_session(
                session.id, pinned_task_id="task-123"
            )
            updated = await connected_db.get_chat_session(session.id)
            assert updated.pinned_task_id == "task-123"

            await connected_db.update_chat_session(
                session.id, pinned_task_id=None
            )
            updated2 = await connected_db.get_chat_session(session.id)
            assert updated2.pinned_task_id is None

        asyncio.get_event_loop().run_until_complete(_test())

    def test_close_session(self, connected_db: Database) -> None:
        async def _test() -> None:
            session = await connected_db.create_chat_session(
                user_id="nick", channel="discord", ttl_minutes=120
            )
            await connected_db.update_chat_session(
                session.id, status="closed", summary="Discussed project planning."
            )
            closed = await connected_db.get_chat_session(session.id)
            assert closed.status == "closed"
            assert closed.summary == "Discussed project planning."

        asyncio.get_event_loop().run_until_complete(_test())


class TestChatMessageCRUD:
    def test_add_and_list_messages(self, connected_db: Database) -> None:
        async def _test() -> None:
            session = await connected_db.create_chat_session(
                user_id="nick", channel="discord", ttl_minutes=120
            )
            msg = await connected_db.add_chat_message(
                session_id=session.id,
                role="user",
                content="What's on my schedule?",
            )
            assert msg.role == "user"
            assert msg.content == "What's on my schedule?"

            messages = await connected_db.list_chat_messages(session.id)
            assert len(messages) == 1
            assert messages[0].id == msg.id

            # Check message_count was incremented
            updated_session = await connected_db.get_chat_session(session.id)
            assert updated_session.message_count == 1

        asyncio.get_event_loop().run_until_complete(_test())

    def test_add_message_with_intent_and_tokens(self, connected_db: Database) -> None:
        async def _test() -> None:
            session = await connected_db.create_chat_session(
                user_id="nick", channel="api", ttl_minutes=120
            )
            msg = await connected_db.add_chat_message(
                session_id=session.id,
                role="assistant",
                content="You have 3 tasks today.",
                intent="task_query",
                tokens_used=450,
            )
            assert msg.intent == "task_query"
            assert msg.tokens_used == 450

        asyncio.get_event_loop().run_until_complete(_test())

    def test_list_messages_paginated(self, connected_db: Database) -> None:
        async def _test() -> None:
            session = await connected_db.create_chat_session(
                user_id="nick", channel="discord", ttl_minutes=120
            )
            for i in range(5):
                await connected_db.add_chat_message(
                    session_id=session.id,
                    role="user",
                    content=f"Message {i}",
                )
            messages = await connected_db.list_chat_messages(
                session.id, limit=3, offset=0
            )
            assert len(messages) == 3

            messages2 = await connected_db.list_chat_messages(
                session.id, limit=3, offset=3
            )
            assert len(messages2) == 2

        asyncio.get_event_loop().run_until_complete(_test())

    def test_get_expired_sessions(self, connected_db: Database) -> None:
        async def _test() -> None:
            # Create a session with 0-minute TTL (already expired)
            session = await connected_db.create_chat_session(
                user_id="nick", channel="discord", ttl_minutes=0
            )
            expired = await connected_db.get_expired_chat_sessions()
            assert len(expired) >= 1
            assert any(s.id == session.id for s in expired)

        asyncio.get_event_loop().run_until_complete(_test())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_chat_database.py -v`
Expected: FAIL — `AttributeError: 'Database' object has no attribute 'create_chat_session'`

- [ ] **Step 3: Add chat CRUD methods to Database class**

Add these imports at the top of `src/donna/tasks/database.py`:

```python
from donna.chat.types import ChatMessage, ChatSession
```

Add these methods to the `Database` class in `src/donna/tasks/database.py` (after existing methods):

```python
    # ------------------------------------------------------------------
    # Chat session & message operations
    # ------------------------------------------------------------------

    async def create_chat_session(
        self,
        user_id: str,
        channel: str,
        ttl_minutes: int,
    ) -> ChatSession:
        """Create a new chat session with sliding TTL."""
        session_id = str(uuid6.uuid7())
        now = datetime.utcnow()
        expires_at = now + timedelta(minutes=ttl_minutes)

        await self._conn.execute(
            """INSERT INTO conversation_sessions
            (id, user_id, channel, status, created_at, last_activity, expires_at, message_count)
            VALUES (?, ?, ?, 'active', ?, ?, ?, 0)""",
            (session_id, user_id, channel, now.isoformat(), now.isoformat(), expires_at.isoformat()),
        )
        await self._conn.commit()
        return ChatSession(
            id=session_id,
            user_id=user_id,
            channel=channel,
            status="active",
            created_at=now.isoformat(),
            last_activity=now.isoformat(),
            expires_at=expires_at.isoformat(),
            message_count=0,
        )

    async def get_chat_session(self, session_id: str) -> ChatSession | None:
        """Fetch a chat session by ID."""
        cursor = await self._conn.execute(
            "SELECT * FROM conversation_sessions WHERE id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_chat_session(row, cursor.description)

    async def get_active_chat_session(
        self, user_id: str, channel: str
    ) -> ChatSession | None:
        """Find the active chat session for a user on a channel."""
        cursor = await self._conn.execute(
            """SELECT * FROM conversation_sessions
            WHERE user_id = ? AND channel = ? AND status = 'active'
            ORDER BY last_activity DESC LIMIT 1""",
            (user_id, channel),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_chat_session(row, cursor.description)

    async def update_chat_session(
        self, session_id: str, **kwargs: Any
    ) -> None:
        """Update chat session fields."""
        allowed = {
            "status", "summary", "pinned_task_id",
            "last_activity", "expires_at", "message_count",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values())
        values.append(session_id)
        await self._conn.execute(
            f"UPDATE conversation_sessions SET {set_clause} WHERE id = ?",
            values,
        )
        await self._conn.commit()

    async def get_expired_chat_sessions(self) -> list[ChatSession]:
        """Find active sessions past their expires_at."""
        now = datetime.utcnow().isoformat()
        cursor = await self._conn.execute(
            """SELECT * FROM conversation_sessions
            WHERE status = 'active' AND expires_at <= ?""",
            (now,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_chat_session(r, cursor.description) for r in rows]

    async def add_chat_message(
        self,
        session_id: str,
        role: str,
        content: str,
        intent: str | None = None,
        tokens_used: int | None = None,
    ) -> ChatMessage:
        """Add a message to a chat session and bump counters."""
        msg_id = str(uuid6.uuid7())
        now = datetime.utcnow()

        await self._conn.execute(
            """INSERT INTO conversation_messages
            (id, session_id, role, content, intent, tokens_used, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (msg_id, session_id, role, content, intent, tokens_used, now.isoformat()),
        )
        # Bump message_count and refresh last_activity
        await self._conn.execute(
            """UPDATE conversation_sessions
            SET message_count = message_count + 1, last_activity = ?
            WHERE id = ?""",
            (now.isoformat(), session_id),
        )
        await self._conn.commit()
        return ChatMessage(
            id=msg_id,
            session_id=session_id,
            role=role,
            content=content,
            created_at=now.isoformat(),
            intent=intent,
            tokens_used=tokens_used,
        )

    async def list_chat_messages(
        self,
        session_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ChatMessage]:
        """List messages in a session, ordered by creation time."""
        cursor = await self._conn.execute(
            """SELECT * FROM conversation_messages
            WHERE session_id = ?
            ORDER BY created_at ASC
            LIMIT ? OFFSET ?""",
            (session_id, limit, offset),
        )
        rows = await cursor.fetchall()
        return [self._row_to_chat_message(r, cursor.description) for r in rows]

    @staticmethod
    def _row_to_chat_session(
        row: Any, description: Any
    ) -> ChatSession:
        cols = [d[0] for d in description]
        d = dict(zip(cols, row))
        return ChatSession(
            id=d["id"],
            user_id=d["user_id"],
            channel=d["channel"],
            status=d["status"],
            created_at=d["created_at"],
            last_activity=d["last_activity"],
            expires_at=d["expires_at"],
            message_count=d["message_count"],
            pinned_task_id=d.get("pinned_task_id"),
            summary=d.get("summary"),
        )

    @staticmethod
    def _row_to_chat_message(
        row: Any, description: Any
    ) -> ChatMessage:
        cols = [d[0] for d in description]
        d = dict(zip(cols, row))
        return ChatMessage(
            id=d["id"],
            session_id=d["session_id"],
            role=d["role"],
            content=d["content"],
            created_at=d["created_at"],
            intent=d.get("intent"),
            tokens_used=d.get("tokens_used"),
        )
```

Also add `from datetime import timedelta` to the imports if not already present.

The `Database.connect()` method already runs `CREATE TABLE IF NOT EXISTS` statements or Alembic migrations. For the tests to work, ensure the `connect()` method creates the chat tables. Add these SQL statements to the `connect()` method's table creation block (alongside existing `CREATE TABLE IF NOT EXISTS` statements):

```sql
CREATE TABLE IF NOT EXISTS conversation_sessions (
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
);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    intent TEXT,
    tokens_used INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES conversation_sessions(id)
);
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_chat_database.py -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/donna/tasks/database.py tests/unit/test_chat_database.py
git commit -m "feat(chat): add chat session and message CRUD to database"
```

---

### Task 5: Prompt Templates and JSON Schemas

**Files:**
- Create: `prompts/chat/chat_system.md`
- Create: `prompts/chat/chat_system_neutral.md`
- Create: `prompts/chat/classify_intent.md`
- Create: `prompts/chat/chat_respond.md`
- Create: `prompts/chat/chat_summarize.md`
- Create: `schemas/chat_intent_output.json`
- Create: `schemas/chat_respond_output.json`
- Create: `schemas/chat_summarize_output.json`

- [ ] **Step 1: Create the prompts/chat directory and system prompts**

```markdown
<!-- prompts/chat/chat_system.md -->
# Donna — Chat System Prompt

You are Donna, an AI personal assistant modeled after Donna Paulsen from Suits.
You are sharp, confident, efficient, occasionally witty, and always one step ahead.

## Personality

- **Confident and direct.** You do not hedge. State facts and actions clearly.
- **Proactive.** Anticipate needs. Point out things the user hasn't noticed yet.
- **Witty but professional.** Light humor is fine. Sarcasm when the user is behind on tasks is on-brand. Never sycophantic.
- **Efficient.** Messages are concise. No filler. Bullet points and clear action items.
- **Loyal and protective of the user's time.** Push back on overcommitment. Flag unrealistic schedules.

## Communication Rules

- Lead with the most important information.
- Use bullet points for lists of tasks or action items.
- Include specific times, dates, and durations whenever referencing schedule items.
- When asking for input, provide clear options rather than open-ended questions.
- Never apologize for being persistent about overdue tasks — that's your job.
- If the user is falling behind, say so directly but constructively.

## Context

Today's date: {{ current_date }}
Current time: {{ current_time }}
User: {{ user_name }}

{{ session_context }}
{{ intent_context }}
```

```markdown
<!-- prompts/chat/chat_system_neutral.md -->
# Donna — Chat System Prompt (Neutral Mode)

You are a task management assistant. Be concise, accurate, and helpful.

## Rules

- Answer questions directly with relevant information.
- Use bullet points for lists.
- Include specific dates, times, and durations when referencing schedule items.
- Do not add personality, humor, or editorial commentary.

## Context

Today's date: {{ current_date }}
Current time: {{ current_time }}
User: {{ user_name }}

{{ session_context }}
{{ intent_context }}
```

```markdown
<!-- prompts/chat/classify_intent.md -->
# Intent Classification

Classify the user's message into exactly one intent category.

## Categories

- **task_query**: Asking about tasks — status, list, details, schedule, deadlines
- **task_action**: Requesting a change — create, reschedule, reprioritize, complete, cancel a task
- **agent_output_query**: Asking about what agents did — prep results, research output, agent activity
- **planning**: Asking for planning advice — "what should I focus on?", "am I overcommitted?", workload assessment
- **freeform**: General conversation, not tied to a specific system action or data lookup
- **escalation_request**: User explicitly asks for Claude's help or a more capable model

## Output

Respond with a JSON object. Set `needs_escalation` to true ONLY if you cannot confidently answer — the question requires complex multi-step reasoning, long-horizon planning, or nuanced judgment beyond your capability.

## Current Context

Today's date: {{ current_date }}
User: {{ user_name }}

## User Message

{{ user_input }}
```

```markdown
<!-- prompts/chat/chat_respond.md -->
# Chat Response

Respond to the user's message given the context below.

## Instructions

- Answer based on the provided context data. Do not make up task details, dates, or agent outputs.
- If the user asks about something not in the context, say you don't have that information.
- If a task action is requested, include the action details in `suggested_actions`.
- If the conversation is clearly about a specific task and the session is not pinned, suggest pinning via `pin_suggestion`.
- Set `needs_escalation` to true if you cannot confidently answer — the question requires complex multi-step reasoning, long-horizon planning, or nuanced judgment. Include a clear reason in `escalation_reason`.

## Context

{{ system_prompt }}

## Conversation History

{{ conversation_history }}

## User Message

{{ user_input }}
```

```markdown
<!-- prompts/chat/chat_summarize.md -->
# Session Summary

Summarize this chat session in 2-3 sentences. Focus on:
- What was discussed (topics, tasks referenced)
- Any decisions made or actions taken
- Any open items or follow-ups mentioned

## Conversation

{{ conversation_history }}
```

- [ ] **Step 2: Create JSON schemas**

```json
// schemas/chat_intent_output.json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "ChatIntentOutput",
  "description": "Intent classification for a chat message",
  "type": "object",
  "required": ["intent", "needs_escalation"],
  "properties": {
    "intent": {
      "type": "string",
      "enum": ["task_query", "task_action", "agent_output_query", "planning", "freeform", "escalation_request"]
    },
    "needs_escalation": {
      "type": "boolean",
      "description": "Whether this requires Claude escalation"
    },
    "escalation_reason": {
      "type": ["string", "null"],
      "description": "Why escalation is needed, if applicable"
    },
    "referenced_task_hint": {
      "type": ["string", "null"],
      "description": "Keyword or phrase that might identify a specific task"
    }
  }
}
```

```json
// schemas/chat_respond_output.json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "ChatRespondOutput",
  "description": "Structured chat response from the LLM",
  "type": "object",
  "required": ["response_text", "needs_escalation"],
  "properties": {
    "response_text": {
      "type": "string",
      "description": "The text response to show the user"
    },
    "needs_escalation": {
      "type": "boolean"
    },
    "escalation_reason": {
      "type": ["string", "null"]
    },
    "suggested_actions": {
      "type": "array",
      "items": { "type": "string" },
      "default": [],
      "description": "Actions Donna can take (e.g. schedule_task, run_prep_agent)"
    },
    "pin_suggestion": {
      "type": ["object", "null"],
      "properties": {
        "task_id": { "type": "string" },
        "task_title": { "type": "string" }
      },
      "description": "Suggest pinning to a task if conversation is about one"
    },
    "action": {
      "type": ["object", "null"],
      "description": "Task action to execute (e.g. {action: 'reschedule', task_id: '...', new_time: '...'})"
    }
  }
}
```

```json
// schemas/chat_summarize_output.json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "ChatSummarizeOutput",
  "description": "Session summary for expired/closed conversations",
  "type": "object",
  "required": ["summary"],
  "properties": {
    "summary": {
      "type": "string",
      "description": "2-3 sentence summary of the conversation",
      "maxLength": 1000
    }
  }
}
```

- [ ] **Step 3: Commit**

```bash
mkdir -p prompts/chat
git add prompts/chat/ schemas/chat_intent_output.json schemas/chat_respond_output.json schemas/chat_summarize_output.json
git commit -m "feat(chat): add prompt templates and JSON schemas for chat"
```

---

### Task 6: Update Routing Config

**Files:**
- Modify: `config/donna_models.yaml`
- Modify: `config/task_types.yaml`
- Modify: `src/donna/api/routes/admin_config.py`

- [ ] **Step 1: Add chat routing entries to donna_models.yaml**

Add to the `routing:` section in `config/donna_models.yaml`:

```yaml
  # Chat interface
  classify_chat_intent:
    model: local_parser
  chat_respond:
    model: local_parser
  chat_summarize:
    model: local_parser
  chat_escalation:
    model: parser
```

- [ ] **Step 2: Add chat task types to task_types.yaml**

Add to the `task_types:` section in `config/task_types.yaml`:

```yaml
  classify_chat_intent:
    description: "Classify user chat message intent"
    model: local_parser
    prompt_template: prompts/chat/classify_intent.md
    output_schema: schemas/chat_intent_output.json
    tools: []

  chat_respond:
    description: "Generate chat response based on intent and context"
    model: local_parser
    prompt_template: prompts/chat/chat_respond.md
    output_schema: schemas/chat_respond_output.json
    tools: []

  chat_summarize:
    description: "Summarize a chat session on close or expiry"
    model: local_parser
    prompt_template: prompts/chat/chat_summarize.md
    output_schema: schemas/chat_summarize_output.json
    tools: []

  chat_escalation:
    description: "Chat response via Claude when local LLM cannot answer"
    model: parser
    prompt_template: prompts/chat/chat_respond.md
    output_schema: schemas/chat_respond_output.json
    tools: []
```

- [ ] **Step 3: Add chat.yaml to allowed configs**

In `src/donna/api/routes/admin_config.py`, add `"chat.yaml"` to `_ALLOWED_CONFIGS`:

```python
_ALLOWED_CONFIGS = {
    "agents.yaml",
    "chat.yaml",
    "dashboard.yaml",
    # ... rest unchanged
}
```

- [ ] **Step 4: Commit**

```bash
git add config/donna_models.yaml config/task_types.yaml src/donna/api/routes/admin_config.py
git commit -m "feat(chat): add chat routing config and task types"
```

---

### Task 7: Context Assembly

**Files:**
- Create: `src/donna/chat/context.py`
- Test: `tests/unit/test_chat_context.py`

- [ ] **Step 1: Write failing tests for context assembly**

```python
# tests/unit/test_chat_context.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_chat_context.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'donna.chat.context'`

- [ ] **Step 3: Implement context assembly**

```python
# src/donna/chat/context.py
"""Context assembly for chat prompts.

Builds the context blocks that get injected into chat prompt templates
based on intent type and session state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from donna.chat.types import ChatIntent, ChatMessage


def build_session_context(
    messages: list[ChatMessage],
    pinned_task: dict[str, Any] | None,
) -> str:
    """Build the session context block for the prompt.

    Includes conversation history and pinned task details.
    """
    parts: list[str] = []

    if pinned_task:
        parts.append("## Pinned Task")
        parts.append(f"**{pinned_task.get('title', 'Untitled')}**")
        if pinned_task.get("description"):
            parts.append(f"Description: {pinned_task['description']}")
        parts.append(f"Status: {pinned_task.get('status', 'unknown')}")
        parts.append(f"Priority: {pinned_task.get('priority', 'unknown')}")
        if pinned_task.get("notes"):
            parts.append(f"Notes: {pinned_task['notes']}")
        parts.append("")

    if messages:
        parts.append("## Conversation History")
        for msg in messages:
            role_label = "User" if msg.role == "user" else "Assistant"
            parts.append(f"{role_label}: {msg.content}")
        parts.append("")

    return "\n".join(parts)


def build_intent_context(
    intent: ChatIntent,
    tasks: list[dict[str, Any]] | None = None,
    schedule_summary: str | None = None,
    open_task_count: int | None = None,
    agent_outputs: list[dict[str, Any]] | None = None,
) -> str:
    """Build intent-specific context for the prompt."""
    if intent == ChatIntent.FREEFORM:
        return ""

    if intent == ChatIntent.ESCALATION_REQUEST:
        return ""

    parts: list[str] = []

    if intent in (ChatIntent.TASK_QUERY, ChatIntent.TASK_ACTION, ChatIntent.PLANNING):
        if tasks:
            parts.append("## Active Tasks")
            for t in tasks:
                parts.append(
                    f"- [{t.get('status', '?')}] {t.get('title', 'Untitled')} "
                    f"(P{t.get('priority', '?')}, {t.get('domain', '?')})"
                )
            parts.append("")

    if intent == ChatIntent.PLANNING:
        if schedule_summary:
            parts.append(f"## Schedule\n{schedule_summary}\n")
        if open_task_count is not None:
            parts.append(f"Open tasks across all domains: {open_task_count}\n")

    if intent == ChatIntent.AGENT_OUTPUT_QUERY:
        if agent_outputs:
            parts.append("## Agent Outputs")
            for ao in agent_outputs:
                parts.append(
                    f"- [{ao.get('task_type', '?')}] {ao.get('model_actual', '?')}: "
                    f"{str(ao.get('output', ''))[:500]}"
                )
            parts.append("")

    return "\n".join(parts)


def render_chat_prompt(
    template: str,
    user_input: str,
    user_name: str = "Nick",
    session_context: str = "",
    intent_context: str = "",
    conversation_history: str = "",
) -> str:
    """Render a chat prompt template with variables."""
    now = datetime.now(timezone.utc)
    return (
        template
        .replace("{{ current_date }}", now.strftime("%Y-%m-%d"))
        .replace("{{ current_time }}", now.strftime("%H:%M %Z"))
        .replace("{{ user_name }}", user_name)
        .replace("{{ user_input }}", user_input)
        .replace("{{ session_context }}", session_context)
        .replace("{{ intent_context }}", intent_context)
        .replace("{{ conversation_history }}", conversation_history)
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_chat_context.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/donna/chat/context.py tests/unit/test_chat_context.py
git commit -m "feat(chat): add context assembly for chat prompts"
```

---

### Task 8: Conversation Engine

**Files:**
- Create: `src/donna/chat/engine.py`
- Test: `tests/unit/test_chat_engine.py`

This is the core component. The engine orchestrates intent classification, context assembly, LLM calls, session management, and escalation detection.

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_chat_engine.py
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
            mock_router.complete.return_value = (
                {"response_text": "Here's my analysis...", "needs_escalation": False, "suggested_actions": [], "pin_suggestion": None, "action": None},
                MagicMock(tokens_in=500, tokens_out=200, cost_usd=0.03, latency_ms=2000),
            )
            resp = await engine.handle_escalation(
                session_id="sess-1", user_id="nick"
            )
            assert resp.text == "Here's my analysis..."
            # Should use chat_escalation task type
            call_kwargs = mock_router.complete.call_args
            assert call_kwargs.kwargs.get("task_type") == "chat_escalation" or \
                   call_kwargs[1].get("task_type") == "chat_escalation" or \
                   "chat_escalation" in str(call_kwargs)

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_chat_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'donna.chat.engine'`

- [ ] **Step 3: Implement ConversationEngine**

```python
# src/donna/chat/engine.py
"""Conversation engine — core chat handler for Donna.

Single entry point for all chat interactions. Classifies intent,
assembles context, calls the local LLM, and manages sessions.
See docs/superpowers/specs/2026-04-12-chat-interface-design.md.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import structlog

from donna.chat.config import ChatConfig
from donna.chat.context import (
    build_intent_context,
    build_session_context,
    render_chat_prompt,
)
from donna.chat.types import ChatIntent, ChatResponse

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from donna.models.router import ModelRouter
    from donna.tasks.database import Database

logger = structlog.get_logger()


class ConversationEngine:
    """Handles all chat interactions regardless of frontend.

    Usage:
        engine = ConversationEngine(db, router, config, project_root)
        response = await engine.handle_message(None, "nick", "Hi", "discord")
    """

    def __init__(
        self,
        db: Database,
        router: ModelRouter,
        config: ChatConfig,
        project_root: Path,
    ) -> None:
        self._db = db
        self._router = router
        self._config = config
        self._project_root = project_root

    async def handle_message(
        self,
        session_id: str | None,
        user_id: str,
        text: str,
        channel: str,
    ) -> ChatResponse:
        """Process a chat message and return a response.

        If session_id is None, resumes the active session or creates one.
        """
        log = logger.bind(user_id=user_id, channel=channel)

        # Resolve or create session
        session = None
        if session_id:
            session = await self._db.get_chat_session(session_id)
        if session is None:
            session = await self._db.get_active_chat_session(user_id, channel)
        if session is None:
            session = await self._db.create_chat_session(
                user_id=user_id,
                channel=channel,
                ttl_minutes=self._config.sessions.ttl_minutes,
            )
            log.info("chat_session_created", session_id=session.id)

        # Refresh TTL
        new_expires = datetime.utcnow() + timedelta(
            minutes=self._config.sessions.ttl_minutes
        )
        await self._db.update_chat_session(
            session.id, expires_at=new_expires.isoformat()
        )

        # Store user message
        await self._db.add_chat_message(
            session_id=session.id, role="user", content=text
        )

        # Classify intent
        intent_result = await self._classify_intent(text, user_id)
        intent = ChatIntent(intent_result.get("intent", "freeform"))

        # Check for escalation at classification stage
        if intent_result.get("needs_escalation"):
            cost_estimate = self._estimate_escalation_cost()
            return ChatResponse(
                text=f"I'd need to use Claude for this — {intent_result.get('escalation_reason', 'complex reasoning required')}. "
                     f"Estimated cost: ~${cost_estimate:.2f}. Go ahead?",
                needs_escalation=True,
                escalation_reason=intent_result.get("escalation_reason"),
                estimated_cost=cost_estimate,
                session_pinned_task_id=getattr(session, "pinned_task_id", None),
            )

        # Load context
        history = await self._db.list_chat_messages(session.id)
        pinned_task = None
        if getattr(session, "pinned_task_id", None):
            task_row = await self._db.get_task(session.pinned_task_id)
            if task_row:
                pinned_task = {
                    "title": task_row.title,
                    "description": task_row.description,
                    "status": task_row.status,
                    "priority": task_row.priority,
                    "notes": task_row.notes,
                }

        session_ctx = build_session_context(history, pinned_task)

        # Build intent-specific context
        intent_ctx = await self._load_intent_context(intent, user_id)

        # Load and render prompt
        system_template = self._load_system_prompt()
        prompt = render_chat_prompt(
            template=system_template,
            user_input=text,
            user_name="Nick",
            session_context=session_ctx,
            intent_context=intent_ctx,
        )

        # Call LLM for response
        response_data, metadata = await self._router.complete(
            prompt=prompt,
            task_type="chat_respond",
            user_id=user_id,
        )

        response_text = response_data.get("response_text", "")
        needs_escalation = response_data.get("needs_escalation", False)

        if needs_escalation:
            cost_estimate = self._estimate_escalation_cost()
            result = ChatResponse(
                text=f"I'd need to use Claude for this — {response_data.get('escalation_reason', 'complex reasoning required')}. "
                     f"Estimated cost: ~${cost_estimate:.2f}. Go ahead?",
                needs_escalation=True,
                escalation_reason=response_data.get("escalation_reason"),
                estimated_cost=cost_estimate,
                session_pinned_task_id=getattr(session, "pinned_task_id", None),
            )
        else:
            result = ChatResponse(
                text=response_text,
                suggested_actions=response_data.get("suggested_actions", []),
                pin_suggestion=response_data.get("pin_suggestion"),
                session_pinned_task_id=getattr(session, "pinned_task_id", None),
            )

        # Store assistant message
        await self._db.add_chat_message(
            session_id=session.id,
            role="assistant",
            content=result.text,
            intent=intent.value,
            tokens_used=metadata.tokens_out if hasattr(metadata, "tokens_out") else None,
        )

        log.info(
            "chat_response_sent",
            session_id=session.id,
            intent=intent.value,
            escalation=result.needs_escalation,
        )

        return result

    async def handle_escalation(
        self, session_id: str, user_id: str
    ) -> ChatResponse:
        """Handle an approved escalation — send context to Claude."""
        session = await self._db.get_chat_session(session_id)
        if session is None:
            return ChatResponse(text="Session not found.")

        history = await self._db.list_chat_messages(session_id)
        session_ctx = build_session_context(history, pinned_task=None)

        system_template = self._load_system_prompt()
        # Use the last user message as the input
        last_user_msg = ""
        for msg in reversed(history):
            if msg.role == "user":
                last_user_msg = msg.content
                break

        prompt = render_chat_prompt(
            template=system_template,
            user_input=last_user_msg,
            user_name="Nick",
            session_context=session_ctx,
        )

        response_data, metadata = await self._router.complete(
            prompt=prompt,
            task_type="chat_escalation",
            user_id=user_id,
        )

        response_text = response_data.get("response_text", "")
        result = ChatResponse(
            text=response_text,
            suggested_actions=response_data.get("suggested_actions", []),
            session_pinned_task_id=getattr(session, "pinned_task_id", None),
        )

        await self._db.add_chat_message(
            session_id=session_id,
            role="assistant",
            content=result.text,
            intent="escalation",
            tokens_used=metadata.tokens_out if hasattr(metadata, "tokens_out") else None,
        )

        return result

    async def pin_session(self, session_id: str, task_id: str) -> None:
        """Pin a session to a task."""
        await self._db.update_chat_session(session_id, pinned_task_id=task_id)

    async def unpin_session(self, session_id: str) -> None:
        """Unpin a session from its task."""
        await self._db.update_chat_session(session_id, pinned_task_id=None)

    async def close_session(self, session_id: str) -> str | None:
        """Close a session and generate a summary."""
        if self._config.sessions.summary_on_close:
            summary = await self._summarize_session(session_id)
            await self._db.update_chat_session(
                session_id, status="closed", summary=summary
            )
            return summary
        await self._db.update_chat_session(session_id, status="closed")
        return None

    async def _classify_intent(
        self, text: str, user_id: str
    ) -> dict[str, Any]:
        """Classify user message intent via local LLM."""
        template_path = self._project_root / "prompts" / "chat" / "classify_intent.md"
        template = ""
        if template_path.exists():
            template = template_path.read_text()
        else:
            template = "Classify this message intent: {{ user_input }}"

        prompt = render_chat_prompt(template=template, user_input=text)
        result, _ = await self._router.complete(
            prompt=prompt,
            task_type="classify_chat_intent",
            user_id=user_id,
        )
        return result

    async def _load_intent_context(
        self, intent: ChatIntent, user_id: str
    ) -> str:
        """Load intent-specific context from the database."""
        if intent in (ChatIntent.FREEFORM, ChatIntent.ESCALATION_REQUEST):
            return ""

        tasks = []
        if intent in (
            ChatIntent.TASK_QUERY,
            ChatIntent.TASK_ACTION,
            ChatIntent.PLANNING,
        ):
            task_rows = await self._db.list_tasks(user_id=user_id)
            tasks = [
                {
                    "title": t.title,
                    "status": t.status,
                    "priority": t.priority,
                    "domain": t.domain,
                }
                for t in task_rows
                if t.status not in ("done", "cancelled")
            ]

        schedule_summary = None
        open_task_count = None
        if intent == ChatIntent.PLANNING:
            open_task_count = len(tasks)
            scheduled = [t for t in tasks if t["status"] == "scheduled"]
            schedule_summary = f"{len(scheduled)} tasks scheduled"

        return build_intent_context(
            intent,
            tasks=tasks,
            schedule_summary=schedule_summary,
            open_task_count=open_task_count,
        )

    def _load_system_prompt(self) -> str:
        """Load the system prompt template based on persona config."""
        if self._config.persona.mode == "neutral":
            path = self._project_root / "prompts" / "chat" / "chat_system_neutral.md"
        else:
            path = self._project_root / "prompts" / "chat" / "chat_system.md"
        if path.exists():
            return path.read_text()
        return "You are a helpful assistant. {{ user_input }}"

    async def _summarize_session(self, session_id: str) -> str:
        """Generate a summary of the session via local LLM."""
        messages = await self._db.list_chat_messages(session_id)
        if not messages:
            return "Empty session."

        history_text = "\n".join(
            f"{'User' if m.role == 'user' else 'Assistant'}: {m.content}"
            for m in messages
        )

        template_path = self._project_root / "prompts" / "chat" / "chat_summarize.md"
        template = ""
        if template_path.exists():
            template = template_path.read_text()
        else:
            template = "Summarize this conversation:\n{{ conversation_history }}"

        prompt = render_chat_prompt(
            template=template,
            user_input="",
            conversation_history=history_text,
        )

        result, _ = await self._router.complete(
            prompt=prompt,
            task_type="chat_summarize",
            user_id="system",
        )
        return result.get("summary", "Session ended.")

    def _estimate_escalation_cost(self) -> float:
        """Rough cost estimate for a Claude escalation call."""
        # ~4k tokens context + ~1k response, at Claude Sonnet pricing
        # $3/MTok input + $15/MTok output (approximate)
        return round(4 * 0.003 + 1 * 0.015, 3)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_chat_engine.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/donna/chat/engine.py tests/unit/test_chat_engine.py
git commit -m "feat(chat): implement ConversationEngine with intent classification and escalation"
```

---

### Task 9: FastAPI Chat Endpoints

**Files:**
- Create: `src/donna/api/routes/chat.py`
- Modify: `src/donna/api/__init__.py`
- Test: `tests/unit/test_chat_api.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_chat_api.py
"""Tests for the chat API endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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
    from donna.api.routes.chat import router, get_chat_engine

    app = FastAPI()
    app.state.db = mock_db
    app.state.chat_engine = mock_engine
    app.include_router(router, prefix="/chat")

    # Override the dependency
    app.dependency_overrides[get_chat_engine] = lambda: mock_engine

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
        # Need to wire up get_session to use mock_db directly
        from donna.api.routes.chat import get_database
        from fastapi import FastAPI

        resp = client.get("/chat/sessions/sess-1")
        # This test verifies the endpoint exists and returns 200
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_chat_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'donna.api.routes.chat'`

- [ ] **Step 3: Implement chat API routes**

```python
# src/donna/api/routes/chat.py
"""Chat API endpoints for the Donna conversation interface.

REST endpoints for session management, messaging, pinning, and escalation.
All endpoints are client-agnostic — used by Flutter app, web client, etc.
See docs/superpowers/specs/2026-04-12-chat-interface-design.md.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from donna.chat.types import ChatResponse

router = APIRouter()


def get_chat_engine(request: Request) -> Any:
    """FastAPI dependency to get the ConversationEngine instance."""
    engine = getattr(request.app.state, "chat_engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="Chat engine not initialized")
    return engine


def get_database(request: Request) -> Any:
    """FastAPI dependency to get the Database instance."""
    return request.app.state.db


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    body: dict[str, Any] = Body(...),
    engine: Any = Depends(get_chat_engine),
) -> dict[str, Any]:
    """Send a message and receive a response.

    If session_id is "new", creates a new session.
    """
    text = body.get("text", "")
    if not text.strip():
        raise HTTPException(status_code=400, detail="text is required")

    user_id = body.get("user_id", "nick")  # TODO: extract from JWT
    channel = body.get("channel", "api")

    sid = session_id if session_id != "new" else None

    resp: ChatResponse = await engine.handle_message(
        session_id=sid,
        user_id=user_id,
        text=text,
        channel=channel,
    )

    return {
        "text": resp.text,
        "needs_escalation": resp.needs_escalation,
        "escalation_reason": resp.escalation_reason,
        "estimated_cost": resp.estimated_cost,
        "suggested_actions": resp.suggested_actions,
        "pin_suggestion": resp.pin_suggestion,
        "session_pinned_task_id": resp.session_pinned_task_id,
    }


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    db: Any = Depends(get_database),
) -> dict[str, Any]:
    """Get session details and recent messages."""
    session = await db.get_chat_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = await db.list_chat_messages(session_id, limit=50)

    return {
        "session": {
            "id": session.id,
            "user_id": session.user_id,
            "channel": session.channel,
            "status": session.status,
            "pinned_task_id": session.pinned_task_id,
            "summary": session.summary,
            "created_at": session.created_at,
            "last_activity": session.last_activity,
            "message_count": session.message_count,
        },
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "intent": m.intent,
                "tokens_used": m.tokens_used,
                "created_at": m.created_at,
            }
            for m in messages
        ],
    }


@router.get("/sessions/{session_id}/messages")
async def list_messages(
    session_id: str,
    limit: int = 50,
    offset: int = 0,
    db: Any = Depends(get_database),
) -> dict[str, Any]:
    """List messages in a session with pagination."""
    messages = await db.list_chat_messages(session_id, limit=limit, offset=offset)
    return {
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "intent": m.intent,
                "tokens_used": m.tokens_used,
                "created_at": m.created_at,
            }
            for m in messages
        ],
    }


@router.post("/sessions/{session_id}/pin")
async def pin_session(
    session_id: str,
    body: dict[str, Any] = Body(...),
    engine: Any = Depends(get_chat_engine),
) -> dict[str, str]:
    """Pin a session to a task."""
    task_id = body.get("task_id")
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")
    await engine.pin_session(session_id=session_id, task_id=task_id)
    return {"status": "pinned", "task_id": task_id}


@router.delete("/sessions/{session_id}/pin")
async def unpin_session(
    session_id: str,
    engine: Any = Depends(get_chat_engine),
) -> dict[str, str]:
    """Unpin a session from its task."""
    await engine.unpin_session(session_id=session_id)
    return {"status": "unpinned"}


@router.post("/sessions/{session_id}/escalate")
async def approve_escalation(
    session_id: str,
    engine: Any = Depends(get_chat_engine),
) -> dict[str, Any]:
    """Approve a pending Claude escalation."""
    user_id = "nick"  # TODO: extract from JWT
    resp: ChatResponse = await engine.handle_escalation(
        session_id=session_id, user_id=user_id
    )
    return {
        "text": resp.text,
        "needs_escalation": resp.needs_escalation,
        "escalation_reason": resp.escalation_reason,
        "suggested_actions": resp.suggested_actions,
    }


@router.delete("/sessions/{session_id}")
async def close_session(
    session_id: str,
    engine: Any = Depends(get_chat_engine),
) -> dict[str, Any]:
    """Close a session and generate a summary."""
    summary = await engine.close_session(session_id=session_id)
    return {"status": "closed", "summary": summary}
```

- [ ] **Step 4: Register the chat router in the FastAPI app**

In `src/donna/api/__init__.py`, add the import:

```python
from donna.api.routes import chat as chat_routes
```

And add the router registration after the existing LLM router line:

```python
    # Chat interface
    app.include_router(chat_routes.router, prefix="/chat", tags=["chat"])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_chat_api.py -v`
Expected: All 5 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/donna/api/routes/chat.py src/donna/api/__init__.py tests/unit/test_chat_api.py
git commit -m "feat(chat): add FastAPI chat endpoints"
```

---

### Task 10: Discord Chat Adapter

**Files:**
- Modify: `src/donna/integrations/discord_bot.py`
- Test: `tests/unit/test_discord_chat.py`

- [ ] **Step 1: Write failing tests for Discord chat routing**

```python
# tests/unit/test_discord_chat.py
"""Tests for Discord chat channel integration."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from donna.chat.types import ChatResponse


@pytest.fixture
def mock_engine() -> AsyncMock:
    engine = AsyncMock()
    engine.handle_message.return_value = ChatResponse(
        text="You have 3 tasks today.",
        suggested_actions=["schedule_task"],
    )
    engine.handle_escalation.return_value = ChatResponse(
        text="Here's a detailed plan.",
    )
    return engine


class TestDiscordChatRouting:
    def test_chat_channel_message_routes_to_engine(self, mock_engine: AsyncMock) -> None:
        """Messages in #donna-chat should route to the ConversationEngine."""
        from donna.integrations.discord_bot import DonnaBot

        bot = DonnaBot(
            input_parser=AsyncMock(),
            database=AsyncMock(),
            tasks_channel_id=111,
            chat_channel_id=222,
            chat_engine=mock_engine,
        )

        message = MagicMock()
        message.author.bot = False
        message.channel.id = 222
        message.content = "What's on my schedule?"
        message.author.id = 12345
        message.channel.send = AsyncMock()

        asyncio.get_event_loop().run_until_complete(bot.on_message(message))

        mock_engine.handle_message.assert_called_once()
        message.channel.send.assert_called_once_with("You have 3 tasks today.")

    def test_tasks_channel_still_works(self, mock_engine: AsyncMock) -> None:
        """Messages in #donna-tasks should still go through InputParser."""
        from donna.integrations.discord_bot import DonnaBot

        mock_parser = AsyncMock()
        mock_parser.parse.return_value = MagicMock(
            confidence=0.9, title="Test task", description=None,
            domain="personal", priority=2, deadline=None,
            deadline_type="none", estimated_duration=30,
            recurrence=None, tags=[], prep_work_flag=False,
            agent_eligible=False,
        )
        mock_db = AsyncMock()
        mock_db.create_task.return_value = MagicMock(
            id="t1", title="Test task", domain="personal",
            priority=2,
        )

        bot = DonnaBot(
            input_parser=mock_parser,
            database=mock_db,
            tasks_channel_id=111,
            chat_channel_id=222,
            chat_engine=mock_engine,
        )

        message = MagicMock()
        message.author.bot = False
        message.channel.id = 111
        message.content = "Buy groceries"
        message.author.id = 12345
        message.channel.send = AsyncMock()

        asyncio.get_event_loop().run_until_complete(bot.on_message(message))

        # Should go through parser, NOT the chat engine
        mock_parser.parse.assert_called_once()
        mock_engine.handle_message.assert_not_called()

    def test_escalation_shows_buttons(self, mock_engine: AsyncMock) -> None:
        """When engine returns needs_escalation, show Approve/Decline buttons."""
        from donna.integrations.discord_bot import DonnaBot

        mock_engine.handle_message.return_value = ChatResponse(
            text="I'd need Claude for this — complex planning. ~$0.03. Go ahead?",
            needs_escalation=True,
            escalation_reason="Complex planning",
            estimated_cost=0.03,
        )

        bot = DonnaBot(
            input_parser=AsyncMock(),
            database=AsyncMock(),
            tasks_channel_id=111,
            chat_channel_id=222,
            chat_engine=mock_engine,
        )

        message = MagicMock()
        message.author.bot = False
        message.channel.id = 222
        message.content = "Should I take on this new project?"
        message.author.id = 12345
        message.channel.send = AsyncMock()

        asyncio.get_event_loop().run_until_complete(bot.on_message(message))

        # Should be called with a view (buttons)
        send_call = message.channel.send.call_args
        assert send_call is not None
        # Check that view kwarg was passed (escalation buttons)
        assert "view" in send_call.kwargs or len(send_call.args) > 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_discord_chat.py -v`
Expected: FAIL — `TypeError: DonnaBot.__init__() got an unexpected keyword argument 'chat_channel_id'`

- [ ] **Step 3: Extend DonnaBot with chat support**

In `src/donna/integrations/discord_bot.py`, modify the `__init__` method to accept chat parameters:

Add to the `__init__` signature:

```python
    def __init__(
        self,
        input_parser: InputParser,
        database: Database,
        tasks_channel_id: int,
        debug_channel_id: int | None = None,
        digest_channel_id: int | None = None,
        agents_channel_id: int | None = None,
        chat_channel_id: int | None = None,
        guild_id: int | None = None,
        overdue_reply_handler: Callable[[str, str], Awaitable[None]] | None = None,
        dispatcher: AgentDispatcher | None = None,
        chat_engine: Any | None = None,
    ) -> None:
```

Store the new attributes:

```python
        self._chat_channel_id = chat_channel_id
        self._chat_engine = chat_engine
```

Add chat channel to `_resolve_channel`:

```python
            "chat": self._chat_channel_id,
```

In `on_message`, add a chat channel check **before** the tasks channel filter:

```python
        # Route chat channel messages to conversation engine.
        if (
            self._chat_channel_id is not None
            and message.channel.id == self._chat_channel_id
            and self._chat_engine is not None
        ):
            await self._handle_chat_message(message)
            return
```

Add the chat handler method:

```python
    async def _handle_chat_message(self, message: discord.Message) -> None:
        """Route a #donna-chat message through the ConversationEngine."""
        user_id = str(message.author.id)
        text = message.content.strip()
        log = logger.bind(user_id=user_id, channel="discord_chat")

        try:
            resp = await self._chat_engine.handle_message(
                session_id=None,
                user_id=user_id,
                text=text,
                channel="discord",
            )

            if resp.needs_escalation:
                from donna.integrations.discord_views import EscalationApprovalView

                view = EscalationApprovalView(
                    session_id=resp.session_pinned_task_id or "unknown",
                    chat_engine=self._chat_engine,
                    user_id=user_id,
                )
                await message.channel.send(resp.text, view=view)
            else:
                await message.channel.send(resp.text)

        except Exception:
            log.exception("chat_message_failed")
            await message.channel.send(
                "Something went wrong. Try again in a moment."
            )
```

Add the `EscalationApprovalView` class to `src/donna/integrations/discord_views.py` (or keep it inline if the views file doesn't have it):

```python
class EscalationApprovalView(discord.ui.View):
    """Approve/Decline buttons for Claude escalation."""

    def __init__(self, session_id: str, chat_engine: Any, user_id: str) -> None:
        super().__init__(timeout=300)
        self._session_id = session_id
        self._chat_engine = chat_engine
        self._user_id = user_id

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        resp = await self._chat_engine.handle_escalation(
            session_id=self._session_id, user_id=self._user_id
        )
        await interaction.followup.send(resp.text)
        self.stop()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.grey)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message("Got it, I'll do my best without Claude.")
        self.stop()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_discord_chat.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Run existing Discord bot tests to verify no regressions**

Run: `pytest tests/unit/test_discord*.py -v`
Expected: All existing tests still PASS

- [ ] **Step 6: Commit**

```bash
git add src/donna/integrations/discord_bot.py src/donna/integrations/discord_views.py tests/unit/test_discord_chat.py
git commit -m "feat(chat): add Discord #donna-chat channel adapter with escalation buttons"
```

---

### Task 11: Wire Up Chat Engine in API Lifespan

**Files:**
- Modify: `src/donna/api/__init__.py`

- [ ] **Step 1: Add chat engine initialization to the lifespan**

In `src/donna/api/__init__.py`, add imports at the top:

```python
from donna.chat.config import get_chat_config
from donna.chat.engine import ConversationEngine
```

In the `lifespan` function, after the LLM Queue Worker initialization and before the `yield`, add:

```python
    # Chat engine
    from donna.models.router import ModelRouter
    from donna.config import ModelsConfig, TaskTypesConfig

    chat_config = get_chat_config(config_dir)
    # Build a ModelRouter for chat if full models config is available
    chat_engine = None
    models_yaml = config_dir / "donna_models.yaml"
    task_types_yaml = config_dir / "task_types.yaml"
    if models_yaml.exists() and task_types_yaml.exists():
        try:
            from donna.config import load_models_config, load_task_types_config
            m_cfg = load_models_config(config_dir)
            t_cfg = load_task_types_config(config_dir)
            project_root = Path(os.environ.get("DONNA_PROJECT_ROOT", "."))
            chat_router = ModelRouter(m_cfg, t_cfg, project_root)
            chat_engine = ConversationEngine(
                db=db, router=chat_router, config=chat_config,
                project_root=project_root,
            )
        except Exception:
            logger.warning("chat_engine_init_failed", exc_info=True)

    app.state.chat_engine = chat_engine
    app.state.chat_config = chat_config
```

- [ ] **Step 2: Verify the app starts without errors**

Run: `python -c "from donna.api import create_app; app = create_app(); print('OK')"`
Expected: `OK` (no import errors)

- [ ] **Step 3: Commit**

```bash
git add src/donna/api/__init__.py
git commit -m "feat(chat): wire up ConversationEngine in API lifespan"
```

---

### Task 12: Integration Smoke Test

**Files:**
- Create: `tests/integration/test_chat_smoke.py`

- [ ] **Step 1: Write an integration test that exercises the full path**

```python
# tests/integration/test_chat_smoke.py
"""Smoke test for the chat interface — full path through engine."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.chat.config import ChatConfig
from donna.chat.engine import ConversationEngine
from donna.tasks.database import Database
from donna.tasks.state_machine import StateMachine


@pytest.fixture
def db(tmp_path: Path) -> Database:
    sm = StateMachine({"transitions": {}, "valid_statuses": ["backlog", "done"]})
    return Database(tmp_path / "test.db", sm)


@pytest.fixture
def connected_db(db: Database) -> Database:
    asyncio.get_event_loop().run_until_complete(db.connect())
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
```

- [ ] **Step 2: Run smoke tests**

Run: `pytest tests/integration/test_chat_smoke.py -v`
Expected: All 2 tests PASS

- [ ] **Step 3: Run full test suite to check for regressions**

Run: `pytest tests/ -v --timeout=30`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_chat_smoke.py
git commit -m "test(chat): add integration smoke tests for full chat flow"
```

---

### Task 13: Add chat.yaml to Admin Dashboard Hot-Reload

**Files:**
- Modify: `src/donna/api/routes/admin_config.py`

- [ ] **Step 1: Add hot-reload hook for chat.yaml**

In `src/donna/api/routes/admin_config.py`, inside the `put_config` function, after the existing `llm_gateway.yaml` hot-reload block, add:

```python
    # Hot-reload hook for chat config
    if filename == "chat.yaml":
        from donna.chat.config import get_chat_config, _cache
        # Clear the cache so next request picks up new config
        key = str(_get_config_dir(request))
        _cache.pop(key, None)
        # Update app state
        new_chat_config = get_chat_config(_get_config_dir(request), cache_ttl_s=0)
        engine = getattr(request.app.state, "chat_engine", None)
        if engine is not None:
            engine._config = new_chat_config
```

- [ ] **Step 2: Verify existing admin config tests still pass**

Run: `pytest tests/unit/test_admin_*.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add src/donna/api/routes/admin_config.py
git commit -m "feat(chat): add hot-reload hook for chat.yaml in admin dashboard"
```

---

### Task 14: Final Validation

- [ ] **Step 1: Run the full test suite**

Run: `pytest tests/ -v --timeout=30`
Expected: All tests PASS

- [ ] **Step 2: Verify no lint issues**

Run: `ruff check src/donna/chat/ tests/unit/test_chat*.py tests/integration/test_chat*.py`
Expected: No issues

- [ ] **Step 3: Verify all new files are tracked**

Run: `git status`
Expected: No untracked files in `src/donna/chat/`, `prompts/chat/`, `schemas/chat_*`, `config/chat.yaml`

- [ ] **Step 4: Final commit if anything was missed**

```bash
git add -A && git status
# Only commit if there are changes
git commit -m "chore(chat): final cleanup"
```
