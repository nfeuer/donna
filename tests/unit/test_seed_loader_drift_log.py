"""Unit test: SeedCapabilityLoader logs drift when UPSERT changes semantic fields."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest
import structlog
from structlog.testing import capture_logs

import donna.skills.seed_capabilities as _seed_mod
from donna.skills.seed_capabilities import SeedCapabilityLoader


@pytest.fixture(autouse=True)
def _reset_seed_capabilities_logger():
    """Force the module-level logger in seed_capabilities to re-bind before each
    test and restore it afterwards.

    Root cause: setup_logging() (called by integration tests that boot the
    full orchestrator) configures structlog with cache_logger_on_first_use=True
    and creates a NEW processor-list object on each call.  Once the module's
    logger has been used it is cached against that old list object.  Subsequent
    calls to structlog.configure() — including the in-place mutation done by
    structlog.testing.capture_logs() — target the *current* list, not the
    cached one, so captured entries are empty.

    Replacing the module-level proxy with a fresh structlog.get_logger() call
    ensures the test's capture_logs() context manager intercepts every emit.
    """
    original_logger = _seed_mod.logger
    _seed_mod.logger = structlog.get_logger()
    yield
    _seed_mod.logger = original_logger


@pytest.mark.asyncio
async def test_drift_log_emitted_on_description_change(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "t.db"
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute(
            "CREATE TABLE capability (id TEXT PRIMARY KEY, name TEXT UNIQUE, "
            "description TEXT, input_schema TEXT, trigger_type TEXT, "
            "default_output_shape TEXT, tools_json TEXT, status TEXT, "
            "created_at TEXT, created_by TEXT)"
        )
        now = datetime.now(UTC).isoformat()
        await conn.execute(
            "INSERT INTO capability VALUES (?, 'x', 'old-desc', '{}', "
            "'on_schedule', NULL, NULL, 'active', ?, 'seed')",
            (str(uuid.uuid4()), now),
        )
        await conn.commit()

        yaml_path = tmp_path / "capabilities.yaml"
        yaml_path.write_text(
            "capabilities:\n"
            "  - name: x\n"
            "    description: 'new-desc'\n"
            "    trigger_type: on_schedule\n"
            "    input_schema: {type: object}\n"
        )

        loader = SeedCapabilityLoader(connection=conn)
        with capture_logs() as cap:
            await loader.load_and_upsert(yaml_path)

        drift_events = [e for e in cap if e["event"] == "seed_capability_drift"]
        assert len(drift_events) == 1
        assert drift_events[0]["capability_name"] == "x"
        assert "description" in drift_events[0]["fields"]


@pytest.mark.asyncio
async def test_no_drift_log_when_unchanged(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "t.db"
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute(
            "CREATE TABLE capability (id TEXT PRIMARY KEY, name TEXT UNIQUE, "
            "description TEXT, input_schema TEXT, trigger_type TEXT, "
            "default_output_shape TEXT, tools_json TEXT, status TEXT, "
            "created_at TEXT, created_by TEXT)"
        )
        now = datetime.now(UTC).isoformat()
        await conn.execute(
            "INSERT INTO capability VALUES (?, 'x', 'same', ?, "
            "'on_schedule', NULL, NULL, 'active', ?, 'seed')",
            (str(uuid.uuid4()), json.dumps({"type": "object"}), now),
        )
        await conn.commit()

        yaml_path = tmp_path / "capabilities.yaml"
        yaml_path.write_text(
            "capabilities:\n"
            "  - name: x\n"
            "    description: 'same'\n"
            "    trigger_type: on_schedule\n"
            "    input_schema: {type: object}\n"
        )

        loader = SeedCapabilityLoader(connection=conn)
        with capture_logs() as cap:
            await loader.load_and_upsert(yaml_path)

        drift_events = [e for e in cap if e["event"] == "seed_capability_drift"]
        assert len(drift_events) == 0
