# Wave 6 · Agents Migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the Agents page off AntD onto Wave 1 primitives with an asymmetric editorial grid, proper keyboard accessibility, and all inline hex replaced by design tokens.

**Architecture:** The Agents grid page (`index.tsx`) is rewritten to use `<PageHeader>`, `<Card>`, `<Pill>`, `<Stat>`, and `<Skeleton>` primitives with a CSS Grid editorial layout (featured card spans two rows). Agent cards become `<Link>` elements with visible focus rings. `AgentDetail.tsx` replaces AntD Card/Tag/Table/Statistic with primitives and the shared `charts/` library, deleting all inline hex. The existing `fetchAgents` / `fetchAgentDetail` API contract is preserved unchanged.

**Tech Stack:** React 18, CSS Modules (tokens from `src/theme/tokens.css`), `react-router-dom` `<Link>`, `recharts` via `src/charts/AreaChart`, existing primitives from `src/primitives/`, Playwright smoke tests.

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `src/pages/Agents/AgentCard.module.css` | Editorial grid card styles, featured variant, focus ring |
| Create | `src/pages/Agents/AgentDetail.module.css` | Detail page layout: config panel, stat strip, chart sections, invocations table |
| Create | `src/pages/Agents/Agents.module.css` | Page root, editorial CSS Grid (featured + standard cards), skeleton placeholders |
| Modify | `src/pages/Agents/AgentCard.tsx` | Rewrite: drop AntD → primitives, `<Link>` wrapper, `<Stat>`, `<Pill>`, mini area chart slot |
| Modify | `src/pages/Agents/AgentDetail.tsx` | Rewrite: drop AntD → primitives + shared charts lib, delete inline hex |
| Modify | `src/pages/Agents/index.tsx` | Rewrite: `<PageHeader>`, editorial grid, `<Skeleton>` loading, `<EmptyState>` |
| Modify | `src/App.tsx:32` | Add `/agents/:name` route for detail view |
| Modify | `tests/e2e/smoke/agents.spec.ts` | Expand: test grid renders, card focus, navigation to detail, primitives present |
| Modify | `tests/e2e/helpers.ts` | Fix mock to return `{ agents: [] }` for `/admin/agents` list endpoint |

---

## Task Group A — Can be parallelized (independent files)

### Task 1: AgentCard component + styles

**Files:**
- Create: `donna-ui/src/pages/Agents/AgentCard.module.css`
- Modify: `donna-ui/src/pages/Agents/AgentCard.tsx`

- [ ] **Step 1: Create AgentCard.module.css**

```css
/* donna-ui/src/pages/Agents/AgentCard.module.css */

.link {
  display: block;
  text-decoration: none;
  color: inherit;
  border-radius: var(--radius-card);
  outline: none;
}

.link:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 2px;
}

.card {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}

.name {
  font-family: var(--font-display);
  font-weight: 300;
  font-size: var(--text-section);
  letter-spacing: var(--tracking-normal);
  line-height: var(--leading-snug);
  color: var(--color-text);
  margin: 0;
  text-transform: capitalize;
}

.tools {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
  margin-bottom: var(--space-3);
}

.stats {
  display: flex;
  gap: var(--space-4);
  margin-top: auto;
}

.chartSlot {
  margin-top: var(--space-3);
  margin-bottom: var(--space-2);
}

.disabled {
  opacity: 0.6;
}

.statusDot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}

.statusDot.active {
  background: var(--color-success);
}

.statusDot.inactive {
  background: var(--color-error);
}
```

- [ ] **Step 2: Rewrite AgentCard.tsx**

```tsx
// donna-ui/src/pages/Agents/AgentCard.tsx
import { Link } from "react-router-dom";
import type { ReactNode } from "react";
import { Card } from "../../primitives/Card";
import { Pill, type PillVariant } from "../../primitives/Pill";
import { Stat } from "../../primitives/Stat";
import { cn } from "../../lib/cn";
import type { AgentSummary } from "../../api/agents";
import styles from "./AgentCard.module.css";

const AUTONOMY_VARIANT: Record<string, PillVariant> = {
  low: "warning",
  medium: "accent",
  high: "success",
};

interface Props {
  agent: AgentSummary;
  /** Optional mini chart rendered between tools and stats (featured card). */
  chart?: ReactNode;
}

export default function AgentCard({ agent, chart }: Props) {
  return (
    <Link
      to={`/agents/${agent.name}`}
      className={cn(styles.link, !agent.enabled && styles.disabled)}
    >
      <Card className={styles.card}>
        <div className={styles.header}>
          <span
            className={cn(
              styles.statusDot,
              agent.enabled ? styles.active : styles.inactive,
            )}
            aria-label={agent.enabled ? "Active" : "Disabled"}
          />
          <h3 className={styles.name}>{agent.name}</h3>
          <Pill variant={AUTONOMY_VARIANT[agent.autonomy] ?? "muted"}>
            {agent.autonomy}
          </Pill>
        </div>

        <div className={styles.tools}>
          {agent.allowed_tools.map((t) => (
            <Pill key={t} variant="muted">{t}</Pill>
          ))}
        </div>

        {chart && <div className={styles.chartSlot}>{chart}</div>}

        <div className={styles.stats}>
          <Stat eyebrow="Calls" value={agent.total_calls.toLocaleString()} />
          <Stat eyebrow="Avg Latency" value={agent.avg_latency_ms} suffix="ms" />
          <Stat eyebrow="Cost" value={`$${agent.total_cost_usd.toFixed(4)}`} />
        </div>
      </Card>
    </Link>
  );
}
```

- [ ] **Step 3: Verify no AntD imports remain in AgentCard.tsx**

Run: `grep -n "antd\|@ant-design" donna-ui/src/pages/Agents/AgentCard.tsx`
Expected: no output

- [ ] **Step 4: Commit**

```bash
git add donna-ui/src/pages/Agents/AgentCard.tsx donna-ui/src/pages/Agents/AgentCard.module.css
git commit -m "feat(agents): rewrite AgentCard on primitives with Link + focus ring"
```

---

### Task 2: AgentDetail component + styles

**Files:**
- Create: `donna-ui/src/pages/Agents/AgentDetail.module.css`
- Modify: `donna-ui/src/pages/Agents/AgentDetail.tsx`

- [ ] **Step 1: Create AgentDetail.module.css**

```css
/* donna-ui/src/pages/Agents/AgentDetail.module.css */

.root {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.loading {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.error {
  color: var(--color-text-muted);
  font-size: var(--text-body);
}

/* ---- Configuration card ---- */
.configGrid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: var(--space-3);
}

.configItem {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.configLabel {
  font-size: var(--text-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--color-text-muted);
  font-weight: 500;
}

.configValue {
  font-size: var(--text-body);
  color: var(--color-text);
}

.pillList {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
}

/* ---- Stat strip ---- */
.statStrip {
  display: flex;
  gap: var(--space-5);
  flex-wrap: wrap;
}

/* ---- Chart sections ---- */
.chartSection {
  min-height: 200px;
}

.sectionTitle {
  font-family: var(--font-display);
  font-weight: 300;
  font-size: var(--text-section);
  letter-spacing: var(--tracking-normal);
  color: var(--color-text);
  margin: 0 0 var(--space-3);
}
```

- [ ] **Step 2: Rewrite AgentDetail.tsx**

```tsx
// donna-ui/src/pages/Agents/AgentDetail.tsx
import { useState, useEffect, useMemo } from "react";
import dayjs from "dayjs";
import { Card } from "../../primitives/Card";
import { Pill } from "../../primitives/Pill";
import { Stat } from "../../primitives/Stat";
import { Skeleton } from "../../primitives/Skeleton";
import { DataTable } from "../../primitives/DataTable";
import { LineChart } from "../../charts";
import { BarChart } from "../../charts";
import type { ColumnDef } from "@tanstack/react-table";
import {
  fetchAgentDetail,
  type AgentDetail as AgentDetailType,
  type AgentInvocation,
} from "../../api/agents";
import styles from "./AgentDetail.module.css";

interface Props {
  agentName: string;
}

const invocationColumns: ColumnDef<AgentInvocation>[] = [
  {
    accessorKey: "timestamp",
    header: "Time",
    size: 140,
    cell: ({ getValue }) => dayjs(getValue<string>()).format("MMM D HH:mm:ss"),
  },
  { accessorKey: "task_type", header: "Task Type", size: 150 },
  { accessorKey: "model_alias", header: "Model", size: 100 },
  {
    accessorKey: "latency_ms",
    header: "Latency",
    size: 80,
    cell: ({ getValue }) => `${getValue<number>()}ms`,
  },
  {
    accessorKey: "cost_usd",
    header: "Cost",
    size: 80,
    cell: ({ getValue }) => `$${getValue<number>().toFixed(4)}`,
  },
  {
    accessorKey: "is_shadow",
    header: "Shadow",
    size: 70,
    cell: ({ getValue }) =>
      getValue<boolean>() ? <Pill variant="accent">Yes</Pill> : "No",
  },
];

export default function AgentDetail({ agentName }: Props) {
  const [detail, setDetail] = useState<AgentDetailType | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchAgentDetail(agentName)
      .then(setDetail)
      .catch(() => setDetail(null))
      .finally(() => setLoading(false));
  }, [agentName]);

  const formatTick = useMemo(
    () => (d: string) => dayjs(d).format("M/D"),
    [],
  );

  if (loading) {
    return (
      <div className={styles.loading}>
        <Card><Skeleton height={120} /></Card>
        <Card><Skeleton height={80} /></Card>
        <Card><Skeleton height={220} /></Card>
      </div>
    );
  }

  if (!detail) return <p className={styles.error}>Failed to load agent details.</p>;

  return (
    <div className={styles.root}>
      {/* Configuration */}
      <Card>
        <h2 className={styles.sectionTitle}>Configuration</h2>
        <div className={styles.configGrid}>
          <div className={styles.configItem}>
            <span className={styles.configLabel}>Status</span>
            <span className={styles.configValue}>
              <Pill variant={detail.enabled ? "success" : "error"}>
                {detail.enabled ? "Active" : "Disabled"}
              </Pill>
            </span>
          </div>
          <div className={styles.configItem}>
            <span className={styles.configLabel}>Timeout</span>
            <span className={styles.configValue}>{detail.timeout_seconds}s</span>
          </div>
          <div className={styles.configItem}>
            <span className={styles.configLabel}>Autonomy</span>
            <span className={styles.configValue}>
              <Pill variant="muted">{detail.autonomy}</Pill>
            </span>
          </div>
          <div className={styles.configItem}>
            <span className={styles.configLabel}>Allowed Tools</span>
            <div className={styles.pillList}>
              {detail.allowed_tools.map((t) => (
                <Pill key={t} variant="muted">{t}</Pill>
              ))}
            </div>
          </div>
          <div className={styles.configItem}>
            <span className={styles.configLabel}>Task Types</span>
            <div className={styles.pillList}>
              {detail.task_types.map((t) => (
                <Pill key={t} variant="accent">{t}</Pill>
              ))}
            </div>
          </div>
        </div>
      </Card>

      {/* Cost Summary */}
      <Card>
        <h2 className={styles.sectionTitle}>Cost Summary</h2>
        <div className={styles.statStrip}>
          <Stat
            eyebrow="Total Invocations"
            value={detail.cost_summary.total_calls.toLocaleString()}
          />
          <Stat
            eyebrow="Total Cost"
            value={`$${detail.cost_summary.total_cost_usd.toFixed(4)}`}
          />
          <Stat
            eyebrow="Avg Cost / Call"
            value={`$${detail.cost_summary.avg_cost_per_call.toFixed(4)}`}
          />
        </div>
      </Card>

      {/* Latency Trend */}
      {detail.daily_latency.length > 0 && (
        <Card>
          <h2 className={styles.sectionTitle}>Latency Trend (30d)</h2>
          <div className={styles.chartSection}>
            <LineChart
              data={detail.daily_latency}
              series={[{ dataKey: "avg_latency_ms", name: "Avg Latency (ms)" }]}
              xKey="date"
              formatTick={formatTick}
              ariaLabel="Agent latency over 30 days"
            />
          </div>
        </Card>
      )}

      {/* Tool Usage */}
      {detail.tool_usage.length > 0 && (
        <Card>
          <h2 className={styles.sectionTitle}>Tool Usage</h2>
          <div className={styles.chartSection}>
            <BarChart
              data={detail.tool_usage}
              series={[{ dataKey: "count", name: "Calls" }]}
              categoryKey="tool"
              orientation="vertical"
              categoryWidth={120}
              ariaLabel="Tool usage counts"
            />
          </div>
        </Card>
      )}

      {/* Recent Invocations */}
      <Card>
        <h2 className={styles.sectionTitle}>
          Recent Invocations ({detail.recent_invocations.length})
        </h2>
        <DataTable
          data={detail.recent_invocations}
          columns={invocationColumns}
          getRowId={(row) => row.id}
          pageSize={10}
        />
      </Card>
    </div>
  );
}
```

- [ ] **Step 3: Verify no AntD imports or inline hex remain**

Run: `grep -n "antd\|@ant-design\|#[0-9a-f]\{3,6\}" donna-ui/src/pages/Agents/AgentDetail.tsx`
Expected: no output

- [ ] **Step 4: Commit**

```bash
git add donna-ui/src/pages/Agents/AgentDetail.tsx donna-ui/src/pages/Agents/AgentDetail.module.css
git commit -m "feat(agents): rewrite AgentDetail on primitives + shared charts, delete inline hex"
```

---

## Task Group B — Depends on Task Group A

### Task 3: Agents index page (editorial grid + routing)

**Files:**
- Create: `donna-ui/src/pages/Agents/Agents.module.css`
- Modify: `donna-ui/src/pages/Agents/index.tsx`
- Modify: `donna-ui/src/App.tsx:32`

- [ ] **Step 1: Create Agents.module.css**

```css
/* donna-ui/src/pages/Agents/Agents.module.css */

.root {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

/* Asymmetric editorial grid: featured card spans 2 rows. */
.grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  grid-auto-rows: auto;
  gap: var(--space-4);
}

.grid > :first-child {
  grid-row: span 2;
}

@media (max-width: 1024px) {
  .grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .grid > :first-child {
    grid-column: span 2;
    grid-row: span 1;
  }
}

@media (max-width: 640px) {
  .grid {
    grid-template-columns: 1fr;
  }
  .grid > :first-child {
    grid-column: span 1;
  }
}

/* Skeleton loading grid */
.skeletonGrid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  grid-auto-rows: auto;
  gap: var(--space-4);
}

.skeletonGrid > :first-child {
  grid-row: span 2;
}

@media (max-width: 1024px) {
  .skeletonGrid {
    grid-template-columns: repeat(2, 1fr);
  }
  .skeletonGrid > :first-child {
    grid-column: span 2;
    grid-row: span 1;
  }
}

@media (max-width: 640px) {
  .skeletonGrid {
    grid-template-columns: 1fr;
  }
  .skeletonGrid > :first-child {
    grid-column: span 1;
  }
}

.backLink {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--color-text-muted);
  text-decoration: none;
  font-size: var(--text-body);
  transition: color var(--duration-fast) var(--ease-out);
}

.backLink:hover {
  color: var(--color-text);
}

.backLink:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 2px;
  border-radius: var(--radius-control);
}

.detailHeader {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.detailTitle {
  font-family: var(--font-display);
  font-weight: 300;
  font-size: var(--text-section);
  letter-spacing: var(--tracking-normal);
  color: var(--color-text);
  margin: 0;
  text-transform: capitalize;
}
```

- [ ] **Step 2: Rewrite index.tsx**

```tsx
// donna-ui/src/pages/Agents/index.tsx
import { useState, useEffect, useCallback, useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { PageHeader } from "../../primitives/PageHeader";
import { Card } from "../../primitives/Card";
import { Skeleton } from "../../primitives/Skeleton";
import { EmptyState } from "../../primitives/EmptyState";
import RefreshButton from "../../components/RefreshButton";
import { AreaChart } from "../../charts";
import AgentCard from "./AgentCard";
import AgentDetailView from "./AgentDetail";
import {
  fetchAgents,
  fetchAgentDetail,
  type AgentSummary,
  type DailyLatency,
} from "../../api/agents";
import styles from "./Agents.module.css";

export default function AgentsPage() {
  const { name } = useParams<{ name?: string }>();

  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [featuredLatency, setFeaturedLatency] = useState<DailyLatency[]>([]);

  const doFetch = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchAgents();
      setAgents(data);
    } catch {
      setAgents([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    doFetch();
  }, [doFetch]);

  // Fetch mini chart data for the featured (most-recent-run) agent.
  const featured = useMemo(() => {
    if (agents.length === 0) return null;
    return agents.reduce((best, a) => {
      if (!a.last_invocation) return best;
      if (!best || !best.last_invocation) return a;
      return a.last_invocation > best.last_invocation ? a : best;
    }, null as AgentSummary | null);
  }, [agents]);

  useEffect(() => {
    if (!featured) return;
    let cancelled = false;
    fetchAgentDetail(featured.name)
      .then((d) => {
        if (!cancelled) setFeaturedLatency(d.daily_latency);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [featured]);

  const formatTick = useMemo(() => (d: string) => d.slice(5), []);

  // Detail view
  if (name) {
    return (
      <div className={styles.root}>
        <div className={styles.detailHeader}>
          <Link to="/agents" className={styles.backLink}>
            <ArrowLeft size={16} />
            All Agents
          </Link>
          <h1 className={styles.detailTitle}>{name} Agent</h1>
        </div>
        <AgentDetailView agentName={name} />
      </div>
    );
  }

  // Grid view
  return (
    <div className={styles.root}>
      <PageHeader
        eyebrow="System"
        title="Agents"
        meta={
          loading
            ? "Loading…"
            : `${agents.length} agent${agents.length !== 1 ? "s" : ""}`
        }
        actions={<RefreshButton onRefresh={doFetch} />}
      />

      {loading ? (
        <div className={styles.skeletonGrid}>
          {Array.from({ length: 6 }).map((_, i) => (
            <Card key={i}>
              <Skeleton height={i === 0 ? 200 : 140} />
            </Card>
          ))}
        </div>
      ) : agents.length === 0 ? (
        <EmptyState
          title="No agents configured"
          body="Agent definitions live in config/agents.yaml. Add one and it'll show up here."
        />
      ) : (
        <div className={styles.grid}>
          {/* Sort: featured agent first, then alphabetical. */}
          {[...agents]
            .sort((a, b) => {
              if (featured && a.name === featured.name) return -1;
              if (featured && b.name === featured.name) return 1;
              return a.name.localeCompare(b.name);
            })
            .map((agent) => (
              <AgentCard
                key={agent.name}
                agent={agent}
                chart={
                  featured &&
                  agent.name === featured.name &&
                  featuredLatency.length > 0 ? (
                    <AreaChart
                      data={featuredLatency}
                      dataKey="avg_latency_ms"
                      xKey="date"
                      formatTick={formatTick}
                      name="Latency"
                      height={80}
                      ariaLabel={`${agent.name} latency sparkline`}
                    />
                  ) : undefined
                }
              />
            ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Add `/agents/:name` route in App.tsx**

In `donna-ui/src/App.tsx`, after the existing `/agents` route (line 32), add:

```tsx
<Route path="/agents/:name" element={<ErrorBoundary><AgentsPage /></ErrorBoundary>} />
```

- [ ] **Step 4: Verify no AntD imports in index.tsx**

Run: `grep -n "antd\|@ant-design" donna-ui/src/pages/Agents/index.tsx`
Expected: no output

- [ ] **Step 5: Build check**

Run: `cd donna-ui && npx tsc --noEmit`
Expected: no type errors in `src/pages/Agents/`

- [ ] **Step 6: Commit**

```bash
git add donna-ui/src/pages/Agents/index.tsx donna-ui/src/pages/Agents/Agents.module.css donna-ui/src/App.tsx
git commit -m "feat(agents): editorial grid page with PageHeader, Skeleton, EmptyState + detail route"
```

---

### Task 4: Fix test helpers + expand smoke tests

**Files:**
- Modify: `donna-ui/tests/e2e/helpers.ts`
- Modify: `donna-ui/tests/e2e/smoke/agents.spec.ts`

- [ ] **Step 1: Fix mockAdminApi for agents list endpoint**

The `fetchAgents()` API calls `GET /admin/agents` and reads `resp.data.agents`. The current mock returns `[]` for list endpoints, but `axios` wraps it: `resp.data = []`, so `resp.data.agents = undefined`. The page's catch block masks this. Fix the mock to return the correct shape for agents.

In `donna-ui/tests/e2e/helpers.ts`, replace the mock route handler to handle agents specially:

```ts
export async function mockAdminApi(page: Page) {
  await page.route("**/admin/**", (route) => {
    const url = route.request().url();

    // /admin/agents (list) returns { agents: [...] }
    if (url.match(/\/admin\/agents(\?|$)/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          agents: [
            {
              name: "test-agent",
              enabled: true,
              timeout_seconds: 30,
              autonomy: "medium",
              allowed_tools: ["web_search"],
              task_types: ["research"],
              total_calls: 42,
              avg_latency_ms: 350,
              total_cost_usd: 1.23,
              last_invocation: "2026-04-01T12:00:00Z",
            },
          ],
        }),
      });
    }

    // /admin/agents/:name (detail) returns full detail
    if (url.match(/\/admin\/agents\/[^/?]+/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          name: "test-agent",
          enabled: true,
          timeout_seconds: 30,
          autonomy: "medium",
          allowed_tools: ["web_search"],
          task_types: ["research"],
          total_calls: 42,
          avg_latency_ms: 350,
          total_cost_usd: 1.23,
          last_invocation: "2026-04-01T12:00:00Z",
          recent_invocations: [],
          daily_latency: [],
          tool_usage: [],
          cost_summary: { total_calls: 42, total_cost_usd: 1.23, avg_cost_per_call: 0.0293 },
        }),
      });
    }

    // Default: empty array for lists, empty object otherwise
    const body = url.match(
      /\/(logs|tasks|configs|prompts|shadow|preferences|rules|corrections)(\?|$)/,
    )
      ? "[]"
      : "{}";
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body,
    });
  });
}
```

- [ ] **Step 2: Rewrite agents.spec.ts**

```ts
// donna-ui/tests/e2e/smoke/agents.spec.ts
import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Agents smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("renders grid with agent cards", async ({ page }) => {
    await page.goto("/agents");
    // PageHeader renders
    await expect(page.locator("text=Agents")).toBeVisible();
    // At least one agent card is rendered as a link
    const card = page.locator('a[href="/agents/test-agent"]');
    await expect(card).toBeVisible();
  });

  test("agent card has visible focus ring", async ({ page }) => {
    await page.goto("/agents");
    const card = page.locator('a[href="/agents/test-agent"]');
    await card.focus();
    // Focus ring is rendered via :focus-visible — just verify the element gets focus
    await expect(card).toBeFocused();
  });

  test("navigates to agent detail", async ({ page }) => {
    await page.goto("/agents");
    await page.click('a[href="/agents/test-agent"]');
    await expect(page).toHaveURL(/\/agents\/test-agent/);
    // Back link is present
    await expect(page.locator("text=All Agents")).toBeVisible();
    // Configuration section renders
    await expect(page.locator("text=Configuration")).toBeVisible();
  });

  test("detail page shows cost summary stats", async ({ page }) => {
    await page.goto("/agents/test-agent");
    await expect(page.locator("text=Cost Summary")).toBeVisible();
    await expect(page.locator("text=Total Invocations")).toBeVisible();
  });

  test("empty state when no agents", async ({ page }) => {
    // Override the agents mock to return empty
    await page.route("**/admin/agents", (route) => {
      if (route.request().url().match(/\/admin\/agents(\?|$)/)) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ agents: [] }),
        });
      }
      return route.continue();
    });
    await page.goto("/agents");
    await expect(page.locator("text=No agents configured")).toBeVisible();
  });
});
```

- [ ] **Step 3: Run smoke tests**

Run: `cd donna-ui && npx playwright test tests/e2e/smoke/agents.spec.ts`
Expected: all 5 tests pass

- [ ] **Step 4: Commit**

```bash
git add donna-ui/tests/e2e/helpers.ts donna-ui/tests/e2e/smoke/agents.spec.ts
git commit -m "test(agents): expand smoke tests for grid, focus, navigation, detail, empty state"
```

---

## Task Group C — Final verification (depends on all above)

### Task 5: Integration check + AntD import audit

- [ ] **Step 1: Full build check**

Run: `cd donna-ui && npx tsc --noEmit && npm run build`
Expected: clean build, no errors

- [ ] **Step 2: Audit for remaining AntD in Agents directory**

Run: `grep -rn "antd\|@ant-design" donna-ui/src/pages/Agents/`
Expected: no output

- [ ] **Step 3: Audit for remaining inline hex in Agents directory**

Run: `grep -rn '#[0-9a-fA-F]\{3,6\}' donna-ui/src/pages/Agents/`
Expected: no output (all colors use CSS custom properties)

- [ ] **Step 4: Run full smoke suite to check no regressions**

Run: `cd donna-ui && npx playwright test tests/e2e/smoke/`
Expected: all smoke tests pass

- [ ] **Step 5: Verify the `CHART_COLORS` import from `darkTheme.ts` is no longer used in Agents**

Run: `grep -rn "CHART_COLORS\|darkTheme" donna-ui/src/pages/Agents/`
Expected: no output

---

## Parallelization Summary

```
Task Group A (parallel):
  ├── Task 1: AgentCard.tsx + AgentCard.module.css
  └── Task 2: AgentDetail.tsx + AgentDetail.module.css

Task Group B (sequential, depends on A):
  └── Task 3: index.tsx + Agents.module.css + App.tsx route

Task Group C (parallel with B's completion):
  ├── Task 4: e2e helpers + smoke tests
  └── Task 5: Integration audit
```

Tasks 1 and 2 are fully independent and can be dispatched to parallel subagents. Task 3 depends on both (imports AgentCard and AgentDetail). Task 4 can run in parallel with Task 3 since it only touches test files. Task 5 is the final gate.
