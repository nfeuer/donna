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

## Logging Implementation (current)

The `src/donna/logging/` package contains two modules:

### `setup.py` — Structured logging configuration

`setup_logging(log_level, json_output)` configures `structlog` with JSON output and `contextvars` for async context propagation. All services call this at startup. Four context variables are injected into every log entry:

| Variable | Default | Description |
|----------|---------|-------------|
| `correlation_id` | `""` | Traces a single request across all services |
| `user_id` | `"system"` | User who triggered the action |
| `channel` | `""` | `discord`, `sms`, `email`, `app`, `system` |
| `task_id` | `""` | Associated task UUID |

Non-empty values are automatically added to every log entry via the `add_context_vars` processor.

### `invocation_logger.py` — LLM call tracking

`InvocationLogger` writes to the `invocation_log` table in `donna_tasks.db` (the main database, not a separate logging database). Every LLM API call is tracked per CLAUDE.md. Fields captured per invocation:

| Field | Type | Description |
|-------|------|-------------|
| `task_type` | str | What kind of LLM call (e.g., `parse_task`, `escalation_lifecycle`) |
| `model_alias` / `model_actual` | str | Routing alias and resolved model name |
| `input_hash` | str | Hash of the prompt for deduplication |
| `latency_ms` | int | Wall-clock latency |
| `tokens_in` / `tokens_out` | int | Token counts |
| `cost_usd` | float | Computed cost |
| `user_id` | str | Requesting user |
| `task_id` | str? | Associated task |
| `is_shadow` | bool | Whether this was a shadow-mode call |
| `skill_id` | str? | Skill that triggered the call |
| `escalation_request_id` | int? | Escalation that triggered the call |
| `chain_id` | str? | Multi-step chain correlation |
| `caller` | str? | Calling module for attribution |
| `queue_wait_ms` | int? | Time spent in the request queue |
| `overflow_escalated` | bool | Whether the call triggered overflow escalation |

This is the primary observability data for cost analysis, evaluation, and budget enforcement. Invocation log rows are permanent.

## Logging Database (aspirational)

> **Status:** The dedicated `donna_logs.db` described below is a design target from spec v3.0 (sections 14-15) and is **not yet implemented**. Current logging routes through structlog to stdout (captured by Docker `json-file` driver and shipped to Loki via Promtail). LLM invocation tracking uses the `invocation_log` table in the main `donna_tasks.db`. The schema, retention policy, and nightly pruning below represent the planned design.

Dedicated `donna_logs.db` on NVMe. Separate from task DB to avoid contention.

### Log Table Schema (planned)

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

### Retention Policy (planned)

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

## Log Pipeline (current state)

1. Each service writes structured JSON to **stdout** via `structlog` (configured by `src/donna/logging/setup.py`). Docker captures via `json-file` log driver.
2. **Promtail** (in donna-monitoring.yml) tails Docker logs and ships to **Loki**.
3. **Grafana** queries Loki for real-time dashboard.
4. LLM invocation data is written to the `invocation_log` table in `donna_tasks.db` by `InvocationLogger` (`src/donna/logging/invocation_logger.py`).

> **Note:** Step 4 in the original spec described a lightweight log collector writing general structured logs to a dedicated SQLite log DB (`donna_logs.db`). This collector is not yet implemented. The `invocation_log` table in the main database covers LLM call tracking; all other structured log data is accessed via Loki/Grafana.

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

### Manual Escalation (slices 17–24)

Sourced from ``invocation_log`` rows whose ``task_type`` is
``escalation_lifecycle`` (slice 17) or ``tool_gap_lifecycle``
(slice 22). Slice 24 adds these panels — the Loki / Promtail
plumbing already streams the rows via the standard ``donna_logs``
write path.

- **Open escalations by mode** — bar chart, count where
  ``status IN ('open','resolved','submitted','failed')``,
  grouped by ``mode``. Highlights the active resolution mix
  (chat vs claude_code vs api_extended).
- **Time-to-resolution histogram** — ``resolved_at - created_at``
  for the last 7 days, faceted by ``mode``. Surfaces UX
  regressions (someone-keeps-not-clicking).
- **Iteration distribution** — histogram of the
  ``escalation_request.iteration`` column at terminal status.
  Mode = claude_code only. Bucket 1/2/3 — bucket 3 is the cap;
  growth in bucket 3 means the spec is unclear or the validator
  is too strict.
- **Validation pass rate** — ratio of ``escalation_validated``
  to (``escalation_validated`` + ``escalation_failed``) per day.
- **Daily extension grant rate + amount** — from the
  ``extension_granted`` event payload. Surfaces "operator is
  always extending" cost drift.
- **Tool gaps per day** — ``tool_gap_detected`` counts faceted
  by ``severity`` and ``detection_point``. Speculative gaps can
  ramp without operator attention; the panel makes that visible.
- **Per-row timeline drill-down** — link from any panel to
  ``/admin/escalations/{correlation_id}`` which renders the
  slice-19 detail timeline (slice 24 unifies escalation_lifecycle
  + tool_gap_lifecycle on that view via the
  ``GET /admin/escalations/{id}/timeline`` endpoint).

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
