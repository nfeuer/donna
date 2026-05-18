# Claude Inspector — Design Spec

**Date:** 2026-05-16
**Goal:** A forensics tool for inspecting and optimizing Claude API usage — full request/response capture, a call browser for manual inspection, and proactive insights that surface waste patterns.

**Primary optimization targets:** quality/cost tradeoff (#1), raw cost reduction (#2).

**References:** spec_v3.md §4.3 (structured logging), §5.2 (cost tracking), §6.1 (dashboard)

---

## 1. Payload Collection

### Storage Format

Every `complete()` call writes a JSON file after the provider responds:

```
data/payloads/{YYYY-MM-DD}/{invocation_id}.json
```

File contents:

```json
{
  "request": {
    "messages": [...],
    "model": "claude-sonnet-4-20250514",
    "tools": [...],
    "max_tokens": 4096,
    "system": "..."
  },
  "response": {
    "content": [...],
    "usage": {"input_tokens": 1200, "output_tokens": 450},
    "stop_reason": "end_turn"
  }
}
```

### Integration Point

In `src/donna/models/router.py`, after the provider call succeeds and invocation logging fires, write the payload file. Fire-and-forget — never block the completion return path. On write failure, log a warning and continue.

### Schema Change

Add `payload_path` (STRING, nullable) to `invocation_log` table via Alembic migration. Populated with the relative path (`{YYYY-MM-DD}/{invocation_id}.json`) on successful write, NULL if write failed or payload evicted.

### Input Hash Reuse

Populate the existing (currently empty) `input_hash` column with a SHA-256 truncation of the `system` prompt field. This enables system-prompt grouping queries without reading files from disk.

---

## 2. FIFO Eviction

### Trigger

Runs on a scheduled hourly sweep. Additionally, the payload writer maintains an in-memory running total (incremented on each write, synced with actual disk usage on startup and hourly). If the in-memory estimate exceeds 1GB, it triggers an immediate eviction pass.

### Logic

1. Get total size (in-memory estimate, or `du -sb data/payloads/` on hourly sync)
2. If over 1GB (1,073,741,824 bytes), delete oldest date directories until total drops below 900MB
3. Set `payload_path = NULL` on invocation_log rows whose files were evicted (batch UPDATE by date)

### Headroom

The 900MB target after eviction leaves ~100MB headroom to avoid thrashing on high-volume days.

---

## 3. API Endpoints

All under the existing admin router.

### GET `/admin/claude/calls`

Paginated call browser.

**Query params:**
- `task_type` (optional, exact match)
- `model` (optional, alias match)
- `date_from`, `date_to` (ISO date strings)
- `min_cost` (float, optional)
- `min_tokens_in` (int, optional)
- `quality_score_below` (float, optional — surfaces low-quality calls)
- `sort` (enum: `cost`, `tokens_in`, `tokens_out`, `latency`, `timestamp`; default `timestamp`)
- `sort_dir` (enum: `asc`, `desc`; default `desc`)
- `limit` (int, default 50, max 200)
- `offset` (int, default 0)

**Response:** Array of invocation metadata objects (no payload content). Includes `has_payload: bool` indicating whether the file still exists.

### GET `/admin/claude/calls/{invocation_id}/payload`

Returns the full request+response JSON from the payload file.

**Response:** The raw payload JSON.
**Errors:** 404 if the file has been evicted or never written.

### GET `/admin/claude/insights`

Proactive optimization analysis. Cached for 5 minutes.

**Query params:**
- `days` (int, default 7)

**Response:**

```json
{
  "top_cost_centers": [
    {"task_type": "...", "total_cost": 0.0, "call_count": 0, "avg_tokens_in": 0, "avg_tokens_out": 0}
  ],
  "system_prompt_groups": [
    {"hash": "...", "token_count": 0, "call_count": 0, "estimated_weekly_cost": 0.0, "sample_invocation_id": "..."}
  ],
  "quality_cost_mismatches": [
    {"task_type": "...", "avg_cost": 0.0, "avg_quality_score": 0.0, "call_count": 0}
  ],
  "token_bloat_outliers": [
    {"invocation_id": "...", "task_type": "...", "tokens_in": 0, "median_for_type": 0, "ratio": 0.0, "cost_usd": 0.0}
  ]
}
```

---

## 4. Insights Engine

Async function invoked by the `/insights` endpoint, result cached in-memory for 5 minutes.

### Insight 1: Top Cost Centers

Pure SQL against `invocation_log`. GROUP BY `task_type`, SUM `cost_usd`, AVG `tokens_in`/`tokens_out`. Top 10 by total cost.

### Insight 2: System Prompt Grouping

Uses the `input_hash` column (populated at write time). Groups by hash, counts occurrences, estimates cost contribution. For display, reads one sample payload file per group to show prompt preview text.

If `input_hash` backfill hasn't run yet, falls back to reading payload files directly (slower, bounded to 7 days).

### Insight 3: Quality/Cost Mismatches

SQL: task types where average `cost_usd` is above the global p75 AND average `quality_score` is below 0.5. Also surfaces individual calls with cost > p75 and quality_score < 0.5.

### Insight 4: Token Bloat Outliers

SQL: per task_type, compute median `tokens_in`. Flag individual calls where tokens_in > 2x median. Return top 10 by cost_usd descending.

---

## 5. Dashboard UI — Claude Inspector Page

New page in the dashboard, accessible from the main nav.

### Layout

Two-panel vertical layout:

#### Top: Insights Summary

- 3-4 cards, each representing one optimization opportunity
- Card contents: short description, estimated savings, link to filter the call browser
- Loaded on page mount, not on the 30s auto-refresh cycle (expensive to compute)

#### Bottom: Call Browser

- **Filter bar:** dropdowns for task_type and model, date range picker, "expensive only" toggle (min_cost > p75)
- **Table** (TanStack Table): timestamp, task_type, model, tokens_in, tokens_out, cost, quality_score, latency
- Column headers sortable
- Click row to expand inline detail panel

#### Detail Panel (expanded row)

- Full request: system prompt, messages array, tools definition
- Full response: content blocks, stop_reason
- Syntax-highlighted JSON with collapsible sections
- Per-message token count annotation
- Copy-to-clipboard button for prompt text
- "Compare" checkbox — selecting 2 calls shows a side-by-side diff view
- "Similar calls" link (same task_type, same day)

### Patterns

Follows existing dashboard conventions:
- React 18 + CSS Modules
- TanStack Table for the data grid
- Fetch via existing API client pattern
- Date range consistent with other dashboard cards

---

## 6. File/Directory Structure

```
data/payloads/                          # Payload storage root
  2026-05-16/
    {invocation_id}.json

src/donna/
  collection/
    payload_writer.py                   # Fire-and-forget payload file writer
    payload_evictor.py                  # FIFO eviction logic
  api/routes/
    admin_claude.py                     # New endpoints
  insights/
    engine.py                           # Insights computation + caching

donna-ui/src/
  pages/ClaudeInspector/
    index.tsx                           # Page layout
    InsightsPanel.tsx                   # Top cards
    CallBrowser.tsx                     # Table + filters
    CallDetail.tsx                      # Expanded row payload view
    CallCompare.tsx                     # Side-by-side diff
    claude-inspector.module.css
  api/
    claude.ts                           # API client functions
```

---

## 7. Migration

Single Alembic migration:
- Add `payload_path` (STRING(300), nullable) to `invocation_log`
- Backfill `input_hash` is NOT required (new writes will populate it going forward; insights engine handles the gap gracefully)

---

## 8. Non-Goals

- Real-time streaming of calls (not needed — page load + manual refresh is fine)
- Prompt editing/replay from the UI (future work if useful)
- Alerting on specific patterns (the existing anomaly detection on the main dashboard covers this)
- Full-text search across prompt content (file grep is sufficient for now; could add later with SQLite FTS if volume warrants)
