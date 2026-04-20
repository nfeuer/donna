"""Unit test for SkillSystemConfig.baseline_reset_window."""
from __future__ import annotations

from donna.config import SkillSystemConfig


def test_default_value_is_100() -> None:
    cfg = SkillSystemConfig()
    assert cfg.baseline_reset_window == 100


def test_can_be_overridden() -> None:
    cfg = SkillSystemConfig(baseline_reset_window=50)
    assert cfg.baseline_reset_window == 50
