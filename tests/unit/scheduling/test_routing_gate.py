"""The routing gate decides route + urgency from facts only — no LLM."""

from datetime import UTC, datetime, timedelta

from donna.scheduling.routing_gate import Route, route
from donna.scheduling.time_intent import TimeIntent

NOW = datetime(2026, 6, 6, 9, 0, tzinfo=UTC)


def test_recurring_routes_to_automation():
    d = route(TimeIntent(kind="recurring", recurrence={"human_readable": "every Wed"}), priority=2, now=NOW)
    assert d.route == Route.AUTOMATION


def test_exact_routes_to_scheduler_now_and_defers_challenger_false():
    d = route(TimeIntent(kind="exact", due_at=NOW + timedelta(days=1), strictness="hard"), priority=2, now=NOW)
    assert d.route == Route.SCHEDULER
    assert d.defer_for_challenger is False


def test_window_and_constrained_route_to_scheduler():
    assert route(TimeIntent(kind="window", latest=NOW + timedelta(days=5)), priority=2, now=NOW).route == Route.SCHEDULER
    assert route(TimeIntent(kind="constrained", latest=NOW + timedelta(days=20), constraints={"weekday": [0]}), priority=2, now=NOW).route == Route.SCHEDULER


def test_none_routes_to_backlog_and_may_defer_challenger():
    d = route(TimeIntent(kind="none"), priority=2, now=NOW)
    assert d.route == Route.BACKLOG
    assert d.defer_for_challenger is True


def test_urgent_when_deadline_near_or_high_priority():
    near = route(TimeIntent(kind="exact", due_at=NOW + timedelta(hours=3)), priority=2, now=NOW)
    high = route(TimeIntent(kind="window", latest=NOW + timedelta(days=10)), priority=5, now=NOW)
    far = route(TimeIntent(kind="window", latest=NOW + timedelta(days=10)), priority=2, now=NOW)
    assert near.urgent is True and high.urgent is True and far.urgent is False
