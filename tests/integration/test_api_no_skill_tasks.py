"""After F-6 Task 14: the API process must not start skill-system background tasks.

The skill-system bundle and nightly cron run in the orchestrator (donna-orchestrator)
process — not the API. We verify this by standing up the API with a config that
*enables* the skill system, then asserting that no background components were
attached to ``app.state``. Prior to Task 14 the API would have wired them, so
these assertions are meaningful.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def api_app(tmp_path, monkeypatch):
    # Copy the real project config into tmp_path and flip skills.enabled=true
    # so that the OLD (pre-Task-14) code path would have populated the
    # skill-system attrs. The NEW code path must leave them absent/None.
    project_root = Path(__file__).resolve().parents[2]
    src_config = project_root / "config"
    dst_config = tmp_path / "config"
    shutil.copytree(src_config, dst_config)

    skills_yaml = dst_config / "skills.yaml"
    text = skills_yaml.read_text()
    text = text.replace("enabled: false", "enabled: true", 1)
    skills_yaml.write_text(text)

    monkeypatch.setenv("DONNA_DB_PATH", str(tmp_path / "donna.db"))
    monkeypatch.setenv("DONNA_CONFIG_DIR", str(dst_config))

    from donna.api import create_app
    app = create_app()
    with TestClient(app) as client:
        yield app, client


def test_api_does_not_wire_skill_cron(api_app) -> None:
    app, _ = api_app
    assert getattr(app.state, "skill_cron_scheduler", None) is None
    assert getattr(app.state, "skill_cron_task", None) is None
    assert getattr(app.state, "auto_drafter", None) is None
    assert getattr(app.state, "skill_lifecycle_manager", None) is None


def test_api_still_loads_skill_system_config(api_app) -> None:
    app, _ = api_app
    assert hasattr(app.state, "skill_system_config")
