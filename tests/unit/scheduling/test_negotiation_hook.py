"""Tests for the AutoScheduler negotiation hook (design §1.8 + §2 matrix).

Confirms the ``except NoSlotFoundError`` arm: transition to needs_scheduling
FIRST (crash-consistent), then gate (§1.2) and dispatch (§2) — PROPOSED sends
the Discord proposal, IMPOSSIBLE sends the row-6 options notice (never silent),
CalendarReadError fires a fallback alert.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.config import (
    CalendarConfig,
    CalendarEntryConfig,
    CredentialsConfig,
    NegotiationConfig,
    SchedulingConfig,
    SyncConfig,
    TimeWindowConfig,
    TimeWindowsConfig,
)
from donna.notifications.service import NOTIF_RESCHEDULE
from donna.scheduling.auto_scheduler import AutoScheduler
from donna.scheduling.scheduler import (
    NEGOTIATION_IMPOSSIBLE,
    NEGOTIATION_PROPOSED,
    CalendarReadError,
    NoSlotFoundError,
    Scheduler,
)
from donna.scheduling.time_intent import TimeIntent
from donna.tasks.database import TaskRow
from donna.tasks.db_models import TaskStatus


def _cfg(**neg_over) -> CalendarConfig:
    return CalendarConfig(
        calendars={
            "personal": CalendarEntryConfig(calendar_id="primary", access="read_write"),
        },
        sync=SyncConfig(),
        scheduling=SchedulingConfig(),
        time_windows=TimeWindowsConfig(
            blackout=TimeWindowConfig(start_hour=0, end_hour=6, days=[0, 1, 2, 3, 4, 5, 6]),
            quiet_hours=TimeWindowConfig(start_hour=22, end_hour=24, days=[0, 1, 2, 3, 4, 5, 6]),
            work=TimeWindowConfig(start_hour=8, end_hour=17, days=[0, 1, 2, 3, 4]),
            personal=TimeWindowConfig(start_hour=8, end_hour=22, days=[0, 1, 2, 3, 4, 5, 6]),
            weekend=TimeWindowConfig(start_hour=6, end_hour=22, days=[5, 6]),
        ),
        credentials=CredentialsConfig(
            client_secrets_path="c.json", token_path="t.json",
            scopes=["https://www.googleapis.com/auth/calendar"],
        ),
        negotiation=NegotiationConfig(**neg_over),
        timezone="UTC",
    )


def _task(priority: int = 4, hard: bool = True, deadline_in: bool = True) -> TaskRow:
    ti = None
    if deadline_in:
        ti = TimeIntent(
            kind="exact",
            due_at=datetime(2026, 6, 15, 9, tzinfo=UTC),
            strictness="hard" if hard else "soft",
        ).to_json()
    return TaskRow(
        id="T", user_id="nick", title="Ship it", description=None, domain="personal",
        priority=priority, status="backlog", estimated_duration=60, deadline=None,
        deadline_type="hard" if hard else "soft", scheduled_start=None,
        actual_start=None, completed_at=None, recurrence=None, dependencies=None,
        parent_task=None, prep_work_flag=False, prep_work_instructions=None,
        agent_eligible=False, assigned_agent=None, agent_status=None, tags=None,
        notes=None, reschedule_count=0, created_at="2026-06-13T08:00:00",
        created_via="discord", estimated_cost=None, calendar_event_id=None,
        donna_managed=False, nudge_count=0, quality_score=None, time_intent_json=ti,
    )


def _db() -> MagicMock:
    db = MagicMock()
    db.transition_task_state = AsyncMock(return_value=[])
    db.update_task = AsyncMock()
    return db


def _notify() -> MagicMock:
    n = MagicMock()
    n.dispatch = AsyncMock(return_value=True)
    n.dispatch_fallback_alert = AsyncMock(return_value=True)
    return n


def _scheduler_raising(cfg: CalendarConfig) -> Scheduler:
    """A real Scheduler whose schedule_task raises NoSlotFoundError."""
    sched = Scheduler(cfg)
    sched.schedule_task = AsyncMock(side_effect=NoSlotFoundError("T", 14))
    return sched


@pytest.mark.asyncio
async def test_no_slot_transitions_first_then_proposes() -> None:
    """PROPOSED outcome → state moved to needs_scheduling FIRST, proposal sent."""
    cfg = _cfg()
    sched = _scheduler_raising(cfg)
    proposal = MagicMock()
    proposal.proposal_id = "p1"
    proposal.slot = MagicMock()
    proposal.slot.start = datetime(2026, 6, 15, 8, tzinfo=UTC)
    proposal.moves = ()
    sched.negotiate_and_apply = AsyncMock(
        return_value=(NEGOTIATION_PROPOSED, proposal)
    )
    db = _db()
    notify = _notify()
    client = MagicMock()
    auto = AutoScheduler(sched, db, client, "primary", notify)

    await auto.on_task_created(_task())

    # Transitioned to needs_scheduling BEFORE negotiating.
    db.transition_task_state.assert_awaited_once_with("T", TaskStatus.NEEDS_SCHEDULING)
    sched.negotiate_and_apply.assert_awaited_once()
    # A reschedule proposal notification was dispatched (with a view).
    assert notify.dispatch.await_count == 1
    kwargs = notify.dispatch.await_args.kwargs
    assert kwargs["notification_type"] == NOTIF_RESCHEDULE
    assert kwargs.get("view") is not None
    # Notification priority is max(task priority, 3).
    assert kwargs["priority"] == 4


@pytest.mark.asyncio
async def test_impossible_sends_options_never_silent() -> None:
    """IMPOSSIBLE outcome → row-6 options notice (never silent), pri >= 4."""
    cfg = _cfg()
    sched = _scheduler_raising(cfg)
    sched.negotiate_and_apply = AsyncMock(
        return_value=(NEGOTIATION_IMPOSSIBLE, None)
    )
    db = _db()
    notify = _notify()
    auto = AutoScheduler(sched, db, MagicMock(), "primary", notify)

    await auto.on_task_created(_task(priority=3))

    db.transition_task_state.assert_awaited_once_with("T", TaskStatus.NEEDS_SCHEDULING)
    assert notify.dispatch.await_count == 1
    kwargs = notify.dispatch.await_args.kwargs
    assert kwargs["notification_type"] == NOTIF_RESCHEDULE
    assert kwargs["priority"] >= 4
    assert "Options" in kwargs["content"] or "option" in kwargs["content"].lower()


@pytest.mark.asyncio
async def test_calendar_read_error_fires_fallback_alert() -> None:
    """CalendarReadError during negotiation → fallback alert (row 9)."""
    cfg = _cfg()
    sched = _scheduler_raising(cfg)
    sched.negotiate_and_apply = AsyncMock(
        side_effect=CalendarReadError("primary")
    )
    db = _db()
    notify = _notify()
    auto = AutoScheduler(sched, db, MagicMock(), "primary", notify)

    await auto.on_task_created(_task())

    notify.dispatch_fallback_alert.assert_awaited_once()
    assert notify.dispatch_fallback_alert.await_args.kwargs["component"] == "negotiator"


@pytest.mark.asyncio
async def test_soft_deadline_does_not_negotiate() -> None:
    """A soft-deadline failure surfaces needs_scheduling, never negotiates (§1.2)."""
    cfg = _cfg()
    sched = _scheduler_raising(cfg)
    sched.negotiate_and_apply = AsyncMock()
    db = _db()
    notify = _notify()
    auto = AutoScheduler(sched, db, MagicMock(), "primary", notify)

    await auto.on_task_created(_task(hard=False))

    db.transition_task_state.assert_awaited_once_with("T", TaskStatus.NEEDS_SCHEDULING)
    sched.negotiate_and_apply.assert_not_called()
    # Still surfaced (not silent).
    assert notify.dispatch.await_count == 1


@pytest.mark.asyncio
async def test_below_priority_floor_does_not_negotiate() -> None:
    """A hard task below min_displacer_priority does not negotiate (§1.2)."""
    cfg = _cfg(min_displacer_priority=5)  # floor above the task's priority
    sched = _scheduler_raising(cfg)
    sched.negotiate_and_apply = AsyncMock()
    db = _db()
    notify = _notify()
    auto = AutoScheduler(sched, db, MagicMock(), "primary", notify)

    await auto.on_task_created(_task(priority=4))

    sched.negotiate_and_apply.assert_not_called()
    db.transition_task_state.assert_awaited_once_with("T", TaskStatus.NEEDS_SCHEDULING)


@pytest.mark.asyncio
async def test_disabled_negotiation_does_not_negotiate() -> None:
    """negotiation.enabled=false → never negotiate, just surface (§1.2)."""
    cfg = _cfg(enabled=False)
    sched = _scheduler_raising(cfg)
    sched.negotiate_and_apply = AsyncMock()
    db = _db()
    notify = _notify()
    auto = AutoScheduler(sched, db, MagicMock(), "primary", notify)

    await auto.on_task_created(_task())

    sched.negotiate_and_apply.assert_not_called()


@pytest.mark.asyncio
async def test_no_calendar_client_does_not_negotiate() -> None:
    """Fallback mode (no calendar client) surfaces needs_scheduling, no negotiate."""
    cfg = _cfg()
    sched = Scheduler(cfg)
    # In fallback mode _schedule calls find_next_slot([]) directly; force no slot.
    sched.find_next_slot = MagicMock(side_effect=NoSlotFoundError("T", 14))
    sched.negotiate_and_apply = AsyncMock()
    db = _db()
    notify = _notify()
    auto = AutoScheduler(sched, db, None, "primary", notify)

    await auto.on_task_created(_task())

    sched.negotiate_and_apply.assert_not_called()
    db.transition_task_state.assert_awaited_once_with("T", TaskStatus.NEEDS_SCHEDULING)
