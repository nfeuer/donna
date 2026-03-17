# Slice 5: Reminders, Overdue Detection & Morning Digest

> **Goal:** Donna starts actively pursuing the user. Reminders before tasks, nudges when overdue, and a morning digest that sets up the day. This is where Donna stops being a passive system and starts being a personal assistant.

## Relevant Docs

- `CLAUDE.md` (always)
- `docs/notifications.md` — Notification types, escalation tiers
- `docs/scheduling.md` — Time constraints (quiet hours, blackout enforcement)
- `docs/task-system.md` — Task states, overdue detection

## What to Build

1. **Implement reminder scheduler** (`src/donna/notifications/reminders.py`):
   - Background async task that checks scheduled tasks every minute
   - 15 minutes before `scheduled_start` → send reminder to Discord `#donna-tasks`
   - Reminder format: "⏰ '[task title]' starts in 15 minutes. Duration: [X] min."
   - Respects quiet hours (8pm–6am) and blackout (12am–6am) — reminders for tasks starting during quiet hours are sent at the boundary

2. **Implement overdue detection** (`src/donna/notifications/overdue.py`):
   - Background task that runs every 15 minutes
   - If task is `scheduled` or `in_progress` and current time > `scheduled_start` + `estimated_duration` + 30 min buffer → trigger overdue nudge
   - Overdue nudge on Discord: "It's [time] and you haven't touched '[task]'. Did you finish it or should I find time tomorrow?"
   - If user replies "done" → transition to `done`. If "reschedule" → transition to `scheduled` (find next slot)

3. **Implement morning digest** (`src/donna/notifications/digest.py`):
   - Runs at 6:30 AM daily (configurable, respects blackout)
   - Gathers: today's calendar events, tasks due today, carry-overs from yesterday, overdue tasks, cost summary
   - Sends data to LLM with `prompts/morning_digest.md` template
   - Posts result to Discord `#donna-digest` as an embed
   - Degraded mode: if LLM unavailable, send raw data in template format

4. **Implement notification service** (`src/donna/notifications/service.py`):
   - Central dispatch: takes a notification type + content, routes to correct channel
   - Phase 1: Discord only (all channels). SMS/email/phone added in Slice 7.
   - Enforces blackout hours at the service level (hard block, not per-channel)
   - Logs every outbound message

5. **Write tests:**
   - Unit test: reminder triggers at correct time relative to `scheduled_start`
   - Unit test: overdue detection fires after buffer period
   - Unit test: blackout enforcement blocks notifications between 12am–6am
   - Unit test: morning digest assembles correct data payload
   - Integration test: mock LLM, verify digest output matches schema

## Acceptance Criteria

- [ ] Reminder sent to Discord 15 minutes before scheduled task start
- [ ] Overdue nudge sent 30 minutes after task should have ended
- [ ] User reply "done" in Discord transitions task to `done` state
- [ ] User reply "reschedule" triggers rescheduling to next available slot
- [ ] Morning digest posts to `#donna-digest` at 6:30 AM as Discord embed
- [ ] Morning digest includes: calendar events, tasks due, carry-overs, overdue, cost summary
- [ ] Degraded digest works when LLM is unavailable (raw data, no persona)
- [ ] No notifications sent during blackout (12am–6am)
- [ ] Quiet hours (8pm–6am) allow only priority 5 notifications
- [ ] All outbound notifications logged with channel, type, timestamp

## Not in Scope

- No escalation tiers (Tier 1 only — Discord)
- No SMS/email/phone channels
- No weekly planning session
- No proactive task capture prompts (post-meeting, evening check-in)

## Session Context

Load only: `CLAUDE.md`, this slice brief, `docs/notifications.md`, `docs/scheduling.md`, `prompts/morning_digest.md`
