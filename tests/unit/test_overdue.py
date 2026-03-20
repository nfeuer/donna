"""Unit tests for OverdueDetector.

Tests overdue detection logic and the handle_reply state-machine transitions.
No real Discord, DB, or scheduler connections.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from donna.notifications.overdue import OVERDUE_BUFFER_MINUTES, OverdueDetector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 3, 20, hour, minute, tzinfo=timezone.utc)


def _task(
    task_id: str = "t1",
    title: str = "Build thing",
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


def _make_detector() -> tuple[OverdueDetector, AsyncMock, AsyncMock, MagicMock]:
    db = AsyncMock()
    service = AsyncMock()
    service.dispatch = AsyncMock(return_value=True)
    bot = MagicMock()
    bot.create_overdue_thread = AsyncMock(return_value=42)
    scheduler = MagicMock()
    scheduler.schedule_task = AsyncMock()

    detector = OverdueDetector(
        db=db,
        service=service,
        bot=bot,
        scheduler=scheduler,
        calendar_id="primary",
        user_id="u1",
    )
    return detector, db, service, bot


# ---------------------------------------------------------------------------
# Detection logic
# ---------------------------------------------------------------------------


class TestOverdueDetection:
    async def test_task_past_buffer_triggers_nudge(self) -> None:
        detector, db, service, bot = _make_detector()
        now = _utc(10, 0)
        # Task started at 9:00, duration 20 min → ends 9:20 + 30 min buffer = 9:50 overdue
        start = _utc(9, 0)
        db.list_tasks = AsyncMock(return_value=[
            _task(scheduled_start=start.isoformat(), estimated_duration=20)
        ])

        await detector._check_and_nudge(now)

        service.dispatch.assert_called_once()

    async def test_task_within_buffer_not_nudged(self) -> None:
        detector, db, service, bot = _make_detector()
        now = _utc(9, 45)
        # Task started at 9:00, duration 20 min → ends 9:20 + 30 min buffer = 9:50
        start = _utc(9, 0)
        db.list_tasks = AsyncMock(return_value=[
            _task(scheduled_start=start.isoformat(), estimated_duration=20)
        ])

        await detector._check_and_nudge(now)

        service.dispatch.assert_not_called()

    async def test_task_with_no_scheduled_start_skipped(self) -> None:
        detector, db, service, bot = _make_detector()
        now = _utc(10, 0)
        db.list_tasks = AsyncMock(return_value=[_task(scheduled_start=None)])

        await detector._check_and_nudge(now)

        service.dispatch.assert_not_called()

    async def test_nudge_only_sent_once_per_day(self) -> None:
        detector, db, service, bot = _make_detector()
        now = _utc(10, 0)
        start = _utc(9, 0)
        task = _task(scheduled_start=start.isoformat(), estimated_duration=20)
        db.list_tasks = AsyncMock(return_value=[task])

        await detector._check_and_nudge(now)
        await detector._check_and_nudge(now)

        assert service.dispatch.call_count == 1

    async def test_overdue_thread_created_when_sent(self) -> None:
        detector, db, service, bot = _make_detector()
        now = _utc(10, 0)
        start = _utc(9, 0)
        db.list_tasks = AsyncMock(return_value=[
            _task(scheduled_start=start.isoformat(), estimated_duration=20)
        ])

        await detector._check_and_nudge(now)

        bot.create_overdue_thread.assert_called_once()

    async def test_nudge_message_contains_task_title(self) -> None:
        detector, db, service, bot = _make_detector()
        now = _utc(10, 0)
        start = _utc(9, 0)
        db.list_tasks = AsyncMock(return_value=[
            _task(title="Write blog post", scheduled_start=start.isoformat(), estimated_duration=20)
        ])

        await detector._check_and_nudge(now)

        content = service.dispatch.call_args[1]["content"]
        assert "Write blog post" in content

    async def test_service_dispatch_not_called_when_service_blocks(self) -> None:
        """When service queues (blackout), thread creation is skipped."""
        detector, db, service, bot = _make_detector()
        service.dispatch = AsyncMock(return_value=False)  # queued, not sent
        now = _utc(10, 0)
        start = _utc(9, 0)
        db.list_tasks = AsyncMock(return_value=[
            _task(scheduled_start=start.isoformat(), estimated_duration=20)
        ])

        await detector._check_and_nudge(now)

        bot.create_overdue_thread.assert_not_called()


# ---------------------------------------------------------------------------
# Reply handling — "done"
# ---------------------------------------------------------------------------


class TestHandleReplyDone:
    async def test_done_reply_transitions_to_done(self) -> None:
        detector, db, service, bot = _make_detector()

        task_mock = _task(task_id="t1", status="scheduled")
        db.get_task = AsyncMock(return_value=task_mock)
        db.transition_task_state = AsyncMock()
        db.update_task = AsyncMock()

        await detector.handle_reply("t1", "done")

        from donna.tasks.db_models import TaskStatus
        # Should transition scheduled → in_progress, then → done
        calls = db.transition_task_state.call_args_list
        statuses = [c[0][1] for c in calls]
        assert TaskStatus.IN_PROGRESS in statuses
        assert TaskStatus.DONE in statuses

    async def test_done_reply_sets_completed_at(self) -> None:
        detector, db, service, bot = _make_detector()

        task_mock = _task(task_id="t1", status="in_progress")
        db.get_task = AsyncMock(return_value=task_mock)
        db.transition_task_state = AsyncMock()
        db.update_task = AsyncMock()

        await detector.handle_reply("t1", "done")

        db.update_task.assert_called_once()
        kw = db.update_task.call_args[1]
        assert "completed_at" in kw

    async def test_done_reply_task_not_found(self) -> None:
        detector, db, service, bot = _make_detector()
        db.get_task = AsyncMock(return_value=None)

        # Should not raise.
        await detector.handle_reply("missing", "done")

        db.transition_task_state.assert_not_called()


# ---------------------------------------------------------------------------
# Reply handling — "reschedule"
# ---------------------------------------------------------------------------


class TestHandleReplyReschedule:
    async def test_reschedule_reply_calls_schedule_task(self) -> None:
        detector, db, service, bot = _make_detector()

        task_mock = _task(task_id="t1", status="in_progress")
        refreshed = _task(task_id="t1", status="scheduled")
        db.get_task = AsyncMock(return_value=task_mock)
        db.transition_task_state = AsyncMock()
        db.get_task = AsyncMock(side_effect=[task_mock, refreshed])

        # Provide a calendar client stub on the scheduler.
        detector._scheduler._client = MagicMock()

        await detector.handle_reply("t1", "reschedule")

        detector._scheduler.schedule_task.assert_called_once()

    async def test_reschedule_transitions_through_in_progress(self) -> None:
        detector, db, service, bot = _make_detector()

        task_mock = _task(task_id="t1", status="scheduled")
        refreshed = _task(task_id="t1", status="scheduled")
        db.get_task = AsyncMock(side_effect=[task_mock, refreshed])
        db.transition_task_state = AsyncMock()
        detector._scheduler._client = MagicMock()

        await detector.handle_reply("t1", "reschedule")

        from donna.tasks.db_models import TaskStatus
        state_calls = [c[0][1] for c in db.transition_task_state.call_args_list]
        assert TaskStatus.IN_PROGRESS in state_calls
        assert TaskStatus.SCHEDULED in state_calls

    async def test_unrecognised_reply_does_nothing(self) -> None:
        detector, db, service, bot = _make_detector()

        task_mock = _task(task_id="t1", status="scheduled")
        db.get_task = AsyncMock(return_value=task_mock)
        db.transition_task_state = AsyncMock()

        await detector.handle_reply("t1", "maybe later")

        db.transition_task_state.assert_not_called()
