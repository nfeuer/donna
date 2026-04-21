"""Unit test: SeedCapabilityLoader logs drift when UPSERT changes semantic fields."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
import pytest
import structlog
from structlog.testing import LogCapture

from donna.skills.seed_capabilities import SeedCapabilityLoader


@pytest.fixture()
def _structlog_capture():
    """Snapshot structlog config, install a LogCapture, restore on teardown.

    Prevents global structlog state mutation from leaking between tests and
    causing ordering-dependent failures in the full test suite.
    """
    original = structlog.get_config()
    cap = LogCapture()
    structlog.configure(processors=[cap])
    yield cap
    structlog.configure(**original)


@pytest.mark.asyncio
async def test_drift_log_emitted_on_description_change(
    tmp_path: Path, _structlog_capture: LogCapture
) -> None:
    cap = _structlog_capture
    db_path = tmp_path / "t.db"
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute(
            "CREATE TABLE capability (id TEXT PRIMARY KEY, name TEXT UNIQUE, "
            "description TEXT, input_schema TEXT, trigger_type TEXT, "
            "default_output_shape TEXT, status TEXT, created_at TEXT, "
            "created_by TEXT)"
        )
        now = datetime.now(timezone.utc).isoformat()
        await conn.execute(
            "INSERT INTO capability VALUES (?, 'x', 'old-desc', '{}', "
            "'on_schedule', NULL, 'active', ?, 'seed')",
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
        await loader.load_and_upsert(yaml_path)

        drift_events = [e for e in cap.entries if e["event"] == "seed_capability_drift"]
        assert len(drift_events) == 1
        assert drift_events[0]["capability_name"] == "x"
        assert "description" in drift_events[0]["fields"]


@pytest.mark.asyncio
async def test_no_drift_log_when_unchanged(
    tmp_path: Path, _structlog_capture: LogCapture
) -> None:
    cap = _structlog_capture
    db_path = tmp_path / "t.db"
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute(
            "CREATE TABLE capability (id TEXT PRIMARY KEY, name TEXT UNIQUE, "
            "description TEXT, input_schema TEXT, trigger_type TEXT, "
            "default_output_shape TEXT, status TEXT, created_at TEXT, "
            "created_by TEXT)"
        )
        now = datetime.now(timezone.utc).isoformat()
        await conn.execute(
            "INSERT INTO capability VALUES (?, 'x', 'same', ?, "
            "'on_schedule', NULL, 'active', ?, 'seed')",
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
        await loader.load_and_upsert(yaml_path)

        drift_events = [e for e in cap.entries if e["event"] == "seed_capability_drift"]
        assert len(drift_events) == 0
