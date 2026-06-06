"""Tests for the LLM-free date fallback that keeps dated tasks routable."""

from datetime import UTC, datetime

from donna.scheduling.date_fallback import fallback_time_intent

NOW = datetime(2026, 6, 6, 9, 0, tzinfo=UTC)  # a Saturday


def test_tomorrow_is_exact_next_day():
    ti = fallback_time_intent("send invoices tomorrow", now=NOW)
    assert ti.kind == "exact"
    assert ti.due_at.date() == datetime(2026, 6, 7, tzinfo=UTC).date()


def test_named_weekday_is_exact():
    ti = fallback_time_intent("call the mechanic Monday", now=NOW)
    assert ti.kind == "exact"
    assert ti.due_at.weekday() == 0  # Monday


def test_next_week_is_window():
    ti = fallback_time_intent("do it sometime next week", now=NOW)
    assert ti.kind == "window"
    assert ti.earliest is not None and ti.latest is not None
    assert ti.earliest < ti.latest


def test_end_of_month_is_window_to_month_end():
    ti = fallback_time_intent("finish by the end of the month", now=NOW)
    assert ti.kind == "window"
    assert ti.latest.month == 6 and ti.latest.day == 30


def test_no_date_is_none():
    ti = fallback_time_intent("organize the garage", now=NOW)
    assert ti.kind == "none"
