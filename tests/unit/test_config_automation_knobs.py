from pathlib import Path

import pytest

from donna.config import SkillSystemConfig, load_skill_system_config


def test_automation_defaults_on_config():
    cfg = SkillSystemConfig()
    assert cfg.automation_poll_interval_seconds == 60
    assert cfg.automation_min_interval_default_seconds == 300
    assert cfg.automation_failure_pause_threshold == 5
    assert cfg.automation_max_cost_per_run_default_usd == 2.0


def test_load_skills_yaml_allows_automation_overrides(tmp_path: Path):
    yaml_path = tmp_path / "skills.yaml"
    yaml_path.write_text(
        "enabled: true\n"
        "automation_poll_interval_seconds: 30\n"
        "automation_failure_pause_threshold: 10\n"
    )
    cfg = load_skill_system_config(tmp_path)
    assert cfg.automation_poll_interval_seconds == 30
    assert cfg.automation_failure_pause_threshold == 10
    assert cfg.automation_min_interval_default_seconds == 300
    assert cfg.automation_max_cost_per_run_default_usd == 2.0
