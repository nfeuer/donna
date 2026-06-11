# Scheduling Engine — Fable Critique & Redesign Spec

**Date:** 2026-06-11
**Status:** Triaged — pending owner decision on implementation scope (§6)
**Critic:** Fable 5 (adversarial design critique)
**Triage:** Opus (independent verification + disposition)
**Related:** `spec_v3.md §6` (Scheduling Engine), `§3.7` (concurrency / calendar-write serialization), `§7.1.1`/`§7.2` (agent hierarchy — Challenger drift), `docs/superpowers/specs/2026-06-05-challenger-and-scheduling-intake-design.md`, `docs/superpowers/specs/2026-05-11-task-scheduling-flows-design.md`, `docs/superpowers/plans/2026-06-11-fable-design-critique-program.md` (the program this is Wave A of)

> Wave A, subsystem 2. Fable's full 14-finding critique is preserved in the
> session record; this captures the **verified** findings, their **triage
> disposition**, and the recommended sequence. No code changed.

---

## 1. Executive finding (verified)

**The scheduling engine schedules in the wrong timezone, and its catastrophic
failure modes are dormant only because the calendar-sync integration was never
wired.** The deterministic core (routing gate, slot-finder shape, `needs_scheduling`
state) is sound, but every seam to reality is broken:

- **Live, user-facing S1:** placements are computed in **UTC** and validated against
  config windows that are **local time** — so the "absolute, no-exceptions"
  12am–6am blackout is enforced as 0–6 *UTC* (8pm–2am ET), a **work task can legally
  land at ~4 AM Eastern**, and every time shown to the user is wrong (off by the
  UTC offset, plus an extra hour each DST season).
- **Dormant landmines:** `CalendarSync` (mass-unschedule on a transient Google 500;
  auto-move of committed items with no confirmation) is **dead code** — never
  constructed in production. These must be fixed *before* anyone wires it.
- **Silent loss:** the weekly-plan "confirm" reply has **no caller**; recurring
  intents are confirmed with a confident "Done." while nothing is created;
  `needs_scheduling` is excluded from the weekly plan and every digest.

The single most dangerous *live* property is **time-correctness**; the most dangerous
*latent* one is silent displacement of committed calendar items.

## 2. Verification log (Opus, independent of Fable)

| # | Claim | Verified? | Evidence |
|---|---|---|---|
| **#1** | Local-hour windows enforced on the UTC clock; blackout violable; wrong times shown | ✅ Confirmed | `scheduler.py:93` `now=datetime.now(tz=UTC)`; `_is_valid_slot` uses raw `check_dt.hour` (`:127`) vs `tw.blackout`/`tw.quiet_hours` which `calendar.yaml` declares local; **zero** `ZoneInfo`/`astimezone` in `src/donna/scheduling/` (grep); events created `timeZone: "UTC"` (`calendar.py:270`). |
| **#2** | `CalendarSync` is dead code | ✅ Confirmed | Only `CalendarSync(` reference in `src/` is its own docstring (`calendar_sync.py:40`); no production constructor, no `run_forever` wiring. §6.1.1 sync does not run. |
| **#3** | Weekly-plan confirm reply has no caller | ✅ Confirmed | `handle_plan_reply` defined `weekly_planner.py:261`, zero callers in `src/` (grep). |
| **#4** | `find_next_slot` ignores deadline/window/constrained bounds | ✅ Confirmed | `scheduler.py:71-113` references neither `task.deadline` nor `task.time_intent`; flat 14-day horizon; first-valid-slot. |
| **#5** | Placement checks only the personal calendar; books blind on read failure | ✅ Confirmed | `scheduler.py:227` lists single `calendar_id`; `:228-230` `except → existing_events=[]` then proceeds to `create_event` with no alert. |
| **#8** | §3.7 "writes serialized through async queue" is fiction; RMW race | ✅ Confirmed | `calendar.py` create/update/delete are bare `to_thread`, no lock/queue; `scheduler.py:227→235` awaits between list and create. |

**Conclusion:** Fable's critique is accurate and evidence-grounded. Its own
live-vs-dormant downgrade (recognizing #6/#7 are inert because of #2) is correct and
materially changes prioritization.

## 3. Triage dispositions

Legend: **ACCEPT** · **ESCALATE** (scope/posture for owner) · **DEFER** (trigger-gated) · **KEEP** (existing design is right).

### LIVE S1/S2 — the placement path actually runs (AutoScheduler → `Scheduler.schedule_task`)
| # | Finding | Disposition |
|---|---|---|
| 1 | **Timezone: schedule in UTC vs local-hour windows; blackout violable; wrong times shown; DST unhandled** | **ACCEPT (top priority)** — thread `ZoneInfo(cfg.timezone)` into the scheduler; convert candidates to local wall-clock before window checks; create events with `timeZone: cfg.timezone`; DST guards (skip nonexistent spring-forward, `fold=0` for fall-back); render user copy in local time. |
| 4 | **Slot finder ignores deadline / window / constrained bounds; flat 14-day horizon** | **ACCEPT** — interim guard (not the full Plan-2 negotiator): clamp search to `derive_deadline(ti)` + `earliest`/`latest`/weekday/time-of-day when present; past-deadline ⇒ `NoSlotFoundError` ⇒ `needs_scheduling` + notify. |
| 5 | **Busy-check only personal calendar; books blind on read failure (fail-open)** | **ACCEPT** — busy-set = union of all configured calendars via one `freebusy.query`; on read failure **abort** (→ `needs_scheduling` + `dispatch_fallback_alert`), never book against an empty calendar. |
| 8 | **No write serialization; list→create RMW race (§3.7 unimplemented)** | **ACCEPT** — one `asyncio.Lock` around list→find→create; reconcile or amend spec §3.7. |
| 12 | **Six fallback paths log without alerting (violates CLAUDE.md)** | **ACCEPT** — `dispatch_fallback_alert` at each; for the two list-failure sites, change semantics to abort (per #5). |

> **Highest-leverage:** findings #1, #4, #5, #8, #12 all exist because four call sites
> each re-implement half of placement. Fable's recommendation — **one `PlacementService`
> choke point** that owns tz-aware validation, deadline/window clamping, the
> all-calendars busy union with abort-on-failure, the lock, and fallback alerting —
> fixes all five at a single seam and gives Plan 2's negotiator its primitive.

### DORMANT — code exists but is unwired; fix *before* wiring, do not wire yet
| # | Finding | Disposition |
|---|---|---|
| 2 | `CalendarSync` never constructed in production (one-way integration: Donna writes but never reads user reality back) | **DEFER (wiring) / ESCALATE** — wiring is desirable but **unsafe until #6 + #7 land**. Owner decision on whether to schedule the full sync-enablement work. |
| 6 | A single failed calendar fetch → absence read as deletion → mass-unschedules every task + wipes mirror | **ACCEPT (as prerequisite to #2)** — distinguish "fetch failed" from "event absent"; skip diff+prune + alert on fetch failure; require N confirmed absences (or `syncToken`). |
| 7 | `_handle_conflict` auto-moves committed items with **zero** confirmation/notification — violates the design's "any move requires user confirmation" invariant; `_handle_event_deleted` bypasses the state machine | **ACCEPT (as prerequisite to #2)** — implement the full conflict-resolution strategy set honoring the confirmation invariant (see §4); route status changes through `transition_task_state`. **Domain doc conflict tables must be rewritten — they describe the superseded auto-move behavior.** |

### DEAD-END / SILENT LOSS — live false-assurance bugs (cheap, high-trust-impact)
| # | Finding | Disposition |
|---|---|---|
| 3 | Weekly-plan confirm has no caller; dry-run self-collides (N tasks → same slot); apply is stale and reports success on failure | **ACCEPT** — route plan replies (reuse `TaskConfirmationView` buttons); accumulate proposed slots in the dry-run; re-validate at apply inside the lock; persist proposals to a table; report real success/fail. |
| 10 | Recurring intent → AutoScheduler logs+skips, but Donna replies **"Done."** — silent loss with false assurance | **ACCEPT (cheap)** — honest copy ("noted the cadence; recurring wiring pending, I'll remind you manually") + `dispatch_fallback_alert` on the skip path. **Challenges TI-FU2** ("stub" undersells — it *lies*). |
| 9 | `needs_scheduling` excluded from weekly plan + all digests; no notification for non-Discord channels | **ACCEPT (cheap)** — add to weekly-planner candidates + morning/EOD digests; notify on the AutoScheduler NoSlot path for all channels. |

### S2/S3 — correctness + hygiene
| # | Finding | Disposition |
|---|---|---|
| 11 | No boot sweep / replay; event created before DB write; orphan events never GC'd | **ACCEPT** — boot-time re-run of `on_task_created` for time-bound backlog + `needs_scheduling`; orphan reconciler; write `scheduled_start` before `create_event` (or a placement-intent row). |
| 13 | Config drift: quiet hours defined 3 ways (config 22–24, domain 20–24, spec 20–06); planner/recalc fire-hours + TTL + thresholds hardcoded in UTC | **ACCEPT** — move to `calendar.yaml`; pick one quiet-hours definition (config wins; sync spec + domain doc); fire-hours in `cfg.timezone`. |
| 14 | Naive-datetime `TypeError` in routing gate could resurrect the strand bug (bus swallows the raise) — **SUSPECTED** | **ACCEPT (with confirm)** — normalize at the boundary (`_parse_dt` localizes naive → `cfg.timezone`); add `TimeIntent.validate()` (the machine-checkable `constrained` grammar); bus emits a fallback alert on subscriber failure. |

### DEFER — sound but trigger-gated
- **Negotiation/rearrange loop (Plan 2)** — trigger: first `needs_scheduling` entries in digests after #4+#9 land.
- **Cascade-shift auto-apply / soft-P1–2 auto-yield (the "dial back" of #7)** — config default-off; trigger: ≥2 weeks of >90% yield-accept data.
- **Extended Work / Emergency Work windows (G-12)** — correctly deferred; user-activated. Trigger: user asks to open one.
- **`syncToken` incremental sync** — the 10-line skip-on-fetch-failure guard (#6) suffices; trigger: deletion-detection latency becomes measured.

### KEEP — existing design is right; a naive critic would wrongly "fix" these
- Deterministic, **LLM-free `routing_gate`** — correctness never leaks onto a model.
- **Live Google reads at placement** (not the SQLite mirror) — the mirror is a diff
  cache; the fix is *more* live reads (all calendars, locked), not promoting the mirror.
- **Blackout-absolute at the notification-service layer** with config-loaded tz.
- **`needs_scheduling` as an explicit state** — needs consumers (#9), not removal.
- **Confirm-before-apply** weekly-plan design — the gate exists (`handle_plan_reply`); the wiring is missing. Don't bolt on a second gate.
- **`deadline`/`deadline_type` derived from `time_intent`** for back-compat.

## 4. Highest-leverage change

**Introduce one `PlacementService.place()` choke point** that every writer
(`AutoScheduler`, `WeeklyPlanner._apply_proposal`, `CalendarSync._handle_conflict`,
the future negotiator, `ReplyHandler` reschedules) calls. It owns, in one place:
(1) tz-aware local wall-clock window validation + DST rules (#1), (2) deadline/window/
constraint clamping (#4), (3) the all-calendars busy union with abort-on-read-failure
(#5), (4) the `asyncio.Lock` that makes §3.7 true (#8), (5) fallback alerting (#12).
One seam closes five live findings and yields the primitive Plan 2 needs.

## 5. Spec-sync obligations (when implemented)

- `spec_v3.md §6.2` — time windows are local; document the tz-aware enforcement + DST.
- `spec_v3.md §3.7.1` — either implement the calendar-write serialization (the lock) or amend the claim.
- `spec_v3.md §6.1.2` + **`docs/domain/scheduling.md` conflict tables** — rewrite to the
  "any move of a committed item requires confirmation" invariant (design 2026-06-05 wins);
  the current auto-move tables are superseded.
- `docs/domain/scheduling.md` "Timezone" section — currently claims tz is threaded
  through all components; that is false for the scheduling package. Correct it.
- `followups.md` — TI-FU2 reframed (recurring path *lies*, not inert); add a Wave-A-2
  residue entry.

## 6. Owner decision required (escalation)

The timezone fix is an unambiguous correctness bug (ACCEPT), but **scope** — how much to
implement now, and whether to take on the dormant-sync hardening (#6/#7) needed before
`CalendarSync` can ever be wired — is the owner's call. Asked separately.

---

## Appendix — program mechanics (Wave A-2)

- The PROVEN-vs-SUSPECTED tagging and spec-drift flagging (carried over from the Cost
  run) again paid off: Fable marked #14 SUSPECTED and correctly downgraded #6/#7 to
  dormant after discovering #2 — preventing a triage that would have over-prioritized
  inert code.
- Fable challenged two existing followups (G-11 "basic overlap detection" is *shipped
  but inert*; TI-FU2's "stub" *actively lies*) — the uncorrelated-judgment payoff.
