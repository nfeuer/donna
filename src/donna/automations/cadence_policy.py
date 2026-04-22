"""CadencePolicy — maps a skill's lifecycle state to its minimum polling interval.

Loaded from ``config/automations.yaml``. Supports per-capability overrides.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Any

import yaml


class PausedState(Exception):  # noqa: N818
    """Raised by ``min_interval_for`` when the lifecycle state is paused.

    Named without the ``Error`` suffix because it represents a lifecycle
    *state* signal used as control flow (like ``StopIteration``), not an
    error condition. Callers catch it to branch, not to report failure.
    """


def load_discord_automation_default_min_interval_seconds(
    path: pathlib.Path,
) -> int:
    """Read ``discord_automation_default_min_interval_seconds`` from config.

    Used by AutomationCreationPath when persisting automations created
    through the Discord NL path. Defaults to 300 when the key is absent
    so older configs keep working.
    """
    data = yaml.safe_load(path.read_text()) or {}
    return int(data.get("discord_automation_default_min_interval_seconds", 300))


@dataclass(slots=True)
class CadencePolicy:
    intervals: dict[str, int]
    paused_states: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, path: pathlib.Path) -> CadencePolicy:
        data = yaml.safe_load(path.read_text()) or {}
        table = data.get("cadence_policy", {})
        intervals: dict[str, int] = {}
        paused: set[str] = set()
        for state, cfg in table.items():
            if cfg.get("pause"):
                paused.add(state)
                continue
            intervals[state] = int(cfg["min_interval_seconds"])
        return cls(intervals=intervals, paused_states=paused)

    def min_interval_for(
        self,
        state: str,
        *,
        override: dict[str, Any] | None = None,
    ) -> int:
        """Return the minimum interval (seconds) allowed for a lifecycle state.

        Resolution order: paused states → known-state check → override → base table.
        Raises ``PausedState`` for paused states, ``KeyError`` for unknown states.
        """
        # F-W3-A: validity of the state must be checked BEFORE any
        # override lookup. If an override supplies a bogus state name,
        # returning its min_interval_seconds silently would mask typos
        # and let unknown lifecycle states flow through the scheduler.
        if state in self.paused_states:
            raise PausedState(state)
        if state not in self.intervals:
            raise KeyError(f"unknown lifecycle state: {state}")
        if override and state in override:
            return int(override[state]["min_interval_seconds"])
        return self.intervals[state]

    def is_paused(self, state: str) -> bool:
        """True if automations pointing at this state should halt scheduling."""
        return state in self.paused_states
