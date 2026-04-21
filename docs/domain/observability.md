# Observability & Logging

> Split from Donna Project Spec v3.0 — Sections 14, 15

## Principle

Observability is a Phase 1 deliverable. Every Donna service emits structured JSON logs. Debugging any issue should require seconds, not SSH and grep.

## Logging Framework

All Python services use `structlog` with JSON output and `contextvars` for async context propagation. Every incoming request binds `correlation_id`, `user_id`, `channel`, and `task_id` as context variables that appear in all downstream log entries.

## Log Levels

| Level | When | Examples |
|-------|------|---------|
| DEBUG | Detailed diagnostics. Off in prod unless troubleshooting. | Full prompts, API response bodies, dedup scores, scheduler slot evaluation |
| INFO | Normal operations. System working correctly. | Task created, state changed, reminder sent, digest generated, calendar synced |
| WARNING | Unexpected but handled. | API retry, confidence below threshold, degraded mode activated |
| ERROR | Operation failed but system continues. | API failed after retries, schema validation rejected, agent timeout |
| CRITICAL | System-level failure, immediate attention. | Circuit breaker activated, DB corruption, orchestrator crash, NVMe full |

## Logging Database

Dedicated `donna_logs.db` on NVMe. Separate from task DB to avoid contention.

### Log Table Schema

| Field | Type | Purpose |
|-------|------|---------|
| id | INTEGER PK | Auto-incrementing |
| timestamp | TEXT ISO 8601 | When (UTC) |
| level | TEXT | DEBUG–CRITICAL |
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

### Retention Policy

| Level | Retention |
|-------|-----------|
| DEBUG | 7 days |
| INFO | 30 days |
| WARNING | 90 days |
| ERROR / CRITICAL | 1 year |
| Invocation logs | Permanent (cost analysis, evaluation, preferences) |

Nightly cron prunes expired logs. Weekly VACUUM reclaims disk space.

## Event Types (Hierarchical)

- `task.*`: created, state_changed, dedup_detected, overdue, escalation_triggered
- `api.*`: call.started, call.completed, call.failed, call.retried, circuit_breaker.opened/closed, degraded_mode.activated
- `agent.*`: dispatched, progress, completed, failed, timeout, interrogation.sent/response_received
- `scheduler.*`: weekly_plan, daily_recalc, slot_assigned, conflict_detected, calendar_sync.completed/user_modification
- `notification.*`: sent, failed, escalated, acknowledged, blackout_blocked
- `preference.*`: correction_logged, rule_extracted, rule_applied, rule_disabled
- `system.*`: startup, shutdown, health_check, backup.completed/failed, migration.applied
- `cost.*`: daily_threshold, monthly_warning, agent_paused, budget_increase
- `sync.*`: supabase.push, supabase.failed, keepalive.sent

## Log Pipeline (Phase 1 — Dual Write)

1. Each service writes structured JSON to **stdout**. Docker captures via `json-file` log driver.
2. **Promtail** (in donna-monitoring.yml) tails Docker logs → ships to **Loki**.
3. **Grafana** queries Loki for real-time dashboard.
4. Simultaneously, lightweight log collector in orchestrator writes to SQLite log DB for programmatic access and retention management.

## Dashboard Panels

### System Health
- Service status (green/yellow/red per container)
- Last successful ops timestamps
- NVMe disk usage breakdown
- Memory/CPU per container
- Circuit breaker state

### Task Pipeline
- Tasks created today/week (by channel, domain)
- State distribution (backlog/scheduled/in_progress/blocked/done/cancelled)
- Avg time-to-schedule
- Reschedule frequency (3+ highlighted)
- Dedup hit rate
- Completion velocity trend

### LLM & Cost
- API calls per hour/day (by task type, model)
- Token usage breakdown
- Daily/weekly/monthly spend, burn rate, projected monthly, budget remaining
- Latency p50/p95/p99 by task type
- Error rate, retries, circuit breaker activations
- Shadow mode comparison (when `shadow` key is set in routing config)

### Agent Activity
- Active agents: task, elapsed vs timeout
- Completed today/week with cost and duration
- Failed today with error summaries
- Cost per agent type

### Notifications
- Messages sent (by channel, type)
- Delivery failures
- Escalation events
- User response times (feeds preference learning)

### Error Exploration
- Filterable table by service, component, event type, time, severity
- Error frequency timeline
- Correlation trace: full request lifecycle across services
- Stack trace viewer

### Preference Learning
- Corrections per week trend
- Rules extracted, rules auto-disabled
- Rule survival rate (% active after 30 days)

## Alerting Rules

| Condition | Action |
|-----------|--------|
| Service down > 5 min | Discord #donna-debug webhook + SMS |
| > 10 errors in 5 min | Discord #donna-debug |
| Circuit breaker opened | Discord #donna-debug + SMS |
| NVMe disk > 80% | Discord #donna-debug |
| Supabase sync failure > 1 hour | Discord #donna-debug |
| No orchestrator heartbeat 10 min | External watchdog handles (see `docs/resilience.md`) |
