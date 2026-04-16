"""Tests for EvolutionScheduler — nightly batch runner for degraded skills."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from donna.config import SkillSystemConfig
from donna.skills.evolution import EvolutionReport
from donna.skills.evolution_scheduler import EvolutionScheduler


@pytest.fixture
async def db(tmp_path: Path):
    conn = await aiosqlite.connect(str(tmp_path / "test.db"))
    await conn.executescript("""
        CREATE TABLE skill (
            id TEXT PRIMARY KEY, capability_name TEXT NOT NULL UNIQUE,
            current_version_id TEXT, state TEXT NOT NULL,
            requires_human_gate INTEGER NOT NULL DEFAULT 0,
            baseline_agreement REAL, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)
    await conn.commit()
    yield conn
    await conn.close()


async def _seed_skills(db, n_degraded: int):
    now = datetime.now(timezone.utc).isoformat()
    for i in range(n_degraded):
        await db.execute(
            "INSERT INTO skill (id, capability_name, current_version_id, "
            "state, requires_human_gate, baseline_agreement, created_at, "
            "updated_at) VALUES (?, ?, ?, 'degraded', 0, 0.9, ?, ?)",
            (f"s{i}", f"cap{i}", f"v{i}", now, now),
        )
    await db.execute(
        "INSERT INTO skill (id, capability_name, current_version_id, "
        "state, requires_human_gate, baseline_agreement, created_at, "
        "updated_at) VALUES ('s_trusted', 'trusted_cap', 'v99', "
        "'trusted', 0, 0.95, ?, ?)",
        (now, now),
    )
    await db.commit()


async def test_scheduler_iterates_degraded_skills(db):
    """Test that scheduler iterates degraded skills and skips other states."""
    await _seed_skills(db, n_degraded=3)

    evolver = AsyncMock()
    evolver.evolve_one.side_effect = [
        EvolutionReport(skill_id="s0", outcome="success", new_version_id="v"),
        EvolutionReport(skill_id="s1", outcome="rejected_validation"),
        EvolutionReport(skill_id="s2", outcome="success", new_version_id="v"),
    ]

    scheduler = EvolutionScheduler(
        connection=db, evolver=evolver,
        config=SkillSystemConfig(evolution_daily_cap=10),
    )
    reports = await scheduler.run(remaining_budget_usd=100.0)

    assert len(reports) == 3
    assert [r.outcome for r in reports] == ["success", "rejected_validation", "success"]
    call_skill_ids = {
        c.kwargs.get("skill_id") for c in evolver.evolve_one.await_args_list
    }
    assert "s_trusted" not in call_skill_ids


async def test_scheduler_respects_daily_cap(db):
    """Test that scheduler respects evolution_daily_cap."""
    await _seed_skills(db, n_degraded=5)

    evolver = AsyncMock()
    evolver.evolve_one.return_value = EvolutionReport(
        skill_id="?", outcome="success", new_version_id="v",
    )

    scheduler = EvolutionScheduler(
        connection=db, evolver=evolver,
        config=SkillSystemConfig(evolution_daily_cap=2),
    )
    reports = await scheduler.run(remaining_budget_usd=100.0)
    assert len(reports) == 2


async def test_scheduler_stops_when_budget_exhausted(db):
    """Test that scheduler stops when remaining budget < estimated cost."""
    await _seed_skills(db, n_degraded=5)

    evolver = AsyncMock()
    evolver.evolve_one.return_value = EvolutionReport(
        skill_id="?", outcome="success", new_version_id="v",
    )
    scheduler = EvolutionScheduler(
        connection=db, evolver=evolver,
        config=SkillSystemConfig(evolution_estimated_cost_usd=0.75),
    )
    reports = await scheduler.run(remaining_budget_usd=1.0)
    assert len(reports) == 1


async def test_scheduler_returns_empty_when_no_degraded_skills(db):
    """Test that scheduler returns empty list when no degraded skills."""
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO skill (id, capability_name, current_version_id, state, "
        "requires_human_gate, baseline_agreement, created_at, updated_at) "
        "VALUES ('s1', 'cap', 'v1', 'trusted', 0, 0.9, ?, ?)",
        (now, now),
    )
    await db.commit()

    evolver = AsyncMock()
    scheduler = EvolutionScheduler(
        connection=db, evolver=evolver, config=SkillSystemConfig(),
    )
    reports = await scheduler.run(remaining_budget_usd=100.0)
    assert reports == []
    evolver.evolve_one.assert_not_awaited()
