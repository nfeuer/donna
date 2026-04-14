# Donna UI Wave 8 — Shadow + Preferences Migration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Strip AntD from `pages/Shadow` and `pages/Preferences`, rebuild both as single scrollable pages (no tabs), add a `ComparisonDrawer` for Shadow, fix the `RuleDetailDrawer` N+1 perf bug via a bundled backend change, and prove zero AntD imports remain in migrated areas.

**Architecture:** Two parallel tracks (Shadow and Preferences) share no code dependencies. A foundation phase lands the backend `?rule_id=` param and updates test mock shapes first — then the tracks fan out and can run in parallel subagents. Both tracks follow the same pattern: migrate child components first (tables, charts, drawers), then rebuild the parent `index.tsx` last so it can import the already-migrated children.

**Tech Stack:** React 18, Radix primitives, TanStack Table, Recharts (via `charts/` primitives — `ChartCard`, `LineChart`, `useChartColors`), CSS Modules with design tokens, Playwright, Python/FastAPI (backend `admin_preferences.py`).

---

## Reality-Check Preamble (read before starting)

**RefreshButton is still on AntD.** `src/components/RefreshButton.tsx` imports `Button`, `Space`, `Typography` from `antd`. It's consumed by Shadow and Preferences (and Dashboard, Tasks, Agents). The wave 8 exit grep checks only `src/pages/Shadow/` and `src/pages/Preferences/` — since RefreshButton lives in `src/components/`, the grep passes. Wave 9 cleanup handles the remaining shared components. **Do not migrate RefreshButton in this wave** — import it as-is.

**`STATUS_COLORS` is consumed by both pages.** Both `Shadow/index.tsx` and `Preferences/index.tsx` import `STATUS_COLORS` from `../../theme/darkTheme`. The new code uses `useChartColors()` for chart colors and inline CSS vars for semantic colors (success, warning, error). After migration, neither page should import from `darkTheme.ts`.

**`CHART_COLORS` is consumed by `ShadowCharts.tsx` and `ComparisonTable.tsx`.** The old AntD palette (`#1890ff`, etc.) is replaced by `useChartColors()` which resolves CSS design tokens at runtime.

**`exportToCsv` stays.** `SpotCheckTable` and `CorrectionsTable` both use `exportToCsv` from `../../utils/csvExport`. This utility has no AntD dependency — keep using it.

**No ScatterChart primitive exists.** The current `ShadowCharts` uses a recharts `ScatterChart` directly. The spec replaces the two charts with:
1. **Quality Δ over time** — `LineChart` from `charts/` using `stats.trend` data. The scatter is dropped.
2. **Cost savings** — `ChartCard` without a chart body (metric-only), showing the cost delta as the headline number with primary/shadow cost in the stat strip. The API (`ShadowStats`) doesn't provide a cost time series, so a line chart would require a new endpoint. Keep it simple: metric-only card.

**Backend column check.** The `correction_log` table has a `rule_extracted` column (nullable text). To filter by `rule_id`, we match against `rule_extracted = ?` (the value is the rule ID string, not a FK). Confirmed from `admin_preferences.py:182` — the column is `rule_extracted`.

Wait, let me verify — `RuleDetailDrawer` currently filters by `supporting_corrections` (an array of correction IDs on the rule). The backend `?rule_id=` param should filter corrections where `rule_extracted = ?` — this returns corrections that were used to extract this rule. But looking at the drawer code more carefully:

```
const ids = new Set(rule.supporting_corrections);
fetchCorrections({ limit: 500 })
  .then((resp) => resp.corrections.filter((c) => ids.has(c.id)))
```

It fetches ALL corrections and filters by the IDs in `rule.supporting_corrections`. So `rule_id` is the rule's own ID, and we need to filter corrections whose `id` is in the rule's `supporting_corrections` list. But that's a list filter, not a simple `WHERE rule_extracted = ?`.

**Corrected backend approach:** Add `?rule_id=<id>` to the corrections endpoint. The handler:
1. Looks up the rule by ID to get its `supporting_corrections` list.
2. Filters `correction_log` with `WHERE id IN (...)`.
This keeps the filter server-side and avoids fetching 500 rows.

**Branch**: work in `wave-8-shadow-preferences` off `main`. If a git worktree is being used (recommended via `superpowers:using-git-worktrees`), this branch is created automatically.

---

## File Structure

### New files

- `donna-ui/src/pages/Shadow/Shadow.module.css` — section layout, chart grid, section headings
- `donna-ui/src/pages/Shadow/ComparisonDrawer.tsx` — full detail view for a comparison row
- `donna-ui/src/pages/Shadow/ComparisonDrawer.module.css` — side-by-side panels, metadata row
- `donna-ui/src/pages/Preferences/Preferences.module.css` — section layout, section headings

### Modified files

- `src/donna/api/routes/admin_preferences.py` — add `rule_id` query param to `list_corrections` (line 150)
- `donna-ui/src/api/preferences.ts` — add `rule_id` to `CorrectionFilters` and `fetchCorrections`
- `donna-ui/tests/e2e/helpers.ts` — add proper mock shapes for shadow + preferences endpoints
- `donna-ui/src/pages/Shadow/index.tsx` — rebuild as one scrollable page, strip AntD
- `donna-ui/src/pages/Shadow/ShadowCharts.tsx` — rewrite: two inline `ChartCard`s, drop scatter
- `donna-ui/src/pages/Shadow/ComparisonTable.tsx` — migrate to `DataTable`, row click → drawer
- `donna-ui/src/pages/Shadow/SpotCheckTable.tsx` — migrate to `DataTable`
- `donna-ui/src/pages/Preferences/index.tsx` — rebuild as one scrollable page, strip AntD
- `donna-ui/src/pages/Preferences/RulesTable.tsx` — migrate to `DataTable`
- `donna-ui/src/pages/Preferences/CorrectionsTable.tsx` — migrate to `DataTable`
- `donna-ui/src/pages/Preferences/RuleDetailDrawer.tsx` — migrate to primitive `Drawer`, use `?rule_id=`
- `donna-ui/tests/e2e/smoke/shadow.spec.ts` — expand coverage
- `donna-ui/tests/e2e/smoke/preferences.spec.ts` — expand coverage

### Not touched

- `donna-ui/src/charts/theme.ts` (API boundary — wave 3 set it, wave 8 reads it)
- `donna-ui/src/components/RefreshButton.tsx` (shared, still on AntD, wave 9)
- Nav rail / routing (no subroute split needed)

---

## Phases

```
Phase 1 (serial): Foundation — Tasks 1..3
Phase 2a (parallel with 2b): Shadow track — Tasks 4..8
Phase 2b (parallel with 2a): Preferences track — Tasks 9..12
Phase 3 (serial): Integration — Tasks 13..16
```

Subagents in phase 2 can be dispatched in parallel per track. Inside a track, tasks are serial.

---

# Phase 1 — Foundation

## Task 1: Backend — add `rule_id` query param to corrections endpoint

**Files:**
- Modify: `src/donna/api/routes/admin_preferences.py:150-212`
- Create: `tests/test_api_corrections_rule_filter.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_api_corrections_rule_filter.py
"""Integration test: GET /admin/preferences/corrections?rule_id=<id> returns
only the corrections whose IDs appear in the rule's supporting_corrections list."""

import pytest
import aiosqlite
from httpx import AsyncClient, ASGITransport
from donna.api.app import create_app


@pytest.fixture
async def seeded_db(tmp_path):
    """Create a temp DB with a rule + corrections for testing."""
    db_path = str(tmp_path / "test.db")
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("""
            CREATE TABLE correction_log (
                id TEXT PRIMARY KEY,
                timestamp TEXT DEFAULT '2026-04-01T12:00:00Z',
                user_id TEXT DEFAULT 'u1',
                task_type TEXT DEFAULT 'parse_task',
                task_id TEXT DEFAULT 't1',
                input_text TEXT,
                field_corrected TEXT DEFAULT 'priority',
                original_value TEXT DEFAULT 'low',
                corrected_value TEXT DEFAULT 'high',
                rule_extracted TEXT
            )
        """)
        await conn.execute("""
            CREATE TABLE preference_rules (
                id TEXT PRIMARY KEY,
                user_id TEXT DEFAULT 'u1',
                rule_type TEXT DEFAULT 'priority',
                rule_text TEXT DEFAULT 'test rule',
                confidence REAL DEFAULT 0.9,
                condition TEXT,
                action TEXT,
                supporting_corrections TEXT DEFAULT '[]',
                enabled INTEGER DEFAULT 1,
                created_at TEXT DEFAULT '2026-04-01T12:00:00Z',
                disabled_at TEXT
            )
        """)
        # Insert 3 corrections
        for i, cid in enumerate(["c1", "c2", "c3"]):
            await conn.execute(
                "INSERT INTO correction_log (id, field_corrected, original_value, corrected_value) VALUES (?, ?, ?, ?)",
                (cid, "priority", f"low{i}", f"high{i}"),
            )
        # Insert a rule referencing c1 and c3
        import json
        await conn.execute(
            "INSERT INTO preference_rules (id, supporting_corrections) VALUES (?, ?)",
            ("r1", json.dumps(["c1", "c3"])),
        )
        await conn.commit()
    return db_path


@pytest.mark.asyncio
async def test_corrections_filtered_by_rule_id(seeded_db):
    app = create_app(db_path=seeded_db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Without rule_id — returns all 3
        resp = await client.get("/admin/preferences/corrections")
        assert resp.status_code == 200
        assert resp.json()["total"] == 3

        # With rule_id — returns only c1 and c3
        resp = await client.get("/admin/preferences/corrections", params={"rule_id": "r1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        ids = {c["id"] for c in data["corrections"]}
        assert ids == {"c1", "c3"}

        # With nonexistent rule_id — returns empty
        resp = await client.get("/admin/preferences/corrections", params={"rule_id": "nonexistent"})
        assert resp.status_code == 200
        assert resp.json()["total"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/feuer/Documents/Projects/donna && python -m pytest tests/test_api_corrections_rule_filter.py -v`
Expected: FAIL — `rule_id` param not implemented yet.

- [ ] **Step 3: Implement the backend change**

In `src/donna/api/routes/admin_preferences.py`, modify the `list_corrections` handler to accept an optional `rule_id` parameter:

```python
@router.get("/preferences/corrections")
async def list_corrections(
    request: Request,
    field: str | None = Query(default=None),
    task_type: str | None = Query(default=None),
    rule_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Paginated correction log with optional filters."""
    conn = request.app.state.db.connection

    where_clauses: list[str] = []
    params: list[Any] = []

    if field:
        where_clauses.append("field_corrected = ?")
        params.append(field)
    if task_type:
        where_clauses.append("task_type = ?")
        params.append(task_type)

    # Filter by rule's supporting_corrections list
    if rule_id:
        import json as _json
        rule_cursor = await conn.execute(
            "SELECT supporting_corrections FROM preference_rules WHERE id = ?",
            [rule_id],
        )
        rule_row = await rule_cursor.fetchone()
        if rule_row:
            correction_ids: list[str] = _json.loads(rule_row[0]) if rule_row[0] else []
            if correction_ids:
                placeholders = ",".join("?" for _ in correction_ids)
                where_clauses.append(f"id IN ({placeholders})")
                params.extend(correction_ids)
            else:
                # Rule has no supporting corrections — return empty
                return {"corrections": [], "total": 0, "limit": limit, "offset": offset}
        else:
            # Rule not found — return empty
            return {"corrections": [], "total": 0, "limit": limit, "offset": offset}

    # Safe: {where} is built from static column names; user values go through params
    where = " AND ".join(where_clauses) if where_clauses else "1=1"

    cursor = await conn.execute(
        f"SELECT COUNT(*) FROM correction_log WHERE {where}", params
    )
    total = (await cursor.fetchone())[0]

    cursor = await conn.execute(
        f"""SELECT id, timestamp, user_id, task_type, task_id,
                   input_text, field_corrected, original_value,
                   corrected_value, rule_extracted
            FROM correction_log
            WHERE {where}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?""",
        params + [limit, offset],
    )
    rows = await cursor.fetchall()

    corrections = [
        {
            "id": row[0],
            "timestamp": row[1],
            "user_id": row[2],
            "task_type": row[3],
            "task_id": row[4],
            "input_text": row[5],
            "field_corrected": row[6],
            "original_value": row[7],
            "corrected_value": row[8],
            "rule_extracted": row[9],
        }
        for row in rows
    ]

    return {
        "corrections": corrections,
        "total": total,
        "limit": limit,
        "offset": offset,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/feuer/Documents/Projects/donna && python -m pytest tests/test_api_corrections_rule_filter.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/donna/api/routes/admin_preferences.py tests/test_api_corrections_rule_filter.py
git commit -m "feat(api): add ?rule_id= filter to corrections endpoint"
```

---

## Task 2: Frontend API — add `rule_id` to `fetchCorrections`

**Files:**
- Modify: `donna-ui/src/api/preferences.ts`

- [ ] **Step 1: Add `rule_id` to the filter type and function**

In `donna-ui/src/api/preferences.ts`, update `CorrectionFilters` and `fetchCorrections`:

```ts
export interface CorrectionFilters {
  field?: string;
  task_type?: string;
  rule_id?: string;
  limit?: number;
  offset?: number;
}

export async function fetchCorrections(
  filters: CorrectionFilters = {},
): Promise<CorrectionsResponse> {
  const params: Record<string, string | number> = {};
  if (filters.field) params.field = filters.field;
  if (filters.task_type) params.task_type = filters.task_type;
  if (filters.rule_id) params.rule_id = filters.rule_id;
  params.limit = filters.limit ?? 50;
  params.offset = filters.offset ?? 0;
  const { data } = await client.get("/admin/preferences/corrections", {
    params,
  });
  return data;
}
```

- [ ] **Step 2: Typecheck**

Run: `cd donna-ui && npx tsc -b --noEmit`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add donna-ui/src/api/preferences.ts
git commit -m "feat(api): add rule_id filter to fetchCorrections"
```

---

## Task 3: Test helpers — proper mock shapes for shadow + preferences endpoints

**Files:**
- Modify: `donna-ui/tests/e2e/helpers.ts`

**Why now:** The existing mock falls through to a bare `"[]"` or `"{}"` for shadow and preferences endpoints. The expanded smoke tests in Tasks 13–14 need realistic response shapes.

- [ ] **Step 1: Add shadow and preferences mock routes**

Add the following route handlers inside the `mockAdminApi` function, **above** the final catch-all block:

```ts
    // /admin/shadow/comparisons
    if (url.match(/\/admin\/shadow\/comparisons(\?|$)/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          comparisons: [
            {
              primary: {
                id: "p1",
                timestamp: "2026-04-01T12:00:00Z",
                task_type: "parse_task",
                task_id: "t1",
                model_alias: "claude-sonnet",
                model_actual: "claude-sonnet-4-20250514",
                input_hash: "abc",
                latency_ms: 450,
                tokens_in: 200,
                tokens_out: 100,
                cost_usd: 0.0025,
                output: { title: "Primary output" },
                quality_score: 0.82,
                is_shadow: false,
                spot_check_queued: false,
                user_id: "u1",
              },
              shadow: {
                id: "s1",
                timestamp: "2026-04-01T12:00:00Z",
                task_type: "parse_task",
                task_id: "t1",
                model_alias: "qwen-32b",
                model_actual: "qwen2.5:32b-instruct-q6_K",
                input_hash: "abc",
                latency_ms: 1200,
                tokens_in: 200,
                tokens_out: 110,
                cost_usd: 0.0,
                output: { title: "Shadow output" },
                quality_score: 0.91,
                is_shadow: true,
                spot_check_queued: false,
                user_id: "u1",
              },
              quality_delta: 0.09,
            },
          ],
          total: 1,
        }),
      });
    }

    // /admin/shadow/stats
    if (url.match(/\/admin\/shadow\/stats(\?|$)/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          primary_avg_quality: 0.82,
          shadow_avg_quality: 0.91,
          avg_delta: 0.09,
          wins: 34,
          losses: 12,
          ties: 6,
          primary_cost: 5.2,
          shadow_cost: 1.0,
          primary_count: 52,
          shadow_count: 52,
          trend: [
            { date: "2026-03-25", avg_quality: 0.85, count: 8 },
            { date: "2026-03-26", avg_quality: 0.88, count: 10 },
            { date: "2026-03-27", avg_quality: 0.91, count: 7 },
          ],
          days: 30,
        }),
      });
    }

    // /admin/shadow/spot-checks
    if (url.match(/\/admin\/shadow\/spot-checks(\?|$)/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            {
              id: "sc1",
              timestamp: "2026-04-01T10:00:00Z",
              task_type: "parse_task",
              task_id: "t2",
              model_alias: "claude-sonnet",
              model_actual: "claude-sonnet-4-20250514",
              latency_ms: 300,
              tokens_in: 150,
              tokens_out: 80,
              cost_usd: 0.0018,
              quality_score: 0.88,
              is_shadow: false,
              spot_check_queued: true,
              user_id: "u1",
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      });
    }

    // /admin/preferences/rules
    if (url.match(/\/admin\/preferences\/rules(\?|$)/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          rules: [
            {
              id: "r1",
              user_id: "u1",
              rule_type: "scheduling",
              rule_text: "Morning deep work blocks before 11am",
              confidence: 0.91,
              condition: { time_before: "11:00" },
              action: { block_type: "deep_work" },
              supporting_corrections: ["c1", "c2"],
              enabled: true,
              created_at: "2026-03-15T08:00:00Z",
              disabled_at: null,
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      });
    }

    // /admin/preferences/rules/:id (PATCH toggle)
    if (url.match(/\/admin\/preferences\/rules\/[^/?]+/) && route.request().method() === "PATCH") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "r1",
          user_id: "u1",
          rule_type: "scheduling",
          rule_text: "Morning deep work blocks before 11am",
          confidence: 0.91,
          condition: { time_before: "11:00" },
          action: { block_type: "deep_work" },
          supporting_corrections: ["c1", "c2"],
          enabled: true,
          created_at: "2026-03-15T08:00:00Z",
          disabled_at: null,
        }),
      });
    }

    // /admin/preferences/corrections (with optional ?rule_id=)
    if (url.match(/\/admin\/preferences\/corrections(\?|$)/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          corrections: [
            {
              id: "c1",
              timestamp: "2026-04-01T09:00:00Z",
              user_id: "u1",
              task_type: "parse_task",
              task_id: "t1",
              input_text: "Schedule standup for tomorrow",
              field_corrected: "priority",
              original_value: "low",
              corrected_value: "high",
              rule_extracted: "r1",
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      });
    }

    // /admin/preferences/stats
    if (url.match(/\/admin\/preferences\/stats(\?|$)/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          total_rules: 14,
          active_rules: 11,
          disabled_rules: 3,
          avg_confidence: 0.82,
          total_corrections: 87,
          top_fields: [
            { field: "priority", count: 34 },
            { field: "deadline", count: 22 },
            { field: "domain", count: 18 },
          ],
        }),
      });
    }
```

- [ ] **Step 2: Verify existing smoke tests still pass**

Run: `cd donna-ui && npx playwright test`
Expected: ALL PASS.

- [ ] **Step 3: Commit**

```bash
git add donna-ui/tests/e2e/helpers.ts
git commit -m "test(helpers): add shadow + preferences mock shapes for wave 8 smoke tests"
```

---

# Phase 2a — Shadow Track

## Task 4: ShadowCharts.tsx → two inline ChartCards

**Files:**
- Modify: `donna-ui/src/pages/Shadow/ShadowCharts.tsx`

**Why first:** The parent `index.tsx` (Task 8) imports this component. Migrate it before rebuilding the parent.

- [ ] **Step 1: Rewrite ShadowCharts**

Replace the entire file content:

```tsx
// donna-ui/src/pages/Shadow/ShadowCharts.tsx
import { ChartCard, LineChart, type ChartCardStat } from "../../charts";
import type { ShadowComparison, ShadowStats } from "../../api/shadow";
import styles from "./Shadow.module.css";

interface Props {
  comparisons: ShadowComparison[];
  stats: ShadowStats | null;
  loading: boolean;
}

export default function ShadowCharts({ comparisons, stats, loading }: Props) {
  const trendData = stats?.trend ?? [];

  const qualityStats: ChartCardStat[] = [
    { label: "Wins", value: stats?.wins ?? 0 },
    { label: "Losses", value: stats?.losses ?? 0 },
    { label: "Ties", value: stats?.ties ?? 0 },
    { label: "Count", value: stats?.primary_count ?? 0 },
  ];

  const saved = stats ? stats.primary_cost - stats.shadow_cost : 0;
  const costStats: ChartCardStat[] = [
    { label: "Primary", value: `$${(stats?.primary_cost ?? 0).toFixed(2)}` },
    { label: "Shadow", value: `$${(stats?.shadow_cost ?? 0).toFixed(2)}` },
    { label: "Comparisons", value: stats?.primary_count ?? 0 },
  ];

  return (
    <div className={styles.chartGrid}>
      <ChartCard
        eyebrow="Quality Δ over time"
        metric={stats?.avg_delta != null ? (stats.avg_delta > 0 ? "+" : "") + stats.avg_delta.toFixed(4) : "—"}
        delta={
          stats?.avg_delta != null
            ? { value: stats.avg_delta * 100, label: "shadow vs primary" }
            : undefined
        }
        chart={
          trendData.length > 0 ? (
            <LineChart
              data={trendData}
              series={[{ dataKey: "avg_quality", name: "Avg Quality" }]}
              xKey="date"
              formatTick={(v) => v.slice(5)}
              formatValue={(v) => v.toFixed(2)}
              ariaLabel="Shadow quality trend over time"
            />
          ) : undefined
        }
        stats={qualityStats}
        loading={loading}
      />
      <ChartCard
        eyebrow="Cost savings"
        metric={`$${Math.abs(saved).toFixed(2)}`}
        metricSuffix={saved >= 0 ? "saved" : "overspend"}
        stats={costStats}
        loading={loading}
      />
    </div>
  );
}
```

Note: This references `styles.chartGrid` which will be created in Task 7 (Shadow CSS module). For now, the class is a no-op — the component renders correctly without it, just without the 2-col grid. Task 7 and 8 land the CSS and parent together.

- [ ] **Step 2: Typecheck**

Run: `cd donna-ui && npx tsc -b --noEmit`
Expected: May fail if `Shadow.module.css` doesn't exist yet. If so, create a minimal placeholder:

```bash
echo ".chartGrid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-4); }" > donna-ui/src/pages/Shadow/Shadow.module.css
```

Then rerun typecheck. Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add donna-ui/src/pages/Shadow/ShadowCharts.tsx donna-ui/src/pages/Shadow/Shadow.module.css
git commit -m "feat(shadow): rewrite ShadowCharts as two ChartCard components"
```

---

## Task 5: ComparisonTable.tsx → DataTable with row click

**Files:**
- Modify: `donna-ui/src/pages/Shadow/ComparisonTable.tsx`

- [ ] **Step 1: Rewrite ComparisonTable**

Replace the entire file content:

```tsx
// donna-ui/src/pages/Shadow/ComparisonTable.tsx
import { useMemo } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { DataTable, Pill, type PillVariant } from "../../primitives";
import type { ShadowComparison } from "../../api/shadow";

interface Props {
  comparisons: ShadowComparison[];
  loading: boolean;
  selectedId?: string | null;
  onRowClick?: (row: ShadowComparison) => void;
}

function getRowId(row: ShadowComparison): string {
  return `${row.primary.id}-${row.shadow.id}`;
}

function deltaVariant(delta: number | null): PillVariant {
  if (delta === null) return "muted";
  if (delta > 0.05) return "success";
  if (delta < -0.05) return "error";
  return "warning";
}

function formatTs(ts: string): string {
  return ts.replace("T", " ").substring(0, 19);
}

export default function ComparisonTable({ comparisons, loading, selectedId, onRowClick }: Props) {
  const columns = useMemo<ColumnDef<ShadowComparison>[]>(
    () => [
      {
        accessorFn: (row) => row.primary.task_type,
        id: "task_type",
        header: "Task Type",
        size: 140,
        cell: ({ getValue }) => (
          <Pill variant="accent">{getValue<string>()}</Pill>
        ),
      },
      {
        accessorFn: (row) => row.primary.timestamp,
        id: "timestamp",
        header: "Timestamp",
        size: 170,
        cell: ({ getValue }) => formatTs(getValue<string>()),
      },
      {
        accessorFn: (row) => row.primary.model_alias,
        id: "primary_model",
        header: "Primary",
        size: 120,
      },
      {
        accessorFn: (row) => row.shadow.model_alias,
        id: "shadow_model",
        header: "Shadow",
        size: 120,
      },
      {
        accessorFn: (row) => row.primary.quality_score,
        id: "primary_q",
        header: "P. Quality",
        size: 100,
        cell: ({ getValue }) => {
          const v = getValue<number | null>();
          return v != null ? v.toFixed(3) : "—";
        },
      },
      {
        accessorFn: (row) => row.shadow.quality_score,
        id: "shadow_q",
        header: "S. Quality",
        size: 100,
        cell: ({ getValue }) => {
          const v = getValue<number | null>();
          return v != null ? v.toFixed(3) : "—";
        },
      },
      {
        accessorKey: "quality_delta",
        header: "Δ",
        size: 90,
        cell: ({ getValue }) => {
          const v = getValue<number | null>();
          if (v == null) return "—";
          return (
            <Pill variant={deltaVariant(v)}>
              {v > 0 ? "+" : ""}{v.toFixed(4)}
            </Pill>
          );
        },
      },
      {
        id: "cost",
        header: "Cost (P/S)",
        size: 130,
        cell: ({ row }) => (
          <span style={{ fontSize: "var(--text-small)" }}>
            ${row.original.primary.cost_usd.toFixed(4)} / ${row.original.shadow.cost_usd.toFixed(4)}
          </span>
        ),
      },
    ],
    [],
  );

  return (
    <DataTable
      data={comparisons}
      columns={columns}
      getRowId={getRowId}
      onRowClick={onRowClick}
      selectedRowId={selectedId}
      keyboardNav
      loading={loading}
      pageSize={20}
      emptyState="No shadow comparisons found"
    />
  );
}
```

- [ ] **Step 2: Typecheck**

Run: `cd donna-ui && npx tsc -b --noEmit`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add donna-ui/src/pages/Shadow/ComparisonTable.tsx
git commit -m "feat(shadow): migrate ComparisonTable to DataTable with row click"
```

---

## Task 6: ComparisonDrawer (new)

**Files:**
- Create: `donna-ui/src/pages/Shadow/ComparisonDrawer.tsx`
- Create: `donna-ui/src/pages/Shadow/ComparisonDrawer.module.css`

- [ ] **Step 1: Create the CSS module**

```css
/* donna-ui/src/pages/Shadow/ComparisonDrawer.module.css */
.header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-4);
}

.panels {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}

.panelLabel {
  font-size: var(--text-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--color-text-muted);
  margin-bottom: var(--space-2);
}

.panelContent {
  background: var(--color-inset);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  padding: var(--space-3);
  max-height: 300px;
  overflow: auto;
  font-family: var(--font-mono);
  font-size: var(--text-small);
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--color-text);
}

.inputBlock {
  margin-bottom: var(--space-4);
}

.inputLabel {
  font-size: var(--text-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--color-text-muted);
  margin-bottom: var(--space-2);
}

.inputContent {
  background: var(--color-inset);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  padding: var(--space-3);
  max-height: 200px;
  overflow: auto;
  font-family: var(--font-mono);
  font-size: var(--text-small);
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--color-text);
}

.metaRow {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border);
}

.metaItem {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.metaLabel {
  font-size: var(--text-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--color-text-muted);
}

.metaValue {
  font-size: var(--text-small);
  color: var(--color-text);
  font-family: var(--font-mono);
}

@media (max-width: 600px) {
  .panels {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 2: Create ComparisonDrawer component**

```tsx
// donna-ui/src/pages/Shadow/ComparisonDrawer.tsx
import { Drawer, Pill, type PillVariant } from "../../primitives";
import type { ShadowComparison } from "../../api/shadow";
import styles from "./ComparisonDrawer.module.css";

interface Props {
  comparison: ShadowComparison | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function outcomeLabel(delta: number | null): { text: string; variant: PillVariant } {
  if (delta === null) return { text: "N/A", variant: "muted" };
  if (delta > 0.05) return { text: "Shadow wins", variant: "success" };
  if (delta < -0.05) return { text: "Primary wins", variant: "error" };
  return { text: "Tie", variant: "warning" };
}

function formatTs(ts: string): string {
  return ts.replace("T", " ").substring(0, 19);
}

function formatOutput(output: Record<string, unknown> | null): string {
  if (!output) return "(no output)";
  return JSON.stringify(output, null, 2);
}

export default function ComparisonDrawer({ comparison, open, onOpenChange }: Props) {
  if (!comparison) return null;

  const { primary, shadow, quality_delta } = comparison;
  const outcome = outcomeLabel(quality_delta);

  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      title="Comparison Detail"
    >
      <div className={styles.header}>
        <Pill variant="accent">{primary.task_type}</Pill>
        <span style={{ color: "var(--color-text-muted)", fontSize: "var(--text-small)" }}>
          {formatTs(primary.timestamp)}
        </span>
        <Pill variant={outcome.variant}>{outcome.text}</Pill>
      </div>

      {primary.input_hash && (
        <div className={styles.inputBlock}>
          <div className={styles.inputLabel}>Input hash</div>
          <div className={styles.inputContent}>{primary.input_hash}</div>
        </div>
      )}

      <div className={styles.panels}>
        <div>
          <div className={styles.panelLabel}>Primary output</div>
          <div className={styles.panelContent}>{formatOutput(primary.output ?? null)}</div>
        </div>
        <div>
          <div className={styles.panelLabel}>Shadow output</div>
          <div className={styles.panelContent}>{formatOutput(shadow.output ?? null)}</div>
        </div>
      </div>

      <div className={styles.metaRow}>
        <div className={styles.metaItem}>
          <span className={styles.metaLabel}>Primary model</span>
          <span className={styles.metaValue}>{primary.model_alias}</span>
        </div>
        <div className={styles.metaItem}>
          <span className={styles.metaLabel}>Shadow model</span>
          <span className={styles.metaValue}>{shadow.model_alias}</span>
        </div>
        <div className={styles.metaItem}>
          <span className={styles.metaLabel}>P. latency</span>
          <span className={styles.metaValue}>{primary.latency_ms}ms</span>
        </div>
        <div className={styles.metaItem}>
          <span className={styles.metaLabel}>S. latency</span>
          <span className={styles.metaValue}>{shadow.latency_ms}ms</span>
        </div>
        <div className={styles.metaItem}>
          <span className={styles.metaLabel}>P. cost</span>
          <span className={styles.metaValue}>${primary.cost_usd.toFixed(4)}</span>
        </div>
        <div className={styles.metaItem}>
          <span className={styles.metaLabel}>S. cost</span>
          <span className={styles.metaValue}>${shadow.cost_usd.toFixed(4)}</span>
        </div>
        <div className={styles.metaItem}>
          <span className={styles.metaLabel}>Quality Δ</span>
          <span className={styles.metaValue}>
            {quality_delta != null ? (quality_delta > 0 ? "+" : "") + quality_delta.toFixed(4) : "—"}
          </span>
        </div>
      </div>
    </Drawer>
  );
}
```

- [ ] **Step 3: Typecheck**

Run: `cd donna-ui && npx tsc -b --noEmit`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add donna-ui/src/pages/Shadow/ComparisonDrawer.tsx donna-ui/src/pages/Shadow/ComparisonDrawer.module.css
git commit -m "feat(shadow): add ComparisonDrawer for experiment detail view"
```

---

## Task 7: SpotCheckTable.tsx → DataTable

**Files:**
- Modify: `donna-ui/src/pages/Shadow/SpotCheckTable.tsx`

- [ ] **Step 1: Rewrite SpotCheckTable**

Replace the entire file content:

```tsx
// donna-ui/src/pages/Shadow/SpotCheckTable.tsx
import { useMemo } from "react";
import { Download } from "lucide-react";
import type { ColumnDef } from "@tanstack/react-table";
import { DataTable, Pill, Button } from "../../primitives";
import type { SpotCheckItem } from "../../api/shadow";
import { exportToCsv } from "../../utils/csvExport";

interface Props {
  items: SpotCheckItem[];
  total: number;
  loading: boolean;
}

function formatTs(ts: string): string {
  return ts.replace("T", " ").substring(0, 19);
}

export default function SpotCheckTable({ items, total, loading }: Props) {
  const handleExport = () => {
    exportToCsv("spot-checks", [
      { key: "timestamp", title: "Timestamp" },
      { key: "task_type", title: "Task Type" },
      { key: "model_alias", title: "Model" },
      { key: "quality_score", title: "Quality Score" },
      { key: "is_shadow", title: "Shadow" },
      { key: "spot_check_queued", title: "Queued" },
      { key: "latency_ms", title: "Latency (ms)" },
      { key: "cost_usd", title: "Cost (USD)" },
    ], items as unknown as Record<string, unknown>[]);
  };

  const columns = useMemo<ColumnDef<SpotCheckItem>[]>(
    () => [
      {
        accessorKey: "timestamp",
        header: "Timestamp",
        size: 170,
        cell: ({ getValue }) => formatTs(getValue<string>()),
      },
      {
        accessorKey: "task_type",
        header: "Task Type",
        size: 140,
        cell: ({ getValue }) => <Pill variant="accent">{getValue<string>()}</Pill>,
      },
      {
        accessorKey: "model_alias",
        header: "Model",
        size: 120,
      },
      {
        accessorKey: "quality_score",
        header: "Quality",
        size: 100,
        cell: ({ getValue }) => {
          const v = getValue<number | null>();
          if (v == null) return <Pill variant="muted">Pending</Pill>;
          const variant = v >= 0.7 ? "success" : "error";
          return <Pill variant={variant}>{Math.round(v * 100)}%</Pill>;
        },
      },
      {
        accessorKey: "is_shadow",
        header: "Shadow",
        size: 80,
        cell: ({ getValue }) => (
          <Pill variant={getValue<boolean>() ? "accent" : "muted"}>
            {getValue<boolean>() ? "Yes" : "No"}
          </Pill>
        ),
      },
      {
        accessorKey: "latency_ms",
        header: "Latency",
        size: 90,
        cell: ({ getValue }) => `${getValue<number>()}ms`,
      },
      {
        accessorKey: "cost_usd",
        header: "Cost",
        size: 90,
        cell: ({ getValue }) => `$${getValue<number>().toFixed(4)}`,
      },
    ],
    [],
  );

  return (
    <>
      <div style={{ marginBottom: "var(--space-2)", textAlign: "right" }}>
        <Button variant="ghost" size="sm" onClick={handleExport}>
          <Download size={14} />
          Export CSV
        </Button>
      </div>
      <DataTable
        data={items}
        columns={columns}
        getRowId={(row) => row.id}
        loading={loading}
        pageSize={50}
        emptyState="No spot-check items flagged for review"
      />
    </>
  );
}
```

- [ ] **Step 2: Typecheck**

Run: `cd donna-ui && npx tsc -b --noEmit`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add donna-ui/src/pages/Shadow/SpotCheckTable.tsx
git commit -m "feat(shadow): migrate SpotCheckTable to DataTable"
```

---

## Task 8: Shadow index.tsx → rebuild as scrollable page

**Files:**
- Modify: `donna-ui/src/pages/Shadow/index.tsx`
- Modify: `donna-ui/src/pages/Shadow/Shadow.module.css` (finalize)

- [ ] **Step 1: Finalize Shadow.module.css**

Replace the placeholder with the full CSS:

```css
/* donna-ui/src/pages/Shadow/Shadow.module.css */
.chartGrid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-4);
  margin-bottom: var(--space-5);
}

.section {
  margin-bottom: var(--space-5);
}

.sectionHeader {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--space-3);
}

.sectionTitle {
  font-family: var(--font-display);
  font-weight: 300;
  font-size: var(--text-section);
  color: var(--color-text);
}

.sectionCount {
  font-size: var(--text-small);
  color: var(--color-text-muted);
  margin-left: var(--space-2);
  font-weight: 400;
  font-family: var(--font-body);
}

.filters {
  display: flex;
  gap: var(--space-2);
}

@media (max-width: 768px) {
  .chartGrid {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 2: Rebuild Shadow index.tsx**

Replace the entire file content:

```tsx
// donna-ui/src/pages/Shadow/index.tsx
import { useState, useCallback, useEffect } from "react";
import { PageHeader, Select, SelectItem } from "../../primitives";
import RefreshButton from "../../components/RefreshButton";
import ShadowCharts from "./ShadowCharts";
import ComparisonTable from "./ComparisonTable";
import SpotCheckTable from "./SpotCheckTable";
import ComparisonDrawer from "./ComparisonDrawer";
import {
  fetchShadowComparisons,
  fetchShadowStats,
  fetchSpotChecks,
  type ShadowComparison,
  type ShadowStats,
  type SpotCheckItem,
} from "../../api/shadow";
import styles from "./Shadow.module.css";

const TASK_TYPE_OPTIONS = [
  { value: "parse_task", label: "parse_task" },
  { value: "classify_priority", label: "classify_priority" },
  { value: "extract_deadline", label: "extract_deadline" },
  { value: "generate_nudge", label: "generate_nudge" },
  { value: "prep_work", label: "prep_work" },
];

const DAYS_OPTIONS = [
  { value: "7", label: "7 days" },
  { value: "14", label: "14 days" },
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
];

export default function ShadowPage() {
  const [taskType, setTaskType] = useState("");
  const [days, setDays] = useState("30");

  // Data
  const [comparisons, setComparisons] = useState<ShadowComparison[]>([]);
  const [stats, setStats] = useState<ShadowStats | null>(null);
  const [spotChecks, setSpotChecks] = useState<SpotCheckItem[]>([]);
  const [spotTotal, setSpotTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  // Drawer
  const [selectedComparison, setSelectedComparison] = useState<ShadowComparison | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const doFetch = useCallback(async () => {
    setLoading(true);
    try {
      const [compResp, statsResp, spotResp] = await Promise.all([
        fetchShadowComparisons({
          task_type: taskType || undefined,
          days: Number(days),
          limit: 50,
        }),
        fetchShadowStats(Number(days)),
        fetchSpotChecks(50, 0),
      ]);
      setComparisons(compResp.comparisons);
      setStats(statsResp);
      setSpotChecks(spotResp.items);
      setSpotTotal(spotResp.total);
    } catch {
      setComparisons([]);
      setStats(null);
      setSpotChecks([]);
      setSpotTotal(0);
    } finally {
      setLoading(false);
    }
  }, [taskType, days]);

  useEffect(() => {
    doFetch();
  }, [doFetch]);

  const handleRowClick = (row: ShadowComparison) => {
    setSelectedComparison(row);
    setDrawerOpen(true);
  };

  const selectedId = selectedComparison
    ? `${selectedComparison.primary.id}-${selectedComparison.shadow.id}`
    : null;

  return (
    <div>
      <PageHeader
        title="Shadow"
        meta="Evaluation comparisons"
        actions={
          <div className={styles.filters}>
            <Select
              value={taskType}
              onValueChange={setTaskType}
              placeholder="All task types"
              aria-label="Filter by task type"
            >
              {TASK_TYPE_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
              ))}
            </Select>
            <Select
              value={days}
              onValueChange={setDays}
              aria-label="Filter by time range"
            >
              {DAYS_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
              ))}
            </Select>
            <RefreshButton onRefresh={doFetch} />
          </div>
        }
      />

      <ShadowCharts comparisons={comparisons} stats={stats} loading={loading} />

      <section className={styles.section}>
        <div className={styles.sectionHeader}>
          <h2 className={styles.sectionTitle}>
            Comparisons
            <span className={styles.sectionCount}>{comparisons.length}</span>
          </h2>
        </div>
        <ComparisonTable
          comparisons={comparisons}
          loading={loading}
          selectedId={selectedId}
          onRowClick={handleRowClick}
        />
      </section>

      <section className={styles.section}>
        <div className={styles.sectionHeader}>
          <h2 className={styles.sectionTitle}>
            Spot Checks
            <span className={styles.sectionCount}>{spotTotal}</span>
          </h2>
        </div>
        <SpotCheckTable
          items={spotChecks}
          total={spotTotal}
          loading={loading}
        />
      </section>

      <ComparisonDrawer
        comparison={selectedComparison}
        open={drawerOpen}
        onOpenChange={setDrawerOpen}
      />
    </div>
  );
}
```

- [ ] **Step 3: Typecheck**

Run: `cd donna-ui && npx tsc -b --noEmit`
Expected: PASS.

- [ ] **Step 4: Verify no AntD imports in Shadow directory**

Run: `grep -rn "antd\|@ant-design" donna-ui/src/pages/Shadow/`
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/pages/Shadow/
git commit -m "feat(shadow): rebuild Shadow page as one scrollable page, zero AntD"
```

---

# Phase 2b — Preferences Track

## Task 9: RulesTable.tsx → DataTable

**Files:**
- Modify: `donna-ui/src/pages/Preferences/RulesTable.tsx`

- [ ] **Step 1: Rewrite RulesTable**

Replace the entire file content:

```tsx
// donna-ui/src/pages/Preferences/RulesTable.tsx
import { useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { DataTable, Pill, Switch, type PillVariant } from "../../primitives";
import { toggleRule, type PreferenceRule } from "../../api/preferences";

interface Props {
  rules: PreferenceRule[];
  loading: boolean;
  onRuleClick: (rule: PreferenceRule) => void;
  onRuleToggled: () => void;
}

const RULE_TYPE_VARIANT: Record<string, PillVariant> = {
  scheduling: "accent",
  priority: "warning",
  domain: "success",
  formatting: "muted",
  delegation: "accent",
};

export default function RulesTable({ rules, loading, onRuleClick, onRuleToggled }: Props) {
  const [toggling, setToggling] = useState<string | null>(null);

  const handleToggle = async (rule: PreferenceRule, checked: boolean, e: React.MouseEvent) => {
    e.stopPropagation();
    setToggling(rule.id);
    try {
      await toggleRule(rule.id, checked);
      onRuleToggled();
    } catch {
      // Silently fail — user sees the switch revert on re-fetch
    } finally {
      setToggling(null);
    }
  };

  const columns = useMemo<ColumnDef<PreferenceRule>[]>(
    () => [
      {
        accessorKey: "rule_type",
        header: "Type",
        size: 110,
        cell: ({ getValue }) => {
          const v = getValue<string>();
          return <Pill variant={RULE_TYPE_VARIANT[v] ?? "muted"}>{v}</Pill>;
        },
      },
      {
        accessorKey: "rule_text",
        header: "Rule",
        cell: ({ getValue }) => (
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", display: "block" }}>
            {getValue<string>()}
          </span>
        ),
      },
      {
        accessorKey: "confidence",
        header: "Confidence",
        size: 100,
        cell: ({ getValue }) => {
          const v = getValue<number>();
          const pct = Math.round(v * 100);
          return (
            <Pill variant={v >= 0.7 ? "success" : "error"}>{pct}%</Pill>
          );
        },
      },
      {
        accessorKey: "enabled",
        header: "Enabled",
        size: 80,
        cell: ({ row }) => (
          <Switch
            checked={row.original.enabled}
            onCheckedChange={(checked) =>
              handleToggle(row.original, checked, event as unknown as React.MouseEvent)
            }
            disabled={toggling === row.original.id}
            aria-label={`Toggle rule ${row.original.rule_text}`}
          />
        ),
      },
      {
        id: "corrections_count",
        header: "Corrections",
        size: 100,
        cell: ({ row }) => row.original.supporting_corrections.length,
      },
      {
        accessorKey: "created_at",
        header: "Created",
        size: 100,
        cell: ({ getValue }) => getValue<string>()?.substring(0, 10),
      },
    ],
    [toggling],
  );

  return (
    <DataTable
      data={rules}
      columns={columns}
      getRowId={(row) => row.id}
      onRowClick={onRuleClick}
      keyboardNav
      loading={loading}
      pageSize={20}
      emptyState="No rules learned yet. Donna picks these up as you correct her."
    />
  );
}
```

**Note:** The `Switch` `onCheckedChange` handler above has a subtle issue — `event` is not accessible inside the cell render. Fix: wrap the `Switch` in a div with `onClick={(e) => e.stopPropagation()}` instead:

```tsx
      {
        accessorKey: "enabled",
        header: "Enabled",
        size: 80,
        cell: ({ row }) => (
          <div onClick={(e) => e.stopPropagation()}>
            <Switch
              checked={row.original.enabled}
              onCheckedChange={(checked) => {
                setToggling(row.original.id);
                toggleRule(row.original.id, checked)
                  .then(() => onRuleToggled())
                  .catch(() => {})
                  .finally(() => setToggling(null));
              }}
              disabled={toggling === row.original.id}
              aria-label={`Toggle rule ${row.original.rule_text}`}
            />
          </div>
        ),
      },
```

Use this corrected version.

- [ ] **Step 2: Typecheck**

Run: `cd donna-ui && npx tsc -b --noEmit`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add donna-ui/src/pages/Preferences/RulesTable.tsx
git commit -m "feat(preferences): migrate RulesTable to DataTable"
```

---

## Task 10: CorrectionsTable.tsx → DataTable

**Files:**
- Modify: `donna-ui/src/pages/Preferences/CorrectionsTable.tsx`

- [ ] **Step 1: Rewrite CorrectionsTable**

Replace the entire file content:

```tsx
// donna-ui/src/pages/Preferences/CorrectionsTable.tsx
import { useMemo } from "react";
import { Download } from "lucide-react";
import type { ColumnDef } from "@tanstack/react-table";
import { DataTable, Pill, Button, type PillVariant } from "../../primitives";
import type { CorrectionEntry } from "../../api/preferences";
import { exportToCsv } from "../../utils/csvExport";

interface Props {
  corrections: CorrectionEntry[];
  total: number;
  loading: boolean;
}

const FIELD_VARIANT: Record<string, PillVariant> = {
  priority: "warning",
  domain: "success",
  scheduled_start: "accent",
  deadline: "error",
  title: "muted",
  status: "accent",
};

function formatTs(ts: string): string {
  return ts.replace("T", " ").substring(0, 19);
}

export default function CorrectionsTable({ corrections, total, loading }: Props) {
  const handleExport = () => {
    exportToCsv("corrections", [
      { key: "timestamp", title: "Timestamp" },
      { key: "task_type", title: "Task Type" },
      { key: "field_corrected", title: "Field" },
      { key: "original_value", title: "Original" },
      { key: "corrected_value", title: "Corrected" },
      { key: "input_text", title: "Input" },
    ], corrections as unknown as Record<string, unknown>[]);
  };

  const columns = useMemo<ColumnDef<CorrectionEntry>[]>(
    () => [
      {
        accessorKey: "timestamp",
        header: "Timestamp",
        size: 170,
        cell: ({ getValue }) => formatTs(getValue<string>()),
      },
      {
        accessorKey: "task_type",
        header: "Task Type",
        size: 130,
        cell: ({ getValue }) => <Pill variant="accent">{getValue<string>()}</Pill>,
      },
      {
        accessorKey: "field_corrected",
        header: "Field",
        size: 130,
        cell: ({ getValue }) => {
          const v = getValue<string>();
          return <Pill variant={FIELD_VARIANT[v] ?? "muted"}>{v}</Pill>;
        },
      },
      {
        accessorKey: "original_value",
        header: "Original",
        size: 150,
        cell: ({ getValue }) => (
          <span style={{ textDecoration: "line-through", color: "var(--color-error)", fontSize: "var(--text-small)" }}>
            {getValue<string>()}
          </span>
        ),
      },
      {
        accessorKey: "corrected_value",
        header: "Corrected",
        size: 150,
        cell: ({ getValue }) => (
          <span style={{ color: "var(--color-success)", fontSize: "var(--text-small)" }}>
            {getValue<string>()}
          </span>
        ),
      },
      {
        accessorKey: "input_text",
        header: "Input",
        cell: ({ getValue }) => {
          const v = getValue<string | null>();
          return (
            <span style={{ color: "var(--color-text-muted)", fontSize: "var(--text-small)" }}>
              {v ? (v.length > 80 ? v.substring(0, 80) + "..." : v) : "—"}
            </span>
          );
        },
      },
    ],
    [],
  );

  return (
    <>
      <div style={{ marginBottom: "var(--space-2)", textAlign: "right" }}>
        <Button variant="ghost" size="sm" onClick={handleExport}>
          <Download size={14} />
          Export CSV
        </Button>
      </div>
      <DataTable
        data={corrections}
        columns={columns}
        getRowId={(row) => row.id}
        loading={loading}
        pageSize={50}
        emptyState="No corrections logged yet"
      />
    </>
  );
}
```

- [ ] **Step 2: Typecheck**

Run: `cd donna-ui && npx tsc -b --noEmit`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add donna-ui/src/pages/Preferences/CorrectionsTable.tsx
git commit -m "feat(preferences): migrate CorrectionsTable to DataTable"
```

---

## Task 11: RuleDetailDrawer.tsx → primitive Drawer + `?rule_id=`

**Files:**
- Modify: `donna-ui/src/pages/Preferences/RuleDetailDrawer.tsx`

- [ ] **Step 1: Rewrite RuleDetailDrawer**

Replace the entire file content:

```tsx
// donna-ui/src/pages/Preferences/RuleDetailDrawer.tsx
import { useState, useEffect, useMemo } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { Drawer, Pill, DataTable } from "../../primitives";
import { fetchCorrections, type PreferenceRule, type CorrectionEntry } from "../../api/preferences";

interface Props {
  rule: PreferenceRule | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function formatTs(ts: string): string {
  return ts.replace("T", " ").substring(0, 19);
}

export default function RuleDetailDrawer({ rule, open, onOpenChange }: Props) {
  const [corrections, setCorrections] = useState<CorrectionEntry[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!rule || !open) return;
    if (rule.supporting_corrections.length === 0) {
      setCorrections([]);
      return;
    }

    setLoading(true);
    fetchCorrections({ rule_id: rule.id, limit: 500 })
      .then((resp) => setCorrections(resp.corrections))
      .catch(() => setCorrections([]))
      .finally(() => setLoading(false));
  }, [rule, open]);

  const correctionColumns = useMemo<ColumnDef<CorrectionEntry>[]>(
    () => [
      {
        accessorKey: "timestamp",
        header: "Timestamp",
        size: 170,
        cell: ({ getValue }) => formatTs(getValue<string>()),
      },
      {
        accessorKey: "field_corrected",
        header: "Field",
        size: 120,
        cell: ({ getValue }) => <Pill variant="accent">{getValue<string>()}</Pill>,
      },
      {
        accessorKey: "original_value",
        header: "Original",
        size: 120,
      },
      {
        accessorKey: "corrected_value",
        header: "Corrected",
        size: 120,
      },
      {
        accessorKey: "task_type",
        header: "Task Type",
        size: 120,
      },
    ],
    [],
  );

  if (!rule) return null;

  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      title="Rule Details"
    >
      <dl style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "var(--space-2) var(--space-4)", marginBottom: "var(--space-4)" }}>
        <dt style={{ color: "var(--color-text-muted)", fontSize: "var(--text-small)" }}>Type</dt>
        <dd><Pill variant="accent">{rule.rule_type}</Pill></dd>

        <dt style={{ color: "var(--color-text-muted)", fontSize: "var(--text-small)" }}>Enabled</dt>
        <dd><Pill variant={rule.enabled ? "success" : "error"}>{rule.enabled ? "Yes" : "No"}</Pill></dd>

        <dt style={{ color: "var(--color-text-muted)", fontSize: "var(--text-small)" }}>Confidence</dt>
        <dd><Pill variant={rule.confidence >= 0.7 ? "success" : "error"}>{Math.round(rule.confidence * 100)}%</Pill></dd>

        <dt style={{ color: "var(--color-text-muted)", fontSize: "var(--text-small)" }}>Rule</dt>
        <dd style={{ color: "var(--color-text)" }}>{rule.rule_text}</dd>

        <dt style={{ color: "var(--color-text-muted)", fontSize: "var(--text-small)" }}>Condition</dt>
        <dd>
          <pre style={{ margin: 0, fontSize: "var(--text-small)", fontFamily: "var(--font-mono)", color: "var(--color-text)" }}>
            {rule.condition ? JSON.stringify(rule.condition, null, 2) : "any"}
          </pre>
        </dd>

        <dt style={{ color: "var(--color-text-muted)", fontSize: "var(--text-small)" }}>Action</dt>
        <dd>
          <pre style={{ margin: 0, fontSize: "var(--text-small)", fontFamily: "var(--font-mono)", color: "var(--color-text)" }}>
            {rule.action ? JSON.stringify(rule.action, null, 2) : "—"}
          </pre>
        </dd>

        <dt style={{ color: "var(--color-text-muted)", fontSize: "var(--text-small)" }}>Created</dt>
        <dd style={{ color: "var(--color-text)" }}>{rule.created_at?.substring(0, 10)}</dd>

        <dt style={{ color: "var(--color-text-muted)", fontSize: "var(--text-small)" }}>Disabled</dt>
        <dd style={{ color: "var(--color-text)" }}>{rule.disabled_at?.substring(0, 10) ?? "—"}</dd>
      </dl>

      <h4 style={{
        fontFamily: "var(--font-display)",
        fontWeight: 300,
        fontSize: "var(--text-section)",
        color: "var(--color-text)",
        marginBottom: "var(--space-3)",
      }}>
        Supporting Corrections ({rule.supporting_corrections.length})
      </h4>

      <DataTable
        data={corrections}
        columns={correctionColumns}
        getRowId={(row) => row.id}
        loading={loading}
        pageSize={100}
        emptyState="No supporting corrections found"
      />
    </Drawer>
  );
}
```

- [ ] **Step 2: Typecheck**

Run: `cd donna-ui && npx tsc -b --noEmit`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add donna-ui/src/pages/Preferences/RuleDetailDrawer.tsx
git commit -m "feat(preferences): migrate RuleDetailDrawer to primitive Drawer + rule_id fetch"
```

---

## Task 12: Preferences index.tsx → rebuild as scrollable page

**Files:**
- Create: `donna-ui/src/pages/Preferences/Preferences.module.css`
- Modify: `donna-ui/src/pages/Preferences/index.tsx`

- [ ] **Step 1: Create Preferences.module.css**

```css
/* donna-ui/src/pages/Preferences/Preferences.module.css */
.section {
  margin-bottom: var(--space-5);
}

.sectionHeader {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--space-3);
}

.sectionTitle {
  font-family: var(--font-display);
  font-weight: 300;
  font-size: var(--text-section);
  color: var(--color-text);
}

.sectionCount {
  font-size: var(--text-small);
  color: var(--color-text-muted);
  margin-left: var(--space-2);
  font-weight: 400;
  font-family: var(--font-body);
}

.filters {
  display: flex;
  gap: var(--space-2);
}

.inlineFilters {
  display: flex;
  gap: var(--space-2);
}
```

- [ ] **Step 2: Rebuild Preferences index.tsx**

Replace the entire file content:

```tsx
// donna-ui/src/pages/Preferences/index.tsx
import { useState, useCallback, useEffect } from "react";
import { PageHeader, Select, SelectItem, EmptyState } from "../../primitives";
import RefreshButton from "../../components/RefreshButton";
import RulesTable from "./RulesTable";
import CorrectionsTable from "./CorrectionsTable";
import RuleDetailDrawer from "./RuleDetailDrawer";
import {
  fetchPreferenceRules,
  fetchCorrections,
  fetchPreferenceStats,
  type PreferenceRule,
  type CorrectionEntry,
  type PreferenceStats,
} from "../../api/preferences";
import styles from "./Preferences.module.css";

const RULE_TYPE_OPTIONS = [
  { value: "scheduling", label: "Scheduling" },
  { value: "priority", label: "Priority" },
  { value: "domain", label: "Domain" },
  { value: "formatting", label: "Formatting" },
  { value: "delegation", label: "Delegation" },
];

const ENABLED_OPTIONS = [
  { value: "true", label: "Enabled" },
  { value: "false", label: "Disabled" },
];

export default function PreferencesPage() {
  // Filters
  const [ruleType, setRuleType] = useState("");
  const [enabledFilter, setEnabledFilter] = useState("");
  const [corrField, setCorrField] = useState("");
  const [corrTaskType, setCorrTaskType] = useState("");

  // Data
  const [rules, setRules] = useState<PreferenceRule[]>([]);
  const [rulesLoading, setRulesLoading] = useState(false);
  const [corrections, setCorrections] = useState<CorrectionEntry[]>([]);
  const [corrTotal, setCorrTotal] = useState(0);
  const [corrLoading, setCorrLoading] = useState(false);
  const [stats, setStats] = useState<PreferenceStats | null>(null);

  // Drawer
  const [selectedRule, setSelectedRule] = useState<PreferenceRule | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const doFetch = useCallback(async () => {
    setRulesLoading(true);
    setCorrLoading(true);
    try {
      const enabledVal =
        enabledFilter === "true" ? true : enabledFilter === "false" ? false : undefined;

      const [rulesResp, corrResp, statsResp] = await Promise.all([
        fetchPreferenceRules({
          rule_type: ruleType || undefined,
          enabled: enabledVal,
        }),
        fetchCorrections({
          field: corrField || undefined,
          task_type: corrTaskType || undefined,
        }),
        fetchPreferenceStats(),
      ]);
      setRules(rulesResp.rules);
      setCorrections(corrResp.corrections);
      setCorrTotal(corrResp.total);
      setStats(statsResp);
    } catch {
      setRules([]);
      setCorrections([]);
      setCorrTotal(0);
      setStats(null);
    } finally {
      setRulesLoading(false);
      setCorrLoading(false);
    }
  }, [ruleType, enabledFilter, corrField, corrTaskType]);

  useEffect(() => {
    doFetch();
  }, [doFetch]);

  const handleRuleClick = (rule: PreferenceRule) => {
    setSelectedRule(rule);
    setDrawerOpen(true);
  };

  const hasRules = rules.length > 0 || rulesLoading;

  return (
    <div>
      <PageHeader
        title="Preferences"
        meta="Learned rules & corrections"
        actions={
          <div className={styles.filters}>
            <Select
              value={ruleType}
              onValueChange={setRuleType}
              placeholder="All rule types"
              aria-label="Filter by rule type"
            >
              {RULE_TYPE_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
              ))}
            </Select>
            <Select
              value={enabledFilter}
              onValueChange={setEnabledFilter}
              placeholder="All states"
              aria-label="Filter by enabled state"
            >
              {ENABLED_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
              ))}
            </Select>
            <RefreshButton onRefresh={doFetch} />
          </div>
        }
      />

      {!hasRules ? (
        <EmptyState
          title="No rules learned yet."
          body="Donna picks these up as you correct her."
        />
      ) : (
        <>
          <section className={styles.section}>
            <div className={styles.sectionHeader}>
              <h2 className={styles.sectionTitle}>
                Learned Rules
                <span className={styles.sectionCount}>{rules.length}</span>
              </h2>
            </div>
            <RulesTable
              rules={rules}
              loading={rulesLoading}
              onRuleClick={handleRuleClick}
              onRuleToggled={doFetch}
            />
          </section>

          <section className={styles.section}>
            <div className={styles.sectionHeader}>
              <h2 className={styles.sectionTitle}>
                Corrections
                <span className={styles.sectionCount}>{corrTotal}</span>
              </h2>
              <div className={styles.inlineFilters}>
                <Select
                  value={corrField}
                  onValueChange={(v) => setCorrField(v)}
                  placeholder="All fields"
                  aria-label="Filter corrections by field"
                >
                  {(stats?.top_fields ?? []).map((f) => (
                    <SelectItem key={f.field} value={f.field}>
                      {f.field} ({f.count})
                    </SelectItem>
                  ))}
                </Select>
                <Select
                  value={corrTaskType}
                  onValueChange={(v) => setCorrTaskType(v)}
                  placeholder="All task types"
                  aria-label="Filter corrections by task type"
                >
                  <SelectItem value="parse_task">parse_task</SelectItem>
                  <SelectItem value="classify_priority">classify_priority</SelectItem>
                  <SelectItem value="extract_deadline">extract_deadline</SelectItem>
                </Select>
              </div>
            </div>
            <CorrectionsTable
              corrections={corrections}
              total={corrTotal}
              loading={corrLoading}
            />
          </section>
        </>
      )}

      <RuleDetailDrawer
        rule={selectedRule}
        open={drawerOpen}
        onOpenChange={setDrawerOpen}
      />
    </div>
  );
}
```

- [ ] **Step 3: Typecheck**

Run: `cd donna-ui && npx tsc -b --noEmit`
Expected: PASS.

- [ ] **Step 4: Verify no AntD imports in Preferences directory**

Run: `grep -rn "antd\|@ant-design" donna-ui/src/pages/Preferences/`
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/pages/Preferences/
git commit -m "feat(preferences): rebuild Preferences page as one scrollable page, zero AntD"
```

---

# Phase 3 — Integration

## Task 13: Shadow smoke tests

**Files:**
- Modify: `donna-ui/tests/e2e/smoke/shadow.spec.ts`

- [ ] **Step 1: Write expanded smoke tests**

Replace the entire file content:

```ts
// donna-ui/tests/e2e/smoke/shadow.spec.ts
import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Shadow smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("loads page with header and charts", async ({ page }) => {
    await page.goto("/shadow");
    await expect(page.getByRole("heading", { name: "Shadow" })).toBeVisible();
    // Charts section renders
    await expect(page.getByText("Quality Δ over time")).toBeVisible();
    await expect(page.getByText("Cost savings")).toBeVisible();
  });

  test("comparisons table renders rows", async ({ page }) => {
    await page.goto("/shadow");
    await expect(page.getByText("Comparisons")).toBeVisible();
    // The mock data has one comparison with task_type "parse_task"
    await expect(page.getByText("parse_task").first()).toBeVisible();
  });

  test("comparison row click opens drawer", async ({ page }) => {
    await page.goto("/shadow");
    // Click the first data row in the comparisons table
    const row = page.locator("tr").filter({ hasText: "parse_task" }).first();
    await row.click();
    // Drawer opens with detail view
    await expect(page.getByText("Comparison Detail")).toBeVisible();
    await expect(page.getByText("Primary output")).toBeVisible();
    await expect(page.getByText("Shadow output")).toBeVisible();
  });

  test("spot checks section renders", async ({ page }) => {
    await page.goto("/shadow");
    await expect(page.getByText("Spot Checks")).toBeVisible();
  });

  test("keyboard nav on comparisons table", async ({ page }) => {
    await page.goto("/shadow");
    // Focus the table body
    const tbody = page.locator("section").filter({ hasText: "Comparisons" }).locator("tbody");
    await tbody.focus();
    // Press ArrowDown then Enter to open drawer
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Enter");
    await expect(page.getByText("Comparison Detail")).toBeVisible();
  });
});
```

- [ ] **Step 2: Run the suite**

Run: `cd donna-ui && npx playwright test tests/e2e/smoke/shadow.spec.ts`
Expected: ALL PASS.

- [ ] **Step 3: Commit**

```bash
git add donna-ui/tests/e2e/smoke/shadow.spec.ts
git commit -m "test(shadow): expanded smoke tests for wave 8 migration"
```

---

## Task 14: Preferences smoke tests

**Files:**
- Modify: `donna-ui/tests/e2e/smoke/preferences.spec.ts`

- [ ] **Step 1: Write expanded smoke tests**

Replace the entire file content:

```ts
// donna-ui/tests/e2e/smoke/preferences.spec.ts
import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Preferences smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("loads page with header and sections", async ({ page }) => {
    await page.goto("/preferences");
    await expect(page.getByRole("heading", { name: "Preferences" })).toBeVisible();
    await expect(page.getByText("Learned Rules")).toBeVisible();
    await expect(page.getByText("Corrections")).toBeVisible();
  });

  test("rules table renders rows", async ({ page }) => {
    await page.goto("/preferences");
    // The mock data has one rule with text "Morning deep work blocks before 11am"
    await expect(page.getByText("Morning deep work")).toBeVisible();
  });

  test("rule click opens drawer with corrections", async ({ page }) => {
    await page.goto("/preferences");
    // Click the rule row
    const row = page.locator("tr").filter({ hasText: "Morning deep work" }).first();
    await row.click();
    // Drawer opens
    await expect(page.getByText("Rule Details")).toBeVisible();
    await expect(page.getByText("Supporting Corrections")).toBeVisible();
  });

  test("corrections section renders with filters", async ({ page }) => {
    await page.goto("/preferences");
    // Corrections section header
    await expect(page.getByText("Corrections").first()).toBeVisible();
    // Filter dropdowns are accessible
    await expect(page.getByLabel("Filter corrections by field")).toBeVisible();
  });

  test("empty state renders when zero rules", async ({ page }) => {
    // Override the rules mock to return empty
    await page.route("**/admin/preferences/rules*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ rules: [], total: 0, limit: 50, offset: 0 }),
      }),
    );
    await page.goto("/preferences");
    await expect(page.getByText("No rules learned yet.")).toBeVisible();
    await expect(page.getByText("Donna picks these up as you correct her.")).toBeVisible();
  });
});
```

- [ ] **Step 2: Run the suite**

Run: `cd donna-ui && npx playwright test tests/e2e/smoke/preferences.spec.ts`
Expected: ALL PASS.

- [ ] **Step 3: Commit**

```bash
git add donna-ui/tests/e2e/smoke/preferences.spec.ts
git commit -m "test(preferences): expanded smoke tests for wave 8 migration"
```

---

## Task 15: Wave 8 exit verification

**Files:**
- No file changes; verification only.

**Why:** Every wave ends with a hard gate. If any step fails, the wave is not done.

- [ ] **Step 1: Typecheck + lint**

Run: `cd donna-ui && npx tsc -b --noEmit && npm run lint`
Expected: PASS.

- [ ] **Step 2: Production build**

Run: `cd donna-ui && npm run build`
Expected: PASS, no warnings about missing chunks.

- [ ] **Step 3: Full smoke suite**

Run: `cd donna-ui && npx playwright test`
Expected: ALL PASS (including prior waves: dashboard, tasks, logs, agents, configs, prompts, shadow, preferences).

- [ ] **Step 4: AntD leak grep in migrated areas**

Run: `grep -rn "antd\|@ant-design" donna-ui/src/pages/Shadow/ donna-ui/src/pages/Preferences/`
Expected: no output.

- [ ] **Step 5: Verify no STATUS_COLORS / CHART_COLORS imports from darkTheme in migrated areas**

Run: `grep -rn "darkTheme" donna-ui/src/pages/Shadow/ donna-ui/src/pages/Preferences/`
Expected: no output.

- [ ] **Step 6: Backend test**

Run: `cd /home/feuer/Documents/Projects/donna && python -m pytest tests/test_api_corrections_rule_filter.py -v`
Expected: PASS.

- [ ] **Step 7: Tag the wave**

```bash
git tag wave-8-complete
```

Then push branch + tag for PR review:

```bash
git push -u origin wave-8-shadow-preferences
git push origin wave-8-complete
```

Open the PR with a body referencing this plan and the audit items it closes.

---

## Task 16: Run impeccable:audit and fix issues

**Files:**
- Whatever the audit flags.

**Why:** The spec requires a final `impeccable:audit` pass on both migrated pages to catch accessibility, theming, responsive, and performance issues before the wave ships.

- [ ] **Step 1: Run the impeccable:audit skill**

Invoke: `impeccable:audit` on the Shadow and Preferences pages.

Focus areas:
- Accessibility: contrast ratios, keyboard navigation, screen reader labels
- Responsive: chart grid collapse, drawer width on mobile, table horizontal scroll
- Theming: no inline hex colors, all values from CSS tokens
- Performance: unnecessary re-renders, missing memoization

- [ ] **Step 2: Fix all issues found**

Apply fixes directly. Each fix gets its own commit with a descriptive message.

- [ ] **Step 3: Re-run exit verification (Task 15 Steps 1–5)**

Confirm nothing regressed from the fixes.

- [ ] **Step 4: Amend the wave tag if needed**

If commits were added after the tag:

```bash
git tag -d wave-8-complete
git tag wave-8-complete
git push -u origin wave-8-shadow-preferences --force-with-lease
git push origin wave-8-complete --force
```

---

## Audit Items Resolution Map

| ID | Audit item | Closed by |
|---|---|---|
| P1a | `RuleDetailDrawer` fetches 500 rows for client-side filter | Task 1, Task 2, Task 11 |
| P1b | Shadow table missing keyboard row navigation | Task 5, Task 8 (DataTable `keyboardNav`) |
| P2a | Shadow chart inline hex → tokens | Task 4 (useChartColors via ChartCard/LineChart) |
| P3a | Preferences page lacks empty state for zero rules | Task 12 (EmptyState component) |

---

## Self-Review Summary

- **Spec coverage:** Every requirement from the design refinement spec (`docs/superpowers/specs/2026-04-10-wave-8-shadow-preferences-refinements.md`) has at least one task. Backend `?rule_id=` (Task 1), frontend API (Task 2), Shadow one-page layout (Tasks 4–8), Preferences one-page layout (Tasks 9–12), ComparisonDrawer (Task 6), RuleDetailDrawer perf fix (Task 11), empty state (Task 12), smoke tests (Tasks 13–14), exit verification (Task 15), impeccable audit (Task 16).
- **Placeholder scan:** No `TBD`, no "similar to Task N", no "add validation" — every step has either literal code or a literal command with expected output.
- **Type consistency:** `ShadowComparison` / `ShadowStats` / `SpotCheckItem` types from `api/shadow.ts` used consistently. `PreferenceRule` / `CorrectionEntry` / `CorrectionFilters` from `api/preferences.ts` used consistently. `CorrectionFilters.rule_id` added in Task 2 and consumed in Task 11. `ComparisonDrawer` props (`comparison: ShadowComparison | null`, `open: boolean`, `onOpenChange: (open: boolean) => void`) match the consumer in Task 8. `RuleDetailDrawer` props (`onOpenChange` instead of old `onClose`) — consistent with primitive `Drawer` API. `SpotCheckTable` simplified props (removed `page`/`pageSize`/`onPageChange` — DataTable handles pagination internally).
- **Parallelism:** Phase 1 is serial (foundation). Phase 2a (Shadow, Tasks 4–8) and Phase 2b (Preferences, Tasks 9–12) are fully independent — subagent-driven execution can run them in parallel. Phase 3 is serial and must run after both tracks merge.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-10-donna-ui-wave-8-shadow-preferences.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Phase 2a and Phase 2b can run in parallel subagents once Phase 1 lands.

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints for review.

Which approach?
