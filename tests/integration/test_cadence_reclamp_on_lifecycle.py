"""Integration: lifecycle state change fires CadenceReclamper.

Exercises the real SkillLifecycleManager.after_state_change hook and the real
AutomationRepository.list_by_capability / update_active_cadence methods to
confirm end-to-end wiring from a skill state transition to an automation's
active_cadence_cron being rewritten.
"""
from __future__ import annotations

import pathlib
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from donna.automations.cadence_policy import CadencePolicy
from donna.automations.cadence_reclamper import CadenceReclamper
from donna.automations.repository import AutomationRepository
from donna.config import SkillSystemConfig
from donna.skills.lifecycle import SkillLifecycleManager
from donna.tasks.db_models import SkillState


class _SchedulerStub:
    async def compute_next_run(self, cron):
        return datetime.now(UTC)


@pytest.fixture
async def db(tmp_path: Path):
    conn = await aiosqlite.connect(str(tmp_path / "test.db"))
    await conn.executescript(
        """
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
            created_via TEXT NOT NULL,
            active_cadence_cron TEXT
        );
        CREATE TABLE skill (
            id TEXT PRIMARY KEY,
            capability_name TEXT NOT NULL,
            current_version_id TEXT,
            state TEXT NOT NULL,
            requires_human_gate INTEGER NOT NULL DEFAULT 0,
            baseline_agreement REAL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE skill_state_transition (
            id TEXT PRIMARY KEY,
            skill_id TEXT NOT NULL,
            from_state TEXT NOT NULL,
            to_state TEXT NOT NULL,
            reason TEXT NOT NULL,
            actor TEXT NOT NULL,
            actor_id TEXT,
            at TEXT NOT NULL,
            notes TEXT
        );
        """
    )
    now = datetime.now(UTC).isoformat()
    await conn.execute(
        "INSERT INTO capability (id, name, description, input_schema, "
        "trigger_type, status, created_at, created_by) VALUES "
        "('c1', 'product_watch', 'cap', '{}', 'on_schedule', 'active', ?, 'seed')",
        (now,),
    )
    await conn.execute(
        "INSERT INTO skill (id, capability_name, state, requires_human_gate, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("sk1", "product_watch", SkillState.SANDBOX.value, 0, now, now),
    )
    await conn.commit()
    yield conn
    await conn.close()


@pytest.mark.asyncio
async def test_reclamper_fires_on_lifecycle_transition(db) -> None:
    """When SkillLifecycleManager.after_state_change fires, the reclamper
    rewrites active_cadence_cron on matching automations."""
    repo = AutomationRepository(db)

    aid = await repo.create(
        user_id="u1",
        name="watch shirt",
        description=None,
        capability_name="product_watch",
        inputs={"url": "https://x.com/shirt"},
        trigger_type="on_schedule",
        schedule="*/15 * * * *",
        alert_conditions={},
        alert_channels=["discord_dm"],
        max_cost_per_run_usd=None,
        min_interval_seconds=900,
        created_via="discord",
        target_cadence_cron="*/15 * * * *",
        active_cadence_cron="0 */12 * * *",  # sandbox floor
    )

    policy = CadencePolicy.load(pathlib.Path("config/automations.yaml"))
    reclamper = CadenceReclamper(
        repo=repo, policy=policy, scheduler=_SchedulerStub()
    )

    mgr = SkillLifecycleManager(db, config=SkillSystemConfig())
    mgr.after_state_change.register(
        lambda cap, new_state: reclamper.reclamp_for_capability(cap, new_state)
    )

    await mgr.transition(
        skill_id="sk1",
        to_state=SkillState.SHADOW_PRIMARY,
        reason="gate_passed",
        actor="system",
    )

    row = await repo.get(aid)
    # shadow_primary floor in config/automations.yaml is 3600s -> hourly
    assert row.active_cadence_cron == "0 * * * *"
