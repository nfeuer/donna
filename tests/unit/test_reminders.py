"""Unit tests for ReminderScheduler.

All timing is controlled via fixed `now` values. No real asyncio loops,
Discord connections, or database access.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from donna.notifications.reminders import REMINDER_LEAD_MINUTES, ReminderScheduler

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(hour: int, minute: int = 0, day: int = 20) -> datetime:
    return datetime(2026, 3, day, hour, minute, tzinfo=UTC)


def _task(
    task_id: str = "t1",
    title: str = "Do the thing",
    scheduled_start: str | None = None,
    estimated_duration: int | None = 30,
    priority: int = 2,
    status: str = "scheduled",
) -> MagicMock:
    t = MagicMock()
    t.id = task_id
    t.title = title
    t.scheduled_start = scheduled_start
    t.estimated_duration = estimated_duration
    t.priority = priority
    t.status = status
    return t


def _make_scheduler() -> tuple[ReminderScheduler, AsyncMock, AsyncMock]:
    db = AsyncMock()
    service = AsyncMock()
    service.dispatch = AsyncMock(return_value=True)
    service._tw = MagicMock()
    service._tw.blackout.end_hour = 6
    sched = ReminderScheduler(db=db, service=service, user_id="u1")
    return sched, db, service


# ---------------------------------------------------------------------------
# Core trigger logic
# ---------------------------------------------------------------------------


class TestReminderTrigger:
    async def test_task_starting_in_14_min_triggers_reminder(self) -> None:
        sched, db, service = _make_scheduler()
        now = _utc(9, 0)
        start = now + timedelta(minutes=14)
        db.list_tasks = AsyncMock(return_value=[_task(scheduled_start=start.isoformat())])

        await sched._check_and_send(now)

        service.dispatch.assert_called_once()
        call_kwargs = service.dispatch.call_args[1]
        assert "15 minutes" in call_kwargs["content"]

    async def test_task_starting_in_16_min_does_not_trigger(self) -> None:
        sched, db, service = _make_scheduler()
        now = _utc(9, 0)
        start = now + timedelta(minutes=16)
        db.list_tasks = AsyncMock(return_value=[_task(scheduled_start=start.isoformat())])

        await sched._check_and_send(now)

        service.dispatch.assert_not_called()

    async def test_task_in_past_does_not_trigger(self) -> None:
        sched, db, service = _make_scheduler()
        now = _utc(9, 0)
        start = now - timedelta(minutes=5)
        db.list_tasks = AsyncMock(return_value=[_task(scheduled_start=start.isoformat())])

        await sched._check_and_send(now)

        service.dispatch.assert_not_called()

    async def test_task_exactly_at_boundary_triggers(self) -> None:
        """A task starting exactly REMINDER_LEAD_MINUTES from now should trigger."""
        sched, db, service = _make_scheduler()
        now = _utc(9, 0)
        start = now + timedelta(minutes=REMINDER_LEAD_MINUTES)
        db.list_tasks = AsyncMock(return_value=[_task(scheduled_start=start.isoformat())])

        await sched._check_and_send(now)

        service.dispatch.assert_called_once()


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestReminderDedup:
    async def test_reminder_not_sent_twice_same_day(self) -> None:
        sched, db, service = _make_scheduler()
        now = _utc(9, 0)
        start = now + timedelta(minutes=14)
        task = _task(scheduled_start=start.isoformat())
        db.list_tasks = AsyncMock(return_value=[task])

        await sched._check_and_send(now)
        await sched._check_and_send(now)

        assert service.dispatch.call_count == 1

    async def test_reminder_resets_next_day(self) -> None:
        sched, db, service = _make_scheduler()

        # Day 1
        now1 = _utc(9, 0, day=20)
        start1 = now1 + timedelta(minutes=14)
        task = _task(scheduled_start=start1.isoformat())
        db.list_tasks = AsyncMock(return_value=[task])
        await sched._check_and_send(now1)

        # Day 2 — task has been rescheduled to same relative window.
        now2 = _utc(9, 0, day=21)
        start2 = now2 + timedelta(minutes=14)
        task2 = _task(scheduled_start=start2.isoformat())
        db.list_tasks = AsyncMock(return_value=[task2])
        await sched._check_and_send(now2)

        assert service.dispatch.call_count == 2


# ---------------------------------------------------------------------------
# Message format
# ---------------------------------------------------------------------------


class TestReminderFormat:
    async def test_reminder_message_contains_title_and_duration(self) -> None:
        sched, db, service = _make_scheduler()
        now = _utc(9, 0)
        start = now + timedelta(minutes=14)
        db.list_tasks = AsyncMock(
            return_value=[_task(title="Write report", estimated_duration=45, scheduled_start=start.isoformat())]
        )

        await sched._check_and_send(now)

        content = service.dispatch.call_args[1]["content"]
        assert "Write report" in content
        assert "45 min" in content

    async def test_reminder_message_unknown_duration(self) -> None:
        sched, db, service = _make_scheduler()
        now = _utc(9, 0)
        start = now + timedelta(minutes=14)
        db.list_tasks = AsyncMock(
            return_value=[_task(estimated_duration=None, scheduled_start=start.isoformat())]
        )

        await sched._check_and_send(now)

        content = service.dispatch.call_args[1]["content"]
        assert "unknown" in content

    async def test_reminder_task_with_no_scheduled_start_skipped(self) -> None:
        sched, db, service = _make_scheduler()
        now = _utc(9, 0)
        db.list_tasks = AsyncMock(return_value=[_task(scheduled_start=None)])

        await sched._check_and_send(now)

        service.dispatch.assert_not_called()
