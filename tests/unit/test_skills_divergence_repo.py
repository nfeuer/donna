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
    for i, score in enumerate([0.9, 0.7, 0.8]):
        await repo.record(
            skill_run_id=f"r{i}",
            shadow_invocation_id=f"inv-{i}",
            overall_agreement=score,
            diff_summary=None,
        )

    rows = await repo.recent_by_run_ids(["r0", "r1", "r2"], limit=10)
    assert len(rows) == 3
    scores = [r.overall_agreement for r in rows]
    assert scores[0] == 0.8  # r2 inserted last


async def test_record_divergence_no_diff_summary(db):
    repo = SkillDivergenceRepository(db)
    div_id = await repo.record(
        skill_run_id="r1",
        shadow_invocation_id="inv-1",
        overall_agreement=1.0,
        diff_summary=None,
    )

    cursor = await db.execute(
        "SELECT diff_summary, flagged_for_evolution FROM skill_divergence WHERE id = ?",
        (div_id,),
    )
    row = await cursor.fetchone()
    assert row[0] is None
    assert row[1] == 0


async def test_record_divergence_flagged(db):
    repo = SkillDivergenceRepository(db)
    div_id = await repo.record(
        skill_run_id="r1",
        shadow_invocation_id="inv-1",
        overall_agreement=0.4,
        diff_summary={"diff": "major"},
        flagged_for_evolution=True,
    )

    cursor = await db.execute(
        "SELECT flagged_for_evolution FROM skill_divergence WHERE id = ?",
        (div_id,),
    )
    row = await cursor.fetchone()
    assert row[0] == 1


async def test_recent_by_run_ids_empty_list(db):
    repo = SkillDivergenceRepository(db)
    rows = await repo.recent_by_run_ids([], limit=10)
    assert rows == []


async def test_row_to_divergence_mapper(db):
    repo = SkillDivergenceRepository(db)
    div_id = await repo.record(
        skill_run_id="r1",
        shadow_invocation_id="inv-1",
        overall_agreement=0.75,
        diff_summary={"key": "val"},
        flagged_for_evolution=True,
    )

    rows = await repo.recent_by_run_ids(["r1"])
    assert len(rows) == 1
    r = rows[0]
    assert r.id == div_id
    assert r.skill_run_id == "r1"
    assert r.shadow_invocation_id == "inv-1"
    assert r.overall_agreement == 0.75
    assert r.diff_summary == {"key": "val"}
    assert r.flagged_for_evolution is True
