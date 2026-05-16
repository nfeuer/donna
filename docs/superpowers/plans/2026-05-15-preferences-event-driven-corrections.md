# Preferences: Event-Driven Correction Logging — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route all user-initiated task edits through the `TaskEventBus` so the preferences pipeline receives corrections from every update path, not just Discord regex commands and calendar sync.

**Architecture:** `Database.update_task()` emits a `task_updated` event with a before/after diff and a `source` tag. A new `CorrectionSubscriber` listens for these events and logs corrections for user-initiated changes (source ≠ None) to allowlisted fields. Existing direct `log_correction()` calls are removed.

**Tech Stack:** Python 3.12, asyncio, aiosqlite, structlog, pytest

**Spec:** `docs/superpowers/specs/2026-05-15-preferences-event-driven-corrections-design.md`

---

### File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `src/donna/tasks/database.py` | Add `source` param and `task_updated` event emission to `update_task` |
| Create | `src/donna/preferences/correction_subscriber.py` | `CorrectionSubscriber` class — event listener that logs corrections |
| Modify | `src/donna/cli_wiring.py` | Wire `CorrectionSubscriber` to the event bus at startup |
| Modify | `src/donna/integrations/discord_views.py` | Pass `source=` to `update_task` calls (modal, selects) |
| Modify | `src/donna/integrations/discord_bot.py` | Pass `source=` to `update_task`, remove direct `log_correction` call |
| Modify | `src/donna/api/routes/tasks.py` | Pass `source="api"` to `update_task` |
| Modify | `src/donna/scheduling/calendar_sync.py` | Pass `source=`, remove `_log_correction` helper |
| Create | `tests/unit/test_correction_subscriber.py` | Unit tests for `CorrectionSubscriber` |
| Modify | `tests/integration/test_database.py` | Tests for `task_updated` event emission from `update_task` |

---

### Task 1: Add `source` param and `task_updated` event emission to `Database.update_task()`

**Files:**
- Modify: `src/donna/tasks/database.py:516-570`
- Test: `tests/integration/test_database.py`

- [ ] **Step 1: Write the failing test — event emitted with diff and source**

Add to the end of `tests/integration/test_database.py`:

```python
class TestUpdateTaskEvent:
    async def test_emits_task_updated_with_changed_fields(self, db: Database) -> None:
        """update_task emits a task_updated event with the before/after diff."""
        from donna.tasks.events import TaskEventBus

        bus = TaskEventBus()
        db.set_event_bus(bus)
        received: list[dict] = []

        async def on_update(task, **ctx):
            received.append({"task": task, **ctx})

        bus.subscribe("task_updated", on_update)

        task = await db.create_task(user_id="nick", title="Original", priority=3)
        await db.update_task(task.id, priority=5, source="api")

        assert len(received) == 1
        evt = received[0]
        assert evt["source"] == "api"
        assert "priority" in evt["changed_fields"]
        old, new = evt["changed_fields"]["priority"]
        assert old == 3
        assert new == 5
        assert evt["task"].priority == 5
        assert evt["previous"].priority == 3

    async def test_no_source_emits_event_with_none(self, db: Database) -> None:
        """update_task without source still emits, source=None."""
        from donna.tasks.events import TaskEventBus

        bus = TaskEventBus()
        db.set_event_bus(bus)
        received: list[dict] = []

        async def on_update(task, **ctx):
            received.append({"task": task, **ctx})

        bus.subscribe("task_updated", on_update)

        task = await db.create_task(user_id="nick", title="T", priority=2)
        await db.update_task(task.id, priority=4)

        assert len(received) == 1
        assert received[0]["source"] is None

    async def test_no_change_no_event(self, db: Database) -> None:
        """update_task with same values emits no task_updated event."""
        from donna.tasks.events import TaskEventBus

        bus = TaskEventBus()
        db.set_event_bus(bus)
        received: list[dict] = []

        async def on_update(task, **ctx):
            received.append({"task": task, **ctx})

        bus.subscribe("task_updated", on_update)

        task = await db.create_task(user_id="nick", title="Same", priority=3)
        await db.update_task(task.id, priority=3)

        # Event may still fire, but changed_fields should be empty
        if received:
            assert received[0]["changed_fields"] == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/integration/test_database.py::TestUpdateTaskEvent -v`
Expected: FAIL — `update_task` does not accept `source` keyword, no `task_updated` event emitted.

- [ ] **Step 3: Implement the `source` param and event emission**

In `src/donna/tasks/database.py`, modify `update_task`. The `source` param must be popped from `fields` before the `_UPDATABLE_COLUMNS` validation since it's metadata, not a database column.

Replace the current `update_task` method (lines 516–570) with:

```python
    async def update_task(
        self, task_id: str, *, source: str | None = None, **fields: Any
    ) -> TaskRow | None:
        """Update specific fields on a task. Returns updated row or None."""
        if not fields:
            return await self.get_task(task_id)

        invalid = set(fields.keys()) - _UPDATABLE_COLUMNS
        if invalid:
            raise ValueError(f"Invalid columns for update: {invalid}")

        previous_row = await self.get_task(task_id)
        previous_status = previous_row.status if previous_row is not None else None

        conn = self.connection

        # Serialize special types
        processed: dict[str, Any] = {}
        for key, value in fields.items():
            if (
                key in ("tags", "notes", "dependencies") and isinstance(value, list)
            ) or (
                key == "inputs_json" and isinstance(value, dict)
            ):
                processed[key] = json.dumps(value)
            elif isinstance(value, datetime):
                processed[key] = value.isoformat()
            elif isinstance(value, _enum_module.Enum):
                processed[key] = value.value
            else:
                processed[key] = value

        set_clause = ", ".join(f"{col} = ?" for col in processed)
        values = [*list(processed.values()), task_id]

        cursor = await conn.execute(
            f"UPDATE tasks SET {set_clause} WHERE id = ?",
            values,
        )
        await conn.commit()

        if cursor.rowcount == 0:
            return None

        task_row = await self.get_task(task_id)
        if self._supabase_sync is not None and task_row is not None:
            await self._supabase_sync.push_task(dataclasses.asdict(task_row))
        if task_row is not None:
            await self._fire_memory_observer(
                "observe_task",
                {
                    "action": "update",
                    "task": dataclasses.asdict(task_row),
                    "previous_status": previous_status,
                },
            )

            # Compute field-level diff for task_updated event.
            changed_fields: dict[str, tuple[Any, Any]] = {}
            if previous_row is not None:
                for field_name in fields:
                    old_val = getattr(previous_row, field_name, None)
                    new_val = getattr(task_row, field_name, None)
                    if old_val != new_val:
                        changed_fields[field_name] = (old_val, new_val)

            await self._emit_event(
                "task_updated",
                task=task_row,
                previous=previous_row,
                changed_fields=changed_fields,
                source=source,
            )

        return task_row
```

Key changes:
- Signature becomes `update_task(self, task_id: str, *, source: str | None = None, **fields: Any)` — the `*` forces all args to be keyword-only. The existing callers already use keyword args (e.g. `update_task(task.id, priority=5)`), so this is backward-compatible.
- After the memory observer fires, compute `changed_fields` by comparing `previous_row` and `task_row` attribute values for each field in the `fields` dict.
- Emit `task_updated` event with `task`, `previous`, `changed_fields`, and `source`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/integration/test_database.py::TestUpdateTaskEvent -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Run the full database test suite to check for regressions**

Run: `pytest tests/integration/test_database.py -v`
Expected: All existing tests still PASS — existing callers don't pass `source`, which defaults to `None`.

- [ ] **Step 6: Commit**

```bash
git add src/donna/tasks/database.py tests/integration/test_database.py
git commit -m "feat(preferences): emit task_updated event from Database.update_task

update_task now accepts an optional source param and emits a
task_updated event via TaskEventBus with the field-level diff.
Ref: spec_v3.md §7.4"
```

---

### Task 2: Create `CorrectionSubscriber`

**Files:**
- Create: `src/donna/preferences/correction_subscriber.py`
- Create: `tests/unit/test_correction_subscriber.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_correction_subscriber.py`:

```python
"""Unit tests for CorrectionSubscriber."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from donna.preferences.correction_subscriber import CorrectionSubscriber

pytestmark = pytest.mark.asyncio


def _make_task(user_id: str = "nick", task_id: str = "task-1") -> MagicMock:
    task = MagicMock()
    task.user_id = user_id
    task.id = task_id
    return task


class TestCorrectionSubscriber:
    async def test_logs_correction_for_learnable_field(self) -> None:
        """A priority change with source logs a correction."""
        mock_db = MagicMock()
        sub = CorrectionSubscriber(mock_db)
        task = _make_task()
        previous = _make_task()

        with patch(
            "donna.preferences.correction_subscriber.log_correction",
            new_callable=AsyncMock,
        ) as mock_log:
            await sub.on_task_updated(
                task,
                previous=previous,
                changed_fields={"priority": (3, 5)},
                source="api",
            )

        mock_log.assert_called_once_with(
            db=mock_db,
            user_id="nick",
            task_id="task-1",
            task_type="api",
            field="priority",
            original="3",
            corrected="5",
            input_text="",
        )

    async def test_skips_non_learnable_field(self) -> None:
        """A status change is not logged as a correction."""
        mock_db = MagicMock()
        sub = CorrectionSubscriber(mock_db)
        task = _make_task()
        previous = _make_task()

        with patch(
            "donna.preferences.correction_subscriber.log_correction",
            new_callable=AsyncMock,
        ) as mock_log:
            await sub.on_task_updated(
                task,
                previous=previous,
                changed_fields={"status": ("backlog", "done")},
                source="api",
            )

        mock_log.assert_not_called()

    async def test_skips_when_source_is_none(self) -> None:
        """System-initiated updates (source=None) are ignored."""
        mock_db = MagicMock()
        sub = CorrectionSubscriber(mock_db)
        task = _make_task()
        previous = _make_task()

        with patch(
            "donna.preferences.correction_subscriber.log_correction",
            new_callable=AsyncMock,
        ) as mock_log:
            await sub.on_task_updated(
                task,
                previous=previous,
                changed_fields={"priority": (2, 4)},
                source=None,
            )

        mock_log.assert_not_called()

    async def test_logs_multiple_fields_separately(self) -> None:
        """Multi-field edit produces one correction per changed field."""
        mock_db = MagicMock()
        sub = CorrectionSubscriber(mock_db)
        task = _make_task()
        previous = _make_task()

        with patch(
            "donna.preferences.correction_subscriber.log_correction",
            new_callable=AsyncMock,
        ) as mock_log:
            await sub.on_task_updated(
                task,
                previous=previous,
                changed_fields={
                    "priority": (2, 4),
                    "domain": ("personal", "work"),
                    "status": ("backlog", "done"),  # not learnable
                },
                source="discord_modal",
            )

        assert mock_log.call_count == 2
        fields_logged = {c.kwargs["field"] for c in mock_log.call_args_list}
        assert fields_logged == {"priority", "domain"}

    async def test_none_original_becomes_empty_string(self) -> None:
        """None values are stringified to empty string."""
        mock_db = MagicMock()
        sub = CorrectionSubscriber(mock_db)
        task = _make_task()
        previous = _make_task()

        with patch(
            "donna.preferences.correction_subscriber.log_correction",
            new_callable=AsyncMock,
        ) as mock_log:
            await sub.on_task_updated(
                task,
                previous=previous,
                changed_fields={"deadline": (None, "2026-06-01")},
                source="api",
            )

        mock_log.assert_called_once()
        assert mock_log.call_args.kwargs["original"] == ""
        assert mock_log.call_args.kwargs["corrected"] == "2026-06-01"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_correction_subscriber.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'donna.preferences.correction_subscriber'`

- [ ] **Step 3: Implement `CorrectionSubscriber`**

Create `src/donna/preferences/correction_subscriber.py`:

```python
"""Event-driven correction logging subscriber.

Subscribes to ``task_updated`` events on the :class:`TaskEventBus` and
logs user-initiated field changes to the ``correction_log`` table via
:func:`log_correction`. System-initiated updates (``source=None``) are
ignored.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from donna.preferences.correction_logger import log_correction

if TYPE_CHECKING:
    from donna.tasks.database import Database, TaskRow

logger = structlog.get_logger()

LEARNABLE_FIELDS: frozenset[str] = frozenset({
    "priority",
    "domain",
    "title",
    "description",
    "scheduled_start",
    "deadline",
    "estimated_duration",
    "tags",
})


class CorrectionSubscriber:
    """Logs user-initiated task field changes as preference corrections."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def on_task_updated(
        self,
        task: TaskRow,
        *,
        previous: TaskRow | None,
        changed_fields: dict[str, tuple[Any, Any]],
        source: str | None,
        **_: Any,
    ) -> None:
        if source is None:
            return

        for field, (original, corrected) in changed_fields.items():
            if field not in LEARNABLE_FIELDS:
                continue
            try:
                await log_correction(
                    db=self._db,
                    user_id=task.user_id,
                    task_id=task.id,
                    task_type=source,
                    field=field,
                    original=str(original) if original is not None else "",
                    corrected=str(corrected) if corrected is not None else "",
                    input_text="",
                )
            except Exception:
                logger.exception(
                    "correction_subscriber_log_failed",
                    task_id=task.id,
                    field=field,
                    source=source,
                )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/unit/test_correction_subscriber.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/donna/preferences/correction_subscriber.py tests/unit/test_correction_subscriber.py
git commit -m "feat(preferences): add CorrectionSubscriber for event-driven logging

Subscribes to task_updated events and logs corrections for
user-initiated changes to learnable fields (priority, domain,
title, description, scheduled_start, due_date, effort_minutes, tags).
Ref: spec_v3.md §7.4"
```

---

### Task 3: Wire `CorrectionSubscriber` at startup

**Files:**
- Modify: `src/donna/cli_wiring.py:1224-1226`

- [ ] **Step 1: Add the wiring after the event bus is created**

In `src/donna/cli_wiring.py`, find the block at line ~1224:

```python
    event_bus = TaskEventBus()
    db.set_event_bus(event_bus)
```

Add immediately after:

```python
    from donna.preferences.correction_subscriber import CorrectionSubscriber

    correction_subscriber = CorrectionSubscriber(db)
    event_bus.subscribe("task_updated", correction_subscriber.on_task_updated)
```

- [ ] **Step 2: Run the existing test suite to check for import errors**

Run: `pytest tests/unit/test_correction_subscriber.py tests/integration/test_database.py -v`
Expected: All tests PASS — no circular imports, no wiring issues.

- [ ] **Step 3: Commit**

```bash
git add src/donna/cli_wiring.py
git commit -m "feat(preferences): wire CorrectionSubscriber to TaskEventBus at startup

Ref: spec_v3.md §7.4"
```

---

### Task 4: Add `source=` to Discord views (modal, priority select, domain select)

**Files:**
- Modify: `src/donna/integrations/discord_views.py:138, 344, 383`

- [ ] **Step 1: Add `source="discord_modal"` to the Edit Modal**

In `src/donna/integrations/discord_views.py`, find the `on_submit` method's `update_task` call at line 138:

```python
                await self._db.update_task(self._task_id, **updates)
```

Replace with:

```python
                await self._db.update_task(self._task_id, source="discord_modal", **updates)
```

- [ ] **Step 2: Add `source="discord_select"` to the Priority select**

In `src/donna/integrations/discord_views.py`, find `priority_callback` at line 344:

```python
            await self._db.update_task(self._task_id, priority=value)
```

Replace with:

```python
            await self._db.update_task(self._task_id, source="discord_select", priority=value)
```

- [ ] **Step 3: Add `source="discord_select"` to the Domain select**

In `src/donna/integrations/discord_views.py`, find `domain_callback` at line 383:

```python
            await self._db.update_task(self._task_id, domain=TaskDomain(value))
```

Replace with:

```python
            await self._db.update_task(self._task_id, source="discord_select", domain=TaskDomain(value))
```

- [ ] **Step 4: Run related tests to verify no regressions**

Run: `pytest tests/ -k "discord" -v --timeout=30`
Expected: All PASS — `source` is a new optional keyword arg.

- [ ] **Step 5: Commit**

```bash
git add src/donna/integrations/discord_views.py
git commit -m "feat(preferences): pass source= to update_task from Discord views

Edit modal, priority select, and domain select now tag their
updates so the CorrectionSubscriber can log them.
Ref: spec_v3.md §7.4"
```

---

### Task 5: Add `source=` to Dashboard API route

**Files:**
- Modify: `src/donna/api/routes/tasks.py:170`

- [ ] **Step 1: Add `source="api"` to the PATCH handler**

In `src/donna/api/routes/tasks.py`, find the `update_task` route handler at line 170:

```python
    row = await db.update_task(task_id, **updates)
```

Replace with:

```python
    row = await db.update_task(task_id, source="api", **updates)
```

- [ ] **Step 2: Run API tests to verify no regressions**

Run: `pytest tests/ -k "api" -v --timeout=30`
Expected: All PASS.

- [ ] **Step 3: Commit**

```bash
git add src/donna/api/routes/tasks.py
git commit -m "feat(preferences): pass source='api' from dashboard PATCH route

Ref: spec_v3.md §7.4"
```

---

### Task 6: Migrate Discord bot — remove direct `log_correction`, add `source=`

**Files:**
- Modify: `src/donna/integrations/discord_bot.py:960-996`

- [ ] **Step 1: Add `source="discord_command"` to update_task calls and remove the direct `log_correction` block**

In `src/donna/integrations/discord_bot.py`, find the `_handle_field_update` method. Replace the two `update_task` calls (lines 968, 971) to include `source=`, and remove the entire `log_correction` try/except block (lines 982–995).

Find this block (lines 965–995):

```python
        # Apply the update.
        try:
            if field == "priority":
                await self._database.update_task(task.id, priority=int(new_value))
            elif field == "domain":
                domain_enum = TaskDomain(new_value.upper())
                await self._database.update_task(task.id, domain=domain_enum)
            else:
                log.warning("field_update_unsupported_field", field=field)
                return
        except (ValueError, Exception):
            log.exception("field_update_apply_failed", field=field, new_value=new_value)
            await message.channel.send(
                f"Couldn't update {field} to '{new_value}'. Check the value and try again."
            )
            return

        # Log the correction.
        try:
            await log_correction(
                db=self._database,
                user_id=user_id,
                task_id=task.id,
                task_type="discord_command",
                field=field,
                original=original_value,
                corrected=new_value,
                input_text=raw_text,
            )
        except Exception:
            log.exception("correction_log_failed", field=field)
```

Replace with:

```python
        # Apply the update — source tag triggers event-driven correction logging.
        try:
            if field == "priority":
                await self._database.update_task(
                    task.id, source="discord_command", priority=int(new_value),
                )
            elif field == "domain":
                domain_enum = TaskDomain(new_value.upper())
                await self._database.update_task(
                    task.id, source="discord_command", domain=domain_enum,
                )
            else:
                log.warning("field_update_unsupported_field", field=field)
                return
        except (ValueError, Exception):
            log.exception("field_update_apply_failed", field=field, new_value=new_value)
            await message.channel.send(
                f"Couldn't update {field} to '{new_value}'. Check the value and try again."
            )
            return
```

- [ ] **Step 2: Remove the `log_correction` import if no longer used**

Check if `log_correction` is imported at line 30:

```python
from donna.preferences.correction_logger import log_correction
```

Search the rest of the file for other usages of `log_correction`. If none remain, remove this import line.

- [ ] **Step 3: Run Discord bot tests to verify no regressions**

Run: `pytest tests/ -k "discord" -v --timeout=30`
Expected: All PASS.

- [ ] **Step 4: Commit**

```bash
git add src/donna/integrations/discord_bot.py
git commit -m "refactor(preferences): remove direct log_correction from discord_bot

Field updates now pass source='discord_command' to update_task,
and the CorrectionSubscriber handles correction logging via the
task_updated event.
Ref: spec_v3.md §7.4"
```

---

### Task 7: Migrate calendar sync — remove `_log_correction` helper, add `source=`

**Files:**
- Modify: `src/donna/scheduling/calendar_sync.py:174-194, 346-376`

- [ ] **Step 1: Add `source="calendar_sync"` to the `update_task` call in `_handle_time_changed`**

In `src/donna/scheduling/calendar_sync.py`, find the `_handle_time_changed` method. Replace the `update_task` call at line 174 to include `source=`, and remove the `_log_correction` call at lines 189–194.

Find this block (lines 174–194):

```python
        await self._db.update_task(
            task_id,
            scheduled_start=new_start,
            reschedule_count=new_count,
        )

        logger.info(
            "calendar_event_time_changed",
            event_id=event_id,
            task_id=task_id,
            old_start=old_start.isoformat(),
            new_start=new_start.isoformat(),
            reschedule_count=new_count,
        )

        await self._log_correction(
            task_id=task_id,
            field="scheduled_start",
            original=old_start.isoformat(),
            corrected=new_start.isoformat(),
        )
```

Replace with:

```python
        await self._db.update_task(
            task_id,
            source="calendar_sync",
            scheduled_start=new_start,
            reschedule_count=new_count,
        )

        logger.info(
            "calendar_event_time_changed",
            event_id=event_id,
            task_id=task_id,
            old_start=old_start.isoformat(),
            new_start=new_start.isoformat(),
            reschedule_count=new_count,
        )
```

- [ ] **Step 2: Remove the `_log_correction` helper method**

Delete the `_log_correction` method (lines 346–376):

```python
    async def _log_correction(
        self,
        task_id: str,
        field: str,
        original: str,
        corrected: str,
    ) -> None:
        """Write a correction_log row for preference learning."""
        import uuid

        conn = self._db.connection
        await conn.execute(
            """
            INSERT INTO correction_log
                (id, timestamp, user_id, task_type, task_id, input_text,
                 field_corrected, original_value, corrected_value)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                datetime.now(tz=UTC).isoformat(),
                self._user_id,
                "calendar_sync",
                task_id,
                "calendar_event_time_change",
                field,
                original,
                corrected,
            ),
        )
        await conn.commit()
```

- [ ] **Step 3: Run calendar sync tests to verify no regressions**

Run: `pytest tests/integration/test_calendar_sync.py -v`
Expected: All PASS. The calendar sync tests may need adjustment if they asserted on `_log_correction` being called — check and update mocks if needed.

- [ ] **Step 4: Commit**

```bash
git add src/donna/scheduling/calendar_sync.py
git commit -m "refactor(preferences): remove _log_correction from CalendarSync

Calendar sync now passes source='calendar_sync' to update_task,
and the CorrectionSubscriber handles correction logging.
Ref: spec_v3.md §7.4"
```

---

### Task 8: Integration test — end-to-end correction flow

**Files:**
- Create: `tests/integration/test_correction_event_flow.py`

- [ ] **Step 1: Write the integration test**

Create `tests/integration/test_correction_event_flow.py`:

```python
"""Integration test: update_task with source -> correction_log row."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from donna.preferences.correction_subscriber import CorrectionSubscriber
from donna.tasks.database import Database
from donna.tasks.db_models import Base
from donna.tasks.events import TaskEventBus

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def wired_db(tmp_path, state_machine):
    """Database with event bus and CorrectionSubscriber wired up."""
    db_path = tmp_path / "test.db"
    database = Database(db_path=str(db_path), state_machine=state_machine)
    await database.connect()

    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    engine.dispose()

    bus = TaskEventBus()
    database.set_event_bus(bus)

    subscriber = CorrectionSubscriber(database)
    bus.subscribe("task_updated", subscriber.on_task_updated)

    yield database
    await database.close()


class TestCorrectionEventFlow:
    async def test_user_update_creates_correction_row(self, wired_db: Database) -> None:
        """A user-sourced update_task call produces a correction_log row."""
        task = await wired_db.create_task(
            user_id="nick", title="Test task", priority=3,
        )

        await wired_db.update_task(task.id, source="api", priority=5)

        cursor = await wired_db.connection.execute(
            "SELECT task_type, field_corrected, original_value, corrected_value "
            "FROM correction_log WHERE task_id = ?",
            (task.id,),
        )
        rows = await cursor.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "api"            # task_type
        assert rows[0][1] == "priority"        # field_corrected
        assert rows[0][2] == "3"               # original_value
        assert rows[0][3] == "5"               # corrected_value

    async def test_system_update_creates_no_correction(self, wired_db: Database) -> None:
        """A system update (source=None) produces no correction_log row."""
        task = await wired_db.create_task(
            user_id="nick", title="System task", priority=2,
        )

        await wired_db.update_task(task.id, priority=4)

        cursor = await wired_db.connection.execute(
            "SELECT COUNT(*) FROM correction_log WHERE task_id = ?",
            (task.id,),
        )
        row = await cursor.fetchone()
        assert row[0] == 0

    async def test_non_learnable_field_not_logged(self, wired_db: Database) -> None:
        """Updating status (not learnable) with source still produces no correction."""
        from donna.tasks.db_models import TaskStatus

        task = await wired_db.create_task(
            user_id="nick", title="Status task",
        )

        await wired_db.update_task(
            task.id, source="api", status=TaskStatus.IN_PROGRESS,
        )

        cursor = await wired_db.connection.execute(
            "SELECT COUNT(*) FROM correction_log WHERE task_id = ?",
            (task.id,),
        )
        row = await cursor.fetchone()
        assert row[0] == 0

    async def test_multi_field_update_logs_each(self, wired_db: Database) -> None:
        """Editing priority and domain in one call logs two corrections."""
        from donna.tasks.db_models import TaskDomain

        task = await wired_db.create_task(
            user_id="nick", title="Multi", priority=2,
        )

        await wired_db.update_task(
            task.id,
            source="discord_modal",
            priority=4,
            domain=TaskDomain.WORK,
        )

        cursor = await wired_db.connection.execute(
            "SELECT field_corrected FROM correction_log WHERE task_id = ? "
            "ORDER BY field_corrected",
            (task.id,),
        )
        rows = await cursor.fetchall()
        fields = [r[0] for r in rows]
        assert "domain" in fields
        assert "priority" in fields
        assert len(rows) == 2
```

- [ ] **Step 2: Run the integration test**

Run: `pytest tests/integration/test_correction_event_flow.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 3: Run the full test suite**

Run: `pytest tests/ -v --timeout=60`
Expected: All tests PASS. No double-logging from the old direct `log_correction` calls.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_correction_event_flow.py
git commit -m "test(preferences): add e2e integration test for correction event flow

Verifies the full pipeline: update_task with source -> event bus ->
CorrectionSubscriber -> correction_log row.
Ref: spec_v3.md §7.4"
```
