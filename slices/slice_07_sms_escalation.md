# Slice 7: SMS Channel & Escalation Tiers

> **Goal:** Add SMS as a second input/output channel via Twilio. Implement escalation tiers so overdue tasks can't be silently ignored. Implement conversation context tracking for multi-turn SMS interactions.

## Relevant Docs

- `CLAUDE.md` (always)
- `docs/notifications.md` — SMS integration, escalation tiers, conversation context store

## What to Build

1. **Implement Twilio SMS integration** (`src/donna/integrations/twilio_sms.py`):
   - Outbound SMS: send notifications, reminders, nudges
   - Inbound SMS: receive via Twilio webhook, parse as task input or conversation reply
   - Webhook endpoint on the orchestrator: `POST /sms/inbound`
   - Rate limit: max 10 outbound SMS per day (prevent runaway notifications)

2. **Implement conversation context store** (uses `conversation_context` table from Slice 1):
   - When agent sends a question via SMS → create active context with `task_id`, `agent_id`, `expires_at`
   - Inbound SMS routing logic:
     - Active context exists for this user on SMS → route to that context's agent
     - Multiple active contexts → disambiguate: "Which task: (1) [A] or (2) [B]?"
     - No active context → treat as new task input
   - Contexts expire after 24 hours. On expiry → re-prompt with fresh TTL

3. **Implement notification escalation tiers** (`src/donna/notifications/escalation.py`):
   - Tier 1: Discord message → wait 30 min
   - Tier 2: SMS text → wait 1 hour
   - Tier 3: Email with "ACTION REQUIRED" subject → wait 2 hours (email deferred to Slice 8)
   - Tier 4: Phone call TTS → only for priority 5 or budget emergencies, max 1/day (deferred)
   - Escalation resets when user acknowledges on any channel
   - "Busy, will handle later" → back off 2 hours

4. **Update notification service** to route through escalation tiers:
   - Overdue nudges now escalate through tiers instead of single Discord message
   - Budget alerts escalate immediately to Tier 2 (SMS)

5. **Write tests:**
   - Unit test: inbound SMS correctly routes to active conversation context
   - Unit test: escalation advances through tiers after timeout periods
   - Unit test: acknowledgment on any channel resets escalation
   - Unit test: "busy" response backs off for 2 hours
   - Integration test: mock Twilio API, verify SMS send/receive cycle

## Acceptance Criteria

- [ ] Outbound SMS sent via Twilio API
- [ ] Inbound SMS webhook receives and parses messages
- [ ] Inbound SMS with active context routes to correct agent/task
- [ ] Inbound SMS without context creates new task
- [ ] Escalation progresses: Discord → SMS after 30 min unanswered
- [ ] User acknowledgment on Discord resets SMS escalation
- [ ] "Busy" reply backs off escalation for 2 hours
- [ ] Conversation contexts expire after 24 hours
- [ ] SMS rate limited to max 10/day
- [ ] Blackout hours (12am–6am) block all SMS

## Not in Scope

- No email channel yet (Slice 8)
- No phone call TTS yet
- No Twilio voice
- No proactive task capture via SMS

## Session Context

Load only: `CLAUDE.md`, this slice brief, `docs/notifications.md`
