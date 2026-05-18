# Notification & Escalation System

> Split from Donna Project Spec v3.0 — Sections 10, 11

> **Status (Wave 1):** NotificationService is now instantiated in the
> orchestrator process on startup, wired with the live DonnaBot and the
> calendar config. Previously only tests constructed it; production code
> now relies on it for automation alerts and skill-system warnings.
>
> **Status (Wave 2):** DM delivery path added. `BotProtocol.send_dm()`
> and `NotificationService.dispatch_dm()` route per-user notifications
> (automation alerts, price watches) directly to the requesting user's
> Discord DMs instead of a shared channel. Same blackout/quiet-hours
> gating as channel dispatch. Queued DMs are replayed via `flush_queue()`.

## Input Channels

| Channel | Implementation | Cost | Priority |
|---------|---------------|------|----------|
| Discord Bot | Bot in dedicated server/channel. discord.py with message intents. Self-hosted. | Free | P0 — cross-device, already installed |
| SMS / Text | Twilio number. Parsed by LLM. | $1–2/mo | P0 — fastest capture |
| Desktop App | Flutter desktop. WebSocket to orchestrator. | Free | P1 — primary workstation |
| Web/Mobile App | Flutter web/PWA. Firebase Hosting. | Firebase free tier | P1 — mobile access |
| Email Forwarding | Dedicated email alias. Forwarded emails parsed for tasks. | Free | P2 — capture from email threads |

## Discord Integration

Dedicated Donna category in existing Linux server alert Discord:

- **#donna-tasks**: Task capture and responses. Multi-turn PM interrogations use Discord threads. Thread ID provides natural context association.
- **#donna-digest**: Morning and evening digests. Clean chronological record.
- **#donna-agents**: Agent activity, completion summaries, cost per task.
- **#donna-debug**: System health alerts, cost warnings, errors, circuit breaker status.

Full bot (not webhooks) for bidirectional communication. `discord.py` with message intent.

2000-char limit handled via message splitting or embeds (up to 6000 chars across fields). Morning digests use embeds.

## Conversation Context Management

### Discord: Thread-Based

Agent opens a Discord thread on the original task message. User replies in-thread. Bot routes by thread ID. No custom context store needed.

### SMS/Email: Context Store

`conversation_context` table in SQLite:

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Context identifier |
| user_id | String | User being interrogated |
| channel | Enum | sms \| email \| slack |
| task_id | UUID | Task being interrogated |
| agent_id | String | Agent that initiated |
| questions_asked | JSON | Questions sent to user |
| responses_received | JSON | Responses received |
| status | Enum | active \| expired \| completed |
| created_at | DateTime | Start |
| expires_at | DateTime | Sliding: 24h from last activity. Hard cap: 72h from creation. Day-boundary reset applies. |
| hard_expires_at | DateTime | Absolute: 72 hours from creation. Never extended. |
| last_activity | DateTime | Last message sent/received. Used for sliding TTL and day-boundary reset. |

### SMS Routing Logic

1. Check: active conversation context for this user on SMS?
2. Yes → route to that context's agent
3. Multiple active contexts (rare) → disambiguate: "Which task: (1) [A] or (2) [B]?"
4. No active context → treat as new task input (normal parsing pipeline)

### Context Expiry Strategy

Since tasks are managed in day blocks, context expiry uses a hybrid TTL strategy with day-boundary awareness:

- **Sliding TTL:** 24 hours from last activity (keeps active conversations alive).
- **Hard cap:** 72 hours absolute from creation (nothing lives forever, prevents zombie contexts).
- **Day-boundary reset:** If a context crosses midnight with no activity, reduce remaining TTL to 8 hours. This covers the morning window for the user to respond, then expires naturally if ignored.
- **On expiry:** If the associated task is still open, the agent re-prompts with a fresh context and new TTL.

This gives the "tasks in day blocks" mental model — contexts naturally die at morning boundaries if abandoned — while still keeping active conversations flowing.

For email: use `In-Reply-To` headers for threading.

## Notification Types

| Type | Channel | Timing | Content |
|------|---------|--------|---------|
| Morning Digest | Email | 6:30 AM ET | Full day schedule, tasks, prep results, agent activity, carry-overs, system health |
| Task Reminders | App push / Discord | 15 min before start | Task name, duration, prep materials |
| Overdue Nudge | SMS | 30 min after scheduled end | "Finish or reschedule?" |
| Agent Interrogation | Email + App | When PM agent needs info | Specific targeted questions |
| Agent Completion | Email | When agent finishes | Summary, thought process, output location, cost |
| End-of-Day Digest | Email | 5:30 PM ET weekdays | Completed, rescheduled, agent activity, daily cost |
| Budget Alert | SMS + Email | $20 daily or 90% monthly | Spend breakdown, continue/pause |
| Conflict Alert | SMS + App | On detection | Description, proposed resolution |
| Urgent Escalation | Phone Call (TTS) | Critical deadline miss or system failure | Brief TTS via Twilio with callback |
| Weekly Efficiency Digest | Discord (#donna-digest) | Sunday 7 PM ET | Completion rate, most-nudged tasks, reschedule patterns, domain breakdown, LLM cost |

All times above are in the user's configured timezone (see
[Scheduling > Timezone](scheduling.md#timezone)). The timezone is loaded
from `calendar.yaml` at startup and passed to all notification components.

## Escalation Tiers

| Tier | Channel | Wait Time |
|------|---------|-----------|
| 1 | App notification / Discord | 30 minutes |
| 2 | SMS text message | 1 hour |
| 3 | Email with "ACTION REQUIRED" subject | 2 hours |
| 4 | Phone call (TTS) | Only for priority 5 or budget emergencies. Max 1 call/day. |

Escalation resets on any acknowledgment on any channel. "Busy, will handle later" → back off 2 hours.

## Input Parsing Pipeline

1. Receive raw text + metadata (source, timestamp, user context)
2. LLM parses into structured task fields
3. Preference engine applies learned rules
4. Deduplication check (see [task-system.md](task-system.md))
5. Complexity assessment → auto-schedule or route for interrogation
6. Confirmation on same channel: "Got it. 'Oil change' scheduled for Saturday 10am. Priority 2."

## Nudge Event Tracking

All nudges (overdue, reminder, escalation) are persisted to the `nudge_events` table for analytics and the weekly efficiency digest.

### `nudge_events` Table

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Event identifier |
| user_id | String | User who received the nudge |
| task_id | UUID | FK → tasks.id |
| nudge_type | String | `overdue`, `reminder`, or `escalation` |
| channel | String | `discord`, `sms`, or `email` |
| escalation_tier | Integer | 1–4 per escalation tiers |
| message_text | Text | Full nudge message content |
| llm_generated | Boolean | Whether the message was LLM-generated or template fallback |
| created_at | DateTime | When the nudge was sent |

Additionally, `tasks.nudge_count` is atomically incremented on each nudge for quick access without joins.

### LLM-Generated Messages

Nudge and reminder messages are generated by the local LLM (Ollama qwen2.5:32b) via `generate_nudge` and `generate_reminder` task types. The prompt includes task context (title, domain, priority, overdue duration, nudge history) and the model generates a Donna-persona message with escalating tone.

If the local LLM is unavailable, the system falls back to hardcoded template strings (the original behavior). This ensures nudges are never blocked by model failures.

## DM Delivery

Per-user notifications bypass shared channels and go directly to the
requesting user's Discord DMs via `NotificationService.dispatch_dm()`.

### Routing Rule

| Notification Source | Delivery | Method |
|---------------------|----------|--------|
| Automation alerts (price watches, condition triggers) | DM to requesting user | `dispatch_dm(discord_id, ...)` |
| Digests (morning, EOD, weekly) | `#donna-digest` channel | `dispatch(channel="digest", ...)` |
| Reminders / nudges | `#donna-tasks` channel | `dispatch(channel="tasks", ...)` |

### BotProtocol

`BotProtocol` defines four async methods that `NotificationService` calls:

- `send_message(channel_name, text)` — plain text to a named channel
- `send_embed(channel_name, embed)` — Discord embed to a named channel
- `send_to_thread(thread_id, text)` — reply in an existing thread
- `send_dm(discord_id, content)` — direct message to a user by snowflake ID

`DonnaBot.send_dm()` calls `fetch_user(int(discord_id))` then `user.send(content)`.
Errors are logged and swallowed so callers don't need to handle Discord-specific failures.

### Blackout / Quiet-Hours Gating

`dispatch_dm()` applies the same time-window rules as `dispatch()`:

- **Blackout (12 AM–6 AM):** all DMs queued via `_enqueue_dm()`.
- **Quiet hours (8 PM–midnight):** priority < 5 queued; priority 5 sent immediately.
- Queued DMs are replayed by `flush_queue()` alongside channel notifications.

## Discord User Auto-Onboarding

When an unknown Discord user messages a Donna channel, the onboarding
gate in `DonnaBot.on_message` intercepts the message before any channel
routing:

1. `resolve_user_id(discord_id)` returns `None` — user is unknown.
2. First message from this user: stash original text in
   `_pending_onboarding`, reply with name challenge.
3. Next message is treated as name reply: create user via
   `Database.create_discord_user()`, confirm, replay stashed message.

Edge cases: repeat messages before name reply get a reminder; empty/whitespace
names re-prompt; creation failures return a retry message.

After onboarding, the user has a `users` row with `donna_user_id` (slug from
Discord username), `discord_id`, and `name`. `immich_user_id` and `email` are
`NULL` and can be linked later.

## Proactive Task Capture

Implemented in `src/donna/notifications/proactive_prompts.py`. Four background loops, each running as an `asyncio.create_task`:

| Class | Trigger | Channel | Description |
|-------|---------|---------|-------------|
| `PostMeetingCapture` | Every 5 min, checks `calendar_mirror` for ended meetings | `#donna-tasks` | "Any new tasks or action items?" after a meeting ends. Deduplicates by event ID. |
| `EveningCheckin` | Daily at 7 PM local (configurable) | `#donna-tasks` | "Anything to capture before tomorrow?" Includes a preview of tomorrow's first task. |
| `StaleTaskDetector` | Daily (configurable interval) | `#donna-tasks` | Flags backlog tasks >7 days old with no scheduled time. "Schedule it or archive it?" |
| `AfternoonInactivityCheck` | Daily at 2 PM local (configurable) | `#donna-tasks` | Nudges if no tasks were started, added, or completed today. |

All four use the same sleep-until-fire-time pattern as `MorningDigest`.

## End-of-Day Digest

Implemented in `src/donna/notifications/eod_digest.py`. The `EodDigest` class runs at 5:30 PM local time on weekdays (configurable via `EmailConfig.digest`). Assembles tasks completed today, still-open tasks, and cost summary, then posts to Discord `#donna-digest` and creates an email draft via `GmailClient` if configured.

## Escalation Subsystem

### Tier state machine

Implemented in `src/donna/notifications/escalation.py`. The `EscalationManager` drives multi-tier escalation when a task goes overdue and the user does not respond:

| Tier | Channel | Wait time |
|------|---------|-----------|
| 1 | Discord message | 30 min (configurable) |
| 2 | SMS text | 1 hour (configurable) |
| 3 | Email | Deferred to slice 8 |
| 4 | Phone TTS | Priority 5 / budget emergencies only |

Escalation state is persisted in the `escalation_state` table. Acknowledgment on any channel resets the tier. "Busy" reply backs off for a configurable number of hours.

### Delivery loop

Implemented in `src/donna/notifications/escalation_delivery_loop.py`. The `EscalationDeliveryLoop` class polls `escalation_request` rows every 60 seconds. It retries Discord delivery for open escalations whose first post failed and sweeps timed-out escalations. On timeout, the row resolves with `mode='pause'`, `resolved_by='timeout'`; the task transitions to `paused`; if priority is high enough (default >= 4), the `EscalationManager` is invoked at SMS tier-2.

## Module Reference

| Module | Role |
|--------|------|
| `service.py` | `NotificationService` — central dispatch for channel/DM/thread messages. Enforces blackout and quiet-hours gating. |
| `bot_protocol.py` | `BotProtocol` — interface defining `send_message`, `send_embed`, `send_to_thread`, `send_dm`. |
| `digest.py` | `MorningDigest` — morning summary (6:30 AM) to Discord and email. |
| `eod_digest.py` | `EodDigest` — end-of-day summary (5:30 PM weekdays) to Discord and email. |
| `weekly_digest.py` | `WeeklyDigest` — Sunday 7 PM efficiency digest. |
| `reminders.py` | Task reminder scheduling (15 min before start). |
| `overdue.py` | `OverdueDetector` — fires nudges 30 min after a task's scheduled end. |
| `escalation.py` | `EscalationManager` — multi-tier escalation state machine. |
| `escalation_delivery_loop.py` | `EscalationDeliveryLoop` — background poller for delivery retries and timeout sweeps. |
| `proactive_prompts.py` | `PostMeetingCapture`, `EveningCheckin`, `StaleTaskDetector`, `AfternoonInactivityCheck`. |
