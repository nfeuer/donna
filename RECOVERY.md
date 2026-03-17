# Donna Recovery Procedures

## Database Recovery from Backup

### When to Use
- Database corruption detected (PRAGMA integrity_check fails)
- Accidental data deletion
- Failed migration that can't be rolled back via Alembic

### Procedure

1. **Stop the orchestrator:**
   ```bash
   docker compose -f docker/donna-core.yml down
   ```

2. **Identify the backup to restore:**
   ```bash
   ls -la /donna/backups/daily/
   # Pick the most recent valid backup
   ```

3. **Verify backup integrity:**
   ```bash
   sqlite3 /donna/backups/daily/donna_tasks_2026-03-17.db "PRAGMA integrity_check;"
   # Should return "ok"
   ```

4. **Replace the live database:**
   ```bash
   # Move corrupted DB aside (don't delete yet)
   mv /donna/db/donna_tasks.db /donna/db/donna_tasks.db.corrupted
   mv /donna/db/donna_tasks.db-wal /donna/db/donna_tasks.db-wal.corrupted 2>/dev/null
   mv /donna/db/donna_tasks.db-shm /donna/db/donna_tasks.db-shm.corrupted 2>/dev/null

   # Restore from backup
   cp /donna/backups/daily/donna_tasks_2026-03-17.db /donna/db/donna_tasks.db
   ```

5. **Restart the orchestrator:**
   ```bash
   docker compose -f docker/donna-core.yml up -d
   ```

6. **Verify recovery:**
   - Check `/health` endpoint returns 200
   - Check morning digest includes tasks
   - Orchestrator will auto-trigger Supabase full re-sync on detecting restored DB

7. **Clean up after confirming recovery:**
   ```bash
   rm /donna/db/donna_tasks.db.corrupted
   ```

### RPO (Recovery Point Objective)
- **Maximum data loss:** 24 hours (last daily backup)
- **Effective RPO:** Near-real-time if Supabase sync was operational (task data recoverable from cloud replica)

## Failed Alembic Migration Recovery

1. Alembic runner creates a pre-migration backup automatically.
2. If migration fails mid-apply:
   ```bash
   # Restore pre-migration backup
   cp /donna/backups/pre_migration_donna_tasks.db /donna/db/donna_tasks.db
   ```
3. Fix the migration script, then re-run:
   ```bash
   alembic upgrade head
   ```

## Circuit Breaker Recovery

If Claude API is down and the circuit breaker is open:
1. Donna automatically enters degraded mode (template-based digests, raw task capture).
2. Circuit breaker tests recovery every 5 minutes.
3. On first successful API response, circuit breaker closes automatically.
4. No manual intervention needed unless the outage exceeds 24 hours.

If the outage exceeds 24 hours:
1. Check Anthropic status page for incident reports.
2. All captured tasks during outage are flagged for re-parsing.
3. On recovery, the orchestrator processes the re-parse queue automatically.

## Full System Recovery (Server Failure)

1. Provision new server or restore from server backup.
2. Install Docker and Docker Compose.
3. Clone the Donna repo.
4. Copy `.env` file (from secure backup or recreate from `.env.example`).
5. Restore database backups from offsite storage:
   ```bash
   # From Backblaze B2 or Google Cloud Storage
   b2 download-file-by-name donna-backups donna_tasks_latest.db /donna/db/donna_tasks.db
   ```
6. Start all services:
   ```bash
   docker compose -f docker/donna-core.yml -f docker/donna-monitoring.yml up -d
   ```
7. Verify health, trigger Supabase re-sync.
