# Notification & Escalation System

> Split from Donna Project Spec v3.0 — Sections 10, 11

> **Status (Wave 1):** NotificationService is now instantiated in the
> orchestrator process on startup, wired with the live DonnaBot and the
> calendar config. Previously only tests constructed it; production code
> now relies on it for automation alerts and skill-system warnings.

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
| Morning Digest | Email | 6:30 AM | Full day schedule, tasks, prep results, agent activity, carry-overs, system health |
| Task Reminders | App push / Discord | 15 min before start | Task name, duration, prep materials |
| Overdue Nudge | SMS | 30 min after scheduled end | "Finish or reschedule?" |
| Agent Interrogation | Email + App | When PM agent needs info | Specific targeted questions |
| Agent Completion | Email | When agent finishes | Summary, thought process, output location, cost |
| End-of-Day Digest | Email | 5:30 PM weekdays | Completed, rescheduled, agent activity, daily cost |
| Budget Alert | SMS + Email | $20 daily or 90% monthly | Spend breakdown, continue/pause |
| Conflict Alert | SMS + App | On detection | Description, proposed resolution |
| Urgent Escalation | Phone Call (TTS) | Critical deadline miss or system failure | Brief TTS via Twilio with callback |
| Weekly Efficiency Digest | Discord (#donna-digest) | Sunday 7 PM | Completion rate, most-nudged tasks, reschedule patterns, domain breakdown, LLM cost |

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
4. Deduplication check (see `docs/task-system.md`)
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

## Proactive Task Capture

- **End-of-meeting prompt:** Calendar shows meeting just ended → "Any new tasks or action items?"
- **Evening check-in:** Configurable (e.g., 7pm) → "Anything to capture before tomorrow?"
- **Stale task detection:** Backlog 7+ days, no scheduled time → "Schedule it or archive it?"
