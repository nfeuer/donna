# Task Scheduling Flows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the existing scheduling, reminder, overdue, and digest components so tasks auto-schedule on creation and all background notification loops run at boot via `NotificationTasks`.

**Architecture:** A lightweight `TaskEventBus` emits lifecycle events from the Database layer. An `AutoScheduler` subscribes to `task_created` and `challenger_resolved` events, calling the existing `Scheduler` to find slots and transition tasks to SCHEDULED. All background loops (digest, reminders, overdue, weekly planner) are constructed in a `_build_notification_tasks()` helper and started via `run_server()`.

**Tech Stack:** Python 3.12, asyncio, aiosqlite, structlog, pytest

**Spec:** `docs/superpowers/specs/2026-05-11-task-scheduling-flows-design.md`

---

### Task 1: TaskEventBus

**Files:**
- Create: `src/donna/tasks/events.py`
- Create: `tests/unit/test_task_event_bus.py`

- [ ] **Step 1: Write failing tests for TaskEventBus**

```python
# tests/unit/test_task_event_bus.py
"""Unit tests for the task lifecycle event bus."""

from __future__ import annotations

import asyncio

import pytest

from donna.tasks.events import TaskEventBus


@pytest.fixture
def bus() -> TaskEventBus:
    return TaskEventBus()


@pytest.mark.asyncio
async def test_subscribe_and_emit(bus: TaskEventBus) -> None:
    received: list[dict] = []

    async def handler(task, **ctx):
        received.append({"task": task, **ctx})

    bus.subscribe("task_created", handler)
    await bus.emit("task_created", task="fake-task", source="discord")

    assert len(received) == 1
    assert received[0]["task"] == "fake-task"
    assert received[0]["source"] == "discord"


@pytest.mark.asyncio
async def test_emit_no_subscribers(bus: TaskEventBus) -> None:
    # Should not raise
    await bus.emit("task_created", task="fake-task")


@pytest.mark.asyncio
async def test_multiple_subscribers(bus: TaskEventBus) -> None:
    calls: list[str] = []

    async def handler_a(task, **ctx):
        calls.append("a")

    async def handler_b(task, **ctx):
        calls.append("b")

    bus.subscribe("task_created", handler_a)
    bus.subscribe("task_created", handler_b)
    await bus.emit("task_created", task="t")

    assert calls == ["a", "b"]


@pytest.mark.asyncio
async def test_subscriber_error_is_isolated(bus: TaskEventBus) -> None:
    calls: list[str] = []

    async def bad_handler(task, **ctx):
        raise RuntimeError("boom")

    async def good_handler(task, **ctx):
        calls.append("ok")

    bus.subscribe("task_created", bad_handler)
    bus.subscribe("task_created", good_handler)
    await bus.emit("task_created", task="t")

    assert calls == ["ok"]


@pytest.mark.asyncio
async def test_different_event_types(bus: TaskEventBus) -> None:
    created: list[str] = []
    changed: list[str] = []

    async def on_created(task, **ctx):
        created.append(task)

    async def on_changed(task, **ctx):
        changed.append(task)

    bus.subscribe("task_created", on_created)
    bus.subscribe("task_state_changed", on_changed)

    await bus.emit("task_created", task="t1")
    await bus.emit("task_state_changed", task="t2", old_status="backlog", new_status="scheduled")

    assert created == ["t1"]
    assert changed == ["t2"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_task_event_bus.py -v`
Expected: ImportError — `donna.tasks.events` does not exist yet.

- [ ] **Step 3: Implement TaskEventBus**

```python
# src/donna/tasks/events.py
"""Lightweight async pub/sub for task lifecycle events.

Subscribers receive (task, **context) and must be async callables.
Exceptions in subscribers are logged and swallowed — a failing
subscriber must never break the caller's flow.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Coroutine
from typing import Any

import structlog

logger = structlog.get_logger()

Callback = Callable[..., Coroutine[Any, Any, None]]


class TaskEventBus:
    """In-process async event bus for task lifecycle events."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callback]] = defaultdict(list)

    def subscribe(self, event_type: str, callback: Callback) -> None:
        self._subscribers[event_type].append(callback)

    async def emit(self, event_type: str, *, task: Any, **context: Any) -> None:
        for callback in self._subscribers.get(event_type, []):
            try:
                await callback(task, **context)
            except Exception:
                logger.exception(
                    "event_subscriber_failed",
                    event_type=event_type,
                    subscriber=getattr(callback, "__qualname__", str(callback)),
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_task_event_bus.py -v`
Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/donna/tasks/events.py tests/unit/test_task_event_bus.py
git commit -m "feat: add TaskEventBus for task lifecycle events

Lightweight async pub/sub. Subscribers are isolated — exceptions are
logged and swallowed. Supports task_created, task_state_changed, and
challenger_resolved event types.

Ref: §4.3 task state machine, scheduling flows spec."
```

---

### Task 2: Wire EventBus into Database

**Files:**
- Modify: `src/donna/tasks/database.py:189-196` (constructor), `327-416` (create_task), `516-551` (transition_task_state)
- Create: `tests/unit/test_database_events.py`

- [ ] **Step 1: Write failing tests for Database event emission**

```python
# tests/unit/test_database_events.py
"""Tests that Database emits events via TaskEventBus."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from donna.tasks.database import Database
from donna.tasks.db_models import InputChannel, TaskDomain, TaskStatus
from donna.tasks.events import TaskEventBus
from donna.tasks.state_machine import StateMachine


@pytest.fixture
def state_machine() -> StateMachine:
    import yaml
    config_path = Path(__file__).resolve().parents[2] / "config" / "task_states.yaml"
    with open(config_path) as f:
        return StateMachine(yaml.safe_load(f))


@pytest.fixture
async def db(tmp_path: Path, state_machine: StateMachine) -> Database:
    db_path = tmp_path / "test.db"
    database = Database(db_path, state_machine)
    await database.connect()
    # Create tasks table for testing
    conn = database.connection
    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            domain TEXT DEFAULT 'personal',
            priority INTEGER DEFAULT 2,
            status TEXT DEFAULT 'backlog',
            estimated_duration INTEGER,
            deadline TEXT,
            deadline_type TEXT DEFAULT 'none',
            scheduled_start TEXT,
            actual_start TEXT,
            completed_at TEXT,
            recurrence TEXT,
            dependencies TEXT,
            parent_task TEXT,
            prep_work_flag INTEGER DEFAULT 0,
            prep_work_instructions TEXT,
            agent_eligible INTEGER DEFAULT 0,
            assigned_agent TEXT,
            agent_status TEXT,
            tags TEXT,
            notes TEXT,
            reschedule_count INTEGER DEFAULT 0,
            created_at TEXT,
            created_via TEXT DEFAULT 'discord',
            estimated_cost REAL,
            calendar_event_id TEXT,
            donna_managed INTEGER DEFAULT 0,
            nudge_count INTEGER DEFAULT 0,
            quality_score REAL,
            capability_name TEXT,
            inputs_json TEXT
        )
    """)
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_create_task_emits_event(db: Database) -> None:
    bus = TaskEventBus()
    db.set_event_bus(bus)

    received: list[dict] = []

    async def handler(task, **ctx):
        received.append({"task": task, **ctx})

    bus.subscribe("task_created", handler)

    task = await db.create_task(
        user_id="nick",
        title="Call the mechanic",
        domain=TaskDomain.PERSONAL,
    )

    assert len(received) == 1
    assert received[0]["task"].id == task.id
    assert received[0]["task"].title == "Call the mechanic"


@pytest.mark.asyncio
async def test_create_task_no_bus(db: Database) -> None:
    # No event bus set — should not raise
    task = await db.create_task(
        user_id="nick",
        title="No bus task",
    )
    assert task.title == "No bus task"


@pytest.mark.asyncio
async def test_transition_emits_state_changed(db: Database) -> None:
    bus = TaskEventBus()
    db.set_event_bus(bus)

    received: list[dict] = []

    async def handler(task, **ctx):
        received.append({"task": task, **ctx})

    bus.subscribe("task_state_changed", handler)

    task = await db.create_task(
        user_id="nick",
        title="Schedule me",
    )
    await db.transition_task_state(task.id, TaskStatus.SCHEDULED)

    assert len(received) == 1
    assert received[0]["old_status"] == "backlog"
    assert received[0]["new_status"] == "scheduled"
    assert received[0]["task"].id == task.id
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_database_events.py -v`
Expected: AttributeError — `set_event_bus` does not exist.

- [ ] **Step 3: Add event_bus support to Database**

In `src/donna/tasks/database.py`, add `set_event_bus` method and a private `_event_bus` field. Then add event emission to `create_task` and `transition_task_state`.

Add `_event_bus` field to `__init__`:

```python
# In Database.__init__, after self._memory_observer = memory_observer
        self._event_bus: TaskEventBus | None = None
```

Add the `set_event_bus` method right after `set_memory_observer`:

```python
    def set_event_bus(self, bus: Any | None) -> None:
        """Attach the task lifecycle event bus post-construction."""
        self._event_bus = bus
```

Add a private helper for emitting events (after `_fire_memory_observer`):

```python
    async def _emit_event(self, event_type: str, **kwargs: Any) -> None:
        """Emit a task lifecycle event if the bus is wired."""
        if self._event_bus is None:
            return
        try:
            await self._event_bus.emit(event_type, **kwargs)
        except Exception as exc:
            logger.warning("event_bus_emit_failed", event_type=event_type, reason=str(exc))
```

In `create_task`, after the memory observer fire (line ~415, after `await self._fire_memory_observer(...)`), add:

```python
        if task_row is not None:
            await self._emit_event("task_created", task=task_row)
```

In `transition_task_state`, after the commit and log (line ~549, before `return side_effects`), add:

```python
        updated_task = await self.get_task(task_id)
        if updated_task is not None:
            await self._emit_event(
                "task_state_changed",
                task=updated_task,
                old_status=task.status,
                new_status=new_status.value,
                side_effects=side_effects,
            )
```

Update the `TYPE_CHECKING` import block at the top of `database.py` to include the event bus type:

```python
if TYPE_CHECKING:
    from donna.integrations.supabase_sync import SupabaseSync
    from donna.tasks.events import TaskEventBus
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_database_events.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Run existing database tests to check for regressions**

Run: `python3 -m pytest tests/unit/ -k "database or task" --ignore=tests/unit/memory -v -q`
Expected: All existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/donna/tasks/database.py tests/unit/test_database_events.py
git commit -m "feat: emit task lifecycle events from Database layer

Database.set_event_bus() wires a TaskEventBus post-construction.
create_task() emits task_created; transition_task_state() emits
task_state_changed with old/new status. Failures are swallowed.

Ref: scheduling flows spec §1."
```

---

### Task 3: AutoScheduler

**Files:**
- Create: `src/donna/scheduling/auto_scheduler.py`
- Create: `tests/unit/test_auto_scheduler.py`

- [ ] **Step 1: Write failing tests for AutoScheduler**

```python
# tests/unit/test_auto_scheduler.py
"""Unit tests for the AutoScheduler event subscriber."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from donna.scheduling.auto_scheduler import AutoScheduler
from donna.scheduling.scheduler import ScheduledSlot
from donna.tasks.database import TaskRow


def _make_task(
    task_id: str = "task-001",
    status: str = "backlog",
    estimated_duration: int | None = 60,
    domain: str = "personal",
    priority: int = 2,
) -> TaskRow:
    return TaskRow(
        id=task_id,
        user_id="nick",
        title="Test task",
        description=None,
        domain=domain,
        priority=priority,
        status=status,
        estimated_duration=estimated_duration,
        deadline=None,
        deadline_type="none",
        scheduled_start=None,
        actual_start=None,
        completed_at=None,
        recurrence=None,
        dependencies=None,
        parent_task=None,
        prep_work_flag=False,
        prep_work_instructions=None,
        agent_eligible=False,
        assigned_agent=None,
        agent_status=None,
        tags=None,
        notes=None,
        reschedule_count=0,
        created_at="2026-05-11T09:00:00",
        created_via="discord",
        estimated_cost=None,
        calendar_event_id=None,
        donna_managed=False,
        nudge_count=0,
        quality_score=None,
    )


@pytest.fixture
def scheduler_mock() -> MagicMock:
    mock = MagicMock()
    slot = ScheduledSlot(
        start=datetime(2026, 5, 12, 9, 0, tzinfo=UTC),
        end=datetime(2026, 5, 12, 10, 0, tzinfo=UTC),
    )
    mock.schedule_task = AsyncMock(return_value=slot)
    mock.find_next_slot = MagicMock(return_value=slot)
    return mock


@pytest.fixture
def db_mock() -> MagicMock:
    mock = MagicMock()
    mock.update_task = AsyncMock()
    mock.transition_task_state = AsyncMock(return_value=[])
    mock.get_task = AsyncMock(return_value=_make_task())
    return mock


@pytest.fixture
def notification_mock() -> MagicMock:
    mock = MagicMock()
    mock.dispatch = AsyncMock(return_value=True)
    return mock


@pytest.fixture
def auto_scheduler(
    scheduler_mock: MagicMock,
    db_mock: MagicMock,
    notification_mock: MagicMock,
) -> AutoScheduler:
    return AutoScheduler(
        scheduler=scheduler_mock,
        db=db_mock,
        calendar_client=None,
        calendar_id="primary",
        notification_service=notification_mock,
    )


@pytest.mark.asyncio
async def test_on_task_created_schedules_without_calendar(
    auto_scheduler: AutoScheduler,
    scheduler_mock: MagicMock,
    db_mock: MagicMock,
) -> None:
    task = _make_task()
    await auto_scheduler.on_task_created(task)

    scheduler_mock.find_next_slot.assert_called_once()
    db_mock.update_task.assert_called_once()
    db_mock.transition_task_state.assert_called_once()


@pytest.mark.asyncio
async def test_on_task_created_uses_calendar_when_available(
    scheduler_mock: MagicMock,
    db_mock: MagicMock,
    notification_mock: MagicMock,
) -> None:
    calendar_client = MagicMock()
    auto = AutoScheduler(
        scheduler=scheduler_mock,
        db=db_mock,
        calendar_client=calendar_client,
        calendar_id="primary",
        notification_service=notification_mock,
    )
    task = _make_task()
    await auto.on_task_created(task)

    scheduler_mock.schedule_task.assert_called_once_with(
        task, db_mock, calendar_client, "primary"
    )


@pytest.mark.asyncio
async def test_on_task_created_skips_when_challenger_pending(
    auto_scheduler: AutoScheduler,
    scheduler_mock: MagicMock,
) -> None:
    task = _make_task()
    await auto_scheduler.on_task_created(task, challenger_pending=True)

    scheduler_mock.find_next_slot.assert_not_called()
    scheduler_mock.schedule_task.assert_not_called()


@pytest.mark.asyncio
async def test_on_task_created_skips_already_scheduled(
    auto_scheduler: AutoScheduler,
    scheduler_mock: MagicMock,
) -> None:
    task = _make_task(status="scheduled")
    await auto_scheduler.on_task_created(task)

    scheduler_mock.find_next_slot.assert_not_called()
    scheduler_mock.schedule_task.assert_not_called()


@pytest.mark.asyncio
async def test_on_challenger_resolved_schedules(
    auto_scheduler: AutoScheduler,
    scheduler_mock: MagicMock,
    db_mock: MagicMock,
) -> None:
    task = _make_task()
    db_mock.get_task = AsyncMock(return_value=task)
    await auto_scheduler.on_challenger_resolved(task)

    scheduler_mock.find_next_slot.assert_called_once()


@pytest.mark.asyncio
async def test_on_task_created_sends_notification(
    auto_scheduler: AutoScheduler,
    notification_mock: MagicMock,
) -> None:
    task = _make_task()
    await auto_scheduler.on_task_created(task)

    notification_mock.dispatch.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_auto_scheduler.py -v`
Expected: ImportError — `donna.scheduling.auto_scheduler` does not exist yet.

- [ ] **Step 3: Implement AutoScheduler**

```python
# src/donna/scheduling/auto_scheduler.py
"""Auto-scheduler — subscribes to task lifecycle events and schedules tasks.

On task_created: if no challenger is pending, schedule immediately.
On challenger_resolved: schedule the task after Q&A is complete.

Calendar fallback: when GoogleCalendarClient is unavailable, uses
Scheduler.find_next_slot() with an empty event list and sets
scheduled_start directly without creating a calendar event.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from donna.notifications.service import CHANNEL_TASKS, NOTIF_REMINDER
from donna.scheduling.scheduler import NoSlotFoundError, Scheduler
from donna.tasks.database import Database, TaskRow
from donna.tasks.db_models import TaskStatus

if TYPE_CHECKING:
    from donna.integrations.calendar import GoogleCalendarClient
    from donna.notifications.service import NotificationService

logger = structlog.get_logger()


class AutoScheduler:
    """Event-driven auto-scheduler for newly created tasks."""

    def __init__(
        self,
        scheduler: Scheduler,
        db: Database,
        calendar_client: GoogleCalendarClient | None,
        calendar_id: str,
        notification_service: NotificationService | None,
    ) -> None:
        self._scheduler = scheduler
        self._db = db
        self._calendar_client = calendar_client
        self._calendar_id = calendar_id
        self._notification_service = notification_service

    async def on_task_created(self, task: TaskRow, **context: Any) -> None:
        if context.get("challenger_pending", False):
            logger.info("auto_scheduler_deferred_challenger", task_id=task.id)
            return
        await self._schedule(task)

    async def on_challenger_resolved(self, task: TaskRow, **context: Any) -> None:
        fresh = await self._db.get_task(task.id)
        if fresh is None:
            return
        await self._schedule(fresh)

    async def _schedule(self, task: TaskRow) -> None:
        if task.status != TaskStatus.BACKLOG.value:
            logger.info("auto_scheduler_skip_not_backlog", task_id=task.id, status=task.status)
            return

        try:
            if self._calendar_client is not None:
                slot = await self._scheduler.schedule_task(
                    task, self._db, self._calendar_client, self._calendar_id
                )
            else:
                slot = self._scheduler.find_next_slot(task, [])
                await self._db.transition_task_state(task.id, TaskStatus.SCHEDULED)
                await self._db.update_task(
                    task.id,
                    scheduled_start=slot.start,
                    donna_managed=True,
                )
                logger.info("auto_scheduler_fallback_mode", task_id=task.id)
        except NoSlotFoundError:
            logger.warning("auto_scheduler_no_slot", task_id=task.id)
            return
        except Exception:
            logger.exception("auto_scheduler_failed", task_id=task.id)
            return

        logger.info(
            "auto_scheduler_scheduled",
            task_id=task.id,
            slot_start=slot.start.isoformat(),
            slot_end=slot.end.isoformat(),
        )

        if self._notification_service is not None:
            start_fmt = slot.start.strftime("%A %-I:%M %p")
            end_fmt = slot.end.strftime("%-I:%M %p")
            await self._notification_service.dispatch(
                notification_type=NOTIF_REMINDER,
                content=f"Scheduled '{task.title}' for {start_fmt}–{end_fmt}.",
                channel=CHANNEL_TASKS,
                priority=task.priority or 2,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_auto_scheduler.py -v`
Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/donna/scheduling/auto_scheduler.py tests/unit/test_auto_scheduler.py
git commit -m "feat: add AutoScheduler event subscriber

Subscribes to task_created and challenger_resolved. Uses Google
Calendar when available, falls back to find_next_slot with empty
event list. Sends Discord confirmation after scheduling.

Ref: scheduling flows spec §2."
```

---

### Task 4: Emit challenger_resolved from Discord bot

**Files:**
- Modify: `src/donna/integrations/discord_bot.py:59-103` (constructor), `897-926` (_handle_challenger_reply)

- [ ] **Step 1: Add event_bus parameter to DonnaBot constructor**

In `src/donna/integrations/discord_bot.py`, add `event_bus` to the constructor. After `self._automation_repo = automation_repo` (line 91), add:

```python
        self._event_bus = event_bus
```

And add the parameter to the `__init__` signature, after `automation_repo`:

```python
        event_bus: Any | None = None,
```

- [ ] **Step 2: Emit challenger_resolved in _handle_challenger_reply**

In `_handle_challenger_reply`, after line 926 (`self._challenger_threads.pop(message.channel.id, None)`), add:

```python
        if self._event_bus is not None:
            updated_task = await self._database.get_task(task_id)
            if updated_task is not None:
                try:
                    await self._event_bus.emit(
                        "challenger_resolved", task=updated_task
                    )
                except Exception:
                    log.exception("challenger_resolved_emit_failed", task_id=task_id)
```

- [ ] **Step 3: Pass challenger_pending=True context in task creation**

In the `on_message` handler where `_run_challenger` is called (around line 394-396), the Discord bot should signal to the event bus that this task has a challenger pending. Since the event is emitted by the Database layer inside `create_task()`, and the Database doesn't know about challengers, we need to set a flag *before* creating the task that the `task_created` event subscriber can read.

The cleanest approach: after the task is created and the event has already fired (line ~392), check if the challenger will be dispatched. If it will, the auto-scheduler has already been called by the `task_created` event. We need to pass the context through the Database layer.

Instead, modify the `on_message` handler to pass `challenger_pending` as an extra context kwarg through `create_task`. Add a `**event_context` parameter to `Database.create_task()` that gets forwarded to the event bus:

In `src/donna/tasks/database.py`, update `create_task` signature to accept extra context:

```python
    async def create_task(
        self,
        # ... existing params ...
        inputs: dict[str, Any] | None = None,
        **event_context: Any,
    ) -> TaskRow:
```

And update the `_emit_event` call in `create_task`:

```python
        if task_row is not None:
            await self._emit_event("task_created", task=task_row, **event_context)
```

Then in `discord_bot.py`, update the `create_task` call (around line 366-379) to pass the context. After the existing `create_task` call, look at whether a dispatcher exists. We need to pass `challenger_pending=True` to create_task when the dispatcher is available:

```python
            task = await self._database.create_task(
                # ... existing params ...
                created_via=InputChannel.DISCORD,
                challenger_pending=self._dispatcher is not None,
            )
```

- [ ] **Step 4: Run existing discord bot tests**

Run: `python3 -m pytest tests/ -k "discord" --ignore=tests/unit/memory --ignore=tests/integration -v -q`
Expected: All pass (the new parameter has a default of None so existing tests aren't affected).

- [ ] **Step 5: Commit**

```bash
git add src/donna/integrations/discord_bot.py src/donna/tasks/database.py
git commit -m "feat: emit challenger_resolved from Discord bot

DonnaBot accepts event_bus and emits challenger_resolved when the
challenger Q&A completes. create_task() forwards **event_context
to the event bus so callers can pass challenger_pending=True.

Ref: scheduling flows spec §5."
```

---

### Task 5: Build NotificationTasks and wire run_server()

**Files:**
- Modify: `src/donna/cli_wiring.py:664-707` (_start_morning_digest), add `_build_notification_tasks()`
- Modify: `src/donna/cli.py:193-337` (_run_orchestrator)

- [ ] **Step 1: Add _build_notification_tasks() to cli_wiring.py**

Add this function in `cli_wiring.py` after the existing `_start_morning_digest` function (around line 708). This replaces the standalone digest wiring and adds all other notification components:

```python
def _build_notification_tasks(
    ctx: StartupContext,
    *,
    calendar_client: Any | None = None,
    gmail_client: Any | None = None,
    scheduler: Any | None = None,
) -> Any | None:
    """Construct a NotificationTasks bundle for run_server().

    Returns None when the notification service is unavailable (no Discord bot).
    Components that can't be constructed are set to None — run_server() checks
    each before starting its background loop.
    """
    if ctx.notification_service is None:
        logger.info("notification_tasks_skipped_no_notification_service")
        return None

    from donna.server import NotificationTasks

    # --- Morning Digest ---
    morning_digest = None
    try:
        from donna.config import load_calendar_config, load_email_config
        from donna.notifications.digest import MorningDigest

        cal_cfg = load_calendar_config(ctx.config_dir)
        personal = cal_cfg.calendars.get("personal")
        calendar_id = personal.calendar_id if personal else "primary"

        user_email = ""
        try:
            email_cfg = load_email_config(ctx.config_dir)
            user_email = getattr(email_cfg, "user_email", "")
        except Exception:
            pass

        morning_digest = MorningDigest(
            db=ctx.db,
            service=ctx.notification_service,
            router=ctx.router,
            calendar_client=calendar_client,
            calendar_id=calendar_id,
            user_id=ctx.user_id,
            project_root=ctx.project_root,
            gmail=gmail_client,
            user_email=user_email,
            tool_request_repo=ctx.tool_request_repository,
            tz=ctx.tz,
        )
        logger.info("morning_digest_constructed")
    except Exception as exc:
        logger.warning("morning_digest_unavailable", reason=str(exc))

    # --- Reminder Scheduler ---
    reminder_scheduler = None
    try:
        from donna.notifications.reminders import ReminderScheduler

        reminder_scheduler = ReminderScheduler(
            db=ctx.db,
            service=ctx.notification_service,
            user_id=ctx.user_id,
            router=ctx.router,
        )
        logger.info("reminder_scheduler_constructed")
    except Exception as exc:
        logger.warning("reminder_scheduler_unavailable", reason=str(exc))

    # --- Overdue Detector ---
    overdue_detector = None
    if ctx.bot is not None and scheduler is not None:
        try:
            from donna.config import load_calendar_config
            from donna.notifications.overdue import OverdueDetector

            cal_cfg = load_calendar_config(ctx.config_dir)
            personal = cal_cfg.calendars.get("personal")
            calendar_id = personal.calendar_id if personal else "primary"

            overdue_detector = OverdueDetector(
                db=ctx.db,
                service=ctx.notification_service,
                bot=ctx.bot,
                scheduler=scheduler,
                calendar_id=calendar_id,
                user_id=ctx.user_id,
                router=ctx.router,
            )
            logger.info("overdue_detector_constructed")
        except Exception as exc:
            logger.warning("overdue_detector_unavailable", reason=str(exc))

    # --- Weekly Planner ---
    weekly_planner = None
    if scheduler is not None:
        try:
            from donna.config import load_calendar_config
            from donna.scheduling.priority_engine import PriorityEngine
            from donna.scheduling.priority_recalculator import PriorityRecalculator
            from donna.scheduling.weekly_planner import WeeklyPlanner

            cal_cfg = load_calendar_config(ctx.config_dir)
            personal = cal_cfg.calendars.get("personal")
            calendar_id = personal.calendar_id if personal else "primary"

            priority_engine = PriorityEngine(cal_cfg.priority)
            recalculator = PriorityRecalculator(
                db=ctx.db,
                engine=priority_engine,
                service=ctx.notification_service,
                user_id=ctx.user_id,
            )

            weekly_planner = WeeklyPlanner(
                db=ctx.db,
                scheduler=scheduler,
                recalculator=recalculator,
                service=ctx.notification_service,
                calendar_client=calendar_client,
                calendar_id=calendar_id,
                user_id=ctx.user_id,
            )
            logger.info("weekly_planner_constructed")
        except Exception as exc:
            logger.warning("weekly_planner_unavailable", reason=str(exc))

    if morning_digest is None or reminder_scheduler is None or overdue_detector is None:
        # NotificationTasks requires all three core fields. If any are missing,
        # fall back: start only what we have via ctx.tasks.
        if morning_digest is not None:
            ctx.tasks.append(asyncio.create_task(morning_digest.run(), name="morning_digest"))
        if reminder_scheduler is not None:
            ctx.tasks.append(asyncio.create_task(reminder_scheduler.run(), name="reminder_scheduler"))
        logger.warning(
            "notification_tasks_partial",
            digest=morning_digest is not None,
            reminders=reminder_scheduler is not None,
            overdue=overdue_detector is not None,
        )
        return None

    return NotificationTasks(
        reminder_scheduler=reminder_scheduler,
        overdue_detector=overdue_detector,
        morning_digest=morning_digest,
        weekly_planner=weekly_planner,
    )
```

- [ ] **Step 2: Add event_bus to StartupContext and build_startup_context()**

The event bus must be created early enough for both the DonnaBot and the auto-scheduler to use it. Construct it in `build_startup_context()`.

In `src/donna/cli_wiring.py`, add to the `StartupContext` dataclass (after `tool_gap_surfacer`, around line 903):

```python
    event_bus: Any | None = None
```

In `build_startup_context()`, after the Database is constructed and connected (find `await db.connect()`), add:

```python
    from donna.tasks.events import TaskEventBus
    event_bus = TaskEventBus()
    db.set_event_bus(event_bus)
```

Pass `event_bus=event_bus` to the `StartupContext(...)` constructor call.

In the `DonnaBot` construction (cli_wiring.py ~line 1084), pass the event bus:

```python
        bot = DonnaBot(
            input_parser=input_parser,
            database=db,
            tasks_channel_id=int(tasks_channel_id_str),
            debug_channel_id=int(debug_channel_id_str) if debug_channel_id_str else None,
            agents_channel_id=int(agents_channel_id_str) if agents_channel_id_str else None,
            guild_id=int(guild_id_str) if guild_id_str else None,
            event_bus=event_bus,
        )
```

- [ ] **Step 3: Wire AutoScheduler and NotificationTasks in cli.py**

In `src/donna/cli.py`, in `_run_orchestrator()`:

1. Remove the `_start_morning_digest` import (line 228) and its call (lines 247-249).
2. After `calendar_client` is built (line 246), add:

```python
    from donna.cli_wiring import _build_notification_tasks
    from donna.config import load_calendar_config
    from donna.scheduling.auto_scheduler import AutoScheduler
    from donna.scheduling.scheduler import Scheduler

    cal_cfg = load_calendar_config(ctx.config_dir)
    task_scheduler = Scheduler(cal_cfg)

    personal = cal_cfg.calendars.get("personal")
    calendar_id = personal.calendar_id if personal else "primary"

    auto_scheduler = AutoScheduler(
        scheduler=task_scheduler,
        db=ctx.db,
        calendar_client=calendar_client,
        calendar_id=calendar_id,
        notification_service=ctx.notification_service,
    )
    ctx.event_bus.subscribe("task_created", auto_scheduler.on_task_created)
    ctx.event_bus.subscribe("challenger_resolved", auto_scheduler.on_challenger_resolved)

    notification_tasks = _build_notification_tasks(
        ctx,
        calendar_client=calendar_client,
        gmail_client=gmail_client,
        scheduler=task_scheduler,
    )
```

3. Move the `run_server()` task creation from line 217 to after `notification_tasks` is built (just before the `asyncio.wait` block). Update it to pass `notification_tasks`:

```python
    ctx.tasks.append(asyncio.create_task(
        run_server(port=ctx.port, discord_bot=ctx.bot, notification_tasks=notification_tasks)
    ))
```

- [ ] **Step 4: Remove the standalone _start_morning_digest function**

Delete the `_start_morning_digest()` function body (cli_wiring.py lines 664-707) entirely. The digest is now constructed inside `_build_notification_tasks()`. Remove the import of `_start_morning_digest` from `cli.py`.

- [ ] **Step 5: Verify the server starts with tests**

Run: `python3 -m pytest tests/unit/ --ignore=tests/unit/memory -k "server or wiring or cli" -v -q`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/donna/cli.py src/donna/cli_wiring.py
git commit -m "feat: wire NotificationTasks, AutoScheduler, and EventBus at boot

All background loops (digest, reminders, overdue, weekly planner)
constructed via _build_notification_tasks() and started through
run_server(). EventBus and AutoScheduler wired in build_startup_context.
Standalone _start_morning_digest() removed.

Ref: scheduling flows spec §3."
```

---

### Task 6: Integration test — task creation triggers scheduling

**Files:**
- Create: `tests/integration/test_auto_scheduling_flow.py`

- [ ] **Step 1: Write integration test**

```python
# tests/integration/test_auto_scheduling_flow.py
"""Integration test: creating a task via Database triggers auto-scheduling."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from donna.config import load_calendar_config
from donna.scheduling.auto_scheduler import AutoScheduler
from donna.scheduling.scheduler import Scheduler
from donna.tasks.database import Database
from donna.tasks.db_models import TaskDomain, TaskStatus
from donna.tasks.events import TaskEventBus
from donna.tasks.state_machine import StateMachine


@pytest.fixture
def state_machine() -> StateMachine:
    config_path = Path(__file__).resolve().parents[2] / "config" / "task_states.yaml"
    with open(config_path) as f:
        return StateMachine(yaml.safe_load(f))


@pytest.fixture
def cal_config():
    config_dir = Path(__file__).resolve().parents[2] / "config"
    return load_calendar_config(config_dir)


@pytest.fixture
async def db(tmp_path: Path, state_machine: StateMachine) -> Database:
    db_path = tmp_path / "test.db"
    database = Database(db_path, state_machine)
    await database.connect()
    conn = database.connection
    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            domain TEXT DEFAULT 'personal',
            priority INTEGER DEFAULT 2,
            status TEXT DEFAULT 'backlog',
            estimated_duration INTEGER,
            deadline TEXT,
            deadline_type TEXT DEFAULT 'none',
            scheduled_start TEXT,
            actual_start TEXT,
            completed_at TEXT,
            recurrence TEXT,
            dependencies TEXT,
            parent_task TEXT,
            prep_work_flag INTEGER DEFAULT 0,
            prep_work_instructions TEXT,
            agent_eligible INTEGER DEFAULT 0,
            assigned_agent TEXT,
            agent_status TEXT,
            tags TEXT,
            notes TEXT,
            reschedule_count INTEGER DEFAULT 0,
            created_at TEXT,
            created_via TEXT DEFAULT 'discord',
            estimated_cost REAL,
            calendar_event_id TEXT,
            donna_managed INTEGER DEFAULT 0,
            nudge_count INTEGER DEFAULT 0,
            quality_score REAL,
            capability_name TEXT,
            inputs_json TEXT
        )
    """)
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_create_task_triggers_auto_schedule(db: Database, cal_config) -> None:
    bus = TaskEventBus()
    db.set_event_bus(bus)

    scheduler = Scheduler(cal_config)
    auto = AutoScheduler(
        scheduler=scheduler,
        db=db,
        calendar_client=None,
        calendar_id="primary",
        notification_service=None,
    )
    bus.subscribe("task_created", auto.on_task_created)

    task = await db.create_task(
        user_id="nick",
        title="Call the mechanic",
        domain=TaskDomain.PERSONAL,
        estimated_duration=30,
    )

    # After create, the task should have been auto-scheduled
    updated = await db.get_task(task.id)
    assert updated is not None
    assert updated.status == "scheduled"
    assert updated.scheduled_start is not None
    assert updated.donna_managed is True


@pytest.mark.asyncio
async def test_challenger_pending_defers_scheduling(db: Database, cal_config) -> None:
    bus = TaskEventBus()
    db.set_event_bus(bus)

    scheduler = Scheduler(cal_config)
    auto = AutoScheduler(
        scheduler=scheduler,
        db=db,
        calendar_client=None,
        calendar_id="primary",
        notification_service=None,
    )
    bus.subscribe("task_created", auto.on_task_created)

    task = await db.create_task(
        user_id="nick",
        title="Research new tires",
        domain=TaskDomain.PERSONAL,
        estimated_duration=30,
        challenger_pending=True,
    )

    # Task should still be in backlog — challenger is pending
    updated = await db.get_task(task.id)
    assert updated is not None
    assert updated.status == "backlog"
    assert updated.scheduled_start is None

    # Now resolve the challenger
    await auto.on_challenger_resolved(task)

    final = await db.get_task(task.id)
    assert final is not None
    assert final.status == "scheduled"
    assert final.scheduled_start is not None
```

- [ ] **Step 2: Run integration test**

Run: `python3 -m pytest tests/integration/test_auto_scheduling_flow.py -v`
Expected: All 2 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_auto_scheduling_flow.py
git commit -m "test: integration test for auto-scheduling flow

Verifies end-to-end: create_task → event_bus → AutoScheduler →
task transitions to SCHEDULED. Also tests challenger_pending deferral.

Ref: scheduling flows spec."
```

---

### Task 7: Run full test suite and fix regressions

**Files:**
- Potentially modify any file with test regressions

- [ ] **Step 1: Run the full test suite**

Run: `python3 -m pytest tests/ --ignore=tests/unit/memory --ignore=tests/integration/test_boot_gmail_wiring.py --ignore=tests/integration/test_boot_memory_wiring.py -v -q`
Expected: All tests pass. If there are failures, investigate and fix.

- [ ] **Step 2: Run type checking**

Run: `python3 -m mypy src/donna/tasks/events.py src/donna/scheduling/auto_scheduler.py --ignore-missing-imports`
Expected: No errors.

- [ ] **Step 3: Fix any regressions found in steps 1-2**

If existing tests fail because of the `**event_context` parameter added to `create_task()`, ensure the signature change is backwards-compatible (kwargs are always optional).

If `StartupContext` tests fail because of the new `event_bus` field, ensure it has a default value of `None`.

- [ ] **Step 4: Commit fixes if any**

```bash
git add -u
git commit -m "fix: resolve test regressions from scheduling flow wiring"
```

---

### Summary

| Task | Component | New files | Modified files |
|------|-----------|-----------|----------------|
| 1 | TaskEventBus | `events.py`, `test_task_event_bus.py` | — |
| 2 | Database events | `test_database_events.py` | `database.py` |
| 3 | AutoScheduler | `auto_scheduler.py`, `test_auto_scheduler.py` | — |
| 4 | Challenger resolved | — | `discord_bot.py`, `database.py` |
| 5 | NotificationTasks wiring | — | `cli_wiring.py`, `cli.py` |
| 6 | Integration test | `test_auto_scheduling_flow.py` | — |
| 7 | Regression sweep | — | as needed |
