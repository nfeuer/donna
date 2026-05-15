# Preferences: Event-Driven Correction Logging

**Date:** 2026-05-15
**Status:** Approved
**Spec ref:** spec_v3.md §7.4 (Preference Learning Loop)

## Problem

The preferences dashboard is empty because the correction logging pipeline has only two narrow data sources:

1. Discord regex commands ("change priority to 3", "move to work domain") — `discord_bot.py:984`
2. Calendar sync reschedules — `calendar_sync.py:189`

Meanwhile, several user-facing task update paths exist that modify the same fields but never log corrections:

- Discord Edit Modal (`discord_views.py:138`)
- Priority dropdown select (`discord_views.py:344`)
- Domain dropdown select (`discord_views.py:383`)
- Dashboard REST API `PATCH /tasks/{id}` (`api/routes/tasks.py:170`)

The weekly `PreferenceRuleExtractor` has nothing to work with, so no learned preferences are ever generated.

## Design

Use the existing `TaskEventBus` to emit `task_updated` events from `Database.update_task()`. A new `CorrectionSubscriber` listens for these events and logs corrections for user-initiated field changes. This replaces the current direct `log_correction()` calls at individual call sites.

### 1. Event Emission from `Database.update_task()`

`update_task` gains an optional `source: str | None = None` parameter.

After the commit, it computes a diff between `previous_row` and `task_row` for all changed fields (not filtered by allowlist — other subscribers may need non-allowlisted fields), then emits:

```python
await self._emit_event(
    "task_updated",
    task=task_row,
    previous=previous_row,
    changed_fields=changed_fields,  # dict[str, tuple[old, new]]
    source=source,
)
```

The `source` parameter is event metadata only — it is stripped before SQL processing. When `source` is `None`, the event still emits (other subscribers may care), but the correction subscriber ignores it.

### 2. CorrectionSubscriber

New file: `src/donna/preferences/correction_subscriber.py`

```python
class CorrectionSubscriber:
    """Subscribes to task_updated events and logs corrections for user-initiated changes."""

    LEARNABLE_FIELDS = frozenset({
        "priority", "domain", "title", "description",
        "scheduled_start", "due_date", "effort_minutes", "tags",
    })

    def __init__(self, db: Database) -> None:
        self._db = db

    async def on_task_updated(self, task, *, previous, changed_fields, source, **_):
        if source is None:
            return
        for field, (original, corrected) in changed_fields.items():
            if field not in self.LEARNABLE_FIELDS:
                continue
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
```

Pattern matches existing codebase conventions (`PreferenceRuleExtractor`, `PreferenceApplier` — class with db ref, wired at startup).

### 3. Field Allowlist

**Learnable fields** (user overriding Donna's judgment):
- `priority`, `domain`, `title`, `description`, `scheduled_start`, `due_date`, `effort_minutes`, `tags`

**Excluded fields** (status transitions, internal bookkeeping):
- `status`, `completed_at`, `agent_status`, `agent_output`, `notes`, `dependencies`, `inputs_json`, `matched_skill`, `reschedule_count`

**Edge cases:**
- **Null → value**: Logged as a correction — Donna's implicit decision was "not set."
- **Same value**: Filtered out — `changed_fields` only includes actual changes.
- **Multi-field edits**: Each changed field produces a separate `correction_log` row.

### 4. Source Tags by Call Site

| Call site | File | Source tag |
|---|---|---|
| Discord Edit Modal | `discord_views.py:138` | `"discord_modal"` |
| Priority dropdown | `discord_views.py:344` | `"discord_select"` |
| Domain dropdown | `discord_views.py:383` | `"discord_select"` |
| Dashboard API PATCH | `api/routes/tasks.py:170` | `"api"` |
| Discord regex commands | `discord_bot.py:968-971` | `"discord_command"` |
| Calendar sync reschedule | `calendar_sync.py` | `"calendar_sync"` |

System callers (prep_agent, overdue, priority_recalculator, decomposition) omit `source` — defaults to `None`.

### 5. Migration: Remove Direct log_correction() Calls

The following direct `log_correction()` calls are removed, replaced by the event subscriber:

- `discord_bot.py:984` — `_handle_field_update` direct call
- `calendar_sync.py:189` — `_log_correction` call

The `_handle_field_update` method in `discord_bot.py` still applies the update, but passes `source="discord_command"` to `update_task` instead of calling `log_correction` itself.

The `calendar_sync._log_correction` helper is removed; `update_task` calls pass `source="calendar_sync"`.

### 6. Startup Wiring

In `cli_wiring.py`, after the event bus and db are created:

```python
from donna.preferences.correction_subscriber import CorrectionSubscriber

correction_sub = CorrectionSubscriber(db)
event_bus.subscribe("task_updated", correction_sub.on_task_updated)
```

### 7. Unchanged Components

No changes needed to:
- `PreferenceRuleExtractor` — already reads from `correction_log`
- `PreferenceApplier` — already reads from `learned_preferences`
- Dashboard UI — already reads from API endpoints that query these tables
- `correction_log` / `learned_preferences` schemas — existing columns suffice

Once corrections start flowing, the weekly extractor picks them up automatically.

## Testing

- Unit test `CorrectionSubscriber.on_task_updated`: verify it logs corrections for allowlisted fields, skips non-allowlisted fields, skips when source is None
- Unit test `Database.update_task` event emission: verify `task_updated` event emitted with correct diff and source
- Integration test: update a task with `source="api"`, verify `correction_log` row appears
- Verify no double-logging after removing direct `log_correction()` calls
