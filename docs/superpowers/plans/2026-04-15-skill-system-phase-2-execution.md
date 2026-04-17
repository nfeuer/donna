# Skill System Phase 2 — Execution Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the Phase 1 single-step `SkillExecutor` into a full multi-step execution engine with deterministic tool dispatch, a minimal flow-control DSL (`for_each`, `retry`, `escalate`), triage-driven failure handling, per-run persistence, and fixture validation.

**Architecture:** Three new DB tables (`skill_run`, `skill_step_result`, `skill_fixture`) persist every skill execution and its per-step outputs. A central `ToolRegistry` maps tool names to async callables; the new `ToolDispatcher` runs declared tool invocations from YAML with retry policies and caches raw blobs in `skill_run.tool_result_cache`. The executor processes steps of three kinds (`llm`, `tool`, `mixed`), rendering Jinja2 templates with state+inputs, validating step outputs against JSON schemas, and handling the `escalate` signal. On any runtime failure, the `TriageAgent` receives structured context and returns one of four decisions: retry, skip, escalate-to-Claude, or alert. A fixture harness loads test cases from the `skills/<capability>/fixtures/` directory and validates skill behavior against them. One multi-step demonstration capability (`fetch_and_summarize`) exercises the full pipeline end-to-end; Phase 1 seed skills are left single-step because they don't need multi-step decomposition.

**Tech Stack:** Python 3.12 async, SQLAlchemy 2.x + Alembic, aiosqlite, Jinja2 (already used in Phase 1), httpx (for `web_fetch` tool), pytest + pytest-asyncio, structlog, FastAPI (for dashboard routes).

**Spec alignment:** This phase implements §6.2 multi-step execution, §6.3 skill file format, §6.4 SkillExecutor, §6.8 Triage Agent, and the handoff contract items in §7 Phase 2. The DSL is minimal per the spec — no `if`, no `repeat_until`, no nested primitives (deferred per OOS-9 and OOS-10).

**Dependencies from Phase 1:** `CapabilityRegistry`, `CapabilityMatcher`, `ChallengerAgent.match_and_extract`, `LocalLLMInputExtractor`, `SkillDatabase`, the `skill` + `skill_version` + `capability` tables, all dataclasses from `donna.capabilities.models` and `donna.skills.models`, and the single-step `SkillExecutor` (which we replace in Task 9).

**Phase 2 invariants (must hold after every task):**
- Phase 1 dispatcher and legacy task flow continue to work unchanged.
- `skill_system.enabled = false` is still a safe default — nothing Phase 2 introduces runs on real traffic until the flag is on.
- Every `SkillExecutor.execute` call writes a `skill_run` row, even on failure.
- Triage is invoked on every runtime failure and its decision is logged.
- All unit tests pass after each task (no regressions).

---

## File Structure

### New files

```
alembic/versions/
  add_skill_run_tables_phase_2.py        -- Migration: skill_run, skill_step_result, skill_fixture

src/donna/skills/
  runs.py                                -- SkillRunRow, SkillStepResultRow dataclasses + row mappers
  tool_registry.py                       -- ToolRegistry: name → callable, allowlist enforcement
  tool_dispatch.py                       -- Executes declared tool invocations, Jinja-renders args, retry
  dsl.py                                 -- for_each primitive evaluator + on_failure policies
  triage.py                              -- TriageAgent: 4-decision failure handler (local LLM)
  fixtures.py                            -- Fixture loader + validate_against_fixtures harness
  run_persistence.py                     -- SkillRunRepository: writes skill_run and skill_step_result rows

src/donna/skills/tools/                  -- Concrete tool implementations
  __init__.py
  web_fetch.py                           -- httpx-based URL fetcher (demonstration tool)

skills/fetch_and_summarize/              -- Multi-step demonstration skill
  skill.yaml
  steps/
    plan.md
    summarize.md
  schemas/
    plan_v1.json
    summarize_v1.json
  fixtures/
    basic_html.json
    empty_response.json

tests/unit/test_skills_runs.py
tests/unit/test_skills_tool_registry.py
tests/unit/test_skills_tool_dispatch.py
tests/unit/test_skills_dsl.py
tests/unit/test_skills_tools_web_fetch.py
tests/unit/test_skills_multistep_executor.py
tests/unit/test_skills_triage.py
tests/unit/test_skills_fixtures.py
tests/unit/test_skills_run_persistence.py
tests/unit/test_api_skill_runs.py
tests/integration/test_skill_system_phase_2_e2e.py
```

### Modified files

```
pyproject.toml                           -- Add httpx if not already present
src/donna/tasks/db_models.py             -- Add SkillRun, SkillStepResult, SkillFixture ORM
src/donna/skills/executor.py             -- REPLACE Phase 1 single-step with multi-step executor
src/donna/skills/__init__.py             -- Export new classes
src/donna/api/routes/skills.py           -- Add runs + step results endpoints
config/task_types.yaml                   -- Add "triage_failure" task type for local LLM
config/donna_models.yaml                 -- Add routing entry for triage_failure
```

---

## Task 1: Alembic migration — `skill_run`, `skill_step_result`, `skill_fixture`

**Files:**
- Create: `alembic/versions/add_skill_run_tables_phase_2.py`

**Schema (all new tables):**

- `skill_run` — one per execution. Includes `state_object` JSON, `tool_result_cache` JSON (map of cache_id → blob-as-base64-string), total timing, status, escalation reason.
- `skill_step_result` — one per step. Links back to `skill_run` and optionally to `invocation_log` (for LLM steps).
- `skill_fixture` — test cases per skill. Sourced from Claude-generated, human-written, or captured-from-run.

- [ ] **Step 1: Find current Alembic head**

```bash
grep -E "^(revision|down_revision)" alembic/versions/seed_skill_system_phase_1.py
```

Should be `revision = "b2c3d4e5f6a7"`. This becomes the `down_revision` for the new migration.

- [ ] **Step 2: Create the migration file**

`alembic/versions/add_skill_run_tables_phase_2.py`:

```python
"""add skill run tables phase 2

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-15
"""
from __future__ import annotations

from typing import Union
import sqlalchemy as sa
from alembic import op

revision = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_run",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("skill_id", sa.String(length=36), nullable=False),
        sa.Column("skill_version_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("automation_run_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("total_latency_ms", sa.Integer(), nullable=True),
        sa.Column("total_cost_usd", sa.Float(), nullable=True),
        sa.Column("state_object", sa.JSON(), nullable=False),
        sa.Column("tool_result_cache", sa.JSON(), nullable=True),
        sa.Column("final_output", sa.JSON(), nullable=True),
        sa.Column("escalation_reason", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["skill_id"], ["skill.id"], name="fk_skill_run_skill_id"),
        sa.ForeignKeyConstraint(["skill_version_id"], ["skill_version.id"], name="fk_skill_run_version_id"),
    )
    with op.batch_alter_table("skill_run", schema=None) as batch_op:
        batch_op.create_index("ix_skill_run_skill_id", ["skill_id"])
        batch_op.create_index("ix_skill_run_status", ["status"])
        batch_op.create_index("ix_skill_run_started_at", ["started_at"])

    op.create_table(
        "skill_step_result",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("skill_run_id", sa.String(length=36), nullable=False),
        sa.Column("step_name", sa.String(length=100), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("step_kind", sa.String(length=20), nullable=False),
        sa.Column("invocation_log_id", sa.String(length=36), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("tool_calls", sa.JSON(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("validation_status", sa.String(length=30), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["skill_run_id"], ["skill_run.id"], name="fk_step_result_run_id"),
    )
    with op.batch_alter_table("skill_step_result", schema=None) as batch_op:
        batch_op.create_index("ix_skill_step_result_run_id", ["skill_run_id"])

    op.create_table(
        "skill_fixture",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("skill_id", sa.String(length=36), nullable=False),
        sa.Column("case_name", sa.String(length=200), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("expected_output_shape", sa.JSON(), nullable=True),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("captured_run_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["skill_id"], ["skill.id"], name="fk_skill_fixture_skill_id"),
    )
    with op.batch_alter_table("skill_fixture", schema=None) as batch_op:
        batch_op.create_index("ix_skill_fixture_skill_id", ["skill_id"])


def downgrade() -> None:
    with op.batch_alter_table("skill_fixture", schema=None) as batch_op:
        batch_op.drop_index("ix_skill_fixture_skill_id")
    op.drop_table("skill_fixture")

    with op.batch_alter_table("skill_step_result", schema=None) as batch_op:
        batch_op.drop_index("ix_skill_step_result_run_id")
    op.drop_table("skill_step_result")

    with op.batch_alter_table("skill_run", schema=None) as batch_op:
        batch_op.drop_index("ix_skill_run_started_at")
        batch_op.drop_index("ix_skill_run_status")
        batch_op.drop_index("ix_skill_run_skill_id")
    op.drop_table("skill_run")
```

- [ ] **Step 3: Test the migration**

```bash
DONNA_DB_PATH=/tmp/donna_test_p2.db alembic upgrade head
sqlite3 /tmp/donna_test_p2.db ".tables"
```

Expected: includes `skill_fixture skill_run skill_step_result` alongside Phase 1 tables.

- [ ] **Step 4: Test downgrade**

```bash
DONNA_DB_PATH=/tmp/donna_test_p2.db alembic downgrade b2c3d4e5f6a7
sqlite3 /tmp/donna_test_p2.db ".tables"
```

Expected: three new tables gone, Phase 1 tables still present.

- [ ] **Step 5: Clean up**

```bash
DONNA_DB_PATH=/tmp/donna_test_p2.db alembic upgrade head
rm /tmp/donna_test_p2.db
```

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/add_skill_run_tables_phase_2.py
git commit -m "feat(db): add skill_run, skill_step_result, skill_fixture tables"
```

---

## Task 2: SQLAlchemy ORM models for the new tables

**Files:**
- Modify: `src/donna/tasks/db_models.py`
- Create: `tests/unit/test_skills_runs_orm.py`

- [ ] **Step 1: Write the test**

```python
# tests/unit/test_skills_runs_orm.py
from datetime import datetime, timezone

from donna.tasks.db_models import SkillRun, SkillStepResult, SkillFixture


def test_skill_run_construction():
    run = SkillRun(
        id="r1", skill_id="s1", skill_version_id="v1",
        task_id=None, automation_run_id=None,
        status="running", total_latency_ms=None, total_cost_usd=None,
        state_object={}, tool_result_cache=None, final_output=None,
        escalation_reason=None, error=None,
        user_id="nick",
        started_at=datetime.now(timezone.utc), finished_at=None,
    )
    assert run.status == "running"
    assert run.state_object == {}


def test_skill_step_result_construction():
    step = SkillStepResult(
        id="sr1", skill_run_id="r1", step_name="extract", step_index=0,
        step_kind="llm", invocation_log_id="inv-1",
        prompt_tokens=100, output={"title": "x"},
        tool_calls=None, latency_ms=50,
        validation_status="valid", error=None,
        created_at=datetime.now(timezone.utc),
    )
    assert step.step_name == "extract"


def test_skill_fixture_construction():
    fix = SkillFixture(
        id="f1", skill_id="s1", case_name="basic",
        input={"raw_text": "hello"},
        expected_output_shape={"title": "string"},
        source="human_written", captured_run_id=None,
        created_at=datetime.now(timezone.utc),
    )
    assert fix.source == "human_written"
```

- [ ] **Step 2: Run test — expect ImportError**

```bash
pytest tests/unit/test_skills_runs_orm.py -v
```

- [ ] **Step 3: Add ORM classes to `src/donna/tasks/db_models.py`**

At the end of the file (after `SkillStateTransition`), add:

```python
class SkillRun(Base):
    __tablename__ = "skill_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    skill_id: Mapped[str] = mapped_column(String(36), ForeignKey("skill.id"), nullable=False, index=True)
    skill_version_id: Mapped[str] = mapped_column(String(36), ForeignKey("skill_version.id"), nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    automation_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    total_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    state_object: Mapped[dict] = mapped_column(JSON, nullable=False)
    tool_result_cache: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    final_output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    escalation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SkillStepResult(Base):
    __tablename__ = "skill_step_result"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    skill_run_id: Mapped[str] = mapped_column(String(36), ForeignKey("skill_run.id"), nullable=False, index=True)
    step_name: Mapped[str] = mapped_column(String(100), nullable=False)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    step_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    invocation_log_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tool_calls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validation_status: Mapped[str] = mapped_column(String(30), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SkillFixture(Base):
    __tablename__ = "skill_fixture"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    skill_id: Mapped[str] = mapped_column(String(36), ForeignKey("skill.id"), nullable=False, index=True)
    case_name: Mapped[str] = mapped_column(String(200), nullable=False)
    input: Mapped[dict] = mapped_column(JSON, nullable=False)
    expected_output_shape: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    captured_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

- [ ] **Step 4: Run test — expect pass**

```bash
pytest tests/unit/test_skills_runs_orm.py -v
```

- [ ] **Step 5: Verify no regressions**

```bash
pytest tests/unit/ -m "not slow" -q
```

- [ ] **Step 6: Commit**

```bash
git add src/donna/tasks/db_models.py tests/unit/test_skills_runs_orm.py
git commit -m "feat(db-models): add SkillRun, SkillStepResult, SkillFixture ORM"
```

---

## Task 3: Dataclasses and row mappers for runs

**Files:**
- Create: `src/donna/skills/runs.py`
- Create: `tests/unit/test_skills_runs.py`

- [ ] **Step 1: Write the test**

```python
# tests/unit/test_skills_runs.py
from datetime import datetime, timezone

from donna.skills.runs import (
    SkillRunRow, SkillStepResultRow,
    row_to_skill_run, row_to_step_result,
)


def test_skill_run_row_basic():
    row = SkillRunRow(
        id="r1", skill_id="s1", skill_version_id="v1",
        task_id=None, automation_run_id=None,
        status="succeeded", total_latency_ms=100, total_cost_usd=0.0,
        state_object={"extract": {"title": "x"}}, tool_result_cache=None,
        final_output={"title": "x"}, escalation_reason=None, error=None,
        user_id="nick",
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    assert row.status == "succeeded"


def test_row_to_skill_run_parses_json_fields():
    raw = (
        "r1", "s1", "v1", None, None, "succeeded", 100, 0.0,
        '{"extract": {"title": "x"}}', None,
        '{"title": "x"}', None, None, "nick",
        "2026-04-15T00:00:00+00:00", "2026-04-15T00:00:01+00:00",
    )
    run = row_to_skill_run(raw)
    assert run.state_object == {"extract": {"title": "x"}}
    assert run.final_output == {"title": "x"}
    assert run.started_at.year == 2026


def test_row_to_step_result_parses_tool_calls():
    raw = (
        "sr1", "r1", "extract", 0, "llm", "inv-1",
        100, '{"title": "x"}', '[{"tool": "web_fetch", "args": {"url": "x"}}]',
        50, "valid", None, "2026-04-15T00:00:00+00:00",
    )
    step = row_to_step_result(raw)
    assert step.output == {"title": "x"}
    assert step.tool_calls == [{"tool": "web_fetch", "args": {"url": "x"}}]
```

- [ ] **Step 2: Run test — expect ImportError**

- [ ] **Step 3: Create `src/donna/skills/runs.py`**

```python
"""Dataclasses and row mappers for skill_run and skill_step_result."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

SKILL_RUN_COLUMNS = (
    "id", "skill_id", "skill_version_id", "task_id", "automation_run_id",
    "status", "total_latency_ms", "total_cost_usd",
    "state_object", "tool_result_cache", "final_output",
    "escalation_reason", "error", "user_id",
    "started_at", "finished_at",
)
SELECT_SKILL_RUN = ", ".join(SKILL_RUN_COLUMNS)

SKILL_STEP_RESULT_COLUMNS = (
    "id", "skill_run_id", "step_name", "step_index", "step_kind",
    "invocation_log_id", "prompt_tokens", "output", "tool_calls",
    "latency_ms", "validation_status", "error", "created_at",
)
SELECT_SKILL_STEP_RESULT = ", ".join(SKILL_STEP_RESULT_COLUMNS)


@dataclass(slots=True)
class SkillRunRow:
    id: str
    skill_id: str
    skill_version_id: str
    task_id: str | None
    automation_run_id: str | None
    status: str
    total_latency_ms: int | None
    total_cost_usd: float | None
    state_object: dict
    tool_result_cache: dict | None
    final_output: dict | None
    escalation_reason: str | None
    error: str | None
    user_id: str
    started_at: datetime
    finished_at: datetime | None


@dataclass(slots=True)
class SkillStepResultRow:
    id: str
    skill_run_id: str
    step_name: str
    step_index: int
    step_kind: str
    invocation_log_id: str | None
    prompt_tokens: int | None
    output: dict | None
    tool_calls: list | None
    latency_ms: int | None
    validation_status: str
    error: str | None
    created_at: datetime


def row_to_skill_run(row: tuple) -> SkillRunRow:
    return SkillRunRow(
        id=row[0], skill_id=row[1], skill_version_id=row[2],
        task_id=row[3], automation_run_id=row[4],
        status=row[5], total_latency_ms=row[6], total_cost_usd=row[7],
        state_object=_parse_json(row[8]) or {},
        tool_result_cache=_parse_json(row[9]),
        final_output=_parse_json(row[10]),
        escalation_reason=row[11], error=row[12], user_id=row[13],
        started_at=_parse_dt(row[14]),
        finished_at=_parse_dt(row[15]) if row[15] is not None else None,
    )


def row_to_step_result(row: tuple) -> SkillStepResultRow:
    return SkillStepResultRow(
        id=row[0], skill_run_id=row[1], step_name=row[2],
        step_index=row[3], step_kind=row[4],
        invocation_log_id=row[5], prompt_tokens=row[6],
        output=_parse_json(row[7]),
        tool_calls=_parse_json_list(row[8]),
        latency_ms=row[9], validation_status=row[10], error=row[11],
        created_at=_parse_dt(row[12]),
    )


def _parse_json(value: Any) -> dict | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    return json.loads(value)


def _parse_json_list(value: Any) -> list | None:
    if value is None:
        return None
    if isinstance(value, list):
        return value
    return json.loads(value)


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)
```

- [ ] **Step 4: Run test — expect pass**

- [ ] **Step 5: Commit**

```bash
git add src/donna/skills/runs.py tests/unit/test_skills_runs.py
git commit -m "feat(skills): add SkillRunRow and SkillStepResultRow dataclasses"
```

---

## Task 4: ToolRegistry with allowlist enforcement

**Files:**
- Create: `src/donna/skills/tool_registry.py`
- Create: `tests/unit/test_skills_tool_registry.py`

The registry holds async callables by name. Each skill step declares an allowlist (`tools: [name1, name2]`); the executor refuses dispatches not in the allowlist.

- [ ] **Step 1: Write test**

```python
# tests/unit/test_skills_tool_registry.py
import pytest

from donna.skills.tool_registry import ToolRegistry, ToolNotAllowedError, ToolNotFoundError


async def _mock_tool(**kwargs):
    return {"echo": kwargs}


async def test_register_and_dispatch():
    registry = ToolRegistry()
    registry.register("mock_tool", _mock_tool)
    result = await registry.dispatch(
        tool_name="mock_tool",
        args={"x": 1},
        allowed_tools=["mock_tool"],
    )
    assert result == {"echo": {"x": 1}}


async def test_dispatch_respects_allowlist():
    registry = ToolRegistry()
    registry.register("mock_tool", _mock_tool)
    with pytest.raises(ToolNotAllowedError, match="not in step allowlist"):
        await registry.dispatch(
            tool_name="mock_tool",
            args={},
            allowed_tools=["other_tool"],
        )


async def test_dispatch_raises_on_unknown_tool():
    registry = ToolRegistry()
    with pytest.raises(ToolNotFoundError):
        await registry.dispatch(tool_name="missing", args={}, allowed_tools=["missing"])


async def test_register_overwrites_existing():
    registry = ToolRegistry()
    registry.register("tool", _mock_tool)

    async def other(**kwargs):
        return {"v": 2}

    registry.register("tool", other)
    result = await registry.dispatch("tool", {}, allowed_tools=["tool"])
    assert result == {"v": 2}


async def test_list_tool_names():
    registry = ToolRegistry()
    registry.register("a", _mock_tool)
    registry.register("b", _mock_tool)
    assert sorted(registry.list_tool_names()) == ["a", "b"]
```

- [ ] **Step 2: Run test — expect ImportError**

- [ ] **Step 3: Create `src/donna/skills/tool_registry.py`**

```python
"""Central registry of skill-layer tools.

Phase 2: tools are registered at app startup via `register()`. Skills
declare per-step allowlists in YAML; the executor calls `dispatch()`
with the allowlist, which enforces that the tool is permitted on the
requesting step.

Tools are async callables accepting keyword arguments and returning a
JSON-serializable dict.
"""

from __future__ import annotations

from typing import Awaitable, Callable

import structlog

logger = structlog.get_logger()


ToolCallable = Callable[..., Awaitable[dict]]


class ToolNotFoundError(Exception):
    """Raised when a skill asks for a tool that isn't registered."""


class ToolNotAllowedError(Exception):
    """Raised when a skill step tries to dispatch a tool not in its allowlist."""


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolCallable] = {}

    def register(self, name: str, callable_: ToolCallable) -> None:
        if name in self._tools:
            logger.info("tool_overwritten", name=name)
        self._tools[name] = callable_

    def list_tool_names(self) -> list[str]:
        return list(self._tools.keys())

    async def dispatch(
        self,
        tool_name: str,
        args: dict,
        allowed_tools: list[str],
    ) -> dict:
        if tool_name not in allowed_tools:
            raise ToolNotAllowedError(
                f"tool {tool_name!r} not in step allowlist {allowed_tools}"
            )
        if tool_name not in self._tools:
            raise ToolNotFoundError(f"tool {tool_name!r} not registered")

        tool = self._tools[tool_name]
        return await tool(**args)
```

- [ ] **Step 4: Run test — expect pass**

- [ ] **Step 5: Commit**

```bash
git add src/donna/skills/tool_registry.py tests/unit/test_skills_tool_registry.py
git commit -m "feat(skills): add ToolRegistry with per-step allowlist enforcement"
```

---

## Task 5: `web_fetch` tool implementation

**Files:**
- Create: `src/donna/skills/tools/__init__.py` (empty package init)
- Create: `src/donna/skills/tools/web_fetch.py`
- Create: `tests/unit/test_skills_tools_web_fetch.py`

The `web_fetch` tool uses `httpx.AsyncClient` to fetch a URL and returns `{status_code, headers, body}`. Body is truncated to 200_000 chars to avoid blowing context.

- [ ] **Step 1: Check httpx is available**

```bash
grep -E "^httpx" pyproject.toml || echo "NOT PRESENT"
```

If NOT PRESENT, add to `pyproject.toml` under the skill-system dependencies group:

```toml
"httpx>=0.27.0",
```

And reinstall: `pip install -e .`

- [ ] **Step 2: Write test**

```python
# tests/unit/test_skills_tools_web_fetch.py
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from donna.skills.tools.web_fetch import web_fetch, WebFetchError


async def test_web_fetch_returns_structured_response():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/html"}
    mock_response.text = "<html>hello</html>"

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.get.return_value = mock_response

    with patch("donna.skills.tools.web_fetch.httpx.AsyncClient", return_value=mock_client):
        result = await web_fetch(url="https://example.com")

    assert result["status_code"] == 200
    assert result["headers"]["content-type"] == "text/html"
    assert result["body"] == "<html>hello</html>"


async def test_web_fetch_truncates_large_body():
    large_body = "x" * 300_000
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.text = large_body

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.get.return_value = mock_response

    with patch("donna.skills.tools.web_fetch.httpx.AsyncClient", return_value=mock_client):
        result = await web_fetch(url="https://example.com")

    assert len(result["body"]) == 200_000
    assert result["truncated"] is True


async def test_web_fetch_raises_on_exception():
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.get.side_effect = RuntimeError("network down")

    with patch("donna.skills.tools.web_fetch.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(WebFetchError, match="network down"):
            await web_fetch(url="https://example.com")
```

- [ ] **Step 3: Run test — expect ImportError**

- [ ] **Step 4: Create files**

`src/donna/skills/tools/__init__.py`:

```python
"""Concrete tool implementations for the skill system.

Tools are async callables. Each tool is a Python module here and is
registered into the ToolRegistry at application startup.
"""
```

`src/donna/skills/tools/web_fetch.py`:

```python
"""web_fetch — fetches a URL and returns structured response data."""

from __future__ import annotations

import httpx
import structlog

logger = structlog.get_logger()

MAX_BODY_CHARS = 200_000


class WebFetchError(Exception):
    """Raised when a web_fetch invocation fails after any retries."""


async def web_fetch(
    url: str,
    timeout_s: float = 10.0,
    method: str = "GET",
) -> dict:
    """Fetch a URL. Returns {status_code, headers, body, truncated}.

    body is truncated to MAX_BODY_CHARS. truncated is True if truncation occurred.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            if method.upper() == "GET":
                resp = await client.get(url)
            else:
                raise WebFetchError(f"unsupported method: {method}")
    except Exception as exc:
        logger.warning("web_fetch_failed", url=url, error=str(exc))
        raise WebFetchError(str(exc)) from exc

    body = resp.text
    truncated = False
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS]
        truncated = True

    return {
        "status_code": resp.status_code,
        "headers": dict(resp.headers),
        "body": body,
        "truncated": truncated,
    }
```

- [ ] **Step 5: Run test — expect pass**

- [ ] **Step 6: Commit**

```bash
git add src/donna/skills/tools/ tests/unit/test_skills_tools_web_fetch.py pyproject.toml
git commit -m "feat(skills): add web_fetch tool for skill-layer HTTP requests"
```

---

## Task 6: Tool dispatcher with retry + Jinja arg rendering

**Files:**
- Create: `src/donna/skills/tool_dispatch.py`
- Create: `tests/unit/test_skills_tool_dispatch.py`

Tool dispatcher takes a skill step's `tool_invocations` list, resolves Jinja args against state+inputs, dispatches through the registry with retry, and returns the tool results dict. Failures propagate per the step's `on_failure` policy.

- [ ] **Step 1: Write test**

```python
# tests/unit/test_skills_tool_dispatch.py
from unittest.mock import AsyncMock

import pytest

from donna.skills.tool_registry import ToolRegistry
from donna.skills.tool_dispatch import (
    ToolDispatcher,
    ToolInvocationError,
    ToolInvocationSpec,
)


async def test_basic_dispatch_with_jinja_args():
    async def echo(**kwargs):
        return {"got": kwargs}

    registry = ToolRegistry()
    registry.register("echo", echo)
    dispatcher = ToolDispatcher(registry)

    result = await dispatcher.run_invocation(
        spec=ToolInvocationSpec(
            tool="echo",
            args={"url": "{{ inputs.url }}", "size": "{{ state.plan.size }}"},
            store_as="result",
        ),
        state={"plan": {"size": "L"}},
        inputs={"url": "https://x.com"},
        allowed_tools=["echo"],
    )

    assert result == {"result": {"got": {"url": "https://x.com", "size": "L"}}}


async def test_dispatch_retries_on_failure():
    attempts = {"count": 0}

    async def flaky(**kwargs):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("transient")
        return {"ok": True}

    registry = ToolRegistry()
    registry.register("flaky", flaky)
    dispatcher = ToolDispatcher(registry)

    result = await dispatcher.run_invocation(
        spec=ToolInvocationSpec(
            tool="flaky",
            args={},
            retry={"max_attempts": 3, "backoff_s": [0, 0, 0]},
            store_as="r",
        ),
        state={},
        inputs={},
        allowed_tools=["flaky"],
    )

    assert result == {"r": {"ok": True}}
    assert attempts["count"] == 3


async def test_dispatch_raises_after_retry_exhausted():
    async def always_fail(**kwargs):
        raise RuntimeError("permanent")

    registry = ToolRegistry()
    registry.register("fail", always_fail)
    dispatcher = ToolDispatcher(registry)

    with pytest.raises(ToolInvocationError, match="permanent"):
        await dispatcher.run_invocation(
            spec=ToolInvocationSpec(
                tool="fail",
                args={},
                retry={"max_attempts": 2, "backoff_s": [0, 0]},
                store_as="r",
            ),
            state={}, inputs={}, allowed_tools=["fail"],
        )


async def test_dispatch_respects_allowlist():
    async def ok(**kwargs):
        return {"v": 1}

    registry = ToolRegistry()
    registry.register("tool1", ok)
    dispatcher = ToolDispatcher(registry)

    with pytest.raises(ToolInvocationError):
        await dispatcher.run_invocation(
            spec=ToolInvocationSpec(tool="tool1", args={}, store_as="r"),
            state={}, inputs={}, allowed_tools=["tool2"],
        )
```

- [ ] **Step 2: Run test — expect ImportError**

- [ ] **Step 3: Create `src/donna/skills/tool_dispatch.py`**

```python
"""Tool dispatcher — resolves args from Jinja templates and runs tools with retry."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import jinja2
import structlog

from donna.skills.tool_registry import (
    ToolNotAllowedError,
    ToolNotFoundError,
    ToolRegistry,
)

logger = structlog.get_logger()


class ToolInvocationError(Exception):
    """Raised when a tool invocation fails (including after retries)."""


@dataclass(slots=True)
class ToolInvocationSpec:
    """A single tool call declared in a skill YAML step."""
    tool: str
    args: dict[str, Any]
    store_as: str = "result"
    retry: dict[str, Any] = field(default_factory=dict)  # {max_attempts, backoff_s}


class ToolDispatcher:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._jinja = jinja2.Environment(
            autoescape=False,
            undefined=jinja2.StrictUndefined,
        )

    async def run_invocation(
        self,
        spec: ToolInvocationSpec,
        state: dict,
        inputs: dict,
        allowed_tools: list[str],
    ) -> dict:
        """Run a single tool invocation, returning {store_as_key: result}."""
        resolved_args = self._render_args(spec.args, state=state, inputs=inputs)

        max_attempts = int(spec.retry.get("max_attempts", 1))
        backoff_s = spec.retry.get("backoff_s", [0])

        last_err: Exception | None = None

        for attempt in range(max_attempts):
            try:
                result = await self._registry.dispatch(
                    tool_name=spec.tool,
                    args=resolved_args,
                    allowed_tools=allowed_tools,
                )
                if attempt > 0:
                    logger.info(
                        "tool_retry_succeeded",
                        tool=spec.tool,
                        attempt=attempt + 1,
                    )
                return {spec.store_as: result}
            except (ToolNotAllowedError, ToolNotFoundError) as exc:
                # Permission / missing tool errors don't benefit from retry.
                raise ToolInvocationError(str(exc)) from exc
            except Exception as exc:
                last_err = exc
                if attempt + 1 < max_attempts:
                    wait = backoff_s[attempt] if attempt < len(backoff_s) else backoff_s[-1]
                    logger.info(
                        "tool_retry_scheduled",
                        tool=spec.tool,
                        attempt=attempt + 1,
                        wait_s=wait,
                        error=str(exc),
                    )
                    if wait > 0:
                        await asyncio.sleep(wait)

        logger.warning(
            "tool_invocation_failed",
            tool=spec.tool,
            attempts=max_attempts,
            error=str(last_err) if last_err else "unknown",
        )
        raise ToolInvocationError(str(last_err)) from last_err

    def _render_args(
        self, args: dict, state: dict, inputs: dict
    ) -> dict:
        """Recursively Jinja-render string values in args."""
        return {k: self._render_value(v, state=state, inputs=inputs) for k, v in args.items()}

    def _render_value(self, value: Any, state: dict, inputs: dict) -> Any:
        if isinstance(value, str):
            try:
                return self._jinja.from_string(value).render(state=state, inputs=inputs)
            except jinja2.UndefinedError as exc:
                raise ToolInvocationError(f"arg render failed: {exc}") from exc
        if isinstance(value, dict):
            return self._render_args(value, state=state, inputs=inputs)
        if isinstance(value, list):
            return [self._render_value(v, state=state, inputs=inputs) for v in value]
        return value
```

- [ ] **Step 4: Run test — expect pass**

- [ ] **Step 5: Commit**

```bash
git add src/donna/skills/tool_dispatch.py tests/unit/test_skills_tool_dispatch.py
git commit -m "feat(skills): add tool dispatcher with Jinja arg rendering and retry"
```

---

## Task 7: Flow control DSL — `for_each` primitive

**Files:**
- Create: `src/donna/skills/dsl.py`
- Create: `tests/unit/test_skills_dsl.py`

The DSL evaluator expands a `for_each` block into concrete `ToolInvocationSpec` instances, one per iteration, with loop variables bound. Only `for_each` is implemented in v1; `retry` is handled by the dispatcher (Task 6); `escalate` is handled by the executor (Task 9).

- [ ] **Step 1: Write test**

```python
# tests/unit/test_skills_dsl.py
import pytest

from donna.skills.dsl import expand_for_each, DSLError
from donna.skills.tool_dispatch import ToolInvocationSpec


def test_for_each_over_list():
    spec = {
        "for_each": "{{ state.plan.urls }}",
        "as": "entry",
        "tool": "web_fetch",
        "args": {"url": "{{ entry }}"},
        "store_as": "fetched[{{ loop.index0 }}]",
    }
    state = {"plan": {"urls": ["https://a.com", "https://b.com", "https://c.com"]}}
    inputs = {}

    specs = expand_for_each(spec, state=state, inputs=inputs)

    assert len(specs) == 3
    assert specs[0].tool == "web_fetch"
    assert specs[0].args == {"url": "https://a.com"}
    assert specs[0].store_as == "fetched[0]"
    assert specs[1].args == {"url": "https://b.com"}
    assert specs[2].store_as == "fetched[2]"


def test_for_each_over_dict_items_is_not_supported_in_v1():
    spec = {
        "for_each": "{{ state.plan.mapping }}",
        "as": "entry",
        "tool": "mock",
        "args": {},
        "store_as": "r",
    }
    state = {"plan": {"mapping": {"a": 1, "b": 2}}}
    with pytest.raises(DSLError, match="must be a list"):
        expand_for_each(spec, state=state, inputs={})


def test_for_each_empty_list_produces_no_specs():
    spec = {
        "for_each": "{{ state.plan.urls }}",
        "as": "entry",
        "tool": "mock",
        "args": {},
        "store_as": "r",
    }
    specs = expand_for_each(spec, state={"plan": {"urls": []}}, inputs={})
    assert specs == []


def test_for_each_loop_index_variables_available():
    spec = {
        "for_each": "{{ inputs.items }}",
        "as": "item",
        "tool": "mock",
        "args": {"i": "{{ loop.index0 }}", "one_indexed": "{{ loop.index }}"},
        "store_as": "r[{{ loop.index0 }}]",
    }
    specs = expand_for_each(spec, state={}, inputs={"items": ["x", "y"]})
    assert specs[0].args == {"i": "0", "one_indexed": "1"}
    assert specs[1].args == {"i": "1", "one_indexed": "2"}
```

- [ ] **Step 2: Run test — expect ImportError**

- [ ] **Step 3: Create `src/donna/skills/dsl.py`**

```python
"""Flow control DSL — for_each primitive for skill tool invocations.

v1 scope: only for_each is supported. retry is handled by ToolDispatcher.
escalate is handled by SkillExecutor via a field in step output schemas.
"""

from __future__ import annotations

from typing import Any

import jinja2

from donna.skills.tool_dispatch import ToolInvocationSpec


class DSLError(Exception):
    pass


_jinja = jinja2.Environment(
    autoescape=False,
    undefined=jinja2.StrictUndefined,
)


def expand_for_each(
    block: dict[str, Any],
    state: dict,
    inputs: dict,
) -> list[ToolInvocationSpec]:
    """Expand a for_each block into a list of concrete ToolInvocationSpec.

    Expected block shape:
        {
            "for_each": "{{ state.plan.urls }}",  # Jinja expression yielding a list
            "as": "entry",                        # loop variable name
            "tool": "web_fetch",
            "args": {"url": "{{ entry }}"},       # may reference `entry` and `loop`
            "store_as": "fetched[{{ loop.index0 }}]",
            "retry": {...},                       # optional, passed through
        }
    """
    iterable_expr = block.get("for_each")
    as_var = block.get("as")
    tool = block.get("tool")

    if not iterable_expr or not as_var or not tool:
        raise DSLError("for_each requires 'for_each', 'as', and 'tool' fields")

    resolved = _render_value(iterable_expr, state=state, inputs=inputs, extra={})
    if not isinstance(resolved, list):
        raise DSLError(
            f"for_each expression must be a list, got {type(resolved).__name__}"
        )

    specs: list[ToolInvocationSpec] = []
    for index, item in enumerate(resolved):
        loop_ctx = {"index0": index, "index": index + 1, "length": len(resolved)}
        extra_ctx = {as_var: item, "loop": loop_ctx}

        rendered_args = _render_args(block.get("args", {}), state=state, inputs=inputs, extra=extra_ctx)
        rendered_store = _render_value(
            block.get("store_as", "result"), state=state, inputs=inputs, extra=extra_ctx
        )

        specs.append(ToolInvocationSpec(
            tool=tool,
            args=rendered_args,
            store_as=rendered_store,
            retry=block.get("retry", {}),
        ))

    return specs


def _render_value(value: Any, state: dict, inputs: dict, extra: dict) -> Any:
    if isinstance(value, str):
        try:
            return _jinja.from_string(value).render(state=state, inputs=inputs, **extra)
        except jinja2.UndefinedError as exc:
            raise DSLError(f"template render failed: {exc}") from exc
    if isinstance(value, dict):
        return _render_args(value, state=state, inputs=inputs, extra=extra)
    if isinstance(value, list):
        return [_render_value(v, state=state, inputs=inputs, extra=extra) for v in value]
    return value


def _render_args(args: dict, state: dict, inputs: dict, extra: dict) -> dict:
    return {k: _render_value(v, state=state, inputs=inputs, extra=extra) for k, v in args.items()}
```

Note: the iterable expression is rendered through the Jinja env, which returns a string representation of a list (e.g. `"['a', 'b']"`). We need it to return the actual list. For this to work correctly, we use a Jinja hack: if the expression is purely `{{ <variable> }}`, we can look it up directly.

Wait — this is a real problem. Standard Jinja rendering stringifies lists. Revise the approach:

**Revised implementation for `_render_value` when the whole value is an iterable expression:**

Detect the case where the expression is just `{{ <path> }}` and evaluate it without stringification. Replace the `_render_value` function above with:

```python
import re

_WHOLE_EXPR_RE = re.compile(r"^\s*\{\{\s*(.+?)\s*\}\}\s*$")


def _render_value(value: Any, state: dict, inputs: dict, extra: dict) -> Any:
    if isinstance(value, str):
        m = _WHOLE_EXPR_RE.match(value)
        if m:
            # Whole value is a single expression — evaluate natively to preserve type.
            try:
                template = _jinja.compile_expression(m.group(1))
                return template(state=state, inputs=inputs, **extra)
            except jinja2.UndefinedError as exc:
                raise DSLError(f"expression eval failed: {exc}") from exc
        try:
            return _jinja.from_string(value).render(state=state, inputs=inputs, **extra)
        except jinja2.UndefinedError as exc:
            raise DSLError(f"template render failed: {exc}") from exc
    if isinstance(value, dict):
        return _render_args(value, state=state, inputs=inputs, extra=extra)
    if isinstance(value, list):
        return [_render_value(v, state=state, inputs=inputs, extra=extra) for v in value]
    return value
```

`Environment.compile_expression` evaluates a Jinja expression and returns the Python value preserving type, which is what we need.

- [ ] **Step 4: Run test — expect pass**

- [ ] **Step 5: Commit**

```bash
git add src/donna/skills/dsl.py tests/unit/test_skills_dsl.py
git commit -m "feat(skills): add for_each DSL primitive with type-preserving eval"
```

---

## Task 8: Triage agent

**Files:**
- Create: `src/donna/skills/triage.py`
- Create: `tests/unit/test_skills_triage.py`
- Modify: `config/task_types.yaml` (add triage_failure task type)
- Modify: `config/donna_models.yaml` (route triage_failure to local_parser)

The triage agent receives a failed skill step context and returns one of four decisions. It runs on local LLM via the model router. Retry cap of 3 per skill run is enforced by the caller (executor), not by triage itself.

- [ ] **Step 1: Write test**

```python
# tests/unit/test_skills_triage.py
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.skills.triage import TriageAgent, TriageDecision, TriageInput


async def test_triage_returns_retry_decision():
    router = AsyncMock()
    router.complete.return_value = (
        {
            "decision": "retry_step_with_modified_prompt",
            "rationale": "output was close; prompt clarification should help",
            "modified_prompt_additions": "Be stricter about field types.",
        },
        MagicMock(invocation_id="i1", cost_usd=0.0),
    )
    agent = TriageAgent(router)

    result = await agent.handle_failure(
        TriageInput(
            skill_id="s1", step_name="extract",
            error_type="schema_validation",
            error_message="missing required field title",
            state={"extract_attempt_1": {"confidence": 0.6}},
            skill_yaml_preview="...",
            user_id="nick",
            retry_count=0,
        ),
    )

    assert result.decision == TriageDecision.RETRY_STEP
    assert result.rationale.startswith("output was close")
    assert "stricter" in (result.modified_prompt_additions or "")


async def test_triage_returns_escalate_decision():
    router = AsyncMock()
    router.complete.return_value = (
        {
            "decision": "escalate_to_claude",
            "rationale": "tool unavailable; only Claude can proceed",
        },
        MagicMock(invocation_id="i1", cost_usd=0.0),
    )
    agent = TriageAgent(router)

    result = await agent.handle_failure(
        TriageInput(
            skill_id="s1", step_name="fetch", error_type="tool_exhausted",
            error_message="web_fetch timeout x3", state={},
            skill_yaml_preview="", user_id="nick", retry_count=3,
        ),
    )

    assert result.decision == TriageDecision.ESCALATE_TO_CLAUDE


async def test_triage_respects_retry_cap():
    """Triage cannot return retry if retry_count >= MAX_RETRY_COUNT."""
    router = AsyncMock()
    # Even if LLM says retry, the agent should override to escalate.
    router.complete.return_value = (
        {"decision": "retry_step_with_modified_prompt", "rationale": "try again"},
        MagicMock(invocation_id="i1", cost_usd=0.0),
    )
    agent = TriageAgent(router)

    result = await agent.handle_failure(
        TriageInput(
            skill_id="s1", step_name="x", error_type="schema_validation",
            error_message="...", state={}, skill_yaml_preview="",
            user_id="nick", retry_count=3,  # at the cap
        ),
    )

    assert result.decision == TriageDecision.ESCALATE_TO_CLAUDE
    assert "retry cap" in result.rationale.lower()


async def test_triage_handles_llm_failure():
    router = AsyncMock()
    router.complete.side_effect = RuntimeError("model unavailable")
    agent = TriageAgent(router)

    result = await agent.handle_failure(
        TriageInput(
            skill_id="s1", step_name="x", error_type="any",
            error_message="...", state={}, skill_yaml_preview="",
            user_id="nick", retry_count=0,
        ),
    )

    # On LLM failure, triage falls back to escalate_to_claude.
    assert result.decision == TriageDecision.ESCALATE_TO_CLAUDE
    assert "triage LLM failed" in result.rationale
```

- [ ] **Step 2: Run test — expect ImportError**

- [ ] **Step 3: Create `src/donna/skills/triage.py`**

```python
"""TriageAgent — handles skill runtime failures with structured decisions."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger()

MAX_RETRY_COUNT = 3


class TriageDecision(str, enum.Enum):
    RETRY_STEP = "retry_step_with_modified_prompt"
    SKIP_STEP = "skip_step"
    ESCALATE_TO_CLAUDE = "escalate_to_claude"
    ALERT_USER = "alert_user"
    MARK_SKILL_DEGRADED = "mark_skill_degraded"


@dataclass(slots=True)
class TriageInput:
    skill_id: str
    step_name: str
    error_type: str              # schema_validation | tool_exhausted | template_error | model_call | ...
    error_message: str
    state: dict
    skill_yaml_preview: str      # First ~1000 chars of the skill YAML for context
    user_id: str
    retry_count: int             # Number of retries already consumed for this skill run


@dataclass(slots=True)
class TriageResult:
    decision: TriageDecision
    rationale: str
    modified_prompt_additions: str | None = None
    alert_message: str | None = None


TRIAGE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": [d.value for d in TriageDecision],
        },
        "rationale": {"type": "string"},
        "modified_prompt_additions": {"type": ["string", "null"]},
        "alert_message": {"type": ["string", "null"]},
    },
    "required": ["decision", "rationale"],
}


class TriageAgent:
    def __init__(self, model_router: Any) -> None:
        self._router = model_router

    async def handle_failure(self, input_: TriageInput) -> TriageResult:
        """Return a structured decision for a failed skill step."""
        # Enforce the retry cap up-front.
        if input_.retry_count >= MAX_RETRY_COUNT:
            return TriageResult(
                decision=TriageDecision.ESCALATE_TO_CLAUDE,
                rationale=(
                    f"retry cap ({MAX_RETRY_COUNT}) reached for skill={input_.skill_id}; "
                    f"escalating to Claude"
                ),
            )

        prompt = self._build_prompt(input_)

        try:
            output, _meta = await self._router.complete(
                prompt=prompt,
                schema=TRIAGE_OUTPUT_SCHEMA,
                model_alias="local_parser",
                task_type="triage_failure",
                user_id=input_.user_id,
            )
        except Exception as exc:
            logger.warning(
                "triage_llm_failed",
                skill_id=input_.skill_id,
                error=str(exc),
            )
            return TriageResult(
                decision=TriageDecision.ESCALATE_TO_CLAUDE,
                rationale=f"triage LLM failed: {exc}",
            )

        try:
            decision = TriageDecision(output["decision"])
        except (KeyError, ValueError):
            return TriageResult(
                decision=TriageDecision.ESCALATE_TO_CLAUDE,
                rationale="triage LLM returned invalid decision; escalating",
            )

        # Override retry if LLM asks for it but we're at the cap.
        if decision == TriageDecision.RETRY_STEP and input_.retry_count >= MAX_RETRY_COUNT - 1:
            logger.info(
                "triage_retry_overridden",
                skill_id=input_.skill_id,
                retry_count=input_.retry_count,
            )
            return TriageResult(
                decision=TriageDecision.ESCALATE_TO_CLAUDE,
                rationale=(
                    "LLM requested retry but retry cap imminent; escalating instead. "
                    f"Original rationale: {output.get('rationale', '')}"
                ),
            )

        return TriageResult(
            decision=decision,
            rationale=output.get("rationale", ""),
            modified_prompt_additions=output.get("modified_prompt_additions"),
            alert_message=output.get("alert_message"),
        )

    @staticmethod
    def _build_prompt(input_: TriageInput) -> str:
        return (
            "You are Donna's skill-failure triage agent. A skill step failed at "
            "runtime. Decide what should happen next.\n\n"
            f"Skill ID: {input_.skill_id}\n"
            f"Step: {input_.step_name}\n"
            f"Error type: {input_.error_type}\n"
            f"Error message: {input_.error_message}\n"
            f"Retries already consumed: {input_.retry_count}\n\n"
            f"Current state object:\n{input_.state}\n\n"
            f"Skill YAML (first part):\n{input_.skill_yaml_preview[:1000]}\n\n"
            "Available decisions:\n"
            "- retry_step_with_modified_prompt: the prompt could be improved and retrying might work\n"
            "- skip_step: the step was non-essential; continue with empty state for it\n"
            "- escalate_to_claude: substantive failure; hand the whole task to Claude\n"
            "- alert_user: needs user intervention; don't proceed\n"
            "- mark_skill_degraded: pattern suggests the skill is broken and needs evolution\n\n"
            "Return a JSON object with your decision and rationale."
        )
```

- [ ] **Step 4: Update config files**

In `config/task_types.yaml`, add under `task_types:`:

```yaml
  triage_failure:
    description: "Decide how to handle a skill runtime failure"
    model: local_parser
    prompt_template: ""
    output_schema: ""
    tools: []
```

In `config/donna_models.yaml`, add under `routing:`:

```yaml
  triage_failure:
    model: local_parser
    fallback: parser
```

- [ ] **Step 5: Run test — expect pass**

- [ ] **Step 6: Commit**

```bash
git add src/donna/skills/triage.py tests/unit/test_skills_triage.py \
        config/task_types.yaml config/donna_models.yaml
git commit -m "feat(skills): add TriageAgent with 5-decision failure handler"
```

---

## Task 9: Multi-step SkillExecutor (REPLACES Phase 1 single-step)

**Files:**
- Modify: `src/donna/skills/executor.py` (full rewrite — Phase 1 version is replaced)
- Modify: `tests/unit/test_skills_executor.py` (add multi-step tests; keep existing Phase 1 tests compatible)

This is the largest task in Phase 2. The multi-step executor:
- Processes steps of three kinds: `llm`, `tool`, `mixed`.
- Resolves tool invocations through `ToolDispatcher` (with DSL `for_each` expansion via `dsl.expand_for_each`).
- Renders LLM prompts with Jinja2 using `state` + `inputs` context.
- Validates LLM output against the step's JSON schema.
- Handles the `escalate` signal in LLM output (short-circuits the run).
- Invokes `TriageAgent.handle_failure` on any runtime failure.
- Tracks retry count per skill run (max 3).
- Returns a full `SkillRunResult` including state, per-step outputs, timings, and costs.

**Scope note:** persistence (writing `skill_run` and `skill_step_result` rows) is in Task 10. This task focuses on in-memory execution semantics.

- [ ] **Step 1: Read the current `src/donna/skills/executor.py`** (Phase 1 single-step) to understand what's being replaced. The `SkillRunResult` dataclass stays; the rest is rewritten.

- [ ] **Step 2: Write new tests**

Extend `tests/unit/test_skills_executor.py` with multi-step scenarios. The existing single-step tests from Phase 1 should still pass after the rewrite (backward compatibility: a skill with one step behaves the same).

Add these new test functions at the bottom of the existing file:

```python
# --- Phase 2 multi-step tests ---

import pytest
from unittest.mock import AsyncMock, MagicMock

from donna.skills.executor import SkillExecutor, SkillRunResult
from donna.skills.models import SkillRow, SkillVersionRow
from donna.skills.tool_registry import ToolRegistry
from donna.skills.tool_dispatch import ToolDispatcher
from donna.skills.triage import TriageAgent, TriageDecision, TriageResult


def _multistep_version(yaml_backbone: str, step_content: dict, output_schemas: dict) -> SkillVersionRow:
    from datetime import datetime, timezone
    return SkillVersionRow(
        id="v1", skill_id="s1", version_number=1,
        yaml_backbone=yaml_backbone,
        step_content=step_content, output_schemas=output_schemas,
        created_by="seed", changelog=None,
        created_at=datetime.now(timezone.utc),
    )


async def test_executor_runs_two_step_skill():
    yaml_backbone = """
capability_name: parse_task
version: 1
steps:
  - name: extract
    kind: llm
    prompt: steps/extract.md
    output_schema: schemas/extract_v1.json
  - name: classify
    kind: llm
    prompt: steps/classify.md
    output_schema: schemas/classify_v1.json
final_output: "{{ state.classify }}"
"""

    version = _multistep_version(
        yaml_backbone,
        step_content={
            "extract": "Extract: {{ inputs.raw_text }}",
            "classify": "Classify: {{ state.extract.title }}",
        },
        output_schemas={
            "extract": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
            "classify": {"type": "object", "properties": {"priority": {"type": "integer"}}, "required": ["priority"]},
        },
    )

    router = AsyncMock()
    router.complete.side_effect = [
        ({"title": "Q2 review"}, MagicMock(invocation_id="i1", latency_ms=50, tokens_in=20, tokens_out=5, cost_usd=0.0)),
        ({"priority": 3}, MagicMock(invocation_id="i2", latency_ms=40, tokens_in=10, tokens_out=3, cost_usd=0.0)),
    ]

    executor = SkillExecutor(router, tool_registry=ToolRegistry(), triage=None)
    result = await executor.execute(
        skill=_make_skill(), version=version,
        inputs={"raw_text": "draft the Q2 review"},
        user_id="nick",
    )

    assert result.status == "succeeded"
    assert result.state["extract"]["title"] == "Q2 review"
    assert result.state["classify"]["priority"] == 3
    assert result.final_output == {"priority": 3}
    assert router.complete.call_count == 2


async def test_executor_runs_tool_step_with_for_each():
    yaml_backbone = """
capability_name: fetch
version: 1
steps:
  - name: fetch_all
    kind: tool
    tools: [mock_fetch]
    tool_invocations:
      - for_each: "{{ inputs.urls }}"
        as: url
        tool: mock_fetch
        args:
          u: "{{ url }}"
        store_as: "fetched_{{ loop.index0 }}"
final_output: "{{ state.fetch_all }}"
"""
    version = _multistep_version(yaml_backbone, step_content={}, output_schemas={})

    async def mock_fetch(u: str):
        return {"url_fetched": u}

    registry = ToolRegistry()
    registry.register("mock_fetch", mock_fetch)

    executor = SkillExecutor(AsyncMock(), tool_registry=registry, triage=None)
    result = await executor.execute(
        skill=_make_skill(), version=version,
        inputs={"urls": ["https://a.com", "https://b.com"]},
        user_id="nick",
    )

    assert result.status == "succeeded"
    assert result.state["fetch_all"]["fetched_0"] == {"url_fetched": "https://a.com"}
    assert result.state["fetch_all"]["fetched_1"] == {"url_fetched": "https://b.com"}


async def test_executor_escalate_signal_short_circuits_multistep():
    yaml_backbone = """
capability_name: parse
version: 1
steps:
  - name: first
    kind: llm
    prompt: p.md
    output_schema: s.json
  - name: second
    kind: llm
    prompt: p2.md
    output_schema: s2.json
final_output: "{{ state.second }}"
"""
    version = _multistep_version(
        yaml_backbone,
        step_content={"first": "...", "second": "..."},
        output_schemas={
            "first": {"type": "object", "properties": {"escalate": {"type": "object"}}},
            "second": {"type": "object"},
        },
    )

    router = AsyncMock()
    router.complete.return_value = (
        {"escalate": {"reason": "insufficient context"}},
        MagicMock(invocation_id="i1", cost_usd=0.0),
    )

    executor = SkillExecutor(router, tool_registry=ToolRegistry(), triage=None)
    result = await executor.execute(
        skill=_make_skill(), version=version, inputs={}, user_id="nick",
    )

    assert result.status == "escalated"
    assert result.escalation_reason == "insufficient context"
    # Second step should not have run.
    assert router.complete.call_count == 1


async def test_executor_calls_triage_on_schema_failure_then_escalates():
    yaml_backbone = """
capability_name: x
version: 1
steps:
  - name: step1
    kind: llm
    prompt: p.md
    output_schema: s.json
final_output: "{{ state.step1 }}"
"""
    version = _multistep_version(
        yaml_backbone,
        step_content={"step1": "prompt"},
        output_schemas={"step1": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}},
    )

    router = AsyncMock()
    router.complete.return_value = (
        {"not_title": "x"},  # schema invalid
        MagicMock(invocation_id="i1", cost_usd=0.0),
    )

    triage = AsyncMock()
    triage.handle_failure.return_value = TriageResult(
        decision=TriageDecision.ESCALATE_TO_CLAUDE,
        rationale="output shape is structurally broken",
    )

    executor = SkillExecutor(router, tool_registry=ToolRegistry(), triage=triage)
    result = await executor.execute(
        skill=_make_skill(), version=version, inputs={}, user_id="nick",
    )

    assert result.status == "escalated"
    triage.handle_failure.assert_awaited_once()
```

- [ ] **Step 3: Rewrite `src/donna/skills/executor.py`**

Full replacement. Preserve the `SkillRunResult` dataclass (Phase 1 callers use it) but add new fields.

```python
"""SkillExecutor — multi-step skill execution with tool dispatch,
DSL, triage, and escalate-signal handling.

See spec §6.4 and Phase 2 plan Task 9.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import jinja2
import structlog
import yaml

from donna.skills.dsl import DSLError, expand_for_each
from donna.skills.models import SkillRow, SkillVersionRow
from donna.skills.state import StateObject
from donna.skills.tool_dispatch import ToolDispatcher, ToolInvocationError, ToolInvocationSpec
from donna.skills.tool_registry import ToolRegistry
from donna.skills.triage import TriageAgent, TriageDecision, TriageInput, TriageResult
from donna.skills.validation import SchemaValidationError, validate_output

logger = structlog.get_logger()


@dataclass(slots=True)
class StepResultRecord:
    """In-memory per-step record for the SkillRunResult."""
    step_name: str
    step_index: int
    step_kind: str
    output: dict | None = None
    tool_calls: list | None = None
    latency_ms: int = 0
    validation_status: str = "valid"  # valid | schema_invalid | escalate_signal | tool_failed
    error: str | None = None
    invocation_id: str | None = None


@dataclass(slots=True)
class SkillRunResult:
    """Result of a skill execution. Compatible with Phase 1 shape."""
    status: str                         # succeeded | failed | escalated
    final_output: Any = None
    state: dict[str, Any] = field(default_factory=dict)
    escalation_reason: str | None = None
    error: str | None = None
    invocation_ids: list[str] = field(default_factory=list)
    total_latency_ms: int = 0
    total_cost_usd: float = 0.0
    step_results: list[StepResultRecord] = field(default_factory=list)
    tool_result_cache: dict = field(default_factory=dict)


class SkillExecutor:
    def __init__(
        self,
        model_router: Any,
        tool_registry: ToolRegistry | None = None,
        triage: TriageAgent | None = None,
    ) -> None:
        self._router = model_router
        self._tool_registry = tool_registry or ToolRegistry()
        self._tool_dispatcher = ToolDispatcher(self._tool_registry)
        self._triage = triage
        self._jinja = jinja2.Environment(
            autoescape=False,
            undefined=jinja2.StrictUndefined,
        )

    async def execute(
        self,
        skill: SkillRow,
        version: SkillVersionRow,
        inputs: dict,
        user_id: str,
    ) -> SkillRunResult:
        state = StateObject()
        start = time.monotonic()
        retry_count = 0

        try:
            backbone = yaml.safe_load(version.yaml_backbone) if version.yaml_backbone else {}
        except yaml.YAMLError as exc:
            return SkillRunResult(status="failed", error=f"yaml_parse: {exc}")

        steps = backbone.get("steps", [])
        if not steps:
            return SkillRunResult(status="succeeded", final_output={}, state={})

        step_results: list[StepResultRecord] = []
        invocation_ids: list[str] = []
        total_cost = 0.0

        for idx, step in enumerate(steps):
            step_name = step.get("name") or f"step_{idx}"
            step_kind = step.get("kind", "llm")
            allowed_tools = step.get("tools", [])

            step_start = time.monotonic()
            record = StepResultRecord(step_name=step_name, step_index=idx, step_kind=step_kind)

            try:
                # Pure tool step: run tool invocations, no LLM.
                if step_kind == "tool":
                    collected = await self._run_tool_invocations(
                        step.get("tool_invocations", []),
                        state=state, inputs=inputs,
                        allowed_tools=allowed_tools,
                    )
                    state[step_name] = collected
                    record.tool_calls = list(collected.keys())

                elif step_kind == "mixed":
                    # Tools first, then LLM with results in state.
                    collected = await self._run_tool_invocations(
                        step.get("tool_invocations", []),
                        state=state, inputs=inputs,
                        allowed_tools=allowed_tools,
                    )
                    state[step_name + "_tool_results"] = collected
                    record.tool_calls = list(collected.keys())
                    llm_output, inv_id, cost = await self._run_llm_step(
                        step=step, step_name=step_name,
                        version=version, state=state, inputs=inputs,
                        user_id=user_id, skill=skill,
                    )
                    total_cost += cost
                    invocation_ids.append(inv_id)
                    record.invocation_id = inv_id

                    # Check for escalate signal.
                    if isinstance(llm_output, dict) and "escalate" in llm_output:
                        esc = llm_output["escalate"]
                        reason = esc.get("reason", "unspecified") if isinstance(esc, dict) else str(esc)
                        record.validation_status = "escalate_signal"
                        step_results.append(record)
                        return SkillRunResult(
                            status="escalated", state=state.to_dict(),
                            escalation_reason=reason, invocation_ids=invocation_ids,
                            total_latency_ms=int((time.monotonic() - start) * 1000),
                            total_cost_usd=total_cost, step_results=step_results,
                        )

                    schema = version.output_schemas.get(step_name, {})
                    validate_output(llm_output, schema)
                    state[step_name] = llm_output
                    record.output = llm_output

                else:  # kind == "llm"
                    llm_output, inv_id, cost = await self._run_llm_step(
                        step=step, step_name=step_name,
                        version=version, state=state, inputs=inputs,
                        user_id=user_id, skill=skill,
                    )
                    total_cost += cost
                    invocation_ids.append(inv_id)
                    record.invocation_id = inv_id

                    if isinstance(llm_output, dict) and "escalate" in llm_output:
                        esc = llm_output["escalate"]
                        reason = esc.get("reason", "unspecified") if isinstance(esc, dict) else str(esc)
                        record.validation_status = "escalate_signal"
                        step_results.append(record)
                        return SkillRunResult(
                            status="escalated", state=state.to_dict(),
                            escalation_reason=reason, invocation_ids=invocation_ids,
                            total_latency_ms=int((time.monotonic() - start) * 1000),
                            total_cost_usd=total_cost, step_results=step_results,
                        )

                    schema = version.output_schemas.get(step_name, {})
                    validate_output(llm_output, schema)
                    state[step_name] = llm_output
                    record.output = llm_output

                record.latency_ms = int((time.monotonic() - step_start) * 1000)
                step_results.append(record)

            except (SchemaValidationError, ToolInvocationError, DSLError, jinja2.UndefinedError) as exc:
                record.error = str(exc)
                record.validation_status = "schema_invalid" if isinstance(exc, SchemaValidationError) else "tool_failed"
                record.latency_ms = int((time.monotonic() - step_start) * 1000)
                step_results.append(record)

                # Try triage.
                triage_result = await self._consult_triage(
                    skill=skill, step_name=step_name, exc=exc,
                    state=state, version=version, user_id=user_id,
                    retry_count=retry_count,
                )

                if triage_result.decision == TriageDecision.RETRY_STEP:
                    retry_count += 1
                    # Re-run the same step; but re-injecting modifications is
                    # Phase 2+ work. For v1 we just retry as-is.
                    logger.info("skill_step_triage_retry", skill_id=skill.id, step=step_name)
                    # Not implementing full retry loop to keep scope tight; triage
                    # requesting retry in v1 is best treated as escalate.
                    return SkillRunResult(
                        status="escalated", state=state.to_dict(),
                        escalation_reason=f"triage requested retry (not yet implemented): {triage_result.rationale}",
                        invocation_ids=invocation_ids, total_latency_ms=int((time.monotonic() - start) * 1000),
                        total_cost_usd=total_cost, step_results=step_results,
                    )

                if triage_result.decision == TriageDecision.SKIP_STEP:
                    state[step_name] = {}
                    continue

                # All other decisions (escalate, alert, degraded) short-circuit.
                return SkillRunResult(
                    status="escalated" if triage_result.decision == TriageDecision.ESCALATE_TO_CLAUDE else "failed",
                    state=state.to_dict(),
                    escalation_reason=triage_result.rationale if triage_result.decision == TriageDecision.ESCALATE_TO_CLAUDE else None,
                    error=str(exc),
                    invocation_ids=invocation_ids,
                    total_latency_ms=int((time.monotonic() - start) * 1000),
                    total_cost_usd=total_cost, step_results=step_results,
                )

            except Exception as exc:
                # Truly unexpected — same triage path.
                record.error = str(exc)
                record.validation_status = "tool_failed"
                record.latency_ms = int((time.monotonic() - step_start) * 1000)
                step_results.append(record)
                logger.exception("skill_executor_unexpected_failure", skill_id=skill.id, step=step_name)
                return SkillRunResult(
                    status="failed", state=state.to_dict(),
                    error=f"unexpected: {exc}",
                    invocation_ids=invocation_ids,
                    total_latency_ms=int((time.monotonic() - start) * 1000),
                    total_cost_usd=total_cost, step_results=step_results,
                )

        # All steps completed.
        final_output_expr = backbone.get("final_output", "{{ state }}")
        try:
            final_output = self._jinja.from_string(final_output_expr).render(
                state=state.to_dict(), inputs=inputs,
            )
            # Try to parse as JSON/dict if possible.
            import json as _json
            try:
                final_output = _json.loads(final_output) if isinstance(final_output, str) else final_output
            except (ValueError, TypeError):
                pass
        except Exception as exc:
            logger.warning("final_output_render_failed", skill_id=skill.id, error=str(exc))
            final_output = state.to_dict()

        return SkillRunResult(
            status="succeeded",
            final_output=final_output if not isinstance(final_output, str) or final_output else state[steps[-1].get("name")],
            state=state.to_dict(),
            invocation_ids=invocation_ids,
            total_latency_ms=int((time.monotonic() - start) * 1000),
            total_cost_usd=total_cost, step_results=step_results,
        )

    async def _run_llm_step(
        self, step: dict, step_name: str, version: SkillVersionRow,
        state: StateObject, inputs: dict, user_id: str, skill: SkillRow,
    ) -> tuple[Any, str, float]:
        prompt_template = version.step_content.get(step_name, "")
        schema = version.output_schemas.get(step_name, {})
        rendered = self._jinja.from_string(prompt_template).render(
            inputs=inputs, state=state.to_dict(),
        )

        output, meta = await self._router.complete(
            prompt=rendered,
            schema=schema,
            model_alias="local_parser",
            task_type=f"skill_step::{skill.capability_name}::{step_name}",
            user_id=user_id,
        )
        return output, meta.invocation_id, getattr(meta, "cost_usd", 0.0)

    async def _run_tool_invocations(
        self, invocations: list[dict], state: StateObject,
        inputs: dict, allowed_tools: list[str],
    ) -> dict:
        """Resolve DSL (for_each) and run all tool invocations for a step."""
        collected: dict = {}
        state_dict = state.to_dict()

        for raw_spec in invocations:
            if "for_each" in raw_spec:
                # DSL expansion
                specs = expand_for_each(raw_spec, state=state_dict, inputs=inputs)
            else:
                specs = [ToolInvocationSpec(
                    tool=raw_spec["tool"],
                    args=raw_spec.get("args", {}),
                    store_as=raw_spec.get("store_as", "result"),
                    retry=raw_spec.get("retry", {}),
                )]

            for spec in specs:
                result = await self._tool_dispatcher.run_invocation(
                    spec=spec, state=state_dict, inputs=inputs,
                    allowed_tools=allowed_tools,
                )
                collected.update(result)

        return collected

    async def _consult_triage(
        self, skill: SkillRow, step_name: str, exc: Exception,
        state: StateObject, version: SkillVersionRow, user_id: str,
        retry_count: int,
    ) -> TriageResult:
        if self._triage is None:
            return TriageResult(
                decision=TriageDecision.ESCALATE_TO_CLAUDE,
                rationale="no triage configured; escalating",
            )

        error_type = {
            SchemaValidationError: "schema_validation",
            ToolInvocationError: "tool_exhausted",
            DSLError: "dsl_error",
            jinja2.UndefinedError: "template_error",
        }.get(type(exc), "unknown")

        return await self._triage.handle_failure(TriageInput(
            skill_id=skill.id,
            step_name=step_name,
            error_type=error_type,
            error_message=str(exc),
            state=state.to_dict(),
            skill_yaml_preview=version.yaml_backbone,
            user_id=user_id,
            retry_count=retry_count,
        ))
```

- [ ] **Step 4: Run tests — expect both Phase 1 and new Phase 2 tests pass**

```bash
pytest tests/unit/test_skills_executor.py -v
```

Expected: all single-step tests still pass (Phase 1 compatibility) plus the new multi-step tests.

- [ ] **Step 5: Run full unit suite to check no regressions**

```bash
pytest tests/unit/ -m "not slow" -q
```

Should match the Phase 1 baseline (708 passed, 5 pre-existing failures).

- [ ] **Step 6: Commit**

```bash
git add src/donna/skills/executor.py tests/unit/test_skills_executor.py
git commit -m "feat(skills): multi-step SkillExecutor with tool dispatch, DSL, and triage"
```

---

## Task 10: Skill run persistence

**Files:**
- Create: `src/donna/skills/run_persistence.py`
- Create: `tests/unit/test_skills_run_persistence.py`
- Modify: `src/donna/skills/executor.py` (wire in persistence)

Persist every skill execution: create a `skill_run` row at the start (status=running), write `skill_step_result` rows as steps complete, update the `skill_run` row at the end with status, final_output, state_object, tool_result_cache, timings, and finished_at.

- [ ] **Step 1: Write test**

```python
# tests/unit/test_skills_run_persistence.py
import json
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
import pytest

from donna.skills.run_persistence import SkillRunRepository


@pytest.fixture
async def db(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = await aiosqlite.connect(str(db_path))
    await conn.executescript("""
        CREATE TABLE skill_run (
            id TEXT PRIMARY KEY, skill_id TEXT, skill_version_id TEXT,
            task_id TEXT, automation_run_id TEXT, status TEXT NOT NULL,
            total_latency_ms INTEGER, total_cost_usd REAL,
            state_object TEXT NOT NULL, tool_result_cache TEXT, final_output TEXT,
            escalation_reason TEXT, error TEXT, user_id TEXT NOT NULL,
            started_at TEXT NOT NULL, finished_at TEXT
        );
        CREATE TABLE skill_step_result (
            id TEXT PRIMARY KEY, skill_run_id TEXT NOT NULL,
            step_name TEXT NOT NULL, step_index INTEGER NOT NULL,
            step_kind TEXT NOT NULL, invocation_log_id TEXT,
            prompt_tokens INTEGER, output TEXT, tool_calls TEXT,
            latency_ms INTEGER, validation_status TEXT NOT NULL, error TEXT,
            created_at TEXT NOT NULL
        );
    """)
    await conn.commit()
    yield conn
    await conn.close()


async def test_start_run_creates_row(db):
    repo = SkillRunRepository(db)
    run_id = await repo.start_run(
        skill_id="s1", skill_version_id="v1",
        inputs={"raw_text": "hi"}, user_id="nick",
        task_id=None, automation_run_id=None,
    )

    cursor = await db.execute("SELECT status FROM skill_run WHERE id = ?", (run_id,))
    row = await cursor.fetchone()
    assert row[0] == "running"


async def test_record_step_creates_row(db):
    repo = SkillRunRepository(db)
    run_id = await repo.start_run(
        skill_id="s1", skill_version_id="v1",
        inputs={}, user_id="nick",
        task_id=None, automation_run_id=None,
    )

    await repo.record_step(
        skill_run_id=run_id,
        step_name="extract", step_index=0, step_kind="llm",
        output={"title": "x"}, latency_ms=50,
        validation_status="valid", invocation_log_id="inv-1",
        tool_calls=None, error=None,
    )

    cursor = await db.execute(
        "SELECT step_name, output FROM skill_step_result WHERE skill_run_id = ?",
        (run_id,),
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "extract"
    assert json.loads(rows[0][1]) == {"title": "x"}


async def test_finish_run_updates_row(db):
    repo = SkillRunRepository(db)
    run_id = await repo.start_run(
        skill_id="s1", skill_version_id="v1",
        inputs={}, user_id="nick",
        task_id=None, automation_run_id=None,
    )

    await repo.finish_run(
        skill_run_id=run_id,
        status="succeeded",
        final_output={"priority": 3},
        state_object={"extract": {"title": "x"}, "classify": {"priority": 3}},
        tool_result_cache={},
        total_latency_ms=100,
        total_cost_usd=0.0,
        escalation_reason=None, error=None,
    )

    cursor = await db.execute(
        "SELECT status, final_output, total_latency_ms FROM skill_run WHERE id = ?",
        (run_id,),
    )
    row = await cursor.fetchone()
    assert row[0] == "succeeded"
    assert json.loads(row[1]) == {"priority": 3}
    assert row[2] == 100
```

- [ ] **Step 2: Run test — expect ImportError**

- [ ] **Step 3: Create `src/donna/skills/run_persistence.py`**

```python
"""SkillRunRepository — writes skill_run and skill_step_result rows."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite
import structlog
import uuid6

logger = structlog.get_logger()


class SkillRunRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._conn = connection

    async def start_run(
        self,
        skill_id: str,
        skill_version_id: str,
        inputs: dict,
        user_id: str,
        task_id: str | None,
        automation_run_id: str | None,
    ) -> str:
        """Create a skill_run row with status=running; return the new run_id."""
        run_id = str(uuid6.uuid7())
        now = datetime.now(timezone.utc).isoformat()

        await self._conn.execute(
            """
            INSERT INTO skill_run (
                id, skill_id, skill_version_id, task_id, automation_run_id,
                status, total_latency_ms, total_cost_usd,
                state_object, tool_result_cache, final_output,
                escalation_reason, error, user_id, started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, 'running', NULL, NULL, ?, NULL, NULL,
                      NULL, NULL, ?, ?, NULL)
            """,
            (
                run_id, skill_id, skill_version_id, task_id, automation_run_id,
                json.dumps({"inputs": inputs}),
                user_id, now,
            ),
        )
        await self._conn.commit()
        return run_id

    async def record_step(
        self,
        skill_run_id: str,
        step_name: str,
        step_index: int,
        step_kind: str,
        output: dict | None,
        latency_ms: int,
        validation_status: str,
        invocation_log_id: str | None = None,
        tool_calls: list | None = None,
        prompt_tokens: int | None = None,
        error: str | None = None,
    ) -> str:
        step_id = str(uuid6.uuid7())
        now = datetime.now(timezone.utc).isoformat()

        await self._conn.execute(
            """
            INSERT INTO skill_step_result (
                id, skill_run_id, step_name, step_index, step_kind,
                invocation_log_id, prompt_tokens, output, tool_calls,
                latency_ms, validation_status, error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                step_id, skill_run_id, step_name, step_index, step_kind,
                invocation_log_id, prompt_tokens,
                json.dumps(output) if output is not None else None,
                json.dumps(tool_calls) if tool_calls is not None else None,
                latency_ms, validation_status, error, now,
            ),
        )
        await self._conn.commit()
        return step_id

    async def finish_run(
        self,
        skill_run_id: str,
        status: str,
        final_output: Any,
        state_object: dict,
        tool_result_cache: dict,
        total_latency_ms: int,
        total_cost_usd: float,
        escalation_reason: str | None,
        error: str | None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()

        await self._conn.execute(
            """
            UPDATE skill_run
               SET status = ?, final_output = ?, state_object = ?,
                   tool_result_cache = ?, total_latency_ms = ?, total_cost_usd = ?,
                   escalation_reason = ?, error = ?, finished_at = ?
             WHERE id = ?
            """,
            (
                status,
                json.dumps(final_output) if final_output is not None else None,
                json.dumps(state_object),
                json.dumps(tool_result_cache) if tool_result_cache else None,
                total_latency_ms, total_cost_usd,
                escalation_reason, error, now,
                skill_run_id,
            ),
        )
        await self._conn.commit()
```

- [ ] **Step 4: Wire the repository into `SkillExecutor`**

Modify `src/donna/skills/executor.py` to accept an optional `run_repository` parameter. When set, the executor calls `start_run`, `record_step` (per step), and `finish_run` (at the end). When `None`, the executor runs without persistence (backward compatible with Phase 1 tests and in-memory use).

In the `__init__`, add:

```python
    def __init__(
        self,
        model_router: Any,
        tool_registry: ToolRegistry | None = None,
        triage: TriageAgent | None = None,
        run_repository: Any | None = None,   # NEW
    ) -> None:
        ...
        self._run_repository = run_repository
```

In `execute()`, at the start (after steps are parsed but before the first step runs), call:

```python
        skill_run_id: str | None = None
        if self._run_repository is not None:
            skill_run_id = await self._run_repository.start_run(
                skill_id=skill.id, skill_version_id=version.id,
                inputs=inputs, user_id=user_id,
                task_id=None, automation_run_id=None,
            )
```

After each step (whether success or failure), persist the record if the repository is set:

```python
        if self._run_repository is not None and skill_run_id is not None:
            await self._run_repository.record_step(
                skill_run_id=skill_run_id,
                step_name=record.step_name, step_index=record.step_index,
                step_kind=record.step_kind, output=record.output,
                latency_ms=record.latency_ms,
                validation_status=record.validation_status,
                invocation_log_id=record.invocation_id,
                tool_calls=record.tool_calls, error=record.error,
            )
```

At every return path that exits the execute method, before returning, call `finish_run`:

```python
        if self._run_repository is not None and skill_run_id is not None:
            await self._run_repository.finish_run(
                skill_run_id=skill_run_id,
                status=result.status,
                final_output=result.final_output,
                state_object=result.state,
                tool_result_cache=result.tool_result_cache,
                total_latency_ms=result.total_latency_ms,
                total_cost_usd=result.total_cost_usd,
                escalation_reason=result.escalation_reason,
                error=result.error,
            )
```

Refactor to have a single exit point if that's cleaner — the plan leaves it to the implementer to choose.

- [ ] **Step 5: Run tests — all existing + new persistence tests should pass**

- [ ] **Step 6: Commit**

```bash
git add src/donna/skills/run_persistence.py src/donna/skills/executor.py tests/unit/test_skills_run_persistence.py
git commit -m "feat(skills): persist skill_run and skill_step_result rows during execution"
```

---

## Task 11: Fixture validation harness

**Files:**
- Create: `src/donna/skills/fixtures.py`
- Create: `tests/unit/test_skills_fixtures.py`

Loads fixtures from `skills/<capability>/fixtures/*.json` files AND from `skill_fixture` table rows. Provides `validate_against_fixtures(skill, executor, fixtures)` that runs the skill on each fixture input and checks output shape against `expected_output_shape`.

- [ ] **Step 1: Write test**

```python
# tests/unit/test_skills_fixtures.py
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.skills.fixtures import (
    FixtureLoader, FixtureValidationReport, validate_against_fixtures,
)


async def test_load_fixtures_from_directory(tmp_path: Path):
    fix_dir = tmp_path / "fixtures"
    fix_dir.mkdir()
    (fix_dir / "case_a.json").write_text(json.dumps({
        "input": {"raw_text": "hello"},
        "expected_output_shape": {"title": "string"},
    }))
    (fix_dir / "case_b.json").write_text(json.dumps({
        "input": {"raw_text": "goodbye"},
        "expected_output_shape": {"title": "string"},
    }))

    loader = FixtureLoader()
    fixtures = loader.load_from_directory(fix_dir)
    assert len(fixtures) == 2
    assert {f.case_name for f in fixtures} == {"case_a", "case_b"}


async def test_validate_against_fixtures_passes():
    skill = MagicMock(id="s1", capability_name="parse_task", current_version_id="v1")
    executor = AsyncMock()
    executor.execute.return_value = MagicMock(
        status="succeeded",
        final_output={"title": "Q2 review"},
    )

    loader = FixtureLoader()
    fixtures = [
        loader._make_fixture("case_a", {"raw_text": "Q2"}, {"type": "object", "required": ["title"]}),
    ]

    report = await validate_against_fixtures(skill, executor, fixtures, version=MagicMock(id="v1"))

    assert report.total == 1
    assert report.passed == 1
    assert report.failed == 0


async def test_validate_against_fixtures_detects_failure():
    skill = MagicMock(id="s1", capability_name="parse_task", current_version_id="v1")
    executor = AsyncMock()
    executor.execute.return_value = MagicMock(
        status="succeeded",
        final_output={"wrong_field": "x"},
    )

    loader = FixtureLoader()
    fixtures = [
        loader._make_fixture("case_a", {"raw_text": "Q2"}, {"type": "object", "required": ["title"]}),
    ]

    report = await validate_against_fixtures(skill, executor, fixtures, version=MagicMock(id="v1"))

    assert report.failed == 1
    assert report.failure_details[0].case_name == "case_a"
```

- [ ] **Step 2: Run test — expect ImportError**

- [ ] **Step 3: Create `src/donna/skills/fixtures.py`**

```python
"""Fixture loader and validation harness for skills."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from donna.skills.validation import SchemaValidationError, validate_output

logger = structlog.get_logger()


@dataclass(slots=True)
class Fixture:
    case_name: str
    input: dict
    expected_output_shape: dict | None = None


@dataclass(slots=True)
class FixtureFailureDetail:
    case_name: str
    reason: str


@dataclass(slots=True)
class FixtureValidationReport:
    total: int
    passed: int
    failed: int
    failure_details: list[FixtureFailureDetail] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


class FixtureLoader:
    def load_from_directory(self, fixtures_dir: Path) -> list[Fixture]:
        fixtures: list[Fixture] = []
        if not fixtures_dir.exists():
            return fixtures

        for file in sorted(fixtures_dir.glob("*.json")):
            try:
                with open(file) as f:
                    data = json.load(f)
                fixtures.append(self._make_fixture(
                    case_name=file.stem,
                    input=data["input"],
                    expected_output_shape=data.get("expected_output_shape"),
                ))
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning("fixture_load_failed", file=str(file), error=str(exc))

        return fixtures

    @staticmethod
    def _make_fixture(case_name: str, input: dict, expected_output_shape: dict | None = None) -> Fixture:
        return Fixture(case_name=case_name, input=input, expected_output_shape=expected_output_shape)


async def validate_against_fixtures(
    skill: Any,
    executor: Any,
    fixtures: list[Fixture],
    version: Any,
) -> FixtureValidationReport:
    total = len(fixtures)
    passed = 0
    failures: list[FixtureFailureDetail] = []

    for fix in fixtures:
        try:
            result = await executor.execute(
                skill=skill, version=version,
                inputs=fix.input, user_id="fixture_harness",
            )

            if result.status != "succeeded":
                failures.append(FixtureFailureDetail(
                    case_name=fix.case_name,
                    reason=f"run status={result.status}: {result.error or result.escalation_reason}",
                ))
                continue

            if fix.expected_output_shape:
                try:
                    validate_output(result.final_output, fix.expected_output_shape)
                except SchemaValidationError as exc:
                    failures.append(FixtureFailureDetail(case_name=fix.case_name, reason=str(exc)))
                    continue

            passed += 1

        except Exception as exc:
            failures.append(FixtureFailureDetail(
                case_name=fix.case_name,
                reason=f"exception: {exc}",
            ))

    return FixtureValidationReport(
        total=total, passed=passed, failed=total - passed,
        failure_details=failures,
    )
```

- [ ] **Step 4: Run test — expect pass**

- [ ] **Step 5: Commit**

```bash
git add src/donna/skills/fixtures.py tests/unit/test_skills_fixtures.py
git commit -m "feat(skills): add fixture loader and validation harness"
```

---

## Task 12: Multi-step demo skill — `fetch_and_summarize`

**Files:**
- Create: `skills/fetch_and_summarize/skill.yaml`
- Create: `skills/fetch_and_summarize/steps/plan.md`
- Create: `skills/fetch_and_summarize/steps/summarize.md`
- Create: `skills/fetch_and_summarize/schemas/plan_v1.json`
- Create: `skills/fetch_and_summarize/schemas/summarize_v1.json`
- Create: `skills/fetch_and_summarize/fixtures/basic_html.json`
- Create: `skills/fetch_and_summarize/fixtures/empty_response.json`

This capability isn't wired to any real task type — it exists to exercise the Phase 2 execution machinery end-to-end. It has three steps: LLM plans what to fetch, tool fetches the URL, LLM summarizes the result. The corresponding capability must be seeded separately (Task 13 adds it to a migration).

- [ ] **Step 1: Create `skills/fetch_and_summarize/skill.yaml`**

```yaml
capability_name: fetch_and_summarize
version: 1
description: |
  Fetch a URL and return a short summary of its content.
  Demonstrates the multi-step Phase 2 executor with an LLM planning
  step, a tool-only fetch step, and an LLM summarization step.

inputs:
  schema:
    type: object
    properties:
      url:
        type: string
    required: [url]

steps:
  - name: plan
    kind: llm
    prompt: steps/plan.md
    output_schema: schemas/plan_v1.json

  - name: fetch
    kind: tool
    tools: [web_fetch]
    tool_invocations:
      - tool: web_fetch
        args:
          url: "{{ inputs.url }}"
          timeout_s: 10
        retry:
          max_attempts: 2
          backoff_s: [1, 3]
        store_as: page

  - name: summarize
    kind: llm
    prompt: steps/summarize.md
    output_schema: schemas/summarize_v1.json

final_output: "{{ state.summarize }}"
```

- [ ] **Step 2: Create step prompts and schemas**

`steps/plan.md`:

```markdown
You are preparing to fetch and summarize content from a URL.

URL: {{ inputs.url }}

Return a JSON object with:
- expected_content_type: one of "article", "product_page", "documentation", "other"
- key_questions: 1-3 questions the summary should answer
```

`steps/summarize.md`:

```markdown
You have fetched a web page. Summarize its content.

URL: {{ inputs.url }}
Expected content type: {{ state.plan.expected_content_type }}
Key questions to answer: {{ state.plan.key_questions }}
HTTP status: {{ state.fetch.page.status_code }}

Page content (truncated to fit context):
{{ state.fetch.page.body[:4000] }}

Return a JSON object with:
- summary: 2-3 sentence summary
- answers: map of question -> one-sentence answer, one per key question
- confidence: 0.0-1.0
```

`schemas/plan_v1.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {
    "expected_content_type": {"type": "string", "enum": ["article", "product_page", "documentation", "other"]},
    "key_questions": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3},
    "escalate": {"type": "object"}
  },
  "required": ["expected_content_type", "key_questions"],
  "additionalProperties": false
}
```

`schemas/summarize_v1.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {
    "summary": {"type": "string", "minLength": 1},
    "answers": {"type": "object"},
    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    "escalate": {"type": "object"}
  },
  "required": ["summary", "answers", "confidence"],
  "additionalProperties": false
}
```

- [ ] **Step 3: Create fixtures**

`fixtures/basic_html.json`:

```json
{
  "input": {"url": "https://example.com/article"},
  "expected_output_shape": {
    "type": "object",
    "properties": {
      "summary": {"type": "string"},
      "answers": {"type": "object"},
      "confidence": {"type": "number"}
    },
    "required": ["summary", "answers", "confidence"]
  }
}
```

`fixtures/empty_response.json`:

```json
{
  "input": {"url": "https://example.com/empty"},
  "expected_output_shape": {
    "type": "object",
    "properties": {
      "summary": {"type": "string"}
    },
    "required": ["summary"]
  }
}
```

- [ ] **Step 4: Commit**

```bash
git add skills/fetch_and_summarize/
git commit -m "feat(skills): add fetch_and_summarize multi-step demo skill"
```

---

## Task 13: Seed the `fetch_and_summarize` capability

**Files:**
- Create: `alembic/versions/seed_fetch_and_summarize.py`

- [ ] **Step 1: Create the migration**

`alembic/versions/seed_fetch_and_summarize.py`:

```python
"""seed fetch_and_summarize capability

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-04-15
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        sa.text("""
            INSERT OR IGNORE INTO capability
              (id, name, description, input_schema, trigger_type, status, created_at, created_by)
            VALUES
              (:id, :name, :description, :input_schema, 'on_manual', 'active', :created_at, 'seed')
        """),
        {
            "id": "seed-fetch_and_summarize",
            "name": "fetch_and_summarize",
            "description": "Fetch a URL and return a short summary (Phase 2 demo)",
            "input_schema": json.dumps({
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            }),
            "created_at": now,
        },
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM skill_version WHERE skill_id IN (SELECT id FROM skill WHERE capability_name = 'fetch_and_summarize')"),
    )
    conn.execute(sa.text("DELETE FROM skill WHERE capability_name = 'fetch_and_summarize'"))
    conn.execute(sa.text("DELETE FROM capability WHERE name = 'fetch_and_summarize'"))
```

- [ ] **Step 2: Test**

```bash
DONNA_DB_PATH=/tmp/donna_test_seed_fas.db alembic upgrade head
sqlite3 /tmp/donna_test_seed_fas.db "SELECT name FROM capability WHERE name = 'fetch_and_summarize';"
```

Expected: `fetch_and_summarize`.

```bash
rm /tmp/donna_test_seed_fas.db
```

- [ ] **Step 3: Commit**

```bash
git add alembic/versions/seed_fetch_and_summarize.py
git commit -m "feat(db): seed fetch_and_summarize capability for Phase 2 demo"
```

---

## Task 14: Dashboard routes — skill runs and step results

**Files:**
- Create: `src/donna/api/routes/skill_runs.py`
- Modify: `src/donna/api/__init__.py`
- Create: `tests/unit/test_api_skill_runs.py`

Read-only routes:
- `GET /admin/skills/{skill_id}/runs` — paginated list of runs for a skill
- `GET /admin/skill-runs/{run_id}` — full run detail with step results
- `GET /admin/skill-runs` — recent runs across all skills (paginated)

- [ ] **Step 1: Write test**

```python
# tests/unit/test_api_skill_runs.py
from pathlib import Path
import aiosqlite
import pytest


@pytest.fixture
async def db_with_runs(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = await aiosqlite.connect(str(db_path))
    await conn.executescript("""
        CREATE TABLE skill_run (
            id TEXT PRIMARY KEY, skill_id TEXT, skill_version_id TEXT,
            task_id TEXT, automation_run_id TEXT, status TEXT,
            total_latency_ms INTEGER, total_cost_usd REAL,
            state_object TEXT, tool_result_cache TEXT, final_output TEXT,
            escalation_reason TEXT, error TEXT, user_id TEXT,
            started_at TEXT, finished_at TEXT
        );
        CREATE INDEX ix_skill_run_skill_id ON skill_run(skill_id);
        CREATE INDEX ix_skill_run_started_at ON skill_run(started_at);
        CREATE TABLE skill_step_result (
            id TEXT PRIMARY KEY, skill_run_id TEXT, step_name TEXT,
            step_index INTEGER, step_kind TEXT, invocation_log_id TEXT,
            prompt_tokens INTEGER, output TEXT, tool_calls TEXT,
            latency_ms INTEGER, validation_status TEXT, error TEXT,
            created_at TEXT
        );
    """)
    await conn.execute("""
        INSERT INTO skill_run (id, skill_id, skill_version_id, status, state_object, user_id, started_at, total_latency_ms)
        VALUES ('r1', 's1', 'v1', 'succeeded', '{}', 'nick', '2026-04-15T10:00:00', 150)
    """)
    await conn.execute("""
        INSERT INTO skill_run (id, skill_id, skill_version_id, status, state_object, user_id, started_at, total_latency_ms)
        VALUES ('r2', 's1', 'v1', 'failed', '{}', 'nick', '2026-04-15T11:00:00', 75)
    """)
    await conn.execute("""
        INSERT INTO skill_step_result (id, skill_run_id, step_name, step_index, step_kind, output, latency_ms, validation_status, created_at)
        VALUES ('sr1', 'r1', 'extract', 0, 'llm', '{"title":"x"}', 50, 'valid', '2026-04-15T10:00:01')
    """)
    await conn.commit()
    yield conn
    await conn.close()


async def test_list_runs_for_skill(db_with_runs):
    """Direct test via the route handler without full FastAPI setup."""
    from donna.api.routes.skill_runs import list_runs_for_skill
    from unittest.mock import MagicMock

    request = MagicMock()
    request.app.state.db.connection = db_with_runs

    result = await list_runs_for_skill(skill_id="s1", request=request, limit=100)

    assert result["count"] == 2
    assert {r["id"] for r in result["runs"]} == {"r1", "r2"}


async def test_get_run_detail(db_with_runs):
    from donna.api.routes.skill_runs import get_run_detail
    from unittest.mock import MagicMock

    request = MagicMock()
    request.app.state.db.connection = db_with_runs

    result = await get_run_detail(run_id="r1", request=request)

    assert result["id"] == "r1"
    assert len(result["step_results"]) == 1
    assert result["step_results"][0]["step_name"] == "extract"


async def test_get_run_detail_404(db_with_runs):
    from donna.api.routes.skill_runs import get_run_detail
    from fastapi import HTTPException
    from unittest.mock import MagicMock

    request = MagicMock()
    request.app.state.db.connection = db_with_runs

    with pytest.raises(HTTPException) as excinfo:
        await get_run_detail(run_id="missing", request=request)
    assert excinfo.value.status_code == 404
```

- [ ] **Step 2: Run test — expect ImportError**

- [ ] **Step 3: Create `src/donna/api/routes/skill_runs.py`**

```python
"""Read-only API routes for skill runs and step results."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from donna.skills.runs import (
    SELECT_SKILL_RUN, SELECT_SKILL_STEP_RESULT,
    row_to_skill_run, row_to_step_result,
)

router = APIRouter()


def _run_to_dict(run) -> dict[str, Any]:
    return {
        "id": run.id,
        "skill_id": run.skill_id,
        "skill_version_id": run.skill_version_id,
        "status": run.status,
        "total_latency_ms": run.total_latency_ms,
        "total_cost_usd": run.total_cost_usd,
        "escalation_reason": run.escalation_reason,
        "error": run.error,
        "user_id": run.user_id,
        "started_at": str(run.started_at),
        "finished_at": str(run.finished_at) if run.finished_at else None,
    }


def _step_to_dict(step) -> dict[str, Any]:
    return {
        "id": step.id,
        "step_name": step.step_name,
        "step_index": step.step_index,
        "step_kind": step.step_kind,
        "output": step.output,
        "tool_calls": step.tool_calls,
        "latency_ms": step.latency_ms,
        "validation_status": step.validation_status,
        "error": step.error,
    }


@router.get("/skills/{skill_id}/runs")
async def list_runs_for_skill(
    skill_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    conn = request.app.state.db.connection
    cursor = await conn.execute(
        f"""
        SELECT {SELECT_SKILL_RUN} FROM skill_run
         WHERE skill_id = ?
         ORDER BY started_at DESC
         LIMIT ? OFFSET ?
        """,
        (skill_id, limit, offset),
    )
    rows = await cursor.fetchall()
    runs = [_run_to_dict(row_to_skill_run(r)) for r in rows]
    return {"runs": runs, "count": len(runs)}


@router.get("/skill-runs/{run_id}")
async def get_run_detail(
    run_id: str,
    request: Request,
) -> dict[str, Any]:
    conn = request.app.state.db.connection

    cursor = await conn.execute(
        f"SELECT {SELECT_SKILL_RUN} FROM skill_run WHERE id = ?",
        (run_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Skill run '{run_id}' not found")
    run = row_to_skill_run(row)

    cursor = await conn.execute(
        f"""
        SELECT {SELECT_SKILL_STEP_RESULT} FROM skill_step_result
         WHERE skill_run_id = ?
         ORDER BY step_index ASC
        """,
        (run_id,),
    )
    step_rows = await cursor.fetchall()
    step_results = [_step_to_dict(row_to_step_result(r)) for r in step_rows]

    result = _run_to_dict(run)
    result["state_object"] = run.state_object
    result["final_output"] = run.final_output
    result["step_results"] = step_results
    return result


@router.get("/skill-runs")
async def list_recent_runs(
    request: Request,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    conn = request.app.state.db.connection
    if status:
        cursor = await conn.execute(
            f"SELECT {SELECT_SKILL_RUN} FROM skill_run WHERE status = ? ORDER BY started_at DESC LIMIT ?",
            (status, limit),
        )
    else:
        cursor = await conn.execute(
            f"SELECT {SELECT_SKILL_RUN} FROM skill_run ORDER BY started_at DESC LIMIT ?",
            (limit,),
        )
    rows = await cursor.fetchall()
    runs = [_run_to_dict(row_to_skill_run(r)) for r in rows]
    return {"runs": runs, "count": len(runs)}
```

- [ ] **Step 4: Register the route**

Edit `src/donna/api/__init__.py`. Add import:

```python
from donna.api.routes import skill_runs as skill_runs_routes
```

Register alongside the existing skill routes:

```python
    app.include_router(skill_runs_routes.router, prefix="/admin", tags=["skill-runs"])
```

- [ ] **Step 5: Run tests — expect pass**

- [ ] **Step 6: Commit**

```bash
git add src/donna/api/routes/skill_runs.py src/donna/api/__init__.py tests/unit/test_api_skill_runs.py
git commit -m "feat(api): add skill run and step result dashboard routes"
```

---

## Task 15: Phase 2 end-to-end integration test

**Files:**
- Create: `tests/integration/test_skill_system_phase_2_e2e.py`

Verifies the Phase 2 handoff contract:
- H2.1: multi-step skill executes and produces expected outputs
- H2.2: tool step with for_each fans out correctly
- H2.3: escalate signal short-circuits
- H2.4: tool failure → triage → graceful result
- H2.5: skill_run and skill_step_result rows are persisted
- H2.6: ToolRegistry allowlist is enforced

```python
"""Phase 2 end-to-end integration test.

Verifies the handoff contract from plan §7 Phase 2:
  H2.1: multi-step skill executes all steps with state accumulation
  H2.2: tool step with for_each fans out and collects results
  H2.3: escalate signal short-circuits a multi-step run
  H2.4: tool failure triggers triage and produces structured result
  H2.5: skill_run + skill_step_result rows persist after execution
  H2.6: ToolRegistry allowlist prevents unauthorized dispatches
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from donna.skills.executor import SkillExecutor
from donna.skills.models import SkillRow, SkillVersionRow
from donna.skills.run_persistence import SkillRunRepository
from donna.skills.tool_registry import ToolNotAllowedError, ToolRegistry
from donna.skills.triage import TriageAgent, TriageDecision, TriageResult

from datetime import datetime, timezone


@pytest.fixture
async def db_with_run_tables(tmp_path: Path):
    db_path = tmp_path / "phase2.db"
    conn = await aiosqlite.connect(str(db_path))
    await conn.executescript("""
        CREATE TABLE skill_run (
            id TEXT PRIMARY KEY, skill_id TEXT, skill_version_id TEXT,
            task_id TEXT, automation_run_id TEXT, status TEXT NOT NULL,
            total_latency_ms INTEGER, total_cost_usd REAL,
            state_object TEXT NOT NULL, tool_result_cache TEXT, final_output TEXT,
            escalation_reason TEXT, error TEXT, user_id TEXT NOT NULL,
            started_at TEXT NOT NULL, finished_at TEXT
        );
        CREATE TABLE skill_step_result (
            id TEXT PRIMARY KEY, skill_run_id TEXT NOT NULL,
            step_name TEXT NOT NULL, step_index INTEGER NOT NULL,
            step_kind TEXT NOT NULL, invocation_log_id TEXT,
            prompt_tokens INTEGER, output TEXT, tool_calls TEXT,
            latency_ms INTEGER, validation_status TEXT NOT NULL, error TEXT,
            created_at TEXT NOT NULL
        );
    """)
    await conn.commit()
    yield conn
    await conn.close()


def _skill() -> SkillRow:
    return SkillRow(
        id="s1", capability_name="demo", current_version_id="v1",
        state="sandbox", requires_human_gate=False, baseline_agreement=None,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )


def _version(yaml_backbone: str, step_content: dict, output_schemas: dict) -> SkillVersionRow:
    return SkillVersionRow(
        id="v1", skill_id="s1", version_number=1,
        yaml_backbone=yaml_backbone,
        step_content=step_content, output_schemas=output_schemas,
        created_by="seed", changelog=None,
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.integration
async def test_h2_1_multistep_skill_accumulates_state(db_with_run_tables):
    yaml_backbone = """
capability_name: demo
version: 1
steps:
  - name: step_a
    kind: llm
    prompt: pa.md
    output_schema: sa.json
  - name: step_b
    kind: llm
    prompt: pb.md
    output_schema: sb.json
final_output: "{{ state.step_b }}"
"""
    version = _version(
        yaml_backbone,
        step_content={"step_a": "Extract from: {{ inputs.raw }}", "step_b": "Classify: {{ state.step_a.title }}"},
        output_schemas={
            "step_a": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
            "step_b": {"type": "object", "properties": {"priority": {"type": "integer"}}, "required": ["priority"]},
        },
    )

    router = AsyncMock()
    router.complete.side_effect = [
        ({"title": "review"}, MagicMock(invocation_id="i1", cost_usd=0.0)),
        ({"priority": 2}, MagicMock(invocation_id="i2", cost_usd=0.0)),
    ]

    repo = SkillRunRepository(db_with_run_tables)
    executor = SkillExecutor(router, ToolRegistry(), triage=None, run_repository=repo)

    result = await executor.execute(skill=_skill(), version=version, inputs={"raw": "draft the review"}, user_id="nick")

    assert result.status == "succeeded"
    assert result.state["step_a"]["title"] == "review"
    assert result.state["step_b"]["priority"] == 2


@pytest.mark.integration
async def test_h2_2_for_each_fan_out(db_with_run_tables):
    yaml_backbone = """
capability_name: demo
version: 1
steps:
  - name: fetch_many
    kind: tool
    tools: [mock_tool]
    tool_invocations:
      - for_each: "{{ inputs.urls }}"
        as: url
        tool: mock_tool
        args: {u: "{{ url }}"}
        store_as: "r{{ loop.index0 }}"
final_output: "{{ state.fetch_many }}"
"""
    version = _version(yaml_backbone, step_content={}, output_schemas={})

    async def mock_tool(u: str):
        return {"got": u}

    registry = ToolRegistry()
    registry.register("mock_tool", mock_tool)
    executor = SkillExecutor(AsyncMock(), registry, triage=None, run_repository=SkillRunRepository(db_with_run_tables))

    result = await executor.execute(
        skill=_skill(), version=version,
        inputs={"urls": ["a", "b", "c"]}, user_id="nick",
    )

    assert result.status == "succeeded"
    assert result.state["fetch_many"]["r0"] == {"got": "a"}
    assert result.state["fetch_many"]["r2"] == {"got": "c"}


@pytest.mark.integration
async def test_h2_3_escalate_short_circuits(db_with_run_tables):
    yaml_backbone = """
capability_name: demo
version: 1
steps:
  - name: s1
    kind: llm
    prompt: p.md
    output_schema: s.json
  - name: s2
    kind: llm
    prompt: p2.md
    output_schema: s2.json
final_output: "{{ state.s2 }}"
"""
    version = _version(
        yaml_backbone,
        step_content={"s1": "x", "s2": "y"},
        output_schemas={"s1": {"type": "object", "properties": {"escalate": {"type": "object"}}}, "s2": {"type": "object"}},
    )

    router = AsyncMock()
    router.complete.return_value = ({"escalate": {"reason": "no idea"}}, MagicMock(invocation_id="i1", cost_usd=0.0))

    executor = SkillExecutor(router, ToolRegistry(), triage=None, run_repository=SkillRunRepository(db_with_run_tables))
    result = await executor.execute(skill=_skill(), version=version, inputs={}, user_id="nick")

    assert result.status == "escalated"
    assert result.escalation_reason == "no idea"
    assert router.complete.call_count == 1


@pytest.mark.integration
async def test_h2_4_tool_failure_triggers_triage(db_with_run_tables):
    yaml_backbone = """
capability_name: demo
version: 1
steps:
  - name: tool_step
    kind: tool
    tools: [failing_tool]
    tool_invocations:
      - tool: failing_tool
        args: {}
        retry: {max_attempts: 2, backoff_s: [0, 0]}
        store_as: result
final_output: "{{ state.tool_step }}"
"""
    version = _version(yaml_backbone, step_content={}, output_schemas={})

    async def failing_tool(**kwargs):
        raise RuntimeError("cannot reach endpoint")

    registry = ToolRegistry()
    registry.register("failing_tool", failing_tool)

    triage = AsyncMock()
    triage.handle_failure.return_value = TriageResult(
        decision=TriageDecision.ESCALATE_TO_CLAUDE,
        rationale="tool unreachable",
    )

    executor = SkillExecutor(AsyncMock(), registry, triage=triage, run_repository=SkillRunRepository(db_with_run_tables))

    result = await executor.execute(skill=_skill(), version=version, inputs={}, user_id="nick")

    assert result.status == "escalated"
    triage.handle_failure.assert_awaited_once()


@pytest.mark.integration
async def test_h2_5_persistence_writes_rows(db_with_run_tables):
    yaml_backbone = """
capability_name: demo
version: 1
steps:
  - name: only
    kind: llm
    prompt: p.md
    output_schema: s.json
final_output: "{{ state.only }}"
"""
    version = _version(
        yaml_backbone,
        step_content={"only": "prompt"},
        output_schemas={"only": {"type": "object", "properties": {"v": {"type": "integer"}}, "required": ["v"]}},
    )

    router = AsyncMock()
    router.complete.return_value = ({"v": 42}, MagicMock(invocation_id="i1", cost_usd=0.0))

    repo = SkillRunRepository(db_with_run_tables)
    executor = SkillExecutor(router, ToolRegistry(), triage=None, run_repository=repo)

    await executor.execute(skill=_skill(), version=version, inputs={}, user_id="nick")

    cursor = await db_with_run_tables.execute("SELECT status, final_output FROM skill_run")
    rows = await cursor.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "succeeded"

    cursor = await db_with_run_tables.execute("SELECT step_name FROM skill_step_result")
    step_rows = await cursor.fetchall()
    assert len(step_rows) == 1
    assert step_rows[0][0] == "only"


@pytest.mark.integration
async def test_h2_6_allowlist_enforced(db_with_run_tables):
    yaml_backbone = """
capability_name: demo
version: 1
steps:
  - name: unauthorized
    kind: tool
    tools: [allowed_tool]
    tool_invocations:
      - tool: forbidden_tool
        args: {}
        store_as: r
final_output: "{}"
"""
    version = _version(yaml_backbone, step_content={}, output_schemas={})

    async def forbidden(**kwargs):
        return {"v": 1}

    registry = ToolRegistry()
    registry.register("forbidden_tool", forbidden)

    triage = AsyncMock()
    triage.handle_failure.return_value = TriageResult(
        decision=TriageDecision.ESCALATE_TO_CLAUDE,
        rationale="tool not allowed",
    )

    executor = SkillExecutor(AsyncMock(), registry, triage=triage, run_repository=SkillRunRepository(db_with_run_tables))

    result = await executor.execute(skill=_skill(), version=version, inputs={}, user_id="nick")

    # Result should be escalated (triage caught the ToolInvocationError).
    assert result.status == "escalated"
```

- [ ] **Step 1 (of this task): Run the integration tests**

```bash
pytest tests/integration/test_skill_system_phase_2_e2e.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 2: Commit**

```bash
git add tests/integration/test_skill_system_phase_2_e2e.py
git commit -m "test(skills): add Phase 2 end-to-end handoff contract test"
```

---

## Task 16: Update spec drift log and Phase 2 handoff contract

**Files:**
- Modify: `docs/superpowers/specs/2026-04-15-skill-system-and-challenger-refactor-design.md`

Record any deviations from the spec introduced during Phase 2 implementation.

- [ ] **Step 1: Add drift entry**

If any deviations occurred (e.g., the triage "retry requested" handling is deferred, or any other adjustment), add to §8 Drift Log using the standard format. If no deviations occurred, add:

```markdown
#### 2026-04-15 — Phase 2, §6.4 Triage retry loop
- **What changed**: Triage's RETRY_STEP decision is not yet a true retry
  loop in the executor. When triage asks for a retry, the executor currently
  returns an escalated result with a descriptive reason, instead of actually
  re-running the failed step.
- **Why**: Full retry-with-prompt-augmentation requires inserting the
  modified_prompt_additions into the step context, which adds state-management
  complexity. Deferred to make Phase 2 execution machinery shippable sooner.
- **Handoff contracts affected**: Phase 2 handoff (triage semantics), Phase 3
  handoff (full triage retry will be in scope once lifecycle + evolution are
  being built).
- **Action required for downstream phases**: Phase 3 should either implement
  the retry loop or formally mark triage's RETRY_STEP as deprecated in the
  decision enum.
```

- [ ] **Step 2: Update Phase 2 handoff contract**

In §7 Phase 2 Handoff Contract, add a note about triage retry semantics:

> - `TriageAgent.handle_failure` returns one of five decisions. `RETRY_STEP` is currently surfaced to the executor but not yet executed as a retry loop (See Drift Log 2026-04-15 entry for Phase 2 §6.4); the executor treats it as an escalate with a descriptive reason.

- [ ] **Step 3: Check off Phase 2 requirements in §9**

Tick the following in the Requirements Checklist:
- R12, R13, R14, R15, R16

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-04-15-skill-system-and-challenger-refactor-design.md
git commit -m "docs(spec): record Phase 2 drift and tick Phase 2 requirements"
```

---

## Self-Review (fill in during execution)

After completing all tasks, run:

```bash
pytest tests/unit/ -v -m "not slow"
pytest tests/unit/ -v -m slow
pytest tests/integration/ -v
```

Verify:
- [ ] All unit tests pass (target: 750+ passed, same 5 pre-existing failures, no new ones)
- [ ] All Phase 2 integration tests pass
- [ ] Phase 1 integration tests still pass
- [ ] Existing callers of `SkillExecutor` that pass only `(model_router,)` still work (backward compatibility)
- [ ] Drift log entries are accurate
- [ ] Requirements checklist reflects Phase 2 completion

---

## Phase 2 Acceptance Scenarios (from spec §7)

**AS-2.1**: Run a multi-step skill (`fetch_and_summarize` demo) against a fixture via the fixture harness. All steps execute, state object is populated, final output is returned, `skill_step_result` rows record each step with its invocation/tool call links.

**AS-2.2**: Skill step declares `for_each` over URLs. Executor fans out web_fetch calls, results stored under `state.step_name.fetched[vendor]` keys. No LLM call on the tool-kind step.

**AS-2.3**: Skill step's LLM call returns an output with `escalate: {reason: "insufficient data"}`. Executor short-circuits, task is routed to claude_native (via the Phase 1 dispatcher shadow path), `skill_run.escalation_reason` populated.

**AS-2.4**: Skill step's `web_fetch` times out 3 times. Retry policy exhausts. `on_failure: escalate` triggers (by virtue of ToolInvocationError bubbling to the executor's triage handler). Triage agent decides to escalate to Claude. Task completes via claude_native.

**AS-2.5**: Skill step LLM returns output that fails schema validation. Executor catches, calls triage, triage decides retry → Phase 2 treats as escalate (drift entry 2026-04-15). End-to-end run still produces a structured result; user sees claude_native output.

---

## Notes for the Implementer

- **Prefer TDD.** Every task starts with a failing test; implement only enough code to make it pass; commit when green.
- **Phase 2 depends on Phase 1.** Do not start Phase 2 before Phase 1 is fully committed. Phase 2 Task 1's migration depends on Phase 1 migration `b2c3d4e5f6a7` being the Alembic head.
- **Task 9 (multi-step executor) is the most sensitive.** The Phase 1 `SkillExecutor` is replaced. Run the full Phase 1 test suite (`pytest tests/unit/test_skills_executor.py -v`) to verify single-step skills still work — the Phase 1 tests should pass unchanged after the rewrite.
- **Model selection for subagents:**
  - Cheap model (haiku-class) for: Tasks 1, 2, 3, 4, 5, 7, 11, 12, 13, 14, 16
  - Standard model for: Tasks 6, 8, 10, 15
  - Most capable model for: **Task 9 (multi-step executor)** — substantial logic, sensitivity to existing tests, multiple return paths.
- **Triage retry loop is deferred.** See drift log entry. Triage asking for RETRY is treated as escalate in Phase 2; Phase 3 can build the real retry mechanism.
- **No tools beyond `web_fetch` in Phase 2.** Additional tools (email_read, calendar_read, etc.) are scoped to Phase 5 (automation subsystem) or later.
- **Check `docs/phase-1-skill-system-setup.md`** for the two startup-wiring actions that were left to the user. Phase 2 adds another — wiring the `ToolRegistry`, `TriageAgent`, and `SkillRunRepository` into application startup. Add this to the setup doc at the end of Phase 2.
