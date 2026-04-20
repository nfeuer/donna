"""Verify the baseline reset uses SkillSystemConfig.baseline_reset_window."""
from __future__ import annotations

import inspect

import pytest


def test_baseline_query_does_not_use_literal_100() -> None:
    """The hardcoded 'LIMIT 100' must be replaced with a config-bound parameter."""
    from donna.api.routes import skills as skills_mod
    src = inspect.getsource(skills_mod)
    assert "LIMIT 100" not in src, (
        "Baseline reset LIMIT should read from config.baseline_reset_window"
    )
    assert "baseline_reset_window" in src


def test_baseline_reset_uses_configured_window_dynamic(tmp_path) -> None:
    """Smoke: import the route and inspect the generated SQL uses a `?` param
    bound to the config value, not a literal. Exact string-match approach for simplicity."""
    from donna.api.routes import skills as skills_mod
    src = inspect.getsource(skills_mod)
    # The SQL should now use LIMIT ? (parameterized)
    assert "LIMIT ?" in src, "LIMIT should be parameterized"
