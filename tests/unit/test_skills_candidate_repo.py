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
            resolved_at TEXT,
            reasoning TEXT
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


async def test_mark_stale(db):
    repo = SkillCandidateRepository(db)
    report_id = await repo.create(
        capability_name="x", task_pattern_hash=None,
        expected_savings_usd=5.0, volume_30d=100, variance_score=None,
    )
    await repo.mark_stale(report_id)
    candidates = await repo.list_new()
    assert candidates == []


async def test_list_new_ordered_by_savings_desc(db):
    repo = SkillCandidateRepository(db)
    for savings in [5.0, 20.0, 10.0]:
        await repo.create(
            capability_name=f"cap_{savings}",
            task_pattern_hash=None,
            expected_savings_usd=savings,
            volume_30d=100,
            variance_score=None,
        )

    candidates = await repo.list_new(limit=10)
    assert len(candidates) == 3
    assert candidates[0].expected_savings_usd == 20.0
    assert candidates[1].expected_savings_usd == 10.0
    assert candidates[2].expected_savings_usd == 5.0


async def test_create_returns_unique_ids(db):
    repo = SkillCandidateRepository(db)
    id1 = await repo.create(
        capability_name="a", task_pattern_hash=None,
        expected_savings_usd=1.0, volume_30d=10, variance_score=None,
    )
    id2 = await repo.create(
        capability_name="b", task_pattern_hash=None,
        expected_savings_usd=2.0, volume_30d=20, variance_score=None,
    )
    assert id1 != id2


async def test_mark_drafted_sets_resolved_at(db):
    repo = SkillCandidateRepository(db)
    report_id = await repo.create(
        capability_name="x", task_pattern_hash=None,
        expected_savings_usd=5.0, volume_30d=100, variance_score=None,
    )
    await repo.mark_drafted(report_id)
    cursor = await db.execute(
        "SELECT status, resolved_at FROM skill_candidate_report WHERE id = ?",
        (report_id,),
    )
    row = await cursor.fetchone()
    assert row[0] == "drafted"
    assert row[1] is not None


async def test_row_mapper_fields(db):
    repo = SkillCandidateRepository(db)
    report_id = await repo.create(
        capability_name="parse_task",
        task_pattern_hash="abc123",
        expected_savings_usd=18.5,
        volume_30d=250,
        variance_score=0.2,
    )

    candidates = await repo.list_new()
    assert len(candidates) == 1
    r = candidates[0]
    assert r.id == report_id
    assert r.capability_name == "parse_task"
    assert r.task_pattern_hash == "abc123"
    assert r.expected_savings_usd == 18.5
    assert r.volume_30d == 250
    assert r.variance_score == 0.2
    assert r.status == "new"
    assert r.resolved_at is None
