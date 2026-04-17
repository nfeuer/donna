from pathlib import Path

import aiosqlite
import pytest

from donna.skills.evolution_log import SkillEvolutionLogRepository


@pytest.fixture
async def db(tmp_path: Path):
    conn = await aiosqlite.connect(str(tmp_path / "test.db"))
    await conn.executescript("""
        CREATE TABLE skill_evolution_log (
            id TEXT PRIMARY KEY, skill_id TEXT NOT NULL,
            from_version_id TEXT NOT NULL, to_version_id TEXT,
            triggered_by TEXT NOT NULL, claude_invocation_id TEXT,
            diagnosis TEXT, targeted_case_ids TEXT,
            validation_results TEXT, outcome TEXT NOT NULL,
            at TEXT NOT NULL
        );
    """)
    await conn.commit()
    yield conn
    await conn.close()


async def test_record_success(db):
    repo = SkillEvolutionLogRepository(db)
    entry_id = await repo.record(
        skill_id="s1", from_version_id="v1", to_version_id="v2",
        triggered_by="statistical_degradation",
        claude_invocation_id="inv-1",
        diagnosis={"step": "extract", "pattern": "noise"},
        targeted_case_ids=["r1", "r2"],
        validation_results={"structural": True, "targeted": 1.0},
        outcome="success",
    )
    cursor = await db.execute(
        "SELECT skill_id, to_version_id, outcome, diagnosis "
        "FROM skill_evolution_log WHERE id = ?",
        (entry_id,),
    )
    row = await cursor.fetchone()
    assert row[0] == "s1"
    assert row[1] == "v2"
    assert row[2] == "success"
    import json
    assert json.loads(row[3]) == {"step": "extract", "pattern": "noise"}


async def test_record_rejected_validation_leaves_to_version_null(db):
    repo = SkillEvolutionLogRepository(db)
    entry_id = await repo.record(
        skill_id="s1", from_version_id="v1", to_version_id=None,
        triggered_by="correction_cluster",
        claude_invocation_id="inv-2",
        diagnosis=None, targeted_case_ids=None,
        validation_results={"fixture_regression": 0.7},
        outcome="rejected_validation",
    )
    cursor = await db.execute(
        "SELECT to_version_id, outcome FROM skill_evolution_log WHERE id = ?",
        (entry_id,),
    )
    row = await cursor.fetchone()
    assert row[0] is None
    assert row[1] == "rejected_validation"


async def test_last_n_outcomes_returns_newest_first(db):
    repo = SkillEvolutionLogRepository(db)
    for outcome in ["rejected_validation", "rejected_validation", "success"]:
        await repo.record(
            skill_id="s1", from_version_id="v1", to_version_id=None,
            triggered_by="test", claude_invocation_id=None,
            diagnosis=None, targeted_case_ids=None,
            validation_results=None, outcome=outcome,
        )

    outcomes = await repo.last_n_outcomes(skill_id="s1", n=2)
    assert outcomes == ["success", "rejected_validation"]


async def test_last_n_outcomes_empty_for_unknown_skill(db):
    repo = SkillEvolutionLogRepository(db)
    outcomes = await repo.last_n_outcomes(skill_id="never", n=5)
    assert outcomes == []
