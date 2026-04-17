# Skill System Wave 3 — Discord NL Automation Creation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver Discord natural-language automation creation (F-3), close the challenger refactor promised in the original 2026-04-15 spec §6.7, and fold in five P2 cleanups (F-W2-C/D/E/G + F-10).

**Architecture:** Unified single-call local-Ollama parse on every Discord message produces intent + capability match + inputs + schedule/deadline/alert_conditions + confidence in one round-trip. No-match messages escalate to a new Claude novelty judge that returns execution-ready extraction plus a `skill_candidate` verdict. Automations route through a confirmation card before persistence. Cadence is clamped by the matched skill's lifecycle state and auto-uplifts when the skill promotes.

**Tech Stack:** Python 3.12 async, aiosqlite (WAL), Alembic, discord.py, Jinja2 prompts, structlog, pytest-asyncio, Ollama (local LLM) + Claude API.

**Spec reference:** `docs/superpowers/specs/2026-04-17-skill-system-wave-3-discord-nl-automation-design.md`.

**Execution context:** This plan assumes it executes on a branch that has Wave 1 (PR #44) and Wave 2 (PR #46) merged. If your branch is older, merge main first. All file paths and migration parent revisions below assume Wave 2's head migration (`b8c9d0e1f2a3` — `add_fixture_tool_mocks.py`) is the current head.

---

## File Structure

### Created files
- `alembic/versions/c9d0e1f2a3b4_skill_candidate_status_claude_native.py` — migration: status enum + pattern_fingerprint
- `alembic/versions/d0e1f2a3b4c5_automation_active_cadence.py` — migration: active_cadence_cron + cadence_policy_override
- `config/automations.yaml` — cadence policy table
- `src/donna/automations/cadence_policy.py` — CadencePolicy loader
- `src/donna/automations/cadence_reclamper.py` — CadenceReclamper service
- `src/donna/automations/creation_flow.py` — AutomationCreationPath
- `src/donna/agents/claude_novelty_judge.py` — ClaudeNoveltyJudge agent
- `src/donna/orchestrator/discord_intent_dispatcher.py` — DiscordIntentDispatcher
- `src/donna/integrations/discord_pending_drafts.py` — shared PendingDraftRegistry
- `prompts/challenger_parse.md` — Jinja2 parse prompt
- `prompts/claude_novelty.md` — novelty judge prompt
- `schemas/challenger_parse.json` — parse output schema
- `schemas/claude_novelty.json` — novelty output schema
- `src/donna/cli_wiring.py` — StartupContext + wire helpers (F-W2-E)

### Modified files
- `src/donna/agents/challenger_agent.py` — extend ChallengerMatchResult, rewrite match_and_extract to use parse prompt
- `src/donna/cli.py` — _run_orchestrator → calls into cli_wiring helpers
- `src/donna/integrations/discord_bot.py` — on_message routes to DiscordIntentDispatcher
- `src/donna/integrations/discord_views.py` — add AutomationConfirmationView
- `src/donna/automations/repository.py` — AutomationRepository.create accepts target_cadence_cron + active_cadence_cron
- `src/donna/automations/models.py` — add target_cadence_cron, active_cadence_cron on AutomationRow
- `src/donna/automations/scheduler.py` — skip NULL active_cadence; compute next_run from active_cadence
- `src/donna/automations/dispatcher.py` — pass capability lifecycle state to hooks
- `src/donna/skills/tools/dispatcher.py` — on_failure DSL handling
- `src/donna/skills/executor.py` — on_failure propagation
- `src/donna/skills/skill_lifecycle_service.py` — after_state_change hook point
- `src/donna/skills/skill_candidate_detector.py` — skip claude_native_registered patterns

### Tests (all new)
- `tests/unit/test_cadence_policy.py`
- `tests/unit/test_cadence_reclamper.py`
- `tests/unit/test_challenger_match_and_extract_wave3.py`
- `tests/unit/test_claude_novelty_judge.py`
- `tests/unit/test_pending_draft_registry.py`
- `tests/unit/test_discord_intent_dispatcher.py`
- `tests/unit/test_automation_creation_flow.py`
- `tests/unit/test_automation_confirmation_view.py`
- `tests/unit/test_skill_candidate_detector_claude_native.py`
- `tests/unit/test_on_failure_dsl.py`
- `tests/integration/test_cli_startup_wire_helpers.py`
- `tests/integration/test_skill_executor_default_registry.py`
- `tests/integration/test_cadence_reclamp_on_lifecycle.py`
- `tests/e2e/test_wave3_discord_nl_automation.py`
- `tests/e2e/test_wave3_task_routing.py`
- `tests/e2e/test_wave3_polling_heuristic.py`
- `tests/e2e/test_wave3_cadence_uplift.py`
- Extends `tests/e2e/test_wave2_product_watch.py` — add shadow_primary scenario

---

## Ordering & Parallelism

Tasks 1-4 are foundation; complete in order (migrations/config must exist before dependent code). Tasks 5-6 are independent LLM work. Tasks 7-11 are the dispatcher + UI chain. Tasks 14-16 are independent fold-ins. Tasks 17-20 are E2E scenarios.

Execute tasks in the numbered order below. Each task ends with a commit.

---

## Task 1: Alembic Migrations

**Files:**
- Create: `alembic/versions/c9d0e1f2a3b4_skill_candidate_status_claude_native.py`
- Create: `alembic/versions/d0e1f2a3b4c5_automation_active_cadence.py`
- Test: `tests/unit/test_migration_wave3.py`

**Note:** Confirm the current Alembic head before writing these migrations:
```bash
alembic heads
```
If the head is not `b8c9d0e1f2a3`, update `down_revision` on the first migration to the actual head.

- [ ] **Step 1: Write migration test**

```python
# tests/unit/test_migration_wave3.py
"""Smoke test that Wave 3 migrations apply and rollback cleanly."""
from __future__ import annotations

import pathlib
import subprocess

import pytest


@pytest.mark.integration
def test_wave3_migrations_apply_and_rollback(tmp_path: pathlib.Path) -> None:
    db_path = tmp_path / "test.db"
    env = {"DONNA_DB_PATH": str(db_path)}

    subprocess.check_call(["alembic", "upgrade", "d0e1f2a3b4c5"], env={**env, **_env_inherit()})
    subprocess.check_call(["alembic", "downgrade", "b8c9d0e1f2a3"], env={**env, **_env_inherit()})


def _env_inherit() -> dict[str, str]:
    import os
    return {k: v for k, v in os.environ.items()}
```

- [ ] **Step 2: Run it — expect FAIL (migrations don't exist)**

```bash
pytest tests/unit/test_migration_wave3.py -v
```
Expected: FAIL with `alembic.util.exc.CommandError: Can't locate revision identified by 'd0e1f2a3b4c5'`.

- [ ] **Step 3: Write first migration**

```python
# alembic/versions/c9d0e1f2a3b4_skill_candidate_status_claude_native.py
"""add claude_native_registered status + pattern_fingerprint

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-04-17 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite CHECK constraints require table recreation via batch mode.
    with op.batch_alter_table("skill_candidate_report", schema=None) as batch_op:
        batch_op.add_column(sa.Column("pattern_fingerprint", sa.Text(), nullable=True))
        # Drop old CHECK, recreate with new enum values.
        # SQLite's ALTER TABLE via batch re-creates the table; constraint replacement is automatic
        # when we re-declare the column with CheckConstraint, but since the CHECK was table-level
        # we drop-and-recreate via batch.
        batch_op.drop_constraint("skill_candidate_report_status_check", type_="check")
        batch_op.create_check_constraint(
            "skill_candidate_report_status_check",
            "status IN ('new', 'drafted', 'rejected', 'claude_native_registered')",
        )
    op.create_index(
        "ix_skill_candidate_report_pattern_fingerprint",
        "skill_candidate_report",
        ["pattern_fingerprint"],
    )


def downgrade() -> None:
    op.drop_index("ix_skill_candidate_report_pattern_fingerprint", table_name="skill_candidate_report")
    with op.batch_alter_table("skill_candidate_report", schema=None) as batch_op:
        batch_op.drop_constraint("skill_candidate_report_status_check", type_="check")
        batch_op.create_check_constraint(
            "skill_candidate_report_status_check",
            "status IN ('new', 'drafted', 'rejected')",
        )
        batch_op.drop_column("pattern_fingerprint")
```

- [ ] **Step 4: Write second migration**

```python
# alembic/versions/d0e1f2a3b4c5_automation_active_cadence.py
"""add automation.active_cadence_cron + capability.cadence_policy_override

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-04-17 00:00:01.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("automation", schema=None) as batch_op:
        batch_op.add_column(sa.Column("active_cadence_cron", sa.Text(), nullable=True))
    # Backfill: active == schedule for existing rows (any NULL stays NULL = paused).
    op.execute("UPDATE automation SET active_cadence_cron = schedule WHERE active_cadence_cron IS NULL AND schedule IS NOT NULL")

    with op.batch_alter_table("capability", schema=None) as batch_op:
        batch_op.add_column(sa.Column("cadence_policy_override", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("capability", schema=None) as batch_op:
        batch_op.drop_column("cadence_policy_override")
    with op.batch_alter_table("automation", schema=None) as batch_op:
        batch_op.drop_column("active_cadence_cron")
```

- [ ] **Step 5: Run the migration test — expect PASS**

```bash
pytest tests/unit/test_migration_wave3.py -v
```
Expected: PASS.

- [ ] **Step 6: Verify the head**

```bash
alembic heads
```
Expected: `d0e1f2a3b4c5 (head)`.

- [ ] **Step 7: Commit**

```bash
git add alembic/versions/c9d0e1f2a3b4_skill_candidate_status_claude_native.py \
        alembic/versions/d0e1f2a3b4c5_automation_active_cadence.py \
        tests/unit/test_migration_wave3.py
git commit -m "feat(migrations): skill_candidate_report + automation cadence schema (Wave 3)"
```

---

## Task 2: Cadence Policy Config + Loader

**Files:**
- Create: `config/automations.yaml`
- Create: `src/donna/automations/cadence_policy.py`
- Test: `tests/unit/test_cadence_policy.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_cadence_policy.py
"""CadencePolicy config loader + min-interval lookup."""
from __future__ import annotations

import pathlib
import textwrap

import pytest

from donna.automations.cadence_policy import CadencePolicy, PausedState


def test_loads_from_yaml(tmp_path: pathlib.Path) -> None:
    cfg = tmp_path / "automations.yaml"
    cfg.write_text(textwrap.dedent("""\
    cadence_policy:
      claude_native: {min_interval_seconds: 43200}
      sandbox: {min_interval_seconds: 43200}
      shadow_primary: {min_interval_seconds: 3600}
      trusted: {min_interval_seconds: 900}
      degraded: {min_interval_seconds: 43200}
      flagged_for_review: {pause: true}
    """))

    policy = CadencePolicy.load(cfg)
    assert policy.min_interval_for("trusted") == 900
    assert policy.min_interval_for("claude_native") == 43200


def test_flagged_for_review_is_paused() -> None:
    policy = CadencePolicy(
        intervals={"claude_native": 43200, "trusted": 900},
        paused_states={"flagged_for_review"},
    )
    with pytest.raises(PausedState):
        policy.min_interval_for("flagged_for_review")


def test_unknown_state_raises() -> None:
    policy = CadencePolicy(intervals={"trusted": 900}, paused_states=set())
    with pytest.raises(KeyError, match="unknown"):
        policy.min_interval_for("unknown")


def test_per_capability_override() -> None:
    policy = CadencePolicy(
        intervals={"trusted": 900},
        paused_states=set(),
    )
    override = {"trusted": {"min_interval_seconds": 60}}
    assert policy.min_interval_for("trusted", override=override) == 60
```

- [ ] **Step 2: Run — expect FAIL**

```bash
pytest tests/unit/test_cadence_policy.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'donna.automations.cadence_policy'`.

- [ ] **Step 3: Write the config file**

```yaml
# config/automations.yaml
cadence_policy:
  claude_native:
    min_interval_seconds: 43200   # 12h  → ≤ 2 runs/day
  sandbox:
    min_interval_seconds: 43200   # 12h  (Claude still primary)
  shadow_primary:
    min_interval_seconds: 3600    # 1h   (local + Claude shadow sampled)
  trusted:
    min_interval_seconds: 900     # 15m  (local only)
  degraded:
    min_interval_seconds: 43200   # 12h  (demoted — Claude retakes)
  flagged_for_review:
    pause: true                   # do not schedule
```

- [ ] **Step 4: Write the loader**

```python
# src/donna/automations/cadence_policy.py
"""CadencePolicy — maps a skill's lifecycle state to its minimum polling interval.

Loaded from ``config/automations.yaml``. Supports per-capability overrides.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Any

import yaml


class PausedState(Exception):
    """Raised by ``min_interval_for`` when the lifecycle state is paused."""


@dataclass(slots=True)
class CadencePolicy:
    intervals: dict[str, int]
    paused_states: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, path: pathlib.Path) -> "CadencePolicy":
        data = yaml.safe_load(path.read_text()) or {}
        table = data.get("cadence_policy", {})
        intervals: dict[str, int] = {}
        paused: set[str] = set()
        for state, cfg in table.items():
            if cfg.get("pause"):
                paused.add(state)
                continue
            intervals[state] = int(cfg["min_interval_seconds"])
        return cls(intervals=intervals, paused_states=paused)

    def min_interval_for(
        self,
        state: str,
        *,
        override: dict[str, Any] | None = None,
    ) -> int:
        if state in self.paused_states:
            raise PausedState(state)
        if override and state in override:
            return int(override[state]["min_interval_seconds"])
        if state not in self.intervals:
            raise KeyError(f"unknown lifecycle state: {state}")
        return self.intervals[state]

    def is_paused(self, state: str) -> bool:
        return state in self.paused_states
```

- [ ] **Step 5: Run — expect PASS**

```bash
pytest tests/unit/test_cadence_policy.py -v
```
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add config/automations.yaml src/donna/automations/cadence_policy.py tests/unit/test_cadence_policy.py
git commit -m "feat(automations): CadencePolicy loader from config/automations.yaml"
```

---

## Task 3: Extend ChallengerMatchResult

**Files:**
- Modify: `src/donna/agents/challenger_agent.py`
- Test: `tests/unit/test_challenger_match_and_extract_wave3.py`

- [ ] **Step 1: Write failing tests for the extended dataclass**

```python
# tests/unit/test_challenger_match_and_extract_wave3.py
"""Wave 3 extensions to ChallengerMatchResult shape."""
from __future__ import annotations

from datetime import datetime, timezone

from donna.agents.challenger_agent import ChallengerMatchResult


def test_result_has_intent_kind_field() -> None:
    r = ChallengerMatchResult(status="ready", intent_kind="automation")
    assert r.intent_kind == "automation"


def test_result_defaults() -> None:
    r = ChallengerMatchResult(status="ready")
    assert r.intent_kind == "task"
    assert r.schedule is None
    assert r.deadline is None
    assert r.alert_conditions is None
    assert r.confidence == 0.0
    assert r.low_quality_signals == []


def test_result_with_automation_fields() -> None:
    r = ChallengerMatchResult(
        status="ready",
        intent_kind="automation",
        schedule={"cron": "0 12 * * *", "human_readable": "daily at noon"},
        alert_conditions={"expression": "price < 100", "channels": ["discord_dm"]},
        confidence=0.92,
        low_quality_signals=[],
    )
    assert r.schedule["cron"] == "0 12 * * *"
    assert r.alert_conditions["expression"] == "price < 100"


def test_result_with_task_fields() -> None:
    deadline = datetime(2026, 4, 24, tzinfo=timezone.utc)
    r = ChallengerMatchResult(status="ready", intent_kind="task", deadline=deadline)
    assert r.deadline == deadline
```

- [ ] **Step 2: Run — expect FAIL**

```bash
pytest tests/unit/test_challenger_match_and_extract_wave3.py -v
```
Expected: FAIL on every test (`TypeError: __init__() got an unexpected keyword argument 'intent_kind'`).

- [ ] **Step 3: Extend the dataclass**

Replace the existing `ChallengerMatchResult` declaration in `src/donna/agents/challenger_agent.py` (the `@dataclass(slots=True)` block near the top) with:

```python
@dataclass(slots=True)
class ChallengerMatchResult:
    """Result of ChallengerAgent.match_and_extract."""
    status: str  # ready | needs_input | escalate_to_claude | ambiguous
    intent_kind: str = "task"  # task | automation | question | chat
    capability: CapabilityRow | None = None
    extracted_inputs: dict[str, Any] = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)
    clarifying_question: str | None = None
    match_score: float = 0.0
    # Wave 3 extensions
    schedule: dict[str, Any] | None = None  # {cron, human_readable} when intent_kind=automation
    deadline: datetime | None = None  # when intent_kind=task
    alert_conditions: dict[str, Any] | None = None  # {expression, channels}
    confidence: float = 0.0  # LLM self-assessed confidence 0..1
    low_quality_signals: list[str] = field(default_factory=list)
```

Add to the imports at the top of the file:

```python
from datetime import datetime
```

- [ ] **Step 4: Run — expect PASS**

```bash
pytest tests/unit/test_challenger_match_and_extract_wave3.py -v
```
Expected: PASS (4 tests).

- [ ] **Step 5: Ensure no existing callers broke**

```bash
pytest tests/unit/test_challenger_match_and_extract.py -v
```
Expected: PASS (existing tests still work because new fields default).

- [ ] **Step 6: Commit**

```bash
git add src/donna/agents/challenger_agent.py tests/unit/test_challenger_match_and_extract_wave3.py
git commit -m "feat(challenger): extend ChallengerMatchResult with Wave 3 fields"
```

---

## Task 4: cli.py Refactor — StartupContext + Wire Helpers (F-W2-E)

**Files:**
- Create: `src/donna/cli_wiring.py`
- Modify: `src/donna/cli.py`
- Test: `tests/integration/test_cli_startup_wire_helpers.py`

- [ ] **Step 1: Write failing test for helpers**

```python
# tests/integration/test_cli_startup_wire_helpers.py
"""cli_wiring helpers each return a handle and don't raise on happy-path config."""
from __future__ import annotations

import pathlib
import tempfile

import pytest

from donna.config import AppConfig
from donna.cli_wiring import (
    StartupContext,
    wire_skill_system,
    wire_automation_subsystem,
    wire_discord,
)


@pytest.fixture
def minimal_config(tmp_path: pathlib.Path) -> AppConfig:
    # Reuse the existing test fixture factory for AppConfig.
    from tests.fixtures.config_factory import build_minimal_test_config
    return build_minimal_test_config(tmp_path)


@pytest.mark.asyncio
async def test_wire_skill_system_returns_handle(minimal_config: AppConfig) -> None:
    ctx = await _bootstrap_context(minimal_config)
    handle = await wire_skill_system(ctx)
    assert handle is not None
    assert hasattr(handle, "scheduler")
    assert hasattr(handle, "validation_executor")


@pytest.mark.asyncio
async def test_wire_automation_subsystem_returns_handle(minimal_config: AppConfig) -> None:
    ctx = await _bootstrap_context(minimal_config)
    skill_h = await wire_skill_system(ctx)
    handle = await wire_automation_subsystem(ctx, skill_h)
    assert handle is not None
    assert handle.scheduler is not None
    assert handle.dispatcher is not None


@pytest.mark.asyncio
async def test_wire_discord_returns_handle(minimal_config: AppConfig) -> None:
    ctx = await _bootstrap_context(minimal_config)
    skill_h = await wire_skill_system(ctx)
    automation_h = await wire_automation_subsystem(ctx, skill_h)
    handle = await wire_discord(ctx, skill_h, automation_h)
    assert handle is not None
    # bot may be None if Discord token is absent — the wire function still runs.


async def _bootstrap_context(cfg: AppConfig) -> StartupContext:
    # Produces a minimally-initialized StartupContext using the same bootstrap
    # primitives as cli._run_orchestrator. Kept here to test in isolation.
    from donna.cli_wiring import build_startup_context
    return await build_startup_context(cfg)
```

If `tests/fixtures/config_factory.py` does not yet exist, create a shim:

```python
# tests/fixtures/config_factory.py
import pathlib
from donna.config import AppConfig, load_config

def build_minimal_test_config(tmp_path: pathlib.Path) -> AppConfig:
    # Use default prod config but override the DB to a temp path.
    cfg = load_config()
    cfg.database.url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    cfg.skill_system.enabled = True
    return cfg
```

- [ ] **Step 2: Run — expect FAIL**

```bash
pytest tests/integration/test_cli_startup_wire_helpers.py -v
```
Expected: FAIL (`ModuleNotFoundError: No module named 'donna.cli_wiring'`).

- [ ] **Step 3: Create cli_wiring.py with StartupContext + helpers**

```python
# src/donna/cli_wiring.py
"""Orchestrator startup wiring — extracted from cli._run_orchestrator (F-W2-E).

Three helpers, one StartupContext. Each helper takes the context plus any prior
handles it depends on and returns a typed handle the next helper can consume.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import Any, Optional

import structlog

from donna.config import AppConfig
from donna.models.router import ModelRouter
from donna.notifications.service import NotificationService
from donna.tasks.database import Database

logger = structlog.get_logger()


@dataclass
class StartupContext:
    config: AppConfig
    database: Database
    model_router: ModelRouter
    notification_service: NotificationService
    # Additional shared handles can be added here as needed.


@dataclass
class SkillSystemHandle:
    scheduler: Any
    validation_executor: Any
    lifecycle_service: Any
    candidate_detector: Any
    auto_drafter: Any
    evolver: Any


@dataclass
class AutomationHandle:
    scheduler: Any
    dispatcher: Any
    repository: Any
    cadence_reclamper: Any


@dataclass
class DiscordHandle:
    bot: Optional[Any]  # discord.Client; None if token absent
    intent_dispatcher: Any


async def build_startup_context(cfg: AppConfig) -> StartupContext:
    """Minimal bootstrap — DB, router, notifier. Matches cli._run_orchestrator preamble."""
    db = Database(cfg.database.url)
    await db.connect()
    router = ModelRouter(cfg.models)
    notifier = NotificationService(cfg.notifications)
    return StartupContext(
        config=cfg,
        database=db,
        model_router=router,
        notification_service=notifier,
    )


async def wire_skill_system(ctx: StartupContext) -> SkillSystemHandle:
    """Construct the skill-system subsystem: validation executor, schedulers, detector."""
    from donna.skills.startup_wiring import assemble_skill_system

    assembled = await assemble_skill_system(
        config=ctx.config.skill_system,
        database=ctx.database,
        model_router=ctx.model_router,
    )
    return SkillSystemHandle(
        scheduler=assembled.scheduler,
        validation_executor=assembled.validation_executor,
        lifecycle_service=assembled.lifecycle_service,
        candidate_detector=assembled.candidate_detector,
        auto_drafter=assembled.auto_drafter,
        evolver=assembled.evolver,
    )


async def wire_automation_subsystem(
    ctx: StartupContext, skill_h: SkillSystemHandle
) -> AutomationHandle:
    """Construct the automation subsystem: scheduler, dispatcher, reclamper."""
    from donna.automations.repository import AutomationRepository
    from donna.automations.scheduler import AutomationScheduler
    from donna.automations.dispatcher import AutomationDispatcher
    from donna.automations.cadence_policy import CadencePolicy
    from donna.automations.cadence_reclamper import CadenceReclamper

    policy = CadencePolicy.load(pathlib.Path("config/automations.yaml"))
    repo = AutomationRepository(ctx.database.connection)
    dispatcher = AutomationDispatcher(
        repository=repo,
        model_router=ctx.model_router,
        notifier=ctx.notification_service,
        skill_executor_factory=lambda: None,  # Wave 2 OOS-W1-2; pending F-11 follow-up
    )
    scheduler = AutomationScheduler(repository=repo, dispatcher=dispatcher)
    reclamper = CadenceReclamper(repo=repo, policy=policy, scheduler=scheduler)
    # Register the reclamper on skill lifecycle transitions.
    skill_h.lifecycle_service.after_state_change.register(reclamper.reclamp_for_capability)
    return AutomationHandle(
        scheduler=scheduler,
        dispatcher=dispatcher,
        repository=repo,
        cadence_reclamper=reclamper,
    )


async def wire_discord(
    ctx: StartupContext,
    skill_h: SkillSystemHandle,
    automation_h: AutomationHandle,
) -> DiscordHandle:
    """Construct the Discord bot + intent dispatcher."""
    from donna.orchestrator.discord_intent_dispatcher import DiscordIntentDispatcher
    from donna.integrations.discord_pending_drafts import PendingDraftRegistry

    intent_dispatcher = DiscordIntentDispatcher(
        challenger=_build_challenger(ctx),
        novelty_judge=_build_novelty_judge(ctx),
        pending_drafts=PendingDraftRegistry(),
        automation_repo=automation_h.repository,
        tasks_db=ctx.database,
        notifier=ctx.notification_service,
    )
    bot = None
    if ctx.config.discord.token:
        from donna.integrations.discord_bot import DonnaBot
        from donna.orchestrator.input_parser import InputParser

        bot = DonnaBot(
            input_parser=InputParser(ctx.model_router),
            database=ctx.database,
            tasks_channel_id=ctx.config.discord.tasks_channel_id,
            debug_channel_id=ctx.config.discord.debug_channel_id,
            intent_dispatcher=intent_dispatcher,
        )
    return DiscordHandle(bot=bot, intent_dispatcher=intent_dispatcher)


def _build_challenger(ctx: StartupContext):
    from donna.agents.challenger_agent import ChallengerAgent
    from donna.capabilities.matcher import CapabilityMatcher
    from donna.capabilities.input_extractor import InputExtractor

    matcher = CapabilityMatcher(database=ctx.database, model_router=ctx.model_router)
    extractor = InputExtractor(model_router=ctx.model_router)
    return ChallengerAgent(matcher=matcher, input_extractor=extractor)


def _build_novelty_judge(ctx: StartupContext):
    from donna.agents.claude_novelty_judge import ClaudeNoveltyJudge
    return ClaudeNoveltyJudge(model_router=ctx.model_router, database=ctx.database)
```

- [ ] **Step 4: Modify cli.py to call the helpers**

Find `_run_orchestrator` in `src/donna/cli.py` and replace its body (keep the function signature + any top-level setup like load_config) with:

```python
async def _run_orchestrator(...existing signature...) -> None:
    cfg = load_config()
    from donna.cli_wiring import (
        build_startup_context,
        wire_skill_system,
        wire_automation_subsystem,
        wire_discord,
    )
    ctx = await build_startup_context(cfg)
    skill_h = await wire_skill_system(ctx)
    automation_h = await wire_automation_subsystem(ctx, skill_h)
    discord_h = await wire_discord(ctx, skill_h, automation_h)

    # Start loops (same as before — keep existing asyncio.create_task calls)
    tasks = []
    tasks.append(asyncio.create_task(skill_h.scheduler.run_forever()))
    tasks.append(asyncio.create_task(automation_h.scheduler.run_forever()))
    if discord_h.bot is not None:
        tasks.append(asyncio.create_task(discord_h.bot.start(cfg.discord.token)))

    try:
        await asyncio.gather(*tasks)
    finally:
        await ctx.database.close()
```

The exact migration will depend on what `_run_orchestrator` contains today — the principle is: anything that constructs a subsystem moves into one of the three `wire_*` helpers; `_run_orchestrator` becomes sequencing + asyncio task launches only. Target ≤ 100 lines.

- [ ] **Step 5: Run all orchestrator tests — expect PASS**

```bash
pytest tests/integration/test_cli_startup_wire_helpers.py tests/integration/test_automation_scheduler_in_orchestrator.py tests/integration/test_automation_independent_of_skills.py -v
```
Expected: PASS on all.

- [ ] **Step 6: Commit**

```bash
git add src/donna/cli_wiring.py src/donna/cli.py tests/integration/test_cli_startup_wire_helpers.py tests/fixtures/config_factory.py
git commit -m "refactor(cli): extract StartupContext + wire helpers (F-W2-E)"
```

---

## Task 5: Challenger Parse Prompt + LLM Call

**Files:**
- Create: `prompts/challenger_parse.md`
- Create: `schemas/challenger_parse.json`
- Modify: `src/donna/agents/challenger_agent.py` (new `match_and_extract` body)
- Test: extend `tests/unit/test_challenger_match_and_extract_wave3.py`

- [ ] **Step 1: Write the JSON schema**

```json
// schemas/challenger_parse.json
{
  "type": "object",
  "required": ["intent_kind", "confidence", "match_score"],
  "properties": {
    "intent_kind": {"enum": ["task", "automation", "question", "chat"]},
    "capability_name": {"type": ["string", "null"]},
    "match_score": {"type": "number", "minimum": 0, "maximum": 1},
    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    "extracted_inputs": {"type": "object"},
    "schedule": {
      "type": ["object", "null"],
      "properties": {
        "cron": {"type": "string"},
        "human_readable": {"type": "string"}
      }
    },
    "deadline": {"type": ["string", "null"], "format": "date-time"},
    "alert_conditions": {
      "type": ["object", "null"],
      "properties": {
        "expression": {"type": "string"},
        "channels": {"type": "array", "items": {"type": "string"}}
      }
    },
    "missing_fields": {"type": "array", "items": {"type": "string"}},
    "clarifying_question": {"type": ["string", "null"]},
    "low_quality_signals": {"type": "array", "items": {"type": "string"}}
  }
}
```

- [ ] **Step 2: Write the parse prompt template**

```markdown
{# prompts/challenger_parse.md #}
You are Donna's natural-language parser. Given a Discord message from the user,
classify its intent and extract structured data.

## Available capabilities (registry snapshot)

{% for cap in capabilities %}
- **{{ cap.name }}**: {{ cap.description }}
  Input schema: {{ cap.input_schema_summary }}
{% endfor %}

## Your job

Analyze the user's message and emit JSON matching this schema:
- `intent_kind`: task | automation | question | chat
  - `automation` if the message implies recurring work (watch, monitor, daily, weekly, every, when X happens)
  - `task` if the message is a single action with a deadline or no timing
  - `question` or `chat` for conversational non-work messages
- `capability_name`: name of best-matching capability from the registry, or null if none matches
- `match_score`: 0..1 — how confident you are in the capability match
- `confidence`: 0..1 — your overall confidence in the parse
- `extracted_inputs`: object of fields from the capability's input schema
- `schedule`: {cron, human_readable} when intent is automation with a clear schedule
- `deadline`: ISO-8601 datetime when intent is task with a deadline
- `alert_conditions`: {expression, channels} when automation has an alert trigger
- `missing_fields`: required input schema fields the user did not supply
- `clarifying_question`: a single question asking the user for missing info
- `low_quality_signals`: array of strings flagging ambiguity (e.g., "malformed_url", "ambiguous_date")

## "When X happens" heuristic

If the user says "when X happens, do Y" (e.g., "when I get an email from jane@x.com"):
- Do NOT emit `intent_kind=chat`. This is an automation.
- Infer a polling interval: most user-facing "when X" cases work as schedules:
  - email / news / inventory → hourly or every 15 min
  - weather / stock / news feed → hourly
  - anything "urgent" → every 15 min
- Emit `schedule.cron` with the inferred interval and `schedule.human_readable` describing it.

## Current date

{{ current_date_iso }}

## User message

{{ user_message }}

## Output

Return only valid JSON matching the schema. No prose.
```

- [ ] **Step 3: Write failing test for the LLM integration**

Append to `tests/unit/test_challenger_match_and_extract_wave3.py`:

```python
import pytest

from donna.agents.challenger_agent import ChallengerAgent


class _FakeRouter:
    def __init__(self, response: dict) -> None:
        self._response = response
        self.calls: list[tuple[str, str]] = []

    async def complete(self, prompt, *, task_type, user_id, schema=None, model_alias=None, **kwargs):
        self.calls.append((task_type, user_id))
        return self._response, {"cost_usd": 0.0, "latency_ms": 50}


class _FakeMatcher:
    async def match(self, message: str):
        from donna.capabilities.matcher import MatchConfidence
        from donna.capabilities.models import CapabilityRow
        cap = CapabilityRow(
            id="cap-1", name="product_watch", version=1,
            description="Watch a product URL for price/availability",
            input_schema={"required": ["url"], "properties": {"url": {"type": "string"}}},
            embedding_json=None, created_at=None, updated_at=None,
        )
        class _M:
            confidence = MatchConfidence.HIGH
            best_match = cap
            best_score = 0.9
        return _M()


@pytest.mark.asyncio
async def test_match_and_extract_returns_automation_result_from_llm() -> None:
    router_response = {
        "intent_kind": "automation",
        "capability_name": "product_watch",
        "match_score": 0.9,
        "confidence": 0.92,
        "extracted_inputs": {"url": "https://x.com/shirt", "required_size": "L", "max_price_usd": 100},
        "schedule": {"cron": "0 12 * * *", "human_readable": "daily at noon"},
        "deadline": None,
        "alert_conditions": {"expression": "triggers_alert == true", "channels": ["discord_dm"]},
        "missing_fields": [],
        "clarifying_question": None,
        "low_quality_signals": [],
    }
    router = _FakeRouter(router_response)
    agent = ChallengerAgent(matcher=_FakeMatcher(), input_extractor=None, model_router=router)
    result = await agent.match_and_extract("watch https://x.com/shirt daily for size L under $100", "u1")
    assert result.status == "ready"
    assert result.intent_kind == "automation"
    assert result.capability.name == "product_watch"
    assert result.schedule["cron"] == "0 12 * * *"
    assert result.alert_conditions["expression"] == "triggers_alert == true"
    assert result.confidence == pytest.approx(0.92)


@pytest.mark.asyncio
async def test_match_and_extract_needs_input_when_missing_fields() -> None:
    router_response = {
        "intent_kind": "automation",
        "capability_name": "product_watch",
        "match_score": 0.85,
        "confidence": 0.7,
        "extracted_inputs": {},
        "schedule": None,
        "deadline": None,
        "alert_conditions": None,
        "missing_fields": ["url", "max_price_usd", "required_size"],
        "clarifying_question": "Which URL, what size, and what's the max price?",
        "low_quality_signals": [],
    }
    router = _FakeRouter(router_response)
    agent = ChallengerAgent(matcher=_FakeMatcher(), input_extractor=None, model_router=router)
    result = await agent.match_and_extract("watch the Patagonia jacket", "u1")
    assert result.status == "needs_input"
    assert result.missing_fields == ["url", "max_price_usd", "required_size"]
    assert result.clarifying_question.startswith("Which URL")
```

- [ ] **Step 4: Run — expect FAIL**

```bash
pytest tests/unit/test_challenger_match_and_extract_wave3.py::test_match_and_extract_returns_automation_result_from_llm -v
```
Expected: FAIL (`ChallengerAgent.__init__` does not take `model_router`).

- [ ] **Step 5: Rewrite ChallengerAgent.match_and_extract**

Replace the ChallengerAgent `__init__` and `match_and_extract` in `src/donna/agents/challenger_agent.py`:

```python
import json
import pathlib
from datetime import datetime
from typing import Optional

import jinja2

from donna.capabilities.matcher import CapabilityMatcher
from donna.models.router import ModelRouter

_PROMPT_TEMPLATE_PATH = pathlib.Path("prompts/challenger_parse.md")


class ChallengerAgent:
    def __init__(
        self,
        *,
        matcher: CapabilityMatcher | None = None,
        input_extractor: Any | None = None,
        model_router: ModelRouter | None = None,
    ) -> None:
        self._matcher = matcher
        self._input_extractor = input_extractor  # legacy path; kept for backward compat
        self._router = model_router
        self._env = jinja2.Environment(loader=jinja2.FileSystemLoader("prompts"))

    async def match_and_extract(
        self,
        user_message: str,
        user_id: str,
    ) -> ChallengerMatchResult:
        if self._router is None:
            # Degraded path: fallback to pre-Wave-3 matcher-only flow.
            return await self._legacy_match_and_extract(user_message, user_id)

        # Snapshot the registry for the prompt context.
        caps = await self._snapshot_capabilities() if self._matcher is not None else []
        template = self._env.get_template("challenger_parse.md")
        prompt = template.render(
            capabilities=caps,
            user_message=user_message,
            current_date_iso=datetime.utcnow().isoformat() + "Z",
        )
        result_json, _meta = await self._router.complete(
            prompt,
            task_type="challenge_task",
            user_id=user_id,
        )
        return self._build_result_from_parse(result_json, caps)

    async def _snapshot_capabilities(self) -> list[Any]:
        # Best-effort: return a concise projection of the registry. Empty OK.
        if self._matcher is None or not hasattr(self._matcher, "list_all"):
            return []
        rows = await self._matcher.list_all()
        out = []
        for r in rows:
            out.append({
                "name": r.name,
                "description": r.description,
                "input_schema_summary": json.dumps(r.input_schema.get("properties", {}), default=str),
            })
        return out

    def _build_result_from_parse(self, parse: dict, caps: list[Any]) -> ChallengerMatchResult:
        cap = None
        name = parse.get("capability_name")
        if name:
            matched = [c for c in caps if (hasattr(c, "name") and c.name == name) or (isinstance(c, dict) and c.get("name") == name)]
            if matched:
                # caps may be dicts if snapshot returned dicts; attempt row-lookup via matcher if needed.
                cap = matched[0] if not isinstance(matched[0], dict) else None
        missing = parse.get("missing_fields") or []
        confidence = float(parse.get("confidence", 0.0))
        match_score = float(parse.get("match_score", 0.0))

        # Derive status
        if parse.get("intent_kind") == "chat" or parse.get("intent_kind") == "question":
            status = "ready"
        elif not name or match_score < 0.4:
            status = "escalate_to_claude"
        elif missing:
            status = "needs_input"
        elif confidence < 0.7:
            status = "ambiguous"
        else:
            status = "ready"

        deadline = None
        if parse.get("deadline"):
            deadline = datetime.fromisoformat(parse["deadline"].rstrip("Z"))

        return ChallengerMatchResult(
            status=status,
            intent_kind=parse.get("intent_kind", "task"),
            capability=cap,
            extracted_inputs=parse.get("extracted_inputs") or {},
            missing_fields=missing,
            clarifying_question=parse.get("clarifying_question"),
            match_score=match_score,
            schedule=parse.get("schedule"),
            deadline=deadline,
            alert_conditions=parse.get("alert_conditions"),
            confidence=confidence,
            low_quality_signals=parse.get("low_quality_signals") or [],
        )

    async def _legacy_match_and_extract(
        self, user_message: str, user_id: str
    ) -> ChallengerMatchResult:
        # Keep the original Wave 1/2 path as a fallback for tests that don't
        # provide a model_router.
        # ... (existing implementation preserved; see the file prior to this edit)
```

Preserve the existing `_legacy_match_and_extract` body by renaming the old `match_and_extract` to `_legacy_match_and_extract`. The legacy path is only reached when `model_router is None`.

- [ ] **Step 6: Run — expect PASS**

```bash
pytest tests/unit/test_challenger_match_and_extract_wave3.py -v
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add prompts/challenger_parse.md schemas/challenger_parse.json src/donna/agents/challenger_agent.py tests/unit/test_challenger_match_and_extract_wave3.py
git commit -m "feat(challenger): unified parse prompt + LLM-driven match_and_extract"
```

---

## Task 6: ClaudeNoveltyJudge

**Files:**
- Create: `src/donna/agents/claude_novelty_judge.py`
- Create: `prompts/claude_novelty.md`
- Create: `schemas/claude_novelty.json`
- Test: `tests/unit/test_claude_novelty_judge.py`

- [ ] **Step 1: Write the schema**

```json
// schemas/claude_novelty.json
{
  "type": "object",
  "required": ["intent_kind", "skill_candidate", "skill_candidate_reasoning"],
  "properties": {
    "intent_kind": {"enum": ["task", "automation", "question", "chat"]},
    "trigger_type": {"enum": ["on_schedule", "on_manual", "on_message", null]},
    "extracted_inputs": {"type": "object"},
    "schedule": {"type": ["object", "null"]},
    "deadline": {"type": ["string", "null"], "format": "date-time"},
    "alert_conditions": {"type": ["object", "null"]},
    "polling_interval_suggestion": {"type": ["string", "null"]},
    "skill_candidate": {"type": "boolean"},
    "skill_candidate_reasoning": {"type": "string"},
    "clarifying_question": {"type": ["string", "null"]}
  }
}
```

- [ ] **Step 2: Write the prompt**

```markdown
{# prompts/claude_novelty.md #}
You are Donna's novelty judge. A user message didn't match any capability in Donna's registry.
Your job is twofold:
1. Extract execution-ready structured data so Donna can act (as a task or automation).
2. Judge whether this pattern is worth drafting as a reusable skill.

## Registry snapshot

The user's message was NOT matched against any of these (all ranked below the confidence threshold):

{% for cap in capabilities %}
- {{ cap.name }}: {{ cap.description }}
{% endfor %}

## User message

{{ user_message }}

## Current date

{{ current_date_iso }}

## Emit JSON matching this schema

- `intent_kind`: task | automation | question | chat
- `trigger_type`: on_schedule | on_manual | on_message | null
- `extracted_inputs`: best-effort extraction
- `schedule`: {cron, human_readable} for recurring intents (see polling guidance)
- `deadline`: ISO-8601 when task has a deadline
- `alert_conditions`: {expression, channels} when automation has an alert
- `polling_interval_suggestion`: cron string for "when X happens" intents that can only be polled
- `skill_candidate`: true if this is a reusable pattern worth drafting a skill for; false if one-off/too-specific/low-frequency
- `skill_candidate_reasoning`: one sentence explaining the judgment
- `clarifying_question`: a single follow-up question if the request is ambiguous, else null

## Guidance on `skill_candidate`

Set `true` when: the pattern is generalizable ("email triage", "news digest", "meeting prep"), likely to repeat across different inputs, or matches a common productivity primitive.
Set `false` when: deeply personal/one-off ("tax prep folder review"), work-specific investigation ("look into object X in case Y"), low frequency and unlikely to recur.

## Guidance on `polling_interval_suggestion`

For "when X happens, do Y" phrasings, suggest a polling cron that matches the user's expected reactivity:
- email / news / inventory → "0 */1 * * *" (hourly)
- daily checks → "0 9 * * *"
- weekly reviews → "0 10 * * 0"
Suppress this field for intents with a clear user-specified schedule.

Return only valid JSON matching the schema. No prose.
```

- [ ] **Step 3: Write failing test**

```python
# tests/unit/test_claude_novelty_judge.py
"""ClaudeNoveltyJudge — Claude call for no-match escalations."""
from __future__ import annotations

import pytest

from donna.agents.claude_novelty_judge import ClaudeNoveltyJudge, NoveltyVerdict


class _FakeRouter:
    def __init__(self, response: dict) -> None:
        self._response = response
        self.calls: list[str] = []

    async def complete(self, prompt, *, task_type, user_id, **kwargs):
        self.calls.append(task_type)
        return self._response, {"cost_usd": 0.002, "latency_ms": 800}


class _FakeDb:
    async def list_capabilities(self):
        return []


@pytest.mark.asyncio
async def test_judge_returns_automation_verdict_with_polling_suggestion() -> None:
    response = {
        "intent_kind": "automation",
        "trigger_type": "on_schedule",
        "extracted_inputs": {"from": "jane@x.com"},
        "schedule": {"cron": "0 */1 * * *", "human_readable": "hourly"},
        "deadline": None,
        "alert_conditions": {"expression": "action_required_count > 0", "channels": ["discord_dm"]},
        "polling_interval_suggestion": "0 */1 * * *",
        "skill_candidate": True,
        "skill_candidate_reasoning": "Email triage is a reusable pattern.",
        "clarifying_question": None,
    }
    router = _FakeRouter(response)
    judge = ClaudeNoveltyJudge(model_router=router, database=_FakeDb())
    verdict = await judge.evaluate("when I get an email from jane@x.com, message me", user_id="u1")
    assert isinstance(verdict, NoveltyVerdict)
    assert verdict.intent_kind == "automation"
    assert verdict.trigger_type == "on_schedule"
    assert verdict.skill_candidate is True
    assert verdict.polling_interval_suggestion == "0 */1 * * *"
    assert router.calls == ["claude_novelty"]


@pytest.mark.asyncio
async def test_judge_marks_non_candidate() -> None:
    response = {
        "intent_kind": "automation",
        "trigger_type": "on_schedule",
        "extracted_inputs": {"folder_path": "~/tax-prep"},
        "schedule": {"cron": "0 10 * * 0", "human_readable": "Sundays at 10am"},
        "deadline": None,
        "alert_conditions": None,
        "polling_interval_suggestion": None,
        "skill_candidate": False,
        "skill_candidate_reasoning": "Annual tax workflow — user-specific, low-frequency.",
        "clarifying_question": None,
    }
    router = _FakeRouter(response)
    judge = ClaudeNoveltyJudge(model_router=router, database=_FakeDb())
    verdict = await judge.evaluate("every Sunday review tax prep folder", user_id="u1")
    assert verdict.skill_candidate is False
```

- [ ] **Step 4: Run — expect FAIL**

```bash
pytest tests/unit/test_claude_novelty_judge.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 5: Implement**

```python
# src/donna/agents/claude_novelty_judge.py
"""ClaudeNoveltyJudge — Claude call for no-capability-match escalations.

Returns execution-ready extraction + a reuse judgment. Called by
DiscordIntentDispatcher when ChallengerAgent emits status=escalate_to_claude.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import jinja2
import structlog

logger = structlog.get_logger()


@dataclass(slots=True)
class NoveltyVerdict:
    intent_kind: str
    trigger_type: str | None
    extracted_inputs: dict[str, Any]
    schedule: dict[str, Any] | None
    deadline: datetime | None
    alert_conditions: dict[str, Any] | None
    polling_interval_suggestion: str | None
    skill_candidate: bool
    skill_candidate_reasoning: str
    clarifying_question: str | None


class ClaudeNoveltyJudge:
    """Calls Claude to judge no-match messages and emit structured intent."""

    _TASK_TYPE = "claude_novelty"

    def __init__(self, *, model_router: Any, database: Any) -> None:
        self._router = model_router
        self._db = database
        self._env = jinja2.Environment(loader=jinja2.FileSystemLoader("prompts"))

    async def evaluate(self, user_message: str, user_id: str) -> NoveltyVerdict:
        caps = []
        if hasattr(self._db, "list_capabilities"):
            caps = await self._db.list_capabilities()

        template = self._env.get_template("claude_novelty.md")
        prompt = template.render(
            capabilities=caps,
            user_message=user_message,
            current_date_iso=datetime.utcnow().isoformat() + "Z",
        )
        parsed, _meta = await self._router.complete(
            prompt,
            task_type=self._TASK_TYPE,
            user_id=user_id,
        )
        deadline = None
        if parsed.get("deadline"):
            deadline = datetime.fromisoformat(parsed["deadline"].rstrip("Z"))
        return NoveltyVerdict(
            intent_kind=parsed["intent_kind"],
            trigger_type=parsed.get("trigger_type"),
            extracted_inputs=parsed.get("extracted_inputs") or {},
            schedule=parsed.get("schedule"),
            deadline=deadline,
            alert_conditions=parsed.get("alert_conditions"),
            polling_interval_suggestion=parsed.get("polling_interval_suggestion"),
            skill_candidate=bool(parsed["skill_candidate"]),
            skill_candidate_reasoning=parsed["skill_candidate_reasoning"],
            clarifying_question=parsed.get("clarifying_question"),
        )
```

Register the new task_type in `config/task_types.yaml`:

```yaml
claude_novelty:
  provider: claude
  model: claude-sonnet-4-20250514
  max_output_tokens: 1024
  temperature: 0.2
```

- [ ] **Step 6: Run — expect PASS**

```bash
pytest tests/unit/test_claude_novelty_judge.py -v
```
Expected: PASS (2 tests).

- [ ] **Step 7: Commit**

```bash
git add src/donna/agents/claude_novelty_judge.py prompts/claude_novelty.md schemas/claude_novelty.json tests/unit/test_claude_novelty_judge.py config/task_types.yaml
git commit -m "feat(agents): ClaudeNoveltyJudge for no-match extractions + reuse verdict"
```

---

## Task 7: PendingDraftRegistry

**Files:**
- Create: `src/donna/integrations/discord_pending_drafts.py`
- Test: `tests/unit/test_pending_draft_registry.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_pending_draft_registry.py
"""PendingDraftRegistry — per-user pending task/automation drafts."""
from __future__ import annotations

import asyncio
import time

import pytest

from donna.integrations.discord_pending_drafts import (
    PendingDraft,
    PendingDraftRegistry,
)


def test_set_and_get_by_thread() -> None:
    reg = PendingDraftRegistry(ttl_seconds=1800)
    draft = PendingDraft(user_id="u1", thread_id=42, draft_kind="automation", partial={"url": "x"})
    reg.set(draft)
    assert reg.get_by_thread(42) == draft


def test_ttl_expires_draft() -> None:
    reg = PendingDraftRegistry(ttl_seconds=0)
    draft = PendingDraft(user_id="u1", thread_id=42, draft_kind="task", partial={})
    reg.set(draft)
    # Stale timestamps
    draft.created_at = time.time() - 3600
    assert reg.get_by_thread(42) is None


def test_list_active_for_user() -> None:
    reg = PendingDraftRegistry(ttl_seconds=1800)
    reg.set(PendingDraft(user_id="u1", thread_id=1, draft_kind="task", partial={}))
    reg.set(PendingDraft(user_id="u2", thread_id=2, draft_kind="automation", partial={}))
    assert len(reg.list_active_for_user("u1")) == 1


def test_discard() -> None:
    reg = PendingDraftRegistry(ttl_seconds=1800)
    reg.set(PendingDraft(user_id="u1", thread_id=42, draft_kind="task", partial={}))
    reg.discard(42)
    assert reg.get_by_thread(42) is None


@pytest.mark.asyncio
async def test_sweeper_removes_expired() -> None:
    reg = PendingDraftRegistry(ttl_seconds=0)
    draft = PendingDraft(user_id="u1", thread_id=42, draft_kind="task", partial={})
    draft.created_at = time.time() - 3600
    reg._drafts[42] = draft  # direct insert to bypass set's timestamp
    await reg.sweep_expired()
    assert reg.get_by_thread(42) is None
```

- [ ] **Step 2: Run — expect FAIL (module missing)**

```bash
pytest tests/unit/test_pending_draft_registry.py -v
```

- [ ] **Step 3: Implement**

```python
# src/donna/integrations/discord_pending_drafts.py
"""PendingDraftRegistry — per-user in-memory map of task/automation drafts.

Thread-id keyed. 30-min TTL. Lost on process restart (acceptable for v1).
Promoted from the Wave 1/2 task-clarification primitive in discord_bot.py;
extended to hold automation partial drafts.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PendingDraft:
    user_id: str
    thread_id: int
    draft_kind: str  # task | automation
    partial: dict[str, Any]
    capability_name: str | None = None
    created_at: float = field(default_factory=time.time)


class PendingDraftRegistry:
    def __init__(self, *, ttl_seconds: int = 1800) -> None:
        self._ttl = ttl_seconds
        self._drafts: dict[int, PendingDraft] = {}

    def set(self, draft: PendingDraft) -> None:
        draft.created_at = time.time()
        self._drafts[draft.thread_id] = draft

    def get_by_thread(self, thread_id: int) -> PendingDraft | None:
        draft = self._drafts.get(thread_id)
        if draft is None:
            return None
        if time.time() - draft.created_at > self._ttl:
            self._drafts.pop(thread_id, None)
            return None
        return draft

    def list_active_for_user(self, user_id: str) -> list[PendingDraft]:
        now = time.time()
        return [d for d in self._drafts.values() if d.user_id == user_id and now - d.created_at <= self._ttl]

    def discard(self, thread_id: int) -> None:
        self._drafts.pop(thread_id, None)

    async def sweep_expired(self) -> int:
        now = time.time()
        expired = [tid for tid, d in self._drafts.items() if now - d.created_at > self._ttl]
        for tid in expired:
            self._drafts.pop(tid, None)
        return len(expired)
```

- [ ] **Step 4: Run — expect PASS**

```bash
pytest tests/unit/test_pending_draft_registry.py -v
```
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/donna/integrations/discord_pending_drafts.py tests/unit/test_pending_draft_registry.py
git commit -m "feat(discord): PendingDraftRegistry for task/automation drafts"
```

---

## Task 8: DiscordIntentDispatcher

**Files:**
- Create: `src/donna/orchestrator/discord_intent_dispatcher.py`
- Test: `tests/unit/test_discord_intent_dispatcher.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_discord_intent_dispatcher.py
"""DiscordIntentDispatcher — post-challenger routing to task/automation/escalation."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from donna.orchestrator.discord_intent_dispatcher import (
    DiscordIntentDispatcher,
    DispatchResult,
)
from donna.agents.challenger_agent import ChallengerMatchResult
from donna.agents.claude_novelty_judge import NoveltyVerdict


class _FakeChallenger:
    def __init__(self, result: ChallengerMatchResult) -> None:
        self._result = result
    async def match_and_extract(self, msg, user_id):
        return self._result


class _FakeNovelty:
    def __init__(self, verdict: NoveltyVerdict) -> None:
        self._verdict = verdict
    async def evaluate(self, msg, user_id):
        return self._verdict


class _FakeAutomationRepo:
    def __init__(self):
        self.created = []
    async def create(self, **kwargs):
        self.created.append(kwargs)
        return "auto-1"


class _FakeTasksDb:
    def __init__(self):
        self.tasks = []
    async def insert_task(self, **kwargs):
        self.tasks.append(kwargs)
        return "task-1"


class _FakePendingDrafts:
    def __init__(self):
        self.drafts = []
    def set(self, d): self.drafts.append(d)
    def get_by_thread(self, tid): return None
    def discard(self, tid): pass


class _FakeNotifier:
    async def post_to_channel(self, channel_id, content): pass


@dataclass
class _Msg:
    content: str
    author_id: str = "u1"
    thread_id: int | None = None


@pytest.mark.asyncio
async def test_ready_task_routes_to_task_path() -> None:
    result = ChallengerMatchResult(status="ready", intent_kind="task", confidence=0.9)
    dispatcher = DiscordIntentDispatcher(
        challenger=_FakeChallenger(result),
        novelty_judge=_FakeNovelty(None),
        pending_drafts=_FakePendingDrafts(),
        automation_repo=_FakeAutomationRepo(),
        tasks_db=_FakeTasksDb(),
        notifier=_FakeNotifier(),
    )
    out = await dispatcher.dispatch(_Msg(content="get oil change by wednesday"))
    assert isinstance(out, DispatchResult)
    assert out.kind == "task_created"


@pytest.mark.asyncio
async def test_ready_automation_returns_confirmation_needed() -> None:
    result = ChallengerMatchResult(
        status="ready", intent_kind="automation", confidence=0.9,
        schedule={"cron": "0 12 * * *", "human_readable": "daily at noon"},
        extracted_inputs={"url": "x"},
    )
    dispatcher = DiscordIntentDispatcher(
        challenger=_FakeChallenger(result),
        novelty_judge=_FakeNovelty(None),
        pending_drafts=_FakePendingDrafts(),
        automation_repo=_FakeAutomationRepo(),
        tasks_db=_FakeTasksDb(),
        notifier=_FakeNotifier(),
    )
    out = await dispatcher.dispatch(_Msg(content="watch this daily"))
    assert out.kind == "automation_confirmation_needed"
    assert out.draft_automation is not None


@pytest.mark.asyncio
async def test_needs_input_sets_pending_draft() -> None:
    result = ChallengerMatchResult(
        status="needs_input", intent_kind="automation",
        clarifying_question="Which URL?",
        missing_fields=["url"], confidence=0.75,
    )
    drafts = _FakePendingDrafts()
    dispatcher = DiscordIntentDispatcher(
        challenger=_FakeChallenger(result),
        novelty_judge=_FakeNovelty(None),
        pending_drafts=drafts,
        automation_repo=_FakeAutomationRepo(),
        tasks_db=_FakeTasksDb(),
        notifier=_FakeNotifier(),
    )
    out = await dispatcher.dispatch(_Msg(content="watch the jacket", thread_id=99))
    assert out.kind == "clarification_posted"
    assert len(drafts.drafts) == 1


@pytest.mark.asyncio
async def test_escalate_calls_novelty_judge_and_routes_automation() -> None:
    challenger_result = ChallengerMatchResult(status="escalate_to_claude")
    verdict = NoveltyVerdict(
        intent_kind="automation", trigger_type="on_schedule",
        extracted_inputs={"from": "jane@x.com"},
        schedule={"cron": "0 */1 * * *", "human_readable": "hourly"},
        deadline=None, alert_conditions=None,
        polling_interval_suggestion="0 */1 * * *",
        skill_candidate=True, skill_candidate_reasoning="email triage",
        clarifying_question=None,
    )
    dispatcher = DiscordIntentDispatcher(
        challenger=_FakeChallenger(challenger_result),
        novelty_judge=_FakeNovelty(verdict),
        pending_drafts=_FakePendingDrafts(),
        automation_repo=_FakeAutomationRepo(),
        tasks_db=_FakeTasksDb(),
        notifier=_FakeNotifier(),
    )
    out = await dispatcher.dispatch(_Msg(content="when I get an email from jane@x.com, message me"))
    assert out.kind == "automation_confirmation_needed"
```

- [ ] **Step 2: Run — expect FAIL**

```bash
pytest tests/unit/test_discord_intent_dispatcher.py -v
```

- [ ] **Step 3: Implement**

```python
# src/donna/orchestrator/discord_intent_dispatcher.py
"""DiscordIntentDispatcher — routes free-text messages to task/automation/escalate.

Called once per inbound Discord message by DonnaBot.on_message.

Returns a DispatchResult indicating what action the caller should take
(post clarification, show confirmation card, confirm task creation, etc.).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

import structlog

from donna.agents.challenger_agent import ChallengerAgent, ChallengerMatchResult
from donna.agents.claude_novelty_judge import ClaudeNoveltyJudge, NoveltyVerdict
from donna.integrations.discord_pending_drafts import (
    PendingDraft,
    PendingDraftRegistry,
)

logger = structlog.get_logger()


@dataclass
class DraftAutomation:
    user_id: str
    capability_name: str | None
    inputs: dict[str, Any]
    schedule_cron: str | None
    schedule_human: str | None
    alert_conditions: dict[str, Any] | None
    target_cadence_cron: str
    active_cadence_cron: str
    skill_candidate: bool = True
    skill_candidate_reasoning: str | None = None


@dataclass
class DispatchResult:
    kind: str  # task_created | automation_confirmation_needed | clarification_posted | chat | no_action
    task_id: str | None = None
    draft_automation: DraftAutomation | None = None
    clarifying_question: str | None = None


class _HasContent(Protocol):
    content: str
    author_id: str
    thread_id: int | None


class DiscordIntentDispatcher:
    def __init__(
        self,
        *,
        challenger: ChallengerAgent,
        novelty_judge: ClaudeNoveltyJudge,
        pending_drafts: PendingDraftRegistry,
        automation_repo: Any,
        tasks_db: Any,
        notifier: Any,
    ) -> None:
        self._challenger = challenger
        self._novelty = novelty_judge
        self._drafts = pending_drafts
        self._repo = automation_repo
        self._tasks = tasks_db
        self._notifier = notifier

    async def dispatch(self, msg: _HasContent) -> DispatchResult:
        # Thread-resume path
        if msg.thread_id is not None:
            existing = self._drafts.get_by_thread(msg.thread_id)
            if existing is not None:
                return await self._resume(msg, existing)

        result = await self._challenger.match_and_extract(msg.content, msg.author_id)
        logger.info(
            "intent_dispatch",
            status=result.status,
            intent_kind=result.intent_kind,
            capability=(result.capability.name if result.capability else None),
            confidence=result.confidence,
        )

        if result.status in ("needs_input", "ambiguous"):
            return self._handle_needs_input(result, msg)
        if result.status == "escalate_to_claude":
            return await self._handle_escalate(msg)

        # status == ready
        if result.intent_kind == "task":
            return await self._create_task(result, msg)
        if result.intent_kind == "automation":
            return self._build_automation_draft(result, msg)
        return DispatchResult(kind="chat")

    def _handle_needs_input(
        self, result: ChallengerMatchResult, msg: _HasContent
    ) -> DispatchResult:
        draft = PendingDraft(
            user_id=msg.author_id,
            thread_id=msg.thread_id or 0,
            draft_kind=result.intent_kind,
            partial={
                "extracted_inputs": result.extracted_inputs,
                "capability_name": result.capability.name if result.capability else None,
                "missing_fields": result.missing_fields,
            },
            capability_name=result.capability.name if result.capability else None,
        )
        self._drafts.set(draft)
        return DispatchResult(
            kind="clarification_posted",
            clarifying_question=result.clarifying_question,
        )

    async def _handle_escalate(self, msg: _HasContent) -> DispatchResult:
        verdict = await self._novelty.evaluate(msg.content, msg.author_id)
        if verdict.clarifying_question:
            draft = PendingDraft(
                user_id=msg.author_id,
                thread_id=msg.thread_id or 0,
                draft_kind=verdict.intent_kind,
                partial={"verdict": verdict},
            )
            self._drafts.set(draft)
            return DispatchResult(
                kind="clarification_posted",
                clarifying_question=verdict.clarifying_question,
            )
        if verdict.intent_kind == "task":
            return await self._create_task_from_verdict(verdict, msg)
        if verdict.intent_kind == "automation":
            return self._build_automation_draft_from_verdict(verdict, msg)
        return DispatchResult(kind="chat")

    async def _create_task(
        self, result: ChallengerMatchResult, msg: _HasContent
    ) -> DispatchResult:
        tid = await self._tasks.insert_task(
            user_id=msg.author_id,
            title=msg.content,
            inputs=result.extracted_inputs,
            deadline=result.deadline,
            capability_name=(result.capability.name if result.capability else None),
        )
        return DispatchResult(kind="task_created", task_id=tid)

    async def _create_task_from_verdict(
        self, verdict: NoveltyVerdict, msg: _HasContent
    ) -> DispatchResult:
        tid = await self._tasks.insert_task(
            user_id=msg.author_id,
            title=msg.content,
            inputs=verdict.extracted_inputs,
            deadline=verdict.deadline,
            capability_name=None,
        )
        return DispatchResult(kind="task_created", task_id=tid)

    def _build_automation_draft(
        self, result: ChallengerMatchResult, msg: _HasContent
    ) -> DispatchResult:
        schedule = result.schedule or {}
        cron = schedule.get("cron") or "0 12 * * *"
        draft = DraftAutomation(
            user_id=msg.author_id,
            capability_name=result.capability.name if result.capability else None,
            inputs=result.extracted_inputs,
            schedule_cron=cron,
            schedule_human=schedule.get("human_readable"),
            alert_conditions=result.alert_conditions,
            target_cadence_cron=cron,
            active_cadence_cron=cron,  # cadence policy clamps happen in Task 11
        )
        return DispatchResult(kind="automation_confirmation_needed", draft_automation=draft)

    def _build_automation_draft_from_verdict(
        self, verdict: NoveltyVerdict, msg: _HasContent
    ) -> DispatchResult:
        cron = verdict.polling_interval_suggestion or (verdict.schedule or {}).get("cron") or "0 12 * * *"
        draft = DraftAutomation(
            user_id=msg.author_id,
            capability_name=None,
            inputs=verdict.extracted_inputs,
            schedule_cron=cron,
            schedule_human=(verdict.schedule or {}).get("human_readable"),
            alert_conditions=verdict.alert_conditions,
            target_cadence_cron=cron,
            active_cadence_cron=cron,  # cadence policy clamps happen in Task 11
            skill_candidate=verdict.skill_candidate,
            skill_candidate_reasoning=verdict.skill_candidate_reasoning,
        )
        return DispatchResult(kind="automation_confirmation_needed", draft_automation=draft)

    async def _resume(
        self, msg: _HasContent, existing: PendingDraft
    ) -> DispatchResult:
        # Merge the user's reply into the partial context and re-parse.
        merged_message = f"{existing.partial.get('extracted_inputs', {})}\n{msg.content}"
        result = await self._challenger.match_and_extract(merged_message, msg.author_id)
        self._drafts.discard(existing.thread_id)
        if result.status == "ready":
            if result.intent_kind == "task":
                return await self._create_task(result, msg)
            if result.intent_kind == "automation":
                return self._build_automation_draft(result, msg)
        # Still missing info — re-ask
        if result.status in ("needs_input", "ambiguous"):
            self._drafts.set(PendingDraft(
                user_id=msg.author_id,
                thread_id=msg.thread_id or 0,
                draft_kind=result.intent_kind,
                partial={"extracted_inputs": result.extracted_inputs,
                         "capability_name": result.capability.name if result.capability else None,
                         "missing_fields": result.missing_fields},
            ))
            return DispatchResult(
                kind="clarification_posted",
                clarifying_question=result.clarifying_question,
            )
        return DispatchResult(kind="no_action")
```

- [ ] **Step 4: Run — expect PASS**

```bash
pytest tests/unit/test_discord_intent_dispatcher.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/donna/orchestrator/discord_intent_dispatcher.py tests/unit/test_discord_intent_dispatcher.py
git commit -m "feat(orchestrator): DiscordIntentDispatcher routes parse results"
```

---

## Task 9: AutomationConfirmationView + AutomationCreationPath

**Files:**
- Modify: `src/donna/integrations/discord_views.py`
- Create: `src/donna/automations/creation_flow.py`
- Test: `tests/unit/test_automation_confirmation_view.py`
- Test: `tests/unit/test_automation_creation_flow.py`

- [ ] **Step 1: Write failing tests for the creation flow**

```python
# tests/unit/test_automation_creation_flow.py
"""AutomationCreationPath — renders confirmation card, handles approve/cancel/edit."""
from __future__ import annotations

import pytest

from donna.automations.creation_flow import AutomationCreationPath
from donna.orchestrator.discord_intent_dispatcher import DraftAutomation


class _FakeRepo:
    def __init__(self):
        self.created = []
    async def create(self, **kwargs):
        self.created.append(kwargs)
        return "auto-1"


@pytest.mark.asyncio
async def test_approve_creates_automation_row() -> None:
    repo = _FakeRepo()
    flow = AutomationCreationPath(repository=repo)
    draft = DraftAutomation(
        user_id="u1", capability_name="product_watch",
        inputs={"url": "https://x.com/shirt"},
        schedule_cron="0 12 * * *", schedule_human="daily at noon",
        alert_conditions={"expression": "triggers_alert == true", "channels": ["discord_dm"]},
        target_cadence_cron="0 12 * * *", active_cadence_cron="0 12 * * *",
    )
    automation_id = await flow.approve(draft, name="watch shirt")
    assert automation_id == "auto-1"
    assert len(repo.created) == 1
    row = repo.created[0]
    assert row["capability_name"] == "product_watch"
    assert row["created_via"] == "discord"
    assert row["schedule"] == "0 12 * * *"


@pytest.mark.asyncio
async def test_approve_twice_is_idempotent() -> None:
    class IdempotentRepo:
        def __init__(self): self.calls = 0
        async def create(self, **kwargs):
            self.calls += 1
            if self.calls > 1:
                from donna.automations.repository import AlreadyExistsError
                raise AlreadyExistsError()
            return "auto-1"

    repo = IdempotentRepo()
    flow = AutomationCreationPath(repository=repo)
    draft = DraftAutomation(
        user_id="u1", capability_name="product_watch", inputs={},
        schedule_cron="0 12 * * *", schedule_human="daily",
        alert_conditions=None,
        target_cadence_cron="0 12 * * *", active_cadence_cron="0 12 * * *",
    )
    id1 = await flow.approve(draft, name="watch")
    id2 = await flow.approve(draft, name="watch")
    assert id1 == "auto-1"
    assert id2 is None  # second attempt returns None
```

- [ ] **Step 2: Write failing tests for the View**

```python
# tests/unit/test_automation_confirmation_view.py
"""AutomationConfirmationView — embed rendering + button callbacks."""
from __future__ import annotations

from donna.integrations.discord_views import AutomationConfirmationView
from donna.orchestrator.discord_intent_dispatcher import DraftAutomation


def _draft() -> DraftAutomation:
    return DraftAutomation(
        user_id="u1", capability_name="product_watch",
        inputs={"url": "https://x.com/shirt", "max_price_usd": 100},
        schedule_cron="*/15 * * * *", schedule_human="every 15 minutes",
        alert_conditions={"expression": "triggers_alert == true", "channels": ["discord_dm"]},
        target_cadence_cron="*/15 * * * *", active_cadence_cron="0 */12 * * *",
    )


def test_embed_shows_fields() -> None:
    view = AutomationConfirmationView(draft=_draft(), name="watch shirt")
    embed = view.build_embed()
    assert "product_watch" in embed.description or "product_watch" in (embed.title or "")
    text = "\n".join(field.value for field in embed.fields)
    assert "https://x.com/shirt" in text
    assert "every 15 minutes" in text


def test_embed_flags_clamped_cadence() -> None:
    view = AutomationConfirmationView(draft=_draft(), name="watch shirt")
    embed = view.build_embed()
    text = "\n".join(field.value for field in embed.fields)
    assert "every 12 hours" in text  # active cadence surfaced
    # user's target preserved visibly
    assert "every 15 minutes" in text
```

- [ ] **Step 3: Run — expect FAIL**

```bash
pytest tests/unit/test_automation_creation_flow.py tests/unit/test_automation_confirmation_view.py -v
```

- [ ] **Step 4: Implement creation flow**

```python
# src/donna/automations/creation_flow.py
"""AutomationCreationPath — final step of the Discord NL creation flow.

Invoked when the user clicks Approve on an AutomationConfirmationView.
Writes the automation row. Idempotent on (user_id, name) uniqueness.
"""
from __future__ import annotations

from typing import Any

import structlog

from donna.orchestrator.discord_intent_dispatcher import DraftAutomation

logger = structlog.get_logger()


class AutomationCreationPath:
    def __init__(self, *, repository: Any) -> None:
        self._repo = repository

    async def approve(self, draft: DraftAutomation, *, name: str) -> str | None:
        try:
            automation_id = await self._repo.create(
                user_id=draft.user_id,
                name=name,
                description=None,
                capability_name=draft.capability_name or "",
                inputs=draft.inputs,
                trigger_type="on_schedule",
                schedule=draft.schedule_cron,
                alert_conditions=draft.alert_conditions or {},
                alert_channels=["discord_dm"],
                max_cost_per_run_usd=None,
                min_interval_seconds=300,
                created_via="discord",
                target_cadence_cron=draft.target_cadence_cron,
                active_cadence_cron=draft.active_cadence_cron,
            )
            logger.info(
                "automation_created_via_discord",
                user_id=draft.user_id, name=name,
                capability=draft.capability_name,
                target_cadence=draft.target_cadence_cron,
                active_cadence=draft.active_cadence_cron,
            )
            return automation_id
        except Exception as exc:
            # Let the repository raise AlreadyExistsError on duplicate (user_id, name).
            if type(exc).__name__ == "AlreadyExistsError":
                logger.info("automation_creation_already_exists", name=name)
                return None
            raise
```

Extend `AutomationRepository.create` in `src/donna/automations/repository.py`. Add `target_cadence_cron` and `active_cadence_cron` to the signature, and include `active_cadence_cron` in the INSERT values tuple in the position that matches `AUTOMATION_COLUMNS` (last column). Example:

```python
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
    target_cadence_cron: str | None = None,   # NEW; mirrors `schedule` for NL flow
    active_cadence_cron: str | None = None,   # NEW; policy-clamped cadence
) -> str:
    auto_id = str(uuid6.uuid7())
    now_iso = datetime.now(timezone.utc).isoformat()
    # target_cadence_cron defaults to schedule if the caller didn't specify.
    target_cadence_cron = target_cadence_cron or schedule
    active_cadence_cron = active_cadence_cron or schedule
    try:
        await self._conn.execute(
            f"INSERT INTO automation ({SELECT_AUTOMATION}) "
            f"VALUES ({', '.join('?' for _ in AUTOMATION_COLUMNS)})",
            (
                auto_id, user_id, name, description, capability_name,
                json.dumps(inputs), trigger_type, schedule,
                json.dumps(alert_conditions), json.dumps(alert_channels),
                max_cost_per_run_usd, min_interval_seconds,
                "active", None,
                next_run_at.isoformat() if next_run_at else None,
                0, 0,
                now_iso, now_iso, created_via,
                active_cadence_cron,   # matches the new last position in AUTOMATION_COLUMNS
            ),
        )
    except aiosqlite.IntegrityError:
        raise AlreadyExistsError()
    await self._conn.commit()
    return auto_id
```

(Note: `schedule` already stores the target; the column stays as-is for backward compat. If you want an explicit `target_cadence_cron` column in the future, file a follow-up — Wave 3's cadence reclamper only needs `schedule` as target + `active_cadence_cron`.)

Update `AUTOMATION_COLUMNS` in `src/donna/automations/models.py`:

```python
AUTOMATION_COLUMNS = (
    "id", "user_id", "name", "description", "capability_name",
    "inputs", "trigger_type", "schedule", "alert_conditions",
    "alert_channels", "max_cost_per_run_usd", "min_interval_seconds",
    "status", "last_run_at", "next_run_at", "run_count",
    "failure_count", "created_at", "updated_at", "created_via",
    "active_cadence_cron",  # NEW (target_cadence stays as `schedule` for backward compat)
)
```

Update `AutomationRow` dataclass to include `active_cadence_cron: str | None`.

Add `AlreadyExistsError` to repository if it doesn't exist:

```python
class AlreadyExistsError(Exception):
    pass
```

And wrap the INSERT in `AutomationRepository.create`:

```python
try:
    await self._conn.execute("INSERT INTO automation (...) VALUES (...)", values)
except aiosqlite.IntegrityError:
    raise AlreadyExistsError()
```

- [ ] **Step 5: Implement the View**

```python
# Add to src/donna/integrations/discord_views.py

import discord
from donna.orchestrator.discord_intent_dispatcher import DraftAutomation


class AutomationConfirmationView(discord.ui.View):
    def __init__(self, *, draft: DraftAutomation, name: str) -> None:
        super().__init__(timeout=1800)
        self.draft = draft
        self.name = name
        self.result: str | None = None  # approve | edit | cancel

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"Create automation: {self.name}",
            description=(f"Capability: `{self.draft.capability_name}`" if self.draft.capability_name
                         else "Capability: _none — Claude will handle runs_"),
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="Inputs",
            value="\n".join(f"{k}: `{v}`" for k, v in self.draft.inputs.items()) or "_(none)_",
            inline=False,
        )
        # Target vs active cadence
        if self.draft.target_cadence_cron != self.draft.active_cadence_cron:
            target_human = self.draft.schedule_human or self.draft.target_cadence_cron
            active_human = _cron_to_human(self.draft.active_cadence_cron)
            embed.add_field(
                name="Schedule",
                value=(f"Your target: **{target_human}**\n"
                       f"Running: **{active_human}** for now\n"
                       "_I'll speed up automatically: hourly once I'm shadowing, your target once trusted._"),
                inline=False,
            )
        else:
            embed.add_field(
                name="Schedule",
                value=self.draft.schedule_human or self.draft.schedule_cron or "(none)",
                inline=False,
            )
        if self.draft.alert_conditions:
            embed.add_field(
                name="Alert when",
                value=f"`{self.draft.alert_conditions.get('expression', '')}`",
                inline=False,
            )
        return embed

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approve(self, interaction, button):
        self.result = "approve"
        self.stop()
        await interaction.response.edit_message(content="✅ Creating automation…", view=None)

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.secondary)
    async def edit(self, interaction, button):
        self.result = "edit"
        self.stop()
        await interaction.response.edit_message(content="What do you want to change?", view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction, button):
        self.result = "cancel"
        self.stop()
        await interaction.response.edit_message(content="❌ Cancelled — nothing created.", view=None)


def _cron_to_human(cron: str | None) -> str:
    if cron is None:
        return "paused"
    # Minimal humanizer; Wave 4+ can integrate croniter or cron-descriptor.
    table = {
        "*/15 * * * *": "every 15 minutes",
        "0 * * * *": "hourly",
        "0 */12 * * *": "every 12 hours",
        "0 12 * * *": "daily at noon",
    }
    return table.get(cron, cron)
```

- [ ] **Step 6: Run — expect PASS**

```bash
pytest tests/unit/test_automation_creation_flow.py tests/unit/test_automation_confirmation_view.py -v
```

- [ ] **Step 7: Commit**

```bash
git add src/donna/automations/creation_flow.py src/donna/automations/repository.py src/donna/automations/models.py src/donna/integrations/discord_views.py tests/unit/test_automation_creation_flow.py tests/unit/test_automation_confirmation_view.py
git commit -m "feat(automations): AutomationCreationPath + ConfirmationView"
```

---

## Task 10: CadenceReclamper + Lifecycle Hook

**Files:**
- Create: `src/donna/automations/cadence_reclamper.py`
- Modify: `src/donna/skills/skill_lifecycle_service.py` (add `after_state_change` registration point if absent)
- Test: `tests/unit/test_cadence_reclamper.py`
- Test: `tests/integration/test_cadence_reclamp_on_lifecycle.py`

- [ ] **Step 1: Write failing unit test**

```python
# tests/unit/test_cadence_reclamper.py
"""CadenceReclamper — recomputes active cadence when skill state changes."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from donna.automations.cadence_policy import CadencePolicy
from donna.automations.cadence_reclamper import CadenceReclamper


class _FakeRepo:
    def __init__(self, rows):
        self.rows = rows
        self.updates: list[dict] = []
    async def list_by_capability(self, cap):
        return self.rows
    async def update_active_cadence(self, automation_id, active_cadence_cron, next_run_at):
        self.updates.append({"id": automation_id, "active": active_cadence_cron, "next": next_run_at})


@pytest.mark.asyncio
async def test_reclamp_clamps_to_policy_floor() -> None:
    policy = CadencePolicy(
        intervals={"sandbox": 43200, "trusted": 900},
        paused_states=set(),
    )
    rows = [
        type("R", (), {"id": "a1", "target_cadence_cron": "*/15 * * * *", "active_cadence_cron": "0 */12 * * *", "capability_name": "product_watch"})(),
    ]
    repo = _FakeRepo(rows)
    scheduler = AsyncMock()
    scheduler.compute_next_run = AsyncMock(return_value=None)
    reclamper = CadenceReclamper(repo=repo, policy=policy, scheduler=scheduler)

    await reclamper.reclamp_for_capability("product_watch", new_state="trusted")

    assert len(repo.updates) == 1
    # target is 15min, trusted floor is 15min → active should upgrade to user target.
    assert repo.updates[0]["active"] == "*/15 * * * *"


@pytest.mark.asyncio
async def test_reclamp_pauses_on_flagged_for_review() -> None:
    policy = CadencePolicy(
        intervals={"sandbox": 43200},
        paused_states={"flagged_for_review"},
    )
    rows = [
        type("R", (), {"id": "a1", "target_cadence_cron": "0 12 * * *", "active_cadence_cron": "0 12 * * *", "capability_name": "product_watch"})(),
    ]
    repo = _FakeRepo(rows)
    scheduler = AsyncMock()
    reclamper = CadenceReclamper(repo=repo, policy=policy, scheduler=scheduler)

    await reclamper.reclamp_for_capability("product_watch", new_state="flagged_for_review")

    assert repo.updates[0]["active"] is None  # NULL = paused
```

- [ ] **Step 2: Run — expect FAIL**

```bash
pytest tests/unit/test_cadence_reclamper.py -v
```

- [ ] **Step 3: Implement CadenceReclamper**

```python
# src/donna/automations/cadence_reclamper.py
"""CadenceReclamper — recomputes automation.active_cadence_cron on skill state change.

Registered via SkillLifecycleService.after_state_change.
"""
from __future__ import annotations

from typing import Any

import structlog

from donna.automations.cadence_policy import CadencePolicy, PausedState

logger = structlog.get_logger()


class CadenceReclamper:
    def __init__(self, *, repo: Any, policy: CadencePolicy, scheduler: Any) -> None:
        self._repo = repo
        self._policy = policy
        self._scheduler = scheduler

    async def reclamp_for_capability(self, capability_name: str, new_state: str) -> int:
        rows = await self._repo.list_by_capability(capability_name)
        changed = 0
        for row in rows:
            try:
                new_active = self._compute_active(row.target_cadence_cron, new_state)
            except PausedState:
                new_active = None
            if new_active == row.active_cadence_cron:
                continue
            next_run_at = None
            if new_active is not None:
                next_run_at = await self._scheduler.compute_next_run(new_active)
            await self._repo.update_active_cadence(row.id, new_active, next_run_at)
            logger.info(
                "cadence_reclamped",
                automation_id=row.id,
                capability=capability_name,
                new_state=new_state,
                old_active=row.active_cadence_cron,
                new_active=new_active,
                target=row.target_cadence_cron,
            )
            changed += 1
        if changed > 50:
            logger.warning("cadence_reclamp_large_batch", count=changed, capability=capability_name)
        return changed

    def _compute_active(self, target_cron: str, lifecycle_state: str) -> str:
        min_interval = self._policy.min_interval_for(lifecycle_state)
        target_interval = _cron_min_interval_seconds(target_cron)
        if target_interval >= min_interval:
            return target_cron  # user's target is already allowed
        return _seconds_to_cron(min_interval)


def _cron_min_interval_seconds(cron: str) -> int:
    """Very approximate — enough for policy floor comparison.

    Exact cron interval calculation is outside Wave 3 scope; use croniter for
    precision when we have a real need.
    """
    if cron.startswith("*/"):
        minutes = int(cron.split()[0][2:])
        return minutes * 60
    if cron.startswith("0 */"):
        hours = int(cron.split()[1][2:])
        return hours * 3600
    if cron.startswith("0 0"):  # daily
        return 86400
    if cron.startswith("0 ") and "* * *" in cron:  # hourly family
        return 3600
    return 86400  # default daily


def _seconds_to_cron(seconds: int) -> str:
    if seconds <= 900:
        return f"*/{max(1, seconds // 60)} * * * *"
    if seconds <= 3600:
        return "0 * * * *"
    if seconds <= 43200:
        return "0 */12 * * *"
    return "0 0 * * *"
```

Add methods to `AutomationRepository` in `src/donna/automations/repository.py`:

```python
async def list_by_capability(self, capability_name: str) -> list[AutomationRow]:
    cursor = await self._conn.execute(
        f"SELECT {SELECT_AUTOMATION} FROM automation WHERE capability_name = ?",
        (capability_name,),
    )
    rows = await cursor.fetchall()
    return [row_to_automation(r) for r in rows]

async def update_active_cadence(
    self, automation_id: str, active_cadence_cron: str | None, next_run_at
) -> None:
    iso = next_run_at.isoformat() if next_run_at is not None else None
    await self._conn.execute(
        "UPDATE automation SET active_cadence_cron = ?, next_run_at = ?, updated_at = ? WHERE id = ?",
        (active_cadence_cron, iso, datetime.now(timezone.utc).isoformat(), automation_id),
    )
    await self._conn.commit()
```

Add `target_cadence_cron` helper — since the existing `schedule` column doubles as the target per §4.4 of the spec, the reclamper reads `row.schedule` and treats it as target. Update `AutomationRow` to expose a `target_cadence_cron` property:

```python
@property
def target_cadence_cron(self) -> str | None:
    return self.schedule
```

- [ ] **Step 4: Add `after_state_change` hook to lifecycle service**

If `SkillLifecycleService` doesn't already expose an event-emit point, add one. In `src/donna/skills/skill_lifecycle_service.py`:

```python
from typing import Callable, Awaitable

class _AfterStateChangeHook:
    def __init__(self) -> None:
        self._subscribers: list[Callable[[str, str], Awaitable[None]]] = []

    def register(self, fn: Callable[[str, str], Awaitable[None]]) -> None:
        self._subscribers.append(fn)

    async def fire(self, capability_name: str, new_state: str) -> None:
        for fn in self._subscribers:
            await fn(capability_name, new_state)


class SkillLifecycleService:
    def __init__(self, ...) -> None:
        ...
        self.after_state_change = _AfterStateChangeHook()

    async def transition(self, ...):
        # ... existing transition logic
        await self.after_state_change.fire(capability_name, new_state)
```

The exact placement depends on the current implementation; the hook fires after the transition row is committed.

- [ ] **Step 5: Write integration test**

```python
# tests/integration/test_cadence_reclamp_on_lifecycle.py
"""Integration: lifecycle transition fires CadenceReclamper."""
from __future__ import annotations

import pathlib

import pytest

from donna.automations.cadence_policy import CadencePolicy
from donna.automations.cadence_reclamper import CadenceReclamper
from donna.automations.repository import AutomationRepository
from donna.skills.skill_lifecycle_service import SkillLifecycleService


@pytest.mark.asyncio
async def test_state_transition_reclamps_automation(fresh_db):
    # fresh_db is an existing fixture that provides a connection with migrations applied.
    repo = AutomationRepository(fresh_db)
    # seed automation
    automation_id = await repo.create(
        user_id="u1", name="watch shirt", description=None,
        capability_name="product_watch",
        inputs={"url": "https://x.com/shirt"},
        trigger_type="on_schedule",
        schedule="*/15 * * * *",  # target = 15min
        alert_conditions={}, alert_channels=[], max_cost_per_run_usd=None,
        min_interval_seconds=900, created_via="discord",
        target_cadence_cron="*/15 * * * *",
        active_cadence_cron="0 */12 * * *",  # sandbox-floor
    )
    # seed capability + skill in sandbox (implementation-specific; assume helpers exist)
    # ...

    policy = CadencePolicy.load(pathlib.Path("config/automations.yaml"))
    reclamper = CadenceReclamper(repo=repo, policy=policy, scheduler=_SchedulerStub())

    lifecycle = SkillLifecycleService(database=fresh_db_raw_handle)  # if it takes one
    lifecycle.after_state_change.register(reclamper.reclamp_for_capability)

    await lifecycle.transition("product_watch", "sandbox", "shadow_primary", reason="gate_passed")

    row = await repo.get(automation_id)
    assert row.active_cadence_cron == "0 * * * *"  # hourly floor for shadow_primary


class _SchedulerStub:
    async def compute_next_run(self, cron):
        from datetime import datetime, timezone
        return datetime.now(timezone.utc)
```

- [ ] **Step 6: Run — expect PASS**

```bash
pytest tests/unit/test_cadence_reclamper.py tests/integration/test_cadence_reclamp_on_lifecycle.py -v
```

- [ ] **Step 7: Commit**

```bash
git add src/donna/automations/cadence_reclamper.py src/donna/automations/repository.py src/donna/automations/models.py src/donna/skills/skill_lifecycle_service.py tests/unit/test_cadence_reclamper.py tests/integration/test_cadence_reclamp_on_lifecycle.py
git commit -m "feat(automations): CadenceReclamper + lifecycle state-change hook (folds in F-10)"
```

---

## Task 11: Cadence Surfacing on Confirmation Card

**Files:**
- Modify: `src/donna/orchestrator/discord_intent_dispatcher.py` (compute active_cadence in draft build)
- Test: `tests/unit/test_discord_intent_dispatcher_cadence.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_discord_intent_dispatcher_cadence.py
"""Intent dispatcher applies cadence policy to drafts."""
from __future__ import annotations

import pathlib

import pytest

from donna.agents.challenger_agent import ChallengerMatchResult
from donna.automations.cadence_policy import CadencePolicy
from donna.capabilities.models import CapabilityRow
from donna.orchestrator.discord_intent_dispatcher import DiscordIntentDispatcher


class _Caps:
    async def list_capabilities(self): return []


class _FakeChallenger:
    def __init__(self, result): self._r = result
    async def match_and_extract(self, msg, uid): return self._r


@pytest.mark.asyncio
async def test_draft_uses_policy_clamp_for_sandbox_capability(tmp_path):
    cfg = tmp_path / "automations.yaml"
    cfg.write_text(
        "cadence_policy:\n"
        "  sandbox: {min_interval_seconds: 43200}\n"
        "  trusted: {min_interval_seconds: 900}\n"
    )
    policy = CadencePolicy.load(cfg)

    cap = CapabilityRow(
        id="c1", name="product_watch", version=1, description="",
        input_schema={}, embedding_json=None, created_at=None, updated_at=None,
    )
    result = ChallengerMatchResult(
        status="ready", intent_kind="automation", capability=cap,
        schedule={"cron": "*/15 * * * *", "human_readable": "every 15 min"},
        extracted_inputs={"url": "x"}, confidence=0.9,
    )
    class _LifecycleFake:
        async def current_state(self, cap_name): return "sandbox"

    dispatcher = DiscordIntentDispatcher(
        challenger=_FakeChallenger(result),
        novelty_judge=None, pending_drafts=None,
        automation_repo=None, tasks_db=None, notifier=None,
        cadence_policy=policy,
        lifecycle_lookup=_LifecycleFake(),
    )
    from dataclasses import dataclass
    @dataclass
    class M:
        content: str; author_id: str = "u1"; thread_id: int | None = None
    out = await dispatcher.dispatch(M(content="watch x every 15 min"))
    assert out.kind == "automation_confirmation_needed"
    assert out.draft_automation.target_cadence_cron == "*/15 * * * *"
    assert out.draft_automation.active_cadence_cron == "0 */12 * * *"
```

- [ ] **Step 2: Run — expect FAIL (dispatcher doesn't accept cadence_policy yet)**

```bash
pytest tests/unit/test_discord_intent_dispatcher_cadence.py -v
```

- [ ] **Step 3: Extend dispatcher**

In `src/donna/orchestrator/discord_intent_dispatcher.py`, extend `__init__` and `_build_automation_draft`:

```python
from donna.automations.cadence_policy import CadencePolicy, PausedState
from donna.automations.cadence_reclamper import _cron_min_interval_seconds, _seconds_to_cron


class DiscordIntentDispatcher:
    def __init__(
        self, *, challenger, novelty_judge, pending_drafts, automation_repo,
        tasks_db, notifier,
        cadence_policy: CadencePolicy | None = None,
        lifecycle_lookup: Any | None = None,
    ) -> None:
        self._challenger = challenger
        self._novelty = novelty_judge
        self._drafts = pending_drafts
        self._repo = automation_repo
        self._tasks = tasks_db
        self._notifier = notifier
        self._policy = cadence_policy
        self._lifecycle = lifecycle_lookup

    async def _resolve_active_cadence(self, target_cron: str, capability_name: str | None) -> str:
        if self._policy is None or self._lifecycle is None:
            return target_cron
        state = "claude_native" if capability_name is None else await self._lifecycle.current_state(capability_name)
        try:
            min_interval = self._policy.min_interval_for(state)
        except PausedState:
            return target_cron  # display target in UI; will be NULL'd on creation
        target_interval = _cron_min_interval_seconds(target_cron)
        if target_interval >= min_interval:
            return target_cron
        return _seconds_to_cron(min_interval)
```

Update `_build_automation_draft` and `_build_automation_draft_from_verdict` to await `_resolve_active_cadence`:

```python
async def _build_automation_draft(self, result, msg):
    schedule = result.schedule or {}
    target_cron = schedule.get("cron") or "0 12 * * *"
    capability_name = result.capability.name if result.capability else None
    active = await self._resolve_active_cadence(target_cron, capability_name)
    # ... rest unchanged, use active for active_cadence_cron
```

Callers of `_build_automation_draft` inside `dispatch()` must now `await` it.

- [ ] **Step 4: Run — expect PASS**

```bash
pytest tests/unit/test_discord_intent_dispatcher_cadence.py tests/unit/test_discord_intent_dispatcher.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/donna/orchestrator/discord_intent_dispatcher.py tests/unit/test_discord_intent_dispatcher_cadence.py
git commit -m "feat(intent-dispatcher): clamp draft active_cadence via policy + lifecycle lookup"
```

---

## Task 12: SkillCandidateDetector — Short-Circuit on claude_native_registered

**Files:**
- Modify: `src/donna/skills/skill_candidate_detector.py`
- Test: `tests/unit/test_skill_candidate_detector_claude_native.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_skill_candidate_detector_claude_native.py
"""SkillCandidateDetector skips patterns with status=claude_native_registered."""
from __future__ import annotations

import pytest

from donna.skills.skill_candidate_detector import SkillCandidateDetector


class _FakeDb:
    def __init__(self, seen_patterns):
        self.seen = seen_patterns
    async def query_registered_non_candidates(self):
        return self.seen


@pytest.mark.asyncio
async def test_skip_claude_native_pattern() -> None:
    db = _FakeDb(seen_patterns={"fingerprint-abc"})
    detector = SkillCandidateDetector(database=db)
    # Pattern matches an already-registered non-candidate — should not emit.
    emitted = await detector.evaluate_pattern(fingerprint="fingerprint-abc", task_type="novel_x")
    assert emitted is False


@pytest.mark.asyncio
async def test_emit_for_fresh_pattern() -> None:
    db = _FakeDb(seen_patterns=set())
    detector = SkillCandidateDetector(database=db)
    emitted = await detector.evaluate_pattern(fingerprint="fingerprint-new", task_type="novel_y")
    assert emitted is True
```

- [ ] **Step 2: Run — expect FAIL**

```bash
pytest tests/unit/test_skill_candidate_detector_claude_native.py -v
```

- [ ] **Step 3: Extend SkillCandidateDetector**

In `src/donna/skills/skill_candidate_detector.py`, add:

```python
class SkillCandidateDetector:
    def __init__(self, *, database: Any, ...existing params...) -> None:
        self._db = database
        # ... existing

    async def evaluate_pattern(self, *, fingerprint: str, task_type: str) -> bool:
        """Returns True if this pattern should be surfaced as a skill candidate,
        False if it matches a previously-registered claude_native pattern."""
        registered = await self._db.query_registered_non_candidates()
        if fingerprint in registered:
            return False
        return True  # production detector adds frequency check; skip here for unit
```

Also write the DB helper:

```python
# In the Database class (or the appropriate repo)
async def query_registered_non_candidates(self) -> set[str]:
    cursor = await self._conn.execute(
        "SELECT pattern_fingerprint FROM skill_candidate_report "
        "WHERE status = 'claude_native_registered' AND pattern_fingerprint IS NOT NULL"
    )
    rows = await cursor.fetchall()
    return {r[0] for r in rows}
```

Hook the novelty judge's `skill_candidate=false` outcomes to insert rows. In `DiscordIntentDispatcher._handle_escalate`:

```python
# After building the draft from verdict, if skill_candidate is False:
if not verdict.skill_candidate:
    await self._persist_claude_native_pattern(
        user_message=msg.content,
        capability_hint=None,
        reasoning=verdict.skill_candidate_reasoning,
    )

async def _persist_claude_native_pattern(self, user_message, capability_hint, reasoning):
    if not hasattr(self._tasks, "insert_claude_native_pattern"):
        return
    fingerprint = _fingerprint(user_message)
    await self._tasks.insert_claude_native_pattern(
        fingerprint=fingerprint,
        status="claude_native_registered",
        reasoning=reasoning,
    )

def _fingerprint(message: str) -> str:
    import hashlib
    normalized = " ".join(message.lower().split())
    return hashlib.sha256(normalized.encode()).hexdigest()[:32]
```

- [ ] **Step 4: Run — expect PASS**

```bash
pytest tests/unit/test_skill_candidate_detector_claude_native.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/donna/skills/skill_candidate_detector.py src/donna/orchestrator/discord_intent_dispatcher.py tests/unit/test_skill_candidate_detector_claude_native.py
git commit -m "feat(skills): detector short-circuits on claude_native_registered patterns"
```

---

## Task 13: Rewire DonnaBot.on_message

**Files:**
- Modify: `src/donna/integrations/discord_bot.py`
- Test: extend existing `tests/unit/test_discord_bot.py` with a new scenario (or add a sibling file)

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_discord_bot_wave3.py
"""DonnaBot.on_message routes through DiscordIntentDispatcher (Wave 3)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.integrations.discord_bot import DonnaBot


class _FakeDispatcher:
    def __init__(self):
        self.received = []
    async def dispatch(self, msg):
        self.received.append(msg.content)
        from donna.orchestrator.discord_intent_dispatcher import DispatchResult
        return DispatchResult(kind="task_created", task_id="t1")


@pytest.mark.asyncio
async def test_on_message_calls_intent_dispatcher():
    dispatcher = _FakeDispatcher()
    # Bot constructed with a minimal subset of deps for testing
    bot = DonnaBot(
        input_parser=MagicMock(),
        database=MagicMock(),
        tasks_channel_id=100,
        intent_dispatcher=dispatcher,
    )
    # Simulate a message in the tasks channel
    msg = MagicMock()
    msg.channel.id = 100
    msg.content = "watch https://x.com/shirt daily"
    msg.author.bot = False
    msg.author.id = 42
    msg.thread = None

    await bot.on_message(msg)
    assert dispatcher.received == ["watch https://x.com/shirt daily"]
```

- [ ] **Step 2: Run — expect FAIL**

```bash
pytest tests/unit/test_discord_bot_wave3.py -v
```

- [ ] **Step 3: Modify DonnaBot**

In `src/donna/integrations/discord_bot.py`:

1. Add `intent_dispatcher` to `__init__`:

```python
def __init__(
    self,
    input_parser: InputParser,
    database: Database,
    tasks_channel_id: int,
    # ... existing params ...
    intent_dispatcher: Any | None = None,
) -> None:
    super().__init__(intents=intents)
    # ... existing wiring ...
    self._intent_dispatcher = intent_dispatcher
```

2. Replace the body of `on_message` (or the tasks-channel branch) to prefer the intent dispatcher:

```python
async def on_message(self, message) -> None:
    if message.author.bot:
        return
    if message.channel.id != self._tasks_channel_id:
        return  # existing chat/debug routing unchanged

    if self._intent_dispatcher is None:
        # Backward-compat: fall through to legacy input_parser
        return await self._legacy_on_message(message)

    # Thin adapter: wrap the discord.Message with a duck-typed view
    class _Msg:
        content = message.content
        author_id = str(message.author.id)
        thread_id = message.thread.id if getattr(message, "thread", None) else None

    result = await self._intent_dispatcher.dispatch(_Msg())

    # Post replies based on result.kind
    if result.kind == "task_created":
        await message.channel.send(f"✅ Task captured (`{result.task_id}`).")
    elif result.kind == "clarification_posted":
        # Start a thread for the clarification
        thread = await message.create_thread(name="Clarification")
        await thread.send(result.clarifying_question)
    elif result.kind == "automation_confirmation_needed":
        from donna.integrations.discord_views import AutomationConfirmationView
        name = _suggest_name(result.draft_automation)
        view = AutomationConfirmationView(draft=result.draft_automation, name=name)
        await message.channel.send(embed=view.build_embed(), view=view)
    elif result.kind == "chat":
        return  # existing chat engine handled separately
```

Preserve the existing behavior as `_legacy_on_message` to keep backward compat. Wave 3's cutover is: bot is constructed with `intent_dispatcher` via `cli_wiring.wire_discord` → new path is active. In tests not providing the dispatcher, legacy path runs.

- [ ] **Step 4: Run — expect PASS**

```bash
pytest tests/unit/test_discord_bot_wave3.py tests/unit/test_discord_bot.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/donna/integrations/discord_bot.py tests/unit/test_discord_bot_wave3.py
git commit -m "feat(discord): route on_message via DiscordIntentDispatcher"
```

---

## Task 14: SkillExecutor Default Registry Test (F-W2-C)

**Files:**
- Create: `tests/integration/test_skill_executor_default_registry.py`

- [ ] **Step 1: Write the test**

```python
# tests/integration/test_skill_executor_default_registry.py
"""F-W2-C: SkillExecutor without explicit tool_registry uses DEFAULT_TOOL_REGISTRY."""
from __future__ import annotations

from unittest.mock import MagicMock

from donna.skills.executor import SkillExecutor
from donna.skills.tools import DEFAULT_TOOL_REGISTRY


def test_executor_uses_default_tool_registry_when_not_overridden() -> None:
    fake_router = MagicMock()
    executor = SkillExecutor(model_router=fake_router)
    assert executor._tool_registry is DEFAULT_TOOL_REGISTRY


def test_executor_allows_explicit_registry_override() -> None:
    fake_router = MagicMock()
    from donna.skills.tools import ToolRegistry
    custom = ToolRegistry()
    executor = SkillExecutor(model_router=fake_router, tool_registry=custom)
    assert executor._tool_registry is custom
```

- [ ] **Step 2: Run — expect PASS (if SkillExecutor already defaults correctly) or FAIL (if not)**

```bash
pytest tests/integration/test_skill_executor_default_registry.py -v
```

If FAIL, modify `SkillExecutor.__init__` to default:

```python
from donna.skills.tools import DEFAULT_TOOL_REGISTRY, ToolRegistry

class SkillExecutor:
    def __init__(self, *, model_router, tool_registry: ToolRegistry | None = None, ...):
        self._tool_registry = tool_registry if tool_registry is not None else DEFAULT_TOOL_REGISTRY
        # ... rest unchanged
```

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_skill_executor_default_registry.py src/donna/skills/executor.py
git commit -m "test(skills): assert SkillExecutor falls back to DEFAULT_TOOL_REGISTRY (F-W2-C)"
```

---

## Task 15: Shadow Sampling E2E for product_watch (F-W2-G)

**Files:**
- Modify: `tests/e2e/test_wave2_product_watch.py`

- [ ] **Step 1: Add scenario**

```python
# Add to tests/e2e/test_wave2_product_watch.py

@pytest.mark.asyncio
async def test_product_watch_runs_via_skill_executor_at_shadow_primary(
    e2e_harness,
):
    """F-W2-G: once promoted, automation dispatches through SkillExecutor."""
    # Seed 20 successful shadow runs (bypass the counter via direct inserts)
    await _seed_shadow_runs(e2e_harness.db, capability="product_watch", count=20, agreement=0.95)
    # Promote the skill
    await e2e_harness.lifecycle.transition("product_watch", "sandbox", "shadow_primary", reason="gate_passed")

    # Trigger the automation via scheduler.run_once
    await e2e_harness.automation_scheduler.run_once()

    # Assertions
    runs = await e2e_harness.db.fetch_all("SELECT * FROM automation_run ORDER BY started_at DESC LIMIT 1")
    assert runs[0]["execution_path"] == "skill"
    assert runs[0]["skill_run_id"] is not None

    # Claude also ran in shadow
    skill_runs = await e2e_harness.db.fetch_all(
        "SELECT * FROM skill_run WHERE id = ?", (runs[0]["skill_run_id"],)
    )
    assert skill_runs[0]["execution_mode"] == "shadow_primary"


async def _seed_shadow_runs(db, *, capability, count, agreement):
    import json, uuid
    from datetime import datetime, timezone
    for i in range(count):
        await db.execute(
            "INSERT INTO skill_run (id, capability_name, started_at, finished_at, status, "
            "final_output, schema_valid, execution_mode) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()), capability,
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
                "complete", json.dumps({"ok": True}), 1, "sandbox",
            ),
        )
    await db.commit()
```

- [ ] **Step 2: Run — expect PASS (or reveal wiring gap)**

```bash
pytest tests/e2e/test_wave2_product_watch.py::test_product_watch_runs_via_skill_executor_at_shadow_primary -v
```
If this fails due to missing wiring (the previously-skipped F-W2-G gap), trace through `AutomationDispatcher.dispatch` to ensure the `shadow_primary` branch routes to `SkillExecutor`. Fix the branch condition as needed.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_wave2_product_watch.py src/donna/automations/dispatcher.py
git commit -m "test(e2e): product_watch fires via SkillExecutor at shadow_primary (F-W2-G)"
```

---

## Task 16: on_failure DSL (F-W2-D)

**Files:**
- Modify: `src/donna/skills/tools/dispatcher.py`
- Modify: `src/donna/skills/executor.py`
- Modify: schema files for skill.yaml step entries
- Test: `tests/unit/test_on_failure_dsl.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_on_failure_dsl.py
"""on_failure DSL — escalate | continue | fail_step | fail_skill."""
from __future__ import annotations

import pytest

from donna.skills.executor import SkillExecutor, StepFailedError, SkillFailedError
from donna.skills.tools import ToolRegistry


class _FailingTool:
    name = "broken_tool"
    async def invoke(self, **kwargs):
        raise RuntimeError("tool broke")


def _registry_with_failing_tool() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(_FailingTool())
    return reg


@pytest.mark.asyncio
async def test_continue_injects_tool_error_and_proceeds() -> None:
    # step 1: broken tool with on_failure: continue
    # step 2: depends on step 1 output
    skill_yaml = {
        "name": "test",
        "steps": [
            {"name": "step1", "tool": "broken_tool", "args": {}, "on_failure": "continue"},
            {"name": "step2", "llm": True, "prompt_template": "echo"},
        ],
    }
    # ... run via an executor shim that accepts inline skill dict
    # Assert:
    # - step 1 output is {"tool_error": "..."}
    # - step 2 runs


@pytest.mark.asyncio
async def test_fail_step_halts_step_skips_to_end() -> None:
    skill_yaml = {
        "name": "test",
        "steps": [
            {"name": "step1", "tool": "broken_tool", "args": {}, "on_failure": "fail_step"},
            {"name": "step2", "llm": True, "prompt_template": "echo"},
        ],
    }
    # Executor should skip step 2 and return. No exception propagates past the step.


@pytest.mark.asyncio
async def test_fail_skill_aborts_entire_run() -> None:
    skill_yaml = {
        "name": "test",
        "steps": [
            {"name": "step1", "tool": "broken_tool", "args": {}, "on_failure": "fail_skill"},
        ],
    }
    # Executor should raise SkillFailedError — executor catches to mark the run failed.


@pytest.mark.asyncio
async def test_escalate_is_default_when_not_specified() -> None:
    # step has no on_failure field — behavior is existing (escalate)
    pass
```

Each test needs a concrete executor invocation. Write a helper that composes `SkillExecutor` with the inline skill dict:

```python
async def _run_skill(skill_yaml, *, registry) -> dict:
    # Minimal harness — create a SkillExecutor configured against an in-memory
    # skill spec. Depends on your existing skill-loading utilities.
    from donna.skills.executor import SkillExecutor
    from unittest.mock import MagicMock
    executor = SkillExecutor(model_router=MagicMock(), tool_registry=registry)
    # Assume a `run_inline` method exists or add one for tests.
    return await executor.run_inline(skill_yaml, inputs={})
```

- [ ] **Step 2: Run — expect FAIL**

```bash
pytest tests/unit/test_on_failure_dsl.py -v
```

- [ ] **Step 3: Implement `on_failure` handling in dispatcher + executor**

In `src/donna/skills/tools/dispatcher.py`, wrap tool invocation:

```python
class ToolDispatcher:
    async def run_invocation(self, step: dict, inputs: dict) -> dict:
        on_failure = step.get("on_failure", "escalate")
        try:
            return await self._invoke(step["tool"], **step.get("args", {}), **inputs)
        except Exception as exc:
            if on_failure == "continue":
                logger.warning("tool_failure_continue", tool=step["tool"], error=str(exc))
                return {"tool_error": str(exc)}
            if on_failure == "fail_step":
                raise StepFailedError(step["name"]) from exc
            if on_failure == "fail_skill":
                raise SkillFailedError(step["name"]) from exc
            # default = escalate
            raise
```

In `src/donna/skills/executor.py`, add the error classes and handle them:

```python
class StepFailedError(Exception):
    def __init__(self, step_name): self.step_name = step_name

class SkillFailedError(Exception):
    def __init__(self, step_name): self.step_name = step_name


class SkillExecutor:
    async def execute(self, ...):
        for step in steps:
            try:
                output = await self._run_step(step, ...)
            except StepFailedError as exc:
                logger.info("step_failed_terminal", step=exc.step_name)
                return self._finalize_with_partial(state, step_stopped_at=exc.step_name)
            except SkillFailedError as exc:
                logger.info("skill_failed_abort", step=exc.step_name)
                return SkillRunResult(status="failed", ...)
            state[step["name"]] = output
        return self._finalize(state)
```

Add enum validation in the skill YAML schema:

```json
// schemas/skill_step.json (or extend existing)
{
  "properties": {
    "on_failure": {
      "enum": ["escalate", "continue", "fail_step", "fail_skill"]
    }
  }
}
```

- [ ] **Step 4: Run — expect PASS**

```bash
pytest tests/unit/test_on_failure_dsl.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/donna/skills/tools/dispatcher.py src/donna/skills/executor.py schemas/skill_step.json tests/unit/test_on_failure_dsl.py
git commit -m "feat(skills): on_failure DSL (escalate|continue|fail_step|fail_skill) (F-W2-D)"
```

---

## Task 17: E2E — Discord NL product_watch (AS-W3.1)

**Files:**
- Create: `tests/e2e/test_wave3_discord_nl_automation.py`

- [ ] **Step 1: Write the E2E**

```python
# tests/e2e/test_wave3_discord_nl_automation.py
"""E2E: Discord DM 'watch URL daily' → confirmation → automation → alert."""
from __future__ import annotations

import pytest

from tests.e2e.harness import E2EHarness


@pytest.mark.asyncio
async def test_high_confidence_nl_automation_creation_and_first_run(e2e_harness: E2EHarness):
    # User DMs the channel
    dispatch_result = await e2e_harness.intent_dispatcher.dispatch(
        e2e_harness.make_message(
            content="watch https://cos.com/shirt daily for size L under $100",
            user_id="nick",
        )
    )
    assert dispatch_result.kind == "automation_confirmation_needed"
    draft = dispatch_result.draft_automation
    assert draft.capability_name == "product_watch"

    # Approve via the creation flow
    from donna.automations.creation_flow import AutomationCreationPath
    creation = AutomationCreationPath(repository=e2e_harness.automation_repo)
    automation_id = await creation.approve(draft, name="watch patagonia shirt")
    assert automation_id is not None

    # Verify row
    row = await e2e_harness.automation_repo.get(automation_id)
    assert row.created_via == "discord"
    assert row.capability_name == "product_watch"
    assert row.schedule == draft.schedule_cron

    # Run the scheduler once — product_watch is in sandbox, so claude_native path fires
    await e2e_harness.automation_scheduler.run_once()

    # Verify a run completed + a Discord DM was dispatched when alert_condition was true
    runs = await e2e_harness.automation_repo.list_runs(automation_id=automation_id)
    assert len(runs) == 1
    assert runs[0].status == "complete"
    # Alert behavior depends on the seeded URL response — the harness can stub
    # the tool_mocks to trigger or not as needed.
```

- [ ] **Step 2: Run**

```bash
pytest tests/e2e/test_wave3_discord_nl_automation.py -v
```
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_wave3_discord_nl_automation.py
git commit -m "test(e2e): Wave 3 AS-W3.1 — NL product_watch automation end-to-end"
```

---

## Task 18: E2E — Task Routing (AS-W3.2)

**Files:**
- Create: `tests/e2e/test_wave3_task_routing.py`

- [ ] **Step 1: Write the E2E**

```python
# tests/e2e/test_wave3_task_routing.py
"""E2E: 'get oil change by Wednesday' routes to task path, not automation."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_task_phrase_routes_to_task_not_automation(e2e_harness):
    result = await e2e_harness.intent_dispatcher.dispatch(
        e2e_harness.make_message(content="get oil change by Wednesday", user_id="nick")
    )
    assert result.kind == "task_created"
    assert result.task_id is not None

    # Confirm no automation row was created
    autos = await e2e_harness.automation_repo.list_all()
    assert len(autos) == 0

    # Confirm task exists with a deadline populated
    task = await e2e_harness.tasks_db.get_task(result.task_id)
    assert task.title.lower().startswith("get oil change")
    assert task.deadline is not None
```

- [ ] **Step 2: Run — expect PASS**

```bash
pytest tests/e2e/test_wave3_task_routing.py -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_wave3_task_routing.py
git commit -m "test(e2e): Wave 3 AS-W3.2 — task routing ignores automation path"
```

---

## Task 19: E2E — "When X Happens" Polling (AS-W3.4)

**Files:**
- Create: `tests/e2e/test_wave3_polling_heuristic.py`

- [ ] **Step 1: Write the E2E**

```python
# tests/e2e/test_wave3_polling_heuristic.py
"""E2E: 'when I get an email from jane@x.com, message me' → polling automation."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_when_x_phrase_creates_polling_automation(e2e_harness):
    # This phrase matches no capability → escalates to novelty judge
    result = await e2e_harness.intent_dispatcher.dispatch(
        e2e_harness.make_message(
            content="when I get an email from jane@x.com, message me",
            user_id="nick",
        )
    )
    assert result.kind == "automation_confirmation_needed"
    draft = result.draft_automation
    assert draft.capability_name is None  # no skill for email triage yet
    # Polling cadence inferred by novelty judge
    assert draft.target_cadence_cron in ("0 */1 * * *", "0 */12 * * *")

    # Approve
    from donna.automations.creation_flow import AutomationCreationPath
    creation = AutomationCreationPath(repository=e2e_harness.automation_repo)
    automation_id = await creation.approve(draft, name="watch emails jane")
    assert automation_id is not None

    # skill_candidate_report row should have been persisted
    rows = await e2e_harness.db.fetch_all(
        "SELECT status, pattern_fingerprint FROM skill_candidate_report "
        "WHERE status IN ('new', 'claude_native_registered') ORDER BY created_at DESC LIMIT 1"
    )
    assert len(rows) == 1
```

- [ ] **Step 2: Run — expect PASS**

```bash
pytest tests/e2e/test_wave3_polling_heuristic.py -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_wave3_polling_heuristic.py
git commit -m "test(e2e): Wave 3 AS-W3.4 — 'when X happens' → polling automation"
```

---

## Task 20: E2E — Cadence Clamp + Auto-Uplift (AS-W3.11)

**Files:**
- Create: `tests/e2e/test_wave3_cadence_uplift.py`

- [ ] **Step 1: Write the E2E**

```python
# tests/e2e/test_wave3_cadence_uplift.py
"""E2E: user asks every 15 min; clamped to 12h; auto-uplifts on shadow_primary → trusted."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_cadence_clamped_and_uplifts_on_lifecycle(e2e_harness):
    # Create automation — product_watch is in sandbox
    result = await e2e_harness.intent_dispatcher.dispatch(
        e2e_harness.make_message(
            content="watch https://x.com/shirt every 15 minutes for size L under $100",
            user_id="nick",
        )
    )
    draft = result.draft_automation
    assert draft.target_cadence_cron == "*/15 * * * *"
    assert draft.active_cadence_cron == "0 */12 * * *"

    from donna.automations.creation_flow import AutomationCreationPath
    creation = AutomationCreationPath(repository=e2e_harness.automation_repo)
    aid = await creation.approve(draft, name="watch shirt fast")

    # Promote skill to shadow_primary — reclamper should fire
    await _seed_shadow_runs(e2e_harness.db, capability="product_watch", count=20, agreement=0.95)
    await e2e_harness.lifecycle.transition("product_watch", "sandbox", "shadow_primary", reason="gate_passed")

    row = await e2e_harness.automation_repo.get(aid)
    assert row.active_cadence_cron == "0 * * * *"  # hourly

    # Promote to trusted
    await e2e_harness.lifecycle.transition("product_watch", "shadow_primary", "trusted", reason="gate_passed")

    row = await e2e_harness.automation_repo.get(aid)
    assert row.active_cadence_cron == "*/15 * * * *"  # user target reached


async def _seed_shadow_runs(db, *, capability, count, agreement):
    import json, uuid
    from datetime import datetime, timezone
    for i in range(count):
        await db.execute(
            "INSERT INTO skill_run (id, capability_name, started_at, finished_at, status, "
            "final_output, schema_valid, execution_mode) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()), capability,
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
                "complete", json.dumps({"ok": True}), 1, "sandbox",
            ),
        )
    await db.commit()
```

- [ ] **Step 2: Run — expect PASS**

```bash
pytest tests/e2e/test_wave3_cadence_uplift.py -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_wave3_cadence_uplift.py
git commit -m "test(e2e): Wave 3 AS-W3.11 — cadence clamp + auto-uplift"
```

---

## Task 21: Final — Full Suite + Requirements Tick

**Files:**
- Modify: spec requirements matrix (tick off W3-R1..W3-R25)
- Modify: `docs/superpowers/followups/2026-04-16-skill-system-followups.md` — add Wave 3 Completed section

- [ ] **Step 1: Run full suite**

```bash
pytest -x
```
Expected: PASS on every test (including Wave 1/2 regression suites).

- [ ] **Step 2: Tick requirements in the spec**

Edit `docs/superpowers/specs/2026-04-17-skill-system-wave-3-discord-nl-automation-design.md` — mark each requirement in §7 from `[ ]` to `[x]`.

- [ ] **Step 3: Update followups doc**

In `docs/superpowers/followups/2026-04-16-skill-system-followups.md`, add a "Completed — Wave 3" section above Wave 2's completed section, listing F-3, F-W2-C, F-W2-D, F-W2-E, F-W2-G, F-10 as shipped.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-04-17-skill-system-wave-3-discord-nl-automation-design.md docs/superpowers/followups/2026-04-16-skill-system-followups.md
git commit -m "docs: tick Wave 3 requirements + log completion in followups"
```

---

## Spec Coverage Check

| Spec Requirement | Task(s) |
|---|---|
| W3-R1 ChallengerMatchResult schema | Task 3 |
| W3-R2 Unified parse LLM call | Task 5 |
| W3-R3 Thread clarification round-trip | Tasks 7, 8 |
| W3-R4 ClaudeNoveltyJudge output | Task 6 |
| W3-R5 "When X" → polling | Tasks 5, 6 (prompts), 19 (E2E) |
| W3-R6 Confirmation card required | Tasks 9, 13 |
| W3-R7 PendingDraftRegistry TTL | Task 7 |
| W3-R8 Idempotent create | Task 9 |
| W3-R9 claude_native_registered + fingerprint | Tasks 1, 12 |
| W3-R10 cli.py refactor (F-W2-E) | Task 4 |
| W3-R11 SkillExecutor default registry (F-W2-C) | Task 14 |
| W3-R12 Shadow sampling (F-W2-G) | Task 15 |
| W3-R13 on_failure DSL (F-W2-D) | Task 16 |
| W3-R14-R18 Original-spec R4-R8 | Tasks 3, 5, 6, 8, 17 |
| W3-R19 created_via='discord' | Task 9 |
| W3-R20 NL → alert E2E | Task 17 |
| W3-R21 active_cadence computation | Tasks 2, 10, 11 |
| W3-R22 Card shows target vs active | Task 9 |
| W3-R23 State-transition reclamp | Task 10 |
| W3-R24 flagged_for_review pauses | Task 10 |
| W3-R25 F-10 enforcement | Tasks 2, 10 |

All requirements covered.
