"""Tests for Wave 1 validation-timeout + automation-poll-interval config knobs.

See docs/superpowers/plans/archive/2026-04-16-skill-system-wave-1-production-enablement.md
Task 9. Covers the three config assertions for Wave 1's new fields in
SkillSystemConfig.
"""

from __future__ import annotations

from donna.config import SkillSystemConfig


def test_validation_timeouts_have_defaults() -> None:
    cfg = SkillSystemConfig()
    assert cfg.validation_per_step_timeout_s == 60
    assert cfg.validation_per_run_timeout_s == 300


def test_automation_poll_interval_default_is_15_seconds() -> None:
    # Wave 1: reduced from 60 to 15 for responsive run-now.
    cfg = SkillSystemConfig()
    assert cfg.automation_poll_interval_seconds == 15


def test_validation_timeouts_override_from_dict() -> None:
    cfg = SkillSystemConfig(
        validation_per_step_timeout_s=30,
        validation_per_run_timeout_s=120,
    )
    assert cfg.validation_per_step_timeout_s == 30
    assert cfg.validation_per_run_timeout_s == 120
