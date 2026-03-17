# Slice 4: Calendar Integration & Basic Scheduling

> **Goal:** Connect to Google Calendar, implement the polling-based sync, and build the basic scheduling engine that places tasks into available time slots.

## Relevant Docs

- `CLAUDE.md` (always)
- `docs/scheduling.md` — Calendar sync strategy, conflict resolution, time constraints, algorithm
- `docs/task-system.md` — Task state machine (backlog → scheduled transitions)

## What to Build

1. **Implement Google Calendar client** (`src/donna/integrations/calendar.py`):
   - Read-write access to personal calendar, read-only for work and family
   - Uses `google-api-python-client` with async wrapper
   - OAuth2 token management (stored encrypted on disk)
   - Create, update, delete events with Donna extended properties (`donnaManaged`, `donnaTaskId`)

2. **Implement calendar sync** (`src/donna/scheduling/calendar_sync.py`):
   - Polls Google Calendar every 5 minutes (configurable)
   - Compares against local mirror in SQLite
   - Detects user modifications to Donna-managed events:
     - Time change → implicit reschedule (update task, increment `reschedule_count`, log correction)
     - Event deleted → move task to backlog, queue notification
   - Detects new user events that conflict with scheduled tasks

3. **Implement basic scheduling engine** (`src/donna/scheduling/scheduler.py`):
   - Given a task in backlog, find the next available time slot that:
     - Respects domain time constraints (work hours, personal time, blackout, etc.)
     - Doesn't overlap existing calendar events
     - Fits the estimated duration
   - Create a Google Calendar event with Donna extended properties
   - Transition task state: backlog → scheduled (via state machine)
   - Update task with `calendar_event_id` and `scheduled_start`

4. **Implement conflict resolution** (basic version):
   - New user event overlaps Donna task → auto-shift task to next available slot
   - Log the conflict and resolution

5. **Write tests:**
   - Unit test: scheduler finds correct slot given a set of existing events and constraints
   - Unit test: time constraint enforcement (no scheduling during blackout, baby time, etc.)
   - Integration test: mock Google Calendar API, verify event creation with correct extended properties

## Acceptance Criteria

- [ ] Calendar client authenticates with Google OAuth2
- [ ] `list_events()` returns events for a date range across all three calendars
- [ ] `create_event()` creates an event with `donnaManaged` and `donnaTaskId` extended properties
- [ ] Calendar sync detects and handles user time changes on Donna events
- [ ] Calendar sync detects and handles user deleting Donna events
- [ ] Scheduler finds correct available slot respecting time constraints
- [ ] Scheduler will not place tasks during blackout (12am–6am) or baby time blocks
- [ ] Task transitions from backlog → scheduled with `calendar_event_id` set
- [ ] Conflict auto-shifts lower-priority task to next available slot
- [ ] Sync polling interval is configurable (not hardcoded)

## Not in Scope

- No weekly planning session
- No daily recalculation
- No dynamic priority escalation
- No dependency chain scheduling

## Session Context

Load only: `CLAUDE.md`, this slice brief, `docs/scheduling.md`, `docs/task-system.md`
