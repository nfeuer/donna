"""Confirmation copy states the real slot in Donna's voice."""

from datetime import UTC, datetime

from donna.integrations.confirmation_copy import capture_confirmation
from donna.scheduling.scheduler import ScheduledSlot
from donna.scheduling.time_intent import TimeIntent


def test_placed_exact_includes_day_date_time():
    # 2026-06-05 is a Friday; use it so the day-name assertion is correct.
    slot = ScheduledSlot(
        start=datetime(2026, 6, 5, 14, 0, tzinfo=UTC),
        end=datetime(2026, 6, 5, 14, 30, tzinfo=UTC),
    )
    msg = capture_confirmation(
        title="Send invoices to Kevin", domain="personal", priority=2,
        time_intent=TimeIntent(kind="exact"), slot=slot,
    )
    assert "Send invoices to Kevin" in msg
    assert "Friday" in msg and "Jun 5" in msg and "2:00" in msg


def test_recurring_states_cadence():
    msg = capture_confirmation(
        title="Standup", domain="work", priority=2,
        time_intent=TimeIntent(
            kind="recurring", recurrence={"human_readable": "every Wednesday at 9:00 AM"}
        ),
        slot=None,
    )
    assert "every Wednesday at 9:00 AM" in msg


def test_no_time_says_backlog():
    msg = capture_confirmation(
        title="Organize the garage", domain="personal", priority=1,
        time_intent=TimeIntent(kind="none"), slot=None,
    )
    assert "backlog" in msg.lower()


def test_no_slot_offers_to_rearrange():
    msg = capture_confirmation(
        title="Invoices", domain="personal", priority=3,
        time_intent=TimeIntent(kind="exact"), slot=None, no_slot=True,
    )
    assert "move something" in msg.lower() or "rearrange" in msg.lower()


def test_dated_task_without_slot_never_reports_no_deadline():
    # Defensive: a time-bound task with no slot must not fall to the backlog copy.
    msg = capture_confirmation(
        title="Invoices", domain="personal", priority=3,
        time_intent=TimeIntent(kind="exact"), slot=None, no_slot=False,
    )
    assert "no deadline" not in msg.lower()
    assert "couldn't find a slot" in msg.lower()
