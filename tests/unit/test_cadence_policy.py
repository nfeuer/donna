"""CadencePolicy config loader + min-interval lookup."""
from __future__ import annotations

import pathlib
import textwrap

import pytest

from donna.automations.cadence_policy import CadencePolicy, PausedState


def test_loads_from_yaml(tmp_path: pathlib.Path) -> None:
    cfg = tmp_path / "automations.yaml"
    cfg.write_text(textwrap.dedent("""\
    cadence_policy:
      claude_native: {min_interval_seconds: 43200}
      sandbox: {min_interval_seconds: 43200}
      shadow_primary: {min_interval_seconds: 3600}
      trusted: {min_interval_seconds: 900}
      degraded: {min_interval_seconds: 43200}
      flagged_for_review: {pause: true}
    """))

    policy = CadencePolicy.load(cfg)
    assert policy.min_interval_for("trusted") == 900
    assert policy.min_interval_for("claude_native") == 43200


def test_flagged_for_review_is_paused() -> None:
    policy = CadencePolicy(
        intervals={"claude_native": 43200, "trusted": 900},
        paused_states={"flagged_for_review"},
    )
    with pytest.raises(PausedState):
        policy.min_interval_for("flagged_for_review")


def test_unknown_state_raises() -> None:
    policy = CadencePolicy(intervals={"trusted": 900}, paused_states=set())
    with pytest.raises(KeyError, match="unknown"):
        policy.min_interval_for("unknown")


def test_per_capability_override() -> None:
    policy = CadencePolicy(
        intervals={"trusted": 900},
        paused_states=set(),
    )
    override = {"trusted": {"min_interval_seconds": 60}}
    assert policy.min_interval_for("trusted", override=override) == 60


def test_cadence_policy_loads_real_config() -> None:
    """Regression test — the real config/automations.yaml must load cleanly."""
    policy = CadencePolicy.load(pathlib.Path("config/automations.yaml"))
    # Spot-check the lifecycle states the Wave 3 spec requires.
    for state in ("claude_native", "sandbox", "shadow_primary", "trusted", "degraded"):
        assert policy.min_interval_for(state) > 0
    assert policy.is_paused("flagged_for_review")


def test_unknown_state_with_override_still_raises() -> None:
    """F-W3-A: An override keyed by a bogus state must not mask validation.

    Before the fix the override lookup ran before the state-validity
    check, so a typo'd override key would silently return its value and
    let an unknown lifecycle state reach the scheduler.
    """
    policy = CadencePolicy(intervals={"trusted": 900}, paused_states=set())
    override = {"bogus_state": {"min_interval_seconds": 60}}
    with pytest.raises(KeyError, match="unknown"):
        policy.min_interval_for("bogus_state", override=override)
