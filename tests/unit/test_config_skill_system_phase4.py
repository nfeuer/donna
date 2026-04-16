"""Unit tests for Phase 4 skill system configuration (evolution loop + correction clustering)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from donna.config import SkillSystemConfig, load_skill_system_config


def _write_skills_yaml(tmp_path: Path, data: dict) -> None:
    """Helper to write a skills.yaml file."""
    with open(tmp_path / "skills.yaml", "w") as f:
        yaml.safe_dump(data, f)


# ── Phase 4 defaults on SkillSystemConfig ───────────────────────────────────


def test_phase4_defaults_on_config() -> None:
    """Verify Phase 4 fields have correct default values."""
    cfg = SkillSystemConfig()

    # Evolution loop
    assert cfg.evolution_min_divergence_cases == 15
    assert cfg.evolution_max_divergence_cases == 30
    assert cfg.evolution_targeted_case_pass_rate == 0.80
    assert cfg.evolution_fixture_regression_pass_rate == 0.95
    assert cfg.evolution_recent_success_count == 20
    assert cfg.evolution_recent_success_window_days == 30
    assert cfg.evolution_max_consecutive_failures == 2
    assert cfg.evolution_estimated_cost_usd == 0.75
    assert cfg.evolution_daily_cap == 10

    # Correction clustering
    assert cfg.correction_cluster_window_runs == 10
    assert cfg.correction_cluster_threshold == 2


# ── Loading Phase 4 knobs from YAML ──────────────────────────────────────────


def test_load_skills_yaml_includes_phase4_evolution_keys(tmp_path: Path) -> None:
    """Test loading Phase 4 evolution knobs from YAML."""
    _write_skills_yaml(tmp_path, {
        "enabled": True,
        "evolution_min_divergence_cases": 20,
        "evolution_max_divergence_cases": 40,
        "evolution_targeted_case_pass_rate": 0.85,
        "evolution_fixture_regression_pass_rate": 0.92,
        "evolution_recent_success_count": 25,
        "evolution_recent_success_window_days": 45,
        "evolution_max_consecutive_failures": 3,
        "evolution_estimated_cost_usd": 1.00,
        "evolution_daily_cap": 5,
    })

    cfg = load_skill_system_config(tmp_path)

    assert cfg.enabled is True
    assert cfg.evolution_min_divergence_cases == 20
    assert cfg.evolution_max_divergence_cases == 40
    assert cfg.evolution_targeted_case_pass_rate == 0.85
    assert cfg.evolution_fixture_regression_pass_rate == 0.92
    assert cfg.evolution_recent_success_count == 25
    assert cfg.evolution_recent_success_window_days == 45
    assert cfg.evolution_max_consecutive_failures == 3
    assert cfg.evolution_estimated_cost_usd == 1.00
    assert cfg.evolution_daily_cap == 5


def test_load_skills_yaml_includes_phase4_clustering_keys(tmp_path: Path) -> None:
    """Test loading Phase 4 correction clustering knobs from YAML."""
    _write_skills_yaml(tmp_path, {
        "correction_cluster_window_runs": 20,
        "correction_cluster_threshold": 5,
    })

    cfg = load_skill_system_config(tmp_path)

    assert cfg.correction_cluster_window_runs == 20
    assert cfg.correction_cluster_threshold == 5
    # Defaults for unspecified fields
    assert cfg.evolution_min_divergence_cases == 15
    assert cfg.evolution_daily_cap == 10


def test_load_skills_yaml_partial_phase4(tmp_path: Path) -> None:
    """Test partial Phase 4 config loading with defaults for unspecified keys."""
    _write_skills_yaml(tmp_path, {
        "enabled": True,
        "evolution_daily_cap": 3,
        "correction_cluster_threshold": 5,
    })

    cfg = load_skill_system_config(tmp_path)

    assert cfg.enabled is True
    assert cfg.evolution_daily_cap == 3
    assert cfg.correction_cluster_threshold == 5

    # Defaults for unspecified fields
    assert cfg.evolution_min_divergence_cases == 15
    assert cfg.evolution_max_divergence_cases == 30
    assert cfg.evolution_targeted_case_pass_rate == 0.80
    assert cfg.evolution_fixture_regression_pass_rate == 0.95
    assert cfg.correction_cluster_window_runs == 10


def test_load_skills_yaml_missing_file_includes_phase4_defaults(tmp_path: Path) -> None:
    """Test that missing skills.yaml still includes Phase 4 defaults."""
    cfg = load_skill_system_config(tmp_path)

    # Verify all Phase 4 defaults are present
    assert cfg.evolution_min_divergence_cases == 15
    assert cfg.evolution_max_divergence_cases == 30
    assert cfg.evolution_targeted_case_pass_rate == 0.80
    assert cfg.evolution_fixture_regression_pass_rate == 0.95
    assert cfg.evolution_recent_success_count == 20
    assert cfg.evolution_recent_success_window_days == 30
    assert cfg.evolution_max_consecutive_failures == 2
    assert cfg.evolution_estimated_cost_usd == 0.75
    assert cfg.evolution_daily_cap == 10
    assert cfg.correction_cluster_window_runs == 10
    assert cfg.correction_cluster_threshold == 2
