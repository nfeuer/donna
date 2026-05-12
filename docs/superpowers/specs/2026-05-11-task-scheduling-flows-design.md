# Task Scheduling Flows — Design Spec

**Date:** 2026-05-11
**Spec ref:** §4.3 (Task State Machine), §5.1 (Scheduling), §6.2 (Notifications)
**Status:** Draft

## Problem

Tasks created via Discord (or any input channel) stay in BACKLOG indefinitely.
The `Scheduler.schedule_task()` method exists and works, but nothing calls it
after task creation. The `ReminderScheduler`, `OverdueDetector`, and
`WeeklyPlanner` are fully implemented but never instantiated or started at boot.
The morning digest runs via a standalone `ctx.tasks.append()` workaround rather
than through the designed `NotificationTasks` pathway.

Additionally, `project_root` resolves to `/usr/local/lib/python3.12` inside
Docker (because `cli_wiring.py` is pip-installed into site-packages), causing
the digest template and all prompt/schema file reads to fail with
`FileNotFoundError`.

## Goals

1. Every new task is auto-scheduled to a calendar slot (or fallback time) immediately after creation.
2. If the challenger agent has questions, scheduling waits until the user answers.
3. ReminderScheduler, OverdueDetector, and WeeklyPlanner run at boot.
4. All background notification loops use `NotificationTasks` via `run_server()`.
5. The `project_root` path resolves correctly in both dev and Docker.

## Non-Goals

- Changing the Scheduler's slot-finding algorithm.
- Modifying the challenger agent's question logic.
- Building a full-featured event bus with persistence, replay, or ordering guarantees.

## Design

### 1. Task Lifecycle Event Bus

**File:** `src/donna/tasks/events.py`

A lightweight async pub/sub for task lifecycle events. No external dependencies.

```python
class TaskEventBus:
    async def subscribe(event_type: str, callback: Callable) -> None
    async def emit(event_type: str, task: TaskRow, **context) -> None
```

**Event types:**

| Event | Emitted by | Payload |
|---|---|---|
| `task_created` | `Database.create_task()` | task row + optional context kwargs |
| `task_state_changed` | `Database.transition_task_state()` | task row, old_status, new_status |
| `challenger_resolved` | `Database.transition_task_state()` or Discord bot after challenger thread closes | task row |

**Emit from the Database layer.** `Database.__init__()` accepts an optional
`event_bus: TaskEventBus | None`. When present, `create_task()` emits
`task_created` after the INSERT commits, and `transition_task_state()` emits
`task_state_changed` after the UPDATE commits. Callers can pass extra context
kwargs through `create_task()` (e.g., `source_channel="discord"`) which are
forwarded to subscribers without the Database needing to understand them.

Error handling: subscriber exceptions are logged and swallowed — a failing
subscriber must not break the caller's flow. Each subscriber runs in its own
try/except.

### 2. Auto-Scheduler Subscriber

**File:** `src/donna/scheduling/auto_scheduler.py`

Subscribes to `task_created` and `challenger_resolved`. Decides whether to
schedule immediately or defer.

**Decision flow on `task_created`:**

1. Is a challenger dispatch pending for this task? → **Defer.** The challenger
   will ask follow-up questions. Scheduling happens on `challenger_resolved`.
2. Is the task already SCHEDULED? → **Skip** (idempotent guard).
3. Schedule the task:
   - **Calendar available:** Call `Scheduler.schedule_task(task, db, client, calendar_id)`.
     This finds a slot, creates a Google Calendar event, transitions to
     SCHEDULED, and updates the DB.
   - **Calendar unavailable (fallback):** Call `Scheduler.find_next_slot(task, [])`
     with an empty event list to find a valid time window. Set `scheduled_start`
     directly on the task, transition to SCHEDULED. No calendar event created.
     Log `auto_scheduler_fallback_mode`.
4. Post confirmation to Discord via NotificationService.

**On `challenger_resolved`:** Same scheduling flow. The task may now have
updated fields (description, estimated_duration) from the challenger Q&A.

**Detecting challenger pending:** The Discord bot's `_run_challenger()` creates
a thread and stores `task_id → thread_id` in `_challenger_threads`. The
auto-scheduler checks whether the task has a pending challenger thread by
accepting a `challenger_pending: bool` context kwarg from the `task_created`
event, set by the Discord bot. Non-Discord creation paths (API, SMS, agents)
do not pass this kwarg, so it defaults to `False` and those tasks schedule
immediately without a challenger gate.

**Class shape:**

```python
class AutoScheduler:
    def __init__(
        self,
        scheduler: Scheduler,
        db: Database,
        calendar_client: GoogleCalendarClient | None,
        calendar_id: str,
        notification_service: NotificationService | None,
    ) -> None: ...

    async def on_task_created(self, task: TaskRow, **context) -> None: ...
    async def on_challenger_resolved(self, task: TaskRow, **context) -> None: ...
```

### 3. NotificationTasks Wiring

Wire all background notification/scheduling loops through the `NotificationTasks`
dataclass and `run_server()`, replacing the standalone `ctx.tasks.append()`
pattern used for the morning digest.

**Components to wire:**

| Component | Status today | Change |
|---|---|---|
| `MorningDigest` | Wired standalone via `ctx.tasks` | Move into `NotificationTasks` |
| `ReminderScheduler` | Exists, never instantiated | Construct and add to `NotificationTasks` |
| `OverdueDetector` | Exists, never instantiated | Construct and add to `NotificationTasks` |
| `WeeklyPlanner` | Exists, never instantiated | Construct and add to `NotificationTasks` |

**Wiring location:** A new `_build_notification_tasks()` helper in
`cli_wiring.py` that constructs all components and returns a
`NotificationTasks` instance. Called from `build_startup_context()` or from
`_run_orchestrator()` in `cli.py`.

**Dependencies:** All components need `db`, `notification_service`, `user_id`,
and `router` from `ctx`. The scheduler-aware ones also need the `Scheduler`
instance and optional `calendar_client` / `calendar_id`. These degrade
gracefully when calendar is `None`.

**`run_server()` change:** Pass `notification_tasks=notification_tasks` to the
existing `run_server()` call in `cli.py`. The server already has the loop logic
at lines 260-335 — it just needs the populated dataclass.

**Remove standalone digest wiring:** Delete `ctx.tasks.append(asyncio.create_task(digest.run()))` from `_start_morning_digest()` — the digest now starts via `run_server()`.

### 4. project_root Fix

**Already applied** in `cli_wiring.py:982`. The fix:

```python
_source_root = Path(__file__).resolve().parents[2]
if (_source_root / "prompts").is_dir():
    project_root = _source_root
else:
    project_root = Path(os.environ.get("DONNA_PROJECT_ROOT", "/app"))
```

This handles both dev (running from source, `parents[2]` is the repo root) and
Docker (pip-installed, falls back to `/app` where the Dockerfile copies project
files).

### 5. challenger_resolved Event

The Discord bot's `_handle_challenger_reply()` currently appends the user's
answer to the task and re-dispatches the challenger. When the challenger is
satisfied (no more questions), the thread is cleaned up and
`_challenger_threads.pop(thread_id)` is called.

At that point, emit `challenger_resolved` so the auto-scheduler picks it up.
Since events emit from the Database layer, this can be done by having the
challenger's final resolution update the task (e.g., clearing a
`challenger_pending` flag), which triggers `task_state_changed` — or by having
the Discord bot emit `challenger_resolved` directly on the event bus as a
special case (the Database layer doesn't know about challenger semantics).

**Recommendation:** Emit `challenger_resolved` from the Discord bot's
`_handle_challenger_reply()` when the challenger reports no further questions.
This is the one event that lives outside the Database layer because it's a
Discord-specific workflow. The event bus is available on the Database instance
(`self._database._event_bus`) or injected into the Discord bot constructor.

## File Changes Summary

### New files:
- `src/donna/tasks/events.py` — TaskEventBus (~50 lines)
- `src/donna/scheduling/auto_scheduler.py` — AutoScheduler (~100 lines)

### Modified files:
- `src/donna/tasks/database.py` — Add optional `event_bus` param, emit events from `create_task()` and `transition_task_state()`
- `src/donna/cli_wiring.py` — Construct event bus, Scheduler, AutoScheduler, all notification components; build NotificationTasks; remove standalone digest wiring; project_root fix (done)
- `src/donna/cli.py` — Pass `notification_tasks` to `run_server()`
- `src/donna/integrations/discord_bot.py` — Emit `challenger_resolved` from `_handle_challenger_reply()`; accept event_bus in constructor

### Unchanged:
- `Scheduler`, `ReminderScheduler`, `OverdueDetector`, `MorningDigest`, `WeeklyPlanner` — all remain as-is
- `config/task_states.yaml`, `config/calendar.yaml` — no changes

## Testing

- Unit test `TaskEventBus`: subscribe, emit, error isolation.
- Unit test `AutoScheduler.on_task_created`: with calendar, without calendar (fallback), with challenger pending (deferred), idempotent skip.
- Integration test: create a task via the Database, verify it transitions to SCHEDULED.
- Integration test: verify `NotificationTasks` components start via `run_server()`.
