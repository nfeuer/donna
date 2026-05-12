# Universal Reply Handler

**Date:** 2026-05-12
**Status:** Draft
**Spec references:** spec_v3.md §4.3 (Tool Validation Layer), §5.2 (Notification Channels), §6.1 (Agent Orchestration)

## Problem

Donna currently handles user replies with hardcoded keyword matching per context (overdue threads use one set of keywords, digest threads another). This is brittle — "I finished it" doesn't match `{"done", "finished"}` because it's a phrase, not an exact keyword. Multi-intent replies like "I finished half of it, reschedule the rest for tomorrow" are impossible to handle. There's no way for the user to give Donna complex instructions via a reply.

## Design Goals

1. Simple replies ("done", "reschedule") resolve in <1ms with zero LLM cost.
2. Complex/ambiguous replies route to the local LLM (qwen2.5:32b on RTX 3090) for intent classification and action extraction.
3. The LLM proposes actions; Donna presents a plan and waits for user confirmation before executing.
4. All responses are written in Donna's persona (sharp, confident, efficient, never sycophantic).
5. Available actions are config-driven, not hardcoded.
6. Capability gaps (requests Donna can't handle) are tracked and auto-promoted to skill candidates.
7. The handler is channel-agnostic — Discord threads, SMS, future Flutter chat all use the same entry point.

## Architecture

### Confidence-Gated Pipeline

```
User reply arrives
       |
       v
+------------------+
|  ReplyHandler    |
|  .handle(msg)    |
+--------+---------+
         |
    +----v-----+
    | Layer 1   |  Keyword match + complexity gate
    | Fast Path |  (<1ms)
    +----+-----+
         |
    match found      no match OR
    AND simple?      complex reply
         |                |
    +----v-----+    +----v------+
    | Execute   |    | Layer 2   |  Local LLM (qwen2.5:32b)
    | + Report  |    | LLM Path  |  (~2-5s)
    +-----------+    +----+-----+
                          |
                     +----v------+
                     | Plan +    |  Present actions, wait
                     | Confirm   |  for user OK
                     +-----------+
```

### Layer 1: Fast Path

Keyword-based intent matching with a complexity gate that prevents misclassification of multi-intent replies.

**Intent definitions** live in `config/reply_intents.yaml`:

```yaml
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

**Complexity gate** — the fast path only fires when ALL conditions hold:

1. Reply is under 60 characters (configurable via `fast_path.max_length`)
2. Reply contains no multi-intent signals: "but", "and also", "however", comma-separated clauses
3. Exactly one intent keyword set matches (no conflicting intents)

If any gate condition fails, the reply routes to Layer 2 regardless of keyword matches.

**Pending plan intercept** — before keyword matching, the fast path checks for a pending action plan on this thread. If one exists:
- Confirmation keywords ("yes", "go ahead", "do it", "ok", "sounds good") execute the pending plan.
- Rejection keywords ("no", "cancel", "nevermind") reject it.
- Anything else cancels the pending plan and processes the reply through the full pipeline.

### Layer 2: LLM Path

When the fast path defers, the reply goes to the local LLM via `ModelRouter.complete()`.

**Prompt construction:**

1. **System prompt** — Donna's persona + instructions to propose actions as structured JSON. Emphasizes: propose only, never claim to have executed. End every reply with a confirmation prompt.
2. **Available actions** — Rendered from the action registry (name, description, parameter schema). Formatted similarly to function-calling tool descriptions.
3. **Conversation memory** — Last 10 messages from the thread, ordered chronologically.
4. **Task context** — Current task state: title, status, priority, domain, scheduled_start, estimated_duration, nudge_count, reschedule_count.
5. **User reply** — The new message to interpret.

**Output schema** (enforced via `ModelRouter.complete()` JSON schema):

```json
{
  "reasoning": "string — internal chain-of-thought, logged but not shown to user",
  "actions": [
    {
      "action": "string — action name from registry",
      "params": { "key": "value" }
    }
  ],
  "reply_to_user": "string — Donna's response in persona, includes plan summary + confirmation prompt"
}
```

**Task type:** `reply_intent` (registered in `config/models.yaml` routing config, routed to local LLM).

**Validation:** The orchestrator validates each proposed action against the action registry before presenting the plan. Invalid actions (unknown name, wrong param types, missing required params) are stripped and logged. If all actions are invalid, Donna asks the user to clarify.

### Action Registry

Actions are defined in `config/reply_actions.yaml` and loaded at startup. Each action maps to a Python handler function and declares its parameters, risk level, and context requirements.

```yaml
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
      domain: { type: string, enum: [work, personal, health, finance] }
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

**`from_context: true`** means the parameter is injected from the thread context (e.g., `task_id` from the thread's linked task), not extracted from the user's message.

**Risk levels** are informational for now. All LLM-proposed actions require confirmation. Risk levels are available for future use if the confirmation model changes to hybrid (auto-execute low-risk, confirm medium+).

### Conversation Memory

Each active thread maintains a rolling message buffer in SQLite.

**Schema:**

```sql
CREATE TABLE thread_memory (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    context_type TEXT NOT NULL,   -- 'overdue', 'digest', 'chat', 'nudge'
    task_id TEXT,
    role TEXT NOT NULL,           -- 'user' or 'donna'
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES task(id)
);
CREATE INDEX idx_thread_memory_thread ON thread_memory(thread_id, created_at);
```

**Recording:** When Donna sends a message in a thread (nudge, plan confirmation, execution report), it's recorded as `role='donna'`. When the user replies, it's recorded as `role='user'`.

**Retrieval:** The LLM prompt includes the last 10 messages ordered by `created_at ASC`. The window size is configurable via `config/reply_actions.yaml` under `memory.window_size`.

**Retention:** Messages older than 7 days are pruned by the nightly cleanup job. Threads with active escalations or pending action plans are exempt until resolution.

### Plan-and-Confirm Flow

When the LLM returns an action plan:

1. **Donna posts the plan** in the thread using the `reply_to_user` field. This is written in Donna's persona and ends with a confirmation prompt.

2. **The plan is persisted:**

```sql
CREATE TABLE pending_action_plan (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    actions_json TEXT NOT NULL,
    reply_text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending, confirmed, rejected, expired
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
```

3. **User confirms or rejects** via their next reply (handled by the pending plan intercept in the fast path).

4. **Expiry:** Pending plans expire after 1 hour (configurable). Expired plans are marked `expired` — Donna does not nag about them.

**Execution:** When confirmed, each action is executed sequentially through its registered handler. Results are collected (success/failure per action). Donna posts a summary: what succeeded, what failed, and any follow-up needed.

### Capability Gap Detection

When the LLM encounters a request it can't map to any available action, it returns a `request_capability` action.

**Schema:**

```sql
CREATE TABLE capability_gap (
    id TEXT PRIMARY KEY,
    user_request TEXT NOT NULL,
    description TEXT NOT NULL,
    context_type TEXT,
    task_id TEXT,
    hit_count INTEGER DEFAULT 1,
    status TEXT DEFAULT 'logged',  -- logged, candidate_created, dismissed
    created_at TEXT NOT NULL,
    last_hit_at TEXT NOT NULL
);
```

**Deduplication:** Before inserting a new gap, normalize the description to lowercase and check for existing gaps where the normalized description matches exactly or the Jaccard similarity of word sets exceeds 0.6. If a match is found, increment `hit_count` and update `last_hit_at` instead of creating a duplicate. Start with this simple heuristic; upgrade to embedding similarity later if needed.

**Frequency-based promotion:** A nightly job scans for `hit_count >= 3` and `status = 'logged'`. For each, it creates a `skill_candidate_report` record (feeding into the existing skill auto-draft pipeline) and updates status to `candidate_created`.

**User visibility:** Donna tells the user "I can't do that yet, but I've noted it." The EOD digest includes a line for new capability gaps with 3+ hits.

## Integration Points

| Existing Component | Change |
|---|---|
| `overdue.py` `handle_reply()` | Replace keyword matching with `ReplyHandler.handle()`. Fast path covers existing keywords. |
| `discord_bot.py` `on_message` | Thread replies route through `ReplyHandler` instead of directly to overdue handler. Handler identifies context from thread metadata. |
| `DiscordIntentDispatcher` | Unchanged. Dispatcher handles **new inbound messages** (DMs, channel mentions). `ReplyHandler` handles **replies within existing threads**. |
| `ToolRegistry` | Sibling concept. `ToolRegistry` manages agent tool calls; action registry manages reply-handler actions. They can share handler implementations. |
| `ModelRouter` | LLM calls use `ModelRouter.complete()` with task_type `reply_intent`. |
| `NotificationService` | Donna's thread replies go through the service for logging and blackout checks. |
| SMS replies (future) | Twilio webhook routes through `ReplyHandler` with `context_type='sms'`. |

## Module Structure

```
src/donna/replies/
    __init__.py
    handler.py          # ReplyHandler entry point, fast path, complexity gate
    llm_classifier.py   # LLM prompt construction, output parsing
    action_registry.py  # Load actions from config, validate proposed actions
    actions/
        __init__.py
        task_actions.py  # mark_done, reschedule, create_task, rename_task, snooze
        gap_actions.py   # log_capability_gap
    memory.py           # Thread conversation memory (read/write/prune)
    pending_plans.py    # Plan persistence, confirmation, expiry

config/
    reply_intents.yaml  # Fast path keyword definitions
    reply_actions.yaml  # Action registry definitions
```

## Testing Strategy

- **Unit tests** for fast path: keyword matching, complexity gate edge cases, pending plan intercept
- **Unit tests** for action validation: bad params, unknown actions, missing required fields
- **Unit tests** for conversation memory: window size, pruning, retention exemptions
- **Integration tests** with real local LLM calls for the classifier (tagged `@pytest.mark.llm`, skippable in CI)
- **Mock-based tests** for plan-and-confirm flow: confirm, reject, expire, new instructions override
- **Capability gap tests**: dedup logic, hit count promotion, skill candidate creation

## Migration

Alembic migration adds three tables: `thread_memory`, `pending_action_plan`, `capability_gap`.

## Open Questions

None — all design decisions resolved during brainstorming.
