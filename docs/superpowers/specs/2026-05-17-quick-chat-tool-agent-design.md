# Quick Chat Tool Agent — Design Spec

**Date:** 2026-05-17
**Goal:** Transform Quick Chat from a rigid classify-dispatch-respond pipeline into a flexible tool-use agent that can read any system data, answer grounded questions, and execute writes only with user confirmation. Fix the new-session reattachment bug. Add per-turn structured logging with Inspector integration.

**Primary design priorities:** accuracy (#1), flexibility (#2), latency is acceptable up to 5 minutes.

**References:** spec_v3.md SS4.3 (structured logging), SS5.2 (cost tracking), SS6.1 (dashboard), SS9.1 (preferences)

---

## 1. Architecture Overview

### Current Flow (Being Replaced)

```
User message
  -> classify_intent (LLM call #1)
  -> try_action_pipeline (maybe LLM call #2 for param extraction)
  -> build_prompt + respond (LLM call #3)
  -> single response
```

Problems: rigid action matching, no data awareness, no multi-step reasoning.

### New Flow: Tool-Use Agent Loop

```
User message
  -> build system prompt (persona + page hint + tool schemas + history)
  -> LOOP:
     -> LLM call (sees tools, history, prior tool results)
     -> LLM returns text response? -> DONE, return to user
     -> LLM returns tool_call?
        -> validate against schema
        -> read tool? -> auto-execute, append result, continue loop
        -> write tool? -> return confirmation prompt, pause loop
        -> malformed? -> feed error back, retry (once)
     -> hit 10 tool calls or 5 min wall-clock? -> summarize and return
  -> store assistant message with trace_id + invocation_ids
```

### Flow Diagram

```
                    +------------------+
                    |  User sends msg  |
                    +--------+---------+
                             |
                    +--------v---------+
                    | Build system     |
                    | prompt + tools   |
                    +--------+---------+
                             |
                    +--------v---------+
               +--->|  Call local LLM  |<-----------+
               |    +--------+---------+            |
               |             |                      |
               |    +--------v---------+            |
               |    | Parse response   |            |
               |    +--+-----+-----+---+            |
               |       |     |     |                |
               |  text |  tool|  malformed          |
               |  resp | call|  output              |
               |       |     |     |                |
               |       |  +--v--+  |                |
               |       |  |Valid|  +---> retry once--+
               |       |  |ate |        (or terminate)
               |       |  +--+-+
               |       |     |
               |       |  +--v--------+
               |       |  | Read tool?|
               |       |  +--+----+---+
               |       |     |    |
               |       |   yes    no (write)
               |       |     |    |
               |       |  +--v-+  +--v-----------+
               |       |  |Exec|  |Confirm prompt|
               |       |  +--+-+  |pause loop    |
               |       |     |    +--------------+
               |       |     |
               |       |  +--v-----------+
               |       |  |Append result |
               |       |  |to context    |
               |       |  +--+-----------+
               |       |     |
               |       |  +--v-----------+
               |       |  |Under limits? |
               |       |  +--+------+----+
               |       |     |      |
               |       |    yes     no
               |       |     |      |
               |       |     +------+---> summarize + return
               |       |     |
               |       +-----+
               |       |
               |  +----v---------+
               |  | Return text  |
               |  | to user      |
               |  +--------------+
               |
               +--- (loop back for next tool result)
```

---

## 2. Tool-Use Protocol

### LLM Output Format

The system prompt instructs the LLM to respond with JSON in one of two forms:

**Text response (terminal):**
```json
{
  "type": "text",
  "response_text": "Here are the 3 errors from today...",
  "needs_escalation": false,
  "escalation_reason": null
}
```

**Tool call (non-terminal):**
```json
{
  "type": "tool_call",
  "tool": "query_invocations",
  "params": {
    "date_from": "2026-05-17",
    "has_error": true,
    "limit": 10
  }
}
```

### Tool Result Injection

After a tool executes, the result is appended to the conversation context as a system message:

```
[Tool Result: query_invocations]
{results: [...], total_count: 3, truncated: false}
```

The LLM sees this on its next turn and can respond with text or call another tool.

### Validation

Each tool call is validated against the tool's JSON schema before execution:
- Unknown tool name -> error message back to LLM, retry allowed
- Missing required params -> error message listing what's missing
- Wrong param type -> error message with expected type
- Malformed JSON -> error message with parse error, retry allowed (once per turn)

After 2 consecutive parse failures, the loop terminates with a fallback response.

---

## 3. Read Tool Inventory

Every read tool returns `{ results: [...], total_count: int, truncated: bool }`. Default limit: 25. Max limit: 100.

### Logs & Invocations

**`query_invocations`** — Search `invocation_log`.

| Param | Type | Description |
|-------|------|-------------|
| `date_from` | string (ISO date) | Start date filter |
| `date_to` | string (ISO date) | End date filter |
| `task_type` | string | Exact match on task_type |
| `model` | string | Model alias or actual model ID |
| `min_cost` | float | Minimum cost_usd |
| `min_latency` | int | Minimum latency_ms |
| `has_error` | bool | Filter to errored invocations |
| `sort` | enum: cost, latency, timestamp, tokens_in | Sort field (default: timestamp) |
| `sort_dir` | enum: asc, desc | Sort direction (default: desc) |
| `limit` | int | Max results (default 25, max 100) |

Returns per row: id, task_type, model_alias, model_actual, tokens_in, tokens_out, cost_usd, latency_ms, quality_score, timestamp, has_error.

**`get_invocation_detail`** — Full record for a single invocation.

| Param | Type | Required |
|-------|------|----------|
| `invocation_id` | string | yes |

Returns: all columns from invocation_log plus `payload_path` (if payload writer is active).

**`query_invocation_stats`** — Aggregation queries.

| Param | Type | Description |
|-------|------|-------------|
| `group_by` | enum: task_type, model, date | Grouping dimension |
| `date_from` | string | Start date |
| `date_to` | string | End date |

Returns per group: group_key, count, total_cost, avg_cost, avg_latency, avg_quality, total_tokens_in, total_tokens_out.

### Tasks

**`query_tasks`** — Enhanced task search.

| Param | Type | Description |
|-------|------|-------------|
| `status` | string | Filter by status |
| `priority` | int | Filter by priority (1-5) |
| `domain` | string | Filter by domain |
| `title_search` | string | Substring match on title |
| `created_after` | string | ISO date |
| `updated_after` | string | ISO date |
| `sort` | enum: priority, created_at, updated_at, deadline | Sort field |
| `limit` | int | Default 25, max 100 |

Returns per row: id, title, status, priority, domain, created_at, updated_at, deadline.

**`get_task_detail`** — Full task record.

| Param | Type | Required |
|-------|------|----------|
| `task_id` | string | yes |

Returns: all task fields including description, notes, scheduling info, history.

### Automations

**`query_automations`** — List automations.

| Param | Type | Description |
|-------|------|-------------|
| `active_only` | bool | Filter to active automations (default true) |
| `skill_name` | string | Filter by associated skill |
| `limit` | int | Default 25 |

Returns per row: id, name, active, cadence, skill_name, last_run_at, next_run_at, run_count.

**`get_automation_detail`** — Full automation config.

| Param | Type | Required |
|-------|------|----------|
| `automation_id` | string | yes |

Returns: full config including steps, trigger, cadence, GPU model, preferred_window, recent run history (last 5).

### Skills

**`query_skills`** — List skills.

| Param | Type | Description |
|-------|------|-------------|
| `status` | string | Filter: active, candidate, shadow, draft |
| `limit` | int | Default 25 |

Returns per row: name, status, description, run_count, last_run_at, avg_quality.

**`get_skill_detail`** — Full skill record.

| Param | Type | Required |
|-------|------|----------|
| `skill_name` | string | yes |

Returns: full config, recent runs with outcomes, quality scores.

**`query_skill_candidates`** — Candidate skills with scores.

| Param | Type | Description |
|-------|------|-------------|
| `status` | string | Filter: pending, approved, rejected |
| `limit` | int | Default 25 |

Returns per row: name, status, confidence, recommendation, source, created_at.

### Vault

**`list_vault_files`** — Already exists. Keep as-is.

**`read_vault_file`** — Already exists. Subject to 4k token truncation.

### System

**`get_system_health`** — System status overview. No parameters.

Returns: queue_depth, worker_status, error_count_1h, uptime_seconds, db_size_mb, active_session_count.

**`query_preferences`** — Learned preference rules.

| Param | Type | Description |
|-------|------|-------------|
| `rule_type` | string | Filter by type (scheduling, priority, etc.) |
| `enabled_only` | bool | Default true |
| `limit` | int | Default 25 |

Returns per row: id, rule_type, rule_text, confidence, enabled, correction_count, created_at.

---

## 4. Write Tools

Existing write tools remain unchanged. All require user confirmation via the `safety: confirm` mechanism:

- `create_task` — creates a new task
- `update_task` — updates status, priority, notes
- `reschedule_task` — changes scheduled date
- `create_vault_note` — creates a vault file
- `create_automation` — creates a new automation (confirm)
- `execute_skill` — runs a skill (confirm)
- `create_skill_draft` — creates a candidate skill draft (confirm)

When the LLM calls a write tool, the loop pauses and returns a confirmation prompt to the user. On confirmation, the engine resumes and executes.

---

## 5. Result Size Management

### Layer 1: Tool-Level Caps

Every read tool accepts a `limit` parameter (default 25, max 100). Results always include `total_count` so the LLM knows the full cardinality.

### Layer 2: LLM-Driven Refinement

When `total_count` greatly exceeds the returned results, the LLM can refine its query with tighter filters, narrower date ranges, or ask the user to clarify. The system prompt includes guidance:

> "When a query returns many more results than shown, refine your filters before summarizing. Do not guess about records you haven't seen."

### Layer 3: Aggregation Tools

Summary questions ("how much did I spend this week?", "which task type has the most errors?") should use aggregation tools (`query_invocation_stats`, `get_system_health`) rather than paging through individual records.

### Layer 4: Hard Truncation

If a serialized tool result exceeds 4,000 tokens (measured by rough char/4 estimate), the engine truncates and appends:

```
[Truncated: showing first {n} of {total} rows. {total_count} total matching records. Refine your query or request specific IDs.]
```

### Truncation Flow

```
Tool executes
  -> serialize result to JSON
  -> estimate tokens (len / 4)
  -> over 4k tokens?
     -> yes: truncate rows from the end until under 3.5k
             set truncated=true
             append truncation notice
     -> no: return as-is
```

---

## 6. Logging & Observability

### Trace Model

Every `handle_message` call generates a `trace_id` (UUID v7). All log events within the loop include this trace_id for end-to-end correlation.

### Per-Turn Log Event

```json
{
  "event_type": "chat.tool_loop_turn",
  "trace_id": "019e3a00-...",
  "session_id": "019e38db-...",
  "turn": 2,
  "action": "tool_call",
  "tool": "query_invocations",
  "params": {"date_from": "2026-05-17", "has_error": true},
  "result_count": 3,
  "result_total": 3,
  "result_truncated": false,
  "prompt_preview": "You are Donna, an AI personal assistant...[first 500 chars of full assembled prompt]",
  "invocation_id": "inv-789",
  "tokens_in": 1800,
  "tokens_out": 45,
  "latency_ms": 2340,
  "cost_usd": 0.00003
}
```

For text responses (final turn):
```json
{
  "event_type": "chat.tool_loop_turn",
  "trace_id": "019e3a00-...",
  "turn": 3,
  "action": "text_response",
  "response_length": 245,
  "prompt_preview": "You are Donna...[first 500 chars of full assembled prompt]",
  "invocation_id": "inv-790",
  "tokens_in": 2400,
  "tokens_out": 52,
  "latency_ms": 3100
}
```

### Error Log Event

```json
{
  "event_type": "chat.tool_loop_error",
  "trace_id": "019e3a00-...",
  "turn": 2,
  "error_type": "malformed_tool_call",
  "raw_output": "{\"tool\": \"query_logs\", params: ...}",
  "error_detail": "JSON parse error at position 28",
  "action_taken": "retry"
}
```

### Loop Summary Event

Emitted once when the loop completes:

```json
{
  "event_type": "chat.tool_loop_complete",
  "trace_id": "019e3a00-...",
  "session_id": "019e38db-...",
  "user_id": "nick",
  "total_turns": 3,
  "tools_called": ["query_invocations"],
  "unique_tools": 1,
  "total_latency_ms": 8200,
  "total_tokens_in": 6000,
  "total_tokens_out": 143,
  "total_cost_usd": 0.00009,
  "termination_reason": "text_response",
  "escalated": false,
  "page_context": "logs"
}
```

`termination_reason` is one of: `text_response`, `max_tools_reached`, `timeout`, `consecutive_errors`, `escalation_paused`, `write_confirmation`.

### Full Prompt Capture

The `prompt_preview` field in structured logs shows the first 500 characters. Full prompts are captured via the Claude Inspector's payload writer (spec: `2026-05-16-claude-inspector-design.md`). Each LLM call within the loop gets its own `invocation_id`, and the payload writer stores the complete request/response JSON at `data/payloads/{date}/{invocation_id}.json`.

### Logging Flow

```
handle_message called
  |
  +-> generate trace_id (UUID v7)
  |
  +-> LOOP START
  |     |
  |     +-> build prompt
  |     +-> call LLM via router (gets invocation_id from inv logger)
  |     +-> log chat.tool_loop_turn (trace_id, turn#, prompt_preview, invocation_id)
  |     +-> payload writer stores full prompt (keyed by invocation_id)
  |     |
  |     +-> parse response
  |     +-> tool call? execute, log result_count
  |     +-> error? log chat.tool_loop_error with raw_output
  |     +-> continue loop
  |
  +-> LOOP END
  |
  +-> log chat.tool_loop_complete (summary)
  +-> store trace_id on conversation_messages row
```

---

## 7. Inspector Integration

### Message-Level Debug Link

Each assistant message in the chat UI gets a subtle debug icon (visible on hover). Clicking it opens:

```
/claude-inspector?trace_id={trace_id}
```

The Claude Inspector page (spec: `2026-05-16-claude-inspector-design.md`) filters its call browser to show all invocations matching that trace_id, ordered by turn number. The user can expand any invocation to see the full prompt and response.

### Schema Change

Add to `conversation_messages` table via Alembic migration:
- `trace_id` (VARCHAR(36), nullable) — links to the tool loop trace
- `invocation_ids` (TEXT, nullable) — JSON array of invocation IDs from the loop

### Grafana Link Template

Configure a Grafana data link on the `invocation_id` field in Loki log panels:

```
/claude-inspector?invocation_id=${__value.raw}
```

This makes any invocation_id in the Grafana log view clickable, opening the Inspector with that specific call expanded.

### Interface Contract with Claude Inspector

The Inspector page (being built separately) must support these query params:
- `?trace_id=xxx` — filter call browser to all invocations with this trace_id, expand the first one
- `?invocation_id=xxx` — filter to a single invocation and auto-expand its detail panel

The `invocation_log` table needs a `trace_id` column (VARCHAR(36), nullable, indexed) added in the same migration. The tool loop engine populates this on every `router.complete()` call within a loop.

---

## 8. New Session Bug Fix

### Problem

When the UI sends `session_id = "new"`, the route converts it to `None`. The engine then calls `get_active_chat_session(user_id, channel)` which finds the existing active session and reattaches to it.

### Fix

Add a `force_new: bool` parameter to `handle_message`:

```python
async def handle_message(
    self,
    session_id: str | None,
    user_id: str,
    text: str,
    channel: str,
    dashboard_context: dict[str, Any] | None = None,
    force_new: bool = False,
) -> ChatResponse:
```

When `force_new=True`, skip the `get_active_chat_session` lookup and create a new session directly.

### API Change

`POST /chat/sessions/{session_id}/messages` — when `session_id == "new"`, pass `force_new=True` to the engine.

### Existing Behavior Preserved

When `session_id` is `None` and `force_new` is `False` (e.g., Discord messages), the active session resume logic still works as before.

---

## 9. Page Context Injection

When `dashboard_context` is provided, the engine injects a lightweight hint into the system prompt:

```
## Current Dashboard Context
User is viewing the Logs page.
```

Or with a selected item:

```
## Current Dashboard Context
User is viewing the Tasks page and has selected task "Fix login bug" (id: task-123).
```

This hint guides the LLM toward relevant tools but does not limit it. The LLM can still query any tool regardless of page context.

---

## 10. System Prompt Structure

The system prompt for the tool loop includes:

1. **Persona** — Donna personality and communication rules (existing `chat_system.md`)
2. **Page context** — lightweight hint from dashboard_context
3. **Available tools** — full schema for each tool, formatted as a tools block
4. **Tool-use instructions** — how to format tool calls, when to refine vs. respond, accuracy rules
5. **Conversation history** — prior messages in the session
6. **Escalation rules** — when to set needs_escalation (complex reasoning, multi-step planning). Always ask the user before escalating.

### Tool-Use Instructions (included in system prompt)

```
## Tool Use

You have access to read-only tools that query Donna's database. Use them to ground your answers in real data.

### Rules
- ALWAYS use a tool before answering data questions. Never guess or fabricate data.
- When a query returns total_count much larger than the results shown, refine your filters before summarizing.
- Do not summarize records you haven't seen. If you need more data, call the tool again with different filters.
- For summary/aggregate questions, prefer aggregation tools (query_invocation_stats) over paging through individual records.
- When you have enough data to answer, respond with a text response. Do not call tools unnecessarily.
- If you cannot answer confidently with the available tools, set needs_escalation to true and explain why.

### Format
To call a tool, respond with:
{"type": "tool_call", "tool": "<tool_name>", "params": {<params>}}

To respond to the user, respond with:
{"type": "text", "response_text": "<your response>", "needs_escalation": false, "escalation_reason": null}

Always respond with exactly one JSON object. No additional text outside the JSON.
```

---

## 11. Timeout and Loop Limits

| Limit | Value | On breach |
|-------|-------|-----------|
| Max tool calls per message | 10 | Summarize gathered data, respond with what's available |
| Wall-clock timeout | 5 minutes | Same as above, plus log `termination_reason: "timeout"` |
| Max consecutive parse failures | 2 | Terminate loop, respond with "I had trouble processing that" |
| Max retries per malformed call | 1 | Move to next turn or terminate |

---

## 12. File Structure

```
src/donna/chat/
  engine.py                    # Modified: tool loop replaces classify-dispatch-respond
  tools/
    __init__.py                # ToolRegistry class, schema loading, execution
    invocations.py             # query_invocations, get_invocation_detail, query_invocation_stats
    tasks.py                   # query_tasks, get_task_detail (replaces actions/tasks.py reads)
    automations.py             # query_automations, get_automation_detail
    skills.py                  # query_skills, get_skill_detail, query_skill_candidates
    vault.py                   # list_vault_files, read_vault_file (wraps existing)
    system.py                  # get_system_health, query_preferences
  actions/                     # Write actions stay here, unchanged
    tasks.py                   # create_task, update_task, reschedule_task
    vault.py                   # create_vault_note
    skills.py                  # execute_skill, create_skill_draft
    automations.py             # create_automation, list_automations
    debug.py                   # Deprecated — replaced by tools/system.py

config/
  chat_tools.yaml              # Tool schemas and metadata (replaces tool definitions in code)

prompts/chat/
  tool_agent_system.md         # New system prompt with tool schemas and instructions
  chat_system.md               # Persona prompt (unchanged, included by reference)

donna-ui/src/
  pages/Chat/
    MessageThread.tsx           # Modified: add debug link icon on assistant messages
  components/
    QuickChatPanel.tsx          # Already modified for session persistence
```

---

## 13. Migration

Single Alembic migration:
- Add `trace_id` (VARCHAR(36), nullable) to `conversation_messages`
- Add `invocation_ids` (TEXT, nullable) to `conversation_messages` — JSON array, populated once the tool loop completes for the message
- Add `trace_id` (VARCHAR(36), nullable, indexed) to `invocation_log`

---

## 14. Adding New Tools

### When to Add a Tool

Add a read tool when:
- A new data domain is introduced (e.g., a calendar integration, email summaries)
- Users ask questions in Quick Chat that require data the LLM can't currently access
- An existing action handler is read-only and would benefit from tool-loop integration

### How to Add a Read Tool

1. **Create the handler** in `src/donna/chat/tools/<domain>.py`:

```python
async def query_widgets(params: dict[str, Any], ctx: ToolContext) -> ToolResult:
    limit = min(params.get("limit", 25), 100)
    # ... DB query ...
    return ToolResult(
        results=[...],
        total_count=total,
    )
```

2. **Add the schema** to `config/chat_tools.yaml`:

```yaml
query_widgets:
  description: "Search widgets by status and type"
  domain: widgets
  type: read
  handler: donna.chat.tools.widgets.query_widgets
  parameters:
    type: object
    properties:
      status:
        type: string
        enum: [active, archived]
      limit:
        type: integer
        default: 25
        maximum: 100
    required: []
```

3. **Register** — the ToolRegistry auto-discovers tools from `chat_tools.yaml` at startup, same as the existing ActionRegistry pattern.

4. **Test** — add a unit test that calls the handler directly with a mock DB, verifying the result shape and total_count.

### Schema Conventions

- Every read tool returns `{ results: list, total_count: int }`
- Include a `limit` parameter with default 25, max 100
- Use ISO date strings for date parameters
- Use enums for fields with known value sets
- Description should be a single sentence explaining what the tool finds

### Write Tool Additions

Write tools stay in `src/donna/chat/actions/` and are registered in `config/chat_actions.yaml` with `safety: confirm` or `safety: write`. The tool loop treats any tool with `type: write` as requiring confirmation.

---

## 15. Non-Goals

- **Streaming tool results to the UI** — the loop runs server-side; the user sees the final response. A "thinking..." indicator in the UI is sufficient.
- **Parallel tool calls** — the LLM calls one tool at a time. Parallel execution adds complexity without meaningful benefit at this scale.
- **Tool result caching** — each tool call hits the DB fresh. The queries are fast enough on SQLite that caching adds complexity without benefit.
- **Custom tool schemas per page** — all tools are available regardless of page. The page context hint guides the LLM, but doesn't restrict it.
- **Automatic escalation** — escalation to Claude always requires user approval. The LLM can recommend escalation, but the engine pauses and asks.
