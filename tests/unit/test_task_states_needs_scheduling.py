"""needs_scheduling is a valid state with the expected transitions."""

from pathlib import Path

import yaml


def _load():
    root = Path(__file__).resolve().parents[2]
    return yaml.safe_load((root / "config" / "task_states.yaml").read_text())


def test_needs_scheduling_is_a_state():
    assert "needs_scheduling" in _load()["states"]


def test_backlog_to_needs_scheduling_and_back_and_to_scheduled():
    transitions = {(t["from"], t["to"]) for t in _load()["transitions"]}
    assert ("backlog", "needs_scheduling") in transitions
    assert ("needs_scheduling", "scheduled") in transitions
    assert ("needs_scheduling", "backlog") in transitions
