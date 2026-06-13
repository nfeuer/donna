# Scheduling Engine

> Split from Donna Project Spec v3.0 — Sections 6.1–6.3

## Calendar Integration

Google Calendar is the single source of truth. Read-write on personal calendar, read on work and family calendars. All three are Google Calendar — no ICS workarounds needed.

### OAuth Setup

The calendar client uses OAuth2 with offline refresh. In Docker
(`DONNA_HEADLESS=true`), a pre-provisioned `token.json` with a valid
refresh token is required. See [Docker operations](../operations/docker.md#google-calendar-oauth-in-docker)
for the provisioning workflow.

### Timezone

All scheduling, notifications, and time-based decisions use the timezone
configured in `calendar.yaml`:

```yaml
timezone: America/New_York
```

This value is loaded once at startup as a `zoneinfo.ZoneInfo` and threaded
through all components via `ctx.tz`. Concretely:

- **EOD digest** fires at 5:30 PM local time (not UTC).
- **Reminder scheduler** flushes the blackout queue based on local hour.
- **Blackout / quiet hours** — all `time_windows` hours in `calendar.yaml`
  are interpreted in the configured timezone.
- **Prompt templates** (input parser, chat, decomposition, preference
  extractor) render `{{ current_date }}` and `{{ current_time }}` in local
  time so the LLM reasons about the user's actual clock.
- **Slot placement** — `Scheduler.find_next_slot` steps candidate slots in
  UTC (DST-safe) and converts each to the configured zone before every
  time-window check, so the absolute blackout and all domain windows are
  enforced on the user's wall clock. Returned slots are timezone-aware in the
  configured zone, so user-facing confirmations show the correct local time.

Components fall back to `America/New_York` if `ctx.tz` is not set, so the
behavior is consistent even if the config key is missing.

### Placement safety (Fable Scheduling S1, 2026-06-11)

`Scheduler.schedule_task` is the serialized placement choke point:

- **All-calendars busy union.** The busy-set is the union of every configured
  calendar (personal + work + family), not just the personal write calendar —
  so a work-calendar meeting blocks a personal placement.
- **Fail-closed reads.** A calendar read error raises `CalendarReadError` and
  aborts placement (surfaced via a fallback alert) rather than booking blind
  against an empty calendar.
- **Serialized writes.** The read→find→create section runs under an
  `asyncio.Lock`, realizing the `spec_v3.md §3.7.1` double-booking guard.
- **Deadline-aware.** The search horizon is clamped to the task's deadline /
  `earliest` bound; an unplaceable dated task surfaces as `needs_scheduling`.

### Calendar Sync Strategy

Polling-based sync with change detection. Polls every 5 minutes (configurable). Compares current calendar state against local mirror in SQLite.

Donna-managed events tagged with Google Calendar extended properties:

```json
{
  "extendedProperties": {
    "private": {
      "donnaManaged": "true",
      "donnaTaskId": "<task-uuid>"
    }
  }
}
```

Invisible to user in Calendar UI, readable by API. Distinguishes Donna events from user-created ones.

### Handling User Modifications to Donna Events

| User Action | System Response |
|-------------|----------------|
| **Time change** | Treated as implicit reschedule. `scheduled_start` updated, `reschedule_count++`. Logged as correction (feeds preference learning). No notification sent. |
| **Event deleted** | Task moved to `backlog`. User notified next interaction: "I noticed you removed [task]. Reschedule or leave in backlog?" |
| **New user event conflicts** | Donna yields. Conflict resolution applies. Donna event auto-shifted to next slot. |

### Conflict Resolution Rules

| Conflict Type | Resolution | Notification |
|--------------|------------|-------------|
| New meeting overlaps scheduled task | Auto-shift task to next slot | None unless priority 4–5 |
| Two meeting invitations at same time | Flag user immediately | SMS or app notification with options |
| High-priority vs low-priority in same slot | Auto-replace, reschedule lower | Include in daily digest |
| Task runs over estimated time | Auto-extend, cascade-shift subsequent | Notify if impacts hard-deadline task |
| User cannot complete task | Accept reschedule or auto-find next slot | Confirm new time via same channel |

## Time Constraints

| Time Block | Hours | Tasks Allowed |
|-----------|-------|---------------|
| Work | 8:00 AM – 5:00 PM (weekdays) | Work domain tasks, meetings |
| Extended Work | 5:00 PM – 7:00 PM (weekdays, optional) | Work overflow, side projects |
| Personal Time | 5:00 PM – 8:00 PM | Personal tasks, R&R, projects, study |
| Baby Time | Per calendar blocks | Family tasks only; never schedule other work |
| Food | Per calendar blocks | Protected; no tasks scheduled |
| Emergency Work | 10:00 PM – 12:00 AM (user-activated) | Only high-priority tasks user explicitly opens |
| Weekends | 6:00 AM – 8:00 PM | Personal and family tasks |
| **Blackout** | **12:00 AM – 6:00 AM (always)** | **No scheduling, no notifications, no contact. No exceptions.** |
| **Quiet Hours** | **8:00 PM – 12:00 AM (default)** | **No new scheduling. Urgent (priority 5) only.** |

### Precedence: Blackout Overrides Quiet Hours

Blackout (12am–6am) and Quiet Hours (8pm–12am) overlap conceptually but have strict precedence:

- **Blackout is absolute.** During blackout hours, nothing goes out — not even priority 5. No scheduling, no notifications, no contact of any kind. Enforced at the notification service level as a hard block.
- **Quiet Hours are soft.** During quiet hours (8pm–12am), only priority 5 urgent notifications break through. New scheduling is suppressed.
- **During the overlap (12am–6am), blackout wins.** If a priority 5 event triggers at 2am, it queues and fires at 6:00 AM when blackout ends. The notification service holds the message until the blackout window closes.

## Conflict Resolution Strategy

### Calendar Conflicts

| Conflict Type | Resolution | Notification |
|--------------|------------|-------------|
| New meeting overlaps scheduled task | Auto-shift task to next slot | None unless priority 4–5 |
| Two meeting invitations at same time | Flag user immediately | SMS or app notification with options |
| High-priority vs low-priority in same slot | Auto-replace, reschedule lower | Include in daily digest |
| Task runs over estimated time | Auto-extend, cascade-shift subsequent | Notify if impacts hard-deadline task |
| User cannot complete task | Accept reschedule or auto-find next slot | Confirm new time via same channel |

### Data Conflicts (Supabase Sync)

- **SQLite is always the source of truth.** On sync conflict, local wins and remote is overwritten.
- Every conflict is logged to `donna_logs.db` with event type `sync.conflict` for audit.
- On Supabase recovery after downtime, a full reconciliation sync runs from SQLite → Supabase.

### State Machine Conflicts (Concurrent Transitions)

- **Phase 1–2 (single-threaded asyncio):** Task state transitions are atomic — read → validate → write in a single async function with a SQLite transaction. No interleaving possible.
- **Worker pool (agent dispatcher):** Optimistic locking on task state. Workers read current state + version → validate transition → write with version check → retry on version mismatch. The orchestrator serializes conflicting writes. See `src/donna/orchestrator/dispatcher.py`.

### Agent Conflicts

- **One agent per task at a time.** The orchestrator enforces this constraint. If agent B needs a task currently locked by agent A, agent B's request is queued until agent A completes or times out.
- Agent outputs are written to the task record only through the orchestrator's internal API, never directly.

## Routing Gate

When a task is captured, a deterministic, LLM-free gate (`donna.scheduling.routing_gate`) decides where it goes next, based on its [`time_intent`](task-system.md#time-intent) and priority. No model call is involved — the decision is a pure function of the extracted intent.

| Time intent | Route | Behavior |
|-------------|-------|----------|
| `exact` / `window` / `constrained` (time-bound) | **Scheduler** | Scheduled immediately. Never deferred for the Challenger. |
| `recurring` | **Automation** | Owned by the automation/cron pipeline (handoff is a stub today — see followup TI-FU2). |
| `none` (undated) | **Backlog** | Left in backlog for the weekly planner / Challenger to surface. Not auto-placed. |

A task carrying a bare `deadline` without a `time_intent` (older rows, app-created tasks, or a model that emitted only `deadline`) is treated as `exact` so it still routes to the scheduler immediately rather than stranding in backlog.

Urgency is computed at the same time: a task is urgent if its derived deadline is within 24 hours or its priority is 4–5.

> **Time-bound tasks schedule independently of the Challenger.** This closes a bug where the auto-scheduler deferred dated tasks pending the Challenger and they stranded in `backlog`. The Challenger no longer gates the scheduling of dated tasks. See followup TI-FU1.

### No Slot Found → `needs_scheduling`

When the scheduler is asked to place a time-bound task but finds no open slot before the deadline (`NoSlotFoundError`), the task transitions to the `needs_scheduling` state rather than silently remaining in `backlog`. This surfaces the unplaceable task explicitly. See [Task System → state machine](task-system.md#valid-transitions).

For a **hard-deadline** task, the **negotiation loop** (Slice A) then tries to make room instead of just parking it: `Scheduler.negotiate_placement` searches the pre-deadline window for a slot whose only blockers are *movable* Donna-managed events (lowest-priority, most-flexible) and proposes displacing them — re-placing each displaced task into a genuinely free slot. Two invariants are absolute: **user-created (non-`donna_managed`) calendar events are never moved**, and **a hard deadline is never silently violated** (an arrangement that can't satisfy one is surfaced with options, never applied). Termination is structural — displaced tasks re-place into *free* slots via `find_next_slot`, so they can never re-displace anything. The loop runs **propose-and-confirm by default** (the user accepts/declines via Discord; accept re-validates under the placement lock), honoring the "never move a committed item silently" invariant; silent auto-apply and cascade-shift are config-gated later slices. Config lives under `calendar.yaml` `negotiation:`. Soft-deadline / undated tasks still just wait in `needs_scheduling`. See `docs/superpowers/specs/2026-06-12-scheduling-negotiation-design.md`.

## Scheduling Algorithm

1. **Weekly Planning (Monday mornings):** Generate proposed week plan. Present to user for review. Lock hard-deadline items first, then fill with flexible tasks.
2. **Daily Recalculation (6:00 AM):** Recalculate today based on previous day's completion, new tasks, calendar changes.
3. **Real-time Adjustment:** New task or reschedule → re-evaluate only affected slots, not entire week.
4. **Minimize Rescheduling:** Prefer inserting into genuinely empty slots before displacing existing tasks. When displacement necessary, move the lowest-priority, most-flexible task.
5. **Get It Done Bias:** Default to scheduling tasks ASAP while respecting constraints. Do not push tasks to "someday."
