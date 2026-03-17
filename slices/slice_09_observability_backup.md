# Slice 9: Observability, Backup & Health Monitoring

> **Goal:** Deploy the Grafana + Loki monitoring stack, configure dashboards for all key metrics, set up automated SQLite backups, and wire up the three-layer health monitoring system. After this slice, Donna is a complete Phase 1 system.

## Relevant Docs

- `CLAUDE.md` (always)
- `docs/observability.md` — Full dashboard spec, alerting rules, log pipeline
- `docs/resilience.md` — Health monitoring (3 layers), backup strategy
- `docs/architecture.md` — Docker compose structure, NVMe layout

## What to Build

1. **Deploy monitoring stack** via `docker/donna-monitoring.yml`:
   - Loki for log aggregation
   - Promtail configured to tail Donna Docker container logs
   - Grafana with Loki as data source
   - Provisioned dashboards (JSON models committed to repo)

2. **Configure Grafana dashboards:**
   - **System Health:** Service status indicators, last successful ops, NVMe usage, circuit breaker state
   - **Task Pipeline:** Tasks created/completed, state distribution, reschedule frequency, dedup hit rate
   - **LLM & Cost:** API calls, token usage, daily/monthly spend, burn rate, latency p50/p95
   - **Error Exploration:** Filterable error table, error frequency timeline, correlation trace

3. **Implement backup automation** (`src/donna/resilience/backup.py`):
   - Uses SQLite `.backup` API (not file copy)
   - Daily at 3 AM: backup `donna_tasks.db` and `donna_logs.db`
   - Retention rotation: 7 daily, 4 weekly, 3 monthly
   - Pre-migration backup triggered by Alembic runner
   - Backup success/failure logged as `system.backup.completed` / `system.backup.failed`

4. **Implement health monitoring layers:**
   - **Layer 1 (Docker):** Healthcheck directives already in compose files. Verify `/health` endpoint checks: SQLite reachable, Discord connected, scheduler running, last API check < 10 min
   - **Layer 2 (External watchdog):** Standalone script (`scripts/watchdog.sh`) running outside Docker. Checks `docker inspect` every 5 min. Alerts via Twilio SMS or Discord webhook on failure
   - **Layer 3 (Self-diagnostic):** Add pre-digest health check: DB integrity, disk space, sync timestamps, pending migrations, budget status. Issues prepended to morning digest

5. **Configure alerting:**
   - Service down > 5 min → Discord `#donna-debug` webhook + SMS
   - Error rate > 10 in 5 min → Discord `#donna-debug`
   - Circuit breaker opened → Discord `#donna-debug` + SMS
   - NVMe disk > 80% → Discord `#donna-debug`

6. **Implement Supabase sync** (`src/donna/integrations/supabase_sync.py`):
   - Async write-through: on every task write to SQLite, push to Supabase
   - Non-blocking: Supabase failure doesn't block local operations
   - Keep-alive cron: ping Supabase every 3 days to prevent free tier pause
   - Full reconciliation sync on Supabase recovery

7. **Write tests:**
   - Unit test: backup creates valid SQLite copy
   - Unit test: retention rotation keeps correct number of backups
   - Unit test: health endpoint returns correct status based on component states
   - Integration test: watchdog script detects container health status

## Acceptance Criteria

- [ ] `docker compose -f docker/donna-monitoring.yml up` starts Grafana + Loki + Promtail
- [ ] Grafana accessible at port 3000 with Loki data source configured
- [ ] System Health dashboard shows service status and resource usage
- [ ] Task Pipeline dashboard shows task creation/completion metrics
- [ ] LLM & Cost dashboard shows spend tracking and latency
- [ ] Error Exploration dashboard allows filtering by service, event type, time
- [ ] SQLite backup runs at 3 AM and creates valid backup files
- [ ] Backup retention correctly prunes old daily/weekly/monthly backups
- [ ] `/health` endpoint checks all components and returns appropriate status
- [ ] External watchdog detects unhealthy container and sends SMS alert
- [ ] Self-diagnostic issues appear in morning digest
- [ ] Supabase write-through sync works (non-blocking)
- [ ] Supabase keep-alive prevents free tier pause
- [ ] All alerting rules fire correctly on simulated conditions

## Not in Scope

- No Flutter dashboard (Phase 4)
- No shadow mode comparison panel (Phase 3)
- No agent activity panel (agents not yet built)

## Session Context

Load only: `CLAUDE.md`, this slice brief, `docs/observability.md`, `docs/resilience.md`, `docs/architecture.md`
