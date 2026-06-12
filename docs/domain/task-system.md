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
| status | Enum | Auto | `backlog` \| `scheduled` \| `needs_scheduling` \| `in_progress` \| `blocked` \| `waiting_input` \| `paused` \| `done` \| `cancelled` |
| estimated_duration | Minutes | Inferred | How long the task will take |
| deadline | DateTime? | User/Inferred | Hard deadline if specified. Null if flexible. Derived from `time_intent` when not set explicitly. |
| deadline_type | Enum | Inferred | `hard` \| `soft` \| `none`. Derived from `time_intent.strictness` when not set explicitly. |
| time_intent | TimeIntent? | Inferred | Structured *when* of the task, stored as `time_intent_json`. See [Time Intent](#time-intent). |
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
| backlog | needs_scheduling | Scheduler finds no slot before the deadline (`scheduler_no_slot_found`) | Negotiation opened — task surfaced as unplaceable rather than left silently in backlog |
| needs_scheduling | scheduled | Alternative slot or rearrange accepted (`alternative_or_rearrange_accepted`) | Calendar event created; `donna_managed = true` |
| needs_scheduling | backlog | User declines scheduling (`user_declines_scheduling`) | Task resurfaced in the weekly plan |
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

### Time Intent

`time_intent` is the structured representation of *when* a task should happen, separate from *what* it is. The input parser emits it (validated against `schemas/task_parse_output.json`), it is persisted as the `time_intent_json` column, and it drives routing — see [Scheduling → Routing Gate](scheduling.md#routing-gate).

It classifies temporal intent into five kinds:

| Kind | Meaning | Fields set |
|------|---------|------------|
| `exact` | A specific point ("tomorrow", "by Friday 5pm") | `due_at` |
| `window` | A flexible range ("sometime next week", "by end of month") | `earliest`, `latest` |
| `constrained` | A range plus a structural rule ("a Monday within the next month") | `earliest`, `latest`, `constraints` (e.g. `{"weekday": [0]}`) |
| `recurring` | Repeats ("every Wednesday") | `recurrence` |
| `none` | No time expressed | — |

`strictness` is `hard` when missing the time has real consequences, else `soft`.

The legacy `deadline` / `deadline_type` fields are **derived** from `time_intent` at task creation when not set explicitly: `deadline` from `due_at` (exact) or `latest` (window/constrained), and `deadline_type` from `strictness`. This keeps existing consumers (reminders, overdue detector, weekly planner) working unchanged. If the parsing model omits `time_intent`, an LLM-free fallback (`donna.scheduling.date_fallback`) re-extracts common phrasings ("tomorrow", a weekday, "next week", "end of month") so dated tasks still route.

### Dynamic Priority Escalation

Priority is not static. Re-evaluated daily:
- **Deadline proximity:** Soft deadline approaching → priority increments.
- **Reschedule count:** Each reschedule adds +0.5. After 3 reschedules → flagged for user.
- **Dependency chains:** Downstream tasks waiting → blocking task priority increases.
- **User override:** Manual priority locks it from auto-adjustment.
- **Learned preferences:** Preference engine may apply adjustments (see `docs/preferences.md`).

#### Priority Cap at 5

Priority has an absolute ceiling of 5. When an escalation action (deadline proximity, reschedule count, dependency chain) would push a task's computed priority above 5:

1. Priority remains at 5.
2. Check whether the user has been notified about this task hitting the cap **today**.
3. If **not notified today** → trigger an immediate notification: "Priority for '[task]' has hit maximum. [reason for escalation]."
4. If **already notified today** → log the cap-hit event, skip notification (prevent spam).

The notification dedup resets daily at midnight. This ensures the user knows when tasks are critically stacking up without being overwhelmed by repeated alerts for the same task.

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

## Manual Escalation Terminal

Tasks that exceed the daily API budget OR `task_approval_threshold_usd`
do not silently pause. They flow into the over-budget decision tree
defined in
[`docs/superpowers/specs/manual-escalation.md`](../superpowers/specs/manual-escalation.md):
the user sees a Discord prompt with `Approve $X / Manual / Pause /
Cancel`, picks a terminal, and the task continues, parks, or closes
accordingly. `Manual` further branches to either `chat` or
`claude_code` mode depending on the task type's
`config/task_types.yaml` `manual_escalation` block. Open escalations
appear in the dashboard at `/admin/escalations`. The work lands across
slices 17–24.
