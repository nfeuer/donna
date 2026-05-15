# Dashboard Chat Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the broken dashboard chat, add a global context-aware quick panel, build a config-driven action registry so chat can execute real actions, and replace right-side drawers with center dialogs / inline expansion.

**Architecture:** Four-phase build. Phase 1 fixes bugs and adds the backend foundation (Alembic migration, list sessions endpoint, session_id in response). Phase 2 builds the action registry core (types, registry, config, engine integration, prompts). Phase 3 implements 15 action handlers across 5 domains. Phase 4 builds the frontend (CenterDialog primitive, DashboardContext, chat page improvements, quick panel, drawer replacement).

**Tech Stack:** Python 3.12 / FastAPI / aiosqlite / structlog / Alembic (backend). React 18 / TypeScript / Vite / CSS Modules / Radix UI (frontend). Jinja2 prompts. YAML config. pytest.

**Spec:** `docs/superpowers/specs/2026-05-15-dashboard-chat-redesign-design.md`

---

## Phase 1: Bug Fixes + Backend Foundation

### Task 1: Alembic Migration — Add Action Columns

**Files:**
- Create: `alembic/versions/add_chat_action_columns.py`

- [ ] **Step 1: Generate migration stub**

```bash
cd /mnt/donna/donna && alembic revision -m "add chat action columns"
```

- [ ] **Step 2: Write the migration**

Replace the generated file contents with:

```python
"""add chat action columns

Revision ID: <generated>
Revises: d2e3f4a5b6c7
Create Date: 2026-05-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "<generated>"
down_revision: Union[str, None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversation_sessions",
        sa.Column("pending_action", sa.Text(), nullable=True),
    )
    op.add_column(
        "conversation_messages",
        sa.Column("action_name", sa.String(100), nullable=True),
    )
    op.add_column(
        "conversation_messages",
        sa.Column("action_result", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversation_messages", "action_result")
    op.drop_column("conversation_messages", "action_name")
    op.drop_column("conversation_sessions", "pending_action")
```

Note: `pending_action` and `action_result` use `sa.Text()` because they store JSON blobs. SQLite doesn't have a native JSON column type.

- [ ] **Step 3: Run migration**

```bash
alembic upgrade head
```

Expected: migration applies cleanly, no errors.

- [ ] **Step 4: Verify columns exist**

```bash
python -c "
import sqlite3
conn = sqlite3.connect('donna_tasks.db')
cursor = conn.execute('PRAGMA table_info(conversation_sessions)')
cols = [row[1] for row in cursor.fetchall()]
assert 'pending_action' in cols, f'Missing pending_action, got: {cols}'
cursor = conn.execute('PRAGMA table_info(conversation_messages)')
cols = [row[1] for row in cursor.fetchall()]
assert 'action_name' in cols, f'Missing action_name, got: {cols}'
assert 'action_result' in cols, f'Missing action_result, got: {cols}'
print('OK: all columns present')
"
```

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/add_chat_action_columns.py
git commit -m "feat(chat): add action columns to chat tables"
```

---

### Task 2: Update Database Methods

**Files:**
- Modify: `src/donna/tasks/database.py`
- Test: `tests/unit/test_chat_engine.py`

- [ ] **Step 1: Add `list_chat_sessions` method**

Add after the existing `get_active_chat_session` method in `src/donna/tasks/database.py`:

```python
    async def list_chat_sessions(
        self,
        user_id: str,
        status: str | None = None,
        channel: str | None = None,
        limit: int = 50,
    ) -> list[ChatSession]:
        """List chat sessions for a user, newest first."""
        conn = self.connection
        where_clauses = ["user_id = ?"]
        params: list[Any] = [user_id]

        if status is not None:
            where_clauses.append("status = ?")
            params.append(status)
        if channel is not None:
            where_clauses.append("channel = ?")
            params.append(channel)

        query = (
            "SELECT id, user_id, channel, pinned_task_id, status, summary,"
            " created_at, last_activity, expires_at, message_count"
            " FROM conversation_sessions"
            f" WHERE {' AND '.join(where_clauses)}"
            " ORDER BY last_activity DESC LIMIT ?"
        )
        params.append(limit)

        cursor = await conn.execute(query, params)
        rows = await cursor.fetchall()
        return [
            ChatSession(
                id=r[0], user_id=r[1], channel=r[2], pinned_task_id=r[3],
                status=r[4], summary=r[5], created_at=r[6],
                last_activity=r[7], expires_at=r[8], message_count=r[9],
            )
            for r in rows
        ]
```

- [ ] **Step 2: Update `add_chat_message` to accept action fields**

Find the `add_chat_message` method and update its signature and INSERT to include the new columns:

```python
    async def add_chat_message(
        self,
        session_id: str,
        role: str,
        content: str,
        intent: str | None = None,
        tokens_used: int | None = None,
        action_name: str | None = None,
        action_result: str | None = None,
    ) -> ChatMessage:
```

Update the INSERT query inside the method to include the new columns:

```sql
INSERT INTO conversation_messages
    (id, session_id, role, content, intent, tokens_used, action_name, action_result, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
```

And add `action_name` and `action_result` to the parameter tuple.

- [ ] **Step 3: Update `update_chat_session` to allow `pending_action`**

In the `update_chat_session` method, the allowed keyword fields are checked. Ensure `pending_action` is in the set of allowed fields. Find the validation set and add `"pending_action"` to it.

- [ ] **Step 4: Run existing tests**

```bash
pytest tests/unit/test_chat_engine.py -v
```

Expected: all existing tests still pass (these changes are additive — new optional params with defaults).

- [ ] **Step 5: Commit**

```bash
git add src/donna/tasks/database.py
git commit -m "feat(chat): add list_chat_sessions and action columns to db methods"
```

---

### Task 3: Fix Backend — Session ID in Response + List Sessions Endpoint

**Files:**
- Modify: `src/donna/api/routes/chat.py`
- Modify: `src/donna/chat/engine.py`
- Modify: `src/donna/chat/types.py`
- Test: `tests/integration/test_chat_api.py`

- [ ] **Step 1: Add `session_id` to `ChatResponse`**

In `src/donna/chat/types.py`, add `session_id` field to the `ChatResponse` dataclass:

```python
@dataclasses.dataclass(frozen=True)
class ChatResponse:
    text: str
    session_id: str | None = None
    needs_escalation: bool = False
    escalation_reason: str | None = None
    estimated_cost: float | None = None
    suggested_actions: list[str] = dataclasses.field(default_factory=list)
    session_pinned_task_id: str | None = None
    pin_suggestion: dict[str, str] | None = None
```

- [ ] **Step 2: Return `session_id` from engine**

In `src/donna/chat/engine.py`, update both places where `ChatResponse` is constructed in `handle_message` to include `session_id=session.id`. There are three construction sites:

1. Early escalation return (~line 98):
```python
return ChatResponse(
    text=...,
    session_id=session.id,
    needs_escalation=True,
    ...
)
```

2. Post-LLM escalation (~line 159):
```python
result = ChatResponse(
    text=...,
    session_id=session.id,
    needs_escalation=True,
    ...
)
```

3. Normal response (~line 171):
```python
result = ChatResponse(
    text=response_text,
    session_id=session.id,
    suggested_actions=...,
    ...
)
```

- [ ] **Step 3: Add `session_id` to route response**

In `src/donna/api/routes/chat.py`, update the `send_message` return dict to include `session_id`:

```python
return {
    "session_id": resp.session_id,
    "text": resp.text,
    "needs_escalation": resp.needs_escalation,
    "escalation_reason": resp.escalation_reason,
    "estimated_cost": resp.estimated_cost,
    "suggested_actions": resp.suggested_actions,
    "pin_suggestion": resp.pin_suggestion,
    "session_pinned_task_id": resp.session_pinned_task_id,
}
```

- [ ] **Step 4: Add `list_sessions` endpoint**

In `src/donna/api/routes/chat.py`, add a new endpoint before the existing `send_message` route:

```python
@router.get("/sessions")
async def list_sessions(
    user_id: CurrentUser,
    status: str | None = None,
    channel: str | None = None,
    limit: int = 50,
    db: Any = Depends(get_database),
) -> dict[str, Any]:
    """List chat sessions for the current user."""
    sessions = await db.list_chat_sessions(
        user_id=user_id, status=status, channel=channel, limit=limit,
    )
    return {
        "sessions": [
            {
                "id": s.id,
                "user_id": s.user_id,
                "channel": s.channel,
                "status": s.status,
                "pinned_task_id": s.pinned_task_id,
                "summary": s.summary,
                "created_at": s.created_at,
                "last_activity": s.last_activity,
                "message_count": s.message_count,
            }
            for s in sessions
        ],
    }
```

- [ ] **Step 5: Write tests for new endpoint and session_id**

Add to `tests/integration/test_chat_api.py`:

```python
@pytest.mark.asyncio
async def test_send_message_returns_session_id(client, mock_engine, mock_db):
    """POST /chat/sessions/new/messages includes session_id in response."""
    resp = client.post(
        "/chat/sessions/new/messages",
        json={"text": "hello"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data
    assert data["session_id"] is not None


@pytest.mark.asyncio
async def test_list_sessions(client, mock_db):
    """GET /chat/sessions returns session list."""
    mock_db.list_chat_sessions.return_value = []
    resp = client.get("/chat/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert "sessions" in data
    assert isinstance(data["sessions"], list)
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/integration/test_chat_api.py -v
```

Expected: all tests pass including new ones.

- [ ] **Step 7: Commit**

```bash
git add src/donna/chat/types.py src/donna/chat/engine.py src/donna/api/routes/chat.py tests/integration/test_chat_api.py
git commit -m "fix(chat): return session_id in response, add list sessions endpoint"
```

---

### Task 4: Fix Frontend — Error Handling + Session Tracking

**Files:**
- Modify: `donna-ui/src/api/client.ts`
- Modify: `donna-ui/src/api/chat.ts`
- Modify: `donna-ui/src/pages/Chat/index.tsx`

- [ ] **Step 1: Fix error interceptor to handle all errors**

Replace the interceptor in `donna-ui/src/api/client.ts`:

```typescript
client.interceptors.response.use(
  (res) => res,
  (error) => {
    const status = error.response?.status;
    const detail = error.response?.data?.detail || error.message;

    if (!error.response) {
      toast.warning("Network Error", {
        description: "Could not reach the Donna API. Is the backend running?",
      });
    } else if (status === 401 || status === 403) {
      toast.error("Authentication Required", { description: detail });
    } else if (status && status >= 400) {
      toast.error(`Error (${status})`, { description: detail });
    }

    return Promise.reject(error);
  },
);
```

- [ ] **Step 2: Add `session_id` to `ChatResponse` type and add `listSessions`**

In `donna-ui/src/api/chat.ts`, update the `ChatResponse` interface:

```typescript
export interface ChatResponse {
  session_id: string | null;
  text: string;
  needs_escalation: boolean;
  escalation_reason?: string;
  estimated_cost?: number;
  suggested_actions: string[];
  pin_suggestion?: Record<string, string>;
  session_pinned_task_id?: string;
}
```

Add the `listSessions` function after the existing `closeSession` function:

```typescript
export async function listSessions(
  params: { status?: string; channel?: string; limit?: number } = {},
): Promise<{ sessions: ChatSession[] }> {
  const { data } = await client.get("/chat/sessions", { params });
  return data;
}
```

- [ ] **Step 3: Fix Chat page — session tracking + session list fetching**

Replace the full contents of `donna-ui/src/pages/Chat/index.tsx`:

```typescript
import { useState, useCallback, useEffect } from "react";
import { PageHeader } from "../../primitives/PageHeader";
import { Button } from "../../primitives/Button";
import {
  sendMessage,
  fetchSession,
  fetchContextStatus,
  escalateSession,
  listSessions,
  type ChatSession,
  type ChatMessage,
  type ChatResponse,
  type ContextStatus,
} from "../../api/chat";
import SessionList from "./SessionList";
import MessageThread from "./MessageThread";
import MessageInput from "./MessageInput";
import ContextMeter from "./ContextMeter";
import styles from "./Chat.module.css";

export default function ChatPage() {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [lastResponse, setLastResponse] = useState<ChatResponse | null>(null);
  const [contextStatus, setContextStatus] = useState<ContextStatus | null>(null);
  const [sending, setSending] = useState(false);

  const refreshSessions = useCallback(async () => {
    try {
      const resp = await listSessions({ limit: 50 });
      setSessions(resp.sessions);
    } catch {
      // Error toast handled by interceptor
    }
  }, []);

  useEffect(() => {
    refreshSessions();
  }, [refreshSessions]);

  const loadSession = useCallback(async (sessionId: string) => {
    try {
      const resp = await fetchSession(sessionId);
      setMessages(resp.messages);
      setActiveSessionId(sessionId);
      const ctx = await fetchContextStatus(sessionId);
      setContextStatus(ctx);
    } catch {
      setMessages([]);
      setContextStatus(null);
    }
  }, []);

  const handleSend = useCallback(
    async (text: string) => {
      const sid = activeSessionId || "new";
      setSending(true);
      try {
        const resp = await sendMessage(sid, text);
        setLastResponse(resp);

        if (resp.session_id && !activeSessionId) {
          setActiveSessionId(resp.session_id);
          await refreshSessions();
        }

        const loadId = resp.session_id || activeSessionId;
        if (loadId) {
          await loadSession(loadId);
        }
      } catch {
        // Error toast handled by interceptor
      } finally {
        setSending(false);
      }
    },
    [activeSessionId, loadSession, refreshSessions],
  );

  const handleEscalate = useCallback(async () => {
    if (!activeSessionId) return;
    try {
      const resp = await escalateSession(activeSessionId);
      setLastResponse(resp);
      await loadSession(activeSessionId);
    } catch {
      // handled by interceptor
    }
  }, [activeSessionId, loadSession]);

  const handleActionClick = useCallback(
    (action: string) => { handleSend(action); },
    [handleSend],
  );

  const handleNewSession = useCallback(() => {
    setActiveSessionId(null);
    setMessages([]);
    setLastResponse(null);
    setContextStatus(null);
  }, []);

  return (
    <div>
      <PageHeader
        eyebrow="Conversation"
        title="Chat"
        actions={
          <Button variant="primary" size="sm" onClick={handleNewSession}>
            New Session
          </Button>
        }
      />
      <div className={styles.chatLayout}>
        <SessionList sessions={sessions} selectedId={activeSessionId} onSelect={loadSession} />
        <div className={styles.conversationPanel}>
          {activeSessionId && <ContextMeter status={contextStatus} />}
          {messages.length > 0 ? (
            <MessageThread
              messages={messages}
              lastResponse={lastResponse}
              onEscalate={handleEscalate}
              onActionClick={handleActionClick}
            />
          ) : (
            <div className={styles.emptyConversation}>
              Send a message to start a conversation with Donna.
            </div>
          )}
          <MessageInput onSend={handleSend} disabled={sending} />
        </div>
      </div>
    </div>
  );
}
```

Key changes from the original:
- `sessions` now has a setter and `refreshSessions()` loads from backend
- `useEffect` calls `refreshSessions()` on mount
- `handleSend` captures `resp.session_id` and sets `activeSessionId` on first message
- After every send, reload the session from backend (not temp messages) for consistency
- Refresh session list after new session creation

- [ ] **Step 4: Verify the build passes**

```bash
cd /mnt/donna/donna/donna-ui && npx tsc --noEmit && npx vite build
```

Expected: no type errors, build succeeds.

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/api/client.ts donna-ui/src/api/chat.ts donna-ui/src/pages/Chat/index.tsx
git commit -m "fix(chat): fix error handling, session tracking, and session list loading"
```

---

## Phase 2: Action Registry Core

### Task 5: Action Types + Registry Class

**Files:**
- Create: `src/donna/chat/actions/__init__.py`
- Modify: `src/donna/chat/types.py`

- [ ] **Step 1: Add action types to `types.py`**

Add these dataclasses at the end of `src/donna/chat/types.py`:

```python
@dataclasses.dataclass(frozen=True)
class ActionContext:
    """Context passed to every action handler."""
    db: Any  # donna.tasks.database.Database
    user_id: str
    session_id: str
    config: Any  # donna.chat.config.ChatConfig
    dashboard_context: dict[str, Any] | None = None


@dataclasses.dataclass
class ActionResult:
    """Standardized result from action handler execution."""
    success: bool
    data: dict[str, Any] = dataclasses.field(default_factory=dict)
    summary: str = ""
    error: str | None = None


@dataclasses.dataclass(frozen=True)
class ActionDefinition:
    """Single action from chat_actions.yaml."""
    name: str
    description: str
    domain: str
    safety: str  # "read" | "write" | "confirm"
    handler: str  # dotted path, e.g. "donna.chat.actions.tasks.query_tasks"
    parameters: dict[str, Any] = dataclasses.field(default_factory=dict)
```

- [ ] **Step 2: Create the ActionRegistry**

Create `src/donna/chat/actions/__init__.py`:

```python
"""Action registry and execution for Donna chat.

Loads action definitions from config/chat_actions.yaml and resolves
handler functions at startup. Provides matching and execution methods
for the ConversationEngine pipeline.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Callable, Awaitable

import structlog
import yaml

from donna.chat.types import ActionContext, ActionDefinition, ActionResult

logger = structlog.get_logger()

ActionHandler = Callable[[dict[str, Any], ActionContext], Awaitable[ActionResult]]


class ActionRegistry:
    """Loads and manages chat action definitions.

    Usage:
        registry = ActionRegistry.from_yaml(Path("config/chat_actions.yaml"))
        action = registry.match(domain="tasks", action_hint="query_tasks")
        result = await registry.execute(action.name, params, context)
    """

    def __init__(self, actions: dict[str, ActionDefinition]) -> None:
        self._actions = actions
        self._handlers: dict[str, ActionHandler] = {}

    @classmethod
    def from_yaml(cls, path: Path) -> ActionRegistry:
        if not path.exists():
            logger.warning("chat_actions_config_not_found", path=str(path))
            return cls({})
        raw = yaml.safe_load(path.read_text()) or {}
        actions_raw = raw.get("actions", {})
        actions: dict[str, ActionDefinition] = {}
        for name, defn in actions_raw.items():
            actions[name] = ActionDefinition(
                name=name,
                description=defn.get("description", ""),
                domain=defn.get("domain", ""),
                safety=defn.get("safety", "read"),
                handler=defn.get("handler", ""),
                parameters=defn.get("parameters", {}),
            )
        logger.info("action_registry_loaded", count=len(actions))
        return cls(actions)

    def match(
        self,
        domain: str | None = None,
        action_hint: str | None = None,
    ) -> ActionDefinition | None:
        if action_hint and action_hint in self._actions:
            return self._actions[action_hint]
        if domain:
            matches = [a for a in self._actions.values() if a.domain == domain]
            if len(matches) == 1:
                return matches[0]
        return None

    def get(self, name: str) -> ActionDefinition | None:
        return self._actions.get(name)

    def list(self) -> list[ActionDefinition]:
        return list(self._actions.values())

    def list_for_domain(self, domain: str) -> list[ActionDefinition]:
        return [a for a in self._actions.values() if a.domain == domain]

    def _resolve_handler(self, action: ActionDefinition) -> ActionHandler:
        if action.name in self._handlers:
            return self._handlers[action.name]
        module_path, func_name = action.handler.rsplit(".", 1)
        module = importlib.import_module(module_path)
        handler = getattr(module, func_name)
        self._handlers[action.name] = handler
        return handler

    async def execute(
        self,
        action_name: str,
        params: dict[str, Any],
        context: ActionContext,
    ) -> ActionResult:
        action = self._actions.get(action_name)
        if action is None:
            return ActionResult(success=False, error=f"Unknown action: {action_name}")
        try:
            handler = self._resolve_handler(action)
            return await handler(params, context)
        except Exception as exc:
            logger.error(
                "action_execution_failed",
                action=action_name,
                error=str(exc),
            )
            return ActionResult(success=False, error=str(exc))

    def format_pending_action(self, action_name: str, params: dict[str, Any]) -> str:
        return json.dumps({"action": action_name, "params": params})

    @staticmethod
    def parse_pending_action(raw: str) -> tuple[str, dict[str, Any]]:
        data = json.loads(raw)
        return data["action"], data["params"]
```

- [ ] **Step 3: Write tests for ActionRegistry**

Create `tests/unit/test_action_registry.py`:

```python
"""Tests for ActionRegistry loading, matching, and execution."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock

from donna.chat.actions import ActionRegistry
from donna.chat.types import ActionContext, ActionDefinition, ActionResult


@pytest.fixture
def sample_yaml(tmp_path: Path) -> Path:
    config = tmp_path / "chat_actions.yaml"
    config.write_text("""
actions:
  query_tasks:
    description: "List tasks"
    domain: tasks
    safety: read
    handler: donna.chat.actions.tasks.query_tasks
    parameters:
      type: object
      properties:
        status: { type: string }
      required: []
  create_task:
    description: "Create a task"
    domain: tasks
    safety: write
    handler: donna.chat.actions.tasks.create_task
    parameters:
      type: object
      properties:
        title: { type: string }
      required: [title]
  execute_skill:
    description: "Run a skill"
    domain: skills
    safety: confirm
    handler: donna.chat.actions.skills.execute_skill
    parameters:
      type: object
      properties:
        skill_name: { type: string }
      required: [skill_name]
""")
    return config


def test_load_from_yaml(sample_yaml: Path) -> None:
    registry = ActionRegistry.from_yaml(sample_yaml)
    assert len(registry.list()) == 3


def test_load_missing_file(tmp_path: Path) -> None:
    registry = ActionRegistry.from_yaml(tmp_path / "nonexistent.yaml")
    assert len(registry.list()) == 0


def test_match_by_action_hint(sample_yaml: Path) -> None:
    registry = ActionRegistry.from_yaml(sample_yaml)
    result = registry.match(action_hint="query_tasks")
    assert result is not None
    assert result.name == "query_tasks"
    assert result.safety == "read"


def test_match_by_domain_single(sample_yaml: Path) -> None:
    registry = ActionRegistry.from_yaml(sample_yaml)
    result = registry.match(domain="skills")
    assert result is not None
    assert result.name == "execute_skill"


def test_match_by_domain_ambiguous(sample_yaml: Path) -> None:
    registry = ActionRegistry.from_yaml(sample_yaml)
    result = registry.match(domain="tasks")
    assert result is None  # two tasks-domain actions, ambiguous


def test_get_by_name(sample_yaml: Path) -> None:
    registry = ActionRegistry.from_yaml(sample_yaml)
    assert registry.get("create_task") is not None
    assert registry.get("nonexistent") is None


def test_list_for_domain(sample_yaml: Path) -> None:
    registry = ActionRegistry.from_yaml(sample_yaml)
    tasks = registry.list_for_domain("tasks")
    assert len(tasks) == 2
    assert all(a.domain == "tasks" for a in tasks)


def test_pending_action_roundtrip() -> None:
    registry = ActionRegistry({})
    raw = registry.format_pending_action("create_task", {"title": "Test"})
    name, params = ActionRegistry.parse_pending_action(raw)
    assert name == "create_task"
    assert params == {"title": "Test"}
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_action_registry.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/donna/chat/types.py src/donna/chat/actions/__init__.py tests/unit/test_action_registry.py
git commit -m "feat(chat): add ActionRegistry, ActionResult, ActionContext types"
```

---

### Task 6: Action Config + Chat Config Updates

**Files:**
- Create: `config/chat_actions.yaml`
- Modify: `config/chat.yaml`
- Modify: `src/donna/chat/config.py`

- [ ] **Step 1: Create `config/chat_actions.yaml`**

```yaml
actions:
  # ── Tasks ──────────────────────────────────────────
  query_tasks:
    description: "List or search tasks by status, priority, or domain"
    domain: tasks
    safety: read
    handler: donna.chat.actions.tasks.query_tasks
    parameters:
      type: object
      properties:
        status:
          type: string
          enum: [backlog, scheduled, in_progress, blocked, waiting_input, paused, done, cancelled]
        priority:
          type: string
          enum: [P0, P1, P2, P3]
        domain:
          type: string
          enum: [personal, work, family]
      required: []

  get_task:
    description: "Get details of a specific task by ID or title"
    domain: tasks
    safety: read
    handler: donna.chat.actions.tasks.get_task
    parameters:
      type: object
      properties:
        task_id:
          type: string
        title_search:
          type: string
      required: []

  create_task:
    description: "Create a new task"
    domain: tasks
    safety: write
    handler: donna.chat.actions.tasks.create_task
    parameters:
      type: object
      properties:
        title:
          type: string
        description:
          type: string
        priority:
          type: string
          enum: [P0, P1, P2, P3]
        domain:
          type: string
          enum: [personal, work, family]
      required: [title]

  update_task:
    description: "Update a task's status, priority, or notes"
    domain: tasks
    safety: write
    handler: donna.chat.actions.tasks.update_task
    parameters:
      type: object
      properties:
        task_id:
          type: string
        status:
          type: string
        priority:
          type: string
        notes:
          type: string
      required: [task_id]

  reschedule_task:
    description: "Reschedule a task to a new date"
    domain: tasks
    safety: write
    handler: donna.chat.actions.tasks.reschedule_task
    parameters:
      type: object
      properties:
        task_id:
          type: string
        scheduled_start:
          type: string
          description: "ISO 8601 date or datetime"
      required: [task_id, scheduled_start]

  # ── Vault ──────────────────────────────────────────
  read_vault_file:
    description: "Read a file from the vault"
    domain: vault
    safety: read
    handler: donna.chat.actions.vault.read_vault_file
    parameters:
      type: object
      properties:
        path:
          type: string
          description: "Relative path within the vault"
      required: [path]

  create_vault_note:
    description: "Create a new note file in the vault"
    domain: vault
    safety: write
    handler: donna.chat.actions.vault.create_vault_note
    parameters:
      type: object
      properties:
        title:
          type: string
        content:
          type: string
        folder:
          type: string
          description: "Subfolder within vault (optional)"
      required: [title, content]

  list_vault_files:
    description: "List files in the vault, optionally within a folder"
    domain: vault
    safety: read
    handler: donna.chat.actions.vault.list_vault_files
    parameters:
      type: object
      properties:
        folder:
          type: string
      required: []

  # ── Skills ─────────────────────────────────────────
  execute_skill:
    description: "Run a skill and report results"
    domain: skills
    safety: confirm
    handler: donna.chat.actions.skills.execute_skill
    parameters:
      type: object
      properties:
        skill_name:
          type: string
        input_data:
          type: object
      required: [skill_name]

  list_skills:
    description: "List available skills"
    domain: skills
    safety: read
    handler: donna.chat.actions.skills.list_skills
    parameters:
      type: object
      properties: {}
      required: []

  create_skill_draft:
    description: "Draft a new skill definition"
    domain: skills
    safety: write
    handler: donna.chat.actions.skills.create_skill_draft
    parameters:
      type: object
      properties:
        name:
          type: string
        description:
          type: string
        steps:
          type: array
          items:
            type: string
      required: [name, description]

  # ── Automations ────────────────────────────────────
  create_automation:
    description: "Create a new automation rule"
    domain: automations
    safety: confirm
    handler: donna.chat.actions.automations.create_automation
    parameters:
      type: object
      properties:
        name:
          type: string
        trigger:
          type: string
        action:
          type: string
        skill_name:
          type: string
      required: [name, trigger, skill_name]

  list_automations:
    description: "List active automations"
    domain: automations
    safety: read
    handler: donna.chat.actions.automations.list_automations
    parameters:
      type: object
      properties: {}
      required: []

  # ── Debug ──────────────────────────────────────────
  get_debug_data:
    description: "System status, queue depth, recent errors"
    domain: debug
    safety: read
    handler: donna.chat.actions.debug.get_debug_data
    parameters:
      type: object
      properties: {}
      required: []

  get_agent_status:
    description: "Agent run history and current status"
    domain: debug
    safety: read
    handler: donna.chat.actions.debug.get_agent_status
    parameters:
      type: object
      properties:
        agent_name:
          type: string
      required: []
```

- [ ] **Step 2: Update `config/chat.yaml`**

Add these sections at the end of the `chat:` block:

```yaml
  quick_panel:
    ttl_minutes: 15
    channel: dashboard_quick
  actions:
    enabled: true
```

- [ ] **Step 3: Update ChatConfig model**

In `src/donna/chat/config.py`, add these Pydantic models and fields:

```python
class QuickPanelConfig(BaseModel):
    ttl_minutes: int = 15
    channel: str = "dashboard_quick"


class ActionsConfig(BaseModel):
    enabled: bool = True
```

Add them as fields on `ChatConfig`:

```python
class ChatConfig(BaseModel):
    persona: PersonaConfig = Field(default_factory=PersonaConfig)
    sessions: SessionsConfig = Field(default_factory=SessionsConfig)
    escalation: EscalationConfig = Field(default_factory=EscalationConfig)
    intents: IntentsConfig = Field(default_factory=IntentsConfig)
    discord: DiscordChatConfig = Field(default_factory=DiscordChatConfig)
    quick_panel: QuickPanelConfig = Field(default_factory=QuickPanelConfig)
    actions: ActionsConfig = Field(default_factory=ActionsConfig)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_chat_engine.py -v
```

Expected: existing tests pass (new config fields have defaults).

- [ ] **Step 5: Commit**

```bash
git add config/chat_actions.yaml config/chat.yaml src/donna/chat/config.py
git commit -m "feat(chat): add action registry config and quick panel settings"
```

---

### Task 7: Action Prompts

**Files:**
- Create: `prompts/chat/extract_action_params.md`
- Create: `prompts/chat/summarize_action_result.md`
- Modify: `prompts/chat/classify_intent.md`

- [ ] **Step 1: Update intent classifier prompt**

Replace `prompts/chat/classify_intent.md`:

```markdown
# Intent Classification

Classify the user's message into exactly one intent category, and optionally suggest which action domain and specific action it maps to.

## Categories

- **task_query**: Asking about tasks — status, list, details, schedule, deadlines
- **task_action**: Requesting a change — create, reschedule, reprioritize, complete, cancel a task
- **agent_output_query**: Asking about what agents did — prep results, research output, agent activity
- **planning**: Asking for planning advice — "what should I focus on?", "am I overcommitted?", workload assessment
- **freeform**: General conversation, not tied to a specific system action or data lookup
- **escalation_request**: User explicitly asks for Claude's help or a more capable model

## Action Domains

If the message maps to a specific action, provide the `domain` and `action_hint`:

- **tasks**: query_tasks, get_task, create_task, update_task, reschedule_task
- **vault**: read_vault_file, create_vault_note, list_vault_files
- **skills**: execute_skill, list_skills, create_skill_draft
- **automations**: create_automation, list_automations
- **debug**: get_debug_data, get_agent_status

## Output

Respond with a JSON object:

```json
{
  "intent": "task_action",
  "domain": "tasks",
  "action_hint": "create_task",
  "needs_escalation": false,
  "escalation_reason": null
}
```

Set `domain` and `action_hint` to null if the message doesn't clearly map to a specific action. Set `needs_escalation` to true ONLY if the question requires complex multi-step reasoning, long-horizon planning, or nuanced judgment beyond your capability.

## Current Context

Today's date: {{ current_date }}
User: {{ user_name }}

## User Message

{{ user_input }}
```

- [ ] **Step 2: Create parameter extraction prompt**

Create `prompts/chat/extract_action_params.md`:

```markdown
# Action Parameter Extraction

Extract the parameters for the action **{{ action_name }}** from the user's message.

## Action Description

{{ action_description }}

## Parameter Schema

{{ parameter_schema }}

## Dashboard Context

{{ dashboard_context }}

When the user says "this", "it", or similar pronouns, resolve them using the dashboard context above.

## Conversation History

{{ conversation_history }}

## User Message

{{ user_input }}

## Output

Respond with a JSON object containing ONLY the parameter values. Use the exact field names from the schema. Omit fields that cannot be determined from the message. Example:

```json
{{ example_output }}
```
```

- [ ] **Step 3: Create action result summary prompt**

Create `prompts/chat/summarize_action_result.md`:

```markdown
# Action Result Summary

Summarize the result of the action for the user in a conversational tone.

## Action Performed

**{{ action_name }}**: {{ action_description }}

## Parameters Used

{{ params_json }}

## Result

Success: {{ success }}
{{ result_data }}

## Instructions

- If the action succeeded, confirm what was done using the result data
- If the action failed, explain what went wrong clearly
- Keep it concise — one to three sentences
- For read actions, present the data in a readable format
- For write actions, confirm the change that was made

## Output

Respond with a JSON object:

```json
{
  "response_text": "Your summary of what happened",
  "suggested_actions": []
}
```
```

- [ ] **Step 4: Commit**

```bash
git add prompts/chat/classify_intent.md prompts/chat/extract_action_params.md prompts/chat/summarize_action_result.md
git commit -m "feat(chat): add action extraction and summarization prompts"
```

---

### Task 8: Engine Pipeline Integration

**Files:**
- Modify: `src/donna/chat/engine.py`
- Modify: `src/donna/chat/context.py`
- Modify: `src/donna/api/routes/chat.py`
- Modify: `src/donna/api/__init__.py`
- Test: `tests/unit/test_chat_engine.py`

This is the core integration task — wiring the ActionRegistry into the ConversationEngine.

- [ ] **Step 1: Update engine `__init__` to accept ActionRegistry**

In `src/donna/chat/engine.py`, update the constructor:

```python
from donna.chat.actions import ActionRegistry
from donna.chat.types import (
    ActionContext,
    ActionResult,
    ChatIntent,
    ChatResponse,
)
```

```python
class ConversationEngine:
    def __init__(
        self,
        db: Database,
        router: ModelRouter,
        config: ChatConfig,
        project_root: Path,
        action_registry: ActionRegistry | None = None,
    ) -> None:
        self._db = db
        self._router = router
        self._config = config
        self._project_root = project_root
        self._action_registry = action_registry
```

- [ ] **Step 2: Add dashboard context to `handle_message`**

Update the `handle_message` signature to accept dashboard context:

```python
async def handle_message(
    self,
    session_id: str | None,
    user_id: str,
    text: str,
    channel: str,
    dashboard_context: dict[str, Any] | None = None,
) -> ChatResponse:
```

- [ ] **Step 3: Add action pipeline after intent classification**

After the intent classification block and before the "Load context" section in `handle_message`, add the action execution branch:

```python
        # ── Action pipeline ──────────────────────────────
        if (
            self._action_registry is not None
            and self._config.actions.enabled
        ):
            action_result = await self._try_action_pipeline(
                intent_result=intent_result,
                text=text,
                user_id=user_id,
                session=session,
                dashboard_context=dashboard_context,
            )
            if action_result is not None:
                return action_result
```

- [ ] **Step 4: Add `_try_action_pipeline` method**

Add this method to `ConversationEngine`:

```python
    async def _try_action_pipeline(
        self,
        intent_result: dict[str, Any],
        text: str,
        user_id: str,
        session: Any,
        dashboard_context: dict[str, Any] | None,
    ) -> ChatResponse | None:
        assert self._action_registry is not None

        domain = intent_result.get("domain")
        action_hint = intent_result.get("action_hint")

        if not domain and not action_hint:
            return None

        action = self._action_registry.match(
            domain=domain, action_hint=action_hint,
        )
        if action is None:
            return None

        log = logger.bind(action=action.name, domain=action.domain)

        params = await self._extract_action_params(action, text, session, dashboard_context)

        required = action.parameters.get("required", [])
        missing = [r for r in required if r not in params or params[r] is None]
        if missing:
            return ChatResponse(
                text=f"I need a bit more info to do that. Missing: {', '.join(missing)}",
                session_id=session.id,
                session_pinned_task_id=getattr(session, "pinned_task_id", None),
            )

        if action.safety == "confirm":
            pending = self._action_registry.format_pending_action(action.name, params)
            await self._db.update_chat_session(session.id, pending_action=pending)
            desc = action.description
            param_summary = ", ".join(f"{k}={v}" for k, v in params.items() if v)
            return ChatResponse(
                text=f"I'll {desc.lower()} ({param_summary}). Go ahead?",
                session_id=session.id,
                needs_escalation=False,
                session_pinned_task_id=getattr(session, "pinned_task_id", None),
            )

        ctx = ActionContext(
            db=self._db,
            user_id=user_id,
            session_id=session.id,
            config=self._config,
            dashboard_context=dashboard_context,
        )
        result = await self._action_registry.execute(action.name, params, ctx)

        log.info("action_executed", success=result.success, summary=result.summary)

        response_text = await self._summarize_action_result(action, params, result, session)

        await self._db.add_chat_message(
            session_id=session.id,
            role="assistant",
            content=response_text,
            intent=action.domain,
            action_name=action.name,
            action_result=json.dumps(result.data) if result.data else None,
        )

        return ChatResponse(
            text=response_text,
            session_id=session.id,
            suggested_actions=result.data.get("suggested_actions", []),
            session_pinned_task_id=getattr(session, "pinned_task_id", None),
        )
```

Add `import json` at the top of the file.

- [ ] **Step 5: Add `_extract_action_params` method**

```python
    async def _extract_action_params(
        self,
        action: Any,
        text: str,
        session: Any,
        dashboard_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        template_path = self._project_root / "prompts" / "chat" / "extract_action_params.md"
        if not template_path.exists():
            return {}
        template = template_path.read_text()

        import json as json_mod
        ctx_str = ""
        if dashboard_context:
            page = dashboard_context.get("page", "unknown")
            selected = dashboard_context.get("selected_item")
            if selected:
                ctx_str = (
                    f"User is viewing the {page.title()} page and has selected "
                    f"{selected.get('type', 'item')} '{selected.get('label', '')}' "
                    f"(id: {selected.get('id', '')})."
                )
            else:
                ctx_str = f"User is viewing the {page.title()} page."

        example_output = json_mod.dumps(
            {p: f"<{p}>" for p in action.parameters.get("properties", {}).keys()},
            indent=2,
        )

        history = await self._db.list_chat_messages(session.id, limit=10)
        history_text = "\n".join(
            f"{'User' if m.role == 'user' else 'Assistant'}: {m.content}"
            for m in history[-6:]
        )

        from donna.chat.context import render_chat_prompt
        prompt = render_chat_prompt(
            template=template,
            user_input=text,
            action_name=action.name,
            action_description=action.description,
            parameter_schema=json_mod.dumps(action.parameters, indent=2),
            dashboard_context=ctx_str,
            conversation_history=history_text,
            example_output=example_output,
        )

        result, _ = await self._router.complete(
            prompt=prompt,
            task_type="classify_chat_intent",
            user_id="system",
        )
        return result
```

- [ ] **Step 6: Add `_summarize_action_result` method**

```python
    async def _summarize_action_result(
        self,
        action: Any,
        params: dict[str, Any],
        result: ActionResult,
        session: Any,
    ) -> str:
        if result.summary and not result.data:
            return result.summary

        template_path = self._project_root / "prompts" / "chat" / "summarize_action_result.md"
        if not template_path.exists():
            return result.summary or result.error or "Action completed."

        template = template_path.read_text()

        import json as json_mod
        from donna.chat.context import render_chat_prompt
        prompt = render_chat_prompt(
            template=template,
            user_input="",
            action_name=action.name,
            action_description=action.description,
            params_json=json_mod.dumps(params, indent=2),
            success=str(result.success),
            result_data=json_mod.dumps(result.data, indent=2, default=str) if result.data else (result.error or "No data"),
        )

        resp, _ = await self._router.complete(
            prompt=prompt,
            task_type="chat_respond",
            user_id="system",
        )
        return resp.get("response_text", result.summary or "Done.")
```

- [ ] **Step 7: Add confirm action handler**

Add this method to `ConversationEngine`:

```python
    async def handle_confirm(
        self, session_id: str, user_id: str, confirmed: bool,
    ) -> ChatResponse:
        session = await self._db.get_chat_session(session_id)
        if session is None:
            return ChatResponse(text="Session not found.", session_id=session_id)

        pending_raw = getattr(session, "pending_action", None)
        if not pending_raw:
            return ChatResponse(
                text="Nothing pending to confirm.",
                session_id=session_id,
            )

        if not confirmed:
            await self._db.update_chat_session(session_id, pending_action=None)
            return ChatResponse(text="Cancelled.", session_id=session_id)

        action_name, params = ActionRegistry.parse_pending_action(pending_raw)
        await self._db.update_chat_session(session_id, pending_action=None)

        assert self._action_registry is not None
        action = self._action_registry.get(action_name)
        if action is None:
            return ChatResponse(text=f"Unknown action: {action_name}", session_id=session_id)

        ctx = ActionContext(
            db=self._db, user_id=user_id, session_id=session_id,
            config=self._config, dashboard_context=None,
        )
        result = await self._action_registry.execute(action_name, params, ctx)
        response_text = await self._summarize_action_result(action, params, result, session)

        await self._db.add_chat_message(
            session_id=session_id,
            role="assistant",
            content=response_text,
            intent=action.domain,
            action_name=action_name,
            action_result=json.dumps(result.data) if result.data else None,
        )

        return ChatResponse(text=response_text, session_id=session_id)
```

- [ ] **Step 8: Update `render_chat_prompt` to accept extra kwargs**

In `src/donna/chat/context.py`, update `render_chat_prompt` to handle additional template variables:

```python
def render_chat_prompt(
    template: str,
    user_input: str,
    user_name: str = "Nick",
    session_context: str = "",
    intent_context: str = "",
    conversation_history: str = "",
    tz: zoneinfo.ZoneInfo | None = None,
    **extra: str,
) -> str:
```

At the end of the existing substitutions, add:

```python
    for key, value in extra.items():
        rendered = rendered.replace(f"{{{{ {key} }}}}", str(value))
    return rendered
```

- [ ] **Step 9: Add confirm endpoint to API routes**

In `src/donna/api/routes/chat.py`, add:

```python
@router.post("/sessions/{session_id}/confirm")
async def confirm_action(
    session_id: str,
    user_id: CurrentUser,
    body: dict[str, Any] = Body(...),
    engine: Any = Depends(get_chat_engine),
    db: Any = Depends(get_database),
) -> dict[str, Any]:
    """Confirm or reject a pending action."""
    await _require_session_owner(db, session_id, user_id)
    confirmed = body.get("confirmed", False)
    resp = await engine.handle_confirm(session_id, user_id, confirmed)
    return {
        "session_id": resp.session_id,
        "text": resp.text,
        "needs_escalation": resp.needs_escalation,
        "suggested_actions": resp.suggested_actions,
    }
```

Also add `GET /chat/actions` endpoint:

```python
@router.get("/actions")
async def list_actions(
    engine: Any = Depends(get_chat_engine),
) -> dict[str, Any]:
    """List available chat actions."""
    if not hasattr(engine, "_action_registry") or engine._action_registry is None:
        return {"actions": []}
    return {
        "actions": [
            {
                "name": a.name,
                "description": a.description,
                "domain": a.domain,
                "safety": a.safety,
            }
            for a in engine._action_registry.list()
        ],
    }
```

- [ ] **Step 10: Update `send_message` route to pass dashboard context**

In the existing `send_message` function in `src/donna/api/routes/chat.py`, update to pass context:

```python
    context = body.get("context")

    resp: ChatResponse = await engine.handle_message(
        session_id=sid,
        user_id=user_id,
        text=text,
        channel=channel,
        dashboard_context=context,
    )
```

- [ ] **Step 11: Wire ActionRegistry in app startup**

In `src/donna/api/__init__.py`, find where the chat engine is created. After the existing engine initialization, load the action registry and pass it:

```python
from donna.chat.actions import ActionRegistry
```

Where the engine is instantiated, add:

```python
action_registry = ActionRegistry.from_yaml(config_dir / "chat_actions.yaml")
```

And pass `action_registry=action_registry` to the `ConversationEngine` constructor.

- [ ] **Step 12: Update `ChatSession` dataclass for `pending_action`**

In `src/donna/chat/types.py`, add `pending_action` to `ChatSession`:

```python
@dataclasses.dataclass(frozen=True)
class ChatSession:
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
    pending_action: str | None = None
```

Also update the database method that constructs `ChatSession` objects to read the `pending_action` column (in `create_chat_session`, `get_chat_session`, `get_active_chat_session`).

- [ ] **Step 13: Run all tests**

```bash
pytest tests/unit/test_chat_engine.py tests/unit/test_action_registry.py tests/integration/test_chat_api.py -v
```

Expected: all pass. The existing engine tests use mocks, so the new optional `action_registry` parameter shouldn't break them.

- [ ] **Step 14: Commit**

```bash
git add src/donna/chat/engine.py src/donna/chat/context.py src/donna/chat/types.py src/donna/api/routes/chat.py src/donna/api/__init__.py
git commit -m "feat(chat): integrate action pipeline into conversation engine"
```

---

## Phase 3: Action Handlers

### Task 9: Task Action Handlers

**Files:**
- Create: `src/donna/chat/actions/tasks.py`
- Test: `tests/unit/test_action_handlers_tasks.py`

- [ ] **Step 1: Create task handlers**

Create `src/donna/chat/actions/tasks.py`:

```python
"""Chat action handlers for task operations."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from donna.chat.types import ActionContext, ActionResult
from donna.tasks.db_models import TaskDomain, TaskStatus


async def query_tasks(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    status = None
    if params.get("status"):
        try:
            status = TaskStatus(params["status"])
        except ValueError:
            return ActionResult(success=False, error=f"Invalid status: {params['status']}")

    domain = None
    if params.get("domain"):
        try:
            domain = TaskDomain(params["domain"])
        except ValueError:
            return ActionResult(success=False, error=f"Invalid domain: {params['domain']}")

    tasks = await ctx.db.list_tasks(
        user_id=ctx.user_id, status=status, domain=domain,
    )

    task_list = [
        {"id": t.id, "title": t.title, "status": t.status, "priority": t.priority, "domain": t.domain}
        for t in tasks
    ]
    return ActionResult(
        success=True,
        data={"tasks": task_list, "count": len(task_list)},
        summary=f"Found {len(task_list)} task(s).",
    )


async def get_task(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    task_id = params.get("task_id")
    title_search = params.get("title_search")

    if task_id:
        task = await ctx.db.get_task(task_id)
        if task is None:
            return ActionResult(success=False, error=f"Task {task_id} not found.")
    elif title_search:
        all_tasks = await ctx.db.list_tasks(user_id=ctx.user_id)
        search_lower = title_search.lower()
        matches = [t for t in all_tasks if search_lower in t.title.lower()]
        if not matches:
            return ActionResult(success=False, error=f"No task matching '{title_search}'.")
        task = matches[0]
    else:
        if ctx.dashboard_context and ctx.dashboard_context.get("selected_item"):
            item = ctx.dashboard_context["selected_item"]
            if item.get("type") == "task":
                task = await ctx.db.get_task(item["id"])
                if task is None:
                    return ActionResult(success=False, error="Selected task not found.")
            else:
                return ActionResult(success=False, error="No task ID or search term provided.")
        else:
            return ActionResult(success=False, error="No task ID or search term provided.")

    return ActionResult(
        success=True,
        data={
            "id": task.id, "title": task.title, "description": task.description,
            "status": task.status, "priority": task.priority, "domain": task.domain,
            "notes": task.notes, "created_at": str(task.created_at),
            "scheduled_start": str(task.scheduled_start) if task.scheduled_start else None,
        },
        summary=f"Task '{task.title}' — {task.status}, priority {task.priority}.",
    )


async def create_task(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    title = params["title"]
    description = params.get("description")
    priority_str = params.get("priority", "P2")
    priority_map = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    priority = priority_map.get(priority_str, 2)

    domain = TaskDomain.PERSONAL
    if params.get("domain"):
        try:
            domain = TaskDomain(params["domain"])
        except ValueError:
            pass

    from donna.tasks.db_models import InputChannel
    task = await ctx.db.create_task(
        user_id=ctx.user_id,
        title=title,
        description=description,
        domain=domain,
        priority=priority,
        created_via=InputChannel.APP,
    )
    return ActionResult(
        success=True,
        data={"id": task.id, "title": task.title, "status": task.status},
        summary=f"Created task '{task.title}' (id: {task.id}).",
    )


async def update_task(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    task_id = params.get("task_id")
    if not task_id:
        return ActionResult(success=False, error="task_id is required.")

    updates: dict[str, Any] = {}
    if params.get("status"):
        updates["status"] = params["status"]
    if params.get("priority"):
        priority_map = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        updates["priority"] = priority_map.get(params["priority"], 2)
    if params.get("notes"):
        updates["notes"] = params["notes"]

    if not updates:
        return ActionResult(success=False, error="No fields to update.")

    task = await ctx.db.update_task(task_id, **updates)
    if task is None:
        return ActionResult(success=False, error=f"Task {task_id} not found.")

    return ActionResult(
        success=True,
        data={"id": task.id, "title": task.title, "status": task.status, "priority": task.priority},
        summary=f"Updated task '{task.title}'.",
    )


async def reschedule_task(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    task_id = params.get("task_id")
    scheduled_start_str = params.get("scheduled_start")

    if not task_id or not scheduled_start_str:
        return ActionResult(success=False, error="task_id and scheduled_start are required.")

    try:
        scheduled_start = datetime.fromisoformat(scheduled_start_str)
    except ValueError:
        return ActionResult(success=False, error=f"Invalid date format: {scheduled_start_str}")

    task = await ctx.db.update_task(task_id, scheduled_start=scheduled_start.isoformat())
    if task is None:
        return ActionResult(success=False, error=f"Task {task_id} not found.")

    return ActionResult(
        success=True,
        data={"id": task.id, "title": task.title, "scheduled_start": scheduled_start_str},
        summary=f"Rescheduled '{task.title}' to {scheduled_start_str}.",
    )
```

- [ ] **Step 2: Write tests**

Create `tests/unit/test_action_handlers_tasks.py`:

```python
"""Tests for task action handlers."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from donna.chat.actions.tasks import query_tasks, get_task, create_task, update_task, reschedule_task
from donna.chat.types import ActionContext


@pytest.fixture
def ctx() -> ActionContext:
    db = AsyncMock()
    return ActionContext(
        db=db, user_id="nick", session_id="sess-1",
        config=MagicMock(), dashboard_context=None,
    )


@pytest.mark.asyncio
async def test_query_tasks_empty(ctx: ActionContext) -> None:
    ctx.db.list_tasks.return_value = []
    result = await query_tasks({}, ctx)
    assert result.success is True
    assert result.data["count"] == 0


@pytest.mark.asyncio
async def test_query_tasks_with_status_filter(ctx: ActionContext) -> None:
    mock_task = MagicMock(id="t1", title="Test", status="backlog", priority=2, domain="personal")
    ctx.db.list_tasks.return_value = [mock_task]
    result = await query_tasks({"status": "backlog"}, ctx)
    assert result.success is True
    assert result.data["count"] == 1


@pytest.mark.asyncio
async def test_query_tasks_invalid_status(ctx: ActionContext) -> None:
    result = await query_tasks({"status": "invalid_status"}, ctx)
    assert result.success is False
    assert "Invalid status" in (result.error or "")


@pytest.mark.asyncio
async def test_get_task_by_id(ctx: ActionContext) -> None:
    mock_task = MagicMock(
        id="t1", title="Fix auth", description="desc", status="in_progress",
        priority=1, domain="work", notes=None, created_at="2026-05-15",
        scheduled_start=None,
    )
    ctx.db.get_task.return_value = mock_task
    result = await get_task({"task_id": "t1"}, ctx)
    assert result.success is True
    assert result.data["title"] == "Fix auth"


@pytest.mark.asyncio
async def test_get_task_not_found(ctx: ActionContext) -> None:
    ctx.db.get_task.return_value = None
    result = await get_task({"task_id": "nonexistent"}, ctx)
    assert result.success is False


@pytest.mark.asyncio
async def test_create_task(ctx: ActionContext) -> None:
    mock_task = MagicMock(id="t-new", title="New task", status="backlog")
    ctx.db.create_task.return_value = mock_task
    result = await create_task({"title": "New task"}, ctx)
    assert result.success is True
    assert result.data["id"] == "t-new"


@pytest.mark.asyncio
async def test_update_task(ctx: ActionContext) -> None:
    mock_task = MagicMock(id="t1", title="Fix auth", status="done", priority=1)
    ctx.db.update_task.return_value = mock_task
    result = await update_task({"task_id": "t1", "status": "done"}, ctx)
    assert result.success is True


@pytest.mark.asyncio
async def test_update_task_no_fields(ctx: ActionContext) -> None:
    result = await update_task({"task_id": "t1"}, ctx)
    assert result.success is False
    assert "No fields" in (result.error or "")


@pytest.mark.asyncio
async def test_reschedule_task(ctx: ActionContext) -> None:
    mock_task = MagicMock(id="t1", title="Review", scheduled_start="2026-05-20")
    ctx.db.update_task.return_value = mock_task
    result = await reschedule_task({"task_id": "t1", "scheduled_start": "2026-05-20"}, ctx)
    assert result.success is True


@pytest.mark.asyncio
async def test_reschedule_task_invalid_date(ctx: ActionContext) -> None:
    result = await reschedule_task({"task_id": "t1", "scheduled_start": "not-a-date"}, ctx)
    assert result.success is False
    assert "Invalid date" in (result.error or "")
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/unit/test_action_handlers_tasks.py -v
```

- [ ] **Step 4: Commit**

```bash
git add src/donna/chat/actions/tasks.py tests/unit/test_action_handlers_tasks.py
git commit -m "feat(chat): add task action handlers"
```

---

### Task 10: Vault Action Handlers

**Files:**
- Create: `src/donna/chat/actions/vault.py`
- Test: `tests/unit/test_action_handlers_vault.py`

- [ ] **Step 1: Create vault handlers**

Create `src/donna/chat/actions/vault.py`:

```python
"""Chat action handlers for vault operations."""

from __future__ import annotations

from typing import Any

from donna.chat.types import ActionContext, ActionResult


async def read_vault_file(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    path = params.get("path", "")
    if not path:
        return ActionResult(success=False, error="File path is required.")

    vault = getattr(ctx.db, "_vault", None)
    if vault is None:
        vault_attr = getattr(ctx.config, "vault", None)
        if vault_attr is None:
            return ActionResult(success=False, error="Vault is not configured.")

    try:
        from donna.memory.vault import VaultRepo
        app_state = getattr(ctx, "_app_state", None)
        if hasattr(ctx.db, "read_vault_file"):
            content = await ctx.db.read_vault_file(path)
        else:
            return ActionResult(success=False, error="Vault read not available via this interface.")
    except Exception as exc:
        return ActionResult(success=False, error=f"Failed to read vault file: {exc}")

    return ActionResult(
        success=True,
        data={"path": path, "content": content},
        summary=f"Read vault file: {path}",
    )


async def create_vault_note(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    title = params.get("title", "")
    content = params.get("content", "")
    folder = params.get("folder", "")

    if not title or not content:
        return ActionResult(success=False, error="Title and content are required.")

    try:
        if hasattr(ctx.db, "create_vault_note"):
            result = await ctx.db.create_vault_note(title=title, content=content, folder=folder)
            return ActionResult(
                success=True,
                data={"title": title, "path": result if isinstance(result, str) else f"{folder}/{title}.md"},
                summary=f"Created vault note: {title}",
            )
        return ActionResult(success=False, error="Vault write not available via this interface.")
    except Exception as exc:
        return ActionResult(success=False, error=f"Failed to create vault note: {exc}")


async def list_vault_files(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    folder = params.get("folder", "")

    try:
        if hasattr(ctx.db, "list_vault_files"):
            files = await ctx.db.list_vault_files(folder=folder)
        else:
            return ActionResult(success=False, error="Vault listing not available via this interface.")
    except Exception as exc:
        return ActionResult(success=False, error=f"Failed to list vault files: {exc}")

    file_list = [{"name": f} if isinstance(f, str) else f for f in files]
    return ActionResult(
        success=True,
        data={"files": file_list, "count": len(file_list), "folder": folder or "/"},
        summary=f"Found {len(file_list)} file(s) in vault{f'/{folder}' if folder else ''}.",
    )
```

Note: The vault handlers check for methods on the db/config objects dynamically because the vault interface may vary. These handlers are safe stubs that will integrate with whatever vault implementation exists.

- [ ] **Step 2: Write tests**

Create `tests/unit/test_action_handlers_vault.py`:

```python
"""Tests for vault action handlers."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from donna.chat.actions.vault import read_vault_file, create_vault_note, list_vault_files
from donna.chat.types import ActionContext


@pytest.fixture
def ctx() -> ActionContext:
    db = AsyncMock()
    return ActionContext(
        db=db, user_id="nick", session_id="sess-1",
        config=MagicMock(), dashboard_context=None,
    )


@pytest.mark.asyncio
async def test_read_vault_file_missing_path(ctx: ActionContext) -> None:
    result = await read_vault_file({}, ctx)
    assert result.success is False
    assert "path is required" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_read_vault_file_success(ctx: ActionContext) -> None:
    ctx.db.read_vault_file = AsyncMock(return_value="# Hello\nWorld")
    result = await read_vault_file({"path": "notes/hello.md"}, ctx)
    assert result.success is True
    assert result.data["content"] == "# Hello\nWorld"


@pytest.mark.asyncio
async def test_create_vault_note_missing_fields(ctx: ActionContext) -> None:
    result = await create_vault_note({"title": "Test"}, ctx)
    assert result.success is False


@pytest.mark.asyncio
async def test_create_vault_note_success(ctx: ActionContext) -> None:
    ctx.db.create_vault_note = AsyncMock(return_value="notes/test.md")
    result = await create_vault_note({"title": "Test", "content": "Body"}, ctx)
    assert result.success is True


@pytest.mark.asyncio
async def test_list_vault_files_success(ctx: ActionContext) -> None:
    ctx.db.list_vault_files = AsyncMock(return_value=["a.md", "b.md"])
    result = await list_vault_files({}, ctx)
    assert result.success is True
    assert result.data["count"] == 2
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/unit/test_action_handlers_vault.py -v
```

- [ ] **Step 4: Commit**

```bash
git add src/donna/chat/actions/vault.py tests/unit/test_action_handlers_vault.py
git commit -m "feat(chat): add vault action handlers"
```

---

### Task 11: Skills, Automations, and Debug Handlers

**Files:**
- Create: `src/donna/chat/actions/skills.py`
- Create: `src/donna/chat/actions/automations.py`
- Create: `src/donna/chat/actions/debug.py`
- Test: `tests/unit/test_action_handlers_misc.py`

- [ ] **Step 1: Create skills handlers**

Create `src/donna/chat/actions/skills.py`:

```python
"""Chat action handlers for skill operations."""

from __future__ import annotations

from typing import Any

from donna.chat.types import ActionContext, ActionResult


async def execute_skill(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    skill_name = params.get("skill_name", "")
    input_data = params.get("input_data", {})

    if not skill_name:
        return ActionResult(success=False, error="skill_name is required.")

    try:
        if hasattr(ctx.db, "get_skill"):
            skill = await ctx.db.get_skill(skill_name)
            if skill is None:
                return ActionResult(success=False, error=f"Skill '{skill_name}' not found.")

        if hasattr(ctx.db, "queue_skill_run"):
            run_id = await ctx.db.queue_skill_run(
                skill_name=skill_name, input_data=input_data, user_id=ctx.user_id,
            )
            return ActionResult(
                success=True,
                data={"run_id": run_id, "skill_name": skill_name, "status": "queued"},
                summary=f"Skill '{skill_name}' queued for execution (run: {run_id}).",
            )

        return ActionResult(success=False, error="Skill execution not available.")
    except Exception as exc:
        return ActionResult(success=False, error=f"Failed to execute skill: {exc}")


async def list_skills(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    try:
        if hasattr(ctx.db, "list_skills"):
            skills = await ctx.db.list_skills()
        else:
            return ActionResult(success=False, error="Skill listing not available.")
    except Exception as exc:
        return ActionResult(success=False, error=f"Failed to list skills: {exc}")

    skill_list = [
        {"name": getattr(s, "name", str(s)), "status": getattr(s, "status", "unknown")}
        for s in skills
    ]
    return ActionResult(
        success=True,
        data={"skills": skill_list, "count": len(skill_list)},
        summary=f"Found {len(skill_list)} skill(s).",
    )


async def create_skill_draft(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    name = params.get("name", "")
    description = params.get("description", "")
    steps = params.get("steps", [])

    if not name or not description:
        return ActionResult(success=False, error="Name and description are required.")

    try:
        if hasattr(ctx.db, "create_skill_draft"):
            draft_id = await ctx.db.create_skill_draft(
                name=name, description=description, steps=steps, user_id=ctx.user_id,
            )
            return ActionResult(
                success=True,
                data={"draft_id": draft_id, "name": name},
                summary=f"Created skill draft: {name}",
            )
        return ActionResult(success=False, error="Skill draft creation not available.")
    except Exception as exc:
        return ActionResult(success=False, error=f"Failed to create skill draft: {exc}")
```

- [ ] **Step 2: Create automations handlers**

Create `src/donna/chat/actions/automations.py`:

```python
"""Chat action handlers for automation operations."""

from __future__ import annotations

from typing import Any

from donna.chat.types import ActionContext, ActionResult


async def create_automation(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    name = params.get("name", "")
    trigger = params.get("trigger", "")
    skill_name = params.get("skill_name", "")

    if not name or not trigger or not skill_name:
        return ActionResult(success=False, error="Name, trigger, and skill_name are required.")

    try:
        if hasattr(ctx.db, "create_automation"):
            auto_id = await ctx.db.create_automation(
                name=name, trigger=trigger, skill_name=skill_name, user_id=ctx.user_id,
            )
            return ActionResult(
                success=True,
                data={"id": auto_id, "name": name, "trigger": trigger, "skill_name": skill_name},
                summary=f"Created automation '{name}' (trigger: {trigger}, skill: {skill_name}).",
            )
        return ActionResult(success=False, error="Automation creation not available.")
    except Exception as exc:
        return ActionResult(success=False, error=f"Failed to create automation: {exc}")


async def list_automations(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    try:
        if hasattr(ctx.db, "list_automations"):
            automations = await ctx.db.list_automations()
        else:
            return ActionResult(success=False, error="Automation listing not available.")
    except Exception as exc:
        return ActionResult(success=False, error=f"Failed to list automations: {exc}")

    auto_list = [
        {
            "name": getattr(a, "name", str(a)),
            "trigger": getattr(a, "trigger_type", "unknown"),
            "active": getattr(a, "active", True),
            "skill_name": getattr(a, "skill_name", ""),
        }
        for a in automations
    ]
    return ActionResult(
        success=True,
        data={"automations": auto_list, "count": len(auto_list)},
        summary=f"Found {len(auto_list)} automation(s).",
    )
```

- [ ] **Step 3: Create debug handlers**

Create `src/donna/chat/actions/debug.py`:

```python
"""Chat action handlers for debug/system operations."""

from __future__ import annotations

from typing import Any

from donna.chat.types import ActionContext, ActionResult


async def get_debug_data(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    data: dict[str, Any] = {}

    try:
        if hasattr(ctx.db, "get_invocation_stats"):
            stats = await ctx.db.get_invocation_stats()
            data["invocation_stats"] = stats

        if hasattr(ctx.db, "list_tasks"):
            all_tasks = await ctx.db.list_tasks(user_id=ctx.user_id)
            status_counts: dict[str, int] = {}
            for t in all_tasks:
                status_counts[t.status] = status_counts.get(t.status, 0) + 1
            data["task_status_counts"] = status_counts
            data["total_tasks"] = len(all_tasks)

    except Exception as exc:
        return ActionResult(success=False, error=f"Failed to gather debug data: {exc}")

    return ActionResult(
        success=True,
        data=data,
        summary=f"System debug data: {len(data)} sections.",
    )


async def get_agent_status(params: dict[str, Any], ctx: ActionContext) -> ActionResult:
    agent_name = params.get("agent_name")

    try:
        if hasattr(ctx.db, "list_agent_runs"):
            runs = await ctx.db.list_agent_runs(agent_name=agent_name, limit=10)
        elif hasattr(ctx.db, "list_skill_runs"):
            runs = await ctx.db.list_skill_runs(skill_name=agent_name, limit=10)
        else:
            return ActionResult(success=False, error="Agent status not available.")
    except Exception as exc:
        return ActionResult(success=False, error=f"Failed to get agent status: {exc}")

    run_list = [
        {
            "id": getattr(r, "id", str(r)),
            "status": getattr(r, "status", "unknown"),
            "started_at": str(getattr(r, "started_at", "")),
            "skill_name": getattr(r, "skill_name", agent_name or ""),
        }
        for r in runs
    ]
    return ActionResult(
        success=True,
        data={"runs": run_list, "count": len(run_list)},
        summary=f"Found {len(run_list)} recent run(s){f' for {agent_name}' if agent_name else ''}.",
    )
```

- [ ] **Step 4: Write tests**

Create `tests/unit/test_action_handlers_misc.py`:

```python
"""Tests for skills, automations, and debug action handlers."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from donna.chat.actions.skills import execute_skill, list_skills, create_skill_draft
from donna.chat.actions.automations import create_automation, list_automations
from donna.chat.actions.debug import get_debug_data, get_agent_status
from donna.chat.types import ActionContext


@pytest.fixture
def ctx() -> ActionContext:
    db = AsyncMock()
    return ActionContext(
        db=db, user_id="nick", session_id="sess-1",
        config=MagicMock(), dashboard_context=None,
    )


# ── Skills ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_skill_missing_name(ctx: ActionContext) -> None:
    result = await execute_skill({}, ctx)
    assert result.success is False


@pytest.mark.asyncio
async def test_execute_skill_queued(ctx: ActionContext) -> None:
    ctx.db.get_skill = AsyncMock(return_value=MagicMock())
    ctx.db.queue_skill_run = AsyncMock(return_value="run-123")
    result = await execute_skill({"skill_name": "product_watch"}, ctx)
    assert result.success is True
    assert result.data["status"] == "queued"


@pytest.mark.asyncio
async def test_list_skills(ctx: ActionContext) -> None:
    ctx.db.list_skills = AsyncMock(return_value=[
        MagicMock(name="product_watch", status="active"),
        MagicMock(name="email_triage", status="active"),
    ])
    result = await list_skills({}, ctx)
    assert result.success is True
    assert result.data["count"] == 2


@pytest.mark.asyncio
async def test_create_skill_draft_missing_fields(ctx: ActionContext) -> None:
    result = await create_skill_draft({"name": "test"}, ctx)
    assert result.success is False


# ── Automations ─────────────────────────────────────

@pytest.mark.asyncio
async def test_create_automation_missing_fields(ctx: ActionContext) -> None:
    result = await create_automation({"name": "test"}, ctx)
    assert result.success is False


@pytest.mark.asyncio
async def test_create_automation_success(ctx: ActionContext) -> None:
    ctx.db.create_automation = AsyncMock(return_value="auto-123")
    result = await create_automation(
        {"name": "Daily watch", "trigger": "schedule", "skill_name": "product_watch"},
        ctx,
    )
    assert result.success is True


@pytest.mark.asyncio
async def test_list_automations(ctx: ActionContext) -> None:
    ctx.db.list_automations = AsyncMock(return_value=[
        MagicMock(name="Daily watch", trigger_type="schedule", active=True, skill_name="product_watch"),
    ])
    result = await list_automations({}, ctx)
    assert result.success is True
    assert result.data["count"] == 1


# ── Debug ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_debug_data(ctx: ActionContext) -> None:
    ctx.db.list_tasks = AsyncMock(return_value=[
        MagicMock(status="in_progress"),
        MagicMock(status="done"),
        MagicMock(status="done"),
    ])
    result = await get_debug_data({}, ctx)
    assert result.success is True
    assert result.data["total_tasks"] == 3


@pytest.mark.asyncio
async def test_get_agent_status(ctx: ActionContext) -> None:
    ctx.db.list_skill_runs = AsyncMock(return_value=[
        MagicMock(id="r1", status="complete", started_at="2026-05-15", skill_name="product_watch"),
    ])
    result = await get_agent_status({"agent_name": "product_watch"}, ctx)
    assert result.success is True
    assert result.data["count"] == 1
```

- [ ] **Step 5: Run all handler tests**

```bash
pytest tests/unit/test_action_handlers_tasks.py tests/unit/test_action_handlers_vault.py tests/unit/test_action_handlers_misc.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/donna/chat/actions/skills.py src/donna/chat/actions/automations.py src/donna/chat/actions/debug.py tests/unit/test_action_handlers_misc.py
git commit -m "feat(chat): add skills, automations, and debug action handlers"
```

---

## Phase 4: Frontend

### Task 12: CenterDialog Primitive

**Files:**
- Create: `donna-ui/src/primitives/CenterDialog.tsx`
- Create: `donna-ui/src/primitives/CenterDialog.module.css`

- [ ] **Step 1: Create CenterDialog component**

Create `donna-ui/src/primitives/CenterDialog.tsx`:

```typescript
import * as RadixDialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import styles from "./CenterDialog.module.css";

interface CenterDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  children: ReactNode;
}

export function CenterDialog({ open, onOpenChange, title, children }: CenterDialogProps) {
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className={styles.overlay} />
        <RadixDialog.Content className={styles.content}>
          <RadixDialog.Title className={styles.title}>{title}</RadixDialog.Title>
          <RadixDialog.Description className="sr-only">
            Detail dialog
          </RadixDialog.Description>
          {children}
          <RadixDialog.Close className={styles.close} aria-label="Close">
            <X size={16} />
          </RadixDialog.Close>
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}
```

- [ ] **Step 2: Create CenterDialog styles**

Create `donna-ui/src/primitives/CenterDialog.module.css`:

```css
.overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  z-index: var(--z-dialog);
  animation: fadeIn var(--duration-fast) var(--ease-out);
}

.content {
  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 90vw;
  max-width: 640px;
  max-height: 85vh;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-card);
  box-shadow: var(--shadow-dialog);
  padding: var(--space-5);
  z-index: var(--z-dialog);
  overflow: auto;
  animation: scaleIn var(--duration-base) var(--ease-out);
}

.close {
  position: absolute;
  top: var(--space-3);
  right: var(--space-3);
  background: transparent;
  border: 0;
  color: var(--color-text-muted);
  cursor: pointer;
  padding: 4px;
}
.close:hover { color: var(--color-accent); }

.title {
  font-family: var(--font-display);
  font-weight: 300;
  font-size: var(--text-section);
  color: var(--color-text);
  margin: 0 0 var(--space-4) 0;
}

@keyframes fadeIn {
  from { opacity: 0; }
  to   { opacity: 1; }
}

@keyframes scaleIn {
  from { opacity: 0; transform: translate(-50%, -50%) scale(0.96); }
  to   { opacity: 1; transform: translate(-50%, -50%) scale(1); }
}

@media (prefers-reduced-motion: reduce) {
  .overlay, .content { animation: none; }
}
```

- [ ] **Step 3: Verify build**

```bash
cd /mnt/donna/donna/donna-ui && npx tsc --noEmit && npx vite build
```

- [ ] **Step 4: Commit**

```bash
git add donna-ui/src/primitives/CenterDialog.tsx donna-ui/src/primitives/CenterDialog.module.css
git commit -m "feat(ui): add CenterDialog primitive"
```

---

### Task 13: DashboardContext Provider

**Files:**
- Create: `donna-ui/src/context/DashboardContext.tsx`
- Modify: `donna-ui/src/layout/AppShell.tsx`

- [ ] **Step 1: Create DashboardContext**

Create `donna-ui/src/context/DashboardContext.tsx`:

```typescript
import { createContext, useContext, useState, useCallback, useMemo, useEffect } from "react";
import { useLocation } from "react-router-dom";
import type { ReactNode } from "react";

export interface SelectedItem {
  type: "task" | "agent" | "skill" | "log_entry" | "vault_file" | "automation";
  id: string;
  label: string;
}

interface DashboardContextValue {
  currentPage: string;
  selectedItem: SelectedItem | null;
  setSelectedItem: (item: SelectedItem | null) => void;
}

const DashboardCtx = createContext<DashboardContextValue>({
  currentPage: "",
  selectedItem: null,
  setSelectedItem: () => {},
});

function pageFromPathname(pathname: string): string {
  const segment = pathname.split("/")[1] || "dashboard";
  return segment || "dashboard";
}

export function DashboardProvider({ children }: { children: ReactNode }) {
  const location = useLocation();
  const currentPage = pageFromPathname(location.pathname);
  const [selectedItem, setSelectedItemRaw] = useState<SelectedItem | null>(null);

  useEffect(() => {
    setSelectedItemRaw(null);
  }, [currentPage]);

  const setSelectedItem = useCallback((item: SelectedItem | null) => {
    setSelectedItemRaw(item);
  }, []);

  const value = useMemo(
    () => ({ currentPage, selectedItem, setSelectedItem }),
    [currentPage, selectedItem, setSelectedItem],
  );

  return <DashboardCtx.Provider value={value}>{children}</DashboardCtx.Provider>;
}

export function useDashboardContext(): DashboardContextValue {
  return useContext(DashboardCtx);
}
```

- [ ] **Step 2: Wrap AppShell with DashboardProvider**

In `donna-ui/src/layout/AppShell.tsx`, add the import and wrap the shell:

```typescript
import { Outlet } from "react-router-dom";
import { Toaster } from "sonner";
import { Sidebar } from "./Sidebar";
import { DashboardProvider } from "../context/DashboardContext";
import useKeyboardShortcuts from "../hooks/useKeyboardShortcuts";
import KeyboardShortcutsModal from "../components/KeyboardShortcutsModal";
import styles from "./AppShell.module.css";

export function AppShell() {
  useKeyboardShortcuts();

  return (
    <DashboardProvider>
      <div className={styles.shell}>
        <Sidebar />
        <main className={styles.main}>
          <Outlet />
        </main>

        <KeyboardShortcutsModal />

        <Toaster
          position="top-right"
          theme="dark"
          toastOptions={{
            style: {
              background: "var(--color-surface)",
              color: "var(--color-text)",
              border: "1px solid var(--color-border)",
              fontFamily: "var(--font-body)",
              fontSize: "var(--text-body)",
              borderRadius: "var(--radius-control)",
            },
          }}
        />
      </div>
    </DashboardProvider>
  );
}
```

- [ ] **Step 3: Verify build**

```bash
cd /mnt/donna/donna/donna-ui && npx tsc --noEmit && npx vite build
```

- [ ] **Step 4: Commit**

```bash
git add donna-ui/src/context/DashboardContext.tsx donna-ui/src/layout/AppShell.tsx
git commit -m "feat(ui): add DashboardContext provider"
```

---

### Task 14: Quick Chat Panel + Floating Button

**Files:**
- Create: `donna-ui/src/components/QuickChatPanel.tsx`
- Create: `donna-ui/src/components/QuickChatPanel.module.css`
- Create: `donna-ui/src/components/QuickChatButton.tsx`
- Create: `donna-ui/src/components/QuickChatButton.module.css`
- Modify: `donna-ui/src/layout/AppShell.tsx`
- Modify: `donna-ui/src/api/chat.ts`

- [ ] **Step 1: Add `confirmAction` API function**

Add to `donna-ui/src/api/chat.ts`:

```typescript
export async function confirmAction(
  sessionId: string,
  confirmed: boolean,
): Promise<ChatResponse> {
  const { data } = await client.post(`/chat/sessions/${sessionId}/confirm`, {
    confirmed,
  });
  return data;
}
```

- [ ] **Step 2: Create QuickChatButton**

Create `donna-ui/src/components/QuickChatButton.module.css`:

```css
.fab {
  position: fixed;
  bottom: var(--space-5);
  right: var(--space-5);
  width: 44px;
  height: 44px;
  border-radius: 50%;
  border: none;
  background: var(--color-accent);
  color: var(--color-accent-contrast);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  z-index: var(--z-popover);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.35);
  transition: transform var(--duration-fast) var(--ease-out),
    opacity var(--duration-fast) var(--ease-out);
}

.fab:hover {
  transform: scale(1.06);
}

.fab:active {
  transform: scale(0.95);
}

.fabHidden {
  display: none;
}
```

Create `donna-ui/src/components/QuickChatButton.tsx`:

```typescript
import { MessageSquare } from "lucide-react";
import { useLocation } from "react-router-dom";
import styles from "./QuickChatButton.module.css";
import { cn } from "../lib/cn";

interface Props {
  onClick: () => void;
  visible: boolean;
}

export default function QuickChatButton({ onClick, visible }: Props) {
  const location = useLocation();
  const onChatPage = location.pathname === "/chat";

  if (onChatPage || !visible) return null;

  return (
    <button
      type="button"
      className={cn(styles.fab)}
      onClick={onClick}
      aria-label="Open quick chat"
    >
      <MessageSquare size={20} />
    </button>
  );
}
```

- [ ] **Step 3: Create QuickChatPanel**

Create `donna-ui/src/components/QuickChatPanel.module.css`:

```css
.overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.25);
  z-index: var(--z-dialog);
}

.panel {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: 380px;
  max-width: 90vw;
  background: var(--color-inset);
  border-left: 1px solid var(--color-border);
  box-shadow: var(--shadow-drawer);
  z-index: var(--z-dialog);
  display: flex;
  flex-direction: column;
  animation: slideIn var(--duration-base) var(--ease-out);
}

.header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--color-border);
}

.headerTitle {
  font-family: var(--font-display);
  font-weight: 300;
  font-size: var(--text-body-lg);
  color: var(--color-text);
  flex: 1;
}

.closeBtn {
  background: transparent;
  border: none;
  color: var(--color-text-muted);
  cursor: pointer;
  padding: 4px;
}
.closeBtn:hover { color: var(--color-accent); }

.contextRow {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-4);
  border-bottom: 1px solid var(--color-border-subtle);
}

.contextChip {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  padding: 2px var(--space-2);
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-control);
  font-size: var(--text-label);
  color: var(--color-accent);
  cursor: pointer;
}

.contextDot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--color-accent);
}

.tokenCount {
  font-size: var(--text-label);
  font-family: var(--font-mono);
  color: var(--color-text-muted);
  margin-left: auto;
}

.body {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

@keyframes slideIn {
  from { transform: translateX(100%); }
  to   { transform: translateX(0); }
}

@media (prefers-reduced-motion: reduce) {
  .panel { animation: none; }
}
```

Create `donna-ui/src/components/QuickChatPanel.tsx`:

```typescript
import { useState, useCallback, useEffect } from "react";
import { X } from "lucide-react";
import {
  sendMessage,
  fetchContextStatus,
  confirmAction,
  type ChatMessage,
  type ChatResponse,
  type ContextStatus,
} from "../api/chat";
import { useDashboardContext } from "../context/DashboardContext";
import MessageThread from "../pages/Chat/MessageThread";
import MessageInput from "../pages/Chat/MessageInput";
import styles from "./QuickChatPanel.module.css";

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function QuickChatPanel({ open, onClose }: Props) {
  const { currentPage, selectedItem, setSelectedItem } = useDashboardContext();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [lastResponse, setLastResponse] = useState<ChatResponse | null>(null);
  const [contextStatus, setContextStatus] = useState<ContextStatus | null>(null);
  const [sending, setSending] = useState(false);

  useEffect(() => {
    if (!open) return;
    setSessionId(null);
    setMessages([]);
    setLastResponse(null);
    setContextStatus(null);
  }, [open]);

  const handleSend = useCallback(
    async (text: string) => {
      const sid = sessionId || "new";
      setSending(true);
      try {
        const context = {
          page: currentPage,
          selected_item: selectedItem
            ? { type: selectedItem.type, id: selectedItem.id, label: selectedItem.label }
            : null,
        };
        const resp = await sendMessage(sid, text, "dashboard_quick", context);
        setLastResponse(resp);

        if (resp.session_id && !sessionId) {
          setSessionId(resp.session_id);
        }

        const loadId = resp.session_id || sessionId;
        if (loadId) {
          const { fetchSession } = await import("../api/chat");
          const session = await fetchSession(loadId);
          setMessages(session.messages);
          const ctx = await fetchContextStatus(loadId);
          setContextStatus(ctx);
        }
      } catch {
        // Error toast handled by interceptor
      } finally {
        setSending(false);
      }
    },
    [sessionId, currentPage, selectedItem],
  );

  const handleEscalate = useCallback(async () => {
    if (!sessionId) return;
    try {
      const { escalateSession } = await import("../api/chat");
      const resp = await escalateSession(sessionId);
      setLastResponse(resp);
    } catch {
      // handled by interceptor
    }
  }, [sessionId]);

  const handleActionClick = useCallback(
    (action: string) => { handleSend(action); },
    [handleSend],
  );

  const handleChipClick = useCallback(() => {
    setSelectedItem(null);
  }, [setSelectedItem]);

  const contextLabel = selectedItem
    ? `Viewing: ${selectedItem.label}`
    : `Viewing: ${currentPage.charAt(0).toUpperCase() + currentPage.slice(1)}`;

  const tokenLabel = contextStatus
    ? `${(contextStatus.used_tokens / 1000).toFixed(1)}k / ${(contextStatus.max_tokens / 1000).toFixed(0)}k`
    : null;

  if (!open) return null;

  return (
    <>
      <div className={styles.overlay} onClick={onClose} />
      <div className={styles.panel}>
        <div className={styles.header}>
          <span className={styles.headerTitle}>Quick Chat</span>
          <button type="button" className={styles.closeBtn} onClick={onClose} aria-label="Close">
            <X size={16} />
          </button>
        </div>
        <div className={styles.contextRow}>
          <button type="button" className={styles.contextChip} onClick={handleChipClick}>
            <span className={styles.contextDot} />
            {contextLabel}
          </button>
          {tokenLabel && <span className={styles.tokenCount}>{tokenLabel}</span>}
        </div>
        <div className={styles.body}>
          {messages.length > 0 ? (
            <MessageThread
              messages={messages}
              lastResponse={lastResponse}
              onEscalate={handleEscalate}
              onActionClick={handleActionClick}
            />
          ) : (
            <div style={{
              flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
              color: "var(--color-text-muted)", fontSize: "var(--text-body)", padding: "var(--space-4)",
            }}>
              Ask Donna anything about this page.
            </div>
          )}
          <div style={{ padding: "var(--space-3)" }}>
            <MessageInput onSend={handleSend} disabled={sending} />
          </div>
        </div>
      </div>
    </>
  );
}
```

- [ ] **Step 4: Wire into AppShell**

Update `donna-ui/src/layout/AppShell.tsx`:

```typescript
import { useState, useCallback, useEffect } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { Toaster } from "sonner";
import { Sidebar } from "./Sidebar";
import { DashboardProvider } from "../context/DashboardContext";
import QuickChatButton from "../components/QuickChatButton";
import QuickChatPanel from "../components/QuickChatPanel";
import useKeyboardShortcuts from "../hooks/useKeyboardShortcuts";
import KeyboardShortcutsModal from "../components/KeyboardShortcutsModal";
import styles from "./AppShell.module.css";

export function AppShell() {
  useKeyboardShortcuts();
  const [quickChatOpen, setQuickChatOpen] = useState(false);
  const location = useLocation();

  useEffect(() => {
    if (location.pathname === "/chat") {
      setQuickChatOpen(false);
    }
  }, [location.pathname]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "j") {
        e.preventDefault();
        if (location.pathname !== "/chat") {
          setQuickChatOpen((prev) => !prev);
        }
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [location.pathname]);

  const handleOpenQuickChat = useCallback(() => setQuickChatOpen(true), []);
  const handleCloseQuickChat = useCallback(() => setQuickChatOpen(false), []);

  return (
    <DashboardProvider>
      <div className={styles.shell}>
        <Sidebar />
        <main className={styles.main}>
          <Outlet />
        </main>

        <QuickChatButton onClick={handleOpenQuickChat} visible={!quickChatOpen} />
        <QuickChatPanel open={quickChatOpen} onClose={handleCloseQuickChat} />

        <KeyboardShortcutsModal />

        <Toaster
          position="top-right"
          theme="dark"
          toastOptions={{
            style: {
              background: "var(--color-surface)",
              color: "var(--color-text)",
              border: "1px solid var(--color-border)",
              fontFamily: "var(--font-body)",
              fontSize: "var(--text-body)",
              borderRadius: "var(--radius-control)",
            },
          }}
        />
      </div>
    </DashboardProvider>
  );
}
```

- [ ] **Step 5: Update `sendMessage` to accept context**

In `donna-ui/src/api/chat.ts`, update `sendMessage`:

```typescript
export async function sendMessage(
  sessionId: string,
  text: string,
  channel = "api",
  context?: { page: string; selected_item: { type: string; id: string; label: string } | null },
): Promise<ChatResponse> {
  const { data } = await client.post(`/chat/sessions/${sessionId}/messages`, {
    text,
    channel,
    context,
  });
  return data;
}
```

- [ ] **Step 6: Verify build**

```bash
cd /mnt/donna/donna/donna-ui && npx tsc --noEmit && npx vite build
```

- [ ] **Step 7: Commit**

```bash
git add donna-ui/src/components/QuickChatButton.tsx donna-ui/src/components/QuickChatButton.module.css donna-ui/src/components/QuickChatPanel.tsx donna-ui/src/components/QuickChatPanel.module.css donna-ui/src/layout/AppShell.tsx donna-ui/src/api/chat.ts
git commit -m "feat(ui): add QuickChatPanel with context awareness and Cmd+J shortcut"
```

---

### Task 15: Drawer → CenterDialog Migration (Logs + Skill System)

**Files:**
- Modify: `donna-ui/src/pages/Logs/TraceView.tsx`
- Modify: `donna-ui/src/pages/Logs/index.tsx`
- Modify: `donna-ui/src/pages/SkillSystem/SkillDrawer.tsx`
- Modify: `donna-ui/src/pages/SkillSystem/AutomationDrawer.tsx`
- Modify: `donna-ui/src/pages/SkillSystem/RunDrawer.tsx`
- Modify: `donna-ui/src/pages/Shadow/ComparisonDrawer.tsx`

- [ ] **Step 1: Migrate TraceView to CenterDialog**

In `donna-ui/src/pages/Logs/TraceView.tsx`, change the import from `Drawer` to `CenterDialog`:

```typescript
import { CenterDialog } from "../../primitives/CenterDialog";
```

Replace all `<Drawer` with `<CenterDialog` and `</Drawer>` with `</CenterDialog>`. The props interface is identical (`open`, `onOpenChange`, `title`, `children`), so no other changes are needed.

- [ ] **Step 2: Migrate SkillDrawer to CenterDialog**

In `donna-ui/src/pages/SkillSystem/SkillDrawer.tsx`, same change:

```typescript
import { CenterDialog } from "../../primitives/CenterDialog";
```

Replace `<Drawer` → `<CenterDialog`, `</Drawer>` → `</CenterDialog>`.

- [ ] **Step 3: Migrate AutomationDrawer to CenterDialog**

In `donna-ui/src/pages/SkillSystem/AutomationDrawer.tsx`, same change.

- [ ] **Step 4: Migrate RunDrawer to CenterDialog**

In `donna-ui/src/pages/SkillSystem/RunDrawer.tsx`, same change.

- [ ] **Step 5: Migrate ComparisonDrawer to CenterDialog**

In `donna-ui/src/pages/Shadow/ComparisonDrawer.tsx`, same change.

- [ ] **Step 6: Verify build**

```bash
cd /mnt/donna/donna/donna-ui && npx tsc --noEmit && npx vite build
```

- [ ] **Step 7: Commit**

```bash
git add donna-ui/src/pages/Logs/TraceView.tsx donna-ui/src/pages/SkillSystem/SkillDrawer.tsx donna-ui/src/pages/SkillSystem/AutomationDrawer.tsx donna-ui/src/pages/SkillSystem/RunDrawer.tsx donna-ui/src/pages/Shadow/ComparisonDrawer.tsx
git commit -m "refactor(ui): migrate Logs, SkillSystem, Shadow drawers to CenterDialog"
```

---

### Task 16: Drawer → Inline Expansion (Tasks + Preferences + SkillSystem Candidate)

**Files:**
- Modify: `donna-ui/src/pages/Tasks/TaskDetailDrawer.tsx` → rename to `TaskDetailExpander.tsx`
- Modify: `donna-ui/src/pages/Tasks/index.tsx`
- Modify: `donna-ui/src/pages/Preferences/RuleDetailDrawer.tsx` → rename to `RuleDetailExpander.tsx`
- Modify: `donna-ui/src/pages/Preferences/index.tsx`
- Modify: `donna-ui/src/pages/SkillSystem/CandidateDrawer.tsx` → rename to `CandidateExpander.tsx`
- Modify: `donna-ui/src/pages/SkillSystem/SkillsTab.tsx` (or wherever CandidateDrawer is used)

The inline expansion pattern replaces the Drawer overlay with a collapsible detail section that renders below the selected row. Each page's table/list needs to conditionally render the expander after the selected row.

This is a substantial refactor per page. The exact implementation depends heavily on whether the page uses `DataTable` (TanStack) or a manual list. For DataTable-based pages, you'll use TanStack's `renderSubComponent` pattern. For manual lists, you render the expander inline.

- [ ] **Step 1: Create TaskDetailExpander component**

Rename and refactor `donna-ui/src/pages/Tasks/TaskDetailDrawer.tsx` to `donna-ui/src/pages/Tasks/TaskDetailExpander.tsx`. The key change: remove the `<Drawer>` wrapper entirely, and export the inner content as a plain component that receives `taskId` and renders inline:

```typescript
interface Props {
  taskId: string;
  onClose: () => void;
}

export default function TaskDetailExpander({ taskId, onClose }: Props) {
  // ... same fetch logic as TaskDetailDrawer ...
  // Instead of wrapping in <Drawer>, return the detail content directly:
  return (
    <div className={styles.expanderContainer}>
      {/* Same content that was inside <Drawer> */}
      <button type="button" onClick={onClose} className={styles.collapseBtn}>
        Collapse
      </button>
    </div>
  );
}
```

Add to `TaskDetailDrawer.module.css` (rename to `TaskDetailExpander.module.css`):

```css
.expanderContainer {
  padding: var(--space-4);
  background: var(--color-inset);
  border: 1px solid var(--color-accent-border);
  border-top: none;
  border-radius: 0 0 var(--radius-card) var(--radius-card);
}

.collapseBtn {
  margin-top: var(--space-3);
  padding: var(--space-1) var(--space-3);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-control);
  color: var(--color-text-muted);
  font-size: var(--text-label);
  cursor: pointer;
}
.collapseBtn:hover { color: var(--color-text); border-color: var(--color-accent-border); }
```

- [ ] **Step 2: Update Tasks page to use inline expansion**

In `donna-ui/src/pages/Tasks/index.tsx`, replace the `<TaskDetailDrawer>` at the bottom with inline rendering. After each task row in the table/list, conditionally render `<TaskDetailExpander>` if that row is selected. The exact integration depends on whether Tasks uses `DataTable` or a custom list. Check the file and apply accordingly:

- If using DataTable: set `getRowCanExpand` and `renderSubComponent` on the table
- If using a manual list: render `<TaskDetailExpander>` after the selected `<li>` / `<tr>`

Remove the URL-based drawer open pattern (`navigate(/tasks/${id})`) and replace with local state: `const [expandedId, setExpandedId] = useState<string | null>(null)`.

- [ ] **Step 3: Apply same pattern to Preferences RuleDetailDrawer**

Rename to `RuleDetailExpander.tsx`, remove Drawer wrapper, render inline. Update `Preferences/index.tsx` to use inline expansion.

- [ ] **Step 4: Apply same pattern to SkillSystem CandidateDrawer**

Rename to `CandidateExpander.tsx`, remove Drawer wrapper, render inline. Update the parent component to use inline expansion.

- [ ] **Step 5: Verify build**

```bash
cd /mnt/donna/donna/donna-ui && npx tsc --noEmit && npx vite build
```

- [ ] **Step 6: Commit**

```bash
git add donna-ui/src/pages/Tasks/ donna-ui/src/pages/Preferences/ donna-ui/src/pages/SkillSystem/
git commit -m "refactor(ui): migrate Tasks, Preferences, Candidate drawers to inline expansion"
```

---

### Task 17: Run Full Test Suite + Final Verification

**Files:** None (verification only)

- [ ] **Step 1: Run backend tests**

```bash
cd /mnt/donna/donna && pytest -v
```

Expected: all pass.

- [ ] **Step 2: Run frontend build**

```bash
cd /mnt/donna/donna/donna-ui && npx tsc --noEmit && npx vite build
```

Expected: no errors.

- [ ] **Step 3: Start dev server and smoke test**

```bash
cd /mnt/donna/donna/donna-ui && npx vite dev &
```

Open http://localhost:5173 in browser. Verify:
- Chat page loads, session list populates
- Sending a message works (no silent failures)
- Error messages appear when backend is down
- Quick chat button appears on non-chat pages
- Cmd+J toggles the quick panel
- Context chip shows current page name
- CenterDialog opens for Logs trace view
- Inline expansion works on Tasks page

- [ ] **Step 4: Kill dev server and commit any final fixes**

```bash
kill %1
```

---

## Deferred from This Plan

These items are in the spec but deferred to a follow-up:

- **`ActionResultCard` / `ConfirmActionBanner`** (spec §5g, §2a) — structured rendering of action results (confirmation cards, code blocks, status badges). For now, the LLM summarization prompt generates plain text responses. A follow-up plan should add typed result renderers in the MessageThread component.
- **`resolve_context.md` prompt** (spec §5h) — handled inline in `_extract_action_params` rather than as a standalone prompt file. Can be extracted if the context resolution logic grows more complex.
