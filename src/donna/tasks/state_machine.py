"""Task lifecycle state machine.

Config-driven from config/task_states.yaml. Loaded at startup.
Rejects invalid transitions. See docs/task-system.md.
"""

from __future__ import annotations

import structlog

from donna.config import StateMachineConfig

logger = structlog.get_logger()


class InvalidTransitionError(Exception):
    """Raised when a state transition is not allowed."""

    def __init__(self, from_state: str, to_state: str, reason: str = ""):
        self.from_state = from_state
        self.to_state = to_state
        self.reason = reason
        msg = f"Invalid transition: {from_state} → {to_state}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)


class StateMachine:
    """Validates and executes task state transitions.

    Usage:
        sm = StateMachine(config)
        side_effects = sm.validate_transition("backlog", "scheduled")
        # Execute side effects...
    """

    def __init__(self, config: StateMachineConfig):
        self.config = config
        self.valid_states = set(config.states)

        # Build lookup: (from, to) -> TransitionEntry
        self._transitions: dict[tuple[str, str], list[str]] = {}
        for t in config.transitions:
            from_key = t.from_state
            # Handle wildcard "*" for "any state"
            if from_key == "*":
                for state in config.states:
                    self._transitions[(state, t.to_state)] = t.side_effects
            else:
                self._transitions[(from_key, t.to_state)] = t.side_effects

        # Build invalid transition lookup for clear error messages
        self._invalid: dict[tuple[str, str], str] = {}
        for inv in config.invalid_transitions:
            from_key = inv.from_state
            to_key = inv.to_state
            if to_key == "*":
                for state in config.states:
                    if state not in inv.except_states:
                        self._invalid[(from_key, state)] = inv.reason
            else:
                self._invalid[(from_key, to_key)] = inv.reason

    def validate_transition(self, from_state: str, to_state: str) -> list[str]:
        """Validate a state transition and return its side effects.

        Raises InvalidTransitionError if the transition is not allowed.
        Returns list of side effect names to execute.
        """
        if from_state not in self.valid_states:
            raise InvalidTransitionError(from_state, to_state, "Unknown source state")
        if to_state not in self.valid_states:
            raise InvalidTransitionError(from_state, to_state, "Unknown target state")

        # Check explicitly invalid transitions first (better error messages)
        invalid_reason = self._invalid.get((from_state, to_state))
        if invalid_reason:
            raise InvalidTransitionError(from_state, to_state, invalid_reason)

        # Check valid transitions
        side_effects = self._transitions.get((from_state, to_state))
        if side_effects is None:
            raise InvalidTransitionError(
                from_state, to_state, "No valid transition defined"
            )

        logger.info(
            "state_transition_validated",
            from_state=from_state,
            to_state=to_state,
            side_effects=side_effects,
        )

        return side_effects

    def get_valid_transitions(self, from_state: str) -> list[str]:
        """Return all valid target states from the given state."""
        return [
            to_state
            for (from_s, to_state) in self._transitions
            if from_s == from_state
        ]
