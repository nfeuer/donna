"""Integration: paused-state transitions load from config/task_states.yaml."""

from __future__ import annotations

from pathlib import Path

import pytest

from donna.config import load_state_machine_config
from donna.tasks.state_machine import InvalidTransitionError, StateMachine


@pytest.fixture
def loaded_state_machine() -> StateMachine:
    """Build a state machine from the live YAML so the test catches drift."""
    config_dir = Path(__file__).resolve().parents[2] / "config"
    return StateMachine(load_state_machine_config(config_dir))


def test_paused_is_a_known_state(loaded_state_machine: StateMachine) -> None:
    assert "paused" in loaded_state_machine.valid_states


def test_scheduled_to_paused_valid(loaded_state_machine: StateMachine) -> None:
    side_effects = loaded_state_machine.validate_transition("scheduled", "paused")
    assert side_effects == []


def test_in_progress_to_paused_valid(loaded_state_machine: StateMachine) -> None:
    side_effects = loaded_state_machine.validate_transition("in_progress", "paused")
    assert side_effects == []


def test_paused_to_backlog_valid(loaded_state_machine: StateMachine) -> None:
    side_effects = loaded_state_machine.validate_transition("paused", "backlog")
    assert side_effects == []


def test_paused_to_done_invalid(loaded_state_machine: StateMachine) -> None:
    with pytest.raises(InvalidTransitionError):
        loaded_state_machine.validate_transition("paused", "done")


def test_done_to_paused_invalid(loaded_state_machine: StateMachine) -> None:
    with pytest.raises(InvalidTransitionError):
        loaded_state_machine.validate_transition("done", "paused")


def test_paused_to_cancelled_via_wildcard(
    loaded_state_machine: StateMachine,
) -> None:
    """``* → cancelled`` covers paused via the wildcard rule."""
    side_effects = loaded_state_machine.validate_transition("paused", "cancelled")
    assert "flag_dependent_tasks" in side_effects
