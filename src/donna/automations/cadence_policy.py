"""CadencePolicy — maps a skill's lifecycle state to its minimum polling interval.

Loaded from ``config/automations.yaml``. Supports per-capability overrides.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Any

import yaml


class PausedState(Exception):
    """Raised by ``min_interval_for`` when the lifecycle state is paused."""


@dataclass(slots=True)
class CadencePolicy:
    intervals: dict[str, int]
    paused_states: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, path: pathlib.Path) -> "CadencePolicy":
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
        if state in self.paused_states:
            raise PausedState(state)
        if override and state in override:
            return int(override[state]["min_interval_seconds"])
        if state not in self.intervals:
            raise KeyError(f"unknown lifecycle state: {state}")
        return self.intervals[state]

    def is_paused(self, state: str) -> bool:
        return state in self.paused_states
