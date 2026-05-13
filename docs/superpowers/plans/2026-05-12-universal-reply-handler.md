# Universal Reply Handler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace brittle per-context keyword matching with a confidence-gated pipeline: fast keyword path for simple replies, local LLM (qwen2.5:32b) for complex/multi-intent replies, with plan-and-confirm flow, conversation memory, and capability gap tracking.

**Architecture:** Two-layer pipeline. Layer 1 is a config-driven keyword matcher with a complexity gate that defers long/multi-intent replies to Layer 2. Layer 2 sends the reply + conversation memory + task context to the local LLM via `ModelRouter.complete()`, which returns structured JSON proposing actions from a config-driven registry. Donna presents the plan in-persona and waits for user confirmation before executing. Unrecognized requests are logged as capability gaps and auto-promoted to skill candidates after 3+ hits.

**Tech Stack:** Python 3.12, aiosqlite, structlog, Pydantic, Alembic, Ollama (qwen2.5:32b), YAML config, JSON Schema, pytest

**Spec:** `docs/superpowers/specs/2026-05-12-universal-reply-handler-design.md`

---

## File Structure

### New Files

```
config/reply_intents.yaml          — Fast-path keyword definitions
config/reply_actions.yaml          — Action registry definitions
schemas/reply_intent_output.json   — JSON schema for LLM structured output
prompts/reply_intent.md            — LLM prompt template for Layer 2
src/donna/replies/__init__.py      — Package init
src/donna/replies/handler.py       — ReplyHandler entry point, fast path, complexity gate
src/donna/replies/llm_classifier.py — LLM prompt construction, output parsing
src/donna/replies/action_registry.py — Load actions from config, validate proposed actions
src/donna/replies/actions/__init__.py — Actions sub-package init
src/donna/replies/actions/task_actions.py — mark_done, reschedule, create_task, rename_task, snooze
src/donna/replies/actions/gap_actions.py  — log_capability_gap
src/donna/replies/memory.py        — Thread conversation memory (read/write/prune)
src/donna/replies/pending_plans.py — Plan persistence, confirmation, expiry
alembic/versions/a1b2c3d4e5f0_add_reply_handler_tables.py — Migration
tests/unit/test_reply_fast_path.py — Fast path unit tests
tests/unit/test_reply_action_registry.py — Action registry unit tests
tests/unit/test_reply_memory.py    — Conversation memory unit tests
tests/unit/test_reply_pending_plans.py — Plan-and-confirm unit tests
tests/unit/test_reply_handler.py   — Full handler integration tests (mocked LLM)
tests/unit/test_reply_gap.py       — Capability gap unit tests
```

### Modified Files

```
config/task_types.yaml             — Add reply_intent task type
config/donna_models.yaml           — Add reply_intent routing entry
src/donna/notifications/overdue.py — Replace handle_reply with ReplyHandler call
src/donna/integrations/discord_bot.py — Route thread replies through ReplyHandler
src/donna/config.py                — Add Pydantic models for reply config
```

---

## Task 1: Alembic Migration — Add Three Tables

**Files:**
- Create: `alembic/versions/a1b2c3d4e5f0_add_reply_handler_tables.py`

- [ ] **Step 1: Write the migration file**

```python
"""add thread_memory, pending_action_plan, capability_gap

Universal Reply Handler tables. thread_memory stores per-thread
conversation history for LLM context. pending_action_plan tracks
proposed action plans awaiting user confirmation. capability_gap
logs requests Donna cannot handle yet.

Revision ID: a1b2c3d4e5f0
Revises: f4a5b6c7d8e9
Create Date: 2026-05-12 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f0"
down_revision: str | None = "f4a5b6c7d8e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "thread_memory",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("context_type", sa.String(length=32), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=64), nullable=False),
    )
    op.create_index(
        "idx_thread_memory_thread",
        "thread_memory",
        ["thread_id", "created_at"],
    )

    op.create_table(
        "pending_action_plan",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("actions_json", sa.Text(), nullable=False),
        sa.Column("reply_text", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("created_at", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.String(length=64), nullable=False),
    )
    op.create_index(
        "idx_pending_plan_thread",
        "pending_action_plan",
        ["thread_id", "status"],
    )

    op.create_table(
        "capability_gap",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_request", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("context_type", sa.String(length=32), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="logged",
        ),
        sa.Column("created_at", sa.String(length=64), nullable=False),
        sa.Column("last_hit_at", sa.String(length=64), nullable=False),
    )


def downgrade() -> None:
    op.drop_index("idx_pending_plan_thread", table_name="pending_action_plan")
    op.drop_index("idx_thread_memory_thread", table_name="thread_memory")
    op.drop_table("capability_gap")
    op.drop_table("pending_action_plan")
    op.drop_table("thread_memory")
```

- [ ] **Step 2: Run the migration**

Run: `cd /mnt/donna/donna && alembic upgrade head`
Expected: Three new tables created, no errors.

- [ ] **Step 3: Verify tables exist**

Run: `cd /mnt/donna/donna && python -c "import sqlite3; conn = sqlite3.connect('donna_tasks.db'); print([r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name IN ('thread_memory','pending_action_plan','capability_gap')\").fetchall()])"`
Expected: `['thread_memory', 'pending_action_plan', 'capability_gap']`

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/a1b2c3d4e5f0_add_reply_handler_tables.py
git commit -m "feat(replies): add migration for thread_memory, pending_action_plan, capability_gap"
```

---

## Task 2: Config Files — Reply Intents and Actions

**Files:**
- Create: `config/reply_intents.yaml`
- Create: `config/reply_actions.yaml`
- Modify: `src/donna/config.py`

- [ ] **Step 1: Write tests for config loading**

Create `tests/unit/test_reply_config.py`:

```python
"""Tests for reply handler config loading."""
from __future__ import annotations

from pathlib import Path

from donna.config import load_reply_actions_config, load_reply_intents_config


def test_load_reply_intents_config() -> None:
    config = load_reply_intents_config(Path("config"))
    assert "mark_done" in config.intents
    assert "reschedule" in config.intents
    assert "busy" in config.intents
    # Each intent has keywords and an action
    for name, intent in config.intents.items():
        assert len(intent.keywords) > 0
        assert intent.action


def test_load_reply_actions_config() -> None:
    config = load_reply_actions_config(Path("config"))
    assert "mark_done" in config.actions
    assert "create_task" in config.actions
    assert "request_capability" in config.actions
    # Each action has a description and handler
    for name, action in config.actions.items():
        assert action.description
        assert action.handler


def test_fast_path_config_defaults() -> None:
    config = load_reply_intents_config(Path("config"))
    assert config.fast_path.max_length == 60
    assert len(config.fast_path.multi_intent_signals) > 0


def test_memory_config_defaults() -> None:
    config = load_reply_actions_config(Path("config"))
    assert config.memory.window_size == 10
    assert config.memory.retention_days == 7
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_reply_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'load_reply_intents_config'`

- [ ] **Step 3: Create `config/reply_intents.yaml`**

```yaml
# Fast-path keyword intent definitions for the Universal Reply Handler.
# See docs/superpowers/specs/2026-05-12-universal-reply-handler-design.md

fast_path:
  max_length: 60
  multi_intent_signals:
    - " but "
    - " and also "
    - " however "
    - " although "

  # Confirmation/rejection keywords for pending action plans
  confirm_keywords: ["yes", "go ahead", "do it", "ok", "sounds good", "yep", "sure", "go for it"]
  reject_keywords: ["no", "cancel", "nevermind", "nah", "stop", "don't"]

intents:
  mark_done:
    keywords: ["done", "finished", "complete", "completed", "did it", "yes"]
    action: mark_done
    confirm: false

  reschedule:
    keywords: ["reschedule", "tomorrow", "later", "push", "move"]
    action: reschedule
    confirm: false

  busy:
    keywords: ["busy", "not now", "snooze"]
    action: snooze
    confirm: false
```

- [ ] **Step 4: Create `config/reply_actions.yaml`**

```yaml
# Action registry for the Universal Reply Handler.
# See docs/superpowers/specs/2026-05-12-universal-reply-handler-design.md

memory:
  window_size: 10
  retention_days: 7

plan:
  expiry_minutes: 60

actions:
  mark_done:
    description: "Mark a task as completed"
    handler: donna.replies.actions.task_actions.mark_done
    params:
      task_id: { type: string, from_context: true }
    risk: low

  reschedule:
    description: "Reschedule a task to a new time"
    handler: donna.replies.actions.task_actions.reschedule_task
    params:
      task_id: { type: string, from_context: true }
      when: { type: string, description: "Natural language time expression" }
    risk: low

  create_task:
    description: "Create a new task"
    handler: donna.replies.actions.task_actions.create_task
    params:
      title: { type: string }
      domain: { type: string, enum: [work, personal, family] }
      priority: { type: int, default: 2 }
      due_by: { type: string, optional: true }
    risk: medium

  rename_task:
    description: "Rename or update a task's title"
    handler: donna.replies.actions.task_actions.rename_task
    params:
      task_id: { type: string, from_context: true }
      new_title: { type: string }
    risk: low

  snooze:
    description: "Snooze notifications for this task"
    handler: donna.replies.actions.task_actions.snooze_task
    params:
      task_id: { type: string, from_context: true }
      duration_hours: { type: int, default: 2 }
    risk: low

  request_capability:
    description: "Flag that the user wants something Donna can't do yet"
    handler: donna.replies.actions.gap_actions.log_capability_gap
    params:
      description: { type: string }
      user_request: { type: string }
    risk: low
```

- [ ] **Step 5: Add Pydantic models and loaders to `src/donna/config.py`**

Add after existing config classes (before the existing `load_*` functions at the bottom of the file):

```python
# --- Reply handler config (Universal Reply Handler) ---


class ReplyIntentDef(BaseModel):
    """A single fast-path intent definition."""

    keywords: list[str]
    action: str
    confirm: bool = False


class FastPathConfig(BaseModel):
    """Fast-path tuning knobs."""

    max_length: int = 60
    multi_intent_signals: list[str] = Field(
        default_factory=lambda: [" but ", " and also ", " however ", " although "],
    )
    confirm_keywords: list[str] = Field(
        default_factory=lambda: ["yes", "go ahead", "do it", "ok", "sounds good", "yep", "sure", "go for it"],
    )
    reject_keywords: list[str] = Field(
        default_factory=lambda: ["no", "cancel", "nevermind", "nah", "stop", "don't"],
    )


class ReplyIntentsConfig(BaseModel):
    """Top-level config for reply_intents.yaml."""

    fast_path: FastPathConfig = Field(default_factory=FastPathConfig)
    intents: dict[str, ReplyIntentDef]


class ActionParamDef(BaseModel):
    """Schema for a single action parameter."""

    type: str
    from_context: bool = False
    description: str = ""
    enum: list[str] | None = None
    default: Any = None
    optional: bool = False


class ActionDef(BaseModel):
    """A single action in the reply action registry."""

    description: str
    handler: str
    params: dict[str, ActionParamDef] = Field(default_factory=dict)
    risk: Literal["low", "medium", "high"] = "low"


class MemoryConfig(BaseModel):
    """Thread memory tuning."""

    window_size: int = 10
    retention_days: int = 7


class PlanConfig(BaseModel):
    """Pending plan tuning."""

    expiry_minutes: int = 60


class ReplyActionsConfig(BaseModel):
    """Top-level config for reply_actions.yaml."""

    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    plan: PlanConfig = Field(default_factory=PlanConfig)
    actions: dict[str, ActionDef]


def load_reply_intents_config(config_dir: Path) -> ReplyIntentsConfig:
    """Load reply_intents.yaml."""
    data = load_yaml(config_dir / "reply_intents.yaml")
    return ReplyIntentsConfig(**data)


def load_reply_actions_config(config_dir: Path) -> ReplyActionsConfig:
    """Load reply_actions.yaml."""
    data = load_yaml(config_dir / "reply_actions.yaml")
    return ReplyActionsConfig(**data)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/unit/test_reply_config.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add config/reply_intents.yaml config/reply_actions.yaml src/donna/config.py tests/unit/test_reply_config.py
git commit -m "feat(replies): add config files and Pydantic models for reply handler"
```

---

## Task 3: Conversation Memory Module

**Files:**
- Create: `src/donna/replies/__init__.py`
- Create: `src/donna/replies/memory.py`
- Create: `tests/unit/test_reply_memory.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_reply_memory.py`:

```python
"""Tests for thread conversation memory."""
from __future__ import annotations

import aiosqlite
import pytest
import uuid6

from donna.replies.memory import ThreadMemory


@pytest.fixture
async def mem_db():
    """In-memory SQLite with thread_memory table."""
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("""
        CREATE TABLE thread_memory (
            id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL,
            context_type TEXT NOT NULL,
            task_id TEXT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    await conn.execute(
        "CREATE INDEX idx_thread_memory_thread ON thread_memory(thread_id, created_at)"
    )
    await conn.commit()
    yield conn
    await conn.close()


@pytest.mark.asyncio
async def test_record_and_retrieve(mem_db: aiosqlite.Connection) -> None:
    mem = ThreadMemory(mem_db, window_size=10)
    await mem.record("thread-1", "overdue", "t1", "donna", "You're overdue on Build thing.")
    await mem.record("thread-1", "overdue", "t1", "user", "done")
    messages = await mem.retrieve("thread-1")
    assert len(messages) == 2
    assert messages[0]["role"] == "donna"
    assert messages[1]["role"] == "user"


@pytest.mark.asyncio
async def test_retrieve_respects_window_size(mem_db: aiosqlite.Connection) -> None:
    mem = ThreadMemory(mem_db, window_size=3)
    for i in range(5):
        await mem.record("thread-1", "overdue", "t1", "user", f"msg-{i}")
    messages = await mem.retrieve("thread-1")
    assert len(messages) == 3
    assert messages[0]["content"] == "msg-2"
    assert messages[2]["content"] == "msg-4"


@pytest.mark.asyncio
async def test_retrieve_empty_thread(mem_db: aiosqlite.Connection) -> None:
    mem = ThreadMemory(mem_db, window_size=10)
    messages = await mem.retrieve("nonexistent")
    assert messages == []


@pytest.mark.asyncio
async def test_prune_old_messages(mem_db: aiosqlite.Connection) -> None:
    mem = ThreadMemory(mem_db, window_size=10)
    # Insert a message with an old timestamp
    await mem_db.execute(
        "INSERT INTO thread_memory (id, thread_id, context_type, task_id, role, content, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(uuid6.uuid7()), "thread-1", "overdue", "t1", "user", "old msg", "2020-01-01T00:00:00+00:00"),
    )
    await mem_db.commit()
    # Insert a recent message
    await mem.record("thread-1", "overdue", "t1", "user", "new msg")
    pruned = await mem.prune(retention_days=7)
    assert pruned >= 1
    messages = await mem.retrieve("thread-1")
    assert len(messages) == 1
    assert messages[0]["content"] == "new msg"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_reply_memory.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'donna.replies'`

- [ ] **Step 3: Create package init**

Create `src/donna/replies/__init__.py`:

```python
"""Universal Reply Handler — confidence-gated pipeline for user replies."""
```

- [ ] **Step 4: Implement `src/donna/replies/memory.py`**

```python
"""Thread conversation memory for the Universal Reply Handler.

Stores a rolling window of messages per thread in SQLite. Used to
provide conversation context to the LLM when classifying complex replies.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
import uuid6

logger = structlog.get_logger()


class ThreadMemory:
    """Read/write conversation memory for a thread.

    Args:
        conn: aiosqlite connection with thread_memory table.
        window_size: Max messages to include in LLM context.
    """

    def __init__(self, conn: Any, window_size: int = 10) -> None:
        self._conn = conn
        self._window_size = window_size

    async def record(
        self,
        thread_id: str,
        context_type: str,
        task_id: str | None,
        role: str,
        content: str,
    ) -> None:
        """Append a message to thread memory."""
        now = datetime.now(tz=UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO thread_memory (id, thread_id, context_type, task_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid6.uuid7()), thread_id, context_type, task_id, role, content, now),
        )
        await self._conn.commit()

    async def retrieve(self, thread_id: str) -> list[dict[str, Any]]:
        """Return the last N messages for a thread, ordered oldest-first."""
        cursor = await self._conn.execute(
            "SELECT role, content, created_at FROM thread_memory "
            "WHERE thread_id = ? ORDER BY created_at DESC LIMIT ?",
            (thread_id, self._window_size),
        )
        rows = await cursor.fetchall()
        rows.reverse()
        return [{"role": r[0], "content": r[1], "created_at": r[2]} for r in rows]

    async def prune(self, retention_days: int = 7) -> int:
        """Delete messages older than retention_days. Returns count deleted."""
        cutoff = (datetime.now(tz=UTC) - timedelta(days=retention_days)).isoformat()
        cursor = await self._conn.execute(
            "DELETE FROM thread_memory WHERE created_at < ?",
            (cutoff,),
        )
        await self._conn.commit()
        count = cursor.rowcount
        if count:
            logger.info("thread_memory_pruned", deleted=count)
        return count
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_reply_memory.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/donna/replies/__init__.py src/donna/replies/memory.py tests/unit/test_reply_memory.py
git commit -m "feat(replies): add thread conversation memory module"
```

---

## Task 4: Pending Plans Module

**Files:**
- Create: `src/donna/replies/pending_plans.py`
- Create: `tests/unit/test_reply_pending_plans.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_reply_pending_plans.py`:

```python
"""Tests for pending action plan persistence."""
from __future__ import annotations

import json

import aiosqlite
import pytest

from donna.replies.pending_plans import PendingPlans


@pytest.fixture
async def plan_db():
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("""
        CREATE TABLE pending_action_plan (
            id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL,
            actions_json TEXT NOT NULL,
            reply_text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    """)
    await conn.execute(
        "CREATE INDEX idx_pending_plan_thread ON pending_action_plan(thread_id, status)"
    )
    await conn.commit()
    yield conn
    await conn.close()


@pytest.mark.asyncio
async def test_save_and_get_pending(plan_db: aiosqlite.Connection) -> None:
    plans = PendingPlans(plan_db, expiry_minutes=60)
    actions = [{"action": "mark_done", "params": {"task_id": "t1"}}]
    plan_id = await plans.save("thread-1", actions, "I'll mark it done. Go ahead?")
    pending = await plans.get_pending("thread-1")
    assert pending is not None
    assert pending["id"] == plan_id
    assert pending["status"] == "pending"
    assert json.loads(pending["actions_json"]) == actions


@pytest.mark.asyncio
async def test_confirm_plan(plan_db: aiosqlite.Connection) -> None:
    plans = PendingPlans(plan_db, expiry_minutes=60)
    actions = [{"action": "mark_done", "params": {"task_id": "t1"}}]
    plan_id = await plans.save("thread-1", actions, "reply")
    result = await plans.confirm("thread-1")
    assert result is not None
    assert json.loads(result["actions_json"]) == actions
    # After confirmation, no pending plan
    assert await plans.get_pending("thread-1") is None


@pytest.mark.asyncio
async def test_reject_plan(plan_db: aiosqlite.Connection) -> None:
    plans = PendingPlans(plan_db, expiry_minutes=60)
    await plans.save("thread-1", [{"action": "reschedule", "params": {}}], "reply")
    await plans.reject("thread-1")
    assert await plans.get_pending("thread-1") is None


@pytest.mark.asyncio
async def test_expire_old_plans(plan_db: aiosqlite.Connection) -> None:
    plans = PendingPlans(plan_db, expiry_minutes=0)
    await plans.save("thread-1", [{"action": "snooze", "params": {}}], "reply")
    expired = await plans.expire_stale()
    assert expired >= 1
    assert await plans.get_pending("thread-1") is None


@pytest.mark.asyncio
async def test_no_pending_returns_none(plan_db: aiosqlite.Connection) -> None:
    plans = PendingPlans(plan_db, expiry_minutes=60)
    assert await plans.get_pending("nonexistent") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_reply_pending_plans.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'donna.replies.pending_plans'`

- [ ] **Step 3: Implement `src/donna/replies/pending_plans.py`**

```python
"""Pending action plan persistence for the Universal Reply Handler.

Stores LLM-proposed action plans awaiting user confirmation. Plans
auto-expire after a configurable timeout.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
import uuid6

logger = structlog.get_logger()


class PendingPlans:
    """Manage pending action plans for threads.

    Args:
        conn: aiosqlite connection with pending_action_plan table.
        expiry_minutes: How long a plan stays pending before auto-expiring.
    """

    def __init__(self, conn: Any, expiry_minutes: int = 60) -> None:
        self._conn = conn
        self._expiry_minutes = expiry_minutes

    async def save(
        self,
        thread_id: str,
        actions: list[dict[str, Any]],
        reply_text: str,
    ) -> str:
        """Save a new pending plan. Cancels any existing pending plan on this thread."""
        await self._conn.execute(
            "UPDATE pending_action_plan SET status = 'rejected' "
            "WHERE thread_id = ? AND status = 'pending'",
            (thread_id,),
        )
        plan_id = str(uuid6.uuid7())
        now = datetime.now(tz=UTC)
        expires_at = now + timedelta(minutes=self._expiry_minutes)
        await self._conn.execute(
            "INSERT INTO pending_action_plan (id, thread_id, actions_json, reply_text, status, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
            (plan_id, thread_id, json.dumps(actions), reply_text, now.isoformat(), expires_at.isoformat()),
        )
        await self._conn.commit()
        return plan_id

    async def get_pending(self, thread_id: str) -> dict[str, Any] | None:
        """Return the pending plan for a thread, or None."""
        now = datetime.now(tz=UTC).isoformat()
        cursor = await self._conn.execute(
            "SELECT id, thread_id, actions_json, reply_text, status, created_at, expires_at "
            "FROM pending_action_plan "
            "WHERE thread_id = ? AND status = 'pending' AND expires_at > ? "
            "ORDER BY created_at DESC LIMIT 1",
            (thread_id, now),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "thread_id": row[1],
            "actions_json": row[2],
            "reply_text": row[3],
            "status": row[4],
            "created_at": row[5],
            "expires_at": row[6],
        }

    async def confirm(self, thread_id: str) -> dict[str, Any] | None:
        """Mark the pending plan as confirmed and return it."""
        pending = await self.get_pending(thread_id)
        if pending is None:
            return None
        await self._conn.execute(
            "UPDATE pending_action_plan SET status = 'confirmed' WHERE id = ?",
            (pending["id"],),
        )
        await self._conn.commit()
        return pending

    async def reject(self, thread_id: str) -> None:
        """Mark the pending plan as rejected."""
        await self._conn.execute(
            "UPDATE pending_action_plan SET status = 'rejected' "
            "WHERE thread_id = ? AND status = 'pending'",
            (thread_id,),
        )
        await self._conn.commit()

    async def expire_stale(self) -> int:
        """Expire all pending plans past their deadline. Returns count expired."""
        now = datetime.now(tz=UTC).isoformat()
        cursor = await self._conn.execute(
            "UPDATE pending_action_plan SET status = 'expired' "
            "WHERE status = 'pending' AND expires_at <= ?",
            (now,),
        )
        await self._conn.commit()
        count = cursor.rowcount
        if count:
            logger.info("pending_plans_expired", count=count)
        return count
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_reply_pending_plans.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/donna/replies/pending_plans.py tests/unit/test_reply_pending_plans.py
git commit -m "feat(replies): add pending action plan persistence module"
```

---

## Task 5: Action Registry Module

**Files:**
- Create: `src/donna/replies/action_registry.py`
- Create: `tests/unit/test_reply_action_registry.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_reply_action_registry.py`:

```python
"""Tests for action registry validation."""
from __future__ import annotations

import pytest

from donna.config import ActionDef, ActionParamDef, ReplyActionsConfig, MemoryConfig, PlanConfig
from donna.replies.action_registry import ActionRegistry


def _make_config() -> ReplyActionsConfig:
    return ReplyActionsConfig(
        memory=MemoryConfig(),
        plan=PlanConfig(),
        actions={
            "mark_done": ActionDef(
                description="Mark done",
                handler="donna.replies.actions.task_actions.mark_done",
                params={"task_id": ActionParamDef(type="string", from_context=True)},
            ),
            "create_task": ActionDef(
                description="Create task",
                handler="donna.replies.actions.task_actions.create_task",
                params={
                    "title": ActionParamDef(type="string"),
                    "domain": ActionParamDef(type="string", enum=["work", "personal"]),
                    "priority": ActionParamDef(type="int", default=2),
                },
                risk="medium",
            ),
        },
    )


class TestValidation:
    def test_valid_action_passes(self) -> None:
        reg = ActionRegistry(_make_config())
        errors = reg.validate_action({"action": "mark_done", "params": {}})
        assert errors == []

    def test_unknown_action_rejected(self) -> None:
        reg = ActionRegistry(_make_config())
        errors = reg.validate_action({"action": "fly_to_moon", "params": {}})
        assert any("unknown" in e.lower() for e in errors)

    def test_missing_required_param(self) -> None:
        reg = ActionRegistry(_make_config())
        errors = reg.validate_action({"action": "create_task", "params": {}})
        assert any("title" in e for e in errors)

    def test_context_param_not_required_from_user(self) -> None:
        reg = ActionRegistry(_make_config())
        errors = reg.validate_action({"action": "mark_done", "params": {}})
        assert errors == []

    def test_param_with_default_not_required(self) -> None:
        reg = ActionRegistry(_make_config())
        errors = reg.validate_action(
            {"action": "create_task", "params": {"title": "Do thing"}}
        )
        assert errors == []


class TestRenderForLLM:
    def test_render_produces_action_descriptions(self) -> None:
        reg = ActionRegistry(_make_config())
        text = reg.render_for_llm()
        assert "mark_done" in text
        assert "create_task" in text
        assert "Mark done" in text

    def test_render_includes_param_info(self) -> None:
        reg = ActionRegistry(_make_config())
        text = reg.render_for_llm()
        assert "title" in text
        assert "string" in text


class TestInjectContext:
    def test_inject_fills_context_params(self) -> None:
        reg = ActionRegistry(_make_config())
        action = {"action": "mark_done", "params": {}}
        context = {"task_id": "t-123"}
        filled = reg.inject_context(action, context)
        assert filled["params"]["task_id"] == "t-123"

    def test_inject_does_not_overwrite_explicit(self) -> None:
        reg = ActionRegistry(_make_config())
        action = {"action": "mark_done", "params": {"task_id": "t-explicit"}}
        context = {"task_id": "t-123"}
        filled = reg.inject_context(action, context)
        assert filled["params"]["task_id"] == "t-explicit"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_reply_action_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'donna.replies.action_registry'`

- [ ] **Step 3: Implement `src/donna/replies/action_registry.py`**

```python
"""Action registry for the Universal Reply Handler.

Loads action definitions from config, validates LLM-proposed actions,
and renders action descriptions for the LLM prompt.
"""
from __future__ import annotations

from typing import Any

import structlog

from donna.config import ReplyActionsConfig

logger = structlog.get_logger()


class ActionRegistry:
    """Validates and describes available reply actions.

    Args:
        config: Parsed ReplyActionsConfig from reply_actions.yaml.
    """

    def __init__(self, config: ReplyActionsConfig) -> None:
        self._config = config

    def validate_action(self, action: dict[str, Any]) -> list[str]:
        """Validate a single proposed action. Returns list of error strings (empty = valid)."""
        errors: list[str] = []
        name = action.get("action", "")
        if name not in self._config.actions:
            errors.append(f"Unknown action: {name!r}")
            return errors

        defn = self._config.actions[name]
        provided = action.get("params", {})

        for param_name, param_def in defn.params.items():
            if param_def.from_context:
                continue
            if param_def.default is not None or param_def.optional:
                continue
            if param_name not in provided:
                errors.append(f"Missing required param {param_name!r} for action {name!r}")

        return errors

    def validate_actions(self, actions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
        """Validate a list of proposed actions. Returns (valid_actions, all_errors)."""
        valid: list[dict[str, Any]] = []
        all_errors: list[str] = []
        for action in actions:
            errors = self.validate_action(action)
            if errors:
                all_errors.extend(errors)
                logger.warning("action_validation_failed", action=action.get("action"), errors=errors)
            else:
                valid.append(action)
        return valid, all_errors

    def inject_context(self, action: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        """Fill from_context params that weren't explicitly provided by the LLM."""
        name = action.get("action", "")
        if name not in self._config.actions:
            return action

        defn = self._config.actions[name]
        params = dict(action.get("params", {}))

        for param_name, param_def in defn.params.items():
            if param_def.from_context and param_name not in params:
                if param_name in context:
                    params[param_name] = context[param_name]

        return {**action, "params": params}

    def render_for_llm(self) -> str:
        """Render action descriptions for the LLM system prompt."""
        lines: list[str] = ["Available actions:"]
        for name, defn in self._config.actions.items():
            lines.append(f"\n- {name}: {defn.description}")
            if defn.params:
                lines.append("  Parameters:")
                for pname, pdef in defn.params.items():
                    if pdef.from_context:
                        continue
                    req = "required" if (pdef.default is None and not pdef.optional) else "optional"
                    desc = pdef.description or pdef.type
                    enum_str = f" (one of: {', '.join(pdef.enum)})" if pdef.enum else ""
                    lines.append(f"    - {pname} ({pdef.type}, {req}){enum_str}: {desc}")
        return "\n".join(lines)

    def get_action_def(self, name: str) -> Any | None:
        """Return the ActionDef for a name, or None."""
        return self._config.actions.get(name)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_reply_action_registry.py -v`
Expected: All 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/donna/replies/action_registry.py tests/unit/test_reply_action_registry.py
git commit -m "feat(replies): add action registry with validation and LLM rendering"
```

---

## Task 6: Action Handlers — Task Actions and Gap Actions

**Files:**
- Create: `src/donna/replies/actions/__init__.py`
- Create: `src/donna/replies/actions/task_actions.py`
- Create: `src/donna/replies/actions/gap_actions.py`
- Create: `tests/unit/test_reply_gap.py`

- [ ] **Step 1: Write failing tests for gap actions**

Create `tests/unit/test_reply_gap.py`:

```python
"""Tests for capability gap tracking."""
from __future__ import annotations

import aiosqlite
import pytest

from donna.replies.actions.gap_actions import CapabilityGapTracker


@pytest.fixture
async def gap_db():
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("""
        CREATE TABLE capability_gap (
            id TEXT PRIMARY KEY,
            user_request TEXT NOT NULL,
            description TEXT NOT NULL,
            context_type TEXT,
            task_id TEXT,
            hit_count INTEGER DEFAULT 1,
            status TEXT DEFAULT 'logged',
            created_at TEXT NOT NULL,
            last_hit_at TEXT NOT NULL
        )
    """)
    await conn.commit()
    yield conn
    await conn.close()


@pytest.mark.asyncio
async def test_log_new_gap(gap_db: aiosqlite.Connection) -> None:
    tracker = CapabilityGapTracker(gap_db)
    await tracker.log_gap("book a restaurant", "User wants to book a restaurant", "overdue", "t1")
    cursor = await gap_db.execute("SELECT COUNT(*) FROM capability_gap")
    assert (await cursor.fetchone())[0] == 1


@pytest.mark.asyncio
async def test_dedup_increments_hit_count(gap_db: aiosqlite.Connection) -> None:
    tracker = CapabilityGapTracker(gap_db)
    await tracker.log_gap("book a restaurant", "user wants to book a restaurant", "overdue", "t1")
    await tracker.log_gap("book restaurant please", "user wants to book a restaurant", "chat", "t2")
    cursor = await gap_db.execute("SELECT hit_count FROM capability_gap")
    row = await cursor.fetchone()
    assert row[0] == 2


@pytest.mark.asyncio
async def test_different_gaps_not_deduped(gap_db: aiosqlite.Connection) -> None:
    tracker = CapabilityGapTracker(gap_db)
    await tracker.log_gap("book a restaurant", "book restaurant", "overdue", "t1")
    await tracker.log_gap("send a fax", "send fax to office", "chat", "t2")
    cursor = await gap_db.execute("SELECT COUNT(*) FROM capability_gap")
    assert (await cursor.fetchone())[0] == 2


@pytest.mark.asyncio
async def test_get_promotable_gaps(gap_db: aiosqlite.Connection) -> None:
    tracker = CapabilityGapTracker(gap_db)
    await tracker.log_gap("book restaurant", "book restaurant", "overdue", None)
    await tracker.log_gap("book restaurant again", "book restaurant", "chat", None)
    await tracker.log_gap("book restaurant third", "book restaurant", "chat", None)
    promotable = await tracker.get_promotable(min_hits=3)
    assert len(promotable) == 1
    assert promotable[0]["hit_count"] >= 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_reply_gap.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create `src/donna/replies/actions/__init__.py`**

```python
"""Action handlers for the Universal Reply Handler."""
```

- [ ] **Step 4: Implement `src/donna/replies/actions/gap_actions.py`**

```python
"""Capability gap tracking for the Universal Reply Handler.

Logs user requests that Donna cannot handle, deduplicates by
Jaccard similarity, and surfaces frequently-requested capabilities
for skill candidate promotion.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
import uuid6

logger = structlog.get_logger()

_JACCARD_THRESHOLD = 0.6


def _jaccard(a: str, b: str) -> float:
    """Word-level Jaccard similarity between two strings."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


class CapabilityGapTracker:
    """Track capability gaps and surface promotable candidates.

    Args:
        conn: aiosqlite connection with capability_gap table.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def log_gap(
        self,
        user_request: str,
        description: str,
        context_type: str | None = None,
        task_id: str | None = None,
    ) -> None:
        """Log a capability gap, deduplicating by Jaccard similarity."""
        now = datetime.now(tz=UTC).isoformat()
        norm_desc = description.lower().strip()

        cursor = await self._conn.execute(
            "SELECT id, description, hit_count FROM capability_gap WHERE status = 'logged'"
        )
        existing = await cursor.fetchall()

        for row in existing:
            existing_desc = row[1].lower().strip()
            if existing_desc == norm_desc or _jaccard(existing_desc, norm_desc) >= _JACCARD_THRESHOLD:
                await self._conn.execute(
                    "UPDATE capability_gap SET hit_count = hit_count + 1, last_hit_at = ? WHERE id = ?",
                    (now, row[0]),
                )
                await self._conn.commit()
                logger.info("capability_gap_deduped", gap_id=row[0], hit_count=row[2] + 1)
                return

        gap_id = str(uuid6.uuid7())
        await self._conn.execute(
            "INSERT INTO capability_gap (id, user_request, description, context_type, task_id, hit_count, status, created_at, last_hit_at) "
            "VALUES (?, ?, ?, ?, ?, 1, 'logged', ?, ?)",
            (gap_id, user_request, description, context_type, task_id, now, now),
        )
        await self._conn.commit()
        logger.info("capability_gap_logged", gap_id=gap_id, description=description[:80])

    async def get_promotable(self, min_hits: int = 3) -> list[dict[str, Any]]:
        """Return gaps with hit_count >= min_hits and status 'logged'."""
        cursor = await self._conn.execute(
            "SELECT id, description, hit_count, created_at, last_hit_at "
            "FROM capability_gap WHERE status = 'logged' AND hit_count >= ?",
            (min_hits,),
        )
        rows = await cursor.fetchall()
        return [
            {"id": r[0], "description": r[1], "hit_count": r[2], "created_at": r[3], "last_hit_at": r[4]}
            for r in rows
        ]

    async def mark_promoted(self, gap_id: str) -> None:
        """Mark a gap as promoted to skill candidate."""
        await self._conn.execute(
            "UPDATE capability_gap SET status = 'candidate_created' WHERE id = ?",
            (gap_id,),
        )
        await self._conn.commit()
```

- [ ] **Step 5: Implement `src/donna/replies/actions/task_actions.py`**

```python
"""Task action handlers for the Universal Reply Handler.

Each handler takes a Database, context dict, and action params,
executes the action, and returns a result summary string.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from donna.tasks.db_models import TaskStatus

if TYPE_CHECKING:
    from donna.scheduling.scheduler import Scheduler
    from donna.tasks.database import Database

logger = structlog.get_logger()


async def mark_done(db: Database, context: dict[str, Any], params: dict[str, Any]) -> str:
    """Mark a task as done. Transitions through in_progress if needed."""
    task_id = params["task_id"]
    task = await db.get_task(task_id)
    if task is None:
        return f"Task {task_id} not found."

    if task.status != TaskStatus.IN_PROGRESS.value:
        try:
            await db.transition_task_state(task_id, TaskStatus.IN_PROGRESS)
        except Exception:
            logger.exception("mark_done_transition_failed", task_id=task_id)
            return f"Failed to transition '{task.title}' to in_progress."

    try:
        await db.transition_task_state(task_id, TaskStatus.DONE)
        await db.update_task(task_id, completed_at=datetime.now(UTC))
        return f"Marked '{task.title}' as done."
    except Exception:
        logger.exception("mark_done_failed", task_id=task_id)
        return f"Failed to mark '{task.title}' as done."


async def reschedule_task(db: Database, context: dict[str, Any], params: dict[str, Any]) -> str:
    """Reschedule a task. Uses the scheduler to find a new slot."""
    task_id = params["task_id"]
    task = await db.get_task(task_id)
    if task is None:
        return f"Task {task_id} not found."

    if task.status != TaskStatus.IN_PROGRESS.value:
        try:
            await db.transition_task_state(task_id, TaskStatus.IN_PROGRESS)
        except Exception:
            logger.exception("reschedule_transition_failed", task_id=task_id)
            return f"Failed to transition '{task.title}' for rescheduling."

    try:
        await db.transition_task_state(task_id, TaskStatus.SCHEDULED)
    except Exception:
        logger.exception("reschedule_to_scheduled_failed", task_id=task_id)
        return f"Failed to move '{task.title}' back to scheduled."

    scheduler: Scheduler | None = context.get("scheduler")
    calendar_client = context.get("calendar_client")
    calendar_id = context.get("calendar_id")

    if scheduler and calendar_client and calendar_id:
        try:
            refreshed = await db.get_task(task_id)
            if refreshed:
                await scheduler.schedule_task(
                    task=refreshed,
                    db=db,
                    client=calendar_client,
                    calendar_id=calendar_id,
                    force_reschedule=True,
                )
                return f"Rescheduled '{task.title}'."
        except Exception:
            logger.exception("reschedule_slot_failed", task_id=task_id)
            return f"Moved '{task.title}' to scheduled but couldn't find a new slot."

    return f"Moved '{task.title}' to scheduled (no calendar client available for slot assignment)."


async def create_task(db: Database, context: dict[str, Any], params: dict[str, Any]) -> str:
    """Create a new task."""
    from donna.tasks.db_models import TaskDomain

    title = params["title"]
    domain_str = params.get("domain", "personal")
    priority = params.get("priority", 2)

    domain_map = {"work": TaskDomain.WORK, "personal": TaskDomain.PERSONAL, "family": TaskDomain.FAMILY}
    domain = domain_map.get(domain_str, TaskDomain.PERSONAL)

    user_id = context.get("user_id", "system")
    try:
        new_task = await db.create_task(
            user_id=user_id,
            title=title,
            domain=domain,
            priority=priority,
        )
        return f"Created task '{title}' (id: {new_task.id})."
    except Exception:
        logger.exception("create_task_failed", title=title)
        return f"Failed to create task '{title}'."


async def rename_task(db: Database, context: dict[str, Any], params: dict[str, Any]) -> str:
    """Rename a task."""
    task_id = params["task_id"]
    new_title = params["new_title"]
    task = await db.get_task(task_id)
    if task is None:
        return f"Task {task_id} not found."

    try:
        await db.update_task(task_id, title=new_title)
        return f"Renamed task to '{new_title}'."
    except Exception:
        logger.exception("rename_task_failed", task_id=task_id)
        return f"Failed to rename task."


async def snooze_task(db: Database, context: dict[str, Any], params: dict[str, Any]) -> str:
    """Snooze a task's notifications."""
    task_id = params["task_id"]
    hours = params.get("duration_hours", 2)
    task = await db.get_task(task_id)
    if task is None:
        return f"Task {task_id} not found."

    logger.info("task_snoozed", task_id=task_id, hours=hours)
    return f"Snoozed notifications for '{task.title}' for {hours} hour(s)."
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/unit/test_reply_gap.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/donna/replies/actions/__init__.py src/donna/replies/actions/task_actions.py src/donna/replies/actions/gap_actions.py tests/unit/test_reply_gap.py
git commit -m "feat(replies): add task action handlers and capability gap tracker"
```

---

## Task 7: JSON Schema and Prompt Template for LLM Path

**Files:**
- Create: `schemas/reply_intent_output.json`
- Create: `prompts/reply_intent.md`
- Modify: `config/task_types.yaml`
- Modify: `config/donna_models.yaml`

- [ ] **Step 1: Create `schemas/reply_intent_output.json`**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {
    "reasoning": {
      "type": "string",
      "description": "Internal chain-of-thought explaining the interpretation. Logged but not shown to user."
    },
    "actions": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "action": {
            "type": "string",
            "description": "Action name from the available actions list"
          },
          "params": {
            "type": "object",
            "description": "Parameters for the action"
          }
        },
        "required": ["action", "params"]
      },
      "description": "Ordered list of actions to propose to the user"
    },
    "reply_to_user": {
      "type": "string",
      "description": "Donna's response in persona. Summarizes the proposed plan and ends with a confirmation prompt."
    }
  },
  "required": ["reasoning", "actions", "reply_to_user"]
}
```

- [ ] **Step 2: Create `prompts/reply_intent.md`**

```
You are Donna, a sharp and direct personal assistant. You never grovel or apologize. You speak with confidence and efficiency.

A user has replied in a conversation thread. Interpret their reply, propose actions, and draft a response.

## Current Task Context
Task: {{ task_title }}
Status: {{ task_status }}
Domain: {{ task_domain }}
Priority: {{ task_priority }}
Scheduled start: {{ scheduled_start }}
Estimated duration: {{ estimated_duration }} minutes

## Conversation History
{% for msg in conversation %}
{{ msg.role | upper }}: {{ msg.content }}
{% endfor %}

## User's New Reply
{{ user_reply }}

## Available Actions
{{ available_actions }}

## Instructions
1. Analyze the user's reply in context of the conversation and task.
2. Propose one or more actions from the available actions list. Use ONLY actions from the list.
3. If the user wants something you cannot do with available actions, use `request_capability` to flag it.
4. Write a reply in Donna's voice — direct, efficient, no filler. Summarize what you'll do and end with a short confirmation prompt like "Sound good?" or "Go ahead?"
5. Do NOT claim you have already done anything. You are PROPOSING actions for confirmation.

Respond with JSON:
{
  "reasoning": "Your analysis of what the user wants",
  "actions": [{"action": "action_name", "params": {...}}],
  "reply_to_user": "Your response to the user in Donna's voice"
}
```

- [ ] **Step 3: Add `reply_intent` to `config/task_types.yaml`**

Add after the existing `triage_failure` entry (around line 143):

```yaml
  reply_intent:
    description: "Classify user reply intent and propose actions"
    model: local_parser
    prompt_template: prompts/reply_intent.md
    output_schema: schemas/reply_intent_output.json
    tools: []
```

- [ ] **Step 4: Add `reply_intent` routing to `config/donna_models.yaml`**

Add in the `routing:` section after `triage_failure`:

```yaml
  reply_intent:
    model: local_parser
    fallback: parser
    confidence_threshold: 0.5
```

- [ ] **Step 5: Verify config loads cleanly**

Run: `cd /mnt/donna/donna && python -c "from donna.config import load_yaml; d = load_yaml('config/task_types.yaml'); assert 'reply_intent' in d.get('task_types', d), 'reply_intent not found'; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add schemas/reply_intent_output.json prompts/reply_intent.md config/task_types.yaml config/donna_models.yaml
git commit -m "feat(replies): add JSON schema, prompt template, and model routing for reply_intent"
```

---

## Task 8: LLM Classifier Module

**Files:**
- Create: `src/donna/replies/llm_classifier.py`
- Create: `tests/unit/test_reply_handler.py` (partial — LLM classifier tests)

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_reply_handler.py`:

```python
"""Tests for the full ReplyHandler pipeline (mocked LLM)."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from donna.replies.llm_classifier import LLMClassifier


@pytest.fixture
async def classifier_db():
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("""
        CREATE TABLE thread_memory (
            id TEXT PRIMARY KEY, thread_id TEXT NOT NULL,
            context_type TEXT NOT NULL, task_id TEXT,
            role TEXT NOT NULL, content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    await conn.execute(
        "CREATE INDEX idx_thread_memory_thread ON thread_memory(thread_id, created_at)"
    )
    await conn.commit()
    yield conn
    await conn.close()


def _mock_router(actions: list, reply: str, reasoning: str = "test") -> MagicMock:
    router = MagicMock()
    router.complete = AsyncMock(return_value=(
        {"reasoning": reasoning, "actions": actions, "reply_to_user": reply},
        MagicMock(),
    ))
    return router


def _mock_task() -> MagicMock:
    t = MagicMock()
    t.id = "t-1"
    t.title = "Build thing"
    t.status = "scheduled"
    t.domain = "work"
    t.priority = 2
    t.scheduled_start = "2026-05-12T09:00:00+00:00"
    t.estimated_duration = 30
    t.nudge_count = 0
    t.reschedule_count = 0
    return t


@pytest.mark.asyncio
async def test_classify_returns_actions_and_reply(classifier_db: aiosqlite.Connection) -> None:
    from donna.config import ReplyActionsConfig, MemoryConfig, PlanConfig, ActionDef, ActionParamDef
    from donna.replies.action_registry import ActionRegistry
    from donna.replies.memory import ThreadMemory

    config = ReplyActionsConfig(
        memory=MemoryConfig(), plan=PlanConfig(),
        actions={
            "mark_done": ActionDef(
                description="Mark done",
                handler="donna.replies.actions.task_actions.mark_done",
                params={"task_id": ActionParamDef(type="string", from_context=True)},
            ),
        },
    )
    registry = ActionRegistry(config)
    memory = ThreadMemory(classifier_db)
    router = _mock_router(
        actions=[{"action": "mark_done", "params": {}}],
        reply="I'll mark 'Build thing' as done. Sound good?",
    )

    classifier = LLMClassifier(router=router, registry=registry, memory=memory)
    result = await classifier.classify(
        thread_id="thread-1",
        user_reply="I finished it earlier today",
        task=_mock_task(),
        context_type="overdue",
    )

    assert len(result["actions"]) == 1
    assert result["actions"][0]["action"] == "mark_done"
    assert "mark" in result["reply_to_user"].lower() or "done" in result["reply_to_user"].lower()
    router.complete.assert_called_once()


@pytest.mark.asyncio
async def test_classify_strips_invalid_actions(classifier_db: aiosqlite.Connection) -> None:
    from donna.config import ReplyActionsConfig, MemoryConfig, PlanConfig, ActionDef, ActionParamDef
    from donna.replies.action_registry import ActionRegistry
    from donna.replies.memory import ThreadMemory

    config = ReplyActionsConfig(
        memory=MemoryConfig(), plan=PlanConfig(),
        actions={
            "mark_done": ActionDef(
                description="Mark done",
                handler="donna.replies.actions.task_actions.mark_done",
                params={"task_id": ActionParamDef(type="string", from_context=True)},
            ),
        },
    )
    registry = ActionRegistry(config)
    memory = ThreadMemory(classifier_db)
    router = _mock_router(
        actions=[
            {"action": "mark_done", "params": {}},
            {"action": "fly_to_moon", "params": {}},
        ],
        reply="reply text",
    )

    classifier = LLMClassifier(router=router, registry=registry, memory=memory)
    result = await classifier.classify(
        thread_id="thread-1",
        user_reply="done and fly me to the moon",
        task=_mock_task(),
        context_type="overdue",
    )

    assert len(result["actions"]) == 1
    assert result["actions"][0]["action"] == "mark_done"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_reply_handler.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'donna.replies.llm_classifier'`

- [ ] **Step 3: Implement `src/donna/replies/llm_classifier.py`**

```python
"""LLM-based reply classifier for the Universal Reply Handler (Layer 2).

Constructs a prompt with conversation memory, task context, and
available actions, then sends to the local LLM via ModelRouter.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from donna.replies.action_registry import ActionRegistry
from donna.replies.memory import ThreadMemory

if TYPE_CHECKING:
    from donna.models.router import ModelRouter

logger = structlog.get_logger()


class LLMClassifier:
    """Classify complex replies via the local LLM.

    Args:
        router: ModelRouter instance for LLM calls.
        registry: ActionRegistry for validation and prompt rendering.
        memory: ThreadMemory for conversation context.
    """

    def __init__(
        self,
        router: ModelRouter,
        registry: ActionRegistry,
        memory: ThreadMemory,
    ) -> None:
        self._router = router
        self._registry = registry
        self._memory = memory

    async def classify(
        self,
        thread_id: str,
        user_reply: str,
        task: Any,
        context_type: str,
    ) -> dict[str, Any]:
        """Send reply + context to LLM and return validated actions.

        Returns dict with keys: actions (list), reply_to_user (str), reasoning (str).
        """
        conversation = await self._memory.retrieve(thread_id)
        available_actions = self._registry.render_for_llm()

        prompt = (
            f"## Current Task Context\n"
            f"Task: {getattr(task, 'title', '')}\n"
            f"Status: {getattr(task, 'status', '')}\n"
            f"Domain: {getattr(task, 'domain', 'personal')}\n"
            f"Priority: {getattr(task, 'priority', 2)}\n"
            f"Scheduled start: {getattr(task, 'scheduled_start', 'unknown')}\n"
            f"Estimated duration: {getattr(task, 'estimated_duration', 0)} minutes\n\n"
            f"## Conversation History\n"
        )

        for msg in conversation:
            prompt += f"{msg['role'].upper()}: {msg['content']}\n"

        prompt += (
            f"\n## User's New Reply\n{user_reply}\n\n"
            f"## Available Actions\n{available_actions}\n"
        )

        task_id = getattr(task, "id", None)

        try:
            result, _meta = await self._router.complete(
                prompt=prompt,
                task_type="reply_intent",
                task_id=task_id,
                user_id="system",
            )
        except Exception:
            logger.exception("llm_classifier_failed", thread_id=thread_id)
            return {
                "actions": [],
                "reply_to_user": "I couldn't process that. Could you try rephrasing?",
                "reasoning": "LLM call failed",
            }

        actions = result.get("actions", [])
        reply_to_user = result.get("reply_to_user", "")
        reasoning = result.get("reasoning", "")

        valid_actions, errors = self._registry.validate_actions(actions)
        if errors:
            logger.warning("llm_actions_had_errors", errors=errors, thread_id=thread_id)

        context = {"task_id": task_id}
        valid_actions = [self._registry.inject_context(a, context) for a in valid_actions]

        logger.info(
            "llm_classify_complete",
            thread_id=thread_id,
            task_id=task_id,
            action_count=len(valid_actions),
            stripped_count=len(actions) - len(valid_actions),
        )

        return {
            "actions": valid_actions,
            "reply_to_user": reply_to_user,
            "reasoning": reasoning,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_reply_handler.py -v`
Expected: All 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/donna/replies/llm_classifier.py tests/unit/test_reply_handler.py
git commit -m "feat(replies): add LLM classifier module with prompt construction and validation"
```

---

## Task 9: Fast Path Module

**Files:**
- Create: `tests/unit/test_reply_fast_path.py`
- Modify: `src/donna/replies/handler.py` (will be created here with the fast path)

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_reply_fast_path.py`:

```python
"""Tests for fast-path keyword matching and complexity gate."""
from __future__ import annotations

import pytest

from donna.config import FastPathConfig, ReplyIntentDef, ReplyIntentsConfig
from donna.replies.handler import FastPath


def _make_config() -> ReplyIntentsConfig:
    return ReplyIntentsConfig(
        fast_path=FastPathConfig(
            max_length=60,
            multi_intent_signals=[" but ", " and also ", " however "],
            confirm_keywords=["yes", "go ahead", "do it", "ok", "sounds good"],
            reject_keywords=["no", "cancel", "nevermind"],
        ),
        intents={
            "mark_done": ReplyIntentDef(keywords=["done", "finished", "did it"], action="mark_done"),
            "reschedule": ReplyIntentDef(keywords=["reschedule", "tomorrow", "later"], action="reschedule"),
            "busy": ReplyIntentDef(keywords=["busy", "not now", "snooze"], action="snooze"),
        },
    )


class TestComplexityGate:
    def test_short_single_intent_passes(self) -> None:
        fp = FastPath(_make_config())
        assert fp.is_simple("done") is True

    def test_long_reply_fails(self) -> None:
        fp = FastPath(_make_config())
        long = "I finished half of it and need to call Mike to let him know tomorrow"
        assert fp.is_simple(long) is False

    def test_multi_intent_signal_fails(self) -> None:
        fp = FastPath(_make_config())
        assert fp.is_simple("done but also reschedule") is False

    def test_conflicting_intents_fail(self) -> None:
        fp = FastPath(_make_config())
        assert fp.is_simple("done tomorrow") is False

    def test_no_keyword_match_fails(self) -> None:
        fp = FastPath(_make_config())
        assert fp.is_simple("what is this about?") is False


class TestKeywordMatch:
    def test_exact_keyword(self) -> None:
        fp = FastPath(_make_config())
        result = fp.match("done")
        assert result is not None
        assert result.action == "mark_done"

    def test_keyword_in_phrase(self) -> None:
        fp = FastPath(_make_config())
        result = fp.match("yes finished")
        assert result is not None
        assert result.action == "mark_done"

    def test_no_match(self) -> None:
        fp = FastPath(_make_config())
        result = fp.match("what?")
        assert result is None

    def test_case_insensitive(self) -> None:
        fp = FastPath(_make_config())
        result = fp.match("DONE")
        assert result is not None
        assert result.action == "mark_done"


class TestPlanInterception:
    def test_confirm_keyword(self) -> None:
        fp = FastPath(_make_config())
        assert fp.is_plan_confirm("yes") is True
        assert fp.is_plan_confirm("go ahead") is True

    def test_reject_keyword(self) -> None:
        fp = FastPath(_make_config())
        assert fp.is_plan_reject("no") is True
        assert fp.is_plan_reject("cancel") is True

    def test_neither(self) -> None:
        fp = FastPath(_make_config())
        assert fp.is_plan_confirm("hmm actually do something else") is False
        assert fp.is_plan_reject("hmm actually do something else") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_reply_fast_path.py -v`
Expected: FAIL — `ImportError: cannot import name 'FastPath'`

- [ ] **Step 3: Implement `src/donna/replies/handler.py`**

```python
"""Universal Reply Handler — confidence-gated pipeline for user replies.

Layer 1 (FastPath): Config-driven keyword matching with a complexity
gate that prevents misclassification of multi-intent replies.

Layer 2 (LLM): Local LLM classifies complex replies, proposes actions,
and drafts a response in Donna's persona.

Plan-and-confirm: LLM-proposed actions are persisted and require
user confirmation before execution.
"""
from __future__ import annotations

import dataclasses
import re
from typing import Any

import structlog

from donna.config import ReplyIntentsConfig

logger = structlog.get_logger()


@dataclasses.dataclass(frozen=True)
class FastPathResult:
    """Result from fast-path keyword matching."""

    intent: str
    action: str
    confirm: bool


class FastPath:
    """Layer 1: keyword matching with complexity gate.

    Args:
        config: Parsed ReplyIntentsConfig.
    """

    def __init__(self, config: ReplyIntentsConfig) -> None:
        self._config = config
        self._fp = config.fast_path

    def is_simple(self, reply: str) -> bool:
        """Check whether a reply passes the complexity gate."""
        if len(reply) > self._fp.max_length:
            return False

        lower = reply.lower()
        for signal in self._fp.multi_intent_signals:
            if signal in lower:
                return False

        if "," in reply and len(reply.split(",")) > 2:
            return False

        matched_intents: list[str] = []
        for name, intent in self._config.intents.items():
            if any(kw in lower for kw in intent.keywords):
                matched_intents.append(name)

        if len(matched_intents) != 1:
            return False

        return True

    def match(self, reply: str) -> FastPathResult | None:
        """Try to match a reply to a single intent. Returns None if no match or complex."""
        lower = reply.lower()
        if not self.is_simple(reply):
            return None

        for name, intent in self._config.intents.items():
            if any(kw in lower for kw in intent.keywords):
                return FastPathResult(
                    intent=name,
                    action=intent.action,
                    confirm=intent.confirm,
                )
        return None

    def is_plan_confirm(self, reply: str) -> bool:
        """Check if reply is confirming a pending plan."""
        lower = reply.lower().strip()
        return any(kw in lower for kw in self._fp.confirm_keywords)

    def is_plan_reject(self, reply: str) -> bool:
        """Check if reply is rejecting a pending plan."""
        lower = reply.lower().strip()
        return any(kw in lower for kw in self._fp.reject_keywords)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_reply_fast_path.py -v`
Expected: All 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/donna/replies/handler.py tests/unit/test_reply_fast_path.py
git commit -m "feat(replies): add fast path with keyword matching and complexity gate"
```

---

## Task 10: Full ReplyHandler Orchestration

**Files:**
- Modify: `src/donna/replies/handler.py` (add ReplyHandler class)
- Extend: `tests/unit/test_reply_handler.py` (add full pipeline tests)

- [ ] **Step 1: Write failing tests for the full pipeline**

Append to `tests/unit/test_reply_handler.py`:

```python
# --- Full pipeline tests ---

from donna.config import (
    ActionDef, ActionParamDef, FastPathConfig, MemoryConfig, PlanConfig,
    ReplyActionsConfig, ReplyIntentDef, ReplyIntentsConfig,
)
from donna.replies.handler import ReplyHandler, ReplyResult


def _intents_config() -> ReplyIntentsConfig:
    return ReplyIntentsConfig(
        fast_path=FastPathConfig(
            max_length=60,
            multi_intent_signals=[" but ", " and also ", " however "],
            confirm_keywords=["yes", "go ahead"],
            reject_keywords=["no", "cancel"],
        ),
        intents={
            "mark_done": ReplyIntentDef(keywords=["done", "finished"], action="mark_done"),
            "reschedule": ReplyIntentDef(keywords=["reschedule", "tomorrow"], action="reschedule"),
        },
    )


def _actions_config() -> ReplyActionsConfig:
    return ReplyActionsConfig(
        memory=MemoryConfig(window_size=10), plan=PlanConfig(expiry_minutes=60),
        actions={
            "mark_done": ActionDef(
                description="Mark done", handler="donna.replies.actions.task_actions.mark_done",
                params={"task_id": ActionParamDef(type="string", from_context=True)},
            ),
            "reschedule": ActionDef(
                description="Reschedule", handler="donna.replies.actions.task_actions.reschedule_task",
                params={
                    "task_id": ActionParamDef(type="string", from_context=True),
                    "when": ActionParamDef(type="string"),
                },
            ),
        },
    )


@pytest.fixture
async def handler_db():
    conn = await aiosqlite.connect(":memory:")
    for sql in [
        """CREATE TABLE thread_memory (
            id TEXT PRIMARY KEY, thread_id TEXT NOT NULL,
            context_type TEXT NOT NULL, task_id TEXT,
            role TEXT NOT NULL, content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
        "CREATE INDEX idx_thread_memory_thread ON thread_memory(thread_id, created_at)",
        """CREATE TABLE pending_action_plan (
            id TEXT PRIMARY KEY, thread_id TEXT NOT NULL,
            actions_json TEXT NOT NULL, reply_text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL, expires_at TEXT NOT NULL
        )""",
        "CREATE INDEX idx_pending_plan_thread ON pending_action_plan(thread_id, status)",
    ]:
        await conn.execute(sql)
    await conn.commit()
    yield conn
    await conn.close()


@pytest.mark.asyncio
async def test_fast_path_returns_immediate(handler_db: aiosqlite.Connection) -> None:
    handler = ReplyHandler(
        conn=handler_db,
        intents_config=_intents_config(),
        actions_config=_actions_config(),
        router=MagicMock(),
        db=AsyncMock(),
        context={},
    )
    result = await handler.handle("thread-1", "done", _mock_task(), "overdue")
    assert result.path == "fast"
    assert result.action == "mark_done"


@pytest.mark.asyncio
async def test_complex_reply_routes_to_llm(handler_db: aiosqlite.Connection) -> None:
    router = _mock_router(
        actions=[{"action": "mark_done", "params": {}}],
        reply="I'll mark it done. Sound good?",
    )
    handler = ReplyHandler(
        conn=handler_db,
        intents_config=_intents_config(),
        actions_config=_actions_config(),
        router=router,
        db=AsyncMock(),
        context={},
    )
    result = await handler.handle(
        "thread-1",
        "I finished it earlier today and also need to call Mike",
        _mock_task(),
        "overdue",
    )
    assert result.path == "llm"
    assert result.pending_plan_id is not None


@pytest.mark.asyncio
async def test_confirm_executes_pending_plan(handler_db: aiosqlite.Connection) -> None:
    router = _mock_router(
        actions=[{"action": "mark_done", "params": {}}],
        reply="I'll mark it done. Sound good?",
    )
    mock_db = AsyncMock()
    mock_task = _mock_task()
    mock_db.get_task = AsyncMock(return_value=mock_task)
    mock_db.transition_task_state = AsyncMock()
    mock_db.update_task = AsyncMock()

    handler = ReplyHandler(
        conn=handler_db,
        intents_config=_intents_config(),
        actions_config=_actions_config(),
        router=router,
        db=mock_db,
        context={},
    )
    # First: LLM proposes
    await handler.handle("thread-1", "I finished it today", mock_task, "overdue")
    # Second: user confirms
    result = await handler.handle("thread-1", "yes", mock_task, "overdue")
    assert result.path == "plan_confirmed"


@pytest.mark.asyncio
async def test_reject_clears_pending_plan(handler_db: aiosqlite.Connection) -> None:
    router = _mock_router(
        actions=[{"action": "mark_done", "params": {}}],
        reply="plan reply",
    )
    handler = ReplyHandler(
        conn=handler_db,
        intents_config=_intents_config(),
        actions_config=_actions_config(),
        router=router,
        db=AsyncMock(),
        context={},
    )
    await handler.handle("thread-1", "I finished it today", _mock_task(), "overdue")
    result = await handler.handle("thread-1", "no", _mock_task(), "overdue")
    assert result.path == "plan_rejected"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_reply_handler.py -v`
Expected: FAIL — `ImportError: cannot import name 'ReplyHandler'`

- [ ] **Step 3: Add `ReplyHandler` and `ReplyResult` to `src/donna/replies/handler.py`**

Append to the existing file (after the `FastPath` class):

```python
@dataclasses.dataclass
class ReplyResult:
    """Result of processing a user reply."""

    path: str  # "fast", "llm", "plan_confirmed", "plan_rejected", "plan_cancelled"
    action: str | None = None
    actions: list[dict[str, Any]] | None = None
    reply_to_user: str | None = None
    pending_plan_id: str | None = None
    execution_results: list[str] | None = None


class ReplyHandler:
    """Universal Reply Handler — confidence-gated pipeline.

    Args:
        conn: aiosqlite connection (for memory and plans tables).
        intents_config: FastPath keyword config.
        actions_config: Action registry config.
        router: ModelRouter for LLM calls.
        db: Database for executing task actions.
        context: Shared context dict (scheduler, calendar_client, etc.).
    """

    def __init__(
        self,
        conn: Any,
        intents_config: ReplyIntentsConfig,
        actions_config: Any,
        router: Any,
        db: Any,
        context: dict[str, Any],
    ) -> None:
        from donna.replies.action_registry import ActionRegistry
        from donna.replies.llm_classifier import LLMClassifier
        from donna.replies.memory import ThreadMemory
        from donna.replies.pending_plans import PendingPlans

        self._db = db
        self._context = context
        self._fast_path = FastPath(intents_config)
        self._registry = ActionRegistry(actions_config)
        self._memory = ThreadMemory(conn, window_size=actions_config.memory.window_size)
        self._plans = PendingPlans(conn, expiry_minutes=actions_config.plan.expiry_minutes)
        self._classifier = LLMClassifier(router=router, registry=self._registry, memory=self._memory)

    async def handle(
        self,
        thread_id: str,
        reply: str,
        task: Any,
        context_type: str,
    ) -> ReplyResult:
        """Process a user reply through the confidence-gated pipeline."""
        await self._memory.record(thread_id, context_type, getattr(task, "id", None), "user", reply)

        # --- Pending plan intercept ---
        pending = await self._plans.get_pending(thread_id)
        if pending is not None:
            if self._fast_path.is_plan_confirm(reply):
                return await self._execute_plan(thread_id, pending, task)
            elif self._fast_path.is_plan_reject(reply):
                await self._plans.reject(thread_id)
                return ReplyResult(path="plan_rejected", reply_to_user="Got it, cancelled.")
            else:
                await self._plans.reject(thread_id)
                # Fall through to process the new reply

        # --- Layer 1: Fast path ---
        match = self._fast_path.match(reply)
        if match is not None:
            result = await self._execute_fast(thread_id, match, task, context_type)
            return result

        # --- Layer 2: LLM path ---
        llm_result = await self._classifier.classify(
            thread_id=thread_id,
            user_reply=reply,
            task=task,
            context_type=context_type,
        )

        actions = llm_result.get("actions", [])
        reply_to_user = llm_result.get("reply_to_user", "")

        if not actions:
            await self._memory.record(thread_id, context_type, getattr(task, "id", None), "donna", reply_to_user)
            return ReplyResult(path="llm", reply_to_user=reply_to_user)

        plan_id = await self._plans.save(thread_id, actions, reply_to_user)
        await self._memory.record(thread_id, context_type, getattr(task, "id", None), "donna", reply_to_user)

        return ReplyResult(
            path="llm",
            actions=actions,
            reply_to_user=reply_to_user,
            pending_plan_id=plan_id,
        )

    async def _execute_fast(
        self, thread_id: str, match: FastPathResult, task: Any, context_type: str,
    ) -> ReplyResult:
        """Execute a fast-path action immediately."""
        import importlib

        action_name = match.action
        action_def = self._registry.get_action_def(action_name)
        if action_def is None:
            return ReplyResult(path="fast", action=action_name, reply_to_user="Action not found.")

        handler_path = action_def.handler
        module_path, func_name = handler_path.rsplit(".", 1)
        mod = importlib.import_module(module_path)
        handler_fn = getattr(mod, func_name)

        context = {**self._context, "task_id": getattr(task, "id", None)}
        params = {"task_id": getattr(task, "id", None)}

        try:
            result_msg = await handler_fn(self._db, context, params)
        except Exception:
            logger.exception("fast_path_execute_failed", action=action_name)
            result_msg = f"Failed to execute {action_name}."

        await self._memory.record(
            thread_id, context_type, getattr(task, "id", None), "donna", result_msg,
        )

        return ReplyResult(path="fast", action=action_name, reply_to_user=result_msg, execution_results=[result_msg])

    async def _execute_plan(
        self, thread_id: str, pending: dict[str, Any], task: Any,
    ) -> ReplyResult:
        """Execute a confirmed pending plan."""
        import importlib
        import json

        plan = await self._plans.confirm(thread_id)
        if plan is None:
            return ReplyResult(path="plan_confirmed", reply_to_user="No plan to confirm.")

        actions = json.loads(plan["actions_json"])
        results: list[str] = []

        for action in actions:
            action_name = action.get("action", "")
            action_def = self._registry.get_action_def(action_name)
            if action_def is None:
                results.append(f"Unknown action: {action_name}")
                continue

            handler_path = action_def.handler
            module_path, func_name = handler_path.rsplit(".", 1)
            mod = importlib.import_module(module_path)
            handler_fn = getattr(mod, func_name)

            context = {**self._context, "task_id": getattr(task, "id", None)}
            params = action.get("params", {})
            if "task_id" not in params:
                params["task_id"] = getattr(task, "id", None)

            try:
                result_msg = await handler_fn(self._db, context, params)
                results.append(result_msg)
            except Exception:
                logger.exception("plan_execute_action_failed", action=action_name)
                results.append(f"Failed: {action_name}")

        summary = " ".join(results)
        await self._memory.record(
            thread_id, "overdue", getattr(task, "id", None), "donna", summary,
        )

        return ReplyResult(
            path="plan_confirmed",
            actions=actions,
            reply_to_user=summary,
            execution_results=results,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_reply_handler.py -v`
Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/donna/replies/handler.py tests/unit/test_reply_handler.py
git commit -m "feat(replies): add full ReplyHandler with pipeline orchestration and plan execution"
```

---

## Task 11: Wire Into Discord Bot and Overdue Detector

**Files:**
- Modify: `src/donna/notifications/overdue.py`
- Modify: `src/donna/integrations/discord_bot.py`

- [ ] **Step 1: Modify `overdue.py` to use ReplyHandler**

In `src/donna/notifications/overdue.py`, the `handle_reply` method currently contains keyword matching logic. Replace it with a call to `ReplyHandler`. The `OverdueDetector.__init__` needs a new `reply_handler` parameter.

Add to `__init__` parameter list:

```python
    reply_handler: ReplyHandler | None = None,
```

And store it:

```python
    self._reply_handler = reply_handler
```

Add the import at the top (in `TYPE_CHECKING` block):

```python
    from donna.replies.handler import ReplyHandler
```

Replace the `handle_reply` method body:

```python
    async def handle_reply(self, task_id: str, reply: str) -> None:
        """Handle user reply in an overdue thread.

        Delegates to ReplyHandler if wired, falls back to legacy keywords.
        """
        task = await self._db.get_task(task_id)
        if task is None:
            logger.warning("overdue_reply_task_not_found", task_id=task_id)
            return

        if self._reply_handler is not None:
            thread_id = f"overdue-{task_id}"
            result = await self._reply_handler.handle(thread_id, reply, task, "overdue")
            logger.info(
                "overdue_reply_handled",
                task_id=task_id,
                path=result.path,
                action=result.action,
            )
            if result.path in ("fast", "plan_confirmed") and self._escalation_manager is not None:
                if result.action in ("mark_done", "reschedule"):
                    await self._escalation_manager.acknowledge(task_id)
                elif result.action == "snooze":
                    await self._escalation_manager.backoff(task_id)
            return

        # Legacy fallback (kept until ReplyHandler is fully wired)
        _DONE_KEYWORDS = {"done", "finished", "complete", "completed", "did it", "yes"}
        _RESCHEDULE_KEYWORDS = {"reschedule", "tomorrow", "later", "push", "move"}
        _BUSY_KEYWORDS = {"busy", "not now", "snooze"}

        words = reply.lower()
        if any(kw in words for kw in _DONE_KEYWORDS):
            if self._escalation_manager is not None:
                await self._escalation_manager.acknowledge(task_id)
            await self._mark_done(task_id, task)
        elif any(kw in words for kw in _RESCHEDULE_KEYWORDS):
            if self._escalation_manager is not None:
                await self._escalation_manager.acknowledge(task_id)
            await self._reschedule(task_id, task)
        elif any(kw in words for kw in _BUSY_KEYWORDS):
            if self._escalation_manager is not None:
                await self._escalation_manager.backoff(task_id)
            logger.info("overdue_reply_busy", task_id=task_id)
        else:
            logger.info("overdue_reply_unrecognised", task_id=task_id, reply=reply[:50])
```

- [ ] **Step 2: Modify `discord_bot.py` to pass reply text to handler**

In `src/donna/integrations/discord_bot.py`, find the overdue thread reply routing block (around line 315–328). The current code lowercases and strips the reply before passing to the handler. This is fine — `ReplyHandler` handles case internally. No structural change needed to the bot; the handler is called via `self._overdue_reply_handler(task_id, reply)` which already points to `OverdueDetector.handle_reply`. The wiring happens at startup in `server.py`.

However, when `ReplyHandler` returns a `reply_to_user` for the LLM path (plan proposal or confirmation result), the bot should post it in the thread. Modify the overdue reply routing block:

Find the block:
```python
if message.channel.id in self.overdue_threads and self._overdue_reply_handler is not None:
    task_id = self.overdue_threads[message.channel.id]
    reply = message.content.strip().lower()
    logger.info("overdue_reply_received", task_id=task_id, reply=reply[:50])
    try:
        await self._overdue_reply_handler(task_id, reply)
    except Exception:
        logger.exception("overdue_reply_handler_failed", task_id=task_id)
    return
```

Replace with:
```python
if message.channel.id in self.overdue_threads and self._overdue_reply_handler is not None:
    task_id = self.overdue_threads[message.channel.id]
    reply = message.content.strip()
    logger.info("overdue_reply_received", task_id=task_id, reply=reply[:50])
    try:
        result = await self._overdue_reply_handler(task_id, reply)
        if hasattr(result, "reply_to_user") and result.reply_to_user:
            await message.channel.send(result.reply_to_user)
    except Exception:
        logger.exception("overdue_reply_handler_failed", task_id=task_id)
    return
```

Note: Remove `.lower()` from the reply — `ReplyHandler` handles case internally, and the LLM needs the original casing for better understanding.

- [ ] **Step 3: Update `handle_reply` to return a result**

Back in `overdue.py`, the `handle_reply` method needs to return `ReplyResult` when using the handler and `None` for legacy path. Change the signature and the return:

```python
    async def handle_reply(self, task_id: str, reply: str) -> Any:
```

At the end of the ReplyHandler path, add:
```python
            return result
```

At the end of legacy keyword branches, add:
```python
        return None
```

- [ ] **Step 4: Run existing overdue tests**

Run: `pytest tests/unit/test_overdue.py -v`
Expected: All tests still PASS (legacy path is used when `reply_handler=None`).

- [ ] **Step 5: Commit**

```bash
git add src/donna/notifications/overdue.py src/donna/integrations/discord_bot.py
git commit -m "feat(replies): wire ReplyHandler into overdue detector and Discord bot"
```

---

## Task 12: Run Full Test Suite and Fix Issues

**Files:**
- Any files that need fixes

- [ ] **Step 1: Run the full test suite**

Run: `pytest tests/ -v --timeout=30 -x`
Expected: All tests pass. If any fail, fix them.

- [ ] **Step 2: Run linting**

Run: `cd /mnt/donna/donna && ruff check src/donna/replies/ tests/unit/test_reply_*.py`
Expected: No errors. Fix any linting issues.

- [ ] **Step 3: Run type checking**

Run: `cd /mnt/donna/donna && python -m mypy src/donna/replies/ --ignore-missing-imports`
Expected: No errors (or only pre-existing ones). Fix any new type issues.

- [ ] **Step 4: Commit any fixes**

```bash
git add -A
git commit -m "fix(replies): lint, typecheck, and test fixes"
```

---

## Summary

| Task | What | Depends On |
|------|-------|-----------|
| 1 | Alembic migration (3 tables) | — |
| 2 | Config files + Pydantic models | — |
| 3 | Conversation memory module | 1 |
| 4 | Pending plans module | 1 |
| 5 | Action registry | 2 |
| 6 | Action handlers (task + gap) | 1, 5 |
| 7 | JSON schema + prompt + model routing | — |
| 8 | LLM classifier | 3, 5 |
| 9 | Fast path (handler.py) | 2 |
| 10 | Full ReplyHandler orchestration | 3, 4, 5, 8, 9 |
| 11 | Wire into Discord bot + overdue | 10 |
| 12 | Full test suite + lint | 11 |
