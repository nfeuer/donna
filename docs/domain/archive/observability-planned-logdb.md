# Planned: Dedicated Logging Database (`donna_logs.db`)

> Archived from `docs/domain/observability.md` on 2026-05-19. This design is retained for reference but is not implemented. Current logging uses structlog → stdout → Docker json-file → Promtail → Loki. Tracked as [G-13, G-25](../../superpowers/followups/open-backlog.md).

Dedicated `donna_logs.db` on NVMe. Separate from task DB to avoid contention.

## Log Table Schema

| Field | Type | Purpose |
|-------|------|---------|
| id | INTEGER PK | Auto-incrementing |
| timestamp | TEXT ISO 8601 | When (UTC) |
| level | TEXT | DEBUG-CRITICAL |
| service | TEXT | orchestrator, mcp_server, discord_bot, scheduler, notification, agent_worker, sync |
| component | TEXT | input_parser, calendar_sync, state_machine, preference_engine, etc. |
| event_type | TEXT | Machine-readable: `task.created`, `api.call.failed`, `agent.timeout` |
| message | TEXT | Human-readable |
| correlation_id | TEXT | Traces single request across all services |
| task_id | TEXT? | Associated task UUID |
| user_id | TEXT? | User who triggered |
| agent_id | TEXT? | Agent type if from agent worker |
| channel | TEXT? | discord, sms, email, app, system |
| duration_ms | INTEGER? | For timed operations |
| cost_usd | REAL? | API cost if model call |
| error_type | TEXT? | Exception class name |
| error_trace | TEXT? | Full stack trace |
| extra | TEXT (JSON) | Additional structured context |

Indexes on: timestamp, level, service, event_type, correlation_id, task_id, error_type. WAL mode.

## Retention Policy

| Level | Retention |
|-------|-----------|
| DEBUG | 7 days |
| INFO | 30 days |
| WARNING | 90 days |
| ERROR / CRITICAL | 1 year |
| Invocation logs | Permanent (cost analysis, evaluation, preferences) |

Nightly cron prunes expired logs. Weekly VACUUM reclaims disk space.
