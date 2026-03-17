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
| **Blackout** | **12:00 AM – 6:00 AM (always)** | **No scheduling, no notifications, no contact** |
| **Quiet Hours** | **8:00 PM – 6:00 AM (default)** | **No new scheduling. Urgent (priority 5) only.** |

## Scheduling Algorithm

1. **Weekly Planning (Monday mornings):** Generate proposed week plan. Present to user for review. Lock hard-deadline items first, then fill with flexible tasks.
2. **Daily Recalculation (6:00 AM):** Recalculate today based on previous day's completion, new tasks, calendar changes.
3. **Real-time Adjustment:** New task or reschedule → re-evaluate only affected slots, not entire week.
4. **Minimize Rescheduling:** Prefer inserting into genuinely empty slots before displacing existing tasks. When displacement necessary, move the lowest-priority, most-flexible task.
5. **Get It Done Bias:** Default to scheduling tasks ASAP while respecting constraints. Do not push tasks to "someday."
