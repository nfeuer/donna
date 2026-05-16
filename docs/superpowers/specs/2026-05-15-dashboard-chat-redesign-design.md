# Dashboard Chat Redesign

**Date:** 2026-05-15
**Spec reference:** `spec_v3.md` §4 (Chat Interface), §3.2 (Model Routing), §2.4 (Safety & Autonomy)

## Problem

The dashboard chat is non-functional: sending a message silently fails and returns the user to the empty state. Beyond the bugs, the chat lacks action execution — it can only converse, not do things. And the right-side drawer pattern used across the dashboard conflicts with the planned chat panel placement and is uncomfortable on wide screens.

## Goals

1. Fix the broken chat (3 bugs causing silent failure)
2. Add a global quick chat panel available on every page, context-aware of the current page and selected item
3. Build a config-driven action registry so chat can execute real actions (create tasks, read vault files, execute skills, etc.)
4. Replace right-side drawers with center dialogs and inline expansion
5. Design for future migration to full agentic tool-use (Approach 3)

## Non-Goals

- Streaming responses (nice-to-have, not in this spec)
- WebSocket real-time updates (REST is sufficient for now)
- Multi-tool chaining in a single turn (that's Approach 3)

---

## Section 1: Bug Fixes

### 1a. Missing session ID in response

The `POST /chat/sessions/{session_id}/messages` response does not include the session ID. When `session_id` is `"new"`, the backend creates a session but the frontend never learns its ID. `activeSessionId` stays `null`, every subsequent message creates a new orphaned session.

**Fix:** Add `session_id` to the response dict in `src/donna/api/routes/chat.py`. The engine's `handle_message` already has access to the session — include `session.id` in the return payload. Frontend captures it and sets `activeSessionId` on the first response.

### 1b. Silent 4xx error swallowing

The axios global interceptor (`src/api/client.ts`) only toasts on 500+ and network errors. Auth failures (401/403), validation errors (400), and engine-not-initialized (503) disappear without feedback.

**Fix:** Extend the interceptor to toast on all error responses. Use the `detail` field from the backend JSON body. 401/403 get a specific "Authentication required" message.

### 1c. Session list never loads

`const [sessions] = useState<ChatSession[]>([])` has no setter and no fetch call. The sidebar is permanently empty.

**Fix:** Add a `GET /chat/sessions` endpoint to the backend (list sessions for current user, filterable by `status`, `channel`, `limit`). Add a `listSessions` function to `src/api/chat.ts`. Fetch on mount and after new session creation.

---

## Section 2: UI Architecture

### 2a. Full `/chat` page improvements

The page structure stays the same (session sidebar + conversation panel). Changes:

- **Session sidebar works** — fetches sessions, shows timestamps and message counts, supports create/close
- **Session ID tracking** — after first message to `"new"`, capture returned `session_id` and set as `activeSessionId`
- **Better empty state** — capabilities overview with quick-start suggestion chips instead of plain text
- **Loading/sending states** — typing indicator or skeleton while waiting for response
- **Action result rendering** — structured blocks for action results: confirmation cards for writes, code/file blocks for vault reads, status badges for skill execution. Plain text for freeform chat.
- **Full context meter** — progress bar showing token usage (existing `ContextMeter` component, now wired up)

### 2b. Global quick panel

A floating button (bottom-right) on every page except `/chat`. Click or keyboard shortcut opens a slide-out panel.

- **Context injection** — reads current route + selected item from `DashboardContext` provider. Sent as metadata with each message.
- **Independent sessions** — each quick panel session is separate from `/chat` page sessions. Short-lived: 15 min TTL (vs 120 min for full chat). Channel: `"dashboard_quick"`.
- **Minimal UI** — no session sidebar, no full context meter. Components:
  - Context chip at top ("Viewing: Tasks" or "Viewing: Fix auth flow")
  - Compact token counter (`1.2k / 24k`) in the header
  - Message thread (shared `MessageThread` component)
  - Message input (shared `MessageInput` component)
  - Suggested actions
- **Keyboard shortcut** — `Cmd+J` (or `Ctrl+J`) to toggle the panel
- **Navigation behavior** — when the user navigates to a different page while the panel is open, the context chip updates to the new page and `selectedItem` clears. The session continues (not closed) — this lets you ask follow-up questions across pages within the same short-lived session. Navigating to `/chat` closes the quick panel (the full page takes over).
- **Shared components** — `MessageThread`, `MessageInput`, and action result renderers are shared between quick panel and full `/chat` page. The panel is a thin wrapper.

### 2c. Drawer replacement

Replace the shared `Drawer` primitive as the default detail view pattern. The `Drawer` component itself is not deleted.

New `CenterDialog` primitive: Radix Dialog, centered, `max-width: 640px`, styled consistently with existing design tokens.

Page-by-page mapping:

| Page | Component | New Pattern | Rationale |
|------|-----------|-------------|-----------|
| Tasks | TaskDetailDrawer | Inline expansion | Simple fields + state stepper, list context matters |
| Logs | TraceView | Center dialog | Traces can be long, need focused reading |
| Skill System | SkillDrawer | Center dialog | Complex content, multiple tabs |
| Skill System | AutomationDrawer | Center dialog | Config-heavy, needs focus |
| Skill System | CandidateDrawer | Inline expansion | Short content, list context helps |
| Skill System | RunDrawer | Center dialog | Output logs can be long |
| Preferences | RuleDetailDrawer | Inline expansion | Simple key-value fields |
| Shadow | ComparisonDrawer | Center dialog | Side-by-side comparison needs width |

---

## Section 3: Action Registry & Execution Pipeline

### 3a. Action registry config

File: `config/chat_actions.yaml`

Each action defines:

```yaml
actions:
  query_tasks:
    description: "List or search tasks by status, priority, or domain"
    domain: tasks
    safety: read          # read | write | confirm
    handler: donna.chat.actions.tasks.query_tasks
    parameters:
      type: object
      properties:
        status:
          type: string
          enum: [captured, triaged, scheduled, in_progress, blocked, done, cancelled]
        priority:
          type: string
          enum: [P0, P1, P2, P3]
        domain:
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
        title: { type: string }
        description: { type: string }
        priority: { type: string, enum: [P0, P1, P2, P3] }
        domain: { type: string }
      required: [title]

  execute_skill:
    description: "Run a skill and report results"
    domain: skills
    safety: confirm
    handler: donna.chat.actions.skills.execute_skill
    parameters:
      type: object
      properties:
        skill_name: { type: string }
        input_data: { type: object }
      required: [skill_name]
```

Safety levels:

- **`read`** — executes immediately, result goes straight to response
- **`write`** — executes immediately, Donna reports what she did or failures
- **`confirm`** — Donna describes the action and waits for explicit confirmation before executing

### 3b. Execution pipeline

The existing intent classifier output (`{ intent, needs_escalation, escalation_reason }`) is extended with two new fields: `domain` (action domain hint) and `action_hint` (best-guess action name). The `classify_intent.md` prompt and output schema are updated accordingly. Existing fields are preserved — escalation detection still works.

```
1. User sends message
2. Intent classifier returns { intent, domain, action_hint, needs_escalation, escalation_reason }
3. Engine matches against action registry:
   - Clear match → extract parameters via schema-guided prompt
   - Ambiguous → ask user to clarify
   - No match → fall through to freeform chat
4. Validate extracted params against action's JSON schema
   - Fail → tell user what's missing
5. Check safety level:
   - read/write → execute immediately
   - confirm → return confirmation prompt, store pending action on session
6. Execute handler → ActionResult
7. Generate response via LLM with action result as context
```

For `confirm` actions, the pending action (action name + extracted params) is stored as JSON on the session's `pending_action` field. When the user confirms ("yes", "go ahead"), the engine retrieves and executes without re-extraction.

### 3c. Handler interface

All handlers follow the same async signature:

```python
@dataclass
class ActionContext:
    db: Database
    user_id: str
    session_id: str
    config: ChatConfig
    dashboard_context: dict | None  # page, selected_item from frontend

@dataclass
class ActionResult:
    success: bool
    data: dict          # structured result
    summary: str        # human-readable one-liner
    error: str | None   # if success=False
```

```python
async def handler(
    params: dict,
    context: ActionContext,
) -> ActionResult:
    ...
```

Handlers live in `src/donna/chat/actions/` organized by domain.

### 3d. Initial action set

| Action | Domain | Safety | Description |
|--------|--------|--------|-------------|
| `query_tasks` | tasks | read | List/filter tasks |
| `get_task` | tasks | read | Get task detail by ID or title |
| `create_task` | tasks | write | Create new task |
| `update_task` | tasks | write | Update status, priority, notes |
| `reschedule_task` | tasks | write | Change scheduled date |
| `read_vault_file` | vault | read | Read a file from the vault |
| `create_vault_note` | vault | write | Create a new note |
| `list_vault_files` | vault | read | List files in vault |
| `execute_skill` | skills | confirm | Run a skill |
| `list_skills` | skills | read | List available skills |
| `create_skill_draft` | skills | write | Draft a new skill definition |
| `create_automation` | automations | confirm | Create a new automation rule |
| `list_automations` | automations | read | List active automations |
| `get_debug_data` | debug | read | System status, queue depth, recent errors |
| `get_agent_status` | debug | read | Agent run history and status |

### 3e. Future: Migration to Approach 3 (Full Tool-Use)

The action registry is designed to become the tool definition source for Approach 3. When the local LLM (or a cloud model) supports function calling well enough:

- `chat_actions.yaml` entries generate tool schemas automatically
- The two-stage pipeline (classify → extract) collapses into a single LLM call with tool definitions
- Handlers stay identical — only the routing layer changes
- Safety levels remain enforced by the engine regardless of how the LLM invokes tools

---

## Section 4: Context Awareness

### 4a. Frontend context provider

A `DashboardContext` React context wraps `AppShell`:

```typescript
interface DashboardContextValue {
  currentPage: string;
  selectedItem: SelectedItem | null;
  setSelectedItem: (item: SelectedItem | null) => void;
}

interface SelectedItem {
  type: "task" | "agent" | "skill" | "log_entry" | "vault_file" | "automation";
  id: string;
  label: string;
}
```

- `currentPage` — derived automatically from `useLocation()`
- `selectedItem` — set by pages when user clicks/focuses a row. Cleared on page navigation.
- Pages opt in by calling `setSelectedItem`. Pages that don't call it simply have no selected item.

### 4b. Context injection into messages

The quick panel includes dashboard context in the request body:

```json
{
  "text": "reschedule this to Monday",
  "channel": "dashboard_quick",
  "context": {
    "page": "tasks",
    "selected_item": {
      "type": "task",
      "id": "abc123",
      "label": "Fix auth flow"
    }
  }
}
```

The full `/chat` page sends `{ "page": "chat", "selected_item": null }`.

### 4c. Backend context resolution

The engine uses dashboard context in two ways:

1. **Pronoun resolution** — the parameter extraction prompt receives: "The user is viewing the Tasks page and has selected task 'Fix auth flow' (id: abc123). When they say 'this' or 'it', they mean this task."
2. **Action scoping** — page context helps the intent classifier prefer domain-relevant actions (e.g., "show errors" on Agents page → `get_agent_status` over `get_debug_data`)

### 4d. Quick panel context chip

Top of the quick panel shows current scope:

- Page only: "Viewing: Tasks"
- Page + item: "Viewing: Fix auth flow"
- Clicking the chip clears `selectedItem` (reverts to page-only context, e.g., "Viewing: Tasks") for unrelated questions

### 4e. Compact token counter

The quick panel header shows a compact token count next to the context chip: `1.2k / 24k`. Updates after each message. Visible from session start so the baseline context cost is always clear.

The full `/chat` page retains the progress bar (`ContextMeter` component).

---

## Section 5: Backend Changes

### 5a. New API endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `GET /chat/sessions` | GET | List sessions for current user. Params: `status`, `channel`, `limit` |
| `POST /chat/sessions/{id}/confirm` | POST | Confirm a pending confirm-level action. Body: `{ "confirmed": true }` |
| `GET /chat/actions` | GET | List available actions from registry |

### 5b. Modified endpoints

| Endpoint | Change |
|----------|--------|
| `POST /chat/sessions/{id}/messages` | Add `session_id` to response. Accept `context` in request body. |

### 5c. Engine modifications

- **`ActionRegistry`** class — loaded from `config/chat_actions.yaml` at startup. Methods: `match(intent, domain)`, `get(action_name)`, `list()`.
- **`handle_message` update** — after intent classification, check for action match. Branch into action pipeline if matched, otherwise continue freeform.
- **Dashboard context handling** — accept `context` from request, inject into prompt assembly.
- **Pending action storage** — confirm-level actions store action name + params on session.

### 5d. New modules

```
src/donna/chat/
├── engine.py              # modified
├── actions/
│   ├── __init__.py        # ActionRegistry, ActionResult, ActionContext
│   ├── tasks.py           # query, get, create, update, reschedule
│   ├── vault.py           # read, create_note, list
│   ├── skills.py          # execute, list, create_draft
│   ├── automations.py     # create, list
│   └── debug.py           # debug_data, agent_status
├── config.py              # modified
├── context.py             # modified
└── types.py               # modified
```

### 5e. Database changes (Alembic migration)

Additive columns only:

- `conversation_sessions` — add `pending_action` (JSON, nullable)
- `conversation_messages` — add `action_name` (text, nullable), `action_result` (JSON, nullable)

### 5f. Config changes

- **New file:** `config/chat_actions.yaml` — action registry
- **Modified:** `config/chat.yaml` — add:
  ```yaml
  quick_panel:
    ttl_minutes: 15
    channel: dashboard_quick
  actions:
    enabled: true
  ```

### 5g. New frontend components

```
donna-ui/src/
├── context/
│   └── DashboardContext.tsx    # provider + hook
├── components/
│   ├── QuickChatPanel.tsx      # slide-out panel
│   ├── QuickChatButton.tsx     # floating trigger button
│   ├── ActionResultCard.tsx    # structured result rendering
│   └── ConfirmActionBanner.tsx # confirm-level action prompt
├── primitives/
│   └── CenterDialog.tsx        # new primitive (Radix Dialog, centered)
│   └── CenterDialog.module.css
└── pages/Chat/
    └── index.tsx               # modified — session tracking, sidebar fetch
```

### 5h. New prompts

```
prompts/chat/
├── extract_action_params.md   # schema-guided parameter extraction
├── summarize_action_result.md # generate response from action result
└── resolve_context.md         # pronoun resolution with dashboard context
```
