# Design: Scheduling Negotiation & Cascade-Shift Loop ("Plan 2")

**Date:** 2026-06-12
**Status:** Proposed — design-ready; pending owner decisions (§8) before implementation
**Author:** design pass (Fable) + Opus verification
**Predecessor:** `docs/superpowers/specs/2026-06-11-scheduling-fable-critique-design.md` (Plan-2 deferral; S1 bundle shipped in #113)
**Spec anchors:** `spec_v3.md` §6.1.2 (conflict table, ~line 1811), §6.3 (Minimize Rescheduling / Get It Done, ~line 1871), §3.7.1 (write serialization); `docs/domain/scheduling.md`

> **Verification (Opus):** Two load-bearing claims confirmed against the code:
> (a) `config/task_states.yaml` already defines `needs_scheduling → scheduled`
> (trigger `alternative_or_rearrange_accepted`, side-effect `open_negotiation`)
> and `needs_scheduling → backlog` (`user_declines_scheduling`) — so **no
> state-machine change is required**. (b) `CalendarEvent` carries `donna_managed`
> + `donna_task_id` (`integrations/calendar.py:41-42`), so the movability filter
> is implementable. The design builds on the existing `Scheduler._lock`,
> `_gather_busy` (fail-closed), and tz-aware `find_next_slot` from the S1 bundle.

## Executive summary

When `Scheduler.find_next_slot` fails for a hard-deadline task, the new
`Scheduler.negotiate_placement` searches the pre-deadline window for a slot whose
only blockers are *movable* Donna-managed events, picks the slot with minimum
displacement cost (priority, deadline strictness, slack, reschedule history),
simulates re-placing each displaced task into a genuinely free slot, and applies
the whole arrangement atomically under the existing placement lock. A companion
`cascade_shift` primitive handles overruns/intrusions by moving each conflicting
Donna event to its next free valid slot — and because re-placement always lands in
free space, cascades cannot ripple and **termination is structural, not heuristic**.
The two non-negotiable safety invariants: **user-created (non-`donna_managed`)
events are never moved**, and **no hard deadline is ever silently violated** — any
arrangement that cannot satisfy one stops and surfaces options. Per the 2026-06-05
"any move of a committed item requires confirmation" invariant (which the critique
triage ruled supersedes the spec's auto-move tables), the loop ships in
**propose-and-confirm mode by default**; silent auto-apply is a config-gated
dial-back behind an accept-rate trigger.

---

## 1. Algorithm

### 1.1 Vocabulary
- **T** — the task that failed clean placement (the *displacer*).
- **Blocker** — a calendar event overlapping a candidate slot for T.
- **Movable** — a blocker eligible for displacement (§1.3).
- **Move** — (displaced task, old slot, new slot, event_id).
- **Proposal** — T's slot + an ordered set of Moves, with total cost.

### 1.2 When negotiation is attempted (the gate)
Runs only when ALL hold (else: today's path — `needs_scheduling`, surfaced by digests/weekly planner):
1. `find_next_slot` raised `NoSlotFoundError` **and** the failure was deadline-clamped (a derived deadline exists inside the horizon — not bare horizon exhaustion).
2. `TimeIntent.strictness == "hard"` (via `derive_deadline_type`). Soft-deadline tasks wait — displacing committed items for a soft preference violates Minimize Rescheduling.
3. `negotiation.enabled` is true in `config/calendar.yaml`.
4. `task.priority >= negotiation.min_displacer_priority`.

Attempted **once per scheduling trigger** (task created, challenger resolved, user retry) — not a background loop. `needs_scheduling` stays the terminal parking state until an external trigger re-fires `_schedule`. **Primary anti-thrash guarantee.**

### 1.3 Movability (hard filter — cost ∞)
`ev` is movable iff ALL of:
- `ev.donna_managed is True` and `ev.donna_task_id` resolves to a task row.
- `ev.calendar_id` is the personal **write** calendar (work/family are `read_only` — immovable even if Donna-tagged).
- Backing task status is `scheduled` (never `in_progress`/`done`/`paused`).
- `ev.start - now >= negotiation.min_lead_minutes`.
- `priority(D) < priority(T)`, **or** equal priority and D is soft/undated while T is hard (OD-1).
- Task has not been auto-moved `max_auto_moves_per_task_per_day` times today (anti-thrash).
- **Feasibility (in simulation):** D re-places via `find_next_slot` (clamped to D's own deadline) — a hard-deadline D with no free pre-deadline slot is automatically infeasible.

**Any user-created event is immovable. No override knob exists.**

### 1.4 Displacement cost (scalar; lower = better victim)
```
slack_hours(D) = hours between D's earliest feasible re-place end and derive_deadline(D)  # ∞ for soft/undated
cost(D) = W_PRIO*priority(D) + W_HARD*(strict?) + W_SLACK/(1+slack) + W_RESCH*reschedule_count(D) + W_SOON/(1+hrs_until_start)
slot_cost(s) = Σ cost(D) for D in blockers(s)
```
Weights in `negotiation.cost_weights` (config, not code). Tie-break: **earliest slot wins** (Get It Done). `reschedule_count` composes with `priority.escalation_after_reschedules` — two independent thrash brakes.

### 1.5 The search — `Scheduler.negotiate_placement`
Refactor: extract the candidate-stepping loop of `find_next_slot` into `_iter_window_valid_slots(task, now, horizon)` yielding window-valid slots (blackout/quiet/domain/weekday/earliest/deadline) **ignoring events**. `find_next_slot` becomes "first yielded slot with zero overlaps"; negotiation reuses it so window/blackout semantics never diverge.
```
async def negotiate_placement(task, db, client, write_calendar_id, now=None) -> Proposal | None:
    # PRECONDITION: caller holds self._lock.
    busy = await self._gather_busy(client, now, horizon)        # fail-closed
    candidates = []
    for slot in _iter_window_valid_slots(task, now, deadline):
        blockers = [ev for ev in busy if overlaps(slot, ev)]
        if not blockers: return CleanSlot(slot)
        if any(not movable(ev) for ev in blockers): continue
        if len(blockers) > cfg.max_displacements_per_placement: continue
        candidates.append((slot_cost(blockers), slot.start, slot, blockers))
    for cost, _, slot, blockers in sorted(candidates):
        sim = busy - blockers + event(T@slot); moves=[]
        for D in sorted(blockers, key=cost):
            try: new = self.find_next_slot(task_of(D), sim, now=now)   # FREE slots only, D-deadline-clamped
            except NoSlotFoundError: moves=None; break
            moves.append(Move(D, D.slot, new)); sim += event(D@new)
        if moves is not None: return Proposal(task.id, slot, tuple(moves), cost)
    return None
```
**Termination proof:** displaced tasks re-place via `find_next_slot`, which returns only non-overlapping slots — a displaced task can never displace anything (depth is **structurally 1**). Candidate scan bounded by the pre-deadline window at `slot_step_minutes`; moves bounded by `max_displacements_per_placement`. No recursion.

### 1.6 Apply — atomic, idempotent, serialized
```
async def negotiate_and_apply(task, db, client, cal_id) -> Outcome:
    async with self._lock:                      # same lock as schedule_task; NOT reentrant
        proposal = await self.negotiate_placement(...)
        if proposal is None: return IMPOSSIBLE
        if not cfg.auto_apply or any(needs_confirm(m) for m in proposal.moves):
            await persist_proposal(db, proposal, ttl=cfg.proposal_ttl_hours); return PROPOSED
        return await self._apply(proposal, ...)  # inside the lock — no TOCTOU
async def _apply(proposal, ...):
    for m in proposal.moves:                     # moves FIRST (each lands in free space)
        await client.update_event(cal_id, m.event_id, m.new.start, m.new.end)
        await db.update_task(m.task_id, scheduled_start=m.new.start, reschedule_count=+1)  # stays `scheduled`
    event = await client.create_event(cal_id, task.title, proposal.slot..., task_id=task.id)
    await db.transition_task_state(task.id, SCHEDULED)   # alternative_or_rearrange_accepted
    await db.update_task(task.id, scheduled_start=..., calendar_event_id=event.event_id, donna_managed=True)
```
- **Crash safety:** moves applied before T's create; a crash leaves displaced tasks in *valid free slots*; T stays `needs_scheduling` (idempotent re-run).
- **Confirm path:** on accept, `_apply` re-validates inside the lock (re-read busy; verify each `move.old` still matches and `move.new`/T-slot still free); on drift, re-negotiate once and apply only if ≤ the approved cost, else re-propose.

### 1.7 Cascade-shift — `Scheduler.cascade_shift`
Given an intrusion interval (a task auto-extended past estimate; or, once CalendarSync is wired, a new user meeting): move each conflicting Donna event to its next free valid slot.
```
async def cascade_shift(intrusion_start, intrusion_end, db, client, cal_id, cause) -> CascadeResult:
    async with self._lock:
        busy = await self._gather_busy(...)
        conflicted = [ev for ev in busy if movable_donna(ev) and overlaps(ev, intrusion)]; conflicted.sort(start)
        moved, stranded = [], []
        for ev in conflicted:
            if len(moved) >= cfg.max_cascade_depth or not movable(ev): stranded += rest; break
            try: new = self.find_next_slot(task_of(ev), busy - ev, now=intrusion_end)
            except NoSlotFoundError: stranded.append((ev,"deadline_at_risk")); break   # ABORT — would blow ev's hard deadline
            await client.update_event(...); await db.update_task(..., reschedule_count=+1)
            busy = busy - ev + event(ev@new); moved.append(...)
        return CascadeResult(moved, stranded)   # stranded LEFT IN PLACE (visible) + escalated in ONE message
```
Only events overlapping the intrusion are touched (Real-time Adjustment §6.3). Each shift lands in a free slot → **no secondary conflicts**; single pass capped at `max_cascade_depth`. Applied moves stand even if the cascade aborts.

**Overrun trigger (full-loop phase):** on the overdue/reminder tick — task `in_progress` with `now > scheduled_start + estimated_duration` → extend its event by `overrun.extension_step_minutes` (capped, never into blackout), then `cascade_shift(old_end, new_end, "overrun")`. Realizes §6.1.2 "auto-extend and cascade-shift subsequent."

### 1.8 Hook in `auto_scheduler`
Confined to the `except NoSlotFoundError` arm of `AutoScheduler._schedule` (auto_scheduler.py:110):
```
except NoSlotFoundError:
    await db.transition_task_state(task.id, NEEDS_SCHEDULING)   # state FIRST (crash-consistent)
    if not negotiation_gate(task, ti, cfg): notify_needs_scheduling(task); return     # §1.2
    if self._calendar_client is None: notify_needs_scheduling(task); return           # fallback mode
    outcome = await self._scheduler.negotiate_and_apply(task, self._db, self._calendar_client, self._calendar_id)
    # dispatch per §2 (incl. CalendarReadError → fallback alert)
```
Transitioning to `needs_scheduling` *before* negotiating makes every path crash-consistent; APPLIED/accepted paths exit via the existing `alternative_or_rearrange_accepted` transition — **no state-machine change**.

---

## 2. Failure → recovery matrix

| # | Outcome | Calendar | Task state | Notification (via `NotificationService` → blackout/quiet enforced) |
|---|---|---|---|---|
| 1 | Clean slot | Event created | `scheduled` | Existing reminder (unchanged) |
| 2 | Displacement, auto-applied | Moves + create | T `scheduled`; displaced stay `scheduled`, count+1 | Low-pri soft move → **digest line only**; pri ≥4 or hard moved → immediate `NOTIF_RESCHEDULE` |
| 3 | Proposal pending (default) | None yet | T `needs_scheduling` + `negotiation_proposals` row | Message + Accept/Decline/Pick-time buttons (pri max(T,3)) |
| 4 | Proposal accepted | As row 2 after re-validation | `needs_scheduling → scheduled` | In-thread confirm + moved-task notes |
| 5 | Declined / TTL expired | None | stays `needs_scheduling` (→ `backlog` if declined) | Ack; appears in digests/weekly plan |
| 6 | Negotiation impossible | None | `needs_scheduling` | **Never silent.** Options at pri ≥4: next post-deadline slot / which immovable blocks / relax deadline / shorten estimate |
| 7 | Cascade clean | N shifted | all `scheduled`, count+1 | Per-move rule as row 2 ("'X' ran long; shifted 'Y' to 4:15") |
| 8 | Cascade aborted | Applied shifts stand; stranded **left in place** | all `scheduled` | One escalation, pri 5 if hard deadline at risk |
| 9 | `CalendarReadError` | None (fail-closed) | `needs_scheduling` | `dispatch_fallback_alert(component="negotiator")` |
| 10 | Write failure mid-apply | Completed moves stand; failed untouched | untouched stay `scheduled`; T `needs_scheduling` | `dispatch_fallback_alert` + applied/failed list; retry safe |

---

## 3. Surfacing UX
`NegotiationProposalView` in `integrations/discord_views.py` (model on `TaskConfirmationView`). New `negotiation_proposals` table (Alembic): proposal_id, task_id, slot start/end, moves JSON, status, created_at, expires_at — survives restarts; accept re-validates so stale rows are safe. New `NOTIF_RESCHEDULE = "reschedule"` in `notifications/service.py`.

## 4. Config contract (`config/calendar.yaml`, via new `NegotiationConfig` in `config.py`)
```yaml
negotiation:
  enabled: true
  auto_apply: false                   # DEFAULT OFF — propose-and-confirm (2026-06-05 invariant)
  min_displacer_priority: 3
  min_lead_minutes: 60
  max_displacements_per_placement: 1  # slice A; 2 in full loop
  max_cascade_depth: 3
  max_auto_moves_per_task_per_day: 2
  proposal_ttl_hours: 4
  notify_min_priority_moved: 4        # §6.1.2 "none unless priority 4–5"
  cost_weights: {priority: 10, hard_deadline: 20, slack: 8, reschedule: 5, imminence: 2}
  overrun: {enabled: false, extension_step_minutes: 15, max_extension_minutes: 60}
```

## 5. Spec alignment
Realizes §6.1.2 overrun/auto-shift/auto-replace rows and §6.3 Minimize-Rescheduling/Get-It-Done. Blackout/quiet absolutes inherited via `find_next_slot`/`NotificationService`. **Ambiguities resolved:** (1) conflict table is §6.1.2 not §6.3 (both cited precisely). (2) §6.1.2 licenses silent auto-moves but the 2026-06-05 confirmation invariant supersedes → confirm-by-default + `auto_apply` dial-back; rewrite the §6.1.2 / domain tables when shipped. (3) spec silent on unshiftable hard-deadline tasks → leave visible + escalate, never silently unschedule.

## 6. Phasing (each independently shippable)
- **Slice A — single-displacement negotiator, confirm-only.** `_iter_window_valid_slots` refactor; `negotiate_placement` (cap 1); proposal persistence + Discord view; auto_scheduler hook; matrix rows 1,3–6,9. *Build trigger: already met.*
- **Slice B — multi-displacement + auto-apply** (soft P1–2 victims). *Trigger: ≥2 weeks of >90% proposal-accept rate (log `negotiation_proposal_{sent,accepted,declined,expired}`).*
- **Slice C — cascade-shift + overrun detector.** Exposes the primitive CalendarSync's `_handle_conflict` will call; does NOT wire CalendarSync (gated on critique #6/#7). *Trigger: Slice B stable 2 weeks.*

## 7. Test plan (`tests/unit/scheduling/`)
Immovable user event → `None` + zero writes + row-6 notice; read-only-calendar guard; single soft displacement (victim re-place valid + before `latest`); victim selection (vary priority/slack/reschedule_count independently); infeasible victim rejected; downstream hard deadline aborts cascade (earlier moves stand, P5 escalation); termination/no-thrash (structural depth-1; max-auto-moves immovable; double-run no oscillation); lock serialization; stale-proposal re-validation; fail-closed `CalendarReadError`; blackout queueing of `NOTIF_RESCHEDULE`.

## 8. Open decisions for the owner
- **OD-1 Displacement eligibility:** strictly-lower-priority only, or also equal-priority-soft when T is hard (designed default)?
- **OD-2 `auto_apply` default:** off (confirmation invariant, designed) vs spec §6.1.2's silent-for-P≤3 — confirm the triage ruling stands + spec tables get rewritten.
- **OD-3 `min_displacer_priority` default 3:** allow *any* hard-deadline task (P1) to displace (floor=1) vs protect calendar stability (floor=3)?
- **OD-4 Quiet-hours displacement:** may a P5 hard T displace into / re-place victims into 10pm–12am (designed: yes for T, no for victims)?
- **OD-5 Proposal expiry:** expire-to-`needs_scheduling` (designed) vs auto-accept-on-expiry for P5.
- **OD-6 Stranded-conflict rendering:** leave double-booked visibly (designed) vs annotate event title with a conflict marker.

## Critical files for implementation
- `src/donna/scheduling/scheduler.py` — `negotiate_placement`, `negotiate_and_apply`, `cascade_shift`, `_iter_window_valid_slots`
- `src/donna/scheduling/auto_scheduler.py` — the `NoSlotFoundError` hook (line 110) + gate
- `config/calendar.yaml` + `src/donna/config.py` — `NegotiationConfig`
- `src/donna/integrations/discord_views.py` — `NegotiationProposalView`
- `src/donna/notifications/service.py` — `NOTIF_RESCHEDULE`; `src/donna/tasks/database.py` — `negotiation_proposals` + victim queries
