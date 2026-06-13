"""Unit tests for the scheduling negotiation loop (design §7, Slice A).

Covers the single-displacement, propose-and-confirm negotiator:

  - immovable user event → ``None`` + zero writes + row-6 options notice;
  - read-only-calendar guard (Donna-tagged events there are still immovable);
  - single soft displacement (victim re-place valid + before its deadline);
  - victim selection by cost;
  - infeasible victim rejected;
  - termination / no-thrash (structural depth-1; max-auto-moves immovable;
    double-run no oscillation);
  - lock serialization;
  - stale-proposal re-validation on accept;
  - fail-closed ``CalendarReadError``.

See docs/superpowers/specs/2026-06-12-scheduling-negotiation-design.md.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from datetime import UTC, datetime
from typing import Any
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
from donna.integrations.calendar import CalendarEvent
from donna.scheduling.scheduler import (
    NEGOTIATION_APPLIED,
    NEGOTIATION_IMPOSSIBLE,
    NEGOTIATION_PROPOSED,
    CalendarReadError,
    Scheduler,
)
from donna.scheduling.time_intent import TimeIntent
from donna.tasks.database import TaskRow

WRITE_CAL = "primary"
WORK_CAL = "work-cal"


# ------------------------------------------------------------------
# Fixtures / builders
# ------------------------------------------------------------------


def _cfg(**neg_over: Any) -> CalendarConfig:
    return CalendarConfig(
        calendars={
            "personal": CalendarEntryConfig(calendar_id=WRITE_CAL, access="read_write"),
            "work": CalendarEntryConfig(calendar_id=WORK_CAL, access="read_only"),
        },
        sync=SyncConfig(),
        scheduling=SchedulingConfig(
            slot_step_minutes=15, default_duration_minutes=60, search_horizon_days=14
        ),
        time_windows=TimeWindowsConfig(
            blackout=TimeWindowConfig(start_hour=0, end_hour=6, days=[0, 1, 2, 3, 4, 5, 6]),
            quiet_hours=TimeWindowConfig(start_hour=22, end_hour=24, days=[0, 1, 2, 3, 4, 5, 6]),
            work=TimeWindowConfig(start_hour=8, end_hour=17, days=[0, 1, 2, 3, 4]),
            # Wide personal window so victims have somewhere to re-place to.
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


def _task(
    task_id: str = "T",
    priority: int = 4,
    domain: str = "personal",
    estimated_duration: int = 60,
    status: str = "needs_scheduling",
    deadline: str | None = None,
    deadline_type: str = "hard",
    reschedule_count: int = 0,
    scheduled_start: str | None = None,
    time_intent_json: str | None = None,
) -> TaskRow:
    return TaskRow(
        id=task_id, user_id="nick", title=f"Task {task_id}", description=None,
        domain=domain, priority=priority, status=status,
        estimated_duration=estimated_duration, deadline=deadline,
        deadline_type=deadline_type, scheduled_start=scheduled_start,
        actual_start=None, completed_at=None, recurrence=None, dependencies=None,
        parent_task=None, prep_work_flag=False, prep_work_instructions=None,
        agent_eligible=False, assigned_agent=None, agent_status=None, tags=None,
        notes=None, reschedule_count=reschedule_count,
        created_at="2026-06-13T08:00:00", created_via="discord", estimated_cost=None,
        calendar_event_id=None, donna_managed=False, nudge_count=0, quality_score=None,
        time_intent_json=time_intent_json,
    )


def _hard_intent(due: datetime) -> str:
    return TimeIntent(kind="exact", due_at=due, strictness="hard").to_json()


def _soft_intent(due: datetime) -> str:
    return TimeIntent(kind="exact", due_at=due, strictness="soft").to_json()


def _utc(h: int, m: int = 0, day: int = 15) -> datetime:
    # 2026-06-15 is a Monday.
    return datetime(2026, 6, day, h, m, tzinfo=UTC)


def _event(
    event_id: str,
    start: datetime,
    end: datetime,
    *,
    cal: str = WRITE_CAL,
    donna_managed: bool = True,
    task_id: str | None = None,
) -> CalendarEvent:
    return CalendarEvent(
        event_id=event_id, calendar_id=cal, summary="ev", start=start, end=end,
        donna_managed=donna_managed, donna_task_id=task_id, etag="x",
    )


class _FakeDB:
    """In-memory stand-in for Database covering the negotiation surface."""

    def __init__(self, tasks: dict[str, TaskRow] | None = None) -> None:
        self.tasks: dict[str, TaskRow] = dict(tasks or {})
        self.proposals: dict[str, dict[str, Any]] = {}
        self.transitions: list[tuple[str, str]] = []
        self.updates: list[tuple[str, dict[str, Any]]] = []

    async def get_task(self, task_id: str) -> TaskRow | None:
        return self.tasks.get(task_id)

    async def update_task(self, task_id: str, **fields: Any) -> None:
        self.updates.append((task_id, fields))
        if task_id in self.tasks:
            norm = {
                k: (v.isoformat() if isinstance(v, datetime) else v)
                for k, v in fields.items()
            }
            self.tasks[task_id] = dataclasses.replace(self.tasks[task_id], **norm)

    async def transition_task_state(self, task_id: str, status: Any) -> list[str]:
        val = getattr(status, "value", status)
        self.transitions.append((task_id, val))
        if task_id in self.tasks:
            self.tasks[task_id] = dataclasses.replace(self.tasks[task_id], status=val)
        return []

    async def create_negotiation_proposal(self, **kw: Any) -> None:
        self.proposals[kw["proposal_id"]] = {**kw, "status": "pending"}

    async def get_negotiation_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        return self.proposals.get(proposal_id)

    async def update_negotiation_proposal_status(
        self, proposal_id: str, status: str
    ) -> None:
        if proposal_id in self.proposals:
            self.proposals[proposal_id]["status"] = status

    async def execute_sql(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        # Used by _auto_moves_today: count accepted proposals created since a cutoff.
        cutoff = (params or [None])[0]
        out = []
        for p in self.proposals.values():
            if p.get("status") == "accepted" and (
                cutoff is None or p.get("created_at", "") >= cutoff
            ):
                out.append({"moves_json": p["moves_json"]})
        return out


def _client(events: list[CalendarEvent], *, read_error: bool = False) -> MagicMock:
    client = MagicMock()
    if read_error:
        client.list_events = AsyncMock(side_effect=RuntimeError("google 500"))
    else:
        async def _list(cal: str, a: datetime, b: datetime) -> list[CalendarEvent]:
            return [e for e in events if e.calendar_id == cal]
        client.list_events = AsyncMock(side_effect=_list)
    client.update_event = AsyncMock(
        side_effect=lambda cal, eid, s, e: _event(eid, s, e, cal=cal)
    )
    client.create_event = AsyncMock(
        side_effect=lambda calendar_id, summary, start, end, task_id: _event(
            f"new-{task_id}", start, end, cal=calendar_id, task_id=task_id
        )
    )
    return client


# ------------------------------------------------------------------
# Movability / placement
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_immovable_user_event_returns_none_zero_writes() -> None:
    """A non-Donna user event blocking the only slot → None + zero writes."""
    sched = Scheduler(_cfg())
    now = _utc(6)
    deadline = _utc(12)
    # Block the entire pre-deadline window with a USER event (not movable).
    user_ev = _event("user-1", _utc(8), _utc(12), donna_managed=False)
    client = _client([user_ev])
    db = _FakeDB()
    task = _task(priority=4, deadline=deadline.isoformat(),
                 time_intent_json=_hard_intent(deadline))

    async with sched._lock:
        proposal = await sched.negotiate_placement(task, db, client, WRITE_CAL, now=now)

    assert proposal is None
    client.update_event.assert_not_called()
    client.create_event.assert_not_called()


@pytest.mark.asyncio
async def test_readonly_calendar_donna_event_is_immovable() -> None:
    """A Donna-tagged event on a read-only calendar is never movable (no override)."""
    sched = Scheduler(_cfg())
    now = _utc(6)
    deadline = _utc(12)
    # Donna-managed event, but on the WORK (read-only) calendar → immovable.
    work_ev = _event("work-ev", _utc(8), _utc(12), cal=WORK_CAL,
                     donna_managed=True, task_id="victim")
    client = _client([work_ev])
    db = _FakeDB({"victim": _task("victim", priority=1, status="scheduled")})
    task = _task(priority=4, deadline=deadline.isoformat(),
                 time_intent_json=_hard_intent(deadline))

    async with sched._lock:
        proposal = await sched.negotiate_placement(task, db, client, WRITE_CAL, now=now)

    assert proposal is None


@pytest.mark.asyncio
async def test_single_soft_displacement_succeeds() -> None:
    """One movable soft victim is displaced; victim re-places before its deadline.

    The ONLY window-valid slot for T (08:00–09:00, T due 09:00) is blocked by a
    movable soft victim, so a clean slot does not exist — displacement is forced.
    """
    sched = Scheduler(_cfg())
    now = _utc(6)
    t_deadline = _utc(9)  # only slot is 08:00–09:00
    victim_ev = _event("vic-ev", _utc(8), _utc(9), task_id="victim")
    victim = _task("victim", priority=1, status="scheduled",
                   scheduled_start=_utc(8).isoformat(),
                   time_intent_json=_soft_intent(_utc(20)))
    client = _client([victim_ev])
    db = _FakeDB({"victim": victim})
    task = _task("T", priority=4, deadline=t_deadline.isoformat(),
                 time_intent_json=_hard_intent(t_deadline))

    async with sched._lock:
        proposal = await sched.negotiate_placement(task, db, client, WRITE_CAL, now=now)

    assert proposal is not None
    assert len(proposal.moves) == 1
    move = proposal.moves[0]
    assert move.task_id == "victim"
    # Victim re-places into a free slot at/after 09:00 (after T takes 08:00).
    assert move.new_start >= _utc(9)
    # T takes the freed 08:00 slot.
    assert proposal.slot.start == _utc(8)


@pytest.mark.asyncio
async def test_victim_selection_by_cost() -> None:
    """Given two candidate slots, the cheaper victim's slot is chosen.

    Two disjoint pre-deadline slots are each blocked by a single movable
    victim: a low-priority/soft victim vs a higher-priority victim. The
    low-cost victim's slot must win.
    """
    sched = Scheduler(_cfg(max_displacements_per_placement=1))
    now = _utc(6)
    t_deadline = _utc(12)
    # Slot A (08:00) blocked by P1 soft victim (cheap).
    # Slot B (10:00) blocked by P3 hard victim (expensive).
    cheap_ev = _event("cheap", _utc(8), _utc(9), task_id="cheap")
    pricey_ev = _event("pricey", _utc(10), _utc(11), task_id="pricey")
    cheap = _task("cheap", priority=1, status="scheduled",
                  scheduled_start=_utc(8).isoformat(),
                  time_intent_json=_soft_intent(_utc(20)))
    pricey = _task("pricey", priority=3, status="scheduled",
                   scheduled_start=_utc(10).isoformat(),
                   time_intent_json=_hard_intent(_utc(20)))
    # Fill 09:00–10:00 and 11:00–12:00 with user events so the ONLY movable
    # slots are A and B (force a real choice between victims).
    filler1 = _event("f1", _utc(9), _utc(10), donna_managed=False)
    filler2 = _event("f2", _utc(11), _utc(12), donna_managed=False)
    client = _client([cheap_ev, pricey_ev, filler1, filler2])
    db = _FakeDB({"cheap": cheap, "pricey": pricey})
    task = _task("T", priority=4, deadline=t_deadline.isoformat(),
                 time_intent_json=_hard_intent(t_deadline))

    async with sched._lock:
        proposal = await sched.negotiate_placement(task, db, client, WRITE_CAL, now=now)

    assert proposal is not None
    assert proposal.moves[0].task_id == "cheap"
    assert proposal.slot.start == _utc(8)


@pytest.mark.asyncio
async def test_infeasible_victim_rejected() -> None:
    """A victim with no free re-place slot before ITS deadline → that slot rejected."""
    sched = Scheduler(_cfg())
    now = _utc(6)
    t_deadline = _utc(9)  # only window-valid slot for T is the victim's
    # Movable victim blocks 08:00–09:00, but the victim is itself a hard-deadline
    # task due at 09:00 with no free slot after being displaced (its only valid
    # pre-deadline slot is the one T wants). → infeasible → None.
    victim_ev = _event("vic", _utc(8), _utc(9), task_id="victim")
    victim = _task("victim", priority=1, status="scheduled",
                   scheduled_start=_utc(8).isoformat(),
                   time_intent_json=_hard_intent(_utc(9)))  # tight hard deadline
    client = _client([victim_ev])
    db = _FakeDB({"victim": victim})
    task = _task("T", priority=4, deadline=t_deadline.isoformat(),
                 time_intent_json=_hard_intent(t_deadline))

    async with sched._lock:
        proposal = await sched.negotiate_placement(task, db, client, WRITE_CAL, now=now)

    assert proposal is None


@pytest.mark.asyncio
async def test_more_than_cap_blockers_skipped() -> None:
    """A slot with 2 blockers is skipped when cap is 1 (Slice A single-displacement)."""
    sched = Scheduler(_cfg(max_displacements_per_placement=1))
    now = _utc(6)
    t_deadline = _utc(9)  # only window-valid 60-min slot is 08:00–09:00
    # T needs 60 min; two 30-min movable victims tile the only slot 08:00–09:00.
    v1 = _event("v1", _utc(8), _utc(8, 30), task_id="v1")
    v2 = _event("v2", _utc(8, 30), _utc(9), task_id="v2")
    # A user filler immediately after 09:00 means any later-starting candidate
    # (which would also overrun the deadline) hits an immovable event — so the
    # ONLY candidate is the 08:00–09:00 slot, which has two blockers (> cap 1).
    filler = _event("f", _utc(9), _utc(9, 30), donna_managed=False)
    db = _FakeDB({
        "v1": _task("v1", priority=1, status="scheduled",
                    scheduled_start=_utc(8).isoformat(),
                    time_intent_json=_soft_intent(_utc(20))),
        "v2": _task("v2", priority=1, status="scheduled",
                    scheduled_start=_utc(8, 30).isoformat(),
                    time_intent_json=_soft_intent(_utc(20))),
    })
    client = _client([v1, v2, filler])
    task = _task("T", priority=4, estimated_duration=60,
                 deadline=t_deadline.isoformat(),
                 time_intent_json=_hard_intent(t_deadline))

    async with sched._lock:
        proposal = await sched.negotiate_placement(task, db, client, WRITE_CAL, now=now)

    assert proposal is None


@pytest.mark.asyncio
async def test_min_lead_minutes_makes_victim_immovable() -> None:
    """A victim starting sooner than min_lead_minutes is immovable (anti-thrash)."""
    sched = Scheduler(_cfg(min_lead_minutes=120))  # 2h lead required
    now = _utc(8)  # victim starts at 08:30 → only 30-min lead, below the floor
    t_deadline = _utc(9, 30)  # T (60 min) must end by 09:30
    # Victim starts at 08:30 — only 30 min lead, below the 120-min floor.
    victim_ev = _event("vic", _utc(8, 30), _utc(9, 30), task_id="victim")
    victim = _task("victim", priority=1, status="scheduled",
                   scheduled_start=_utc(8, 30).isoformat(),
                   time_intent_json=_soft_intent(_utc(20)))
    # User filler 08:00–08:30 blocks the earlier candidates so the victim's slot
    # (08:30–09:30) is the only one whose sole blocker is the (un-leadable) victim.
    filler = _event("f", _utc(8), _utc(8, 30), donna_managed=False)
    client = _client([victim_ev, filler])
    db = _FakeDB({"victim": victim})
    task = _task("T", priority=4, deadline=t_deadline.isoformat(),
                 time_intent_json=_hard_intent(t_deadline))

    async with sched._lock:
        proposal = await sched.negotiate_placement(task, db, client, WRITE_CAL, now=now)

    assert proposal is None


@pytest.mark.asyncio
async def test_in_progress_victim_immovable() -> None:
    """A backing task that is in_progress (not scheduled) is never movable."""
    sched = Scheduler(_cfg())
    now = _utc(6)
    t_deadline = _utc(9)  # only window-valid slot is 08:00–09:00 (the victim's)
    victim_ev = _event("vic", _utc(8), _utc(9), task_id="victim")
    victim = _task("victim", priority=1, status="in_progress",
                   scheduled_start=_utc(8).isoformat(),
                   time_intent_json=_soft_intent(_utc(20)))
    client = _client([victim_ev])
    db = _FakeDB({"victim": victim})
    task = _task("T", priority=4, deadline=t_deadline.isoformat(),
                 time_intent_json=_hard_intent(t_deadline))

    async with sched._lock:
        proposal = await sched.negotiate_placement(task, db, client, WRITE_CAL, now=now)

    assert proposal is None


@pytest.mark.asyncio
async def test_equal_priority_only_movable_when_soft_vs_hard() -> None:
    """Equal-priority victim is movable only if it is soft and T is hard (OD-1)."""
    now = _utc(6)
    t_deadline = _utc(9)  # only window-valid slot is the victim's
    victim_ev = _event("vic", _utc(8), _utc(9), task_id="victim")
    client = _client([victim_ev])

    # Equal priority, victim HARD → NOT movable.
    sched = Scheduler(_cfg())
    hard_victim = _task("victim", priority=4, status="scheduled",
                        scheduled_start=_utc(8).isoformat(),
                        time_intent_json=_hard_intent(_utc(20)))
    db = _FakeDB({"victim": hard_victim})
    task = _task("T", priority=4, deadline=t_deadline.isoformat(),
                 time_intent_json=_hard_intent(t_deadline))
    async with sched._lock:
        assert await sched.negotiate_placement(task, db, client, WRITE_CAL, now=now) is None

    # Equal priority, victim SOFT, T hard → movable.
    soft_victim = _task("victim", priority=4, status="scheduled",
                        scheduled_start=_utc(8).isoformat(),
                        time_intent_json=_soft_intent(_utc(20)))
    db2 = _FakeDB({"victim": soft_victim})
    async with sched._lock:
        proposal = await sched.negotiate_placement(task, db2, client, WRITE_CAL, now=now)
    assert proposal is not None
    assert proposal.moves[0].task_id == "victim"


# ------------------------------------------------------------------
# negotiate_and_apply (propose-only in Slice A) + fail-closed
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_negotiate_and_apply_persists_and_proposes() -> None:
    """Slice A: auto_apply=false → always persist a pending proposal, no writes."""
    sched = Scheduler(_cfg())  # auto_apply defaults False
    now = _utc(6)
    t_deadline = _utc(9)  # only window-valid slot is the victim's → forces displacement
    victim_ev = _event("vic", _utc(8), _utc(9), task_id="victim")
    victim = _task("victim", priority=1, status="scheduled",
                   scheduled_start=_utc(8).isoformat(),
                   time_intent_json=_soft_intent(_utc(20)))
    client = _client([victim_ev])
    db = _FakeDB({"victim": victim})
    task = _task("T", priority=4, deadline=t_deadline.isoformat(),
                 time_intent_json=_hard_intent(t_deadline))

    outcome, proposal = await sched.negotiate_and_apply(
        task, db, client, WRITE_CAL, now=now
    )

    assert outcome == NEGOTIATION_PROPOSED
    assert proposal is not None
    assert len(proposal.moves) == 1  # one victim displaced
    # Persisted as pending; NO calendar writes happened (confirm invariant).
    assert db.proposals[proposal.proposal_id]["status"] == "pending"
    client.update_event.assert_not_called()
    client.create_event.assert_not_called()


@pytest.mark.asyncio
async def test_negotiate_and_apply_impossible_no_writes() -> None:
    """No feasible arrangement → IMPOSSIBLE, nothing persisted, zero writes."""
    sched = Scheduler(_cfg())
    now = _utc(6)
    t_deadline = _utc(12)
    user_ev = _event("user", _utc(8), _utc(12), donna_managed=False)
    client = _client([user_ev])
    db = _FakeDB()
    task = _task("T", priority=4, deadline=t_deadline.isoformat(),
                 time_intent_json=_hard_intent(t_deadline))

    outcome, proposal = await sched.negotiate_and_apply(
        task, db, client, WRITE_CAL, now=now
    )

    assert outcome == NEGOTIATION_IMPOSSIBLE
    assert proposal is None
    assert db.proposals == {}
    client.create_event.assert_not_called()


@pytest.mark.asyncio
async def test_fail_closed_on_calendar_read_error() -> None:
    """A calendar read failure propagates CalendarReadError (fail-closed)."""
    sched = Scheduler(_cfg())
    now = _utc(6)
    t_deadline = _utc(12)
    client = _client([], read_error=True)
    db = _FakeDB()
    task = _task("T", priority=4, deadline=t_deadline.isoformat(),
                 time_intent_json=_hard_intent(t_deadline))

    with pytest.raises(CalendarReadError):
        await sched.negotiate_and_apply(task, db, client, WRITE_CAL, now=now)

    client.create_event.assert_not_called()


# ------------------------------------------------------------------
# Apply path + stale-proposal re-validation
# ------------------------------------------------------------------


async def _make_pending_proposal(
    sched: Scheduler, db: _FakeDB, client: MagicMock, task: TaskRow, now: datetime
) -> Any:
    outcome, proposal = await sched.negotiate_and_apply(
        task, db, client, WRITE_CAL, now=now
    )
    assert outcome == NEGOTIATION_PROPOSED
    return proposal


@pytest.mark.asyncio
async def test_apply_success_moves_then_creates() -> None:
    """Apply on a stable world: victim moved (count+1), T created + scheduled."""
    sched = Scheduler(_cfg())
    now = _utc(6)
    t_deadline = _utc(9)  # only window-valid slot is the victim's
    victim_ev = _event("vic", _utc(8), _utc(9), task_id="victim")
    victim = _task("victim", priority=1, status="scheduled",
                   scheduled_start=_utc(8).isoformat(), reschedule_count=0,
                   time_intent_json=_soft_intent(_utc(20)))
    client = _client([victim_ev])
    db = _FakeDB({"victim": victim})
    task = _task("T", priority=4, status="needs_scheduling",
                 deadline=t_deadline.isoformat(),
                 time_intent_json=_hard_intent(t_deadline))
    db.tasks["T"] = task
    proposal = await _make_pending_proposal(sched, db, client, task, now)

    async with sched._lock:
        outcome = await sched._apply(proposal, task, db, client, WRITE_CAL, now=now)

    assert outcome == NEGOTIATION_APPLIED
    # Moves applied BEFORE create.
    assert client.update_event.await_count == 1
    assert client.create_event.await_count == 1
    # Victim stays scheduled, reschedule_count +1.
    assert db.tasks["victim"].status == "scheduled"
    assert db.tasks["victim"].reschedule_count == 1
    # T transitions needs_scheduling → scheduled.
    assert ("T", "scheduled") in db.transitions
    assert db.proposals[proposal.proposal_id]["status"] == "accepted"


@pytest.mark.asyncio
async def test_apply_revalidates_on_drift_renegotiates() -> None:
    """If the victim's old slot drifted, apply re-negotiates rather than clobbering."""
    sched = Scheduler(_cfg())
    now = _utc(6)
    t_deadline = _utc(9)  # initially only the victim's slot is window-valid
    victim_ev = _event("vic", _utc(8), _utc(9), task_id="victim")
    victim = _task("victim", priority=1, status="scheduled",
                   scheduled_start=_utc(8).isoformat(),
                   time_intent_json=_soft_intent(_utc(20)))
    client = _client([victim_ev])
    db = _FakeDB({"victim": victim})
    task = _task("T", priority=4, status="needs_scheduling",
                 deadline=t_deadline.isoformat(),
                 time_intent_json=_hard_intent(t_deadline))
    db.tasks["T"] = task
    proposal = await _make_pending_proposal(sched, db, client, task, now)
    assert len(proposal.moves) == 1  # initial plan displaced the victim

    # DRIFT: the victim's event moved off 08:00 (old_start no longer matches), so
    # 08:00–09:00 is now genuinely free. Re-read returns the drifted world.
    drifted_victim = _event("vic", _utc(12), _utc(13), task_id="victim")

    async def _list_drift(cal: str, a: datetime, b: datetime) -> list[CalendarEvent]:
        return [drifted_victim] if cal == WRITE_CAL else []
    client.list_events = AsyncMock(side_effect=_list_drift)

    async with sched._lock:
        outcome = await sched._apply(proposal, task, db, client, WRITE_CAL, now=now)

    # Re-negotiation found the now-free 08:00 slot (cheaper — zero moves), which
    # is <= the approved cost, so the fresh arrangement applies (T created, no
    # victim move). The stale victim move was NOT clobbered.
    assert outcome == NEGOTIATION_APPLIED
    assert client.create_event.await_count == 1
    client.update_event.assert_not_called()  # no stale victim move applied


# ------------------------------------------------------------------
# Termination / no-thrash
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_auto_moves_makes_victim_immovable() -> None:
    """A victim already auto-moved max times today is immovable (anti-thrash)."""
    sched = Scheduler(_cfg(max_auto_moves_per_task_per_day=1))
    now = _utc(11)  # victim at 12:00 → 60-min lead (meets the floor)
    t_deadline = _utc(13)
    victim_ev = _event("vic", _utc(12), _utc(13), task_id="victim")
    victim = _task("victim", priority=1, status="scheduled",
                   scheduled_start=_utc(12).isoformat(),
                   time_intent_json=_soft_intent(_utc(20)))
    # User filler 11:00–12:00 means the victim's slot (12:00–13:00) is the only
    # candidate — so the test isolates the max-auto-moves immovability.
    filler = _event("f", _utc(11), _utc(12), donna_managed=False)
    client = _client([victim_ev, filler])
    db = _FakeDB({"victim": victim})
    # Pre-seed an accepted proposal earlier today that already moved "victim".
    db.proposals["old"] = {
        "proposal_id": "old", "status": "accepted",
        "created_at": _utc(8).isoformat(),
        "moves_json": json.dumps([{"task_id": "victim"}]),
    }
    task = _task("T", priority=4, deadline=t_deadline.isoformat(),
                 time_intent_json=_hard_intent(t_deadline))

    async with sched._lock:
        proposal = await sched.negotiate_placement(task, db, client, WRITE_CAL, now=now)

    assert proposal is None


@pytest.mark.asyncio
async def test_double_run_no_oscillation() -> None:
    """Running negotiate twice on the same pending world yields the same plan.

    Propose-only never mutates the calendar, so a second pass sees the same
    world and produces an equivalent (same victim, same target slot) proposal —
    no oscillation. (Structural depth-1 guarantees re-placed tasks land free.)
    """
    sched = Scheduler(_cfg())
    now = _utc(6)
    t_deadline = _utc(9)  # only window-valid slot is the victim's
    victim_ev = _event("vic", _utc(8), _utc(9), task_id="victim")
    victim = _task("victim", priority=1, status="scheduled",
                   scheduled_start=_utc(8).isoformat(),
                   time_intent_json=_soft_intent(_utc(20)))
    client = _client([victim_ev])
    db = _FakeDB({"victim": victim})
    task = _task("T", priority=4, deadline=t_deadline.isoformat(),
                 time_intent_json=_hard_intent(t_deadline))

    async with sched._lock:
        p1 = await sched.negotiate_placement(task, db, client, WRITE_CAL, now=now)
        p2 = await sched.negotiate_placement(task, db, client, WRITE_CAL, now=now)

    assert p1 is not None and p2 is not None
    assert p1.slot.start == p2.slot.start
    assert [m.task_id for m in p1.moves] == [m.task_id for m in p2.moves]
    assert [m.new_start for m in p1.moves] == [m.new_start for m in p2.moves]


# ------------------------------------------------------------------
# Lock serialization
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_negotiate_and_apply_serialized_by_lock() -> None:
    """Two concurrent negotiate_and_apply calls do not overlap (shared lock)."""
    sched = Scheduler(_cfg())
    now = _utc(6)
    t_deadline = _utc(10)
    in_section = 0
    max_parallel = 0

    async def _list(cal: str, a: datetime, b: datetime) -> list[CalendarEvent]:
        nonlocal in_section, max_parallel
        in_section += 1
        max_parallel = max(max_parallel, in_section)
        await asyncio.sleep(0.01)
        in_section -= 1
        return []  # no events → IMPOSSIBLE for a hard task with no free slot

    client = MagicMock()
    client.list_events = AsyncMock(side_effect=_list)
    client.create_event = AsyncMock()
    client.update_event = AsyncMock()
    db = _FakeDB()
    # Deadline before the window opens so find_next_slot fails → negotiation,
    # which finds no movable blockers (empty calendar) → IMPOSSIBLE. The point
    # is the lock, not the outcome.
    task = _task("T", priority=4, deadline=t_deadline.isoformat(),
                 domain="work",
                 time_intent_json=_hard_intent(_utc(7)))  # before 08:00 work open

    await asyncio.gather(
        sched.negotiate_and_apply(task, db, client, WRITE_CAL, now=now),
        sched.negotiate_and_apply(task, db, client, WRITE_CAL, now=now),
    )

    assert max_parallel == 1
