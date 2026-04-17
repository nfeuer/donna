"""Wave 3 extensions to ChallengerMatchResult shape."""
from __future__ import annotations

from datetime import datetime, timezone

from donna.agents.challenger_agent import ChallengerMatchResult


def test_result_has_intent_kind_field() -> None:
    r = ChallengerMatchResult(status="ready", intent_kind="automation")
    assert r.intent_kind == "automation"


def test_result_defaults() -> None:
    r = ChallengerMatchResult(status="ready")
    assert r.intent_kind == "task"
    assert r.schedule is None
    assert r.deadline is None
    assert r.alert_conditions is None
    assert r.confidence == 0.0
    assert r.low_quality_signals == []


def test_result_with_automation_fields() -> None:
    r = ChallengerMatchResult(
        status="ready",
        intent_kind="automation",
        schedule={"cron": "0 12 * * *", "human_readable": "daily at noon"},
        alert_conditions={"expression": "price < 100", "channels": ["discord_dm"]},
        confidence=0.92,
        low_quality_signals=[],
    )
    assert r.schedule["cron"] == "0 12 * * *"
    assert r.alert_conditions["expression"] == "price < 100"


def test_result_with_task_fields() -> None:
    deadline = datetime(2026, 4, 24, tzinfo=timezone.utc)
    r = ChallengerMatchResult(status="ready", intent_kind="task", deadline=deadline)
    assert r.deadline == deadline
