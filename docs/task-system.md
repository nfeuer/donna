# Task Management System

> Split from Donna Project Spec v3.0 — Sections 5.1–5.6

## Task Schema

Every task is represented by the following fields. Fields marked **auto** are inferred by the system; the user only provides natural language input. `user_id` is included from day one for future multi-user.

| Field | Type | Source | Description |
|-------|------|--------|-------------|
| id | UUID | Auto | Unique task identifier |
| user_id | String | Auto | Owner. Defaults to primary user. |
| title | String | User/Inferred | Extracted from natural language |
| description | String | User/Agent | Detailed description. May be populated by PM agent. |
| domain | Enum | Inferred | `personal` \| `work` \| `family` (extensible) |
| priority | Int (1–5) | Inferred/User | 1=lowest, 5=critical |
| status | Enum | Auto | `backlog` \| `scheduled` \| `in_progress` \| `blocked` \| `waiting_input` \| `done` \| `cancelled` |
| estimated_duration | Minutes | Inferred | How long the task will take |
| deadline | DateTime? | User/Inferred | Hard deadline if specified. Null if flexible. |
| deadline_type | Enum | Inferred | `hard` \| `soft` \| `none` |
| scheduled_start | DateTime? | Scheduler | When the task is scheduled on the calendar |
| actual_start | DateTime? | Auto | When the user actually started |
| completed_at | DateTime? | Auto | Completion timestamp |
| recurrence | Cron/RRULE? | User | Recurrence pattern |
| dependencies | UUID[] | User/Agent | Tasks that must complete first |
| parent_task | UUID? | Agent | Parent task if this is a subtask |
| prep_work_flag | Boolean | User | Whether prep work should be performed |
| prep_work_instructions | String? | User | What to prepare |
| agent_eligible | Boolean | Inferred/User | Can be delegated to a sub-agent |
| assigned_agent | String? | Orchestrator | Which agent is handling this |
| agent_status | Enum? | Agent | `pending` \| `gathering_requirements` \| `in_progress` \| `review` \| `complete` \| `failed` |
| tags | String[] | User/Inferred | Freeform tags |
| notes | String[] | User/Agent | Running notes and context |
| reschedule_count | Int | Auto | Times rescheduled. Triggers priority escalation. |
| created_at | DateTime | Auto | Creation timestamp |
| created_via | Enum | Auto | `sms` \| `discord` \| `slack` \| `app` \| `email` \| `voice` |
| estimated_cost | Float? | Auto | Estimated API cost if agent-eligible |
| calendar_event_id | String? | Auto | Google Calendar event ID for sync |
| donna_managed | Boolean | Auto | Whether Donna created/manages this calendar event |

## Task Lifecycle State Machine

Defined in `config/task_states.yaml`. Orchestrator loads at startup, rejects invalid transitions. Each transition specifies triggers and side effects.

### Valid Transitions

| From | To | Trigger | Side Effects |
|------|----|---------|-------------|
| backlog | scheduled | Scheduler assigns time slot | Calendar event created; `calendar_event_id` stored; `donna_managed = true` |
| scheduled | in_progress | User acknowledges start OR scheduled time arrives | `actual_start` set |
| scheduled | backlog | User cancels scheduled time | Calendar event deleted; `reschedule_count++` |
| in_progress | done | User/agent reports completion | `completed_at` set; velocity metrics updated |
| in_progress | blocked | User/agent reports blocker | Dependencies updated; blocking reason logged |
| in_progress | scheduled | User requests reschedule | New slot assigned; `reschedule_count++`; calendar event updated |
| blocked | scheduled | Blocker resolved | Scheduler finds next slot |
| blocked | cancelled | User abandons blocked task | Dependent tasks flagged |
| waiting_input | scheduled | User/agent provides info | PM Agent updates task; scheduler assigns slot |
| waiting_input | cancelled | No response after timeout (default 7 days) | User notified; task archived |
| any | cancelled | User explicitly cancels | Dependent tasks flagged; calendar event deleted |
| done | in_progress | User reopens completed task | `completed_at` cleared |

### Invalid Transitions (Enforced)

- `backlog → done`: Cannot complete without scheduling. Must go `scheduled → in_progress → done`.
- `cancelled → any` (except `backlog`): Must be re-opened to backlog first.
- `done → scheduled`: Must go through `in_progress` first.

## Task Deduplication

Two-pass system. Never blocks the capture pipeline.

### Pass 1: Fuzzy Title Match

Uses `rapidfuzz` (Python, fast C implementation) with token-sort ratio.

- **Above 85%** similarity: auto-flag as duplicate, ask user to confirm merge.
- **Below 70%**: clearly different, no further check.
- **70–85%**: proceed to Pass 2.

### Pass 2: LLM Semantic Comparison

For candidates in the 70–85% range, send both descriptions to LLM: "Are these the same task? Respond: `same` (merge), `related` (link but keep separate), or `different` (no relation)."

### User Flow

On duplicate detection, prompt on same channel: "This looks like a duplicate of '[task]' (created [date]). Merge, keep both, or update existing?"

Track false positive and false negative rates in evaluation fixtures.

## Task Type Registry

Defined in `config/task_types.yaml`. Each type specifies: prompt template, output schema, model assignment, tool dependencies.

Adding a new task type = config entry + tool implementation (if new tool needed). No orchestrator code changes.

See `config/task_types.yaml` for the current registry.

## Task Intelligence

### Natural Language Parsing

Example: "Get oil change before end of month" →
- Title: "Get oil change"
- Deadline: End of current month (soft)
- Domain: personal (automotive context)
- Priority: 2 (flexible, no urgency keywords)
- Estimated duration: 60–90 minutes

### Dynamic Priority Escalation

Priority is not static. Re-evaluated daily:
- **Deadline proximity:** Soft deadline approaching → priority increments.
- **Reschedule count:** Each reschedule adds +0.5. After 3 reschedules → flagged for user.
- **Dependency chains:** Downstream tasks waiting → blocking task priority increases.
- **User override:** Manual priority locks it from auto-adjustment.
- **Learned preferences:** Preference engine may apply adjustments (see `docs/preferences.md`).

### Task Complexity Assessment

| Complexity | Criteria | Action |
|-----------|----------|--------|
| Simple | < 30 min, no dependencies | Auto-schedule without interrogation |
| Medium | 30 min–2 hours, may have dependencies | Schedule, optionally flag for prep |
| Complex | 2+ hours, likely has subtasks | Route to PM agent for decomposition |

## Task Domains

| Domain | Scheduling Window | Priority Defaults | Notes |
|--------|-------------------|-------------------|-------|
| Personal | Evenings (5–8pm), Weekends | Standard (1–3) | Flexible, fills gaps |
| Work | 8am–5pm weekdays (extends to 7pm) | Standard to High (2–5) | Respects work calendar |
| Family | Evenings, Weekends, Baby time | High for child-related (3–5) | Never auto-deprioritize |
