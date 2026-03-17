# Resilience, Security & Failure Handling

> Split from Donna Project Spec v3.0 — Sections 3.6, 16, 17, 18

## API Resilience Layer

Every Claude API call goes through a resilience wrapper.

### Retry Policies

| Task Category | Max Retries | Backoff | On Failure |
|--------------|-------------|---------|------------|
| Critical (digest, deadline reminders) | 3 | Exponential, 2s start, 30s cap | Fall back to degraded mode |
| Standard (parse, classify) | 2 | Exponential, 1s start, 15s cap | Queue for retry next cycle; notify user of delay |
| Agent work (research, code gen) | 1 | 5s fixed | Mark `agent_status = failed`; notify user; **do not retry** (budget protection) |

### Degraded Mode Definitions

- **Morning digest:** Template-based using raw calendar + task list from SQLite. No LLM, no persona. "Today's schedule: [list]. Tasks due: [list]. Note: AI digest unavailable."
- **Reminders:** Static template: "Reminder: [task title] starts at [time]." Personality lost — acceptable.
- **Task parsing:** Accept raw text as-is, title = raw input, all fields defaulted, flagged for re-parsing on recovery. **Never lose a capture.**

### Circuit Breaker

5 consecutive failures in 10 minutes → circuit-breaker mode:
1. Pause all non-critical agent work
2. Switch critical paths to degraded mode
3. Send single SMS: "Donna's AI is temporarily unavailable. Running in basic mode. Will notify when restored."
4. Test recovery every 5 minutes with lightweight health-check
5. Reset on first successful response

### Response Validation

Every API response validated against expected output schema before use. Malformed JSON, missing fields, or schema mismatches → retry (counted against retry budget).

## Health Monitoring

### Layer 1: Docker Healthchecks

Each service gets a `healthcheck` directive. Orchestrator exposes HTTP `/health` endpoint. Docker polls every 30s. Three failures → container restart (`restart: unless-stopped`).

`/health` checks: SQLite reachable, Discord bot connected, scheduler loop running, last API health-check < 10 min. Returns 200 or 503 with JSON listing failures.

### Layer 2: External Watchdog

Separate lightweight process **outside Docker**. Every 5 min checks `docker inspect --format='{{.State.Health.Status}}' donna-orchestrator`. If unhealthy/stopped → alert via Twilio SMS or Discord webhook (independent of Donna bot).

### Layer 3: Daily Self-Diagnostic

Part of morning digest generation. Before generating: DB integrity (`PRAGMA integrity_check`), NVMe space, last calendar sync, last Supabase sync, pending migrations, budget status. Issues prepended to morning digest.

## Backup Strategy

### Method

SQLite `.backup` API (`connection.backup()`). **Never file copy** — copying WAL-mode SQLite during writes can corrupt.

### Schedule & Retention

- Daily at 3 AM (blackout hours): full backup of `donna_tasks.db` and `donna_logs.db`
- 7 daily, 4 weekly (Sunday), 3 monthly (1st) retained
- Worst case: ~14 backups × 500MB = 7GB (trivial on 1TB NVMe)
- Off-server: weekly/monthly pushed to cloud (GCS free 5GB or Backblaze B2 ~$0.04/month)

### Recovery

- **RPO:** 24 hours (last daily backup). Supabase replica reduces effective RPO to sync interval.
- **Procedure:** Stop orchestrator → copy backup to live path → restart → orchestrator triggers Supabase re-sync. Documented in `RECOVERY.md`.

## Security & Privacy

| Principle | Implementation |
|-----------|---------------|
| Least privilege | Each agent has only the tools defined in task type registry |
| No credentials in agent context | Agents request tool calls via orchestrator. Never see raw API keys. |
| Sandboxed filesystem | Agents only access `/donna/workspace/` |
| Git safety | Feature branches only. Main/production have push protection. |
| Email safety | Draft-only default. Send scope behind feature flag + OAuth re-auth. |
| No data exfiltration | MCP server whitelists allowed outbound destinations |
| Tool validation | Orchestrator validates all model tool call requests before execution |
| Blackout enforcement | 12am–6am hard block on outbound at notification service level |
| Log sanitization | No credentials in logs. API bodies at DEBUG only with redaction. |
| NVMe encryption | LUKS at rest. Decryption via TPM or entered at boot. |

## Acceptable Failures

- Priority misclassification (user corrects → feeds learning)
- Duplicate reminders (annoying, no data loss)
- Agent produces low-quality code (user reviews before merge)
- Suboptimal scheduling (user reschedules → feeds learning)
- Local LLM misroutes to Claude API (costs more, completes correctly)

## Unacceptable Failures

- Missing a deadline reminder (must escalate)
- Sending emails to unintended recipients (architecturally blocked)
- Deleting files without backup (append/modify only)
- Overwriting code without version control (always branched/stashed)
- Exceeding budget without notification (synchronous cost monitoring)
- Contact during blackout (hard block at notification service)
- Agent running indefinitely (timeout enforced)
- Learned preference causing repeated errors (auto-disabled)
- Silent service failure (detected within 10 min)
