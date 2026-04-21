# Scheduling Engine

> Split from Donna Project Spec v3.0 — Sections 6.1–6.3

## Calendar Integration

Google Calendar is the single source of truth. Read-write on personal calendar, read on work and family calendars. All three are Google Calendar — no ICS workarounds needed.

### Calendar Sync Strategy

Polling-based sync with change detection. Polls every 5 minutes (configurable). Compares current calendar state against local mirror in SQLite.

Donna-managed events tagged with Google Calendar extended properties:

```json
{
  "extendedProperties": {
    "private": {
      "donnaManaged": "true",
      "donnaTaskId": "<task-uuid>"
    }
  }
}
```

Invisible to user in Calendar UI, readable by API. Distinguishes Donna events from user-created ones.

### Handling User Modifications to Donna Events

| User Action | System Response |
|-------------|----------------|
| **Time change** | Treated as implicit reschedule. `scheduled_start` updated, `reschedule_count++`. Logged as correction (feeds preference learning). No notification sent. |
| **Event deleted** | Task moved to `backlog`. User notified next interaction: "I noticed you removed [task]. Reschedule or leave in backlog?" |
| **New user event conflicts** | Donna yields. Conflict resolution applies. Donna event auto-shifted to next slot. |

### Conflict Resolution Rules

| Conflict Type | Resolution | Notification |
|--------------|------------|-------------|
| New meeting overlaps scheduled task | Auto-shift task to next slot | None unless priority 4–5 |
| Two meeting invitations at same time | Flag user immediately | SMS or app notification with options |
| High-priority vs low-priority in same slot | Auto-replace, reschedule lower | Include in daily digest |
| Task runs over estimated time | Auto-extend, cascade-shift subsequent | Notify if impacts hard-deadline task |
| User cannot complete task | Accept reschedule or auto-find next slot | Confirm new time via same channel |

## Time Constraints

| Time Block | Hours | Tasks Allowed |
|-----------|-------|---------------|
| Work | 8:00 AM – 5:00 PM (weekdays) | Work domain tasks, meetings |
| Extended Work | 5:00 PM – 7:00 PM (weekdays, optional) | Work overflow, side projects |
| Personal Time | 5:00 PM – 8:00 PM | Personal tasks, R&R, projects, study |
| Baby Time | Per calendar blocks | Family tasks only; never schedule other work |
| Food | Per calendar blocks | Protected; no tasks scheduled |
| Emergency Work | 10:00 PM – 12:00 AM (user-activated) | Only high-priority tasks user explicitly opens |
| Weekends | 6:00 AM – 8:00 PM | Personal and family tasks |
| **Blackout** | **12:00 AM – 6:00 AM (always)** | **No scheduling, no notifications, no contact. No exceptions.** |
| **Quiet Hours** | **8:00 PM – 12:00 AM (default)** | **No new scheduling. Urgent (priority 5) only.** |

### Precedence: Blackout Overrides Quiet Hours

Blackout (12am–6am) and Quiet Hours (8pm–12am) overlap conceptually but have strict precedence:

- **Blackout is absolute.** During blackout hours, nothing goes out — not even priority 5. No scheduling, no notifications, no contact of any kind. Enforced at the notification service level as a hard block.
- **Quiet Hours are soft.** During quiet hours (8pm–12am), only priority 5 urgent notifications break through. New scheduling is suppressed.
- **During the overlap (12am–6am), blackout wins.** If a priority 5 event triggers at 2am, it queues and fires at 6:00 AM when blackout ends. The notification service holds the message until the blackout window closes.

## Conflict Resolution Strategy

### Calendar Conflicts

| Conflict Type | Resolution | Notification |
|--------------|------------|-------------|
| New meeting overlaps scheduled task | Auto-shift task to next slot | None unless priority 4–5 |
| Two meeting invitations at same time | Flag user immediately | SMS or app notification with options |
| High-priority vs low-priority in same slot | Auto-replace, reschedule lower | Include in daily digest |
| Task runs over estimated time | Auto-extend, cascade-shift subsequent | Notify if impacts hard-deadline task |
| User cannot complete task | Accept reschedule or auto-find next slot | Confirm new time via same channel |

### Data Conflicts (Supabase Sync)

- **SQLite is always the source of truth.** On sync conflict, local wins and remote is overwritten.
- Every conflict is logged to `donna_logs.db` with event type `sync.conflict` for audit.
- On Supabase recovery after downtime, a full reconciliation sync runs from SQLite → Supabase.

### State Machine Conflicts (Concurrent Transitions)

- **Phase 1–2 (single-threaded asyncio):** Task state transitions are atomic — read → validate → write in a single async function with a SQLite transaction. No interleaving possible.
- **Worker pool (agent dispatcher):** Optimistic locking on task state. Workers read current state + version → validate transition → write with version check → retry on version mismatch. The orchestrator serializes conflicting writes. See `src/donna/orchestrator/dispatcher.py`.

### Agent Conflicts

- **One agent per task at a time.** The orchestrator enforces this constraint. If agent B needs a task currently locked by agent A, agent B's request is queued until agent A completes or times out.
- Agent outputs are written to the task record only through the orchestrator's internal API, never directly.

## Scheduling Algorithm

1. **Weekly Planning (Monday mornings):** Generate proposed week plan. Present to user for review. Lock hard-deadline items first, then fill with flexible tasks.
2. **Daily Recalculation (6:00 AM):** Recalculate today based on previous day's completion, new tasks, calendar changes.
3. **Real-time Adjustment:** New task or reschedule → re-evaluate only affected slots, not entire week.
4. **Minimize Rescheduling:** Prefer inserting into genuinely empty slots before displacing existing tasks. When displacement necessary, move the lowest-priority, most-flexible task.
5. **Get It Done Bias:** Default to scheduling tasks ASAP while respecting constraints. Do not push tasks to "someday."
