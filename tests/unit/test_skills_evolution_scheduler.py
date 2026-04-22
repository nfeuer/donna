"""Tests for EvolutionScheduler — nightly batch runner for degraded skills."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import aiosqlite
import pytest
import structlog

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
    now = datetime.now(UTC).isoformat()
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


async def test_scheduler_emits_skill_evolution_outcome_per_attempt(db):
    """Wave 3 F-12: each EvolutionReport produces a `skill_evolution_outcome`
    structlog event with skill_id, outcome, cost_usd, latency_ms, new_version_id,
    rationale. Powers panel 2 of the Skill System Grafana dashboard."""
    await _seed_skills(db, n_degraded=2)

    evolver = AsyncMock()
    evolver.evolve_one.side_effect = [
        EvolutionReport(
            skill_id="s0", outcome="success", new_version_id="v-new",
            rationale="all 4 gates passed", cost_usd=0.42, latency_ms=1234,
        ),
        EvolutionReport(
            skill_id="s1", outcome="rejected_validation",
            rationale="targeted case gate failed",
            cost_usd=0.37, latency_ms=987,
        ),
    ]

    scheduler = EvolutionScheduler(
        connection=db, evolver=evolver,
        config=SkillSystemConfig(evolution_daily_cap=10),
    )

    with structlog.testing.capture_logs() as cap:
        await scheduler.run(remaining_budget_usd=100.0)

    outcome_events = [e for e in cap if e.get("event") == "skill_evolution_outcome"]
    assert len(outcome_events) == 2

    by_skill = {e["skill_id"]: e for e in outcome_events}
    assert by_skill["s0"]["outcome"] == "success"
    assert by_skill["s0"]["cost_usd"] == pytest.approx(0.42)
    assert by_skill["s0"]["latency_ms"] == 1234
    assert by_skill["s0"]["new_version_id"] == "v-new"
    assert by_skill["s0"]["rationale"] == "all 4 gates passed"

    assert by_skill["s1"]["outcome"] == "rejected_validation"
    assert by_skill["s1"]["cost_usd"] == pytest.approx(0.37)
    assert by_skill["s1"]["latency_ms"] == 987


async def test_scheduler_emits_outcome_even_when_evolver_raises(db):
    """Unexpected evolver errors still produce a `skill_evolution_outcome`
    event (with outcome=error, cost_usd=0) so the dashboard does not miss
    failed attempts."""
    await _seed_skills(db, n_degraded=1)

    evolver = AsyncMock()
    evolver.evolve_one.side_effect = RuntimeError("boom")

    scheduler = EvolutionScheduler(
        connection=db, evolver=evolver,
        config=SkillSystemConfig(evolution_daily_cap=10),
    )

    with structlog.testing.capture_logs() as cap:
        await scheduler.run(remaining_budget_usd=100.0)

    outcome_events = [e for e in cap if e.get("event") == "skill_evolution_outcome"]
    assert len(outcome_events) == 1
    assert outcome_events[0]["outcome"] == "error"
    assert outcome_events[0]["cost_usd"] == 0.0


async def test_scheduler_returns_empty_when_no_degraded_skills(db):
    """Test that scheduler returns empty list when no degraded skills."""
    now = datetime.now(UTC).isoformat()
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
