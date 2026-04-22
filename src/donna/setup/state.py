"""Setup state persistence across wizard restarts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STATE_VERSION = 1
DEFAULT_STATE_PATH = "docker/.setup-state.json"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def empty_state(phase: int = 1) -> dict[str, Any]:
    """Return a fresh state dict."""
    return {
        "version": STATE_VERSION,
        "phase": phase,
        "completed_steps": [],
        "skipped_steps": [],
        "started_at": _now_iso(),
        "last_step_at": _now_iso(),
    }


def load_state(path: Path) -> dict[str, Any] | None:
    """Load state from disk. Returns ``None`` if no state file exists."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
        if data.get("version") != STATE_VERSION:
            return None
        return data
    except (json.JSONDecodeError, KeyError):
        return None


def save_state(state: dict[str, Any], path: Path) -> None:
    """Atomically save state to disk."""
    state["last_step_at"] = _now_iso()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    tmp.replace(path)


def mark_completed(state: dict[str, Any], step_id: str) -> None:
    """Mark a step as completed (removes from skipped if it was there)."""
    if step_id not in state["completed_steps"]:
        state["completed_steps"].append(step_id)
    if step_id in state["skipped_steps"]:
        state["skipped_steps"].remove(step_id)


def mark_skipped(state: dict[str, Any], step_id: str) -> None:
    """Mark a step as skipped."""
    if step_id not in state["skipped_steps"]:
        state["skipped_steps"].append(step_id)
    if step_id in state["completed_steps"]:
        state["completed_steps"].remove(step_id)


def is_step_done(state: dict[str, Any], step_id: str) -> bool:
    """Check if a step is completed or skipped."""
    return step_id in state["completed_steps"] or step_id in state["skipped_steps"]
