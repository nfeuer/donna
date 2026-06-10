# Automation Alert Delivery — Design

**Date:** 2026-06-10
**Status:** Approved (design); implementation pending
**Spec refs:** `spec_v3.md §6.9` (automation dispatch), notifications section (blackout/quiet windows)

## Problem

The "COS Utility Shirt L Under 60" automation (`product_watch`, schedule `0 9 * * *`)
fired correctly on 2026-06-10 — run output `price_usd: 59.4, in_stock: true,
triggers_alert: true` — and the run was recorded `alert_sent: 1`, yet the user
received no Discord DM.

### Root cause

Three compounding defects:

1. **Schedule timezone mismatch (why nothing arrived).**
   `CronScheduleCalculator.next_run` (`src/donna/automations/cron.py`) evaluates
   cron expressions in **UTC**. `0 9 * * *` fires at 09:00 UTC = **05:00
   America/New_York** (EDT). That is inside the absolute blackout window
   (12 AM–6 AM, `config/calendar.yaml`). During blackout,
   `NotificationService.dispatch_dm` (`src/donna/notifications/service.py:191`)
   **queues** the DM and returns `False` — it never sends. The rest of Donna
   treats schedule hours as Eastern (digest, skills, SMS configs all annotate
   "hours are Eastern"); automation crons are the outlier.

2. **`alert_sent` is recorded untruthfully (why the run data misled the user).**
   `AutomationDispatcher._dispatch_alert_to_channel`
   (`src/donna/automations/dispatcher.py:486`) **discards** the boolean returned
   by `dispatch_dm`/`dispatch_sms`/etc. The dispatcher sets `alert_sent = True`
   whenever the channel calls do not raise (`dispatcher.py:266-269`). A queued
   (blackout) DM returns `False` without raising, so the run is recorded as
   delivered when it was not. This violates the project's "no silent failures"
   convention.

3. **Blackout queue is in-memory and lossy (why even the deferred path failed).**
   Queued DMs live in an in-memory `deque` on the `NotificationService` instance,
   flushed once at 6 AM Eastern by the reminder loop
   (`src/donna/notifications/reminders.py:74-80`). The dispatcher and reminder
   scheduler share the same instance (`cli_wiring.py:730`, `:2339`), so the queue
   is shared — but a process restart between 05:00 and 06:00 ET (common during
   deploys) silently drops it.

### Confirmed non-issues

- **Alert-condition evaluation is correct.** `alert_conditions` is
  `{"field": "triggers_alert", "op": "==", "value": true}`; the capability set
  `triggers_alert: true`; `AlertEvaluator` matched.
- **The DM target is correct.** `automation.user_id` = `209121227925618688` =
  `DONNA_OWNER_DISCORD_ID`.
- **Conversational replies already bypass blackout.** Task-created confirmations
  and clarification follow-ups are sent directly via `message.channel.send()` /
  `thread.send()` in `discord_bot.py`'s `on_message` handler — they never pass
  through `NotificationService` blackout gating. The user's "Donna can still
  reply to me in a thread at night" requirement is already satisfied by the
  existing architecture; this design only needs to **preserve** it.

## Goals

1. An automation scheduled for "9 AM" runs at 9 AM in the user's timezone.
2. User-initiated automation alerts, system/debug messages, and user-set
   reminders are delivered regardless of blackout/quiet hours.
3. Proactive, unsolicited nudges (overdue, scheduling) continue to respect
   blackout/quiet hours.
4. `alert_sent` reflects actual delivery; deferred/blocked deliveries are never
   silent.

## Non-goals

- Conversation-state / "engaged within N minutes" tracking. Reactive replies
  already bypass gating; no timer is needed.
- Durable (DB-backed) notification queue. With automation alerts exempt from
  blackout, the in-memory queue no longer carries time-sensitive alerts. Tracked
  as a follow-up, not in scope here.
- Changing SMS night-silence. SMS remains always-silent during blackout/quiet
  regardless of type.

## Design

### 1. Schedule cron in the user's timezone

`CronScheduleCalculator.next_run` evaluates the cron expression against a
timezone-aware base time in the configured zone (`calendar.yaml.timezone`,
`America/New_York`) and returns the result converted to UTC. `croniter` honors
DST when given a tz-aware base, so `0 9 * * *` resolves to 9 AM ET year-round.

- The calculator receives the zone (injected from `CalendarConfig`) rather than
  hardcoding UTC.
- **One-time migration:** after the change, recompute `next_run_at` for every
  `on_schedule` automation so existing rows (created under UTC interpretation)
  realign. A startup/CLI routine iterates active automations and rewrites
  `next_run_at = next_run(expression, after=now)`. Existing schedules shift by
  the UTC offset (~4–5h); acceptable since they were previously misaligned.

### 2. Type-based blackout / quiet policy (config-driven)

New `config/notifications.yaml` lists the types **exempt** from each window;
everything else falls through to a default that respects both. This is an
exempt-list (not a full enumeration) because proactive nudges use many distinct,
evolving type strings (`overdue`, `post_meeting`, `evening_checkin`,
`stale_task`, `afternoon_inactivity`, digest sub-types, …). A default-respects
policy means any current or future proactive type is gated correctly without a
config edit; only the deliberately-exempt set is special-cased.

```yaml
# Per-type blackout/quiet-hours policy.
# Types NOT listed below respect both windows (the safe default):
#   - blackout (12 AM–6 AM): absolute window, all priorities queued
#   - quiet hours: priority < 5 queued
# Listed types are exempt and deliver regardless of window.
notification_policy:
  blackout_exempt:
    - reminder            # user-set for a specific time
    - automation_alert    # user-configured deliberately
    - automation_failure  # ops signal
    - debug               # ops signal
  quiet_exempt:
    - reminder
    - automation_alert
    - automation_failure
    - debug
```

- `NotificationService.dispatch` and `dispatch_dm` check whether the
  notification type is in `blackout_exempt` / `quiet_exempt` before applying the
  corresponding gate. Any type not listed (e.g. `overdue`, `digest`,
  `post_meeting`, `evening_checkin`, `stale_task`, `afternoon_inactivity`)
  respects both windows — fail safe / least surprising.
- `automation_alert`, `automation_failure`, `debug`, and `reminder` are exempt
  from both blackout and quiet; the two lists are identical today but kept
  separate so a type can later respect one window and not the other.
- `dispatch_sms` is unchanged: it keeps its own unconditional blackout/quiet
  check (channel-level hard rule), so an exempt type still cannot fire SMS at
  night.
- Policy is loaded into `NotificationService` at construction (alongside
  `CalendarConfig`), per the "config over code" principle.

### 3. Truthful `alert_sent`

- `_dispatch_alert_to_channel` returns the `bool` from the underlying
  `dispatch_*` call (and `False` for the misconfigured/unknown-channel branches
  that currently only log).
- The dispatcher aggregates per channel: `alert_sent = True` only if **at least
  one** channel reported actual delivery.
- When a channel reports `False` (queued, blocked, or misconfigured), the
  dispatcher emits a `debug` notification (which is itself blackout-exempt) so
  the deferred/failed delivery is surfaced, not silent.

### 4. Documentation

- `config/calendar.yaml`: revise the "blackout is absolute — no exceptions"
  comment to describe the new per-type policy.
- `spec_v3.md §6.9` and the notifications section: document tz-local cron
  evaluation and the type-based window policy.
- `docs/superpowers/specs/followups.md`: log the deferred durable-queue item.

## Components touched

| Component | Change |
|---|---|
| `src/donna/automations/cron.py` | Evaluate cron in configured tz; return UTC |
| `src/donna/automations/dispatcher.py` | Propagate delivery bool; truthful `alert_sent`; debug alert on deferral |
| `src/donna/notifications/service.py` | Consult per-type policy in `dispatch`/`dispatch_dm` |
| `src/donna/config.py` | Load `notifications.yaml` policy |
| `config/notifications.yaml` (new) | Per-type window policy |
| `config/calendar.yaml` | Doc update |
| migration routine | Recompute `next_run_at` for `on_schedule` automations |

## Data flow (after fix)

```
cron "0 9 * * *"  --(evaluate in America/New_York)-->  next_run 13:00 UTC
  -> dispatcher runs at 9 AM ET (outside blackout)
  -> AlertEvaluator: triggers_alert == true -> fires
  -> _dispatch_alert_to_channel("discord_dm") -> dispatch_dm(priority=3)
       automation_alert in blackout_exempt -> send immediately
       -> bot.send_dm() -> returns True
  -> alert_sent = True (truthful)
```

## Testing

- **cron.py:** `0 9 * * *` with base in winter (EST) and summer (EDT) resolves to
  14:00 / 13:00 UTC respectively (DST correctness); naive base still handled.
- **service.py:** during blackout, `automation_alert`/`automation_failure`/
  `debug`/`reminder` send; `overdue`/`digest`/`stale_task` (and any unlisted
  type) queue. During quiet hours with priority < 5, same split. SMS stays
  night-silent for all types. Unlisted type respects both (default).
- **dispatcher.py:** queued DM (mock `dispatch_dm` -> False) yields
  `alert_sent == False` and emits a debug notification; successful DM yields
  `alert_sent == True`; multi-channel where one delivers -> `alert_sent == True`.
- **migration:** existing `on_schedule` rows get `next_run_at` recomputed in
  local tz; `manual`/`event` triggers untouched.

## Risks / considerations

- **Relaxes a documented invariant.** Blackout was "absolute, no exceptions, not
  even priority 5." It is now type-scoped. Documented in `calendar.yaml` and
  `spec_v3.md`.
- **Cutover double-fire/skip.** Mitigated by the one-time `next_run_at`
  recompute; run once, post-deploy.
- **In-memory queue still lossy** for the types that *do* respect blackout
  (overdue/scheduling/digest). Acceptable: those are low-urgency proactive
  nudges; durable queue deferred to follow-up.
