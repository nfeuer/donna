"""Tests for POST /admin/automations/{id}/run-now after F-6 (Task 16).

After Task 14 moved the AutomationDispatcher out of the API process, the
``run-now`` endpoint is a pure DB-update operation: it sets
``automation.next_run_at = now()`` so the orchestrator-side
AutomationScheduler picks it up on its next poll tick. The endpoint returns
202 Accepted — it does not wait for the run to complete and does not
depend on any in-process dispatcher.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _seed_capability(db_path: Path) -> None:
    """Insert a ``parse_task`` capability row so create_automation's
    existence check succeeds.

    Opens a fresh synchronous sqlite3 connection against the same DB file
    the API process is using. Safe with WAL mode because SQLite supports
    concurrent readers/writers across connections.
    """
    import sqlite3
    import uuid6

    conn = sqlite3.connect(str(db_path))
    try:
        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            "SELECT 1 FROM capability WHERE name = ?", ("parse_task",)
        )
        if cur.fetchone() is not None:
            return
        conn.execute(
            "INSERT INTO capability "
            "(id, name, description, input_schema, trigger_type, "
            "default_output_shape, status, embedding, created_at, "
            "created_by, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid6.uuid7()),
                "parse_task",
                "seed capability for run-now tests",
                json.dumps({}),
                "on_schedule",
                None,
                "active",
                None,
                now,
                "test-seed",
                None,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _run_migrations(db_path: Path) -> None:
    """Run alembic upgrade head against ``db_path`` to create the schema."""
    from alembic import command
    from alembic.config import Config

    project_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")


@pytest.fixture
def client(tmp_path, monkeypatch):
    project_root = Path(__file__).resolve().parents[2]
    src_config = project_root / "config"
    dst_config = tmp_path / "config"
    shutil.copytree(src_config, dst_config)

    db_path = tmp_path / "donna.db"
    _run_migrations(db_path)
    _seed_capability(db_path)

    monkeypatch.setenv("DONNA_DB_PATH", str(db_path))
    monkeypatch.setenv("DONNA_CONFIG_DIR", str(dst_config))

    from donna.api import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c


def _create_automation(client: TestClient, **overrides) -> str:
    body = {
        "user_id": "nick",
        "name": "test",
        "capability_name": "parse_task",
        "inputs": {},
        "trigger_type": "on_schedule",
        "schedule": "0 * * * *",
        "alert_conditions": {},
        "alert_channels": ["tasks"],
        "min_interval_seconds": 60,
    }
    body.update(overrides)
    resp = client.post("/admin/automations", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_run_now_returns_202_and_sets_next_run_at(client) -> None:
    automation_id = _create_automation(client)

    resp = client.post(f"/admin/automations/{automation_id}/run-now")
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "scheduled"
    assert "next_run_at" in body
    assert body["next_run_at"] is not None

    detail = client.get(f"/admin/automations/{automation_id}")
    assert detail.status_code == 200
    assert detail.json()["next_run_at"] is not None


def test_run_now_404_on_missing_automation(client) -> None:
    resp = client.post("/admin/automations/nonexistent/run-now")
    assert resp.status_code == 404


def test_run_now_404_on_paused_automation(client) -> None:
    automation_id = _create_automation(client, name="paused-auto")
    pause_resp = client.post(f"/admin/automations/{automation_id}/pause")
    assert pause_resp.status_code == 200

    resp = client.post(f"/admin/automations/{automation_id}/run-now")
    assert resp.status_code == 404
