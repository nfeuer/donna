"""Shared test fixtures for Donna.

Provides state machine config, state machine, and database fixtures
reusable across unit and integration tests.
"""

from __future__ import annotations

import pytest

from donna.config import (
    InvalidTransitionEntry,
    StateMachineConfig,
    TransitionEntry,
)
from donna.tasks.state_machine import StateMachine


@pytest.fixture
def state_machine_config() -> StateMachineConfig:
    """Build a state machine config matching config/task_states.yaml."""
    return StateMachineConfig(
        states=[
            "backlog",
            "scheduled",
            "in_progress",
            "blocked",
            "waiting_input",
            "done",
            "cancelled",
        ],
        initial_state="backlog",
        transitions=[
            TransitionEntry(
                **{
                    "from": "backlog",
                    "to": "scheduled",
                    "trigger": "scheduler_assigns_slot",
                    "side_effects": [
                        "create_calendar_event",
                        "set_donna_managed_true",
                    ],
                }
            ),
            TransitionEntry(
                **{
                    "from": "scheduled",
                    "to": "in_progress",
                    "trigger": "user_starts",
                    "side_effects": ["set_actual_start"],
                }
            ),
            TransitionEntry(
                **{
                    "from": "scheduled",
                    "to": "backlog",
                    "trigger": "user_cancels",
                    "side_effects": [
                        "delete_calendar_event",
                        "increment_reschedule_count",
                    ],
                }
            ),
            TransitionEntry(
                **{
                    "from": "in_progress",
                    "to": "done",
                    "trigger": "user_completes",
                    "side_effects": [
                        "set_completed_at",
                        "update_velocity_metrics",
                    ],
                }
            ),
            TransitionEntry(
                **{
                    "from": "in_progress",
                    "to": "blocked",
                    "trigger": "blocker_reported",
                    "side_effects": [
                        "update_dependencies",
                        "log_blocking_reason",
                        "notify_dependent_tasks",
                    ],
                }
            ),
            TransitionEntry(
                **{
                    "from": "in_progress",
                    "to": "scheduled",
                    "trigger": "user_reschedules",
                    "side_effects": [
                        "assign_new_slot",
                        "increment_reschedule_count",
                        "update_calendar_event",
                    ],
                }
            ),
            TransitionEntry(
                **{
                    "from": "blocked",
                    "to": "scheduled",
                    "trigger": "blocker_resolved",
                    "side_effects": ["find_next_available_slot"],
                }
            ),
            TransitionEntry(
                **{
                    "from": "blocked",
                    "to": "cancelled",
                    "trigger": "user_abandons",
                    "side_effects": ["flag_dependent_tasks"],
                }
            ),
            TransitionEntry(
                **{
                    "from": "waiting_input",
                    "to": "scheduled",
                    "trigger": "info_provided",
                    "side_effects": [
                        "pm_agent_updates_task",
                        "scheduler_assigns_slot",
                    ],
                }
            ),
            TransitionEntry(
                **{
                    "from": "waiting_input",
                    "to": "cancelled",
                    "trigger": "timeout",
                    "side_effects": ["notify_user", "archive_task"],
                }
            ),
            TransitionEntry(
                **{
                    "from": "*",
                    "to": "cancelled",
                    "trigger": "user_explicitly_cancels",
                    "side_effects": [
                        "flag_dependent_tasks",
                        "delete_calendar_event_if_exists",
                    ],
                }
            ),
            TransitionEntry(
                **{
                    "from": "done",
                    "to": "in_progress",
                    "trigger": "user_reopens",
                    "side_effects": ["clear_completed_at"],
                }
            ),
            TransitionEntry(
                **{
                    "from": "cancelled",
                    "to": "backlog",
                    "trigger": "user_reopens_cancelled",
                    "side_effects": ["clear_cancelled_at"],
                }
            ),
        ],
        invalid_transitions=[
            InvalidTransitionEntry(
                **{
                    "from": "backlog",
                    "to": "done",
                    "reason": "Cannot complete without scheduling.",
                }
            ),
            InvalidTransitionEntry(
                **{
                    "from": "cancelled",
                    "to": "*",
                    "except": ["backlog"],
                    "reason": "Must re-open to backlog first.",
                }
            ),
            InvalidTransitionEntry(
                **{
                    "from": "done",
                    "to": "scheduled",
                    "reason": "Must go through in_progress first.",
                }
            ),
        ],
    )


@pytest.fixture
def state_machine(state_machine_config: StateMachineConfig) -> StateMachine:
    """Build a StateMachine from the shared config."""
    return StateMachine(state_machine_config)
