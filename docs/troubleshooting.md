# Troubleshooting

Common issues and their fixes. This page grows over time as new edge cases surface.

## Budget breach pauses all autonomous work

**Cause:** Daily spend exceeded $20 or monthly spend exceeded $100. `BudgetGuard` rejects all LLM calls.

**Fix:**
1. Check current spend:
   ```sql
   SELECT date(ts), SUM(cost_usd), COUNT(*)
   FROM invocation_log
   GROUP BY 1 ORDER BY 1 DESC LIMIT 7;
   ```
2. Daily pauses clear automatically at UTC midnight.
3. To temporarily raise the threshold, edit `config/donna_models.yaml` with an explicit reason.
4. Route expensive task types to Ollama by changing the model alias.

See [Handle Budget Breach](workflows/handle-budget-breach.md).

## Docker container won't start

**Cause:** Missing environment variables, port conflicts, or stale images.

**Fix:**
1. Check logs: `docker compose -f docker/compose.yml logs <service>`
2. Verify `.env` file exists in `docker/` with all required variables (copy from `docker/.env.template`).
3. Check port availability: `ss -tlnp | grep -E '8000|8080|3100|3000'`
4. Rebuild after code changes: `docker compose -f docker/compose.yml build <service>`

## Alembic migration fails with "Multiple heads"

**Cause:** Two migrations branched from the same parent without merging.

**Fix:**
1. Check heads: `alembic heads`
2. If multiple heads, create a merge migration: `alembic merge heads -m "merge branches"`
3. Always check heads before creating new migrations.

See [Add a Migration](workflows/add-a-migration.md).

## Discord bot doesn't respond to DMs

**Cause:** Bot token invalid, intents not enabled, or orchestrator not running.

**Fix:**
1. Verify the bot token in `docker/.env` matches the Discord developer portal.
2. Confirm **Message Content Intent** is enabled in the Discord bot settings.
3. Check the bot process is running: `docker compose -f docker/compose.yml ps`
4. Check logs for connection errors: `docker compose -f docker/compose.yml logs donna-bot`

## Ollama model not available

**Cause:** Model not pulled, Ollama service not running, or GPU memory exhausted.

**Fix:**
1. Check Ollama is running: `curl http://localhost:11434/api/tags`
2. Pull the model: `ollama pull qwen2.5:32b-instruct-q6_K`
3. Check GPU memory: `nvidia-smi`
4. If GPU memory is exhausted, stop other GPU processes or route to Claude API temporarily.

## Supabase sync falling behind

**Cause:** Network issues, Supabase rate limits, or sync process crashed.

**Fix:**
1. Check sync status in logs: `grep "supabase_sync" /var/log/donna/*.log`
2. Verify Supabase credentials in config.
3. The sync is write-through and eventually consistent — local SQLite is always the source of truth.
4. Restart the sync process if stuck.

## Calendar events not appearing

**Cause:** Google Calendar OAuth token expired or calendar sync not running.

**Fix:**
1. Re-authenticate: check `config/token.json` exists and is valid.
2. Restart the calendar sync service.
3. Check for timezone mismatches between Donna's config and Google Calendar.

## Structured logging not appearing in Grafana

**Cause:** Promtail not scraping the correct log paths, or Loki not receiving logs.

**Fix:**
1. Verify Promtail config points to Donna's log output directory.
2. Check Loki is healthy: `curl http://localhost:3100/ready`
3. Verify Donna is using `structlog` (not `print()`) — all log output should be JSON-formatted.

See [Observability](domain/observability.md).

## Tests fail with "database is locked"

**Cause:** Multiple test processes accessing the same SQLite file without WAL mode, or a test left a connection open.

**Fix:**
1. Ensure test fixtures use a fresh in-memory or temp-file database.
2. Check that WAL mode is enabled: `PRAGMA journal_mode;` should return `wal`.
3. Run tests sequentially if parallel execution causes lock contention: `pytest -p no:xdist`
