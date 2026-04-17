"""F-7: log_correction fires CorrectionClusterDetector scan synchronously."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
from alembic import command
from alembic.config import Config


@pytest.fixture
async def seeded_db_with_trusted_skill(tmp_path):
    """DB with a trusted skill for capability_name='test_cap'."""
    db = tmp_path / "t.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    command.upgrade(cfg, "head")

    conn = await aiosqlite.connect(db)
    now = datetime.now(timezone.utc).isoformat()
    skill_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO capability (id, name, description, input_schema, "
        "trigger_type, status, created_at, created_by) "
        "VALUES (?, 'test_cap', '', '{}', 'on_message', 'active', ?, 'seed')",
        (str(uuid.uuid4()), now),
    )
    await conn.execute(
        "INSERT INTO skill (id, capability_name, current_version_id, state, "
        "requires_human_gate, baseline_agreement, created_at, updated_at) "
        "VALUES (?, 'test_cap', ?, 'trusted', 0, 0.9, ?, ?)",
        (skill_id, version_id, now, now),
    )
    await conn.execute(
        "INSERT INTO skill_version (id, skill_id, version_number, yaml_backbone, "
        "step_content, output_schemas, created_by, created_at) "
        "VALUES (?, ?, 1, 'steps: []', '{}', '{}', 'test', ?)",
        (version_id, skill_id, now),
    )
    await conn.commit()
    yield conn, skill_id
    await conn.close()


@pytest.mark.asyncio
async def test_log_correction_calls_scan_for_capability_when_detector_provided(
    seeded_db_with_trusted_skill,
):
    from donna.preferences.correction_logger import log_correction

    conn, skill_id = seeded_db_with_trusted_skill
    db = MagicMock()
    db.connection = conn

    detector = MagicMock()
    detector.scan_for_capability = AsyncMock()

    await log_correction(
        db=db,
        user_id="nick",
        task_id="task-123",
        task_type="test_cap",
        field="priority",
        original="3",
        corrected="5",
        cluster_detector=detector,
    )

    detector.scan_for_capability.assert_called_once_with("test_cap")


@pytest.mark.asyncio
async def test_log_correction_without_detector_is_noop(seeded_db_with_trusted_skill):
    """Default detector=None path — no error raised; no scan fires."""
    from donna.preferences.correction_logger import log_correction

    conn, _ = seeded_db_with_trusted_skill
    db = MagicMock()
    db.connection = conn

    # No cluster_detector kwarg — should complete without error.
    await log_correction(
        db=db, user_id="nick", task_id="task-456", task_type="test_cap",
        field="priority", original="1", corrected="2",
    )

    # Verify the correction was written anyway.
    cursor = await conn.execute(
        "SELECT COUNT(*) FROM correction_log WHERE task_id = ?", ("task-456",),
    )
    assert (await cursor.fetchone())[0] == 1


@pytest.mark.asyncio
async def test_scan_for_capability_flags_when_threshold_exceeded(
    seeded_db_with_trusted_skill,
):
    """CorrectionClusterDetector.scan_for_capability flags the skill when corrections >= threshold."""
    from donna.config import SkillSystemConfig
    from donna.skills.correction_cluster import CorrectionClusterDetector
    from donna.skills.lifecycle import SkillLifecycleManager

    conn, skill_id = seeded_db_with_trusted_skill
    config = SkillSystemConfig(correction_cluster_threshold=2, correction_cluster_window_runs=10)

    # Seed 10 skill_runs and 2 corrections referencing task_type='test_cap'.
    now = datetime.now(timezone.utc).isoformat()
    version_id = str(uuid.uuid4())
    for i in range(10):
        await conn.execute(
            "INSERT INTO skill_run (id, skill_id, skill_version_id, status, "
            "state_object, started_at, finished_at, user_id) "
            "VALUES (?, ?, (SELECT current_version_id FROM skill WHERE id=?), "
            "'succeeded', '{}', ?, ?, 'nick')",
            (str(uuid.uuid4()), skill_id, skill_id, now, now),
        )
    for i in range(2):
        await conn.execute(
            "INSERT INTO correction_log (id, timestamp, user_id, task_type, task_id, "
            "input_text, field_corrected, original_value, corrected_value) "
            "VALUES (?, ?, 'nick', 'test_cap', ?, '', 'priority', '1', '5')",
            (str(uuid.uuid4()), now, f"t{i}"),
        )
    await conn.commit()

    notifier_calls = []

    async def _notifier(msg: str) -> None:
        notifier_calls.append(msg)

    lifecycle = SkillLifecycleManager(conn, config)
    detector = CorrectionClusterDetector(
        connection=conn, lifecycle_manager=lifecycle,
        notifier=_notifier, config=config,
    )
    await detector.scan_for_capability("test_cap")

    cursor = await conn.execute("SELECT state FROM skill WHERE id = ?", (skill_id,))
    assert (await cursor.fetchone())[0] == "flagged_for_review"
    assert len(notifier_calls) == 1
