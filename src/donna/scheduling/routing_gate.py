"""Deterministic routing + urgency for a freshly captured task.

No LLM: given the extracted TimeIntent and priority, decide where the task goes
and whether it is urgent. This is the gate that closes the strand bug — a
time-bound task is sent to the scheduler immediately and is never deferred for
the Challenger. See the design spec (2026-06-05) §3.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from donna.scheduling.time_intent import TimeIntent, derive_deadline

URGENT_WITHIN = timedelta(hours=24)
URGENT_PRIORITY = 4


class Route(enum.Enum):
    """Where a captured task should go next."""

    SCHEDULER = "scheduler"
    AUTOMATION = "automation"
    BACKLOG = "backlog"


@dataclass(frozen=True, slots=True)
class RouteDecision:
    route: Route
    urgent: bool
    defer_for_challenger: bool


def route(ti: TimeIntent, priority: int, now: datetime | None = None) -> RouteDecision:
    """Decide route + urgency. Time-bound tasks never defer for the Challenger."""
    now = now or datetime.now(tz=UTC)

    if ti.kind == "recurring":
        return RouteDecision(Route.AUTOMATION, urgent=False, defer_for_challenger=False)

    if ti.kind in ("exact", "window", "constrained"):
        deadline = derive_deadline(ti)
        near = deadline is not None and (deadline - now) <= URGENT_WITHIN
        urgent = bool(near or (priority or 0) >= URGENT_PRIORITY)
        return RouteDecision(Route.SCHEDULER, urgent=urgent, defer_for_challenger=False)

    # kind == "none": no time pressure — eligible for the Challenger / backlog.
    return RouteDecision(Route.BACKLOG, urgent=False, defer_for_challenger=True)
