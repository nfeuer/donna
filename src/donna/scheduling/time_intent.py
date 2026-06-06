"""TimeIntent — the structured representation of *when* a task should happen.

Captures the five temporal kinds Donna recognizes (exact, window, constrained,
recurring, none) and derives the legacy ``deadline`` / ``deadline_type`` values
so existing consumers (reminders, overdue detector, weekly planner) keep working
unchanged. See docs/superpowers/specs/2026-06-05-challenger-and-scheduling-intake-design.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

Kind = Literal["exact", "window", "constrained", "recurring", "none"]
Strictness = Literal["hard", "soft"]


def _parse_dt(value: Any) -> datetime | None:
    """Parse an ISO-8601 string (or passthrough datetime) to datetime, else None."""
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class TimeIntent:
    """Normalized temporal intent extracted from a task.

    Args:
        kind: One of exact | window | constrained | recurring | none.
        due_at: Concrete deadline for ``exact``.
        earliest: Lower bound for ``window`` / ``constrained``.
        latest: Upper bound for ``window`` / ``constrained``.
        strictness: hard | soft. Ignored when kind == none/recurring.
        constraints: e.g. {"weekday": [0], "time_of_day": "morning"} for ``constrained``.
        recurrence: e.g. {"rrule_or_cron": "0 9 * * 3", "human_readable": "every Wednesday 9am"}.
    """

    kind: Kind = "none"
    due_at: datetime | None = None
    earliest: datetime | None = None
    latest: datetime | None = None
    strictness: Strictness = "soft"
    constraints: dict[str, Any] | None = None
    recurrence: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TimeIntent:
        """Build a TimeIntent from a loosely-typed dict (e.g. LLM JSON)."""
        return cls(
            kind=data.get("kind", "none"),
            due_at=_parse_dt(data.get("due_at")),
            earliest=_parse_dt(data.get("earliest")),
            latest=_parse_dt(data.get("latest")),
            strictness=data.get("strictness", "soft"),
            constraints=data.get("constraints"),
            recurrence=data.get("recurrence"),
        )

    @classmethod
    def from_json(cls, raw: str | None) -> TimeIntent:
        """Deserialize from the JSON string stored on the task row."""
        if not raw:
            return cls(kind="none")
        return cls.from_dict(json.loads(raw))

    def to_json(self) -> str:
        """Serialize to a JSON string for the ``time_intent_json`` column."""
        out: dict[str, Any] = {"kind": self.kind, "strictness": self.strictness}
        for name in ("due_at", "earliest", "latest"):
            value = getattr(self, name)
            if value is not None:
                out[name] = value.isoformat()
        if self.constraints is not None:
            out["constraints"] = self.constraints
        if self.recurrence is not None:
            out["recurrence"] = self.recurrence
        return json.dumps(out)


def derive_deadline(ti: TimeIntent) -> datetime | None:
    """Back-compat deadline: due_at (exact) or latest (window/constrained); else None."""
    if ti.kind == "exact":
        return ti.due_at
    if ti.kind in ("window", "constrained"):
        return ti.latest
    return None


def derive_deadline_type(ti: TimeIntent) -> str:
    """Back-compat deadline_type: strictness for time-bound kinds, else 'none'."""
    if ti.kind in ("exact", "window", "constrained"):
        return ti.strictness
    return "none"
