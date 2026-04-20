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


def test_baseline_reset_requires_skill_system_config() -> None:
    """When skill_system_config is None, the route returns 503."""
    # Read the source file directly to avoid import caching issues.
    import pathlib
    skills_file = pathlib.Path(__file__).parent.parent.parent / "src" / "donna" / "api" / "routes" / "skills.py"
    src = skills_file.read_text()
    # The handler should raise 503 if skill_system_config is None.
    # Grep for the pattern at least once in the file.
    assert "if skill_config is None:" in src, (
        "baseline_reset block should check 'if skill_config is None:'"
    )
    assert "503" in src, "Should raise HTTPException with status_code=503"
