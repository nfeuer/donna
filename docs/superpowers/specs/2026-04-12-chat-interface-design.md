# Chat Interface Design Spec

> Brainstormed 2026-04-12. Approach A: Conversation Engine as Orchestrator Extension.

## Problem

Donna has task capture (Discord `#donna-tasks`, SMS) and proactive outreach (nudges, digests, reminders), but no freeform conversational interface. The user can't follow up on agent outputs, ask planning questions, or have multi-turn dialogs with Donna.

## Goals

- Freeform chat with Donna via Discord and a client-agnostic REST API
- Local LLM first (Ollama qwen2.5:32b) — zero marginal cost for conversations
- Explicit Claude escalation with user approval and cost transparency
- Session-based context with optional task pinning for persistent topic focus
- Donna persona on opinionated responses, neutral/direct on factual queries, configurable
- Hot-reloadable config — all behavioral tuning editable in the admin dashboard

## Non-Goals

- Streaming responses (v1 is request/response; streaming is a future upgrade — see Future Considerations)
- Voice/audio chat
- Multi-user conversations (single-user system)
- Replacing existing `#donna-tasks` capture flow — chat is a separate channel

## Architecture

### Conversation Engine

`src/donna/chat/engine.py` — single entry point for all chat interactions.

**Responsibilities:**
- Receives message + session ID + user ID
- Manages session lifecycle (create, load, expire, pin/unpin)
- Classifies intent via local LLM
- Assembles context for LLM prompt (session history, pinned task data, recent activity)
- Calls local LLM through existing `ModelRouter`
- Detects escalation need from structured LLM output
- Returns response with optional structured data

**Does NOT:**
- Know about Discord or HTTP — frontends are adapters
- Execute task mutations directly — delegates to `Database` and `AgentDispatcher`
- Manage its own LLM connection — uses `ModelRouter`

**Primary method:**

```python
async def handle_message(
    self, session_id: str, user_id: str, text: str
) -> ChatResponse
```

**ChatResponse dataclass:**

| Field | Type | Description |
|-------|------|-------------|
| text | str | The reply to the user |
| needs_escalation | bool | Whether Claude is needed |
| escalation_reason | str? | Why escalation is needed |
| estimated_cost | float? | Estimated Claude cost in USD |
| suggested_actions | list[str] | Actions Donna can take (e.g., "schedule_task", "run_prep_agent") |
| session_pinned_task_id | str? | Currently pinned task |
| pin_suggestion | dict? | Task ID + title if engine detects topic focus |

### Intent Classification

Local LLM call (`classify_chat_intent` task type). Output is one of:

| Intent | Description |
|--------|-------------|
| `task_query` | Questions about tasks — status, list, details |
| `task_action` | Requests to modify tasks — reschedule, reprioritize, create |
| `agent_output_query` | Asking about what agents did — prep results, research output |
| `planning` | Planning advice — "what should I focus on?", "am I overcommitted?" |
| `freeform` | General conversation, not tied to a specific system action |
| `escalation_request` | User explicitly asks for Claude's help |

Intent determines what context gets loaded into the prompt.

### Task Actions from Chat

When the intent is `task_action`, the local LLM's structured output includes an `action` field describing what to do (e.g., `{"action": "reschedule", "task_id": "...", "new_time": "..."}`). The engine validates the action, executes it via the existing `Database` methods (same code paths as the Discord field-update commands and API endpoints), and confirms the result in the chat response. The chat engine does NOT re-parse the message through `InputParser` — intent classification already determined this is an action, not a new task. New task creation from chat uses `task_action` with `{"action": "create", ...}` and delegates to `Database.create_task`.

## Session Management

### Database Schema

**`conversation_sessions` table** (tasks database):

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Session identifier |
| user_id | String | Who owns this session |
| channel | Enum | discord, api, sms |
| pinned_task_id | UUID? | FK → tasks.id, null if unpinned |
| status | Enum | active, expired, closed |
| summary | Text? | LLM-generated summary on close/expire |
| created_at | DateTime | Session start |
| last_activity | DateTime | Last message in either direction |
| expires_at | DateTime | Sliding: 2h from last activity |
| message_count | Int | Running count for context budget tracking |

**`conversation_messages` table** (tasks database):

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Message identifier |
| session_id | UUID | FK → conversation_sessions.id |
| role | Enum | user, assistant |
| content | Text | Message text |
| intent | String? | Classified intent (assistant messages only) |
| tokens_used | Int? | Token count for this message's LLM call |
| created_at | DateTime | When sent |

**Why not reuse `conversation_context`?** That table is designed for structured agent interrogation (questions_asked/responses_received as JSON). Chat sessions are open-ended multi-turn conversations — separate tables are cleaner.

### Session Lifecycle

- **Create:** First message with no active session → auto-created
- **Expire:** 2-hour sliding TTL from last activity. On expiry, local LLM generates a brief summary stored on the session row.
- **Close:** User says "done" or closes via command/API. Same summary behavior.
- **Pin:** User says "let's talk about [task]" or engine detects topic focus. Loads task context (title, description, notes, agent outputs, related tasks) into prompt.
- **Unpin:** User shifts topic or explicitly unpins. Conversation-relevant learnings summarized back to task notes.

### Context Budget

The local LLM's ~32k context window is the constraint. Engine tracks cumulative tokens per session. When approaching the limit (~24k, leaving room for system prompt + task context), it summarizes the oldest messages and replaces them with the summary. Transparent to the user.

## Context Assembly & Prompt Strategy

Context layers assembled in order:

1. **System prompt** — Donna persona (or neutral, per config). Current date/time, user name. Loaded from `prompts/chat/chat_system.md`.
2. **Session history** — Recent messages, or summarized older ones if context budget is tight.
3. **Pinned task context** (if pinned) — Task fields, notes, description, agent outputs from database.
4. **Intent-specific context:**

| Intent | Additional Context Loaded |
|--------|--------------------------|
| `task_query` | User's active tasks (titles, status, priority, domain) |
| `task_action` | Same as task_query + target task's full details |
| `agent_output_query` | Relevant `invocation_log` entries and agent results |
| `planning` | Today's schedule, upcoming deadlines, open task count by domain |
| `freeform` | Minimal — session history and pinned context only |
| `escalation_request` | Full context assembled, held for Claude |

**Prompt templates:** Externalized in `prompts/chat/` — one per intent type plus shared system prompt. Jinja2 templates consistent with existing prompt patterns.

**Structured LLM output:**

```json
{
  "response_text": "...",
  "needs_escalation": false,
  "escalation_reason": null,
  "suggested_actions": [],
  "pin_suggestion": null
}
```

`suggested_actions` surface as Discord buttons or API response fields. `pin_suggestion` is a task ID if the LLM detects topic focus on an unpinned session.

## Claude Escalation

### How the Local LLM Decides

Prompt-driven, not code-driven. The `chat_system.md` template instructs: "If you cannot confidently answer, if the question requires complex multi-step reasoning, long-horizon planning, or nuanced judgment, set `needs_escalation` to true and explain why."

### Escalation Flow

1. Local LLM returns `needs_escalation: true` with reason
2. Engine estimates Claude cost — rough token count × model pricing from `donna_models.yaml`
3. Engine responds: *"I'd need to use Claude for this — [reason]. Estimated cost: ~$0.03. Go ahead?"*
4. User confirms → Full context sent to Claude via `ModelRouter` (`chat_escalation` task type)
5. User declines → Local LLM generates best-effort response with disclaimer
6. Claude response added to session history like any other message

### Guardrails

- `chat_escalation` routes to `parser` (Claude) with no fallback — explicit choice, not automatic failover
- Cost tracked in `invocation_log` like every other LLM call
- Daily chat escalation budget in `config/chat.yaml` — once hit, escalation blocked for the day with explanation
- Config option for auto-approve under a cost threshold (default: $0.00 = always ask)

### What Doesn't Escalate

- Task queries — database lookups, LLM just formats
- Agent output retrieval — data already exists
- Simple freeform chat — persona doesn't need Claude

Escalation targets: complex planning advice, multi-factor prioritization, nuanced project decomposition.

## Frontend Adapters

### Discord Adapter

New channel `#donna-chat` in the existing Donna category.

**Mapping:**
- Discord user ID → `user_id`
- Channel → one active session per user (new message resumes or creates)
- Discord threads → pinned task conversations. Pinning creates a thread named after the task, allowing multiple pinned conversations in parallel.
- Escalation confirmation → Discord buttons (Approve / Decline) via `discord.ui.View`
- `suggested_actions` → Discord buttons on response messages

**No changes to existing behavior.** `#donna-tasks` continues to work for quick task capture.

### FastAPI Adapter

New routes under `/chat`:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/chat/sessions` | POST | Create new session (or resume active) |
| `/chat/sessions/{id}` | GET | Session details + recent messages |
| `/chat/sessions/{id}/messages` | POST | Send message, receive response |
| `/chat/sessions/{id}/messages` | GET | Message history (paginated) |
| `/chat/sessions/{id}/pin` | POST | Pin session to a task |
| `/chat/sessions/{id}/pin` | DELETE | Unpin session |
| `/chat/sessions/{id}/escalate` | POST | Approve pending escalation |
| `/chat/sessions/{id}` | DELETE | Close session |

All endpoints auth'd via Firebase JWT. Response format for POST messages:

```json
{
  "message_id": "...",
  "text": "...",
  "needs_escalation": false,
  "escalation_reason": null,
  "estimated_cost": null,
  "suggested_actions": ["schedule_task", "run_prep_agent"],
  "pin_suggestion": { "task_id": "...", "task_title": "..." }
}
```

## Configuration

### `config/chat.yaml`

```yaml
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
    templates_dir: prompts/chat/

  discord:
    chat_channel_id: ${DONNA_DISCORD_CHAT_CHANNEL_ID}
```

### New routing entries in `donna_models.yaml`

```yaml
routing:
  classify_chat_intent:
    model: local_parser
  chat_respond:
    model: local_parser
  chat_summarize:
    model: local_parser
  chat_escalation:
    model: parser
```

### Hot Reload

Config is read at request time with a short TTL cache (~5 seconds). When edited via the admin dashboard and saved, the next message through the engine picks up new values. Covers: persona mode, escalation thresholds, session TTL, auto-approve threshold, daily budget.

The admin dashboard config editor (existing `admin_config.py` pattern) supports `chat.yaml` alongside other config files.

## Future Considerations

- **Streaming responses:** v1 is request/response. If latency becomes an issue with longer contexts, add SSE or WebSocket streaming to the FastAPI adapter and a chunked message pattern for Discord. The `ConversationEngine.handle_message` return type would become an async generator.
- **Web client:** The FastAPI `/chat` endpoints are client-agnostic. A donna subdomain web interface can consume the same API the mobile app uses.
- **Multi-session support in Discord:** v1 is one active session per user in `#donna-chat` plus thread-based pinned conversations. Could expand to named sessions if needed.
