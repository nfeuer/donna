# Skill System Phase 3 — Lifecycle, Shadow Sampling, Auto-Drafting

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the skill system from "runs but nobody watches it" into an autonomously-improving pipeline. Add shadow Claude sampling, statistical lifecycle gates (sandbox → shadow_primary → trusted → flagged_for_review), a skill candidate detector, nightly auto-drafting with budget enforcement, and dashboard surfaces for the whole loop.

**Architecture:**
- A new `SkillLifecycleManager` becomes the sole mutator of `skill.state`, enforcing every transition from §6.2 of the spec with explicit gate checks.
- `ShadowSampler` runs Claude in parallel with skill execution at a state-dependent rate (`shadow_primary` = 100%, `trusted` = configurable 5% default), writes `skill_divergence` rows, and asks Claude to grade semantic equivalence.
- `SkillCandidateDetector` runs as part of the nightly cron, analyzes `invocation_log` for `claude_native` task types with positive expected savings, and writes `skill_candidate_report` rows.
- `AutoDrafter` picks top candidates within the remaining daily budget, asks Claude to generate skill YAML + fixtures, runs fixture validation in a sandbox, and creates new `skill` + `skill_version` rows in `draft` state.
- `DegradationDetector` runs on trusted skills, applies Wilson score confidence intervals to the rolling shadow agreement window, and moves degraded skills to `flagged_for_review`.
- EOD digest grows a "Skill system changes" section; dashboard gains Candidates, Drafts, and Divergences views.

**Tech Stack:** Python 3.12 async, SQLAlchemy 2.x + Alembic, aiosqlite, existing Claude client via ModelRouter, scipy for Wilson score CI (or a small hand-rolled implementation), FastAPI, structlog.

**Spec alignment:** This phase implements §6.2 lifecycle gates in full, §6.5 auto-drafting, §6.6 evolution-ready divergence capture (evolution itself is Phase 4), §6.10 dashboard skill review, and all Phase 3 acceptance scenarios AS-3.1 through AS-3.5.

**Dependencies from Phase 1 + Phase 2:** `CapabilityRegistry`, `SkillExecutor`, `SkillRunRepository`, `TriageAgent`, `ModelRouter`, the Phase 1 + 2 migrations, `BudgetGuard` (existing), EOD digest infrastructure (`src/donna/notifications/eod_digest.py`).

**Deferred items from Phase 2 that are now in scope:**
1. **Triage `RETRY_STEP` full retry loop** (drift log entry 2026-04-15 Phase 2 §6.4) — Task 14 implements it or formally removes the enum value.
2. **No-triage failure-shape compatibility shim** (drift log entry 2026-04-15 Phase 2 §6.4) — Task 15 rewrites the two affected Phase 1 tests and removes the `_ModelCallError` shim.
3. **`SkillSystemConfig` dead-code fix** (code review issue I-1) — Task 4 wires it through the config loader so thresholds aren't hardcoded.
4. **Duplicate Jinja render logic in `dsl.py` and `tool_dispatch.py`** (code review issue I-3) — Task 7 consolidates into a shared helper.
5. **Seed skills currently in `sandbox` must promote to `shadow_primary`** (drift log entry 2026-04-15 §7) — Task 10 migration does this automatically once shadow sampling is live.

**Phase 3 invariants (must hold after every task):**
- `skill_system.enabled = false` remains a safe default — no autonomous work runs until the flag is on.
- `skill.state` is never mutated outside `SkillLifecycleManager`.
- Auto-drafting respects `BudgetGuard` — it defers when remaining daily budget is below the per-draft threshold.
- Every state transition writes a `skill_state_transition` audit row.
- Shadow sampling never blocks skill execution; a slow or failing Claude call is logged and ignored.

**Out of scope for Phase 3 (explicitly deferred):**
- **Full evolution loop** (degraded → re-evolved skill) — Phase 4. Phase 3 captures all the divergence data evolution will need, but only the detect-and-flag side is built.
- **Capability novelty judgment via Claude** (for low-confidence challenger matches) — Phase 4 or 5. Phase 3's auto-drafter only targets existing `claude_native` task types where the capability already exists.
- **Automation subsystem** (on_schedule / on_manual triggers) — Phase 5.
- **requires_human_gate flag honoring at state transitions** — the flag exists from Phase 1 but only starts having behavioral effects in Phase 3. Manual flag-toggle UI is part of Task 12.

---

## File Structure

### New files

```
alembic/versions/
  add_lifecycle_tables_phase_3.py           -- Migration: skill_divergence, skill_candidate_report, skill_evolution_log
  promote_seed_skills_to_shadow_primary.py  -- Promotes Phase 1 seed skills from sandbox to shadow_primary

config/
  skills.yaml                               -- Tunable thresholds (promotion gates, shadow sample rates, etc.)

src/donna/skills/
  lifecycle.py                              -- SkillLifecycleManager: sole mutator of skill.state
  shadow.py                                 -- ShadowSampler: runs Claude in parallel, writes divergence rows
  equivalence.py                            -- Claude-backed semantic equivalence judge
  detector.py                               -- SkillCandidateDetector: finds worthwhile claude_native task types
  auto_drafter.py                           -- AutoDrafter: generates + validates + persists draft skills
  degradation.py                            -- DegradationDetector: Wilson CI on shadow agreement
  divergence.py                             -- SkillDivergenceRepository: reads/writes skill_divergence rows
  candidate_report.py                       -- SkillCandidateRepository: reads/writes skill_candidate_report
  _render.py                                -- Shared Jinja rendering helper (consolidates dsl.py + tool_dispatch.py)

src/donna/skills/crons/                     -- Nightly cron entry points
  __init__.py
  nightly.py                                -- Orchestrator: detector → auto_drafter → degradation → digest

tests/unit/test_skills_lifecycle.py
tests/unit/test_skills_shadow.py
tests/unit/test_skills_equivalence.py
tests/unit/test_skills_detector.py
tests/unit/test_skills_auto_drafter.py
tests/unit/test_skills_degradation.py
tests/unit/test_skills_divergence_repo.py
tests/unit/test_skills_candidate_repo.py
tests/unit/test_skills_render_helper.py
tests/unit/test_skills_nightly_cron.py
tests/unit/test_api_skill_candidates.py
tests/unit/test_api_skill_drafts.py
tests/unit/test_api_skill_divergences.py
tests/integration/test_skill_system_phase_3_e2e.py
```

### Modified files

```
src/donna/config.py                         -- Wire SkillSystemConfig through top-level DonnaConfig (closes I-1)
src/donna/skills/executor.py                -- Optional shadow sampler injection; remove _ModelCallError shim (drift cleanup)
src/donna/skills/dsl.py                     -- Call into _render.py helper (drift cleanup I-3)
src/donna/skills/tool_dispatch.py           -- Call into _render.py helper (drift cleanup I-3)
src/donna/skills/triage.py                  -- Implement RETRY_STEP as a real retry loop, OR deprecate the enum value
src/donna/api/routes/skills.py              -- Add POST /skills/{id}/state for lifecycle transitions + flag toggle
src/donna/api/routes/skill_runs.py          -- Add GET /skill-runs/{id}/divergence endpoint
src/donna/notifications/eod_digest.py       -- New "Skill system changes" section
tests/unit/test_skills_executor.py          -- Update/rewrite two Phase 1 tests (drift cleanup)
docs/phase-1-skill-system-setup.md          -- Add Phase 3 wiring (lifecycle cron, shadow, config changes)
docs/superpowers/specs/2026-04-15-skill-system-and-challenger-refactor-design.md  -- Drift log + check off Phase 3 requirements
```

---

## Task 1: Alembic migration — `skill_divergence`, `skill_candidate_report`, `skill_evolution_log`

**Files:**
- Create: `alembic/versions/add_lifecycle_tables_phase_3.py`

The three new tables from spec §5.7, §5.9, §5.10. `skill_evolution_log` is not used in Phase 3 but added now so Phase 4 doesn't need another migration.

- [ ] **Step 1: Check current Alembic head**

```bash
grep -E "^(revision|down_revision)" alembic/versions/seed_fetch_and_summarize.py
```

Expected: `revision = "d4e5f6a7b8c9"`. This becomes the `down_revision`.

- [ ] **Step 2: Create the migration**

```python
"""add lifecycle tables phase 3

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-04-16
"""
from __future__ import annotations

from typing import Union
import sqlalchemy as sa
from alembic import op

revision = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_divergence",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("skill_run_id", sa.String(length=36), nullable=False),
        sa.Column("shadow_invocation_id", sa.String(length=36), nullable=False),
        sa.Column("overall_agreement", sa.Float(), nullable=False),
        sa.Column("diff_summary", sa.JSON(), nullable=True),
        sa.Column("flagged_for_evolution", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["skill_run_id"], ["skill_run.id"],
            name="fk_divergence_skill_run_id",
        ),
    )
    with op.batch_alter_table("skill_divergence", schema=None) as batch_op:
        batch_op.create_index("ix_skill_divergence_skill_run_id", ["skill_run_id"])
        batch_op.create_index("ix_skill_divergence_created_at", ["created_at"])

    op.create_table(
        "skill_candidate_report",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("capability_name", sa.String(length=200), nullable=True),
        sa.Column("task_pattern_hash", sa.String(length=64), nullable=True),
        sa.Column("expected_savings_usd", sa.Float(), nullable=False),
        sa.Column("volume_30d", sa.Integer(), nullable=False),
        sa.Column("variance_score", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.batch_alter_table("skill_candidate_report", schema=None) as batch_op:
        batch_op.create_index("ix_skill_candidate_status", ["status"])
        batch_op.create_index("ix_skill_candidate_reported_at", ["reported_at"])

    op.create_table(
        "skill_evolution_log",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("skill_id", sa.String(length=36), nullable=False),
        sa.Column("from_version_id", sa.String(length=36), nullable=False),
        sa.Column("to_version_id", sa.String(length=36), nullable=True),
        sa.Column("triggered_by", sa.String(length=30), nullable=False),
        sa.Column("claude_invocation_id", sa.String(length=36), nullable=True),
        sa.Column("diagnosis", sa.JSON(), nullable=True),
        sa.Column("targeted_case_ids", sa.JSON(), nullable=True),
        sa.Column("validation_results", sa.JSON(), nullable=True),
        sa.Column("outcome", sa.String(length=30), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["skill_id"], ["skill.id"], name="fk_evo_log_skill_id"),
    )
    with op.batch_alter_table("skill_evolution_log", schema=None) as batch_op:
        batch_op.create_index("ix_evo_log_skill_id", ["skill_id"])


def downgrade() -> None:
    with op.batch_alter_table("skill_evolution_log", schema=None) as batch_op:
        batch_op.drop_index("ix_evo_log_skill_id")
    op.drop_table("skill_evolution_log")

    with op.batch_alter_table("skill_candidate_report", schema=None) as batch_op:
        batch_op.drop_index("ix_skill_candidate_reported_at")
        batch_op.drop_index("ix_skill_candidate_status")
    op.drop_table("skill_candidate_report")

    with op.batch_alter_table("skill_divergence", schema=None) as batch_op:
        batch_op.drop_index("ix_skill_divergence_created_at")
        batch_op.drop_index("ix_skill_divergence_skill_run_id")
    op.drop_table("skill_divergence")
```

- [ ] **Step 3: Test upgrade + downgrade against a fresh temp DB** (same pattern as Phase 2 Task 1).

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/add_lifecycle_tables_phase_3.py
git commit -m "feat(db): add skill_divergence, skill_candidate_report, skill_evolution_log"
```

---

## Task 2: ORM models + dataclasses for new tables

**Files:**
- Modify: `src/donna/tasks/db_models.py`
- Create: `src/donna/skills/divergence.py`
- Create: `src/donna/skills/candidate_report.py`
- Create: `tests/unit/test_skills_divergence_repo.py`
- Create: `tests/unit/test_skills_candidate_repo.py`

Add `SkillDivergence`, `SkillCandidateReport`, `SkillEvolutionLog` ORM classes following the existing style. Create lightweight dataclass row-mappers for `SkillDivergence` and `SkillCandidateReport` (evolution log is read by the dashboard only, can use ORM directly for now).

Follow the same pattern as `src/donna/skills/runs.py` — column tuple, SELECT constant, dataclass with slots, `row_to_*` mapper with JSON/datetime parsing.

- [ ] **Step 1: Write failing tests for both repo modules**

`tests/unit/test_skills_divergence_repo.py`:

```python
import json
from pathlib import Path

import aiosqlite
import pytest

from donna.skills.divergence import SkillDivergenceRepository


@pytest.fixture
async def db(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = await aiosqlite.connect(str(db_path))
    await conn.executescript("""
        CREATE TABLE skill_divergence (
            id TEXT PRIMARY KEY,
            skill_run_id TEXT NOT NULL,
            shadow_invocation_id TEXT NOT NULL,
            overall_agreement REAL NOT NULL,
            diff_summary TEXT,
            flagged_for_evolution INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
    """)
    await conn.commit()
    yield conn
    await conn.close()


async def test_record_divergence(db):
    repo = SkillDivergenceRepository(db)
    div_id = await repo.record(
        skill_run_id="r1",
        shadow_invocation_id="inv-shadow-1",
        overall_agreement=0.85,
        diff_summary={"diff": "minor wording"},
    )

    cursor = await db.execute(
        "SELECT overall_agreement, diff_summary FROM skill_divergence WHERE id = ?",
        (div_id,),
    )
    row = await cursor.fetchone()
    assert row[0] == 0.85
    assert json.loads(row[1]) == {"diff": "minor wording"}


async def test_recent_for_skill_returns_ordered(db):
    repo = SkillDivergenceRepository(db)
    # Insert via the repo to avoid manual schema duplication.
    for i, score in enumerate([0.9, 0.7, 0.8]):
        await repo.record(
            skill_run_id=f"r{i}",
            shadow_invocation_id=f"inv-{i}",
            overall_agreement=score,
            diff_summary=None,
        )

    # Insert corresponding skill_run rows so we can query by skill_id.
    # (Alternative: query directly by skill_run_id list.) For this test,
    # verify the repo's "recent_by_run_ids" style helper works.
    rows = await repo.recent_by_run_ids(["r0", "r1", "r2"], limit=10)
    assert len(rows) == 3
    # Most recent first.
    scores = [r.overall_agreement for r in rows]
    assert scores[0] == 0.8  # r2 inserted last
```

`tests/unit/test_skills_candidate_repo.py`:

```python
import pytest
from pathlib import Path
import aiosqlite

from donna.skills.candidate_report import SkillCandidateRepository


@pytest.fixture
async def db(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = await aiosqlite.connect(str(db_path))
    await conn.executescript("""
        CREATE TABLE skill_candidate_report (
            id TEXT PRIMARY KEY,
            capability_name TEXT,
            task_pattern_hash TEXT,
            expected_savings_usd REAL NOT NULL,
            volume_30d INTEGER NOT NULL,
            variance_score REAL,
            status TEXT NOT NULL,
            reported_at TEXT NOT NULL,
            resolved_at TEXT
        );
    """)
    await conn.commit()
    yield conn
    await conn.close()


async def test_create_and_get_new(db):
    repo = SkillCandidateRepository(db)
    report_id = await repo.create(
        capability_name="parse_task",
        task_pattern_hash=None,
        expected_savings_usd=18.5,
        volume_30d=250,
        variance_score=0.2,
    )

    candidates = await repo.list_new(limit=10)
    assert len(candidates) == 1
    assert candidates[0].capability_name == "parse_task"
    assert candidates[0].status == "new"


async def test_mark_drafted(db):
    repo = SkillCandidateRepository(db)
    report_id = await repo.create(
        capability_name="x", task_pattern_hash=None,
        expected_savings_usd=5.0, volume_30d=100, variance_score=None,
    )
    await repo.mark_drafted(report_id)
    candidates = await repo.list_new()
    assert candidates == []


async def test_mark_dismissed(db):
    repo = SkillCandidateRepository(db)
    report_id = await repo.create(
        capability_name="x", task_pattern_hash=None,
        expected_savings_usd=5.0, volume_30d=100, variance_score=None,
    )
    await repo.mark_dismissed(report_id)
    candidates = await repo.list_new()
    assert candidates == []
```

- [ ] **Step 2: Add ORM classes** to the end of `src/donna/tasks/db_models.py` (`SkillDivergence`, `SkillCandidateReport`, `SkillEvolutionLog`) following the Phase 2 Task 2 pattern.

- [ ] **Step 3: Create `src/donna/skills/divergence.py`**

```python
"""SkillDivergenceRepository — reads/writes skill_divergence rows."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiosqlite
import structlog
import uuid6

logger = structlog.get_logger()

SKILL_DIVERGENCE_COLUMNS = (
    "id", "skill_run_id", "shadow_invocation_id",
    "overall_agreement", "diff_summary", "flagged_for_evolution", "created_at",
)
SELECT_SKILL_DIVERGENCE = ", ".join(SKILL_DIVERGENCE_COLUMNS)


@dataclass(slots=True)
class SkillDivergenceRow:
    id: str
    skill_run_id: str
    shadow_invocation_id: str
    overall_agreement: float
    diff_summary: dict | None
    flagged_for_evolution: bool
    created_at: datetime


def row_to_divergence(row: tuple) -> SkillDivergenceRow:
    return SkillDivergenceRow(
        id=row[0], skill_run_id=row[1], shadow_invocation_id=row[2],
        overall_agreement=row[3],
        diff_summary=(json.loads(row[4]) if isinstance(row[4], str) else row[4]) if row[4] is not None else None,
        flagged_for_evolution=bool(row[5]),
        created_at=datetime.fromisoformat(row[6]) if isinstance(row[6], str) else row[6],
    )


class SkillDivergenceRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._conn = connection

    async def record(
        self,
        skill_run_id: str,
        shadow_invocation_id: str,
        overall_agreement: float,
        diff_summary: dict | None,
        flagged_for_evolution: bool = False,
    ) -> str:
        div_id = str(uuid6.uuid7())
        now = datetime.now(timezone.utc).isoformat()

        await self._conn.execute(
            f"""
            INSERT INTO skill_divergence ({SELECT_SKILL_DIVERGENCE})
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                div_id, skill_run_id, shadow_invocation_id,
                overall_agreement,
                json.dumps(diff_summary) if diff_summary is not None else None,
                1 if flagged_for_evolution else 0,
                now,
            ),
        )
        await self._conn.commit()
        return div_id

    async def recent_by_run_ids(self, run_ids: list[str], limit: int = 100) -> list[SkillDivergenceRow]:
        if not run_ids:
            return []
        placeholders = ",".join("?" * len(run_ids))
        cursor = await self._conn.execute(
            f"""
            SELECT {SELECT_SKILL_DIVERGENCE}
              FROM skill_divergence
             WHERE skill_run_id IN ({placeholders})
             ORDER BY created_at DESC LIMIT ?
            """,
            (*run_ids, limit),
        )
        rows = await cursor.fetchall()
        return [row_to_divergence(r) for r in rows]

    async def recent_for_skill(
        self, skill_id: str, limit: int = 100,
    ) -> list[SkillDivergenceRow]:
        """Join through skill_run to list divergences for a skill."""
        cursor = await self._conn.execute(
            f"""
            SELECT {', '.join(f'd.{c}' for c in SKILL_DIVERGENCE_COLUMNS)}
              FROM skill_divergence d
              JOIN skill_run r ON d.skill_run_id = r.id
             WHERE r.skill_id = ?
             ORDER BY d.created_at DESC
             LIMIT ?
            """,
            (skill_id, limit),
        )
        rows = await cursor.fetchall()
        return [row_to_divergence(r) for r in rows]
```

- [ ] **Step 4: Create `src/donna/skills/candidate_report.py`** following the same pattern (CRUD + `list_new`, `mark_drafted`, `mark_dismissed`, `mark_stale`).

- [ ] **Step 5: Run tests → commit**

```bash
git add src/donna/tasks/db_models.py src/donna/skills/divergence.py src/donna/skills/candidate_report.py \
        tests/unit/test_skills_divergence_repo.py tests/unit/test_skills_candidate_repo.py
git commit -m "feat(skills): add divergence + candidate repositories and ORM"
```

---

## Task 3: SkillLifecycleManager

**Files:**
- Create: `src/donna/skills/lifecycle.py`
- Create: `tests/unit/test_skills_lifecycle.py`

**Purpose:** single mutator of `skill.state`. Enforces all transitions from spec §6.2. Every state change writes a `skill_state_transition` row.

**Public API:**

```python
class SkillLifecycleManager:
    async def transition(
        self,
        skill_id: str,
        to_state: SkillState,
        reason: str,      # gate_passed | human_approval | degradation | evolution_failed | manual_override
        actor: str,       # "system" | "user"
        actor_id: str | None = None,
        notes: str | None = None,
    ) -> None
```

Internally consults a hardcoded transition table (from spec §6.2) and raises `IllegalTransitionError` if the from→to pair is not allowed. Also raises if the skill has `requires_human_gate=True` and the actor is "system".

- [ ] **Step 1: Write comprehensive state-machine tests.** Cover every legal transition plus at least three illegal transitions and one `requires_human_gate` override attempt.

- [ ] **Step 2: Implement.** Use a class-level `TRANSITIONS` dict mapping `(from_state, to_state) → allowed_reasons: set[str]`. On each transition: verify legality, check `requires_human_gate`, update `skill.state`, write an audit row.

- [ ] **Step 3: Commit**

---

## Task 4: Wire `SkillSystemConfig` through the top-level config loader

**Closes code-review issue I-1.**

**Files:**
- Modify: `src/donna/config.py`
- Create: `config/skills.yaml`
- Modify: `src/donna/capabilities/matcher.py`
- Modify: `src/donna/capabilities/registry.py`
- Modify: `src/donna/skills/startup.py`
- Create: `tests/unit/test_config_skill_system.py`

**Current state:** `SkillSystemConfig` exists but nothing loads it. Thresholds live as module constants.

**Work:**
1. Create `config/skills.yaml` with all Phase 1–3 thresholds (confidence, similarity audit, sample rates, degradation gates, budget caps, promotion counts).
2. Add a `load_skill_system_config()` function in `src/donna/config.py` or the skills package that reads the YAML and returns a `SkillSystemConfig` instance.
3. Update `CapabilityMatcher` and `CapabilityRegistry` to accept the config in their constructors and read thresholds from it. Keep the module constants as defaults if no config passed.
4. Update `initialize_skill_system()` to accept `config: SkillSystemConfig` and pass it through.
5. Document the schema in a header comment in `config/skills.yaml`.

Expected YAML:

```yaml
# Phase 1
enabled: false
match_confidence_high: 0.75
match_confidence_medium: 0.40
similarity_audit_threshold: 0.80
seed_skills_initial_state: sandbox

# Phase 3 additions
shadow_sample_rate_trusted: 0.05     # 5% while trusted; shadow_primary is always 100%
sandbox_promotion_min_runs: 20
sandbox_promotion_validity_rate: 0.90
shadow_primary_promotion_min_runs: 100
shadow_primary_promotion_agreement_rate: 0.85
degradation_rolling_window: 30
degradation_ci_confidence: 0.95
auto_draft_daily_cap: 50
auto_draft_min_expected_savings_usd: 5.0
auto_draft_fixture_pass_rate: 0.80
nightly_run_hour_utc: 3              # 3 AM UTC
```

- [ ] **Step 1: Write a config-loader test** that verifies the YAML parses into a populated `SkillSystemConfig`.
- [ ] **Step 2: Implement loader + YAML file.**
- [ ] **Step 3: Refactor matcher/registry to accept config.** Keep backward-compat: if `config=None`, use defaults.
- [ ] **Step 4: Commit**

---

## Task 5: Semantic equivalence judge

**Files:**
- Create: `src/donna/skills/equivalence.py`
- Create: `tests/unit/test_skills_equivalence.py`

A small Claude-backed module. Input: two structured outputs (A and B). Output: `{agreement: 0.0-1.0, rationale: str}`. Uses a tight prompt that says "do A and B convey the same information?"

**Public API:**

```python
class EquivalenceJudge:
    def __init__(self, model_router, task_type: str = "skill_equivalence_judge"): ...
    async def judge(self, output_a: dict, output_b: dict, context: dict | None = None) -> float
```

Returns a number in [0.0, 1.0]. Context is the task input / capability description so the judge knows what the outputs are supposed to represent.

Under the hood: calls `ModelRouter.complete(prompt, schema, model_alias="reasoner", task_type=...)`. The schema enforces `{agreement, rationale}` and `agreement` is a float 0–1.

- [ ] **Step 1: Write tests** using AsyncMock model router. Verify:
  - Equivalent outputs → high agreement
  - Malformed LLM response → returns 0.0 and logs
  - LLM call failure → returns 0.0 and logs
  - Context is included in the prompt
- [ ] **Step 2: Implement.**
- [ ] **Step 3: Register `skill_equivalence_judge` in `config/task_types.yaml` and route to `reasoner` in `config/donna_models.yaml`.**
- [ ] **Step 4: Commit**

---

## Task 6: ShadowSampler — runs Claude in parallel with skill execution

**Files:**
- Create: `src/donna/skills/shadow.py`
- Create: `tests/unit/test_skills_shadow.py`
- Modify: `src/donna/skills/executor.py` — accept optional `shadow_sampler`

**Behavior:** After a successful skill run (in `shadow_primary` or `trusted` state), the executor calls `shadow_sampler.sample_if_applicable(skill, skill_run, inputs)`. The sampler:
1. Decides whether to sample based on skill state and configured rate (100% for shadow_primary, configurable for trusted).
2. If sampling: runs the **full Claude path** for the same task (via `ModelRouter.complete` with `model_alias="reasoner"` or the task-type's default Claude model) and captures the invocation ID.
3. Asks the `EquivalenceJudge` for an agreement score comparing skill output vs Claude output.
4. Writes a `skill_divergence` row.
5. **Never raises.** All errors logged.

**Critical constraint:** shadow sampling runs after the skill has already returned its result. It cannot delay the user-facing response. Either fire-and-forget (via `asyncio.create_task`) or run synchronously only when the caller explicitly opts in.

- [ ] **Step 1: Write failing tests** covering:
  - Skill in sandbox: sample never runs
  - Skill in shadow_primary: sample runs every call
  - Skill in trusted: sample runs per configured rate (deterministic seed for test)
  - Claude call failure: logged, no crash
  - Equivalence judge failure: logged, divergence row written with `agreement=0.0`
- [ ] **Step 2: Implement.**
- [ ] **Step 3: Wire into executor.** Add `shadow_sampler: ShadowSampler | None = None` to `SkillExecutor.__init__`. After the success-path return, call `await self._shadow_sampler.sample_if_applicable(...)` if configured. For fire-and-forget: wrap in `asyncio.create_task(...)` and let exceptions log.
- [ ] **Step 4: Commit**

---

## Task 7: Consolidate Jinja render logic

**Closes code-review issue I-3.**

**Files:**
- Create: `src/donna/skills/_render.py`
- Create: `tests/unit/test_skills_render_helper.py`
- Modify: `src/donna/skills/dsl.py`
- Modify: `src/donna/skills/tool_dispatch.py`

Extract a shared `render_value(value, context, preserve_types=True)` helper from the two duplicate implementations. Both `dsl.py` and `tool_dispatch.py` call into it with appropriate flags.

The helper handles:
- Whole-expression detection (`{{ expr }}`) for type preservation
- `_AttrDict` wrapping of contexts (dict access over attribute access)
- Nested dict and list traversal
- StrictUndefined error propagation

- [ ] **Step 1: Move shared code to `_render.py` with unit tests that cover both sites' semantics.**
- [ ] **Step 2: Replace the duplicate code in `dsl.py` and `tool_dispatch.py` with calls into `_render.py`.**
- [ ] **Step 3: Run `pytest tests/unit/test_skills_dsl.py tests/unit/test_skills_tool_dispatch.py tests/unit/test_skills_render_helper.py -v` — everything passes.**
- [ ] **Step 4: Commit**

---

## Task 8: SkillCandidateDetector

**Files:**
- Create: `src/donna/skills/detector.py`
- Create: `tests/unit/test_skills_detector.py`

**Behavior:** queries `invocation_log` for calls with `task_type` matching `claude_native` pattern or explicit `claude_native` state on their skill. Computes per-task-type aggregates over the last 30 days:
- `volume_30d` — invocation count
- `avg_cost_usd` — mean cost per call
- `expected_savings_usd` — `volume_30d * avg_cost_usd * (1 - skill_overhead_ratio)` where `skill_overhead_ratio` is a config-tunable number (default 0.15 representing the 5% shadow sampling and overhead cost of running local).
- `variance_score` — a proxy for how repetitive the output patterns are (e.g., hash distinct output shapes → 1 - unique_shape_fraction).

For each task_type with `expected_savings_usd >= config.auto_draft_min_expected_savings_usd`:
- If a `new` or `drafted` candidate report already exists for the capability, skip.
- Otherwise create a `skill_candidate_report` row with `status="new"`.

Returns a list of new candidate IDs.

- [ ] **Step 1: Write tests** with a fixture DB populated with invocation_log rows. Assert correct aggregation, threshold behavior, idempotency (running twice doesn't double-insert).
- [ ] **Step 2: Implement.**
- [ ] **Step 3: Commit**

---

## Task 9: AutoDrafter

**Files:**
- Create: `src/donna/skills/auto_drafter.py`
- Create: `tests/unit/test_skills_auto_drafter.py`

**Behavior:**
Takes a `SkillCandidateReport` and produces a draft skill. Two-step process:
1. Ask Claude to generate skill YAML + step prompts + schemas + 3–5 fixtures for the target capability. Input: capability definition + recent invocation_log samples for that task_type.
2. Run fixture validation harness (`validate_against_fixtures` from Phase 2) in sandbox. Pass rate must be ≥ `config.auto_draft_fixture_pass_rate`.

If validation passes: create the `skill` + `skill_version` rows via the existing Phase 1 skill loader pattern (but from in-memory content instead of files) and transition `skill.state → draft` via `SkillLifecycleManager`. Mark the candidate report as `drafted`.

If validation fails: mark the candidate report as `dismissed` with a rationale, do NOT create a skill.

**Budget check:** before calling Claude, check `BudgetGuard.can_spend(estimated_cost)`. If insufficient, return early with reason `"budget_exhausted"`.

- [ ] **Step 1: Write tests** with AsyncMock model router. Cover: successful draft, fixture validation failure, budget exhaustion, malformed Claude output.
- [ ] **Step 2: Implement.** Use an explicit JSON schema for the Claude generation call so the output is predictable.
- [ ] **Step 3: Commit**

---

## Task 10: Promote Phase 1 seed skills from sandbox to shadow_primary

**Closes Phase 2 drift log entry 2026-04-15 Phase 1 §7.**

**Files:**
- Create: `alembic/versions/promote_seed_skills_to_shadow_primary.py`

Now that shadow sampling exists (Task 6), the Phase 1 seed skills can legitimately move to `shadow_primary`. This migration does so and records `skill_state_transition` rows for the audit trail.

- [ ] **Step 1: Create the migration.** For each of `parse_task`, `dedup_check`, `classify_priority`: update `skill.state` from `sandbox` to `shadow_primary`, insert a `skill_state_transition` with `reason="gate_passed"` and `actor="system"` and notes referencing "Phase 3 §10".
- [ ] **Step 2: Test upgrade + downgrade on a temp DB.**
- [ ] **Step 3: Commit**

---

## Task 11: DegradationDetector

**Files:**
- Create: `src/donna/skills/degradation.py`
- Create: `tests/unit/test_skills_degradation.py`

**Behavior:** For each `trusted` skill with at least `config.degradation_rolling_window` recent divergence rows:
1. Establish baseline agreement from the skill's `baseline_agreement` field (or the first `degradation_rolling_window` runs at trusted).
2. Compute Wilson score confidence interval for the current rolling window.
3. Compare CI against baseline. If `upper_bound_current < lower_bound_baseline`, the skill is statistically significantly worse.
4. Call `SkillLifecycleManager.transition(skill_id, FLAGGED_FOR_REVIEW, reason="degradation", actor="system", notes=<CI stats>)`.

Use `statsmodels.stats.proportion.proportion_confint` if available, otherwise a hand-rolled Wilson score (formula is short and well-known). Prefer hand-rolled to avoid a new heavy dependency.

**Helper function:**

```python
def wilson_score_ci(successes: int, trials: int, confidence: float = 0.95) -> tuple[float, float]:
    """Returns (lower, upper) bound of Wilson score CI for a binomial proportion."""
    if trials == 0:
        return (0.0, 1.0)
    import math
    z_by_confidence = {0.90: 1.645, 0.95: 1.96, 0.99: 2.576}
    z = z_by_confidence.get(confidence, 1.96)
    phat = successes / trials
    denom = 1 + z**2 / trials
    centre = phat + z**2 / (2 * trials)
    margin = z * math.sqrt((phat * (1 - phat) + z**2 / (4 * trials)) / trials)
    lower = max(0.0, (centre - margin) / denom)
    upper = min(1.0, (centre + margin) / denom)
    return (lower, upper)
```

- [ ] **Step 1: Write tests** covering: stable skill → no demotion, statistically significant drop → demotion, too few samples → no demotion, edge case `trials=0`.
- [ ] **Step 2: Implement.**
- [ ] **Step 3: Commit**

---

## Task 12: Auto-promotion gates integrated into ShadowSampler + LifecycleManager

**Files:**
- Modify: `src/donna/skills/shadow.py` — after each divergence write, check promotion gates
- Modify: `src/donna/skills/lifecycle.py` — add `check_and_promote_if_eligible(skill_id)` helper

**Behavior:** After `ShadowSampler` records a divergence row, call `lifecycle.check_and_promote_if_eligible(skill.id)`. That helper:
- If `state == sandbox` and skill has `>= config.sandbox_promotion_min_runs` successful runs with `validity_rate >= config.sandbox_promotion_validity_rate`: transition to `shadow_primary`.
- If `state == shadow_primary` and shadow window has `>= config.shadow_primary_promotion_min_runs` with `agreement_rate >= config.shadow_primary_promotion_agreement_rate`: transition to `trusted`, set `baseline_agreement` to observed rate.
- Otherwise no-op.

Honors `requires_human_gate` — if set, only logs that the skill is eligible for promotion but doesn't actually transition.

- [ ] **Step 1: Write tests** covering each promotion path, the `requires_human_gate` block, and the no-op cases.
- [ ] **Step 2: Implement.**
- [ ] **Step 3: Commit**

---

## Task 13: Nightly cron orchestrator

**Files:**
- Create: `src/donna/skills/crons/__init__.py`
- Create: `src/donna/skills/crons/nightly.py`
- Create: `tests/unit/test_skills_nightly_cron.py`

**Behavior:** a single entry point called by the existing scheduler at `config.nightly_run_hour_utc`:

```python
async def run_nightly_tasks(deps) -> NightlyReport:
    report = NightlyReport()
    # 1. Detect new skill candidates.
    report.new_candidates = await deps.detector.run()
    # 2. Auto-draft top candidates within remaining budget.
    #    Evolution work goes first (Phase 4), then auto-drafting.
    report.drafted = await deps.auto_drafter.run(
        remaining_budget=await deps.budget_guard.remaining_daily_budget(),
        max_drafts=deps.config.auto_draft_daily_cap,
    )
    # 3. Run degradation detector on all trusted skills.
    report.degraded = await deps.degradation.run()
    # 4. Emit the EOD digest section (handled in Task 14).
    return report
```

Logs a structured summary at the end: drafts created, drafts rejected, skills flagged, budget consumed.

- [ ] **Step 1: Write tests** that mock each component and verify call order, budget threading, and that failures in one step don't stop others.
- [ ] **Step 2: Implement.**
- [ ] **Step 3: Commit**

---

## Task 14: Implement Triage RETRY_STEP as a real retry loop (or formally deprecate)

**Closes Phase 2 drift log entry 2026-04-15 Phase 2 §6.4.**

**Files:**
- Modify: `src/donna/skills/executor.py`
- Modify: `src/donna/skills/triage.py`
- Modify: `tests/unit/test_skills_executor.py`

**Implementation path:** Implement the retry loop. When triage returns `RETRY_STEP`, the executor should:
1. Increment `retry_count` on the run.
2. Rebuild the step's prompt with `modified_prompt_additions` from the triage result appended.
3. Re-execute the step (the same `_run_llm_step` logic).
4. If it succeeds, continue with the rest of the skill.
5. If it fails again, consult triage again — this time triage's retry-cap logic kicks in and escalates.

Alternative path (if retry proves too complex): remove `RETRY_STEP` from the `TriageDecision` enum and update the spec drift log accordingly.

- [ ] **Step 1: Write a test** that verifies a triage-requested retry with modifications actually succeeds on the second attempt.
- [ ] **Step 2: Implement.**
- [ ] **Step 3: Remove the "Phase 2 defers retry" drift log note, replace with the completion note.**

---

## Task 15: Clean up no-triage failure-shape shim

**Closes Phase 2 drift log entry 2026-04-15 Phase 2 §6.4 (second entry).**

**Files:**
- Modify: `src/donna/skills/executor.py` — remove `_ModelCallError` and the phase1-style failure branch
- Modify: `tests/unit/test_skills_executor.py` — rewrite two Phase 1 tests to match the new spec wording

**New spec behavior:** without a triage configured, typed skill failures (SchemaValidationError, ToolInvocationError, DSLError, jinja2.UndefinedError) produce `status="escalated"` with `escalation_reason = f"{error_type}: {exc}"`. Model call failures produce `status="failed"` with error message.

The two affected tests:
- `test_executor_fails_on_schema_validation_error` — currently expects `status="failed"`, update to `status="escalated"` with the relevant reason.
- `test_executor_fails_on_model_exception` — keeps `status="failed"` but the error message now comes from the generic exception handler, not the `_ModelCallError` wrapper.

- [ ] **Step 1: Update the two tests** to match the new spec-aligned behavior.
- [ ] **Step 2: Remove `_ModelCallError` and the `_phase1_style_failure_result` branch in `executor.py`.**
- [ ] **Step 3: Run the full executor test suite.** All pass.
- [ ] **Step 4: Commit**

---

## Task 16: Dashboard routes — candidates, draft review, divergences

**Files:**
- Create: `src/donna/api/routes/skill_candidates.py`
- Create: `src/donna/api/routes/skill_drafts.py`
- Modify: `src/donna/api/routes/skills.py` — add POST `/skills/{id}/state` and POST `/skills/{id}/flags/requires_human_gate`
- Modify: `src/donna/api/routes/skill_runs.py` — add GET `/skill-runs/{id}/divergence`
- Modify: `src/donna/api/__init__.py`
- Create: `tests/unit/test_api_skill_candidates.py`
- Create: `tests/unit/test_api_skill_drafts.py`
- Create: `tests/unit/test_api_skill_divergences.py`

New endpoints:

- `GET /admin/skill-candidates` — list new candidates (paginated)
- `POST /admin/skill-candidates/{id}/dismiss` — mark a candidate dismissed
- `POST /admin/skill-candidates/{id}/draft-now` — trigger auto-draft immediately (bypass nightly cron)
- `GET /admin/skill-drafts` — list skills in `draft` state
- `POST /admin/skills/{id}/state` — user-driven state transitions (approve, reject, promote, demote). Body: `{to_state, reason, notes}`. Goes through `SkillLifecycleManager` with `actor="user"`.
- `POST /admin/skills/{id}/flags/requires_human_gate` — toggle the flag. Body: `{value: bool}`.
- `GET /admin/skill-runs/{id}/divergence` — shadow divergence details for a skill run (if any)

Follow the existing Phase 1+2 route patterns (FastAPI APIRouter, `request.app.state.db.connection` for DB).

- [ ] **Step 1: Write tests for each new endpoint** covering happy path + 404 + bad input.
- [ ] **Step 2: Implement.**
- [ ] **Step 3: Register the new routers** in `src/donna/api/__init__.py` under `/admin` prefix.
- [ ] **Step 4: Commit**

---

## Task 17: EOD digest "Skill system changes" section

**Files:**
- Modify: `src/donna/notifications/eod_digest.py`
- Create: `tests/unit/test_eod_digest_skill_section.py`

**Content:** new section in the daily digest with:
- Skills flagged for review today (link to dashboard)
- Skills auto-drafted today (capability name, expected monthly savings, link to dashboard)
- Skills promoted today (sandbox → shadow_primary or shadow_primary → trusted)
- Skills demoted today (trusted → flagged_for_review)
- Total Claude spend on skill-system work (auto-drafting + shadow sampling + triage)

Pulls data from `skill_state_transition` + `skill_candidate_report` + `skill_divergence` + `invocation_log`, filtered to the last 24 hours.

- [ ] **Step 1: Write test** with fixture data for yesterday's transitions/candidates and verify the section content.
- [ ] **Step 2: Implement.**
- [ ] **Step 3: Commit**

---

## Task 18: Phase 3 end-to-end integration test

**Files:**
- Create: `tests/integration/test_skill_system_phase_3_e2e.py`

Verifies the Phase 3 handoff contract:
- H3.1: A `claude_native` task type with high volume + positive savings gets surfaced as a `skill_candidate_report` row by the nightly detector.
- H3.2: The auto-drafter consumes a top-ranked candidate, generates a draft skill, passes fixture validation, and creates a `skill.state == draft` row. Budget is respected.
- H3.3: User-driven `POST /admin/skills/{id}/state` transitions work through `SkillLifecycleManager`.
- H3.4: Auto-promotion from `sandbox → shadow_primary` fires after enough successful runs.
- H3.5: Auto-promotion from `shadow_primary → trusted` fires after enough agreeing shadow samples.
- H3.6: Statistical degradation triggers transition to `flagged_for_review` with a recorded reason.
- H3.7: `requires_human_gate` prevents auto-promotion but allows manual promotion.

- [ ] **Step 1: Write the test** (will be long — each scenario sets up a mini fixture chain).
- [ ] **Step 2: Run and commit.**

---

## Task 19: Update spec drift log + Phase 3 handoff contract

**Files:**
- Modify: `docs/superpowers/specs/2026-04-15-skill-system-and-challenger-refactor-design.md`

Check off:
- R17 (lifecycle state machine)
- R18 (sandbox → shadow_primary auto-promotion)
- R19 (shadow_primary → trusted auto-promotion)
- R22 (candidate detector)
- R23 (auto-drafter with budget guard)
- R24 (EOD digest)
- R25 (Wilson CI degradation)
- R28 (evolution 4-gate validation) — partial, full Phase 4
- R38 / R39 / R40 — reconfirm these still hold

Add closing drift entries for the Phase 2 items that Phase 3 closed (triage retry loop, no-triage shim, seed skill promotion, SkillSystemConfig wiring, Jinja duplication consolidation). Each entry marked `Resolved in Phase 3 Task N`.

Add any new drift entries introduced during Phase 3 implementation.

- [ ] **Step 1: Update the spec**
- [ ] **Step 2: Commit**

---

## Task 20: Update setup doc for Phase 3 + update diagrams

**Files:**
- Modify: `docs/phase-1-skill-system-setup.md` — rename internally or just update contents
- Modify: `donna-diagrams.html` — update the skill system architecture diagram with Phase 3 components

Setup doc additions:
- Phase 3 migrations (1 new migration + 1 seed migration)
- `config/skills.yaml` setup with explanation of every knob
- Wiring: register `ShadowSampler`, `DegradationDetector`, `SkillCandidateDetector`, `AutoDrafter`, `SkillLifecycleManager` into the executor + nightly cron
- Scheduler cron entry to call `run_nightly_tasks` at `config.nightly_run_hour_utc`
- New dashboard routes for draft review and candidates

Diagram updates:
- Add the lifecycle state machine as its own panel
- Show the nightly cron as a new component linking detector → auto_drafter → degradation → digest
- Show shadow sampling as an arrow from SkillExecutor through ShadowSampler to skill_divergence table

- [ ] **Step 1: Update setup doc.**
- [ ] **Step 2: Update diagrams.**
- [ ] **Step 3: Commit**

---

## Self-Review (fill in during execution)

After completing all tasks:

```bash
pytest tests/unit/ -v -m "not slow"
pytest tests/unit/ -v -m slow
pytest tests/integration/test_skill_system_phase_3_e2e.py -v
```

Verify:
- [ ] All unit tests pass (target: 820+ passed, same 5 pre-existing failures)
- [ ] Phase 3 E2E test passes
- [ ] Phase 1 + Phase 2 E2E tests still pass
- [ ] `_ModelCallError` shim gone from `executor.py`
- [ ] Duplicate Jinja render logic gone from `dsl.py` and `tool_dispatch.py`
- [ ] `SkillSystemConfig` is loaded from `config/skills.yaml` at startup
- [ ] Every skill state transition in the DB has a matching `skill_state_transition` row
- [ ] Drift log reflects Phase 3 closures and any new entries

---

## Phase 3 Acceptance Scenarios (from spec §7)

**AS-3.1:** Task type with 30 invocations / 30 days at $0.15 each → flagged as candidate with `expected_savings_usd ≈ $4.5` (below default threshold of $5). Candidate is **not drafted**.

**AS-3.2:** Task type with 200 invocations / 30 days at $0.10 each → flagged with `expected_savings ≈ $20`. Auto-drafter picks it up at end-of-day, generates skill, fixtures pass, `skill.state = draft`, candidate status → `drafted`. EOD digest announces the new draft.

**AS-3.3:** User clicks approve on a draft → POST `/admin/skills/{id}/state` with `to_state=sandbox`, `reason=human_approval` → transition succeeds via lifecycle manager. 20 successful runs later, skill auto-promotes to `shadow_primary`. 100 agreeing shadow samples later, auto-promotes to `trusted`.

**AS-3.4:** Claude-generated draft fails fixture validation (pass rate 60%, below 80% threshold) → auto-drafter marks candidate `dismissed` with rationale, does not create skill. EOD digest reports rejection count.

**AS-3.5:** Auto-drafter attempts to run but daily budget is exhausted → defers all candidates to tomorrow. Logged clearly. No partial work.

---

## Notes for the Implementer

- **Read the spec + Phase 1 plan + Phase 2 plan first.** Phase 3 depends on the machinery built in Phases 1 and 2. In particular: `SkillExecutor`, `SkillRunRepository`, `CapabilityRegistry`, `TriageAgent`, the `skill.state` enum values, and the `skill_state_transition` table are all in use from day one of Phase 3.

- **`SkillLifecycleManager` is the most important new concept.** Once it's in place, every state change MUST go through it. Grep the codebase at the end of Phase 3 for `skill.state =` and `UPDATE skill SET state` — the only matches should be inside `SkillLifecycleManager`.

- **Shadow sampling must never block the user path.** Use `asyncio.create_task` for fire-and-forget OR run synchronously only when the caller explicitly passes an option. Never `await` a shadow call in the critical path.

- **Tasks 14 + 15 (drift cleanup) can happen in parallel with the rest** — they don't depend on new Phase 3 infrastructure. Good warm-up tasks for early subagents.

- **Task 20 (diagram updates) is the only task that modifies HTML/CSS.** Consider batching it with the setup-doc update.

- **Model selection for subagents:**
  - Cheap (haiku): Tasks 1, 2, 4, 10, 19, 20
  - Standard (sonnet): Tasks 3, 5, 6, 7, 8, 11, 12, 13, 16, 17, 18
  - Most capable (opus): Task 9 (auto-drafter — Claude generating Claude-generated skills is recursive and subtle), Task 14 (executor retry loop is a real change to the hot path), Task 15 (executor shim removal requires careful test surgery).

- **Observability is non-negotiable.** Every new component emits structured logs with the standard event-key pattern. Add entries to the invocation_log for every Claude call (auto-drafter, shadow sampler, equivalence judge, triage) so the budget view in the dashboard stays accurate.

- **Don't boil the ocean.** The Phase 3 evolution loop (degraded → Claude re-evolves skill) is **explicitly Phase 4**. If a subagent wanders into evolution code, gently steer them back — Phase 3's job is to detect and flag, not repair.

- **Budget integration is easy to get wrong.** `BudgetGuard.can_spend(estimated_cost)` must be called before every Claude invocation in auto-drafting and equivalence judging. Shadow sampling is subtler: we've already committed to running Claude (shadow is not optional at shadow_primary state), so skipping shadow on budget exhaustion means skill quality is silently uncalibrated. Instead, warn loudly and consider an "emergency shadow skip" metric in the dashboard rather than silent degradation.

- **Data model sanity check.** At the end of Phase 3 the full skill-system schema should be: `capability`, `skill`, `skill_version`, `skill_state_transition`, `skill_run`, `skill_step_result`, `skill_fixture`, `skill_divergence`, `skill_candidate_report`, `skill_evolution_log`. Ten tables. No more in Phase 3.
