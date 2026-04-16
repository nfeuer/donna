from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from donna.config import SkillSystemConfig
from donna.skills.correction_cluster import CorrectionClusterDetector


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
        CREATE TABLE skill_run (
            id TEXT PRIMARY KEY, skill_id TEXT NOT NULL,
            skill_version_id TEXT, status TEXT NOT NULL,
            state_object TEXT NOT NULL, user_id TEXT NOT NULL,
            started_at TEXT NOT NULL, finished_at TEXT,
            task_id TEXT, automation_run_id TEXT,
            total_latency_ms INTEGER, total_cost_usd REAL,
            tool_result_cache TEXT, final_output TEXT,
            escalation_reason TEXT, error TEXT
        );
        CREATE TABLE correction_log (
            id TEXT PRIMARY KEY, timestamp TEXT NOT NULL,
            user_id TEXT NOT NULL, task_type TEXT NOT NULL,
            task_id TEXT NOT NULL, input_text TEXT NOT NULL,
            field_corrected TEXT NOT NULL, original_value TEXT NOT NULL,
            corrected_value TEXT NOT NULL, rule_extracted TEXT
        );
    """)
    await conn.commit()
    yield conn
    await conn.close()


async def _seed(db, skill_state="trusted"):
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO skill (id, capability_name, current_version_id, state, "
        "requires_human_gate, baseline_agreement, created_at, updated_at) "
        "VALUES ('s1', 'parse_task', 'v1', ?, 0, 0.95, ?, ?)",
        (skill_state, now, now),
    )
    for i in range(10):
        await db.execute(
            "INSERT INTO skill_run (id, skill_id, status, state_object, "
            "user_id, started_at) VALUES (?, 's1', 'succeeded', '{}', "
            "'nick', ?)",
            (f"r{i}", now),
        )
    await db.commit()


async def test_detector_fires_when_corrections_exceed_threshold(db):
    await _seed(db)
    now = datetime.now(timezone.utc).isoformat()
    for i in range(3):
        await db.execute(
            "INSERT INTO correction_log (id, timestamp, user_id, task_type, "
            "task_id, input_text, field_corrected, original_value, "
            "corrected_value) VALUES (?, ?, 'nick', 'parse_task', ?, "
            "'x', 'title', 'a', 'b')",
            (f"c{i}", now, f"r{i}"),
        )
    await db.commit()

    lifecycle = AsyncMock()
    notifier = AsyncMock()
    detector = CorrectionClusterDetector(
        connection=db, lifecycle_manager=lifecycle,
        notifier=notifier, config=SkillSystemConfig(),
    )
    reports = await detector.scan_once()
    assert len(reports) == 1
    assert reports[0]["skill_id"] == "s1"
    lifecycle.transition.assert_awaited_once()
    notifier.assert_awaited_once()


async def test_detector_silent_when_below_threshold(db):
    await _seed(db)
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO correction_log (id, timestamp, user_id, task_type, "
        "task_id, input_text, field_corrected, original_value, "
        "corrected_value) VALUES ('c1', ?, 'nick', 'parse_task', 'r1', "
        "'x', 'title', 'a', 'b')",
        (now,),
    )
    await db.commit()

    lifecycle = AsyncMock()
    notifier = AsyncMock()
    detector = CorrectionClusterDetector(
        connection=db, lifecycle_manager=lifecycle,
        notifier=notifier, config=SkillSystemConfig(),
    )
    reports = await detector.scan_once()
    assert reports == []
    lifecycle.transition.assert_not_awaited()


async def test_detector_only_scans_eligible_skill_states(db):
    await _seed(db, skill_state="sandbox")
    now = datetime.now(timezone.utc).isoformat()
    for i in range(5):
        await db.execute(
            "INSERT INTO correction_log (id, timestamp, user_id, task_type, "
            "task_id, input_text, field_corrected, original_value, "
            "corrected_value) VALUES (?, ?, 'nick', 'parse_task', ?, "
            "'x', 'title', 'a', 'b')",
            (f"c{i}", now, f"r{i}"),
        )
    await db.commit()

    lifecycle = AsyncMock()
    notifier = AsyncMock()
    detector = CorrectionClusterDetector(
        connection=db, lifecycle_manager=lifecycle,
        notifier=notifier, config=SkillSystemConfig(),
    )
    reports = await detector.scan_once()
    assert reports == []


async def test_detector_idempotent_when_already_flagged(db):
    await _seed(db, skill_state="flagged_for_review")
    now = datetime.now(timezone.utc).isoformat()
    for i in range(5):
        await db.execute(
            "INSERT INTO correction_log (id, timestamp, user_id, task_type, "
            "task_id, input_text, field_corrected, original_value, "
            "corrected_value) VALUES (?, ?, 'nick', 'parse_task', ?, "
            "'x', 'title', 'a', 'b')",
            (f"c{i}", now, f"r{i}"),
        )
    await db.commit()

    lifecycle = AsyncMock()
    notifier = AsyncMock()
    detector = CorrectionClusterDetector(
        connection=db, lifecycle_manager=lifecycle,
        notifier=notifier, config=SkillSystemConfig(),
    )
    reports = await detector.scan_once()
    assert reports == []
    lifecycle.transition.assert_not_awaited()
    notifier.assert_not_awaited()
