# Time-Intent Foundation & Strand-Bug Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every task with a parseable time signal route to scheduling immediately (never gated by the Challenger), with urgency derived deterministically, and replace the silent "Scheduled: pending" strand with informative, on-persona confirmations.

**Architecture:** A new `time_intent` value object captures *when* a task should happen (exact / window / constrained / recurring / none). The input parser emits it (with a deterministic `dateparser` fallback when the LLM is degraded). A pure `routing_gate` function decides the route from the extracted facts — no LLM. The `AutoScheduler` consumes that decision and stops deferring time-bound tasks for the Challenger. A new `needs_scheduling` state ensures unplaceable tasks are surfaced, never lost.

**Tech Stack:** Python 3.12 / asyncio, SQLite + aiosqlite, Alembic, `dateparser` 1.4.0, structlog, pytest.

**Scope note:** This is Plan 1 of 3. Constraint-aware placement + the negotiation/rearrange loop are Plan 2; moving the Challenger off the critical path with a widened vocabulary is Plan 3. This plan ships working value on its own: dated tasks schedule correctly and the strand bug is closed. References `spec_v3.md §7.1.1 / §7.2` and `docs/superpowers/specs/2026-06-05-challenger-and-scheduling-intake-design.md`.

---

## File Structure

**Create:**
- `src/donna/scheduling/time_intent.py` — `TimeIntent` value object + JSON (de)serialization + `derive_deadline` / `derive_deadline_type`.
- `src/donna/scheduling/date_fallback.py` — deterministic `dateparser`-based extraction of a coarse `TimeIntent` from raw text when the LLM parse fails.
- `src/donna/scheduling/routing_gate.py` — pure `route(time_intent) -> RouteDecision`.
- `src/donna/integrations/confirmation_copy.py` — persona-voice capture confirmation templates.
- `tests/unit/scheduling/test_time_intent.py`, `test_date_fallback.py`, `test_routing_gate.py`
- `tests/unit/integrations/test_confirmation_copy.py`
- Alembic migration adding `tasks.time_intent_json`.

**Modify:**
- `schemas/task_parse_output.json` — add `time_intent` object.
- `prompts/parse_task.md` — instruct the model to emit `time_intent`.
- `src/donna/orchestrator/input_parser.py` — `TaskParseResult.time_intent`, `_to_parse_result`, fallback wiring in `parse`.
- `src/donna/tasks/database.py` — `create_task(..., time_intent_json=None)`, derive `deadline`/`deadline_type`, persist column.
- `config/task_states.yaml` — add `needs_scheduling` state + transitions.
- `src/donna/scheduling/auto_scheduler.py` — consume `routing_gate`; drop unconditional Challenger defer.
- `src/donna/integrations/discord_bot.py` — stop setting `challenger_pending` for time-bound tasks; send persona confirmation.

---

## Task 1: `TimeIntent` value object + derivation

**Files:**
- Create: `src/donna/scheduling/time_intent.py`
- Test: `tests/unit/scheduling/test_time_intent.py`

- [ ] **Step 1: Create the test package marker**

Run: `mkdir -p tests/unit/scheduling && touch tests/unit/scheduling/__init__.py`

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/scheduling/test_time_intent.py
"""Tests for the TimeIntent value object and deadline derivation."""

from datetime import UTC, datetime

from donna.scheduling.time_intent import TimeIntent, derive_deadline, derive_deadline_type


def _dt(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def test_exact_round_trips_through_json():
    ti = TimeIntent(kind="exact", due_at=_dt(2026, 6, 6, 14), strictness="hard")
    restored = TimeIntent.from_json(ti.to_json())
    assert restored == ti


def test_none_kind_has_no_times():
    ti = TimeIntent.from_dict({"kind": "none"})
    assert ti.kind == "none"
    assert ti.due_at is None and ti.latest is None


def test_derive_deadline_prefers_due_at_then_latest():
    exact = TimeIntent(kind="exact", due_at=_dt(2026, 6, 6, 14), strictness="hard")
    window = TimeIntent(kind="window", earliest=_dt(2026, 6, 6), latest=_dt(2026, 6, 13), strictness="soft")
    assert derive_deadline(exact) == _dt(2026, 6, 6, 14)
    assert derive_deadline(window) == _dt(2026, 6, 13)
    assert derive_deadline(TimeIntent(kind="none")) is None
    assert derive_deadline(TimeIntent(kind="recurring")) is None


def test_derive_deadline_type_maps_strictness_else_none():
    assert derive_deadline_type(TimeIntent(kind="exact", strictness="hard")) == "hard"
    assert derive_deadline_type(TimeIntent(kind="window", strictness="soft")) == "soft"
    assert derive_deadline_type(TimeIntent(kind="none")) == "none"
    assert derive_deadline_type(TimeIntent(kind="recurring")) == "none"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/unit/scheduling/test_time_intent.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'donna.scheduling.time_intent'`

- [ ] **Step 4: Write the implementation**

```python
# src/donna/scheduling/time_intent.py
"""TimeIntent — the structured representation of *when* a task should happen.

Captures the five temporal kinds Donna recognizes (exact, window, constrained,
recurring, none) and derives the legacy ``deadline`` / ``deadline_type`` values
so existing consumers (reminders, overdue detector, weekly planner) keep working
unchanged. See docs/superpowers/specs/2026-06-05-challenger-and-scheduling-intake-design.md.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

Kind = Literal["exact", "window", "constrained", "recurring", "none"]
Strictness = Literal["hard", "soft"]


def _parse_dt(value: Any) -> datetime | None:
    """Parse an ISO-8601 string (or passthrough datetime) to datetime, else None."""
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class TimeIntent:
    """Normalized temporal intent extracted from a task.

    Args:
        kind: One of exact | window | constrained | recurring | none.
        due_at: Concrete deadline for ``exact``.
        earliest: Lower bound for ``window`` / ``constrained``.
        latest: Upper bound for ``window`` / ``constrained``.
        strictness: hard | soft. Ignored when kind == none/recurring.
        constraints: e.g. {"weekday": [0], "time_of_day": "morning"} for ``constrained``.
        recurrence: e.g. {"rrule_or_cron": "0 9 * * 3", "human_readable": "every Wednesday 9am"}.
    """

    kind: Kind = "none"
    due_at: datetime | None = None
    earliest: datetime | None = None
    latest: datetime | None = None
    strictness: Strictness = "soft"
    constraints: dict[str, Any] | None = None
    recurrence: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TimeIntent:
        """Build a TimeIntent from a loosely-typed dict (e.g. LLM JSON)."""
        return cls(
            kind=data.get("kind", "none"),
            due_at=_parse_dt(data.get("due_at")),
            earliest=_parse_dt(data.get("earliest")),
            latest=_parse_dt(data.get("latest")),
            strictness=data.get("strictness", "soft"),
            constraints=data.get("constraints"),
            recurrence=data.get("recurrence"),
        )

    @classmethod
    def from_json(cls, raw: str | None) -> TimeIntent:
        """Deserialize from the JSON string stored on the task row."""
        if not raw:
            return cls(kind="none")
        return cls.from_dict(json.loads(raw))

    def to_json(self) -> str:
        """Serialize to a JSON string for the ``time_intent_json`` column."""
        out: dict[str, Any] = {"kind": self.kind, "strictness": self.strictness}
        for name in ("due_at", "earliest", "latest"):
            value = getattr(self, name)
            if value is not None:
                out[name] = value.isoformat()
        if self.constraints is not None:
            out["constraints"] = self.constraints
        if self.recurrence is not None:
            out["recurrence"] = self.recurrence
        return json.dumps(out)


def derive_deadline(ti: TimeIntent) -> datetime | None:
    """Back-compat deadline: due_at (exact) or latest (window/constrained); else None."""
    if ti.kind == "exact":
        return ti.due_at
    if ti.kind in ("window", "constrained"):
        return ti.latest
    return None


def derive_deadline_type(ti: TimeIntent) -> str:
    """Back-compat deadline_type: strictness for time-bound kinds, else 'none'."""
    if ti.kind in ("exact", "window", "constrained"):
        return ti.strictness
    return "none"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/scheduling/test_time_intent.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add src/donna/scheduling/time_intent.py tests/unit/scheduling/
git commit -m "feat(scheduling): add TimeIntent value object + deadline derivation"
```

---

## Task 2: Deterministic date fallback

**Files:**
- Create: `src/donna/scheduling/date_fallback.py`
- Test: `tests/unit/scheduling/test_date_fallback.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/scheduling/test_date_fallback.py
"""Tests for the LLM-free date fallback that keeps dated tasks routable."""

from datetime import UTC, datetime

from donna.scheduling.date_fallback import fallback_time_intent

NOW = datetime(2026, 6, 6, 9, 0, tzinfo=UTC)  # a Saturday


def test_tomorrow_is_exact_next_day():
    ti = fallback_time_intent("send invoices tomorrow", now=NOW)
    assert ti.kind == "exact"
    assert ti.due_at.date() == datetime(2026, 6, 7, tzinfo=UTC).date()


def test_named_weekday_is_exact():
    ti = fallback_time_intent("call the mechanic Monday", now=NOW)
    assert ti.kind == "exact"
    assert ti.due_at.weekday() == 0  # Monday


def test_next_week_is_window():
    ti = fallback_time_intent("do it sometime next week", now=NOW)
    assert ti.kind == "window"
    assert ti.earliest is not None and ti.latest is not None
    assert ti.earliest < ti.latest


def test_end_of_month_is_window_to_month_end():
    ti = fallback_time_intent("finish by the end of the month", now=NOW)
    assert ti.kind == "window"
    assert ti.latest.month == 6 and ti.latest.day == 30


def test_no_date_is_none():
    ti = fallback_time_intent("organize the garage", now=NOW)
    assert ti.kind == "none"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/scheduling/test_date_fallback.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'donna.scheduling.date_fallback'`

- [ ] **Step 3: Write the implementation**

```python
# src/donna/scheduling/date_fallback.py
"""LLM-free temporal extraction so dated tasks still route when parsing degrades.

Handles the common phrasings ("tomorrow", a named weekday, "next week",
"end of month") using stdlib + dateparser. Intentionally conservative: when in
doubt it returns kind="none" rather than guessing. This is a *fallback*, not the
primary parser — see input_parser.parse for where it is invoked.
"""

from __future__ import annotations

import calendar
import re
from datetime import UTC, datetime, timedelta

import dateparser

from donna.scheduling.time_intent import TimeIntent


def _month_end(now: datetime) -> datetime:
    last = calendar.monthrange(now.year, now.month)[1]
    return now.replace(day=last, hour=23, minute=59, second=0, microsecond=0)


def fallback_time_intent(text: str, now: datetime | None = None) -> TimeIntent:
    """Best-effort TimeIntent from raw text, no LLM. Returns kind='none' if unsure."""
    now = now or datetime.now(tz=UTC)
    lowered = text.lower()

    # Window phrasings take precedence over a bare date inside them.
    if "next week" in lowered:
        days_to_monday = (7 - now.weekday()) % 7 or 7
        start = (now + timedelta(days=days_to_monday)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return TimeIntent(
            kind="window", earliest=start, latest=start + timedelta(days=6), strictness="soft"
        )

    if re.search(r"end of (the )?month", lowered):
        return TimeIntent(kind="window", earliest=now, latest=_month_end(now), strictness="soft")

    # Exact: let dateparser resolve relative dates ("tomorrow", "Friday", "Jun 9").
    parsed = dateparser.parse(
        text,
        settings={
            "RELATIVE_BASE": now.replace(tzinfo=None),
            "PREFER_DATES_FROM": "future",
            "RETURN_AS_TIMEZONE_AWARE": False,
        },
    )
    if parsed is not None:
        due = parsed.replace(tzinfo=UTC)
        if due.hour == 0 and due.minute == 0:
            due = due.replace(hour=12)  # noon default for date-only phrases
        return TimeIntent(kind="exact", due_at=due, strictness="soft")

    return TimeIntent(kind="none")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/scheduling/test_date_fallback.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/donna/scheduling/date_fallback.py tests/unit/scheduling/test_date_fallback.py
git commit -m "feat(scheduling): add LLM-free date fallback for degraded parses"
```

---

## Task 3: Parser emits `time_intent` (schema, prompt, result, fallback wiring)

**Files:**
- Modify: `schemas/task_parse_output.json`
- Modify: `prompts/parse_task.md`
- Modify: `src/donna/orchestrator/input_parser.py:31-46` (dataclass), `:65-80` (`_to_parse_result`), `:106-160` (`parse`)
- Test: `tests/unit/test_input_parser_time_intent.py`

- [ ] **Step 1: Add `time_intent` to the JSON schema**

In `schemas/task_parse_output.json`, add this property inside `"properties"` (after `"recurrence"`):

```json
    "time_intent": {
      "type": ["object", "null"],
      "description": "Structured temporal intent. Null when no time is expressed.",
      "properties": {
        "kind": { "type": "string", "enum": ["exact", "window", "constrained", "recurring", "none"] },
        "due_at": { "type": ["string", "null"], "format": "date-time" },
        "earliest": { "type": ["string", "null"], "format": "date-time" },
        "latest": { "type": ["string", "null"], "format": "date-time" },
        "strictness": { "type": "string", "enum": ["hard", "soft"] },
        "constraints": { "type": ["object", "null"] },
        "recurrence": { "type": ["object", "null"] }
      }
    }
```

(Do not add it to the top-level `"required"` array — it is optional for back-compat.)

- [ ] **Step 2: Document `time_intent` in the prompt**

In `prompts/parse_task.md`, inside the ```json output block, add this line after the `"recurrence"` line:

```
  "time_intent": { "kind": "exact|window|constrained|recurring|none", "due_at": null, "earliest": null, "latest": null, "strictness": "soft", "constraints": null, "recurrence": null },
```

Then append this section after "## Priority Guidelines":

```markdown
## Time Intent

Classify *when* the task should happen into `time_intent.kind`:

- `exact` — a specific point ("tomorrow", "Monday", "by Friday 5pm"). Set `due_at`.
- `window` — a flexible range ("sometime next week", "by end of month"). Set `earliest` + `latest`.
- `constrained` — a range plus a structural rule ("a Monday within the next month"). Set
  `earliest` + `latest` + `constraints` (e.g. `{"weekday": [0]}`, Monday=0 … Sunday=6).
- `recurring` — repeats ("every Wednesday"). Set `recurrence.human_readable`.
- `none` — no time expressed.

`strictness`: `hard` if missing it has real consequences, else `soft`. All datetimes ISO-8601.
```

- [ ] **Step 3: Write the failing test**

```python
# tests/unit/test_input_parser_time_intent.py
"""TaskParseResult carries time_intent; parse() falls back when the LLM omits it."""

from donna.orchestrator.input_parser import TaskParseResult, _to_parse_result


def _base(**over):
    data = {
        "title": "Send invoices", "description": None, "domain": "personal",
        "priority": 2, "deadline": None, "deadline_type": "none",
        "estimated_duration": 30, "recurrence": None, "tags": [],
        "prep_work_flag": False, "agent_eligible": False, "confidence": 0.9,
    }
    data.update(over)
    return data


def test_result_has_time_intent_field_defaulting_none():
    result = _to_parse_result(_base())
    assert isinstance(result, TaskParseResult)
    assert result.time_intent is None


def test_result_preserves_time_intent_dict():
    ti = {"kind": "exact", "due_at": "2026-06-07T12:00:00+00:00", "strictness": "soft"}
    result = _to_parse_result(_base(time_intent=ti))
    assert result.time_intent == ti
```

- [ ] **Step 4: Run test to verify it fails**

Run: `pytest tests/unit/test_input_parser_time_intent.py -v`
Expected: FAIL — `TypeError: TaskParseResult.__init__() ... unexpected` / missing field

- [ ] **Step 5: Add the field to the dataclass**

In `src/donna/orchestrator/input_parser.py`, add to `TaskParseResult` (after `confidence: float`):

```python
    time_intent: dict | None = None
```

- [ ] **Step 6: Populate it in `_to_parse_result`**

In `_to_parse_result`, add before the closing `)`:

```python
        time_intent=data.get("time_intent"),
```

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest tests/unit/test_input_parser_time_intent.py -v`
Expected: PASS (2 passed)

- [ ] **Step 8: Wire the fallback into `parse`**

In `src/donna/orchestrator/input_parser.py`, replace the `# 5. Convert to result` line and its `result = _to_parse_result(validated)` with:

```python
        # 5. Convert to result
        result = _to_parse_result(validated)

        # 5a. Backfill time_intent deterministically if the LLM omitted it.
        # Keeps dated tasks routable when the model degrades (CLAUDE.md fallback rule).
        if result.time_intent is None:
            from donna.scheduling.date_fallback import fallback_time_intent

            fallback = fallback_time_intent(raw_text)
            if fallback.kind != "none":
                import dataclasses as _dc

                result = _dc.replace(result, time_intent=json_safe(fallback))
                logger.warning(
                    "task_parse_time_intent_fallback",
                    kind=fallback.kind,
                    user_id=user_id,
                )
```

Add this helper near `_to_parse_result` (module level):

```python
def json_safe(ti: "Any") -> dict:
    """Round-trip a TimeIntent into a plain JSON-safe dict for TaskParseResult."""
    import json as _json

    return _json.loads(ti.to_json())
```

> Note: this fallback logs but does not call `dispatch_fallback_alert()` because the parser has no `NotificationService` handle; the `logger.warning(event_type-style)` satisfies the CLAUDE.md "log with fallback" exception. The scheduler-side alerting is covered in Task 7.

- [ ] **Step 9: Run the parser tests**

Run: `pytest tests/unit/test_input_parser_time_intent.py tests/unit -k input_parser -v`
Expected: PASS (existing parser tests still green)

- [ ] **Step 10: Commit**

```bash
git add schemas/task_parse_output.json prompts/parse_task.md src/donna/orchestrator/input_parser.py tests/unit/test_input_parser_time_intent.py
git commit -m "feat(parser): emit time_intent with deterministic fallback"
```

---

## Task 4: Persist `time_intent` + derive deadline/deadline_type

**Files:**
- Modify: `src/donna/tasks/db_models.py:162` (SQLAlchemy `Task` model)
- Create: Alembic migration under `alembic/versions/`
- Modify: `src/donna/tasks/database.py` (`_TASK_COLUMNS`, `_UPDATABLE_COLUMNS`, `TaskRow`, `create_task`)
- Test: `tests/integration/test_create_task_time_intent.py`

> **Why integration, not unit:** `create_task` requires a real `Database` + `StateMachine`.
> The integration suite builds tables from the SQLAlchemy model via
> `Base.metadata.create_all` (see `tests/integration/test_database.py:28-41`), so the model
> in Step 1 is what makes the column exist in tests. The Alembic migration (Step 2) is what
> makes it exist in production. **Both are required** per CLAUDE.md.

- [ ] **Step 1: Add the column to the SQLAlchemy model**

In `src/donna/tasks/db_models.py`, immediately after the `inputs_json` column (line 162):

```python
    time_intent_json: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 2: Generate and fill the Alembic migration**

Run: `alembic revision -m "add tasks.time_intent_json"`
Set the body of the generated file:

```python
def upgrade() -> None:
    op.add_column("tasks", sa.Column("time_intent_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "time_intent_json")
```

- [ ] **Step 3: Apply the migration**

Run: `alembic upgrade head`
Expected: completes; `sqlite3 db/donna_tasks.db '.schema tasks'` shows `time_intent_json TEXT`.

- [ ] **Step 4: Add the column to the DB-layer constants + `TaskRow`**

The INSERT and SELECTs are driven positionally by `_TASK_COLUMNS` and `TaskRow(*values)`
in `_row_to_task`, so the column must be appended at the **end** of both, in lockstep.

1. Append to `_TASK_COLUMNS` (`database.py:148`, after `"inputs_json",`):

```python
    "time_intent_json",
```

2. Add as the **final field** of the `TaskRow` dataclass (`database.py`, after the
   `inputs: dict[str, Any] | None = None` field):

```python
    # Structured temporal intent (raw JSON string; see scheduling.time_intent).
    time_intent_json: str | None = None
```

3. Add `"time_intent_json"` to the `_UPDATABLE_COLUMNS` set (`database.py:~108`, after
   `"inputs_json",`) so `update_task` can write it.

`_row_to_task` needs **no** change: `time_intent_json` is a plain string that passes
through positionally to the new final `TaskRow` field. (Its docstring line "inputs_json …
is the final SELECT column" is now stale — update the wording if you like; not functional.)

- [ ] **Step 5: Add the `create_task` parameter + derivation + INSERT value**

In `create_task` (`database.py:~519`), add to the signature after
`inputs: dict[str, Any] | None = None,`:

```python
        time_intent_json: str | None = None,
```

Immediately after `now = datetime.utcnow().isoformat()` (`database.py:530`), derive the
back-compat fields when a `time_intent_json` is supplied and the caller did not override:

```python
        if time_intent_json is not None:
            from donna.scheduling.time_intent import (
                TimeIntent,
                derive_deadline,
                derive_deadline_type,
            )

            _ti = TimeIntent.from_json(time_intent_json)
            if deadline is None:
                deadline = derive_deadline(_ti)
            if deadline_type == DeadlineType.NONE:
                deadline_type = DeadlineType(derive_deadline_type(_ti))
```

In the `VALUES (...)` tuple, append after `json.dumps(inputs) if inputs else None,`
(`database.py:569`) as the new final element:

```python
                    time_intent_json,
```

(The column name is already in `_SELECT_COLUMNS` via Step 4, so the INSERT picks it up.)

- [ ] **Step 6: Write the integration test**

```python
# tests/integration/test_create_task_time_intent.py
"""create_task stores time_intent and derives deadline/deadline_type from it."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine

from donna.scheduling.time_intent import TimeIntent
from donna.tasks.database import Database
from donna.tasks.db_models import Base, DeadlineType

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def db(tmp_path, state_machine):
    """Database backed by a temp file, tables from SQLAlchemy metadata."""
    db_path = tmp_path / "test.db"
    database = Database(db_path=str(db_path), state_machine=state_machine)
    await database.connect()
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    yield database
    await database.close()


async def test_create_task_derives_deadline_from_window_intent(db: Database):
    ti = TimeIntent(kind="window", latest=datetime(2026, 6, 13, tzinfo=UTC), strictness="soft")
    task = await db.create_task(
        user_id="nick", title="Send invoices", time_intent_json=ti.to_json()
    )
    assert task.deadline is not None and task.deadline.startswith("2026-06-13")
    assert task.deadline_type == DeadlineType.SOFT.value
    assert task.time_intent_json == ti.to_json()


async def test_create_task_none_intent_leaves_deadline_type_none(db: Database):
    ti = TimeIntent(kind="none")
    task = await db.create_task(user_id="nick", title="Organize garage", time_intent_json=ti.to_json())
    assert task.deadline is None
    assert task.deadline_type == DeadlineType.NONE.value
```

> `state_machine` is a shared fixture from `tests/conftest.py:192`.

- [ ] **Step 7: Run the test**

Run: `pytest tests/integration/test_create_task_time_intent.py -v`
Expected: PASS (2 passed). If it fails with "no such column", the model (Step 1) or
`_TASK_COLUMNS` (Step 4) is out of sync — fix before continuing.

- [ ] **Step 8: Run the DB regression suite**

Run: `pytest tests/integration/test_database.py -q`
Expected: PASS (positional `TaskRow(*values)` mapping still intact).

- [ ] **Step 9: Commit**

```bash
git add src/donna/tasks/db_models.py alembic/versions/ src/donna/tasks/database.py tests/integration/test_create_task_time_intent.py
git commit -m "feat(db): persist time_intent_json and derive deadline/deadline_type"
```

---

## Task 5: Deterministic routing gate

**Files:**
- Create: `src/donna/scheduling/routing_gate.py`
- Test: `tests/unit/scheduling/test_routing_gate.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/scheduling/test_routing_gate.py
"""The routing gate decides route + urgency from facts only — no LLM."""

from datetime import UTC, datetime, timedelta

from donna.scheduling.routing_gate import Route, route
from donna.scheduling.time_intent import TimeIntent

NOW = datetime(2026, 6, 6, 9, 0, tzinfo=UTC)


def test_recurring_routes_to_automation():
    d = route(TimeIntent(kind="recurring", recurrence={"human_readable": "every Wed"}), priority=2, now=NOW)
    assert d.route == Route.AUTOMATION


def test_exact_routes_to_scheduler_now_and_defers_challenger_false():
    d = route(TimeIntent(kind="exact", due_at=NOW + timedelta(days=1), strictness="hard"), priority=2, now=NOW)
    assert d.route == Route.SCHEDULER
    assert d.defer_for_challenger is False


def test_window_and_constrained_route_to_scheduler():
    assert route(TimeIntent(kind="window", latest=NOW + timedelta(days=5)), priority=2, now=NOW).route == Route.SCHEDULER
    assert route(TimeIntent(kind="constrained", latest=NOW + timedelta(days=20), constraints={"weekday": [0]}), priority=2, now=NOW).route == Route.SCHEDULER


def test_none_routes_to_backlog_and_may_defer_challenger():
    d = route(TimeIntent(kind="none"), priority=2, now=NOW)
    assert d.route == Route.BACKLOG
    assert d.defer_for_challenger is True


def test_urgent_when_deadline_near_or_high_priority():
    near = route(TimeIntent(kind="exact", due_at=NOW + timedelta(hours=3)), priority=2, now=NOW)
    high = route(TimeIntent(kind="window", latest=NOW + timedelta(days=10)), priority=5, now=NOW)
    far = route(TimeIntent(kind="window", latest=NOW + timedelta(days=10)), priority=2, now=NOW)
    assert near.urgent is True and high.urgent is True and far.urgent is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/scheduling/test_routing_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'donna.scheduling.routing_gate'`

- [ ] **Step 3: Write the implementation**

```python
# src/donna/scheduling/routing_gate.py
"""Deterministic routing + urgency for a freshly captured task.

No LLM: given the extracted TimeIntent and priority, decide where the task goes
and whether it is urgent. This is the gate that closes the strand bug — a
time-bound task is sent to the scheduler immediately and is never deferred for
the Challenger. See the design spec (2026-06-05) §3.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from donna.scheduling.time_intent import TimeIntent, derive_deadline

URGENT_WITHIN = timedelta(hours=24)
URGENT_PRIORITY = 4


class Route(enum.Enum):
    """Where a captured task should go next."""

    SCHEDULER = "scheduler"
    AUTOMATION = "automation"
    BACKLOG = "backlog"


@dataclass(frozen=True, slots=True)
class RouteDecision:
    route: Route
    urgent: bool
    defer_for_challenger: bool


def route(ti: TimeIntent, priority: int, now: datetime | None = None) -> RouteDecision:
    """Decide route + urgency. Time-bound tasks never defer for the Challenger."""
    now = now or datetime.now(tz=UTC)

    if ti.kind == "recurring":
        return RouteDecision(Route.AUTOMATION, urgent=False, defer_for_challenger=False)

    if ti.kind in ("exact", "window", "constrained"):
        deadline = derive_deadline(ti)
        near = deadline is not None and (deadline - now) <= URGENT_WITHIN
        urgent = bool(near or (priority or 0) >= URGENT_PRIORITY)
        return RouteDecision(Route.SCHEDULER, urgent=urgent, defer_for_challenger=False)

    # kind == "none": no time pressure — eligible for the Challenger / backlog.
    return RouteDecision(Route.BACKLOG, urgent=False, defer_for_challenger=True)
```

> The test imports `route` and `Route` and reads `.route`. Keep `RouteDecision.route`
> as the attribute name so Task 7's wiring matches.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/scheduling/test_routing_gate.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/donna/scheduling/routing_gate.py tests/unit/scheduling/test_routing_gate.py
git commit -m "feat(scheduling): add deterministic routing + urgency gate"
```

---

## Task 6: Add `needs_scheduling` state

**Files:**
- Modify: `config/task_states.yaml`
- Test: `tests/unit/test_task_states_needs_scheduling.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_task_states_needs_scheduling.py
"""needs_scheduling is a valid state with the expected transitions."""

from pathlib import Path

import yaml


def _load():
    root = Path(__file__).resolve().parents[2]
    return yaml.safe_load((root / "config" / "task_states.yaml").read_text())


def test_needs_scheduling_is_a_state():
    assert "needs_scheduling" in _load()["states"]


def test_backlog_to_needs_scheduling_and_back_and_to_scheduled():
    transitions = {(t["from"], t["to"]) for t in _load()["transitions"]}
    assert ("backlog", "needs_scheduling") in transitions
    assert ("needs_scheduling", "scheduled") in transitions
    assert ("needs_scheduling", "backlog") in transitions
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_task_states_needs_scheduling.py -v`
Expected: FAIL — `assert 'needs_scheduling' in [...]`

- [ ] **Step 3: Edit the config**

In `config/task_states.yaml`, add `needs_scheduling` to the `states:` list (after `scheduled`),
and add these transitions to the `transitions:` list:

```yaml
  - from: backlog
    to: needs_scheduling
    trigger: scheduler_no_slot_found
    side_effects:
      - open_negotiation

  - from: needs_scheduling
    to: scheduled
    trigger: alternative_or_rearrange_accepted
    side_effects:
      - create_calendar_event
      - set_donna_managed_true

  - from: needs_scheduling
    to: backlog
    trigger: user_declines_scheduling
    side_effects:
      - resurface_in_weekly_plan
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_task_states_needs_scheduling.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add config/task_states.yaml tests/unit/test_task_states_needs_scheduling.py
git commit -m "feat(config): add needs_scheduling task state + transitions"
```

---

## Task 7: AutoScheduler consumes the routing gate (kills the strand bug)

**Files:**
- Modify: `src/donna/scheduling/auto_scheduler.py:46-61`
- Test: `tests/unit/scheduling/test_auto_scheduler_routing.py`

- [ ] **Step 1: Write the failing regression test**

```python
# tests/unit/scheduling/test_auto_scheduler_routing.py
"""Regression: a time-bound task schedules even if the Challenger never resolves."""

from datetime import UTC, datetime, timedelta

import pytest

from donna.scheduling.auto_scheduler import AutoScheduler
from donna.scheduling.scheduler import ScheduledSlot
from donna.scheduling.time_intent import TimeIntent
from donna.tasks.db_models import TaskStatus


class _FakeScheduler:
    def find_next_slot(self, task, events, now=None):
        return ScheduledSlot(start=datetime(2026, 6, 7, 14, tzinfo=UTC),
                             end=datetime(2026, 6, 7, 14, 30, tzinfo=UTC))


class _FakeDB:
    def __init__(self):
        self.transitions = []
        self.updates = {}

    async def transition_task_state(self, task_id, status):
        self.transitions.append(status)

    async def update_task(self, task_id, **kw):
        self.updates.update(kw)


def _task(**over):
    class T:
        id = "t1"
        status = TaskStatus.BACKLOG.value
        domain = "personal"
        priority = 2
        estimated_duration = 30
        title = "Send invoices"
        time_intent_json = TimeIntent(
            kind="exact", due_at=datetime(2026, 6, 7, tzinfo=UTC), strictness="hard"
        ).to_json()
    t = T()
    for k, v in over.items():
        setattr(t, k, v)
    return t


@pytest.mark.asyncio
async def test_time_bound_task_schedules_without_challenger_resolution():
    db = _FakeDB()
    auto = AutoScheduler(_FakeScheduler(), db, None, "primary", None)
    # challenger_pending=True simulates the old defer signal — it must be IGNORED
    # for a time-bound task now.
    await auto.on_task_created(_task(), challenger_pending=True)
    assert TaskStatus.SCHEDULED in db.transitions
    assert "scheduled_start" in db.updates


@pytest.mark.asyncio
async def test_no_time_task_stays_in_backlog_not_auto_scheduled():
    db = _FakeDB()
    auto = AutoScheduler(_FakeScheduler(), db, None, "primary", None)
    none_intent = TimeIntent(kind="none").to_json()
    await auto.on_task_created(_task(time_intent_json=none_intent), challenger_pending=False)
    # Undated tasks are NOT crammed onto the calendar — they wait in backlog.
    assert TaskStatus.SCHEDULED not in db.transitions
    assert "scheduled_start" not in db.updates
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/scheduling/test_auto_scheduler_routing.py -v`
Expected: FAIL — the time-bound case is deferred (no transition), because `on_task_created`
returns early when `challenger_pending` is truthy.

- [ ] **Step 3: Rewrite `on_task_created` to consult the routing gate**

In `src/donna/scheduling/auto_scheduler.py`, replace the current `on_task_created`
(lines 46-50) with explicit per-route branches. Note the BACKLOG route **never** calls
`_schedule` — undated tasks are left in backlog (surfaced later by the weekly planner),
not auto-placed:

```python
    async def on_task_created(self, task: TaskRow, **context: Any) -> None:
        from donna.scheduling.routing_gate import Route, route
        from donna.scheduling.time_intent import TimeIntent

        ti = TimeIntent.from_json(getattr(task, "time_intent_json", None))
        decision = route(ti, priority=task.priority or 2)

        if decision.route is Route.SCHEDULER:
            # Time-bound: ALWAYS schedule now, regardless of the Challenger.
            # This is the strand-bug fix.
            await self._schedule(task)
            return

        if decision.route is Route.AUTOMATION:
            # Recurring intents are owned by the automation/cron pipeline.
            logger.info("auto_scheduler_skip_recurring", task_id=task.id)
            return

        # Route.BACKLOG: no time pressure. Leave it in backlog for the weekly
        # planner / Challenger to surface — do NOT auto-place an undated task.
        logger.info("auto_scheduler_backlog_no_time", task_id=task.id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/scheduling/test_auto_scheduler_routing.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Run the existing auto-scheduler tests for regressions**

Run: `pytest tests/unit -k auto_scheduler -v`
Expected: PASS (existing behavior for no-time / challenger cases preserved)

- [ ] **Step 6: Commit**

```bash
git add src/donna/scheduling/auto_scheduler.py tests/unit/scheduling/test_auto_scheduler_routing.py
git commit -m "fix(scheduling): route time-bound tasks immediately; end backlog strand"
```

---

## Task 8: Persona confirmation copy + Discord wiring

**Files:**
- Create: `src/donna/integrations/confirmation_copy.py`
- Modify: `src/donna/integrations/discord_bot.py:524-555`
- Test: `tests/unit/integrations/test_confirmation_copy.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/integrations/test_confirmation_copy.py
"""Confirmation copy states the real slot in Donna's voice."""

from datetime import UTC, datetime

from donna.integrations.confirmation_copy import capture_confirmation
from donna.scheduling.scheduler import ScheduledSlot
from donna.scheduling.time_intent import TimeIntent


def test_placed_exact_includes_day_date_time():
    slot = ScheduledSlot(
        start=datetime(2026, 6, 6, 14, 0, tzinfo=UTC),
        end=datetime(2026, 6, 6, 14, 30, tzinfo=UTC),
    )
    msg = capture_confirmation(
        title="Send invoices to Kevin", domain="personal", priority=2,
        time_intent=TimeIntent(kind="exact"), slot=slot,
    )
    assert "Send invoices to Kevin" in msg
    assert "Friday" in msg and "Jun 6" in msg and "2:00" in msg


def test_recurring_states_cadence():
    msg = capture_confirmation(
        title="Standup", domain="work", priority=2,
        time_intent=TimeIntent(kind="recurring", recurrence={"human_readable": "every Wednesday at 9:00 AM"}),
        slot=None,
    )
    assert "every Wednesday at 9:00 AM" in msg


def test_no_time_says_backlog():
    msg = capture_confirmation(
        title="Organize the garage", domain="personal", priority=1,
        time_intent=TimeIntent(kind="none"), slot=None,
    )
    assert "backlog" in msg.lower()


def test_no_slot_offers_to_rearrange():
    msg = capture_confirmation(
        title="Invoices", domain="personal", priority=3,
        time_intent=TimeIntent(kind="exact"), slot=None, no_slot=True,
    )
    assert "move something" in msg.lower() or "rearrange" in msg.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/integrations/test_confirmation_copy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'donna.integrations.confirmation_copy'`

- [ ] **Step 3: Write the implementation**

```python
# src/donna/integrations/confirmation_copy.py
"""Persona-voice capture confirmations (see prompts/donna_persona.md).

Templates, not LLM output: deterministic, zero-token, and consistent with
Donna's voice — confident, specific times, clear options when she needs you.
"""

from __future__ import annotations

from donna.scheduling.scheduler import ScheduledSlot
from donna.scheduling.time_intent import TimeIntent


def _fmt_range(slot: ScheduledSlot) -> str:
    # e.g. "Friday, Jun 6, 2:00–2:30 PM"
    day = slot.start.strftime("%A, %b ") + str(slot.start.day)
    start = slot.start.strftime("%-I:%M").lstrip("0")
    end = slot.end.strftime("%-I:%M %p").lstrip("0")
    return f"{day}, {start}–{end}"


def capture_confirmation(
    *,
    title: str,
    domain: str,
    priority: int,
    time_intent: TimeIntent,
    slot: ScheduledSlot | None,
    no_slot: bool = False,
) -> str:
    """Return the message Donna sends after capturing a task."""
    tag = f"({domain} · P{priority})"

    if time_intent.kind == "recurring":
        human = (time_intent.recurrence or {}).get("human_readable", "on your schedule")
        return f"**{human}.** Done. — {title}"

    if no_slot:
        return (
            f"'{title}' is tight — I couldn't find a slot before your deadline. "
            f"Want me to move something to make room, or take the next opening?"
        )

    if slot is not None:
        if time_intent.kind in ("window", "constrained"):
            return (
                f"Penciled '{title}' in for **{_fmt_range(slot)}** — it's flexible, "
                f"I'll tighten it as your week fills. {tag}"
            )
        return f"Done. {title} — **{_fmt_range(slot)}**. {tag}"

    # No time expressed and nothing scheduled.
    return (
        f"Filed '{title}' in your backlog. No deadline, so I'll raise it in your "
        f"weekly plan — unless you tell me it matters sooner."
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/integrations/test_confirmation_copy.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Wire it into the Discord capture path**

In `src/donna/integrations/discord_bot.py`, at the `create_task` call (lines 524-538),
pass the parsed `time_intent` and stop unconditionally deferring for the Challenger:

Change the `create_task(...)` call to include:

```python
                time_intent_json=__import__("json").dumps(result.time_intent) if result.time_intent else None,
                challenger_pending=False,  # routing_gate (auto_scheduler) decides deferral now
```

Then replace the static confirmation send (lines 547-551) with slot-aware copy. After
the task is created, the `AutoScheduler` runs on the `task_created` event; for the
confirmation we re-read the (possibly now-scheduled) task and render:

```python
            from donna.integrations.confirmation_copy import capture_confirmation
            from donna.scheduling.scheduler import ScheduledSlot
            from donna.scheduling.time_intent import TimeIntent

            fresh = await self._database.get_task(task.id)
            ti = TimeIntent.from_json(getattr(fresh, "time_intent_json", None))
            slot = None
            if fresh is not None and fresh.scheduled_start:
                from datetime import datetime, timedelta

                start = datetime.fromisoformat(fresh.scheduled_start)
                slot = ScheduledSlot(
                    start=start,
                    end=start + timedelta(minutes=fresh.estimated_duration or 30),
                )
            msg = capture_confirmation(
                title=task.title, domain=task.domain, priority=task.priority,
                time_intent=ti, slot=slot, no_slot=(fresh is not None and fresh.status == "needs_scheduling"),
            )
            from donna.integrations.discord_views import TaskConfirmationView

            await message.channel.send(
                msg, view=TaskConfirmationView(task_id=task.id, db=self._database)
            )
```

> Remove the old `confirmation_view`/`message.channel.send("Got it. … Scheduled: pending.")`
> block (lines 542-551) that this replaces.

- [ ] **Step 6: Run the Discord capture tests**

Run: `pytest tests/unit -k "discord and capture" -v`
Expected: PASS (or update the one assertion that checked for the literal
"Scheduled: pending." string to expect the new copy).

- [ ] **Step 7: Commit**

```bash
git add src/donna/integrations/confirmation_copy.py src/donna/integrations/discord_bot.py tests/unit/integrations/test_confirmation_copy.py
git commit -m "feat(discord): persona capture confirmations; drop unconditional defer"
```

---

## Task 9: Full-suite verification + spec sync

**Files:**
- Modify: `spec_v3.md` (§7.1.1 / §7.2 note), `docs/superpowers/specs/followups.md`

- [ ] **Step 1: Run the full unit suite**

Run: `pytest tests/unit -q`
Expected: PASS (no regressions). Investigate and fix any failure before proceeding.

- [ ] **Step 2: Note the spec drift**

Append to `docs/superpowers/specs/followups.md`:

```markdown
- 2026-06-06 (time-intent foundation): urgency/deadline classification moved from the
  Challenger path to the input parser (`time_intent`) + deterministic `routing_gate`.
  Challenger no longer gates scheduling of time-bound tasks. `spec_v3.md §7.1.1/§7.2`
  to be updated when Plan 3 (Challenger off critical path) lands.
```

- [ ] **Step 3: Commit**

```bash
git add spec_v3.md docs/superpowers/specs/followups.md
git commit -m "docs: record time-intent routing drift in followups"
```

---

## Self-Review Notes (for the implementer)

- **Strand bug** (spec §1): closed by Task 7 — `BACKLOG` is the only route that may defer.
- **Deterministic urgency** (spec §3): Task 5; no LLM dependency.
- **time_intent taxonomy** (spec §1/§2): Tasks 1, 3, 4.
- **Degraded-LLM robustness** (spec, Error Handling): Task 2 + Task 3 Step 8.
- **needs_scheduling** (spec §8): Task 6; surfaced no-slot copy in Task 8.
- **Persona confirmations** (spec §7): Task 8.
- **Deferred to Plan 2/3** (explicitly not here): constraint-aware `find_next_slot`,
  negotiation/bump planner, Challenger vocabulary widening + Novelty Judge routing.
