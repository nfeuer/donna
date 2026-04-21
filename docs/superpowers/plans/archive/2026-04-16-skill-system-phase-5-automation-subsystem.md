# Skill System Phase 5 — Automation Subsystem

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the automation subsystem — schedule-driven Donna work that consumes capabilities, runs on cron expressions, dispatches to skill or claude-native per run, evaluates alert conditions, and pauses itself after repeated failures.

**Architecture:**
- New tables `automation` + `automation_run` (spec §5.11, §5.12). Migration `add_automation_tables_phase_5`.
- `AutomationRepository` — single persistence boundary for automation + automation_run rows.
- `CronScheduleCalculator` — computes `next_run_at` from a cron expression using `croniter` (new dependency).
- `AlertEvaluator` — evaluates the `alert_conditions` JSON DSL (all_of / any_of / leaf predicates with eight ops) against a run output dict.
- `AutomationDispatcher` — for a single due automation: decides skill vs claude_native, runs it, enforces per-run cost cap, evaluates alert, dispatches notification, increments run/failure counters, advances `next_run_at`, pauses on repeated failures.
- `AutomationScheduler` — asyncio loop polling every minute for due automations; calls the dispatcher per row. Sole creator of `automation_run` rows.
- REST routes under `/admin/automations` for list, detail, create, edit, pause, resume, delete, run-now, run-history.
- Lifespan wiring adds the scheduler as a background task (parallel to the existing `AsyncCronScheduler` from Phase 4).

**Tech Stack:** Python 3.12 async, SQLAlchemy 2.x + Alembic, aiosqlite, `croniter` (new), existing `ModelRouter`, `BudgetGuard`, `SkillExecutor`, `NotificationService`, FastAPI, structlog.

**Spec alignment:** Implements §5.11, §5.12, §6.9, §6.10 Automations view, and R30–R34. Covers acceptance scenarios AS-5.1 through AS-5.5.

**Dependencies from earlier phases:** `CapabilityRegistry`, `CapabilityMatcher`, `SkillExecutor`, `SkillLifecycleManager`, `ModelRouter`, `BudgetGuard`, `NotificationService`, `SkillRunRepository`, Phase 1–4 migrations.

**Phase 5 invariants (must hold after every task):**
- `automation_run` rows are inserted only by `AutomationDispatcher` (called from the scheduler or from the manual-run endpoint).
- Every Claude invocation from the claude_native path goes through `ModelRouter` so `invocation_log` stays authoritative.
- `AutomationScheduler` respects `config.enabled` — when the skill system is disabled in `config/skills.yaml`, the scheduler does not register (so skill-dependent automations cannot silently run Claude-only).
- `BudgetGuard.check_pre_call` is invoked before every dispatch; `BudgetPausedError` produces `status = skipped_budget` with `next_run_at` advanced.
- Per-run cost cap (`automation.max_cost_per_run_usd`) is enforced AFTER the run completes by comparing accumulated cost_usd; over-budget runs mark the row `failed` with `error = "cost_exceeded"`.
- Repeated failures (≥ `config.automation_failure_pause_threshold`, default 5, consecutive) pause the automation and emit a `NOTIF_AUTOMATION_FAILURE` message.

**Out of scope for Phase 5 (explicitly deferred):**
- Event-triggered automations (OOS-1). Only `on_schedule` + `on_manual` ship.
- Automation composition / chains (OOS-3).
- Automation sharing across users (OOS-7).
- Dashboard UI (only JSON routes in this phase).
- Discord "create automation from natural language" flow (the challenger change is Phase 5 work per the spec but the plan groups it as a downstream enhancement — the creation endpoint exists from day one for the Discord/chat adapter to call).

---

## File Structure

### New files

```
alembic/versions/
  add_automation_tables_phase_5.py       -- Migration for automation + automation_run

src/donna/automations/
  __init__.py
  models.py                              -- AutomationRow, AutomationRunRow dataclasses + row mappers
  repository.py                          -- AutomationRepository (CRUD + queries)
  cron.py                                -- CronScheduleCalculator - next_run_at from cron expr
  alert.py                               -- AlertEvaluator - evaluates alert_conditions JSON
  dispatcher.py                          -- AutomationDispatcher - executes one due automation
  scheduler.py                           -- AutomationScheduler - asyncio loop

src/donna/api/routes/
  automations.py                         -- /admin/automations routes

tests/unit/
  test_automation_repository.py
  test_automation_cron.py
  test_automation_alert.py
  test_automation_dispatcher.py
  test_automation_scheduler.py
  test_api_automations.py

tests/integration/
  test_automation_phase_5_e2e.py
```

### Modified files

```
pyproject.toml                            -- Add croniter dependency
src/donna/tasks/db_models.py              -- Automation, AutomationRun ORM classes
src/donna/config.py                       -- Automation knobs
config/skills.yaml                        -- Automation defaults
src/donna/notifications/service.py        -- NOTIF_AUTOMATION_ALERT + NOTIF_AUTOMATION_FAILURE constants
src/donna/api/__init__.py                 -- Register /admin/automations router + start scheduler
docs/superpowers/specs/2026-04-15-skill-system-and-challenger-refactor-design.md -- Tick R30-R34 + drift log
docs/phase-1-skill-system-setup.md        -- Phase 5 setup notes
donna-diagrams.html                       -- Add automation pipeline diagram
```

---

## Task 1: Add `croniter` dependency + config knobs

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/donna/config.py`
- Modify: `config/skills.yaml`
- Create: `tests/unit/test_config_automation_knobs.py`

- [ ] **Step 1: Add `croniter` dependency.** In `pyproject.toml`, under `dependencies = [...]`, add after the existing entries (around the `# Skill system (Phase 2)` comment block):

```toml
    # Automation subsystem (Phase 5)
    "croniter>=2.0.0",
```

- [ ] **Step 2: Install the dependency in the dev environment** so tests can import it:

```bash
pip install croniter
```

- [ ] **Step 3: Add automation knobs to `SkillSystemConfig`.** Find the `SkillSystemConfig(BaseModel)` class in `src/donna/config.py` and append these fields (keep all existing fields unchanged):

```python
    # Phase 5 — automation subsystem
    automation_poll_interval_seconds: int = 60
    automation_min_interval_default_seconds: int = 300       # 5 minutes floor
    automation_failure_pause_threshold: int = 5              # consecutive failures -> paused
    automation_max_cost_per_run_default_usd: float = 2.0
```

- [ ] **Step 4: Add the knobs to `config/skills.yaml`.** Append:

```yaml

# Phase 5 — automation subsystem
automation_poll_interval_seconds: 60
automation_min_interval_default_seconds: 300
automation_failure_pause_threshold: 5
automation_max_cost_per_run_default_usd: 2.0
```

- [ ] **Step 5: Write a config test** at `tests/unit/test_config_automation_knobs.py`:

```python
from pathlib import Path

import pytest

from donna.config import SkillSystemConfig, load_skill_system_config


def test_automation_defaults_on_config():
    cfg = SkillSystemConfig()
    assert cfg.automation_poll_interval_seconds == 60
    assert cfg.automation_min_interval_default_seconds == 300
    assert cfg.automation_failure_pause_threshold == 5
    assert cfg.automation_max_cost_per_run_default_usd == 2.0


def test_load_skills_yaml_allows_automation_overrides(tmp_path: Path):
    yaml_path = tmp_path / "skills.yaml"
    yaml_path.write_text(
        "enabled: true\n"
        "automation_poll_interval_seconds: 30\n"
        "automation_failure_pause_threshold: 10\n"
    )
    cfg = load_skill_system_config(tmp_path)
    assert cfg.automation_poll_interval_seconds == 30
    assert cfg.automation_failure_pause_threshold == 10
    assert cfg.automation_min_interval_default_seconds == 300
    assert cfg.automation_max_cost_per_run_default_usd == 2.0
```

- [ ] **Step 6: Run the test and commit.**

```bash
pytest tests/unit/test_config_automation_knobs.py -v
git add pyproject.toml src/donna/config.py config/skills.yaml \
        tests/unit/test_config_automation_knobs.py
git commit -m "feat(config): add croniter dep and Phase 5 automation knobs"
```

DO NOT use `--no-verify`.

---

## Task 2: Alembic migration for `automation` + `automation_run`

**Files:**
- Create: `alembic/versions/add_automation_tables_phase_5.py`

- [ ] **Step 1: Determine current alembic head.** It is `f6a7b8c9d0e1` (from `alembic/versions/promote_seed_skills_to_shadow_primary.py`). This becomes the `down_revision`.

- [ ] **Step 2: Create the migration file** at `alembic/versions/add_automation_tables_phase_5.py`:

```python
"""add automation + automation_run tables (phase 5)

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-04-16
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "automation",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("capability_name", sa.String(length=200), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("trigger_type", sa.String(length=20), nullable=False),
        sa.Column("schedule", sa.String(length=200), nullable=True),
        sa.Column("alert_conditions", sa.JSON(), nullable=False),
        sa.Column("alert_channels", sa.JSON(), nullable=False),
        sa.Column("max_cost_per_run_usd", sa.Float(), nullable=True),
        sa.Column("min_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_via", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(
            ["capability_name"], ["capability.name"],
            name="fk_automation_capability_name",
        ),
    )
    with op.batch_alter_table("automation", schema=None) as batch_op:
        batch_op.create_index("ix_automation_user_id", ["user_id"])
        batch_op.create_index("ix_automation_status", ["status"])
        batch_op.create_index("ix_automation_next_run_at", ["next_run_at"])
        batch_op.create_index("ix_automation_capability_name", ["capability_name"])

    op.create_table(
        "automation_run",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("automation_id", sa.String(length=36), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("execution_path", sa.String(length=20), nullable=False),
        sa.Column("skill_run_id", sa.String(length=36), nullable=True),
        sa.Column("invocation_log_id", sa.String(length=36), nullable=True),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("alert_sent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("alert_content", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["automation_id"], ["automation.id"],
            name="fk_automation_run_automation_id",
        ),
    )
    with op.batch_alter_table("automation_run", schema=None) as batch_op:
        batch_op.create_index("ix_automation_run_automation_id", ["automation_id"])
        batch_op.create_index("ix_automation_run_started_at", ["started_at"])
        batch_op.create_index("ix_automation_run_status", ["status"])


def downgrade() -> None:
    with op.batch_alter_table("automation_run", schema=None) as batch_op:
        batch_op.drop_index("ix_automation_run_status")
        batch_op.drop_index("ix_automation_run_started_at")
        batch_op.drop_index("ix_automation_run_automation_id")
    op.drop_table("automation_run")

    with op.batch_alter_table("automation", schema=None) as batch_op:
        batch_op.drop_index("ix_automation_capability_name")
        batch_op.drop_index("ix_automation_next_run_at")
        batch_op.drop_index("ix_automation_status")
        batch_op.drop_index("ix_automation_user_id")
    op.drop_table("automation")
```

- [ ] **Step 3: Test upgrade + downgrade on a temp DB.**

```bash
TEMP_DB=$(mktemp --suffix=.db)
DONNA_DB_PATH="$TEMP_DB" alembic upgrade head
DONNA_DB_PATH="$TEMP_DB" alembic downgrade f6a7b8c9d0e1
DONNA_DB_PATH="$TEMP_DB" alembic upgrade head
rm -f "$TEMP_DB"
```

Inspect `alembic/env.py` if the env var name differs.

- [ ] **Step 4: Commit.**

```bash
git add alembic/versions/add_automation_tables_phase_5.py
git commit -m "feat(db): add automation + automation_run tables"
```

DO NOT use `--no-verify`.

---

## Task 3: ORM models + dataclass row mappers

**Files:**
- Modify: `src/donna/tasks/db_models.py`
- Create: `src/donna/automations/__init__.py`
- Create: `src/donna/automations/models.py`
- Create: `tests/unit/test_automation_models.py`

- [ ] **Step 1: Write the failing test** at `tests/unit/test_automation_models.py`:

```python
from datetime import datetime, timezone

from donna.automations.models import (
    AutomationRow,
    AutomationRunRow,
    row_to_automation,
    row_to_automation_run,
)


def test_row_to_automation_parses_json_and_datetime():
    now = datetime.now(timezone.utc)
    row = (
        "a1", "nick", "Watch shirt", "Price monitor",
        "product_watch",
        '{"url": "https://cos.com/shirt"}',
        "on_schedule",
        "0 12 * * *",
        '{"all_of": [{"field": "price", "op": "<=", "value": 100}]}',
        '["discord"]',
        2.0, 300, "active",
        now.isoformat(), now.isoformat(),
        1, 0,
        now.isoformat(), now.isoformat(),
        "dashboard",
    )
    auto = row_to_automation(row)
    assert isinstance(auto, AutomationRow)
    assert auto.id == "a1"
    assert auto.inputs == {"url": "https://cos.com/shirt"}
    assert auto.alert_channels == ["discord"]
    assert auto.last_run_at == now
    assert auto.run_count == 1


def test_row_to_automation_run_parses_output():
    now = datetime.now(timezone.utc)
    row = (
        "r1", "a1", now.isoformat(), now.isoformat(),
        "succeeded", "skill", "sk1", None,
        '{"price_usd": 89}', 1, "alert body", None, 0.0,
    )
    run = row_to_automation_run(row)
    assert isinstance(run, AutomationRunRow)
    assert run.output == {"price_usd": 89}
    assert run.alert_sent is True


def test_row_to_automation_run_null_optional_fields():
    now = datetime.now(timezone.utc)
    row = (
        "r1", "a1", now.isoformat(), None,
        "skipped_budget", "claude_native", None, None,
        None, 0, None, None, None,
    )
    run = row_to_automation_run(row)
    assert run.finished_at is None
    assert run.output is None
    assert run.alert_sent is False
```

- [ ] **Step 2: Add the two ORM classes** at the end of `src/donna/tasks/db_models.py`. Keep the existing imports and other classes unchanged:

```python
class Automation(Base):
    """Recurring work item Donna runs on a schedule. See docs/skills-system.md §6.9."""

    __tablename__ = "automation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    capability_name: Mapped[str] = mapped_column(
        String(200), ForeignKey("capability.name"), nullable=False, index=True,
    )
    inputs: Mapped[dict] = mapped_column(JSON, nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(20), nullable=False)
    schedule: Mapped[str | None] = mapped_column(String(200), nullable=True)
    alert_conditions: Mapped[dict] = mapped_column(JSON, nullable=False)
    alert_channels: Mapped[list] = mapped_column(JSON, nullable=False)
    max_cost_per_run_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_via: Mapped[str] = mapped_column(String(20), nullable=False)


class AutomationRun(Base):
    """Single execution of an automation. See docs/skills-system.md §5.12."""

    __tablename__ = "automation_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    automation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("automation.id"), nullable=False, index=True,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    execution_path: Mapped[str] = mapped_column(String(20), nullable=False)
    skill_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    invocation_log_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    alert_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    alert_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
```

- [ ] **Step 3: Create `src/donna/automations/__init__.py`** (package marker with docstring):

```python
"""Automation subsystem — scheduled Donna work on capabilities."""
```

- [ ] **Step 4: Create `src/donna/automations/models.py`**:

```python
"""Automation + AutomationRun dataclass row mappers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

AUTOMATION_COLUMNS = (
    "id", "user_id", "name", "description", "capability_name",
    "inputs", "trigger_type", "schedule", "alert_conditions",
    "alert_channels", "max_cost_per_run_usd", "min_interval_seconds",
    "status", "last_run_at", "next_run_at", "run_count",
    "failure_count", "created_at", "updated_at", "created_via",
)
SELECT_AUTOMATION = ", ".join(AUTOMATION_COLUMNS)

AUTOMATION_RUN_COLUMNS = (
    "id", "automation_id", "started_at", "finished_at", "status",
    "execution_path", "skill_run_id", "invocation_log_id",
    "output", "alert_sent", "alert_content", "error", "cost_usd",
)
SELECT_AUTOMATION_RUN = ", ".join(AUTOMATION_RUN_COLUMNS)


@dataclass(slots=True)
class AutomationRow:
    id: str
    user_id: str
    name: str
    description: str | None
    capability_name: str
    inputs: dict
    trigger_type: str
    schedule: str | None
    alert_conditions: dict
    alert_channels: list
    max_cost_per_run_usd: float | None
    min_interval_seconds: int
    status: str
    last_run_at: datetime | None
    next_run_at: datetime | None
    run_count: int
    failure_count: int
    created_at: datetime
    updated_at: datetime
    created_via: str


@dataclass(slots=True)
class AutomationRunRow:
    id: str
    automation_id: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    execution_path: str
    skill_run_id: str | None
    invocation_log_id: str | None
    output: dict | None
    alert_sent: bool
    alert_content: str | None
    error: str | None
    cost_usd: float | None


def _parse_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def row_to_automation(row: tuple) -> AutomationRow:
    return AutomationRow(
        id=row[0], user_id=row[1], name=row[2], description=row[3],
        capability_name=row[4],
        inputs=_parse_json(row[5]) or {},
        trigger_type=row[6], schedule=row[7],
        alert_conditions=_parse_json(row[8]) or {},
        alert_channels=_parse_json(row[9]) or [],
        max_cost_per_run_usd=row[10],
        min_interval_seconds=row[11],
        status=row[12],
        last_run_at=_parse_dt(row[13]),
        next_run_at=_parse_dt(row[14]),
        run_count=row[15], failure_count=row[16],
        created_at=_parse_dt(row[17]),
        updated_at=_parse_dt(row[18]),
        created_via=row[19],
    )


def row_to_automation_run(row: tuple) -> AutomationRunRow:
    return AutomationRunRow(
        id=row[0], automation_id=row[1],
        started_at=_parse_dt(row[2]),
        finished_at=_parse_dt(row[3]),
        status=row[4], execution_path=row[5],
        skill_run_id=row[6], invocation_log_id=row[7],
        output=_parse_json(row[8]),
        alert_sent=bool(row[9]),
        alert_content=row[10], error=row[11], cost_usd=row[12],
    )
```

- [ ] **Step 5: Run tests and commit.**

```bash
pytest tests/unit/test_automation_models.py -v
git add src/donna/tasks/db_models.py src/donna/automations/__init__.py \
        src/donna/automations/models.py \
        tests/unit/test_automation_models.py
git commit -m "feat(automations): add Automation + AutomationRun ORM and row mappers"
```

---

## Task 4: `AutomationRepository` — CRUD + queries

**Files:**
- Create: `src/donna/automations/repository.py`
- Create: `tests/unit/test_automation_repository.py`

**Purpose:** Single persistence boundary for automation + automation_run rows. Includes a `list_due(now)` query that the scheduler calls every poll interval.

**Public API methods:**
- `create(...)` — returns automation_id
- `get(automation_id)` — returns `AutomationRow | None`
- `list_all(status=None, capability_name=None, limit=100, offset=0)` — list + filter
- `list_due(now)` — rows with `status='active' AND next_run_at <= now`
- `update_fields(automation_id, **fields)` — partial update; JSON-encodes known dict/list cols
- `set_status(automation_id, status)`
- `advance_schedule(automation_id, last_run_at, next_run_at, increment_run_count, increment_failure_count)`
- `reset_failure_count(automation_id)`
- `insert_run(automation_id, started_at, execution_path)` — returns run_id (inserts with status='running')
- `finish_run(run_id, status, output, skill_run_id, invocation_log_id, alert_sent, alert_content, error, cost_usd)`
- `list_runs(automation_id, limit=50, offset=0)` — newest first

- [ ] **Step 1: Write the failing tests** at `tests/unit/test_automation_repository.py`:

```python
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite
import pytest

from donna.automations.repository import AutomationRepository


@pytest.fixture
async def db(tmp_path: Path):
    conn = await aiosqlite.connect(str(tmp_path / "test.db"))
    await conn.executescript("""
        CREATE TABLE capability (
            id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            description TEXT, input_schema TEXT, trigger_type TEXT,
            status TEXT NOT NULL, created_at TEXT NOT NULL,
            created_by TEXT NOT NULL, embedding BLOB
        );
        CREATE TABLE automation (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
            name TEXT NOT NULL, description TEXT,
            capability_name TEXT NOT NULL,
            inputs TEXT NOT NULL, trigger_type TEXT NOT NULL,
            schedule TEXT, alert_conditions TEXT NOT NULL,
            alert_channels TEXT NOT NULL,
            max_cost_per_run_usd REAL, min_interval_seconds INTEGER NOT NULL,
            status TEXT NOT NULL, last_run_at TEXT, next_run_at TEXT,
            run_count INTEGER NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            created_via TEXT NOT NULL
        );
        CREATE TABLE automation_run (
            id TEXT PRIMARY KEY, automation_id TEXT NOT NULL,
            started_at TEXT NOT NULL, finished_at TEXT,
            status TEXT NOT NULL, execution_path TEXT NOT NULL,
            skill_run_id TEXT, invocation_log_id TEXT,
            output TEXT, alert_sent INTEGER NOT NULL DEFAULT 0,
            alert_content TEXT, error TEXT, cost_usd REAL
        );
    """)
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        "INSERT INTO capability (id, name, description, input_schema, "
        "trigger_type, status, created_at, created_by) VALUES "
        "('c1', 'product_watch', 'cap', '{}', 'on_schedule', 'active', ?, 'seed')",
        (now,),
    )
    await conn.commit()
    yield conn
    await conn.close()


async def _create(repo, *, name="Test Auto", status_override=None, next_run_at=None):
    auto_id = await repo.create(
        user_id="nick", name=name, description=None,
        capability_name="product_watch",
        inputs={"url": "https://example.com"},
        trigger_type="on_schedule", schedule="0 12 * * *",
        alert_conditions={"all_of": []},
        alert_channels=["discord"],
        max_cost_per_run_usd=2.0,
        min_interval_seconds=300,
        created_via="dashboard",
        next_run_at=next_run_at,
    )
    if status_override is not None:
        await repo.set_status(auto_id, status_override)
    return auto_id


async def test_create_and_get(db):
    repo = AutomationRepository(db)
    auto_id = await _create(repo, name="Watch shirt")
    row = await repo.get(auto_id)
    assert row is not None
    assert row.name == "Watch shirt"
    assert row.inputs == {"url": "https://example.com"}
    assert row.status == "active"
    assert row.run_count == 0


async def test_get_returns_none_for_missing(db):
    repo = AutomationRepository(db)
    assert await repo.get("missing") is None


async def test_list_all_filters_by_status(db):
    repo = AutomationRepository(db)
    await _create(repo, name="A", status_override="active")
    await _create(repo, name="B", status_override="paused")
    await _create(repo, name="C", status_override="active")

    actives = await repo.list_all(status="active")
    assert {r.name for r in actives} == {"A", "C"}
    paused = await repo.list_all(status="paused")
    assert {r.name for r in paused} == {"B"}


async def test_list_due_returns_rows_with_next_run_before_now_and_active(db):
    repo = AutomationRepository(db)
    now = datetime.now(timezone.utc)
    past = now - timedelta(minutes=5)
    future = now + timedelta(minutes=5)
    await _create(repo, name="Due", next_run_at=past)
    await _create(repo, name="Not yet", next_run_at=future)
    await _create(repo, name="Paused", next_run_at=past, status_override="paused")
    await _create(repo, name="No next", next_run_at=None)

    due = await repo.list_due(now)
    due_names = {r.name for r in due}
    assert "Due" in due_names
    assert "Not yet" not in due_names
    assert "Paused" not in due_names
    assert "No next" not in due_names


async def test_advance_schedule_updates_counters_and_times(db):
    repo = AutomationRepository(db)
    auto_id = await _create(repo, name="Auto")
    now = datetime.now(timezone.utc)
    later = now + timedelta(days=1)

    await repo.advance_schedule(
        automation_id=auto_id, last_run_at=now,
        next_run_at=later, increment_run_count=True,
        increment_failure_count=False,
    )
    row = await repo.get(auto_id)
    assert row.run_count == 1
    assert row.failure_count == 0
    assert row.last_run_at.replace(microsecond=0) == now.replace(microsecond=0)


async def test_advance_schedule_increments_failure_counter(db):
    repo = AutomationRepository(db)
    auto_id = await _create(repo, name="Auto")
    now = datetime.now(timezone.utc)
    await repo.advance_schedule(
        automation_id=auto_id, last_run_at=now, next_run_at=None,
        increment_run_count=True, increment_failure_count=True,
    )
    row = await repo.get(auto_id)
    assert row.run_count == 1
    assert row.failure_count == 1


async def test_reset_failure_count(db):
    repo = AutomationRepository(db)
    auto_id = await _create(repo, name="Auto")
    now = datetime.now(timezone.utc)
    await repo.advance_schedule(
        automation_id=auto_id, last_run_at=now, next_run_at=None,
        increment_run_count=True, increment_failure_count=True,
    )
    await repo.reset_failure_count(auto_id)
    row = await repo.get(auto_id)
    assert row.failure_count == 0


async def test_insert_and_finish_run(db):
    repo = AutomationRepository(db)
    auto_id = await _create(repo, name="Auto")
    started = datetime.now(timezone.utc)

    run_id = await repo.insert_run(
        automation_id=auto_id, started_at=started,
        execution_path="claude_native",
    )
    await repo.finish_run(
        run_id=run_id, status="succeeded",
        output={"price": 42}, skill_run_id=None,
        invocation_log_id="inv-1", alert_sent=True,
        alert_content="price dropped", error=None, cost_usd=0.05,
    )
    runs = await repo.list_runs(auto_id)
    assert len(runs) == 1
    assert runs[0].status == "succeeded"
    assert runs[0].alert_sent is True
    assert runs[0].cost_usd == 0.05


async def test_list_runs_ordered_newest_first(db):
    repo = AutomationRepository(db)
    auto_id = await _create(repo, name="Auto")
    for i in range(3):
        started = datetime.now(timezone.utc) + timedelta(seconds=i)
        run_id = await repo.insert_run(
            automation_id=auto_id, started_at=started,
            execution_path="claude_native",
        )
        await repo.finish_run(
            run_id=run_id, status="succeeded",
            output={"n": i}, skill_run_id=None, invocation_log_id=None,
            alert_sent=False, alert_content=None, error=None, cost_usd=0.0,
        )
    runs = await repo.list_runs(auto_id, limit=10)
    assert len(runs) == 3
    assert [r.output["n"] for r in runs] == [2, 1, 0]
```

- [ ] **Step 2: Implement `src/donna/automations/repository.py`.**

```python
"""AutomationRepository — sole persistence layer for automation rows."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import aiosqlite
import structlog
import uuid6

from donna.automations.models import (
    AUTOMATION_COLUMNS,
    AUTOMATION_RUN_COLUMNS,
    SELECT_AUTOMATION,
    SELECT_AUTOMATION_RUN,
    AutomationRow,
    AutomationRunRow,
    row_to_automation,
    row_to_automation_run,
)

logger = structlog.get_logger()


class AutomationRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._conn = connection

    async def create(
        self,
        *,
        user_id: str,
        name: str,
        description: str | None,
        capability_name: str,
        inputs: dict,
        trigger_type: str,
        schedule: str | None,
        alert_conditions: dict,
        alert_channels: list,
        max_cost_per_run_usd: float | None,
        min_interval_seconds: int,
        created_via: str,
        next_run_at: datetime | None = None,
    ) -> str:
        auto_id = str(uuid6.uuid7())
        now_iso = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            f"INSERT INTO automation ({SELECT_AUTOMATION}) "
            f"VALUES ({', '.join('?' for _ in AUTOMATION_COLUMNS)})",
            (
                auto_id, user_id, name, description, capability_name,
                json.dumps(inputs), trigger_type, schedule,
                json.dumps(alert_conditions), json.dumps(alert_channels),
                max_cost_per_run_usd, min_interval_seconds,
                "active",
                None,
                next_run_at.isoformat() if next_run_at else None,
                0, 0,
                now_iso, now_iso, created_via,
            ),
        )
        await self._conn.commit()
        return auto_id

    async def get(self, automation_id: str) -> AutomationRow | None:
        cursor = await self._conn.execute(
            f"SELECT {SELECT_AUTOMATION} FROM automation WHERE id = ?",
            (automation_id,),
        )
        row = await cursor.fetchone()
        return row_to_automation(row) if row is not None else None

    async def list_all(
        self,
        *,
        status: str | None = None,
        capability_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AutomationRow]:
        clauses: list[str] = []
        params: list = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if capability_name is not None:
            clauses.append("capability_name = ?")
            params.append(capability_name)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cursor = await self._conn.execute(
            f"SELECT {SELECT_AUTOMATION} FROM automation {where} "
            f"ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        rows = await cursor.fetchall()
        return [row_to_automation(r) for r in rows]

    async def list_due(self, now: datetime) -> list[AutomationRow]:
        cursor = await self._conn.execute(
            f"SELECT {SELECT_AUTOMATION} FROM automation "
            f"WHERE status = 'active' AND next_run_at IS NOT NULL "
            f"AND next_run_at <= ? "
            f"ORDER BY next_run_at ASC",
            (now.isoformat(),),
        )
        rows = await cursor.fetchall()
        return [row_to_automation(r) for r in rows]

    async def update_fields(self, automation_id: str, **fields) -> None:
        if not fields:
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        json_cols = {"inputs", "alert_conditions", "alert_channels"}
        dt_cols = {"last_run_at", "next_run_at"}
        set_clauses: list[str] = []
        params: list = []
        for key, value in fields.items():
            if key in json_cols and value is not None:
                set_clauses.append(f"{key} = ?")
                params.append(json.dumps(value))
            elif key in dt_cols and isinstance(value, datetime):
                set_clauses.append(f"{key} = ?")
                params.append(value.isoformat())
            else:
                set_clauses.append(f"{key} = ?")
                params.append(value)
        set_clauses.append("updated_at = ?")
        params.append(now_iso)
        params.append(automation_id)
        await self._conn.execute(
            f"UPDATE automation SET {', '.join(set_clauses)} WHERE id = ?",
            tuple(params),
        )
        await self._conn.commit()

    async def set_status(self, automation_id: str, status: str) -> None:
        await self.update_fields(automation_id, status=status)

    async def advance_schedule(
        self,
        automation_id: str,
        *,
        last_run_at: datetime,
        next_run_at: datetime | None,
        increment_run_count: bool,
        increment_failure_count: bool,
    ) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            "UPDATE automation SET "
            "last_run_at = ?, next_run_at = ?, "
            "run_count = run_count + ?, "
            "failure_count = failure_count + ?, "
            "updated_at = ? WHERE id = ?",
            (
                last_run_at.isoformat(),
                next_run_at.isoformat() if next_run_at else None,
                1 if increment_run_count else 0,
                1 if increment_failure_count else 0,
                now_iso, automation_id,
            ),
        )
        await self._conn.commit()

    async def reset_failure_count(self, automation_id: str) -> None:
        await self.update_fields(automation_id, failure_count=0)

    async def insert_run(
        self,
        *,
        automation_id: str,
        started_at: datetime,
        execution_path: str,
    ) -> str:
        run_id = str(uuid6.uuid7())
        await self._conn.execute(
            f"INSERT INTO automation_run ({SELECT_AUTOMATION_RUN}) "
            f"VALUES ({', '.join('?' for _ in AUTOMATION_RUN_COLUMNS)})",
            (
                run_id, automation_id, started_at.isoformat(),
                None,
                "running", execution_path,
                None, None, None, 0, None, None, None,
            ),
        )
        await self._conn.commit()
        return run_id

    async def finish_run(
        self,
        *,
        run_id: str,
        status: str,
        output: dict | None,
        skill_run_id: str | None,
        invocation_log_id: str | None,
        alert_sent: bool,
        alert_content: str | None,
        error: str | None,
        cost_usd: float | None,
    ) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            "UPDATE automation_run SET "
            "finished_at = ?, status = ?, output = ?, "
            "skill_run_id = ?, invocation_log_id = ?, "
            "alert_sent = ?, alert_content = ?, error = ?, cost_usd = ? "
            "WHERE id = ?",
            (
                now_iso, status,
                json.dumps(output) if output is not None else None,
                skill_run_id, invocation_log_id,
                1 if alert_sent else 0, alert_content, error, cost_usd,
                run_id,
            ),
        )
        await self._conn.commit()

    async def list_runs(
        self,
        automation_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AutomationRunRow]:
        cursor = await self._conn.execute(
            f"SELECT {SELECT_AUTOMATION_RUN} FROM automation_run "
            f"WHERE automation_id = ? "
            f"ORDER BY started_at DESC LIMIT ? OFFSET ?",
            (automation_id, limit, offset),
        )
        rows = await cursor.fetchall()
        return [row_to_automation_run(r) for r in rows]
```

- [ ] **Step 3: Run tests and commit.**

```bash
pytest tests/unit/test_automation_repository.py -v
git add src/donna/automations/repository.py \
        tests/unit/test_automation_repository.py
git commit -m "feat(automations): add AutomationRepository with CRUD + list_due"
```

---

## Task 5: `CronScheduleCalculator`

**Files:**
- Create: `src/donna/automations/cron.py`
- Create: `tests/unit/test_automation_cron.py`

**Purpose:** Convert a cron expression + current time to the next run time. Thin wrapper around `croniter` so the rest of the code doesn't depend directly on it.

- [ ] **Step 1: Write the failing tests** at `tests/unit/test_automation_cron.py`:

```python
from datetime import datetime, timezone

import pytest

from donna.automations.cron import (
    CronScheduleCalculator,
    InvalidCronExpressionError,
)


def test_next_run_daily_at_noon():
    calc = CronScheduleCalculator()
    ref = datetime(2026, 4, 16, 6, 0, tzinfo=timezone.utc)
    nxt = calc.next_run(expression="0 12 * * *", after=ref)
    assert nxt.hour == 12
    assert nxt.day == 16


def test_next_run_wraps_to_next_day():
    calc = CronScheduleCalculator()
    ref = datetime(2026, 4, 16, 15, 0, tzinfo=timezone.utc)
    nxt = calc.next_run(expression="0 12 * * *", after=ref)
    assert nxt.hour == 12
    assert nxt.day == 17


def test_next_run_every_5_minutes():
    calc = CronScheduleCalculator()
    ref = datetime(2026, 4, 16, 12, 3, tzinfo=timezone.utc)
    nxt = calc.next_run(expression="*/5 * * * *", after=ref)
    assert nxt.minute == 5
    assert nxt.hour == 12


def test_invalid_cron_expression_raises():
    calc = CronScheduleCalculator()
    with pytest.raises(InvalidCronExpressionError):
        calc.next_run(expression="not a cron", after=datetime.now(timezone.utc))


def test_result_is_timezone_aware_utc():
    calc = CronScheduleCalculator()
    ref = datetime(2026, 4, 16, 6, 0, tzinfo=timezone.utc)
    nxt = calc.next_run(expression="0 12 * * *", after=ref)
    assert nxt.tzinfo is not None
    assert nxt.utcoffset().total_seconds() == 0
```

- [ ] **Step 2: Implement `src/donna/automations/cron.py`.**

```python
"""CronScheduleCalculator — thin wrapper over croniter for next-run arithmetic."""

from __future__ import annotations

from datetime import datetime, timezone

from croniter import CroniterBadCronError, croniter


class InvalidCronExpressionError(ValueError):
    """Raised when the cron expression cannot be parsed."""


class CronScheduleCalculator:
    def next_run(self, *, expression: str, after: datetime) -> datetime:
        """Compute the next execution time strictly AFTER *after* (timezone-aware UTC)."""
        if after.tzinfo is None:
            after = after.replace(tzinfo=timezone.utc)
        try:
            it = croniter(expression, after)
        except (CroniterBadCronError, ValueError, KeyError) as exc:
            raise InvalidCronExpressionError(
                f"invalid cron expression {expression!r}: {exc}"
            ) from exc
        nxt = it.get_next(datetime)
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=timezone.utc)
        return nxt.astimezone(timezone.utc)
```

- [ ] **Step 3: Run tests and commit.**

```bash
pytest tests/unit/test_automation_cron.py -v
git add src/donna/automations/cron.py tests/unit/test_automation_cron.py
git commit -m "feat(automations): add CronScheduleCalculator using croniter"
```

---

## Task 6: `AlertEvaluator`

**Files:**
- Create: `src/donna/automations/alert.py`
- Create: `tests/unit/test_automation_alert.py`

**Purpose:** Evaluate `alert_conditions` JSON against a run output dict. Spec §6.9 supports leaf predicates (`field`, `op`, `value`) and compound nodes (`all_of`, `any_of`). Ops: `==`, `!=`, `<`, `<=`, `>`, `>=`, `contains`, `exists`. Dotted field paths (`a.b.c`) walk nested dicts.

- [ ] **Step 1: Write the failing tests** at `tests/unit/test_automation_alert.py`:

```python
import pytest

from donna.automations.alert import AlertEvaluator, InvalidAlertExpressionError


def test_leaf_equal_true():
    calc = AlertEvaluator()
    expr = {"field": "price", "op": "==", "value": 100}
    assert calc.evaluate(expr, {"price": 100}) is True


def test_leaf_equal_false():
    calc = AlertEvaluator()
    expr = {"field": "price", "op": "==", "value": 100}
    assert calc.evaluate(expr, {"price": 99}) is False


def test_leaf_less_than_or_equal():
    calc = AlertEvaluator()
    expr = {"field": "price", "op": "<=", "value": 100}
    assert calc.evaluate(expr, {"price": 99}) is True
    assert calc.evaluate(expr, {"price": 100}) is True
    assert calc.evaluate(expr, {"price": 101}) is False


def test_leaf_contains_string():
    calc = AlertEvaluator()
    expr = {"field": "title", "op": "contains", "value": "shirt"}
    assert calc.evaluate(expr, {"title": "red cotton shirt"}) is True
    assert calc.evaluate(expr, {"title": "red cotton pants"}) is False


def test_leaf_contains_list():
    calc = AlertEvaluator()
    expr = {"field": "tags", "op": "contains", "value": "sale"}
    assert calc.evaluate(expr, {"tags": ["sale", "new"]}) is True


def test_leaf_exists():
    calc = AlertEvaluator()
    expr = {"field": "price", "op": "exists"}
    assert calc.evaluate(expr, {"price": 100}) is True
    assert calc.evaluate(expr, {"name": "shirt"}) is False


def test_dotted_field_path():
    calc = AlertEvaluator()
    expr = {"field": "product.price", "op": "<", "value": 50}
    assert calc.evaluate(expr, {"product": {"price": 42}}) is True
    assert calc.evaluate(expr, {"product": {"price": 60}}) is False


def test_missing_field_evaluates_false_for_comparisons():
    calc = AlertEvaluator()
    expr = {"field": "price", "op": "<=", "value": 100}
    assert calc.evaluate(expr, {"name": "x"}) is False


def test_all_of_true_when_every_child_true():
    calc = AlertEvaluator()
    expr = {"all_of": [
        {"field": "price", "op": "<=", "value": 100},
        {"field": "in_stock", "op": "==", "value": True},
    ]}
    assert calc.evaluate(expr, {"price": 50, "in_stock": True}) is True


def test_all_of_false_when_any_child_false():
    calc = AlertEvaluator()
    expr = {"all_of": [
        {"field": "price", "op": "<=", "value": 100},
        {"field": "in_stock", "op": "==", "value": True},
    ]}
    assert calc.evaluate(expr, {"price": 50, "in_stock": False}) is False


def test_any_of_true_when_at_least_one_child_true():
    calc = AlertEvaluator()
    expr = {"any_of": [
        {"field": "price", "op": "<=", "value": 100},
        {"field": "tag", "op": "==", "value": "sale"},
    ]}
    assert calc.evaluate(expr, {"price": 200, "tag": "sale"}) is True
    assert calc.evaluate(expr, {"price": 200, "tag": "regular"}) is False


def test_empty_conditions_return_false():
    calc = AlertEvaluator()
    assert calc.evaluate({}, {"price": 100}) is False


def test_unknown_op_raises():
    calc = AlertEvaluator()
    expr = {"field": "price", "op": "lol", "value": 1}
    with pytest.raises(InvalidAlertExpressionError):
        calc.evaluate(expr, {"price": 1})


def test_unknown_compound_raises():
    calc = AlertEvaluator()
    expr = {"only_of": [{"field": "x", "op": "==", "value": 1}]}
    with pytest.raises(InvalidAlertExpressionError):
        calc.evaluate(expr, {"x": 1})


def test_nested_compound():
    calc = AlertEvaluator()
    expr = {"all_of": [
        {"any_of": [
            {"field": "price", "op": "<=", "value": 100},
            {"field": "is_sale", "op": "==", "value": True},
        ]},
        {"field": "in_stock", "op": "==", "value": True},
    ]}
    assert calc.evaluate(expr, {"price": 80, "is_sale": False, "in_stock": True}) is True
    assert calc.evaluate(expr, {"price": 200, "is_sale": True, "in_stock": True}) is True
    assert calc.evaluate(expr, {"price": 80, "is_sale": False, "in_stock": False}) is False
```

- [ ] **Step 2: Implement `src/donna/automations/alert.py`.**

```python
"""AlertEvaluator — evaluates automation.alert_conditions against a run output.

Spec §6.9 alert-condition DSL:
  - Leaf: {field, op, value} where op in (==, !=, <, <=, >, >=, contains, exists).
  - Compound: {all_of: [child, ...]} or {any_of: [child, ...]}.
  - An empty dict means "no alert conditions" and evaluates to False.
"""

from __future__ import annotations

from typing import Any

_LEAF_OPS = {"==", "!=", "<", "<=", ">", ">=", "contains", "exists"}


class InvalidAlertExpressionError(ValueError):
    """Raised when the alert expression has unknown ops or malformed shape."""


class AlertEvaluator:
    def evaluate(self, expression: Any, output: dict) -> bool:
        if not isinstance(expression, dict) or not expression:
            return False
        return self._check(expression, output)

    def _check(self, node: Any, output: dict) -> bool:
        if not isinstance(node, dict):
            raise InvalidAlertExpressionError(
                f"expected dict, got {type(node).__name__}"
            )
        if "all_of" in node:
            children = node["all_of"]
            if not isinstance(children, list):
                raise InvalidAlertExpressionError("all_of must be a list")
            return all(self._check(c, output) for c in children)
        if "any_of" in node:
            children = node["any_of"]
            if not isinstance(children, list):
                raise InvalidAlertExpressionError("any_of must be a list")
            return any(self._check(c, output) for c in children)
        if "field" in node and "op" in node:
            return self._check_leaf(node, output)
        raise InvalidAlertExpressionError(f"unknown node shape: keys={list(node)}")

    def _check_leaf(self, leaf: dict, output: dict) -> bool:
        op = leaf["op"]
        if op not in _LEAF_OPS:
            raise InvalidAlertExpressionError(f"unknown op {op!r}")
        field_path = leaf["field"]
        present, actual = _walk(output, field_path)
        if op == "exists":
            return present
        if not present:
            return False
        value = leaf.get("value")
        if op == "==":
            return actual == value
        if op == "!=":
            return actual != value
        if op == "contains":
            try:
                return value in actual
            except TypeError:
                return False
        try:
            if op == "<":
                return actual < value
            if op == "<=":
                return actual <= value
            if op == ">":
                return actual > value
            if op == ">=":
                return actual >= value
        except TypeError:
            return False
        return False


def _walk(output: dict, dotted_path: str) -> tuple[bool, Any]:
    """Walk a.b.c into nested dicts. Returns (present, value)."""
    parts = dotted_path.split(".")
    cur: Any = output
    for part in parts:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return False, None
    return True, cur
```

- [ ] **Step 3: Run tests and commit.**

```bash
pytest tests/unit/test_automation_alert.py -v
git add src/donna/automations/alert.py tests/unit/test_automation_alert.py
git commit -m "feat(automations): add AlertEvaluator with dotted paths and 8 ops"
```

---

## Task 7: `AutomationDispatcher` — execute one due automation

**Files:**
- Create: `src/donna/automations/dispatcher.py`
- Create: `tests/unit/test_automation_dispatcher.py`

**Purpose:** Given an `AutomationRow` that is due, execute it once. Handles five paths from spec §6.9:
- Skill available (state in `{shadow_primary, trusted}`) → `SkillExecutor`, records `execution_path = "skill"`.
- Otherwise → `ModelRouter.complete(prompt=..., task_type=capability_name)` claude-native path, records `execution_path = "claude_native"`.
- `BudgetPausedError` → `status = skipped_budget`, `next_run_at` advanced.
- `cost_usd > max_cost_per_run_usd` → `status = failed`, `error = "cost_exceeded"`.
- Execution failure → `status = failed`, `error = str(exc)`.

After the run, run alert conditions (only on success). Increment run_count, failure_count, reset failures on success. Pause automation on consecutive failures ≥ threshold.

**Public API:**

```python
@dataclass(slots=True)
class DispatchReport:
    automation_id: str
    run_id: str | None
    outcome: str              # succeeded | failed | skipped_budget | cost_exceeded | error
    alert_sent: bool
    error: str | None = None


class AutomationDispatcher:
    def __init__(
        self, *,
        connection,                 # aiosqlite.Connection
        repository,                 # AutomationRepository
        model_router,               # ModelRouter
        skill_executor_factory,     # Callable returning SkillExecutor or None
        budget_guard,               # BudgetGuard | None
        alert_evaluator,            # AlertEvaluator
        cron,                       # CronScheduleCalculator
        notifier,                   # NotificationService | None
        config,                     # SkillSystemConfig
    ): ...

    async def dispatch(self, automation: AutomationRow) -> DispatchReport: ...
```

- [ ] **Step 1: Write the failing tests** at `tests/unit/test_automation_dispatcher.py`:

```python
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from donna.automations.alert import AlertEvaluator
from donna.automations.cron import CronScheduleCalculator
from donna.automations.dispatcher import (
    AutomationDispatcher,
    DispatchReport,
)
from donna.automations.repository import AutomationRepository
from donna.config import SkillSystemConfig
from donna.cost.budget import BudgetPausedError


@pytest.fixture
async def db(tmp_path: Path):
    conn = await aiosqlite.connect(str(tmp_path / "test.db"))
    await conn.executescript("""
        CREATE TABLE capability (
            id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            description TEXT, input_schema TEXT, trigger_type TEXT,
            status TEXT NOT NULL, created_at TEXT NOT NULL,
            created_by TEXT NOT NULL, embedding BLOB
        );
        CREATE TABLE skill (
            id TEXT PRIMARY KEY, capability_name TEXT NOT NULL UNIQUE,
            current_version_id TEXT, state TEXT NOT NULL,
            requires_human_gate INTEGER NOT NULL DEFAULT 0,
            baseline_agreement REAL, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE automation (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
            description TEXT, capability_name TEXT NOT NULL,
            inputs TEXT NOT NULL, trigger_type TEXT NOT NULL,
            schedule TEXT, alert_conditions TEXT NOT NULL,
            alert_channels TEXT NOT NULL, max_cost_per_run_usd REAL,
            min_interval_seconds INTEGER NOT NULL,
            status TEXT NOT NULL, last_run_at TEXT, next_run_at TEXT,
            run_count INTEGER NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            created_via TEXT NOT NULL
        );
        CREATE TABLE automation_run (
            id TEXT PRIMARY KEY, automation_id TEXT NOT NULL,
            started_at TEXT NOT NULL, finished_at TEXT,
            status TEXT NOT NULL, execution_path TEXT NOT NULL,
            skill_run_id TEXT, invocation_log_id TEXT,
            output TEXT, alert_sent INTEGER NOT NULL DEFAULT 0,
            alert_content TEXT, error TEXT, cost_usd REAL
        );
    """)
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        "INSERT INTO capability (id, name, description, input_schema, "
        "trigger_type, status, created_at, created_by) VALUES "
        "('c1', 'product_watch', 'cap', '{\"type\":\"object\"}', 'on_schedule', 'active', ?, 'seed')",
        (now,),
    )
    await conn.commit()
    yield conn
    await conn.close()


async def _seed_automation(db, *, alert_conditions=None, max_cost=None):
    repo = AutomationRepository(db)
    auto_id = await repo.create(
        user_id="nick", name="watch shirt", description=None,
        capability_name="product_watch",
        inputs={"url": "https://cos.com/shirt"},
        trigger_type="on_schedule", schedule="0 12 * * *",
        alert_conditions=alert_conditions or {},
        alert_channels=["discord"],
        max_cost_per_run_usd=max_cost,
        min_interval_seconds=300,
        created_via="dashboard",
        next_run_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    return auto_id, await repo.get(auto_id)


def _make_dispatcher(db, **overrides):
    repo = AutomationRepository(db)

    class _ReasonerOutputMeta:
        invocation_id = "inv-reasoner"
        cost_usd = 0.02

    router = overrides.pop("router", AsyncMock())
    if not hasattr(router, "complete") or not callable(router.complete):
        router.complete = AsyncMock(return_value=({"price_usd": 89, "in_stock": True}, _ReasonerOutputMeta()))
    elif not getattr(router.complete, "return_value", None):
        router.complete = AsyncMock(return_value=({"price_usd": 89, "in_stock": True}, _ReasonerOutputMeta()))

    budget_guard = overrides.pop("budget_guard", AsyncMock())
    if not hasattr(budget_guard, "check_pre_call") or not callable(budget_guard.check_pre_call):
        budget_guard.check_pre_call = AsyncMock()

    notifier = overrides.pop("notifier", AsyncMock())
    notifier.dispatch = AsyncMock(return_value=True)

    kwargs = dict(
        connection=db,
        repository=repo,
        model_router=router,
        skill_executor_factory=overrides.pop("skill_executor_factory", lambda: None),
        budget_guard=budget_guard,
        alert_evaluator=AlertEvaluator(),
        cron=CronScheduleCalculator(),
        notifier=notifier,
        config=SkillSystemConfig(),
    )
    kwargs.update(overrides)
    return AutomationDispatcher(**kwargs), repo, router, budget_guard, notifier


async def test_claude_native_succeeds_and_advances_schedule(db):
    _, auto = await _seed_automation(
        db, alert_conditions={"all_of": [{"field": "price_usd", "op": "<=", "value": 100}]},
    )
    dispatcher, repo, router, budget_guard, notifier = _make_dispatcher(db)

    report = await dispatcher.dispatch(auto)

    assert report.outcome == "succeeded"
    assert report.alert_sent is True
    updated = await repo.get(auto.id)
    assert updated.run_count == 1
    assert updated.failure_count == 0
    assert updated.next_run_at is not None
    runs = await repo.list_runs(auto.id)
    assert len(runs) == 1
    assert runs[0].execution_path == "claude_native"
    assert runs[0].output == {"price_usd": 89, "in_stock": True}
    notifier.dispatch.assert_awaited_once()


async def test_skill_path_is_used_when_skill_is_trusted(db):
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO skill (id, capability_name, current_version_id, state, "
        "requires_human_gate, baseline_agreement, created_at, updated_at) "
        "VALUES ('s1', 'product_watch', 'v1', 'trusted', 0, 0.95, ?, ?)",
        (now, now),
    )
    await db.execute(
        "CREATE TABLE skill_version (id TEXT PRIMARY KEY, skill_id TEXT, "
        "version_number INTEGER, yaml_backbone TEXT, step_content TEXT, "
        "output_schemas TEXT, created_by TEXT, changelog TEXT, created_at TEXT)",
    )
    await db.execute(
        "INSERT INTO skill_version (id, skill_id, version_number, yaml_backbone, "
        "step_content, output_schemas, created_by, changelog, created_at) VALUES "
        "('v1', 's1', 1, 'x', '{}', '{}', 'seed', NULL, ?)",
        (now,),
    )
    await db.commit()

    _, auto = await _seed_automation(db)

    executor = MagicMock()
    executor.execute = AsyncMock(return_value=MagicMock(
        status="succeeded", final_output={"price_usd": 42},
        invocation_ids=["inv-skill"],
        total_cost_usd=0.0,
    ))
    dispatcher, repo, *_ = _make_dispatcher(
        db, skill_executor_factory=lambda: executor,
    )
    report = await dispatcher.dispatch(auto)
    assert report.outcome == "succeeded"
    runs = await repo.list_runs(auto.id)
    assert runs[0].execution_path == "skill"


async def test_budget_paused_produces_skipped_budget(db):
    _, auto = await _seed_automation(db)
    budget_guard = AsyncMock()
    budget_guard.check_pre_call = AsyncMock(
        side_effect=BudgetPausedError(daily_spent=30.0, daily_limit=20.0),
    )
    dispatcher, repo, *_ = _make_dispatcher(db, budget_guard=budget_guard)

    report = await dispatcher.dispatch(auto)
    assert report.outcome == "skipped_budget"
    runs = await repo.list_runs(auto.id)
    assert runs == []
    updated = await repo.get(auto.id)
    assert updated.next_run_at is not None


async def test_cost_exceeded_marks_failed(db):
    _, auto = await _seed_automation(db, max_cost=0.01)

    class _BigCostMeta:
        invocation_id = "inv-x"
        cost_usd = 0.50

    router = AsyncMock()
    router.complete = AsyncMock(return_value=({"price_usd": 100}, _BigCostMeta()))
    dispatcher, repo, *_ = _make_dispatcher(db, router=router)
    report = await dispatcher.dispatch(auto)
    assert report.outcome == "cost_exceeded"
    runs = await repo.list_runs(auto.id)
    assert runs[0].status == "failed"
    assert runs[0].error == "cost_exceeded"
    updated = await repo.get(auto.id)
    assert updated.failure_count == 1


async def test_execution_error_marks_failed(db):
    _, auto = await _seed_automation(db)
    router = AsyncMock()
    router.complete = AsyncMock(side_effect=RuntimeError("network broke"))
    dispatcher, repo, *_ = _make_dispatcher(db, router=router)
    report = await dispatcher.dispatch(auto)
    assert report.outcome in ("failed", "error")
    runs = await repo.list_runs(auto.id)
    assert runs[0].status == "failed"
    updated = await repo.get(auto.id)
    assert updated.failure_count == 1


async def test_success_resets_failure_count(db):
    _, auto = await _seed_automation(db)
    repo = AutomationRepository(db)
    await repo.advance_schedule(
        automation_id=auto.id, last_run_at=datetime.now(timezone.utc),
        next_run_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        increment_run_count=True, increment_failure_count=True,
    )
    await repo.advance_schedule(
        automation_id=auto.id, last_run_at=datetime.now(timezone.utc),
        next_run_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        increment_run_count=True, increment_failure_count=True,
    )
    auto = await repo.get(auto.id)
    assert auto.failure_count == 2

    dispatcher, _, *_ = _make_dispatcher(db)
    await dispatcher.dispatch(auto)
    updated = await repo.get(auto.id)
    assert updated.failure_count == 0


async def test_consecutive_failures_pause_automation(db):
    _, auto = await _seed_automation(db)
    repo = AutomationRepository(db)
    threshold = SkillSystemConfig().automation_failure_pause_threshold

    await repo.update_fields(auto.id, failure_count=threshold - 1)
    auto = await repo.get(auto.id)
    assert auto.failure_count == threshold - 1

    router = AsyncMock()
    router.complete = AsyncMock(side_effect=RuntimeError("broken"))
    dispatcher, *_, notifier = _make_dispatcher(db, router=router)
    await dispatcher.dispatch(auto)

    updated = await repo.get(auto.id)
    assert updated.status == "paused"
    assert notifier.dispatch.await_count >= 1


async def test_alert_not_sent_when_conditions_false(db):
    _, auto = await _seed_automation(
        db, alert_conditions={"all_of": [{"field": "price_usd", "op": "<=", "value": 10}]},
    )
    dispatcher, repo, *_, notifier = _make_dispatcher(db)
    notifier.dispatch.reset_mock()
    report = await dispatcher.dispatch(auto)
    assert report.alert_sent is False
    notifier.dispatch.assert_not_awaited()
```

- [ ] **Step 2: Implement `src/donna/automations/dispatcher.py`.**

```python
"""AutomationDispatcher — executes one due automation end-to-end.

Spec §6.9: skill vs claude_native resolution, per-run budget cap,
global BudgetGuard, alert evaluation + dispatch, consecutive-failure pause.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import aiosqlite
import structlog

from donna.automations.models import AutomationRow
from donna.automations.repository import AutomationRepository
from donna.config import SkillSystemConfig
from donna.cost.budget import BudgetPausedError
from donna.notifications.service import CHANNEL_TASKS

logger = structlog.get_logger()


@dataclass(slots=True)
class DispatchReport:
    automation_id: str
    run_id: str | None
    outcome: str
    alert_sent: bool
    error: str | None = None


class AutomationDispatcher:
    def __init__(
        self,
        *,
        connection: aiosqlite.Connection,
        repository: AutomationRepository,
        model_router: Any,
        skill_executor_factory: Callable[[], Any],
        budget_guard: Any,
        alert_evaluator: Any,
        cron: Any,
        notifier: Any,
        config: SkillSystemConfig,
    ) -> None:
        self._conn = connection
        self._repo = repository
        self._router = model_router
        self._skill_executor_factory = skill_executor_factory
        self._budget_guard = budget_guard
        self._alerts = alert_evaluator
        self._cron = cron
        self._notifier = notifier
        self._config = config

    async def dispatch(self, automation: AutomationRow) -> DispatchReport:
        now = datetime.now(timezone.utc)

        try:
            if self._budget_guard is not None:
                await self._budget_guard.check_pre_call(user_id=automation.user_id)
        except BudgetPausedError:
            next_run_at = self._compute_next_run(automation, now)
            await self._repo.advance_schedule(
                automation_id=automation.id,
                last_run_at=now, next_run_at=next_run_at,
                increment_run_count=False, increment_failure_count=False,
            )
            logger.info("automation_skipped_budget", automation_id=automation.id)
            return DispatchReport(
                automation_id=automation.id, run_id=None,
                outcome="skipped_budget", alert_sent=False,
            )

        path = await self._decide_path(automation.capability_name)
        run_id = await self._repo.insert_run(
            automation_id=automation.id, started_at=now,
            execution_path=path,
        )

        output: dict | None = None
        skill_run_id: str | None = None
        invocation_log_id: str | None = None
        cost_usd: float = 0.0
        run_status = "failed"
        error: str | None = None
        alert_sent = False
        alert_content: str | None = None

        try:
            if path == "skill":
                executor = self._skill_executor_factory()
                if executor is None:
                    raise RuntimeError("skill path selected but executor_factory returned None")
                result = await self._execute_skill(executor, automation)
                output = result.final_output if isinstance(result.final_output, dict) else None
                cost_usd = float(getattr(result, "total_cost_usd", 0.0) or 0.0)
                run_status = result.status
                if result.status != "succeeded":
                    error = getattr(result, "error", None) or getattr(result, "escalation_reason", None)
            else:
                parsed, metadata = await self._router.complete(
                    prompt=self._build_prompt(automation),
                    task_type=automation.capability_name,
                    task_id=None,
                    user_id=automation.user_id,
                )
                output = parsed if isinstance(parsed, dict) else {"output": parsed}
                invocation_log_id = getattr(metadata, "invocation_id", None)
                cost_usd = float(getattr(metadata, "cost_usd", 0.0) or 0.0)
                run_status = "succeeded"
        except BudgetPausedError:
            await self._repo.finish_run(
                run_id=run_id, status="skipped_budget",
                output=None, skill_run_id=None, invocation_log_id=None,
                alert_sent=False, alert_content=None, error=None,
                cost_usd=0.0,
            )
            next_run_at = self._compute_next_run(automation, now)
            await self._repo.advance_schedule(
                automation_id=automation.id, last_run_at=now,
                next_run_at=next_run_at,
                increment_run_count=False, increment_failure_count=False,
            )
            return DispatchReport(
                automation_id=automation.id, run_id=run_id,
                outcome="skipped_budget", alert_sent=False,
            )
        except Exception as exc:
            error = str(exc)
            run_status = "failed"
            logger.warning(
                "automation_run_exception",
                automation_id=automation.id, error=error,
            )

        if (
            run_status == "succeeded"
            and automation.max_cost_per_run_usd is not None
            and cost_usd > automation.max_cost_per_run_usd
        ):
            run_status = "failed"
            error = "cost_exceeded"

        if run_status == "succeeded" and output is not None:
            try:
                fires = self._alerts.evaluate(automation.alert_conditions, output)
            except Exception as exc:
                logger.warning(
                    "automation_alert_check_failed",
                    automation_id=automation.id, error=str(exc),
                )
                fires = False
            if fires:
                alert_content = self._render_alert_content(automation, output)
                try:
                    if self._notifier is not None:
                        await self._notifier.dispatch(
                            notification_type="automation_alert",
                            content=alert_content,
                            channel=CHANNEL_TASKS,
                            priority=3,
                        )
                        alert_sent = True
                except Exception:
                    logger.exception(
                        "automation_alert_dispatch_failed",
                        automation_id=automation.id,
                    )

        await self._repo.finish_run(
            run_id=run_id, status=run_status,
            output=output, skill_run_id=skill_run_id,
            invocation_log_id=invocation_log_id,
            alert_sent=alert_sent, alert_content=alert_content,
            error=error, cost_usd=cost_usd,
        )

        run_succeeded = run_status == "succeeded"
        next_run_at = self._compute_next_run(automation, now)
        await self._repo.advance_schedule(
            automation_id=automation.id, last_run_at=now,
            next_run_at=next_run_at,
            increment_run_count=True,
            increment_failure_count=not run_succeeded,
        )
        if run_succeeded:
            await self._repo.reset_failure_count(automation.id)

        if not run_succeeded:
            updated = await self._repo.get(automation.id)
            if (
                updated is not None
                and updated.failure_count >= self._config.automation_failure_pause_threshold
            ):
                await self._repo.set_status(automation.id, "paused")
                pause_msg = (
                    f"Automation '{automation.name}' paused after "
                    f"{updated.failure_count} consecutive failures. "
                    f"Last error: {error or 'unknown'}"
                )
                try:
                    if self._notifier is not None:
                        await self._notifier.dispatch(
                            notification_type="automation_failure",
                            content=pause_msg, channel=CHANNEL_TASKS, priority=4,
                        )
                except Exception:
                    logger.exception(
                        "automation_pause_notification_failed",
                        automation_id=automation.id,
                    )

        outcome = self._classify_outcome(run_status, error)
        return DispatchReport(
            automation_id=automation.id, run_id=run_id,
            outcome=outcome, alert_sent=alert_sent, error=error,
        )

    async def _decide_path(self, capability_name: str) -> str:
        cursor = await self._conn.execute(
            "SELECT state FROM skill WHERE capability_name = ?",
            (capability_name,),
        )
        row = await cursor.fetchone()
        if row is None:
            return "claude_native"
        state = row[0]
        if state in ("shadow_primary", "trusted"):
            return "skill"
        return "claude_native"

    async def _execute_skill(self, executor: Any, automation: AutomationRow) -> Any:
        cursor = await self._conn.execute(
            "SELECT id, capability_name, current_version_id, state, "
            "requires_human_gate, baseline_agreement, created_at, updated_at "
            "FROM skill WHERE capability_name = ?",
            (automation.capability_name,),
        )
        skill_row = await cursor.fetchone()
        if skill_row is None:
            raise RuntimeError("skill not found at dispatch time")
        cursor = await self._conn.execute(
            "SELECT id, skill_id, version_number, yaml_backbone, step_content, "
            "output_schemas, created_by, changelog, created_at "
            "FROM skill_version WHERE id = ?",
            (skill_row[2],),
        )
        version_row = await cursor.fetchone()
        if version_row is None:
            raise RuntimeError("skill version not found")
        from donna.skills.models import (
            row_to_skill,
            row_to_skill_version,
        )
        skill = row_to_skill(skill_row)
        version = row_to_skill_version(version_row)
        return await executor.execute(
            skill=skill, version=version,
            inputs=automation.inputs,
            user_id=automation.user_id,
        )

    def _compute_next_run(self, automation: AutomationRow, now: datetime) -> datetime | None:
        if automation.trigger_type != "on_schedule" or not automation.schedule:
            return None
        try:
            return self._cron.next_run(expression=automation.schedule, after=now)
        except Exception as exc:
            logger.warning(
                "automation_invalid_cron",
                automation_id=automation.id, error=str(exc),
            )
            return None

    def _build_prompt(self, automation: AutomationRow) -> str:
        return (
            f"Execute capability '{automation.capability_name}' with the following inputs. "
            f"Return a strict JSON object matching the capability's output schema.\n\n"
            f"Inputs:\n{json.dumps(automation.inputs, indent=2)}"
        )

    def _render_alert_content(self, automation: AutomationRow, output: dict) -> str:
        return (
            f"Automation '{automation.name}' alert:\n"
            f"Output: {json.dumps(output, indent=2)}"
        )

    @staticmethod
    def _classify_outcome(run_status: str, error: str | None) -> str:
        if run_status == "succeeded":
            return "succeeded"
        if error == "cost_exceeded":
            return "cost_exceeded"
        if run_status == "skipped_budget":
            return "skipped_budget"
        if run_status == "failed":
            return "failed" if error and error != "cost_exceeded" else "error"
        return "error"
```

- [ ] **Step 3: Run tests and commit.**

```bash
pytest tests/unit/test_automation_dispatcher.py -v
git add src/donna/automations/dispatcher.py \
        tests/unit/test_automation_dispatcher.py
git commit -m "feat(automations): add AutomationDispatcher with skill/claude routing"
```

---

## Task 8: `AutomationScheduler` — asyncio poll loop

**Files:**
- Create: `src/donna/automations/scheduler.py`
- Create: `tests/unit/test_automation_scheduler.py`

**Purpose:** Asyncio loop polling `list_due(now)` every `config.automation_poll_interval_seconds`. Calls `AutomationDispatcher.dispatch(row)` for each. Cancellable via `stop()`.

- [ ] **Step 1: Write the failing tests** at `tests/unit/test_automation_scheduler.py`:

```python
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.automations.scheduler import AutomationScheduler


async def test_scheduler_run_once_dispatches_due_automations():
    mock_due_a = MagicMock(id="a1")
    mock_due_b = MagicMock(id="a2")
    repo = MagicMock()
    repo.list_due = AsyncMock(return_value=[mock_due_a, mock_due_b])

    dispatcher = MagicMock()
    dispatcher.dispatch = AsyncMock()

    scheduler = AutomationScheduler(
        repository=repo, dispatcher=dispatcher,
        poll_interval_seconds=60,
        now_fn=lambda: datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc),
        sleep_fn=lambda s: asyncio.sleep(0),
    )
    await scheduler.run_once()
    assert dispatcher.dispatch.await_count == 2


async def test_scheduler_run_once_does_nothing_when_no_due():
    repo = MagicMock()
    repo.list_due = AsyncMock(return_value=[])
    dispatcher = MagicMock()
    dispatcher.dispatch = AsyncMock()

    scheduler = AutomationScheduler(
        repository=repo, dispatcher=dispatcher,
        poll_interval_seconds=60,
        now_fn=lambda: datetime.now(timezone.utc),
        sleep_fn=lambda s: asyncio.sleep(0),
    )
    await scheduler.run_once()
    dispatcher.dispatch.assert_not_awaited()


async def test_scheduler_dispatch_errors_do_not_stop_loop():
    mock_due_a = MagicMock(id="a1")
    mock_due_b = MagicMock(id="a2")
    repo = MagicMock()
    repo.list_due = AsyncMock(return_value=[mock_due_a, mock_due_b])
    dispatcher = MagicMock()
    dispatcher.dispatch = AsyncMock(side_effect=[RuntimeError("broke"), None])

    scheduler = AutomationScheduler(
        repository=repo, dispatcher=dispatcher,
        poll_interval_seconds=60,
        now_fn=lambda: datetime.now(timezone.utc),
        sleep_fn=lambda s: asyncio.sleep(0),
    )
    await scheduler.run_once()
    assert dispatcher.dispatch.await_count == 2


async def test_scheduler_stop_signal_exits_loop():
    repo = MagicMock()
    repo.list_due = AsyncMock(return_value=[])
    dispatcher = MagicMock()

    scheduler = AutomationScheduler(
        repository=repo, dispatcher=dispatcher,
        poll_interval_seconds=60,
        now_fn=lambda: datetime.now(timezone.utc),
        sleep_fn=lambda s: asyncio.sleep(0),
    )
    scheduler.stop()
    await asyncio.wait_for(scheduler.run_forever(), timeout=0.5)


async def test_scheduler_run_forever_polls_until_stopped():
    mock_due = MagicMock(id="a1")
    repo = MagicMock()
    repo.list_due = AsyncMock(return_value=[mock_due])
    dispatcher = MagicMock()
    dispatcher.dispatch = AsyncMock()

    ticks = 0

    async def _sleep(_secs):
        nonlocal ticks
        ticks += 1
        if ticks >= 2:
            scheduler.stop()

    scheduler = AutomationScheduler(
        repository=repo, dispatcher=dispatcher,
        poll_interval_seconds=60,
        now_fn=lambda: datetime.now(timezone.utc),
        sleep_fn=_sleep,
    )
    await asyncio.wait_for(scheduler.run_forever(), timeout=1.0)
    assert dispatcher.dispatch.await_count >= 1
```

- [ ] **Step 2: Implement `src/donna/automations/scheduler.py`.**

```python
"""AutomationScheduler — asyncio poll loop that dispatches due automations."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import structlog

logger = structlog.get_logger()


class AutomationScheduler:
    def __init__(
        self,
        *,
        repository: Any,
        dispatcher: Any,
        poll_interval_seconds: int,
        now_fn: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._repo = repository
        self._dispatcher = dispatcher
        self._poll = poll_interval_seconds
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._sleep_fn = sleep_fn or asyncio.sleep
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    async def run_once(self) -> None:
        now = self._now_fn()
        try:
            due = await self._repo.list_due(now)
        except Exception:
            logger.exception("automation_scheduler_list_due_failed")
            return
        for row in due:
            try:
                await self._dispatcher.dispatch(row)
            except Exception:
                logger.exception(
                    "automation_scheduler_dispatch_failed",
                    automation_id=getattr(row, "id", None),
                )

    async def run_forever(self) -> None:
        while not self._stop:
            await self.run_once()
            if self._stop:
                return
            await self._sleep_fn(self._poll)
```

- [ ] **Step 3: Run tests and commit.**

```bash
pytest tests/unit/test_automation_scheduler.py -v
git add src/donna/automations/scheduler.py \
        tests/unit/test_automation_scheduler.py
git commit -m "feat(automations): add AutomationScheduler poll loop"
```

---

## Task 9: REST API routes under `/admin/automations`

**Files:**
- Create: `src/donna/api/routes/automations.py`
- Create: `tests/unit/test_api_automations.py`
- Modify: `src/donna/api/__init__.py`

**Endpoints:**
- `GET    /admin/automations` — list (filters: status, capability_name).
- `GET    /admin/automations/{id}` — single detail.
- `POST   /admin/automations` — create.
- `PATCH  /admin/automations/{id}` — edit inputs/alert_conditions/alert_channels/schedule/max_cost.
- `POST   /admin/automations/{id}/pause` — set status=paused.
- `POST   /admin/automations/{id}/resume` — set status=active and recompute next_run_at.
- `DELETE /admin/automations/{id}` — soft delete (status=deleted).
- `POST   /admin/automations/{id}/run-now` — immediately dispatch.
- `GET    /admin/automations/{id}/runs` — run history.

- [ ] **Step 1: Write the failing tests** at `tests/unit/test_api_automations.py`.

Use the existing test-client pattern from `tests/unit/test_api_skills.py` (call route handlers directly). One test per endpoint plus error paths (roughly 14 tests):

1. `test_post_create_returns_automation_id` — POST valid body → 201/200, id + next_run_at returned.
2. `test_post_create_rejects_missing_capability` — capability_name not in capability table → 400.
3. `test_post_create_rejects_invalid_cron` — schedule not parseable → 400.
4. `test_get_list_returns_active_by_default` — 2 automations, one active, one paused; GET returns only active unless ?status=all.
5. `test_get_list_filters_by_capability_name` — filter works.
6. `test_get_single_returns_detail` — happy path.
7. `test_get_single_404` — unknown id → 404.
8. `test_patch_updates_fields` — PATCH `{inputs, alert_conditions}` → 200 + persisted.
9. `test_patch_recomputes_next_run_when_schedule_changes` — PATCH schedule → next_run_at recomputed.
10. `test_post_pause_sets_status` → status=paused.
11. `test_post_resume_sets_status_and_schedule` → status=active, next_run_at recomputed.
12. `test_delete_soft_deletes` — status=deleted; list filtered by status='active' excludes it.
13. `test_post_run_now_dispatches_immediately` — dispatcher called with the row.
14. `test_get_runs_returns_history` — 2 runs seeded; endpoint returns them newest first.

- [ ] **Step 2: Implement `src/donna/api/routes/automations.py`** mirroring `src/donna/api/routes/skills.py` style (APIRouter, Pydantic request bodies, `request.app.state.db.connection`).

Key implementation notes:
- Dispatcher via `getattr(request.app.state, "automation_dispatcher", None)` — return 503 if absent (mirrors `/skills/{id}/state`).
- `CronScheduleCalculator` is stateless — instantiate per request (cheap) OR pull via `getattr(request.app.state, "cron_calculator", None)` with fallback.
- POST `/` computes `next_run_at = cron.next_run(expression=schedule, after=now)` at creation time, then passes to `repository.create(...)`.
- PATCH with a schedule change recomputes `next_run_at`.
- DELETE calls `repository.set_status(id, "deleted")`.
- `run-now` loads the row and calls `await dispatcher.dispatch(row)`.
- Response shape for detail: mirror the repository `AutomationRow` dataclass flattened to a dict (the `automations_routes` module should have a `_automation_to_dict(row)` helper).

- [ ] **Step 3: Register the router in `src/donna/api/__init__.py`.** Find the block that registers other admin routers (around `app.include_router(skill_candidates_routes.router, prefix="/admin"...)`) and add:

```python
from donna.api.routes import automations as automations_routes
# later ...
app.include_router(automations_routes.router, prefix="/admin", tags=["automations"])
```

- [ ] **Step 4: Run tests and commit.**

```bash
pytest tests/unit/test_api_automations.py -v
git add src/donna/api/routes/automations.py src/donna/api/__init__.py \
        tests/unit/test_api_automations.py
git commit -m "feat(api): add /admin/automations CRUD, pause/resume, run-now, runs history"
```

---

## Task 10: Notification constants + lifespan wiring

**Files:**
- Modify: `src/donna/notifications/service.py`
- Modify: `src/donna/automations/dispatcher.py`
- Modify: `src/donna/api/__init__.py`

- [ ] **Step 1: Add notification constants.** In `src/donna/notifications/service.py`, find the `NOTIF_*` constants and append:

```python
NOTIF_AUTOMATION_ALERT = "automation_alert"
NOTIF_AUTOMATION_FAILURE = "automation_failure"
```

- [ ] **Step 2: Update `src/donna/automations/dispatcher.py`** to use the constants:

```python
from donna.notifications.service import (
    CHANNEL_TASKS,
    NOTIF_AUTOMATION_ALERT,
    NOTIF_AUTOMATION_FAILURE,
)

# Alert dispatch path:
await self._notifier.dispatch(
    notification_type=NOTIF_AUTOMATION_ALERT,
    ...
)

# Pause path:
await self._notifier.dispatch(
    notification_type=NOTIF_AUTOMATION_FAILURE,
    ...
)
```

- [ ] **Step 3: Wire scheduler into FastAPI lifespan.** In `src/donna/api/__init__.py`, inside `lifespan` — directly after the skill-system wiring block — add:

```python
# Automation subsystem — scheduler + dispatcher
app.state.automation_scheduler = None
app.state.automation_scheduler_task = None
app.state.automation_dispatcher = None

try:
    from donna.automations.alert import AlertEvaluator
    from donna.automations.cron import CronScheduleCalculator
    from donna.automations.dispatcher import AutomationDispatcher
    from donna.automations.repository import AutomationRepository
    from donna.automations.scheduler import AutomationScheduler

    if getattr(app.state, "skill_system_bundle", None) is not None:
        automation_repo = AutomationRepository(db.connection)
        dispatcher = AutomationDispatcher(
            connection=db.connection,
            repository=automation_repo,
            model_router=skill_router,
            skill_executor_factory=lambda: None,
            budget_guard=skill_budget_guard,
            alert_evaluator=AlertEvaluator(),
            cron=CronScheduleCalculator(),
            notifier=getattr(app.state, "notification_service", None),
            config=skill_config,
        )
        app.state.automation_dispatcher = dispatcher
        app.state.automation_repository = automation_repo

        scheduler = AutomationScheduler(
            repository=automation_repo,
            dispatcher=dispatcher,
            poll_interval_seconds=skill_config.automation_poll_interval_seconds,
        )
        cron_task = asyncio.create_task(scheduler.run_forever())
        app.state.automation_scheduler = scheduler
        app.state.automation_scheduler_task = cron_task
        logger.info(
            "automation_scheduler_started",
            poll_interval_seconds=skill_config.automation_poll_interval_seconds,
        )
    else:
        logger.info("automation_scheduler_skipped_skill_system_disabled")
except Exception:
    logger.warning("automation_scheduler_wiring_failed", exc_info=True)
```

And in the shutdown block, after the existing `skill_cron_task.cancel()`:

```python
automation_scheduler = getattr(app.state, "automation_scheduler", None)
automation_task = getattr(app.state, "automation_scheduler_task", None)
if automation_scheduler is not None:
    automation_scheduler.stop()
if automation_task is not None:
    automation_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await automation_task
```

- [ ] **Step 4: Verify the app still boots.**

```bash
pytest tests/unit/test_api_health.py -v
```

- [ ] **Step 5: Commit.**

```bash
git add src/donna/notifications/service.py src/donna/automations/dispatcher.py \
        src/donna/api/__init__.py
git commit -m "feat(api): wire AutomationScheduler into FastAPI lifespan"
```

---

## Task 11: Phase 5 end-to-end integration test

**File:**
- Create: `tests/integration/test_automation_phase_5_e2e.py`

Spec AS-5.1 through AS-5.5. Mirror the style of `tests/integration/test_skill_system_phase_4_e2e.py`.

- [ ] **Step 1: Write one test per AS.** Each uses a fresh in-memory DB fixture with the full Phase 5 schema + mocked router/notifier.

Tests:
- `test_as_5_1_dashboard_post_creates_automation_with_cron_schedule` — POST /admin/automations → verify row written, `next_run_at` computed.
- `test_as_5_2_scheduler_dispatches_due_and_advances_next_run` — dispatcher call produces an automation_run with execution_path=claude_native and alert not sent; `next_run_at` is advanced.
- `test_as_5_3_skill_is_used_once_available` — seed shadow_primary skill for the capability; next dispatch uses execution_path=skill.
- `test_as_5_4_alert_sent_when_condition_true` — alert_conditions fire → `NotificationService.dispatch` called with content.
- `test_as_5_5_consecutive_failures_pause_automation` — 5 failing dispatches → automation status=paused, notifier called.

- [ ] **Step 2: Run.**

```bash
pytest tests/integration/test_automation_phase_5_e2e.py -v
```

Expected: 5 passed.

- [ ] **Step 3: Commit.**

```bash
git add tests/integration/test_automation_phase_5_e2e.py
git commit -m "test(automations): add Phase 5 end-to-end integration test"
```

---

## Task 12: Spec drift log + tick R30–R34

**File:**
- Modify: `docs/superpowers/specs/2026-04-15-skill-system-and-challenger-refactor-design.md`

- [ ] **Step 1: Tick requirements.** Change `[ ]` → `[x]` for R30, R31, R32, R33, R34.

- [ ] **Step 2: Append a Phase 5 closures block** to §8 Drift Log:

```markdown
### Phase 5 closures (2026-04-16)

- **§5.11, §5.12 schema shipped.** `automation` + `automation_run` tables in migration `add_automation_tables_phase_5`.
- **§6.9 execution loop.** `AutomationScheduler` polls `list_due(now)` every 60s. `AutomationDispatcher` is the sole creator of `automation_run` rows. Per-run cost cap enforced after the run; over-budget marks the row `failed` with `error = "cost_exceeded"`. Consecutive failures reaching `config.automation_failure_pause_threshold` transition the automation to `paused` and emit a `NOTIF_AUTOMATION_FAILURE`.
- **Skill vs claude_native.** Dispatcher re-queries `skill.state` at every dispatch, so automations transparently switch to skill execution once the skill reaches `shadow_primary`. AS-5.3.
- **Alert DSL.** `AlertEvaluator` implements the 8 ops from §6.9 (`==`, `!=`, `<`, `<=`, `>`, `>=`, `contains`, `exists`) with `all_of` / `any_of` compound nodes and dotted field paths. Empty `alert_conditions` dict → no alert.
- **min_interval_seconds semantics.** v1 scheduler does not double-enforce min_interval beyond what cron produces. The column is persisted and available for future dashboard-side validation.
- **Manual run (trigger_type=on_manual).** Handled via `POST /admin/automations/{id}/run-now`. These automations have `schedule=null` and `next_run_at=null`; the scheduler never picks them up.
- **NotificationService dependency may be None in v1.** If `app.state.notification_service` is absent, dispatcher calls are short-circuited via `self._notifier is not None` and the run proceeds.
- **Natural-language creation flow deferred.** AS-5.1 via Discord ("watch this URL daily") depends on a challenger refactor that outputs `trigger_type=on_schedule`. The backend endpoint is in place from day one for the dashboard; the challenger adapter is a downstream task.
```

- [ ] **Step 3: Commit.**

```bash
git add docs/superpowers/specs/2026-04-15-skill-system-and-challenger-refactor-design.md
git commit -m "docs(spec): tick Phase 5 requirements and record drift log closures"
```

---

## Task 13: Setup doc + diagram

**Files:**
- Modify: `docs/phase-1-skill-system-setup.md`
- Modify: `donna-diagrams.html`

- [ ] **Step 1: Append a "## Phase 5 — Automation Subsystem" section** to the setup doc covering:
- P5.1 New tables: `automation`, `automation_run`.
- P5.2 Components: `AutomationRepository`, `CronScheduleCalculator`, `AlertEvaluator`, `AutomationDispatcher`, `AutomationScheduler`.
- P5.3 Config knobs: `automation_poll_interval_seconds`, `automation_min_interval_default_seconds`, `automation_failure_pause_threshold`, `automation_max_cost_per_run_default_usd`.
- P5.4 Dependency: `croniter`.
- P5.5 Wiring: lifespan instantiates scheduler as a background task when the skill system is enabled.
- P5.6 New API routes under `/admin/automations`.
- P5.7 Deferred items: event triggers (OOS-1), composition (OOS-3), sharing (OOS-7); dashboard UI; Discord natural-language creation.

- [ ] **Step 2: Add a mermaid diagram panel** to `donna-diagrams.html` for the automation pipeline. Follow the existing Phase 4 diagram's card pattern (`<script type="text/plain" class="src">` + `renderInto()` call):

```
flowchart LR
    Scheduler[AutomationScheduler poll every 60s] --> List[repo.list_due]
    List -->|due automations| Dispatcher[AutomationDispatcher]
    Dispatcher --> BudgetGate{BudgetGuard check}
    BudgetGate -->|paused| SkipBudget[skipped_budget]
    BudgetGate -->|ok| PathDecide{skill state?}
    PathDecide -->|shadow_primary or trusted| SkillExec[SkillExecutor]
    PathDecide -->|else| Claude[ModelRouter.complete]
    SkillExec --> CostCap{cost > cap?}
    Claude --> CostCap
    CostCap -->|yes| Failed[failed cost_exceeded]
    CostCap -->|no| AlertCheck[AlertEvaluator]
    AlertCheck -->|fires| Notifier[NotificationService]
    AlertCheck -->|quiet| Persist[finish_run + advance_schedule]
    Notifier --> Persist
    Persist --> Paused{failure_count >= threshold?}
    Paused -->|yes| PauseAuto[status=paused + notify]
    Paused -->|no| Done[next_run_at advanced]
```

- [ ] **Step 3: Commit.**

```bash
git add docs/phase-1-skill-system-setup.md donna-diagrams.html
git commit -m "docs(skills): Phase 5 setup notes and automation pipeline diagram"
```

---

## Self-Review

After completing all tasks:

```bash
pytest tests/unit/ -v
pytest tests/integration/ -v
```

Checks:
- [ ] All new Phase 5 unit tests pass.
- [ ] Phase 5 E2E test passes (5 scenarios).
- [ ] Pre-existing 5 failures unchanged.
- [ ] `grep -rn "INSERT INTO automation_run" src/donna/` — only `AutomationRepository.insert_run` matches.
- [ ] `grep -rn "UPDATE automation" src/donna/` — only `AutomationRepository` matches.
- [ ] Drift log has Phase 5 block; R30–R34 all `[x]`.

---

## Phase 5 Acceptance Scenarios (from spec §7)

- **AS-5.1:** Dashboard POST creates automation with cron schedule. ✓ (Tasks 3–4, 9).
- **AS-5.2:** Scheduler dispatches a due automation via claude-native, no alert. ✓ (Tasks 7, 8, 11).
- **AS-5.3:** Once skill reaches shadow_primary, next run uses skill path. ✓ (Task 7 `_decide_path`).
- **AS-5.4:** Alert condition true → notification dispatched. ✓ (Tasks 6, 7).
- **AS-5.5:** 5 consecutive failures → automation paused + notification. ✓ (Task 7).

---

## Notes for the Implementer

- **Task order matters.** Tasks 1–3 set up schema + ORM. Task 4 ships the repository. Tasks 5–6 are pure-logic helpers. Task 7 composes them. Task 8 wraps the dispatcher in a scheduler. Task 9 is REST. Task 10 wires the lifespan. Tasks 11–13 are test, spec, docs.

- **Skill executor factory is None by default.** Like Phase 3/4, the lifespan passes `skill_executor_factory=lambda: None`. If an automation resolves to the skill path but the factory returns None, the dispatcher raises a run-level error. That automation will fail until a real executor is wired in — documented in the Phase 5 drift log.

- **min_interval_seconds.** Spec describes this as "rate limit floor." Phase 5 v1 persists the column but does not add a second enforcement layer on top of the cron schedule. Future dashboard-side validation can reject cron expressions that would fire more often than min_interval.

- **Discord natural-language flow.** AS-5.1 mentions Discord "watch this URL daily." The challenger refactor that distinguishes `on_schedule` from `on_message` is a downstream dependency. Phase 5 ships the repository and REST endpoints; Discord flow reuses them via the chat adapter.

- **BudgetPausedError can fire twice.** Once at `check_pre_call` (before we start the run row) and once mid-call inside `router.complete`. Both paths collapse to `status = skipped_budget`. Test coverage exercises only the pre-call case; mid-call is structurally the same path.

- **Cost cap is enforced post-run.** We can't know the cost upfront. If `metadata.cost_usd > cap`, the run is still logged (the Claude call already happened) but marked `failed` with `error = "cost_exceeded"`. The user sees the row in the history and the automation counts this as a failure.
