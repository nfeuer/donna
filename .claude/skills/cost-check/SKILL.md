---
name: cost-check
description: Query invocation_log for current API spend vs daily and monthly budget thresholds
---

# Cost Check

Check Donna's API spend against budget thresholds. Budget rules: $100/month hard cap, $20/day pause threshold.

## Workflow

1. **Find the database:**
   ```bash
   find /donna -name "donna_tasks.db" -o -name "donna.db" 2>/dev/null
   ls /mnt/donna/donna_tasks.db 2>/dev/null
   ```

2. **Query today's spend:**
   ```bash
   sqlite3 <db_path> "SELECT
     COUNT(*) as calls,
     COALESCE(SUM(cost_usd), 0) as total_usd,
     COALESCE(SUM(input_tokens), 0) as input_tokens,
     COALESCE(SUM(output_tokens), 0) as output_tokens
   FROM invocation_log
   WHERE date(created_at) = date('now');"
   ```

3. **Query this month's spend:**
   ```bash
   sqlite3 <db_path> "SELECT
     COUNT(*) as calls,
     COALESCE(SUM(cost_usd), 0) as total_usd
   FROM invocation_log
   WHERE strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now');"
   ```

4. **Breakdown by model (today):**
   ```bash
   sqlite3 <db_path> "SELECT
     model,
     COUNT(*) as calls,
     COALESCE(SUM(cost_usd), 0) as cost_usd
   FROM invocation_log
   WHERE date(created_at) = date('now')
   GROUP BY model
   ORDER BY cost_usd DESC;"
   ```

5. **Breakdown by task type (today):**
   ```bash
   sqlite3 <db_path> "SELECT
     task_type,
     COUNT(*) as calls,
     COALESCE(SUM(cost_usd), 0) as cost_usd
   FROM invocation_log
   WHERE date(created_at) = date('now')
   GROUP BY task_type
   ORDER BY cost_usd DESC;"
   ```

6. **Output a report:**
   ```
   ## Cost Report

   ### Today
   - Calls: X | Spend: $X.XX / $20.00 daily threshold (XX%)
   - Status: OK | APPROACHING | EXCEEDED

   ### This Month
   - Calls: X | Spend: $X.XX / $100.00 monthly cap (XX%)
   - Status: OK | APPROACHING | EXCEEDED

   ### Top models (today)
   | Model | Calls | Cost |
   |-------|-------|------|

   ### Top task types (today)
   | Task Type | Calls | Cost |
   |-----------|-------|------|
   ```

7. Flag if daily spend > $15 (approaching) or > $20 (exceeded). Flag if monthly spend > $80 (approaching) or > $100 (exceeded).
