"""Unit tests for the AutoScheduler event subscriber."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.scheduling.auto_scheduler import AutoScheduler
from donna.scheduling.scheduler import ScheduledSlot
from donna.scheduling.time_intent import TimeIntent
from donna.tasks.database import TaskRow


def _make_task(
    task_id: str = "task-001",
    status: str = "backlog",
    estimated_duration: int | None = 60,
    domain: str = "personal",
    priority: int = 2,
    time_intent_json: str | None = None,
) -> TaskRow:
    return TaskRow(
        id=task_id,
        user_id="nick",
        title="Test task",
        description=None,
        domain=domain,
        priority=priority,
        status=status,
        estimated_duration=estimated_duration,
        deadline=None,
        deadline_type="none",
        scheduled_start=None,
        actual_start=None,
        completed_at=None,
        recurrence=None,
        dependencies=None,
        parent_task=None,
        prep_work_flag=False,
        prep_work_instructions=None,
        agent_eligible=False,
        assigned_agent=None,
        agent_status=None,
        tags=None,
        notes=None,
        reschedule_count=0,
        created_at="2026-05-11T09:00:00",
        created_via="discord",
        estimated_cost=None,
        calendar_event_id=None,
        donna_managed=False,
        nudge_count=0,
        quality_score=None,
        time_intent_json=time_intent_json,
    )


# A time-bound intent → routes to SCHEDULER (the gate schedules immediately).
def _exact_intent() -> str:
    return TimeIntent(
        kind="exact",
        due_at=datetime(2026, 5, 13, 9, 0, tzinfo=UTC),
        strictness="soft",
    ).to_json()


@pytest.fixture
def scheduler_mock() -> MagicMock:
    mock = MagicMock()
    slot = ScheduledSlot(
        start=datetime(2026, 5, 12, 9, 0, tzinfo=UTC),
        end=datetime(2026, 5, 12, 10, 0, tzinfo=UTC),
    )
    mock.schedule_task = AsyncMock(return_value=slot)
    mock.find_next_slot = MagicMock(return_value=slot)
    return mock


@pytest.fixture
def db_mock() -> MagicMock:
    mock = MagicMock()
    mock.update_task = AsyncMock()
    mock.transition_task_state = AsyncMock(return_value=[])
    mock.get_task = AsyncMock(return_value=_make_task())
    return mock


@pytest.fixture
def notification_mock() -> MagicMock:
    mock = MagicMock()
    mock.dispatch = AsyncMock(return_value=True)
    return mock


@pytest.fixture
def auto_scheduler(
    scheduler_mock: MagicMock,
    db_mock: MagicMock,
    notification_mock: MagicMock,
) -> AutoScheduler:
    return AutoScheduler(
        scheduler=scheduler_mock,
        db=db_mock,
        calendar_client=None,
        calendar_id="primary",
        notification_service=notification_mock,
    )


@pytest.mark.asyncio
async def test_on_task_created_schedules_without_calendar(
    auto_scheduler: AutoScheduler,
    scheduler_mock: MagicMock,
    db_mock: MagicMock,
) -> None:
    task = _make_task(time_intent_json=_exact_intent())
    await auto_scheduler.on_task_created(task)

    scheduler_mock.find_next_slot.assert_called_once()
    db_mock.update_task.assert_called_once()
    db_mock.transition_task_state.assert_called_once()


@pytest.mark.asyncio
async def test_on_task_created_uses_calendar_when_available(
    scheduler_mock: MagicMock,
    db_mock: MagicMock,
    notification_mock: MagicMock,
) -> None:
    calendar_client = MagicMock()
    auto = AutoScheduler(
        scheduler=scheduler_mock,
        db=db_mock,
        calendar_client=calendar_client,
        calendar_id="primary",
        notification_service=notification_mock,
    )
    task = _make_task(time_intent_json=_exact_intent())
    await auto.on_task_created(task)

    scheduler_mock.schedule_task.assert_called_once_with(
        task, db_mock, calendar_client, "primary"
    )


@pytest.mark.asyncio
async def test_on_task_created_skips_when_challenger_pending(
    auto_scheduler: AutoScheduler,
    scheduler_mock: MagicMock,
) -> None:
    task = _make_task()
    await auto_scheduler.on_task_created(task, challenger_pending=True)

    scheduler_mock.find_next_slot.assert_not_called()
    scheduler_mock.schedule_task.assert_not_called()


@pytest.mark.asyncio
async def test_on_task_created_skips_already_scheduled(
    auto_scheduler: AutoScheduler,
    scheduler_mock: MagicMock,
) -> None:
    task = _make_task(status="scheduled")
    await auto_scheduler.on_task_created(task)

    scheduler_mock.find_next_slot.assert_not_called()
    scheduler_mock.schedule_task.assert_not_called()


@pytest.mark.asyncio
async def test_on_challenger_resolved_schedules(
    auto_scheduler: AutoScheduler,
    scheduler_mock: MagicMock,
    db_mock: MagicMock,
) -> None:
    task = _make_task()
    db_mock.get_task = AsyncMock(return_value=task)
    await auto_scheduler.on_challenger_resolved(task)

    scheduler_mock.find_next_slot.assert_called_once()


@pytest.mark.asyncio
async def test_on_task_created_sends_notification(
    auto_scheduler: AutoScheduler,
    notification_mock: MagicMock,
) -> None:
    task = _make_task(time_intent_json=_exact_intent())
    await auto_scheduler.on_task_created(task)

    notification_mock.dispatch.assert_called_once()
