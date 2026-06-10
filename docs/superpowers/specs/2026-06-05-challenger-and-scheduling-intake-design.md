# Challenger & Scheduling Intake — Design Spec

**Date:** 2026-06-05
**Status:** Approved (brainstorm) — pending implementation plan
**Related:** `spec_v3.md §7.1.1` (agent hierarchy), `spec_v3.md §7.2` (agent execution flow),
`docs/superpowers/specs/2026-05-11-task-scheduling-flows-design.md` (auto-scheduler + `challenger_resolved` event)

## Problem

Tasks created via Discord can silently strand in `backlog` and never get scheduled, even
when they carry a clear deadline. Two real cases on 2026-06-03 ("send invoices to Kevin
tomorrow", "bring car in to mechanic Friday") were captured with a parsed `deadline` but
`deadline_type='none'`, no `scheduled_start`, and stayed in `backlog`.

Three root causes, in order of severity:

1. **Challenger defer/resume asymmetry (the strand bug).** At creation the Discord path
   always sets `challenger_pending = (dispatcher is not None)` (`discord_bot.py:537`), and
   `AutoScheduler.on_task_created` *defers* scheduling when that flag is set
   (`auto_scheduler.py:46-50`). The only thing that ever un-defers is a `challenger_resolved`
   event, which is emitted in exactly one place — a **user reply in a Challenger thread**
   (`discord_bot.py:1152-1157`). Challenger threads are only opened when
   `result.status == "needs_input" and result.questions` (`discord_bot.py:1104`). For every
   other outcome (`ready`, `ambiguous`, `escalate_to_claude`, `complete`, or a dispatch that
   throws and is swallowed at `1100-1102`) the task is deferred at creation and **never
   un-deferred**. A degraded local LLM returns empty output → `ready`/throw → permanent strand.

2. **Urgency is owned by nobody.** Scheduling urgency should come from the deadline, but the
   scheduler only keys off `priority` (`scheduler.py:103`). Deadline *type* (hard/soft) and
   "schedule this now" are not computed anywhere reliable. The result is that a task due
   *tomorrow* gets no urgency treatment.

3. **The intake LLM is boxed into a narrow vocabulary.** The Challenger parse path constrains
   the LLM to `intent_kind ∈ {task, automation, question, chat}` and a quality prompt that only
   asks about success criteria / dependencies / scope. It cannot express "this is urgent",
   "this is really two tasks", "this is blocked on X", or "this needs prep first", and it has
   no representation of *windowed*, *constrained*, or *recurring* time intents — those all
   collapse into a single `deadline` datetime, losing their semantics.

## Goals

- A time-bound task **always** routes to scheduling immediately and is **never** gated by the
  Challenger. The Challenger enriches context off the critical path.
- Urgency is **deterministic** (derived from deadline proximity + priority), so dated tasks
  schedule correctly even when the LLM is degraded or down.
- Represent the full range of temporal phrasing: exact, window, constrained, recurring, none.
- Constraint-aware, *flexible* placement: when Donna can't satisfy a constraint she proposes an
  alternative, and on rejection proposes moving other tasks — a real assistant negotiation.
- Auto-place into open slots; **any move of an existing item requires user confirmation**.
- Informative, on-persona capture confirmations that state the real date / day / time.
- Nothing is ever silently lost: an unplaceable task rests in a surfaced `needs_scheduling`
  state, not a dead `backlog`.

## Non-Goals

- No new dedicated "urgency" or "triage" LLM agent — urgency is rule-based.
- No collapse of the input parser and Challenger into one mega-prompt (protects context window
  and per-call quality).
- No change to the existing automation/cron pipeline's internals — recurring intents *route*
  to it; how it runs is unchanged.
- No autonomous moving of protected items (hard-deadline, higher-priority, user-placed,
  in-progress) without an explicit user override. (Override UX beyond a yes/no confirm is out
  of scope.)
- Sentiment detection is explicitly out of scope for this work.

## Ownership Model (after this work)

| Component | Owns | Uses LLM? |
|-----------|------|-----------|
| Input parser | Extract fields + `time_intent` (the *what* and *when-expressed*) | Yes — one focused call |
| Urgency / routing gate | Decide route + urgency from extracted facts | **No — deterministic** |
| Scheduler (Agent + core) | Placement, constraint-solving, negotiation/rearrange | Deterministic core; templates for messages |
| Challenger | "Do we understand this task?" — bounded quality flags + free-text observations | Yes — focused, small prompt, off critical path |
| Novelty Judge | Novel / unclassifiable observations | Yes — Claude |

The decisive architectural change: **the Challenger moves off the scheduling critical path.**
It enriches a task; it never blocks placement.

## Design

### 1. `time_intent` data model

A `time_intent` becomes the rich source of truth for *when* a task should happen. The existing
`deadline` / `deadline_type` columns are **kept and derived** so downstream consumers
(`reminders.py`, `overdue.py`, `weekly_planner.py`) keep working unchanged — backward compatible.

```jsonc
time_intent = {
  "kind": "exact" | "window" | "constrained" | "recurring" | "none",
  "due_at":   "<ISO datetime> | null",   // exact
  "earliest": "<ISO datetime> | null",   // window / constrained lower bound
  "latest":   "<ISO datetime> | null",   // window / constrained upper bound
  "strictness": "hard" | "soft",          // replaces deadline_type semantics
  "constraints": {                         // constrained only; null otherwise
    "weekday": [0..6],                     // e.g. [0] = Monday-only
    "time_of_day": "morning|afternoon|evening|null"
  } | null,
  "recurrence": { "rrule_or_cron": "<str>", "human_readable": "<str>" } | null
}
```

**Storage:** add a `time_intent_json TEXT` column to `tasks` (Alembic migration, per project
convention — never hand-edit tables). Keep `deadline` / `deadline_type` columns.

**Back-compat derivation** (applied whenever a task is created/updated):
- `deadline ← due_at` (exact) or `latest` (window/constrained); `null` for `recurring`/`none`.
- `deadline_type ← strictness`; `none` when `kind == "none"`.

**Mapping of the canonical examples:**

| Phrase | kind | normalized |
|--------|------|------------|
| "tomorrow" / "Monday" / "by Friday" | `exact` | `due_at`, strictness from phrasing |
| "sometime next week" | `window` | `earliest`=next Mon 00:00, `latest`=next Sun 23:59, `soft` |
| "by the end of the month" | `window` | `earliest`=now, `latest`=month-end, `soft` |
| "on a Monday within the next month" | `constrained` | window `[now, +1mo]`, `constraints.weekday=[0]` |
| "remind me every Wednesday" | `recurring` | `recurrence` weekly Wed → automation/cron pipeline |
| "organize the garage" | `none` | no time fields |

### 2. Parser extraction

The input parser emits `time_intent` as part of its structured output (extend
`schemas/task_parse_output.json` and `prompts/parse_task.md`). The parser's job is **extraction
only** — it does not decide urgency or routing.

**Degraded-LLM fallback (robustness):** if the LLM parse fails, a deterministic date fallback
(e.g. `dateutil`/`dateparser` over the raw text) still resolves common phrasings — "tomorrow",
"Friday", "next week", "end of month" — into at least a coarse `time_intent` so dated tasks
still route. Any degraded fallback calls `dispatch_fallback_alert()` per the project rule
(CLAUDE.md). The fallback never throws away a recognizable date.

### 3. Deterministic urgency / routing gate

A pure function (no LLM) consumes the extracted task + `time_intent` and decides the route:

```
route(task, time_intent):
  if kind == "recurring":                      -> AUTOMATION pipeline (cron); confirm capture
  if kind in {"exact","window","constrained"}: -> SCHEDULER now (never defer)
  if kind == "none":                           -> CHALLENGER may probe; else BACKLOG (surfaced)
```

`challenger_pending` is **no longer** set on time-bound tasks. The Challenger runs in parallel
(Section 6) and its outcome cannot change whether a dated task is scheduled.

**Urgency** is a derived scalar used for slot ordering and nudge cadence, computed from
`latest`/`due_at` proximity and `priority` (exact rule defined in the implementation plan;
e.g. "deadline within N hours OR priority ≥ 4 ⇒ urgent"). It is never an LLM output.

### 4. Constraint-aware placement

`Scheduler.find_next_slot` gains a constraint filter. A candidate slot is valid iff it:
- falls within `[earliest, latest]` (defaulting `earliest`=now, `latest`=`due_at` for `exact`),
- satisfies `constraints` (weekday, time-of-day) when present, **and**
- passes the existing priority / domain-window / quiet-hours checks.

The first valid open slot wins → **auto-place**: set `scheduled_start`, transition to
`SCHEDULED`, set `donna_managed=true`, create the calendar event (or fallback per
`auto_scheduler.py:69-77`). No confirmation needed for placing into an *open* slot.

### 5. Negotiation & rearrange loop

**Trigger:** Section 4 finds no valid *open* slot for a must-place task.

**Conversational state.** A pending-negotiation map keyed by user/thread, mirroring the existing
`_dedup_pending` and `_challenger_threads` patterns in `discord_bot.py`. States:

1. `proposing_alternative` — Donna proposes the nearest slot that *would* satisfy (relaxing to
   next availability while still honoring hard constraints like weekday where possible).
   - user **accepts** → place at the alternative; done.
   - user **rejects** → go to `proposing_rearrange`.
2. `proposing_rearrange` — Donna proposes a concrete bump plan (below).
   - user **accepts** → execute the moves + place the new task.
   - user **rejects** → leave the task in `needs_scheduling`; back off gracefully.

**Bump planner (deterministic — no LLM in the math):**
- Identify the existing item(s) blocking a valid slot for the new task.
- A blocker is **movable** iff: `donna_managed == true` AND strictness is `soft` (or no
  deadline) AND `priority ≤` the new task's priority AND not in-progress.
- Relocate each movable blocker to *its own* next valid slot (honoring *its* constraints /
  deadline). Only propose a plan in which **every** displaced task lands somewhere valid.
- Prefer the plan that moves the **fewest, lowest-priority** tasks (minimize disruption).
- If no valid plan exists → honest message: "Everything in the way is locked — hard deadlines
  or higher priority. You'll have to bump one yourself."

**Guardrails — never crossed without explicit user override.** Donna never moves a task that is
hard-deadline, higher-priority, non-`donna_managed` (user-placed), or in-progress. This is the
concrete form of the chosen autonomy rule ("auto-place open slots; moves require confirmation").

**Execution:** on confirm, update each displaced task's `scheduled_start` + calendar event,
then place the new task, then send a one-line summary of what moved.

**Robustness:** if the user never replies, the task rests in `needs_scheduling` (surfaced in
digests / weekly plan), never silently lost. The planner math is deterministic; only message
wording is templated.

### 6. Challenger off the critical path

The Challenger still runs on task creation, but:
- It **never** sets/relies on `challenger_pending` for scheduling. Time-bound tasks are already
  routed to the scheduler by Section 3 before/independently of the Challenger.
- Its output vocabulary is widened from the 4-value `intent_kind` to a **bounded set of flags**
  plus a free-text escape hatch:
  - structured flags: `urgent`, `multi_task`, `blocked_on`, `needs_prep`,
    `vague_success_criteria`, `ambiguous_scope`
  - `observations: string` — free text for anything outside the taxonomy.
- `urgent` and `blocked_on` are *advisory* — they can raise a nudge or annotate the task, but
  the deterministic gate (Section 3) remains the source of truth for whether/when a dated task
  is scheduled. (Rationale: keep robustness; the LLM informs, it does not gate.)
- Novel / low-confidence `observations` route to the Claude Novelty Judge, per
  `spec_v3.md §7.1.1` ("judges tasks the Challenger cannot classify").
- On Challenger error: log + `dispatch_fallback_alert()`; the task proceeds unaffected.

The `needs_input` → thread → `challenger_resolved` flow is retained for genuine clarification,
but it is now *additive context*, not a scheduling gate.

### 7. Informative confirmation copy (Donna persona)

Replaces the static `"… Scheduled: pending."` (`discord_bot.py:547-551`). Authored as
**templates** (deterministic, zero-token, persona-consistent per `prompts/donna_persona.md`):

- **Placed (exact):** `Done. Invoices to Kevin — **Friday, Jun 6, 2:00–2:30 PM**. (personal · P2)`
- **Soft window:** `Penciled in for **Wed morning, Jun 11** — it's flexible, I'll tighten it as your week fills. Deadline's the 13th.`
- **Recurring:** `**Every Wednesday, 9:00 AM.** Done.`
- **No slot → negotiation:** `Monday's not happening — you're booked solid before the deadline. Closest I've got is **Tue 10:00 AM**. Take it, or want me to move something to free up Monday?`
- **Rearrange proposal:** `Here's the move: 'Review Q2 budget' → **Tue 2:00 PM**, which clears **Mon 10:00 AM** for the invoices. Good?`
- **No time (none):** `Filed 'organize the garage' in your backlog. No deadline, so I'll raise it in your weekly plan — unless you tell me it matters sooner.`

### 8. New task state

Add `needs_scheduling` to `config/task_states.yaml` with transitions:
`backlog → needs_scheduling` (placement failed, awaiting negotiation outcome),
`needs_scheduling → scheduled` (alternative or rearrange accepted),
`needs_scheduling → backlog` (user declines; resurfaced by weekly planner).
All transitions go through the state machine per project convention.

## Data Flow (end to end)

```
Discord capture
  → Parser: extract fields + time_intent            (focused LLM call, with date fallback)
  → Urgency/Routing gate (deterministic):
       recurring                → automation/cron pipeline → confirm
       exact|window|constrained → Scheduler NOW (never defer)
       none                     → Challenger may probe; else surfaced backlog
  → Scheduler.find_next_slot (constraint-aware):
       open slot found → auto-place (scheduled_start, donna_managed, calendar)
       no open slot    → needs_scheduling + open Negotiation loop (Section 5)
  → Challenger runs in PARALLEL (context/quality; never gates placement)
  → Persona confirmation sent (Section 7)
```

## Error Handling & Robustness

- **Parser LLM failure** → deterministic date fallback; dated tasks still route; fallback alert.
- **Challenger failure** → non-blocking by construction; log + fallback alert; task proceeds.
- **Scheduler exception** → existing `auto_scheduler.py:81-90` path (alert + leave in backlog),
  but the task lands in `needs_scheduling` (surfaced), not silent `backlog`.
- **User never responds to negotiation** → task rests in `needs_scheduling`, surfaced in digests.
- Negotiation bump math is deterministic and does not depend on the LLM being healthy.

## Testing

- Unit: one test per `time_intent.kind` → expected route + (for time-bound) a valid slot.
- Unit: constraint filter — weekday/time-of-day honored; rejects invalid slots.
- Unit: bump planner — picks fewest/lowest-priority movable tasks; refuses to move protected
  tasks; returns "no plan" honestly when everything is locked.
- Unit: the five confirmation templates render with correct date/day/time.
- Regression (the strand bug): Challenger returns `ready` / throws → a dated task still ends
  `scheduled`, never `backlog`.
- Robustness: degraded-LLM parse → "invoices tomorrow" still schedules via the date fallback.
- Integration: full capture → place → confirm; capture → no-slot → negotiate → rearrange → place.

## File Changes Summary

**New:**
- `src/donna/scheduling/time_intent.py` — `time_intent` model + derivation helpers.
- `src/donna/scheduling/routing_gate.py` — deterministic route/urgency function.
- `src/donna/scheduling/negotiator.py` — negotiation state machine + bump planner.
- Alembic migration — add `tasks.time_intent_json`.

**Modified:**
- `prompts/parse_task.md`, `schemas/task_parse_output.json` — emit `time_intent`.
- `prompts/challenger_parse.md`, `schemas/challenger_parse.json` — bounded flags + `observations`.
- `src/donna/scheduling/scheduler.py` — constraint-aware `find_next_slot`.
- `src/donna/scheduling/auto_scheduler.py` — consume routing gate; drop unconditional defer.
- `src/donna/integrations/discord_bot.py` — routing on capture, negotiation state, persona
  confirmation; Challenger off the critical path.
- `config/task_states.yaml` — add `needs_scheduling` state + transitions.
- `src/donna/agents/challenger_agent.py` — widened result vocabulary; non-gating.

## Open Questions / Follow-ups

- Exact urgency formula (proximity threshold, priority cut-off) — pin in the implementation plan.
- Whether "recurring reminder" deserves its own primitive vs. riding the automation/cron path
  (noted as semantically muddy; deferred — log in `followups.md`).
- `spec_v3.md §7.1.1 / §7.2` must be updated to reflect the Challenger moving off the critical
  path and the new deterministic routing gate.
