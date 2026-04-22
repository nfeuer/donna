"""Unit tests for task lifecycle state machine.

Tests pure state transition logic — no external dependencies.
"""

import pytest

from donna.config import (
    InvalidTransitionEntry,
    StateMachineConfig,
    TransitionEntry,
)
from donna.tasks.state_machine import InvalidTransitionError, StateMachine


@pytest.fixture
def config() -> StateMachineConfig:
    """Build a minimal state machine config for testing."""
    return StateMachineConfig(
        states=[
            "backlog", "scheduled", "in_progress", "blocked",
            "waiting_input", "done", "cancelled",
        ],
        initial_state="backlog",
        transitions=[
            TransitionEntry(**{
                "from": "backlog", "to": "scheduled",
                "trigger": "scheduler_assigns_slot",
                "side_effects": ["create_calendar_event"],
            }),
            TransitionEntry(**{
                "from": "scheduled", "to": "in_progress",
                "trigger": "user_starts", "side_effects": ["set_actual_start"],
            }),
            TransitionEntry(**{
                "from": "scheduled", "to": "backlog",
                "trigger": "user_cancels",
                "side_effects": ["delete_calendar_event", "increment_reschedule_count"],
            }),
            TransitionEntry(**{
                "from": "in_progress", "to": "done",
                "trigger": "user_completes", "side_effects": ["set_completed_at"],
            }),
            TransitionEntry(**{
                "from": "in_progress", "to": "blocked",
                "trigger": "blocker_reported", "side_effects": ["log_blocking_reason"],
            }),
            TransitionEntry(**{
                "from": "in_progress", "to": "scheduled",
                "trigger": "user_reschedules",
                "side_effects": ["increment_reschedule_count"],
            }),
            TransitionEntry(**{
                "from": "blocked", "to": "scheduled",
                "trigger": "blocker_resolved",
                "side_effects": ["find_next_available_slot"],
            }),
            TransitionEntry(**{
                "from": "blocked", "to": "cancelled",
                "trigger": "user_abandons", "side_effects": ["flag_dependent_tasks"],
            }),
            TransitionEntry(**{
                "from": "waiting_input", "to": "scheduled",
                "trigger": "info_provided", "side_effects": [],
            }),
            TransitionEntry(**{
                "from": "waiting_input", "to": "cancelled",
                "trigger": "timeout", "side_effects": ["notify_user"],
            }),
            TransitionEntry(**{
                "from": "*", "to": "cancelled",
                "trigger": "user_explicitly_cancels",
                "side_effects": ["flag_dependent_tasks"],
            }),
            TransitionEntry(**{
                "from": "done", "to": "in_progress",
                "trigger": "user_reopens", "side_effects": ["clear_completed_at"],
            }),
            TransitionEntry(**{
                "from": "cancelled", "to": "backlog",
                "trigger": "user_reopens_cancelled",
                "side_effects": ["clear_cancelled_at"],
            }),
        ],
        invalid_transitions=[
            InvalidTransitionEntry(**{
                "from": "backlog", "to": "done",
                "reason": "Cannot complete without scheduling.",
            }),
            InvalidTransitionEntry(**{
                "from": "cancelled", "to": "*", "except": ["backlog"],
                "reason": "Must re-open to backlog first.",
            }),
            InvalidTransitionEntry(**{
                "from": "done", "to": "scheduled",
                "reason": "Must go through in_progress first.",
            }),
        ],
    )


@pytest.fixture
def sm(config: StateMachineConfig) -> StateMachine:
    return StateMachine(config)


class TestValidTransitions:
    def test_backlog_to_scheduled(self, sm: StateMachine) -> None:
        effects = sm.validate_transition("backlog", "scheduled")
        assert "create_calendar_event" in effects

    def test_scheduled_to_in_progress(self, sm: StateMachine) -> None:
        effects = sm.validate_transition("scheduled", "in_progress")
        assert "set_actual_start" in effects

    def test_in_progress_to_done(self, sm: StateMachine) -> None:
        effects = sm.validate_transition("in_progress", "done")
        assert "set_completed_at" in effects

    def test_in_progress_to_blocked(self, sm: StateMachine) -> None:
        effects = sm.validate_transition("in_progress", "blocked")
        assert "log_blocking_reason" in effects

    def test_in_progress_to_scheduled_reschedule(self, sm: StateMachine) -> None:
        effects = sm.validate_transition("in_progress", "scheduled")
        assert "increment_reschedule_count" in effects

    def test_blocked_to_scheduled(self, sm: StateMachine) -> None:
        effects = sm.validate_transition("blocked", "scheduled")
        assert "find_next_available_slot" in effects

    def test_done_to_in_progress_reopen(self, sm: StateMachine) -> None:
        effects = sm.validate_transition("done", "in_progress")
        assert "clear_completed_at" in effects

    def test_wildcard_any_to_cancelled(self, sm: StateMachine) -> None:
        """The '*' wildcard should allow cancellation from any state."""
        for state in ["backlog", "scheduled", "in_progress", "blocked", "waiting_input", "done"]:
            effects = sm.validate_transition(state, "cancelled")
            assert "flag_dependent_tasks" in effects


class TestInvalidTransitions:
    def test_backlog_to_done_rejected(self, sm: StateMachine) -> None:
        with pytest.raises(InvalidTransitionError) as exc_info:
            sm.validate_transition("backlog", "done")
        assert "Cannot complete without scheduling" in str(exc_info.value)

    def test_cancelled_to_scheduled_rejected(self, sm: StateMachine) -> None:
        with pytest.raises(InvalidTransitionError) as exc_info:
            sm.validate_transition("cancelled", "scheduled")
        assert "Must re-open to backlog first" in str(exc_info.value)

    def test_cancelled_to_backlog_allowed(self, sm: StateMachine) -> None:
        """Cancelled → backlog is the `except` carve-out — should be allowed."""
        effects = sm.validate_transition("cancelled", "backlog")
        assert "clear_cancelled_at" in effects

    def test_done_to_scheduled_rejected(self, sm: StateMachine) -> None:
        with pytest.raises(InvalidTransitionError) as exc_info:
            sm.validate_transition("done", "scheduled")
        assert "Must go through in_progress first" in str(exc_info.value)

    def test_unknown_state_rejected(self, sm: StateMachine) -> None:
        with pytest.raises(InvalidTransitionError) as exc_info:
            sm.validate_transition("nonexistent", "done")
        assert "Unknown source state" in str(exc_info.value)

    def test_undefined_transition_rejected(self, sm: StateMachine) -> None:
        with pytest.raises(InvalidTransitionError):
            sm.validate_transition("backlog", "blocked")


class TestGetValidTransitions:
    def test_from_backlog(self, sm: StateMachine) -> None:
        valid = sm.get_valid_transitions("backlog")
        assert "scheduled" in valid
        assert "cancelled" in valid
        assert "done" not in valid

    def test_from_in_progress(self, sm: StateMachine) -> None:
        valid = sm.get_valid_transitions("in_progress")
        assert "done" in valid
        assert "blocked" in valid
        assert "scheduled" in valid
        assert "cancelled" in valid
