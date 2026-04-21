# Budget & Cost

Every LLM call is logged with its dollar cost. The budget guard enforces
the daily soft pause ($20) and the monthly hard cap ($100).

Design references:
[`spec_v3.md` §4.3 Structured Invocation Logging](../reference-specs/spec-v3.md),
[`CLAUDE.md`](../start-here/conventions.md) budget section.

## Where Costs Live

- Table: `invocation_log` in `donna_logs.db`
- Columns: `ts, user_id, task_type, model, tokens_in, tokens_out, latency_ms, cost_usd, correlation_id, output_hash`
- Writer: [`donna.logging.invocation_logger`](../reference/donna/logging/invocation_logger.md)
- Guard: [`donna.cost.budget_guard`](../reference/donna/cost/index.md)
- Pricing: hard-coded per-provider constants kept in sync with public
  pricing pages (see [`donna.models.providers`](../reference/donna/models/providers/index.md))

## Useful Queries

```sql
-- Today
SELECT task_type, model, COUNT(*) AS calls, ROUND(SUM(cost_usd), 4) AS usd
FROM invocation_log
WHERE date(ts) = date('now')
GROUP BY 1, 2 ORDER BY usd DESC;

-- Last 14 days by day
SELECT date(ts) AS d, ROUND(SUM(cost_usd), 4) AS usd, COUNT(*) AS calls
FROM invocation_log
GROUP BY 1 ORDER BY 1 DESC LIMIT 14;

-- Model leaderboard this month
SELECT model, COUNT(*) AS calls, ROUND(SUM(cost_usd), 4) AS usd
FROM invocation_log
WHERE ts >= datetime('now', 'start of month')
GROUP BY 1 ORDER BY usd DESC;
```

## When the Budget Trips

Jump to [Workflow → Handle Budget Breach](../workflows/handle-budget-breach.md).
