# Donna UI Redesign — Wave 4 (Dashboard Migration) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate `donna-ui/src/pages/Dashboard/` off Ant Design onto the Wave 1 primitives, introduce the new `donna-ui/src/charts/` module (theme-aware Recharts wrappers + `ChartCard`), swap `notification.warning` for `sonner` toasts, and add the spec's signature 50 ms staggered fade-in on initial page load. After this plan the Dashboard page and its five card components render entirely on primitives, contain zero inline hex literals, consume a single accent for all chart series, and the `/admin/dashboard/*` API contract is preserved byte-for-byte.

**Architecture:**

- A new `donna-ui/src/charts/` directory holds a thin, theme-aware Recharts layer. `colors.ts` exposes a `useChartColors()` hook that reads CSS custom properties from `:root` via `getComputedStyle` and subscribes to `[data-theme]` attribute mutations via a `MutationObserver` — so when the user flips the theme with `⌘.`, every chart re-reads its palette on the next render without a full reload. `theme.ts` exposes typed helpers (`gridProps`, `axisProps`, `tooltipProps`) that build Recharts sub-component props from the live color object. `AreaChart`, `LineChart`, and `BarChart` are ~60-line wrappers around their Recharts counterparts; each one accepts `data`, `dataKey`, an optional `xKey`, an optional `referenceLine`, and renders with the single accent. The card container `ChartCard` composes `Card` + eyebrow + Fraunces metric + optional delta `Pill` + chart slot + optional stat strip + optional `children` escape-hatch slot; it exposes a `loading` prop that swaps content for `Skeleton` primitives.
- The Dashboard page is rebuilt as a `PageHeader` + CSS-grid body. The AntD `Row/Col` grid is replaced with a CSS Grid (`grid-template-columns: repeat(2, minmax(0, 1fr))` at `lg+`, single column below 900 px). `CostAnalyticsCard` spans both columns for prominence, mirroring the current layout. The time-range selector becomes the primitive `Segmented` (string-valued — the page keeps its numeric `days` state and converts), the health badge becomes a primitive `Pill` inside a primitive `Tooltip`, and the existing anomaly-dedup `useRef` logic is preserved but its `notification.warning` calls become `sonner` `toast.warning` calls.
- Each of the five card components is rewritten to compose `ChartCard` + the new chart wrappers. Four of them (`ParseAccuracy`, `AgentPerformance`, `TaskThroughput`, `QualityWarnings`) fit the base `ChartCard` shape; `CostAnalytics` uses the `children` slot to host its budget progress bar and two horizontal-bar breakdowns. The `TaskThroughput` card drops the rainbow `status_distribution` pie entirely in favour of a stat-strip row of `Pill`-formatted counts — this is the cleanest way to honour the "[P1] rainbow chart colors → single accent" audit item for a card whose chief value was a legend-coded pie.
- All semantic color references (`--color-warning` for the budget reference line, `--color-warning`/`--color-error` for the quality warnings stacked area) flow through `useChartColors()` — no inline hex anywhere in the `pages/Dashboard/` or `charts/` subtree. The imports from `theme/darkTheme.ts` are removed from the Dashboard subtree; `darkTheme.ts` itself is left untouched because Tasks, Agents, Shadow, Preferences, Configs, and Prompts pages still depend on it. Wave 9 deletes it.
- Signature motion: on initial mount the `Dashboard` root sets `data-entered="false"`, then flips to `"true"` inside a `requestAnimationFrame` on the first effect run. A single `@keyframes cardRise` in `Dashboard.module.css` animates each direct-child card from `opacity: 0; transform: translateY(8px)` to rest state with `animation-delay` values `0ms, 50ms, 100ms, 150ms, 200ms` applied via `:nth-child(n)` selectors — so the stagger is pure CSS and fires exactly once per page load. `prefers-reduced-motion: reduce` disables the animation. Data refreshes and range changes do not re-trigger the stagger because the attribute only transitions once.

**Tech Stack:** React 18, TypeScript 5.7, Recharts 2.15, Radix UI primitives (Tooltip via existing `primitives/Tooltip`), `sonner` 2 (already globally mounted in `layout/AppShell.tsx`), CSS Modules. No new runtime dependencies. No new dev dependencies.

**Spec reference:** `docs/superpowers/specs/2026-04-08-donna-ui-redesign-design.md` §2 (lines 162–168, 218–231) for the `charts/` module shape and `ChartCard` API, §3.1 (line 243) for the Dashboard layout direction, and §4 Wave 3 (line 341) for the migration checklist. Note: the master spec calls this work **Wave 3**, but the user is executing it as **Wave 4** because the local wave sequence went `Wave 0/1 primitives → Wave 2 shell → Wave 3 Logs (branched ahead of Dashboard) → Wave 4 Dashboard`. The spec's own Wave 3 text applies unchanged to this plan.

**Precondition:**

- Branch off from `main` — NOT from `wave-3-logs`. Wave 3 Logs is on its own unmerged branch and this plan does not consume it. The primitives the plan relies on (`PageHeader`, `Card`, `Segmented`, `Skeleton`, `Stat`, `Pill`, `Tooltip`, `Button`) all landed on `main` in Wave 0/1 and Wave 2 commits before `5a77a8e Merge pull request #30 from nfeuer/wave-2-shell`.
- `git log main --oneline -1` should show the Wave 2 shell merge commit or something downstream of it.
- `donna-ui/src/charts/` does not exist before Task 1. If it does, something is wrong — stop and reconcile before proceeding.
- `donna-ui/src/theme/tokens.css` contains `--color-accent`, `--color-accent-soft`, `--color-accent-border`, `--color-border-subtle`, `--color-surface`, `--color-text-muted`, `--color-text-dim`, `--color-success`, `--color-warning`, `--color-error`. (Verified in this plan from `tokens.css` lines 8–31.)
- `donna-ui/src/layout/AppShell.tsx` mounts `<Toaster />` globally (verified — line 28). The plan does not mount it again.
- `recharts@^2.15.0` and `sonner@^2.0.7` are already in `donna-ui/package.json`.
- Working directory: `/home/feuer/Documents/Projects/donna`. The `donna-ui` Vite app lives at `donna-ui/`. All build commands run from `donna-ui/`.

---

## Audit issues fixed in this wave

The spec (§4 Wave 3) lists three audit items this wave resolves. Each is addressed below and verified in Task 15.

- **[P1] Dashboard rainbow chart colors → single accent.** Resolved by centralising all chart palette access in `charts/colors.ts` + `useChartColors()` hook. Every series in every card fills with `--color-accent` (soft + line). `TaskThroughput`'s `status_distribution` pie is removed entirely — it was the worst rainbow offender and its information is re-expressed as a stat-strip row. Verified by the `grep` check in Task 15.
- **[P2] AntD `Statistic` hardcoded inline `valueStyle` on metric values.** Every `<Statistic valueStyle={{ fontSize: 22, color: … }} />` usage across the five card files is deleted. Metric numbers now render through either the `ChartCard` headline slot (Fraunces display via `.value` class in `Stat.module.css`) or through the primitive `Stat` component used in stat strips. No inline style objects on metric text remain.
- **[P2] Inline hex color references in dashboard cards.** Every `#[0-9a-fA-F]{3,6}` literal is removed from `donna-ui/src/pages/Dashboard/` and from the new `donna-ui/src/charts/` subtree. Semantic colors flow through CSS custom properties (tokens.css) consumed via `useChartColors()`. Verified by the `grep` check in Task 15.

---

## File Structure Overview

### Created in Wave 4

```
donna-ui/src/
├── charts/
│   ├── colors.ts                          (CREATED — theme-aware palette reader + hook)
│   ├── theme.ts                           (CREATED — Recharts axis/grid/tooltip helpers)
│   ├── AreaChart.tsx                      (CREATED — soft-wash wrapper)
│   ├── LineChart.tsx                      (CREATED — hairline wrapper)
│   ├── BarChart.tsx                       (CREATED — tick-bar wrapper, vertical-layout capable)
│   ├── ChartCard.tsx                      (CREATED — composition container w/ children escape hatch)
│   ├── ChartCard.module.css               (CREATED)
│   └── index.ts                           (CREATED — barrel export)
│
└── pages/
    └── Dashboard/
        └── Dashboard.module.css           (CREATED — grid layout + signature motion keyframes)
```

### Rewritten in Wave 4

```
donna-ui/src/pages/Dashboard/
├── index.tsx                              (REWRITTEN — PageHeader + Segmented + grid + sonner + stagger)
├── CostAnalyticsCard.tsx                  (REWRITTEN — ChartCard w/ children escape hatch)
├── ParseAccuracyCard.tsx                  (REWRITTEN — ChartCard + AreaChart)
├── AgentPerformanceCard.tsx               (REWRITTEN — ChartCard + BarChart)
├── TaskThroughputCard.tsx                 (REWRITTEN — ChartCard + BarChart, pie removed)
└── QualityWarningsCard.tsx                (REWRITTEN — ChartCard + stacked AreaChart)
```

### Expanded in Wave 4

```
donna-ui/tests/e2e/smoke/
└── dashboard.spec.ts                      (EXPANDED — selector-level assertions)
```

### Untouched in Wave 4 (explicit non-goals)

- `donna-ui/src/theme/darkTheme.ts` — still imported by Tasks, Agents, Shadow, Preferences, Configs, Prompts, and the shared `components/RefreshButton.tsx`. Deleted in Wave 9.
- `donna-ui/src/components/RefreshButton.tsx` — the current Dashboard imports it; the rewritten Dashboard keeps importing it. `RefreshButton` itself stays AntD for now because four other pages still use it. Wave 9 ports it to primitives.
- `donna-ui/src/api/dashboard.ts` — frozen. Types and endpoint paths unchanged.
- `donna-ui/src/api/health.ts` — frozen. `fetchAdminHealth()` contract unchanged.
- `donna-ui/package.json` — no dependency changes. `antd` is **not** removed in this wave; that happens in Wave 9.
- `donna-ui/src/App.tsx` — the Dashboard route mounts the rewritten page automatically; no routing change needed.
- `donna-ui/src/pages/DevPrimitives/index.tsx` — no new story entries for the chart wrappers. Adding them is a nice-to-have and deferred to Wave 9 cleanup to keep Wave 4 focused.

### Principles

- Each `.tsx` stays under ~140 lines; colocates its `.module.css`.
- No file in `pages/Dashboard/` or `charts/` imports from `antd`, `@ant-design/icons`, or `theme/darkTheme.ts` after Task 13.
- No inline hex literals (`#RRGGBB`, `#RGB`) anywhere in the `pages/Dashboard/` or `charts/` subtrees. All colors come from tokens via `useChartColors()` or from CSS custom properties in module CSS.
- Every LLM-facing or backend-facing API call site (`fetchCostAnalytics`, etc.) keeps its exact signature. The dashboard consumes the same shapes described in `donna-ui/src/api/dashboard.ts`.

---

## Wave 4 · Dashboard Migration

### Task 1: Branch off `main` and scaffold the `charts/` directory

Creates the new branch, creates the empty `charts/` directory, and stubs the barrel so later tasks have something to import from. No visible code yet — just the plumbing.

**Files:**
- Create: `donna-ui/src/charts/index.ts`

- [ ] **Step 1: Fetch and branch from `main`**

```bash
cd /home/feuer/Documents/Projects/donna
git fetch origin
git checkout main
git pull origin main
git checkout -b wave-4-dashboard
```

Expected: working tree clean, new branch `wave-4-dashboard` created from the current tip of `main`. Run `git log --oneline -1` — it should show the Wave 2 shell merge commit (`5a77a8e Merge pull request #30 from nfeuer/wave-2-shell`) or something newer. If you see `wave-3-logs` commits in `git log`, you branched from the wrong base — `git checkout main && git reset --hard origin/main && git checkout -b wave-4-dashboard` to start over.

- [ ] **Step 2: Verify the charts directory does not exist**

```bash
ls donna-ui/src/charts 2>&1 || echo "absent (expected)"
```

Expected: `absent (expected)`. If the directory already exists, stop and investigate before continuing — something is out of sync.

- [ ] **Step 3: Create the empty barrel**

Create `donna-ui/src/charts/index.ts`:

```ts
// Barrel export for donna-ui/src/charts.
// Populated incrementally across Wave 4 tasks 2–7.
// Consumers import from "../../charts" — never from deep paths.
export {};
```

- [ ] **Step 4: Typecheck**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc -b --noEmit
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
cd /home/feuer/Documents/Projects/donna
git add donna-ui/src/charts/index.ts
git commit -m "$(cat <<'EOF'
Scaffold donna-ui/src/charts module

Creates the empty barrel so subsequent Wave 4 tasks can add the
theme-aware Recharts wrappers incrementally. No runtime effect.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `charts/colors.ts` — theme-aware palette reader and hook

Reads the live CSS custom properties from `:root` via `getComputedStyle` and subscribes to `[data-theme]` attribute mutations via a `MutationObserver` so Recharts re-reads colors when the user flips `⌘.`.

**Files:**
- Create: `donna-ui/src/charts/colors.ts`
- Modify: `donna-ui/src/charts/index.ts`

- [ ] **Step 1: Create `charts/colors.ts`**

```ts
import { useEffect, useState } from "react";

/**
 * Live-read color palette for Recharts.
 *
 * Every field is a resolved CSS value (hex, rgba, or oklch string —
 * whatever the token contains). Consumers pass these strings directly
 * to Recharts props like `stroke`, `fill`, `contentStyle`, `tick.fill`.
 *
 * The palette is theme-aware: when [data-theme="coral"] is toggled
 * on <html>, the MutationObserver in `useChartColors` fires and the
 * hook re-reads, causing subscribers to re-render with the new accent.
 */
export interface ChartColors {
  accent: string;
  accentSoft: string;
  accentBorder: string;
  borderSubtle: string;
  surface: string;
  textMuted: string;
  textDim: string;
  success: string;
  warning: string;
  error: string;
}

const TOKEN_MAP: Record<keyof ChartColors, string> = {
  accent: "--color-accent",
  accentSoft: "--color-accent-soft",
  accentBorder: "--color-accent-border",
  borderSubtle: "--color-border-subtle",
  surface: "--color-surface",
  textMuted: "--color-text-muted",
  textDim: "--color-text-dim",
  success: "--color-success",
  warning: "--color-warning",
  error: "--color-error",
};

// SSR-safe defaults; match tokens.css `:root` defaults exactly. Only hit
// when running outside a browser (unit tests without jsdom, SSR builds).
const DEFAULT_CHART_COLORS: ChartColors = {
  accent: "#d4a943",
  accentSoft: "rgba(212, 169, 67, 0.10)",
  accentBorder: "rgba(212, 169, 67, 0.28)",
  borderSubtle: "#221f1c",
  surface: "#1f1c18",
  textMuted: "#8a8378",
  textDim: "#5e5850",
  success: "#8aa672",
  warning: "#d4a943",
  error: "#c8665e",
};

function readChartColors(): ChartColors {
  if (typeof document === "undefined") return DEFAULT_CHART_COLORS;
  const style = getComputedStyle(document.documentElement);
  const out = {} as ChartColors;
  for (const [key, varName] of Object.entries(TOKEN_MAP) as Array<
    [keyof ChartColors, string]
  >) {
    const value = style.getPropertyValue(varName).trim();
    out[key] = value || DEFAULT_CHART_COLORS[key];
  }
  return out;
}

/**
 * Returns the live chart palette and re-renders the caller whenever
 * the theme changes. Internally subscribes to a MutationObserver on
 * <html>'s `data-theme` attribute — so the hook is decoupled from
 * React state ownership and works regardless of which component
 * happens to own the useTheme hook's state.
 */
export function useChartColors(): ChartColors {
  const [colors, setColors] = useState<ChartColors>(readChartColors);

  useEffect(() => {
    if (typeof document === "undefined") return;
    const root = document.documentElement;
    const observer = new MutationObserver(() => setColors(readChartColors()));
    observer.observe(root, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    return () => observer.disconnect();
  }, []);

  return colors;
}
```

- [ ] **Step 2: Update the barrel**

Replace `donna-ui/src/charts/index.ts` with:

```ts
export { useChartColors, type ChartColors } from "./colors";
```

- [ ] **Step 3: Typecheck**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc -b --noEmit
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
cd /home/feuer/Documents/Projects/donna
git add donna-ui/src/charts/colors.ts donna-ui/src/charts/index.ts
git commit -m "$(cat <<'EOF'
Add theme-aware chart color reader to donna-ui/src/charts

useChartColors() reads CSS custom properties from :root via
getComputedStyle and subscribes to [data-theme] mutations so
Recharts re-renders automatically on the cmd+. theme flip.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `charts/theme.ts` — Recharts sub-component prop helpers

Translates the live `ChartColors` into ready-to-spread prop objects for `<CartesianGrid>`, `<XAxis>`, `<YAxis>`, and `<Tooltip>`. Every card imports these so grid, tick, and tooltip styling is consistent across all charts.

**Files:**
- Create: `donna-ui/src/charts/theme.ts`
- Modify: `donna-ui/src/charts/index.ts`

- [ ] **Step 1: Create `charts/theme.ts`**

```ts
import type { CSSProperties } from "react";
import type { ChartColors } from "./colors";

/**
 * Recharts prop builders. Call these inside a component that has a
 * live `ChartColors` from useChartColors(), then spread the return
 * value onto the corresponding Recharts sub-component. Example:
 *
 *   const colors = useChartColors();
 *   <CartesianGrid {...gridProps(colors)} />
 *
 * Keeping these as pure builders (not JSX) means every chart wrapper
 * can opt out selectively (e.g. LineChart skips grid) without React
 * component overhead.
 */

export function gridProps(colors: ChartColors) {
  return {
    stroke: colors.borderSubtle,
    strokeDasharray: "3 3",
    vertical: false,
  } as const;
}

export function axisTickStyle(colors: ChartColors) {
  return {
    fill: colors.textMuted,
    fontSize: 10,
    fontFamily: "var(--font-mono)",
  } as const;
}

export function axisLineStyle(colors: ChartColors) {
  return {
    stroke: colors.borderSubtle,
  } as const;
}

export function tooltipContentStyle(colors: ChartColors): CSSProperties {
  return {
    background: colors.surface,
    border: `1px solid ${colors.accentBorder}`,
    borderRadius: 2,
    fontSize: 12,
    fontFamily: "var(--font-body)",
    color: "var(--color-text)",
    boxShadow: "0 8px 24px rgba(0, 0, 0, 0.35)",
  };
}

export function tooltipItemStyle(colors: ChartColors): CSSProperties {
  return {
    color: colors.accent,
    fontFamily: "var(--font-mono)",
  };
}

export function tooltipLabelStyle(colors: ChartColors): CSSProperties {
  return {
    color: colors.textMuted,
    fontSize: 11,
    marginBottom: 2,
  };
}
```

- [ ] **Step 2: Update the barrel**

Replace `donna-ui/src/charts/index.ts` with:

```ts
export { useChartColors, type ChartColors } from "./colors";
export {
  gridProps,
  axisTickStyle,
  axisLineStyle,
  tooltipContentStyle,
  tooltipItemStyle,
  tooltipLabelStyle,
} from "./theme";
```

- [ ] **Step 3: Typecheck**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc -b --noEmit
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
cd /home/feuer/Documents/Projects/donna
git add donna-ui/src/charts/theme.ts donna-ui/src/charts/index.ts
git commit -m "$(cat <<'EOF'
Add Recharts theme helpers to donna-ui/src/charts

Pure prop builders for CartesianGrid, XAxis, YAxis, and Tooltip
that consume a live ChartColors object. No inline hex — every
value resolves to a token or a var(--...) reference.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `charts/AreaChart.tsx` — soft-wash area chart wrapper

Thin wrapper around Recharts `AreaChart`. Single accent series with a gradient fill (10% opacity) and a 1.5 px accent line. Optional `referenceLine` for things like the daily budget threshold. One `<linearGradient>` definition per instance keyed off `id` to avoid cross-chart SVG-id collisions.

**Files:**
- Create: `donna-ui/src/charts/AreaChart.tsx`
- Modify: `donna-ui/src/charts/index.ts`

- [ ] **Step 1: Create `charts/AreaChart.tsx`**

```tsx
import { useId } from "react";
import {
  Area,
  AreaChart as RAreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useChartColors } from "./colors";
import {
  axisLineStyle,
  axisTickStyle,
  gridProps,
  tooltipContentStyle,
  tooltipItemStyle,
  tooltipLabelStyle,
} from "./theme";

export interface AreaChartReference {
  /** Y value where the line is drawn. */
  y: number;
  /** Optional label rendered at the right end of the line. */
  label?: string;
  /** Which semantic token to color it with. Defaults to "warning". */
  tone?: "warning" | "error" | "muted";
}

interface AreaChartProps<T extends object> {
  data: T[];
  /** Numeric field on each row that becomes the area series. */
  dataKey: keyof T & string;
  /** Categorical/time field for the X axis. Defaults to "date". */
  xKey?: keyof T & string;
  /** Format the tooltip value (e.g. `(v) => \`$${v.toFixed(2)}\``). */
  formatValue?: (value: number) => string;
  /** Format the X-axis tick (e.g. `(v) => v.slice(5)` for MM-DD). */
  formatTick?: (value: string) => string;
  /** Human label for the tooltip series row. */
  name?: string;
  referenceLine?: AreaChartReference;
  /** Fixed pixel height. Defaults to 160 — matches the spec's dashboard density. */
  height?: number;
  /** ARIA label for the chart region (wraps ResponsiveContainer). */
  ariaLabel?: string;
}

/**
 * Soft-wash area chart — single accent series, 10% gradient fill,
 * 1.5 px line, grid on, Y-axis hidden by default? No — shown, muted.
 *
 * All colors flow through useChartColors() so the chart repaints on
 * the cmd+. theme flip without a reload.
 */
export function AreaChart<T extends object>({
  data,
  dataKey,
  xKey = "date" as keyof T & string,
  formatValue,
  formatTick,
  name,
  referenceLine,
  height = 160,
  ariaLabel,
}: AreaChartProps<T>) {
  const colors = useChartColors();
  const gradientId = useId();
  const refTone = referenceLine?.tone ?? "warning";
  const refColor =
    refTone === "error"
      ? colors.error
      : refTone === "muted"
        ? colors.textDim
        : colors.warning;

  return (
    <div role="img" aria-label={ariaLabel}>
      <ResponsiveContainer width="100%" height={height}>
        <RAreaChart data={data} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={colors.accent} stopOpacity={0.22} />
              <stop offset="100%" stopColor={colors.accent} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid {...gridProps(colors)} />
          <XAxis
            dataKey={xKey}
            tick={axisTickStyle(colors)}
            tickFormatter={formatTick as (v: unknown) => string | undefined}
            tickLine={false}
            axisLine={axisLineStyle(colors)}
          />
          <YAxis
            tick={axisTickStyle(colors)}
            tickFormatter={formatValue as (v: unknown) => string | undefined}
            tickLine={false}
            axisLine={axisLineStyle(colors)}
            width={48}
          />
          <Tooltip
            contentStyle={tooltipContentStyle(colors)}
            itemStyle={tooltipItemStyle(colors)}
            labelStyle={tooltipLabelStyle(colors)}
            formatter={
              formatValue
                ? (value: number) => [formatValue(value), name ?? dataKey]
                : undefined
            }
          />
          {referenceLine && (
            <ReferenceLine
              y={referenceLine.y}
              stroke={refColor}
              strokeDasharray="4 4"
              label={
                referenceLine.label
                  ? {
                      value: referenceLine.label,
                      fill: refColor,
                      fontSize: 10,
                      position: "right",
                    }
                  : undefined
              }
            />
          )}
          <Area
            type="monotone"
            dataKey={dataKey as string}
            stroke={colors.accent}
            strokeWidth={1.5}
            fill={`url(#${gradientId})`}
            name={name ?? (dataKey as string)}
            activeDot={{ r: 3, fill: colors.accent, stroke: colors.surface }}
            isAnimationActive={false}
          />
        </RAreaChart>
      </ResponsiveContainer>
    </div>
  );
}
```

- [ ] **Step 2: Update the barrel**

Replace `donna-ui/src/charts/index.ts` with:

```ts
export { useChartColors, type ChartColors } from "./colors";
export {
  gridProps,
  axisTickStyle,
  axisLineStyle,
  tooltipContentStyle,
  tooltipItemStyle,
  tooltipLabelStyle,
} from "./theme";
export { AreaChart, type AreaChartReference } from "./AreaChart";
```

- [ ] **Step 3: Typecheck**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc -b --noEmit
```

Expected: no errors. If Recharts complains about the generic `T` and `dataKey` prop type, the `as string` casts inside the component handle it — no changes needed.

- [ ] **Step 4: Commit**

```bash
cd /home/feuer/Documents/Projects/donna
git add donna-ui/src/charts/AreaChart.tsx donna-ui/src/charts/index.ts
git commit -m "$(cat <<'EOF'
Add soft-wash AreaChart wrapper to donna-ui/src/charts

Thin Recharts wrapper with single accent, gradient fill, and
optional reference line. Colors pulled via useChartColors so the
chart repaints on theme flip.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `charts/LineChart.tsx` — hairline line chart wrapper

Multi-series line variant for when stacked area is overkill and the user needs to see overlapping series. Used by `QualityWarningsCard` to render `warnings` and `criticals` as two lines (warning tone + error tone) instead of a stacked area — the stacked area pattern is reserved for cases where the metric has one meaningful stream, and we want the [P1] "no rainbow" rule respected as literally as possible: a dashboard with **stacked warning+critical** fill is defensible because the colors are semantically required, whereas a third series would start to drift.

Actually — the Quality card will use an `AreaChart` for the primary series and the `LineChart` wrapper is provided for future use (Shadow card in Wave 8 per the spec). We create the wrapper now because the spec's §2 names it as a first-class chart primitive; skipping it would leave a gap the next wave has to plug. Keep it ~60 lines and move on.

**Files:**
- Create: `donna-ui/src/charts/LineChart.tsx`
- Modify: `donna-ui/src/charts/index.ts`

- [ ] **Step 1: Create `charts/LineChart.tsx`**

```tsx
import {
  CartesianGrid,
  Line,
  LineChart as RLineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useChartColors } from "./colors";
import {
  axisLineStyle,
  axisTickStyle,
  gridProps,
  tooltipContentStyle,
  tooltipItemStyle,
  tooltipLabelStyle,
} from "./theme";

export interface LineSeries {
  dataKey: string;
  name: string;
  /** "accent" (default), "muted", "warning", "error". */
  tone?: "accent" | "muted" | "warning" | "error";
}

interface LineChartProps<T extends object> {
  data: T[];
  series: LineSeries[];
  xKey?: keyof T & string;
  formatTick?: (value: string) => string;
  formatValue?: (value: number) => string;
  height?: number;
  ariaLabel?: string;
}

/**
 * Hairline line chart — 1.5 px strokes, no fill. Each series picks
 * its color from a semantic tone resolved against useChartColors().
 * Used for overlapping time series where stacked area would obscure
 * the signal. Shadow page (Wave 8) is the primary consumer.
 */
export function LineChart<T extends object>({
  data,
  series,
  xKey = "date" as keyof T & string,
  formatTick,
  formatValue,
  height = 160,
  ariaLabel,
}: LineChartProps<T>) {
  const colors = useChartColors();

  const toneColor = (tone: LineSeries["tone"]): string => {
    switch (tone) {
      case "warning":
        return colors.warning;
      case "error":
        return colors.error;
      case "muted":
        return colors.textDim;
      default:
        return colors.accent;
    }
  };

  return (
    <div role="img" aria-label={ariaLabel}>
      <ResponsiveContainer width="100%" height={height}>
        <RLineChart data={data} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid {...gridProps(colors)} />
          <XAxis
            dataKey={xKey}
            tick={axisTickStyle(colors)}
            tickFormatter={formatTick as (v: unknown) => string | undefined}
            tickLine={false}
            axisLine={axisLineStyle(colors)}
          />
          <YAxis
            tick={axisTickStyle(colors)}
            tickFormatter={formatValue as (v: unknown) => string | undefined}
            tickLine={false}
            axisLine={axisLineStyle(colors)}
            width={48}
          />
          <Tooltip
            contentStyle={tooltipContentStyle(colors)}
            itemStyle={tooltipItemStyle(colors)}
            labelStyle={tooltipLabelStyle(colors)}
          />
          {series.map((s) => (
            <Line
              key={s.dataKey}
              type="monotone"
              dataKey={s.dataKey}
              name={s.name}
              stroke={toneColor(s.tone)}
              strokeWidth={1.5}
              dot={false}
              activeDot={{ r: 3, fill: toneColor(s.tone), stroke: colors.surface }}
              isAnimationActive={false}
            />
          ))}
        </RLineChart>
      </ResponsiveContainer>
    </div>
  );
}
```

- [ ] **Step 2: Update the barrel**

Replace `donna-ui/src/charts/index.ts` with:

```ts
export { useChartColors, type ChartColors } from "./colors";
export {
  gridProps,
  axisTickStyle,
  axisLineStyle,
  tooltipContentStyle,
  tooltipItemStyle,
  tooltipLabelStyle,
} from "./theme";
export { AreaChart, type AreaChartReference } from "./AreaChart";
export { LineChart, type LineSeries } from "./LineChart";
```

- [ ] **Step 3: Typecheck**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc -b --noEmit
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
cd /home/feuer/Documents/Projects/donna
git add donna-ui/src/charts/LineChart.tsx donna-ui/src/charts/index.ts
git commit -m "$(cat <<'EOF'
Add hairline LineChart wrapper to donna-ui/src/charts

Multi-series line variant with semantic tone picking
(accent/muted/warning/error). Shadow page (Wave 8) consumes.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: `charts/BarChart.tsx` — tick-bar chart wrapper

Supports both vertical (default, time-series bars) and horizontal (`layout="vertical"` in Recharts terms — bars run left-to-right) layouts. `CostAnalyticsCard`'s two breakdowns (`by_task_type`, `by_model`) use horizontal. `AgentPerformanceCard` and `TaskThroughputCard` use vertical.

**Files:**
- Create: `donna-ui/src/charts/BarChart.tsx`
- Modify: `donna-ui/src/charts/index.ts`

- [ ] **Step 1: Create `charts/BarChart.tsx`**

```tsx
import {
  Bar,
  BarChart as RBarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useChartColors } from "./colors";
import {
  axisLineStyle,
  axisTickStyle,
  gridProps,
  tooltipContentStyle,
  tooltipItemStyle,
  tooltipLabelStyle,
} from "./theme";

export interface BarSeries {
  dataKey: string;
  name: string;
  /** "accent" (default) or "accentSoft" for secondary series. */
  tone?: "accent" | "accentSoft";
}

interface BarChartProps<T extends object> {
  data: T[];
  series: BarSeries[];
  /** Categorical key (x for vertical, y for horizontal). */
  categoryKey: keyof T & string;
  /** "vertical" = horizontal bars running left→right. "horizontal" = time-series columns. */
  orientation?: "horizontal" | "vertical";
  formatCategoryTick?: (value: string) => string;
  formatValue?: (value: number) => string;
  /** Width of the category axis in horizontal orientation. Defaults to 100. */
  categoryWidth?: number;
  /** Tilt the category tick labels by N degrees (horizontal orientation only). */
  tickAngle?: number;
  height?: number;
  ariaLabel?: string;
}

/**
 * Tick-bar chart. Single accent fill by default; secondary series
 * render in the `accentSoft` wash so two series are still readable
 * without invoking a second hue.
 */
export function BarChart<T extends object>({
  data,
  series,
  categoryKey,
  orientation = "horizontal",
  formatCategoryTick,
  formatValue,
  categoryWidth = 100,
  tickAngle = 0,
  height = 160,
  ariaLabel,
}: BarChartProps<T>) {
  const colors = useChartColors();

  const toneFill = (tone: BarSeries["tone"]): string =>
    tone === "accentSoft" ? colors.accentBorder : colors.accent;

  return (
    <div role="img" aria-label={ariaLabel}>
      <ResponsiveContainer width="100%" height={height}>
        <RBarChart
          data={data}
          layout={orientation === "vertical" ? "vertical" : "horizontal"}
          margin={{ top: 4, right: 12, left: 0, bottom: tickAngle ? 40 : 0 }}
        >
          <CartesianGrid {...gridProps(colors)} />
          {orientation === "horizontal" ? (
            <>
              <XAxis
                dataKey={categoryKey as string}
                tick={axisTickStyle(colors)}
                tickFormatter={formatCategoryTick as (v: unknown) => string | undefined}
                tickLine={false}
                axisLine={axisLineStyle(colors)}
                interval={0}
                angle={tickAngle ? -tickAngle : 0}
                textAnchor={tickAngle ? "end" : "middle"}
                height={tickAngle ? 50 : 30}
              />
              <YAxis
                tick={axisTickStyle(colors)}
                tickFormatter={formatValue as (v: unknown) => string | undefined}
                tickLine={false}
                axisLine={axisLineStyle(colors)}
                width={48}
              />
            </>
          ) : (
            <>
              <XAxis
                type="number"
                tick={axisTickStyle(colors)}
                tickFormatter={formatValue as (v: unknown) => string | undefined}
                tickLine={false}
                axisLine={axisLineStyle(colors)}
              />
              <YAxis
                type="category"
                dataKey={categoryKey as string}
                tick={axisTickStyle(colors)}
                tickFormatter={formatCategoryTick as (v: unknown) => string | undefined}
                tickLine={false}
                axisLine={axisLineStyle(colors)}
                width={categoryWidth}
              />
            </>
          )}
          <Tooltip
            contentStyle={tooltipContentStyle(colors)}
            itemStyle={tooltipItemStyle(colors)}
            labelStyle={tooltipLabelStyle(colors)}
            cursor={{ fill: colors.accentSoft }}
          />
          {series.map((s) => (
            <Bar
              key={s.dataKey}
              dataKey={s.dataKey}
              name={s.name}
              fill={toneFill(s.tone)}
              radius={orientation === "vertical" ? [0, 2, 2, 0] : [2, 2, 0, 0]}
              isAnimationActive={false}
            />
          ))}
        </RBarChart>
      </ResponsiveContainer>
    </div>
  );
}
```

- [ ] **Step 2: Update the barrel**

Replace `donna-ui/src/charts/index.ts` with:

```ts
export { useChartColors, type ChartColors } from "./colors";
export {
  gridProps,
  axisTickStyle,
  axisLineStyle,
  tooltipContentStyle,
  tooltipItemStyle,
  tooltipLabelStyle,
} from "./theme";
export { AreaChart, type AreaChartReference } from "./AreaChart";
export { LineChart, type LineSeries } from "./LineChart";
export { BarChart, type BarSeries } from "./BarChart";
```

- [ ] **Step 3: Typecheck**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc -b --noEmit
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
cd /home/feuer/Documents/Projects/donna
git add donna-ui/src/charts/BarChart.tsx donna-ui/src/charts/index.ts
git commit -m "$(cat <<'EOF'
Add BarChart wrapper to donna-ui/src/charts

Supports horizontal (time-series) and vertical (category-row)
layouts. Secondary series render in the accentSoft wash so two
series remain readable without introducing a second hue.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: `charts/ChartCard.tsx` — composition container

The canonical dashboard card shell. Composes a primitive `Card` with eyebrow, Fraunces headline metric, optional delta `Pill`, the chart slot, an optional stat strip, and an optional `children` escape hatch for cards (like `CostAnalytics`) that need more than the standard shape. When `loading={true}` the content is replaced with `Skeleton` placeholders that match the real layout's density.

**Files:**
- Create: `donna-ui/src/charts/ChartCard.tsx`
- Create: `donna-ui/src/charts/ChartCard.module.css`
- Modify: `donna-ui/src/charts/index.ts`

- [ ] **Step 1: Create `charts/ChartCard.module.css`**

```css
.card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: var(--space-3);
}

.headlineLeft {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
  flex: 1;
}

.eyebrow {
  font-size: var(--text-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--color-text-muted);
  font-weight: 500;
}

.metric {
  font-family: var(--font-display);
  font-weight: 300;
  font-size: clamp(32px, 3.6vw, 44px);
  line-height: var(--leading-tight);
  letter-spacing: var(--tracking-tight);
  color: var(--color-text);
  margin: 0;
  font-variant-numeric: tabular-nums;
}

.metricSuffix {
  font-size: 60%;
  color: var(--color-text-dim);
  margin-left: 4px;
}

.delta {
  flex-shrink: 0;
  margin-top: 2px;
}

.chart {
  margin-top: var(--space-2);
}

.stats {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-4) var(--space-5);
  margin-top: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border-subtle);
}

.statItem {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.statLabel {
  font-size: var(--text-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--color-text-muted);
  font-weight: 500;
}

.statValue {
  font-family: var(--font-mono);
  font-size: var(--text-body);
  color: var(--color-text);
  font-variant-numeric: tabular-nums;
}

.extra {
  margin-top: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border-subtle);
}

.skeletonChart {
  height: 160px;
  width: 100%;
}
```

- [ ] **Step 2: Create `charts/ChartCard.tsx`**

```tsx
import type { ReactNode } from "react";
import { Card } from "../primitives/Card";
import { Pill } from "../primitives/Pill";
import { Skeleton } from "../primitives/Skeleton";
import styles from "./ChartCard.module.css";

export interface ChartCardDelta {
  /** Signed percentage, e.g. -12 for "down 12%". */
  value: number;
  /** Human label such as "vs prior period". */
  label: string;
}

export interface ChartCardStat {
  label: string;
  value: ReactNode;
}

interface ChartCardProps {
  eyebrow: string;
  metric: ReactNode;
  /** Optional small suffix rendered inline after the metric (e.g. "ms", "%"). */
  metricSuffix?: string;
  delta?: ChartCardDelta;
  chart?: ReactNode;
  stats?: ChartCardStat[];
  /** Escape hatch rendered below the stat strip, inside the card. */
  children?: ReactNode;
  /** When true, chart + stats + children collapse to Skeletons. */
  loading?: boolean;
  /** Applied to the root Card element. */
  className?: string;
}

/**
 * Canonical dashboard card. Eyebrow + Fraunces headline metric +
 * optional delta pill + chart slot + optional stat strip + optional
 * children escape hatch. When loading, the chart and body areas are
 * replaced by Skeletons that match the real layout's density.
 *
 * All five Wave 4 dashboard cards compose from this.
 */
export function ChartCard({
  eyebrow,
  metric,
  metricSuffix,
  delta,
  chart,
  stats,
  children,
  loading,
  className,
}: ChartCardProps) {
  const deltaVariant = delta
    ? delta.value > 0
      ? "success"
      : delta.value < 0
        ? "error"
        : "muted"
    : "muted";

  const deltaLabel = delta
    ? `${delta.value > 0 ? "+" : ""}${delta.value.toFixed(0)}% ${delta.label}`
    : null;

  return (
    <Card className={className}>
      <div className={styles.card}>
        <header className={styles.header}>
          <div className={styles.headlineLeft}>
            <div className={styles.eyebrow}>{eyebrow}</div>
            <p className={styles.metric}>
              {loading ? <Skeleton width={140} height={44} /> : metric}
              {metricSuffix && !loading && (
                <span className={styles.metricSuffix}>{metricSuffix}</span>
              )}
            </p>
          </div>
          {deltaLabel && !loading && (
            <Pill variant={deltaVariant} className={styles.delta}>
              {deltaLabel}
            </Pill>
          )}
        </header>

        {chart && (
          <div className={styles.chart}>
            {loading ? <Skeleton className={styles.skeletonChart} /> : chart}
          </div>
        )}

        {stats && stats.length > 0 && (
          <dl className={styles.stats}>
            {stats.map((s) => (
              <div key={s.label} className={styles.statItem}>
                <dt className={styles.statLabel}>{s.label}</dt>
                <dd className={styles.statValue}>
                  {loading ? <Skeleton width={60} height={14} /> : s.value}
                </dd>
              </div>
            ))}
          </dl>
        )}

        {children && <div className={styles.extra}>{children}</div>}
      </div>
    </Card>
  );
}
```

- [ ] **Step 3: Update the barrel**

Replace `donna-ui/src/charts/index.ts` with:

```ts
export { useChartColors, type ChartColors } from "./colors";
export {
  gridProps,
  axisTickStyle,
  axisLineStyle,
  tooltipContentStyle,
  tooltipItemStyle,
  tooltipLabelStyle,
} from "./theme";
export { AreaChart, type AreaChartReference } from "./AreaChart";
export { LineChart, type LineSeries } from "./LineChart";
export { BarChart, type BarSeries } from "./BarChart";
export {
  ChartCard,
  type ChartCardDelta,
  type ChartCardStat,
} from "./ChartCard";
```

- [ ] **Step 4: Typecheck**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc -b --noEmit
```

Expected: no errors.

- [ ] **Step 5: Build check**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npm run build
```

Expected: clean build. No consumer imports the new chart module yet, so tree-shaking will exclude it from the bundle — that's fine, the next tasks wire it in.

- [ ] **Step 6: Commit**

```bash
cd /home/feuer/Documents/Projects/donna
git add donna-ui/src/charts/ChartCard.tsx donna-ui/src/charts/ChartCard.module.css donna-ui/src/charts/index.ts
git commit -m "$(cat <<'EOF'
Add ChartCard composition container to donna-ui/src/charts

Eyebrow + Fraunces metric + optional delta pill + chart slot +
optional stat strip + optional children escape hatch. All five
Wave 4 dashboard cards will compose from this in subsequent tasks.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Rewrite `ParseAccuracyCard.tsx` on ChartCard + AreaChart

Simplest non-cost card: one primary metric (accuracy %), a time series (accuracy over time), and a stat strip of supporting counts. The `field_breakdown` AntD Table is collapsed into a prose footnote inside the `children` slot — top 3 corrected fields as `Pill`s — because a secondary table inside every card is visual clutter and the full breakdown lives on the Agents page.

**Files:**
- Rewrite: `donna-ui/src/pages/Dashboard/ParseAccuracyCard.tsx`

- [ ] **Step 1: Replace the file**

Overwrite `donna-ui/src/pages/Dashboard/ParseAccuracyCard.tsx`:

```tsx
import { AreaChart, ChartCard, type ChartCardStat } from "../../charts";
import { Pill } from "../../primitives/Pill";
import type { ParseAccuracyData } from "../../api/dashboard";

interface Props {
  data: ParseAccuracyData | null;
  loading: boolean;
}

function formatPct(v: number): string {
  return `${v.toFixed(1)}%`;
}

export default function ParseAccuracyCard({ data, loading }: Props) {
  const s = data?.summary;

  const stats: ChartCardStat[] = [
    { label: "Parses", value: (s?.total_parses ?? 0).toLocaleString() },
    { label: "Corrections", value: (s?.total_corrections ?? 0).toLocaleString() },
    { label: "Most Corrected", value: s?.most_corrected_field ?? "—" },
  ];

  const topFields = (data?.field_breakdown ?? []).slice(0, 4);

  return (
    <ChartCard
      eyebrow={`Parse Accuracy · ${data?.days ?? 30} days`}
      metric={s ? formatPct(s.accuracy_pct) : "—"}
      chart={
        data?.time_series && data.time_series.length > 0 ? (
          <AreaChart
            data={data.time_series}
            dataKey="accuracy"
            xKey="date"
            name="Accuracy"
            formatValue={(v) => `${v.toFixed(0)}%`}
            formatTick={(v) => v.slice(5)}
            ariaLabel={`Parse accuracy trend over ${data.days} days`}
          />
        ) : undefined
      }
      stats={stats}
      loading={loading && !data}
    >
      {topFields.length > 0 && (
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "var(--space-2)",
            alignItems: "center",
          }}
        >
          <span
            style={{
              fontSize: "var(--text-eyebrow)",
              letterSpacing: "var(--tracking-eyebrow)",
              textTransform: "uppercase",
              color: "var(--color-text-muted)",
              marginRight: "var(--space-1)",
            }}
          >
            Top Corrected
          </span>
          {topFields.map((f) => (
            <Pill key={f.field} variant="muted">
              {f.field} · {f.count}
            </Pill>
          ))}
        </div>
      )}
    </ChartCard>
  );
}
```

- [ ] **Step 2: Typecheck**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc -b --noEmit
```

Expected: no errors. If TypeScript complains about the generic `AreaChart` inferring `T` from `time_series`, verify `ParseAccuracyData["time_series"][number]` includes `accuracy` (it does — see `donna-ui/src/api/dashboard.ts:12`).

- [ ] **Step 3: Verify no AntD imports remain in this file**

```bash
cd /home/feuer/Documents/Projects/donna
grep -n "from \"antd\"\|from 'antd'\|@ant-design\|darkTheme" donna-ui/src/pages/Dashboard/ParseAccuracyCard.tsx
```

Expected: no output.

- [ ] **Step 4: Commit**

```bash
cd /home/feuer/Documents/Projects/donna
git add donna-ui/src/pages/Dashboard/ParseAccuracyCard.tsx
git commit -m "$(cat <<'EOF'
Rewrite ParseAccuracyCard on ChartCard + AreaChart

Drops AntD Card/Statistic/Row/Col/Table/Skeleton and the darkTheme
chart constant imports. Accuracy renders as the Fraunces headline
metric, trend as a single-accent AreaChart, supporting counts as a
stat strip, and the top-corrected fields as a Pill row in the
children escape hatch. Closes audit [P2] inline valueStyles and
[P2] inline hex for this file.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Rewrite `AgentPerformanceCard.tsx` on ChartCard + BarChart

Headline metric is the avg latency (most actionable number). The main chart is `call_count` per `task_type` as a horizontal bar chart (time tilt deleted — horizontal because task-type labels are long and tilted category ticks are an aesthetic offender per the spec's "quiet motion" direction). Stat strip: total calls, p95 latency, total cost. The per-agent AntD Table is dropped because that data already appears on the Agents page — duplicating it on the dashboard is the kind of redundancy the spec's Wave 2 audit item called out.

**Files:**
- Rewrite: `donna-ui/src/pages/Dashboard/AgentPerformanceCard.tsx`

- [ ] **Step 1: Replace the file**

Overwrite `donna-ui/src/pages/Dashboard/AgentPerformanceCard.tsx`:

```tsx
import { BarChart, ChartCard, type ChartCardStat } from "../../charts";
import type { AgentPerformanceData } from "../../api/dashboard";

interface Props {
  data: AgentPerformanceData | null;
  loading: boolean;
}

function formatMs(v: number): string {
  if (v < 1000) return `${Math.round(v)} ms`;
  return `${(v / 1000).toFixed(1)} s`;
}

function formatUsd(v: number): string {
  return `$${v.toFixed(2)}`;
}

export default function AgentPerformanceCard({ data, loading }: Props) {
  const s = data?.summary;

  const stats: ChartCardStat[] = [
    { label: "Total Calls", value: (s?.total_calls ?? 0).toLocaleString() },
    { label: "P95 Latency", value: s ? formatMs(s.p95_latency_ms) : "—" },
    { label: "Total Cost", value: s ? formatUsd(s.total_cost_usd) : "—" },
  ];

  // Top 6 agents by call volume. Recharts gets cranky with >8 bars.
  const agents = (data?.agents ?? [])
    .slice()
    .sort((a, b) => b.call_count - a.call_count)
    .slice(0, 6);

  return (
    <ChartCard
      eyebrow={`Agent Latency · ${data?.days ?? 30} days`}
      metric={s ? formatMs(s.avg_latency_ms) : "—"}
      chart={
        agents.length > 0 ? (
          <BarChart
            data={agents}
            series={[{ dataKey: "call_count", name: "Calls" }]}
            categoryKey="task_type"
            orientation="vertical"
            categoryWidth={120}
            ariaLabel="Agent call volume by task type"
          />
        ) : undefined
      }
      stats={stats}
      loading={loading && !data}
    />
  );
}
```

- [ ] **Step 2: Typecheck**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc -b --noEmit
```

Expected: no errors.

- [ ] **Step 3: Verify no AntD / darkTheme imports**

```bash
cd /home/feuer/Documents/Projects/donna
grep -n "from \"antd\"\|from 'antd'\|@ant-design\|darkTheme" donna-ui/src/pages/Dashboard/AgentPerformanceCard.tsx
```

Expected: no output.

- [ ] **Step 4: Commit**

```bash
cd /home/feuer/Documents/Projects/donna
git add donna-ui/src/pages/Dashboard/AgentPerformanceCard.tsx
git commit -m "$(cat <<'EOF'
Rewrite AgentPerformanceCard on ChartCard + horizontal BarChart

Headline metric is avg latency; chart is top-6 agents by call
volume as a horizontal bar. The per-agent AntD Table is removed —
that data lives on the Agents page. Closes audit [P1] rainbow
colors (single accent), [P2] inline valueStyles, [P2] inline hex
for this file.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Rewrite `TaskThroughputCard.tsx` on ChartCard + BarChart (no pie)

Headline metric is the completion rate. The main chart is `created` + `completed` per day as a two-series bar chart using `accent` + `accentSoft` — two hues of the same accent, not a second color. The `status_distribution` pie is **removed** entirely: it was the worst rainbow offender and its signal is cleanly expressed as a stat strip. Status counts go into the children slot as a `Pill` row.

**Files:**
- Rewrite: `donna-ui/src/pages/Dashboard/TaskThroughputCard.tsx`

- [ ] **Step 1: Replace the file**

Overwrite `donna-ui/src/pages/Dashboard/TaskThroughputCard.tsx`:

```tsx
import { BarChart, ChartCard, type ChartCardStat } from "../../charts";
import { Pill, type PillVariant } from "../../primitives/Pill";
import type { TaskThroughputData } from "../../api/dashboard";

interface Props {
  data: TaskThroughputData | null;
  loading: boolean;
}

function formatPct(v: number): string {
  return `${v.toFixed(0)}%`;
}

/** Status → Pill variant. No rainbow — only meaningful semantics. */
function statusVariant(status: string): PillVariant {
  const normalized = status.toLowerCase();
  if (normalized.includes("done") || normalized.includes("complete")) return "success";
  if (normalized.includes("overdue") || normalized.includes("block")) return "error";
  if (normalized.includes("progress") || normalized.includes("doing")) return "accent";
  return "muted";
}

export default function TaskThroughputCard({ data, loading }: Props) {
  const s = data?.summary;

  const stats: ChartCardStat[] = [
    { label: "Created", value: (s?.total_created ?? 0).toLocaleString() },
    { label: "Completed", value: (s?.total_completed ?? 0).toLocaleString() },
    {
      label: "Overdue",
      value: s && s.overdue_count > 0 ? `${s.overdue_count}` : "0",
    },
    {
      label: "Avg Hours",
      value: s?.avg_completion_hours != null ? s.avg_completion_hours.toFixed(1) : "—",
    },
  ];

  const statusEntries = Object.entries(data?.status_distribution ?? {});

  return (
    <ChartCard
      eyebrow={`Task Throughput · ${data?.days ?? 30} days`}
      metric={s ? formatPct(s.completion_rate) : "—"}
      chart={
        data?.time_series && data.time_series.length > 0 ? (
          <BarChart
            data={data.time_series}
            series={[
              { dataKey: "created", name: "Created" },
              { dataKey: "completed", name: "Completed", tone: "accentSoft" },
            ]}
            categoryKey="date"
            orientation="horizontal"
            formatCategoryTick={(v) => v.slice(5)}
            ariaLabel={`Task creation and completion trend over ${data.days} days`}
          />
        ) : undefined
      }
      stats={stats}
      loading={loading && !data}
    >
      {statusEntries.length > 0 && (
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "var(--space-2)",
            alignItems: "center",
          }}
        >
          <span
            style={{
              fontSize: "var(--text-eyebrow)",
              letterSpacing: "var(--tracking-eyebrow)",
              textTransform: "uppercase",
              color: "var(--color-text-muted)",
              marginRight: "var(--space-1)",
            }}
          >
            Status
          </span>
          {statusEntries.map(([name, count]) => (
            <Pill key={name} variant={statusVariant(name)}>
              {name} · {count}
            </Pill>
          ))}
        </div>
      )}
    </ChartCard>
  );
}
```

- [ ] **Step 2: Typecheck**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc -b --noEmit
```

Expected: no errors.

- [ ] **Step 3: Verify no AntD / darkTheme imports**

```bash
cd /home/feuer/Documents/Projects/donna
grep -n "from \"antd\"\|from 'antd'\|@ant-design\|darkTheme" donna-ui/src/pages/Dashboard/TaskThroughputCard.tsx
```

Expected: no output.

- [ ] **Step 4: Commit**

```bash
cd /home/feuer/Documents/Projects/donna
git add donna-ui/src/pages/Dashboard/TaskThroughputCard.tsx
git commit -m "$(cat <<'EOF'
Rewrite TaskThroughputCard on ChartCard + BarChart

Removes the rainbow status_distribution pie entirely — status
counts now render as a Pill row in the children slot with
semantic variants (success/error/accent/muted) only. The
created/completed trend uses accent + accentSoft so two series
remain readable without a second hue. Closes audit [P1] rainbow
colors, [P2] inline valueStyles, [P2] inline hex for this file.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Rewrite `QualityWarningsCard.tsx` on ChartCard + AreaChart

Headline is the warning-rate percentage. The time-series uses two `AreaChart` series — but since the AreaChart wrapper is single-series, this card renders two stacked `AreaChart` components? No — simpler: render a single `AreaChart` on the `warnings` series and show `criticals` as a stat-strip figure + a small secondary `AreaChart` below only when critical count is nonzero. Keeping it single-series per chart honours the wrapper's thinness principle. The `by_task_type` AntD Table is dropped for the same reason as AgentPerformance (redundant with drill-down pages).

Actually: a cleaner pattern is to use the `LineChart` wrapper we just built — two lines (warnings in warning tone, criticals in error tone). That's the exact use case LineChart is designed for. Switching.

**Files:**
- Rewrite: `donna-ui/src/pages/Dashboard/QualityWarningsCard.tsx`

- [ ] **Step 1: Replace the file**

Overwrite `donna-ui/src/pages/Dashboard/QualityWarningsCard.tsx`:

```tsx
import { ChartCard, LineChart, type ChartCardStat } from "../../charts";
import type { QualityWarningsData } from "../../api/dashboard";

interface Props {
  data: QualityWarningsData | null;
  loading: boolean;
}

function formatPct(v: number): string {
  return `${v.toFixed(1)}%`;
}

export default function QualityWarningsCard({ data, loading }: Props) {
  const s = data?.summary;

  const stats: ChartCardStat[] = [
    { label: "Warnings", value: (s?.total_warnings ?? 0).toLocaleString() },
    { label: "Criticals", value: (s?.total_criticals ?? 0).toLocaleString() },
    { label: "Total Scored", value: (s?.total_scored ?? 0).toLocaleString() },
    {
      label: "Thresholds",
      value: data?.thresholds
        ? `warn < ${data.thresholds.warning_threshold} · crit < ${data.thresholds.critical_threshold}`
        : "—",
    },
  ];

  return (
    <ChartCard
      eyebrow={`Quality · ${data?.days ?? 30} days`}
      metric={s ? formatPct(s.warning_rate_pct) : "—"}
      chart={
        data?.time_series && data.time_series.length > 0 ? (
          <LineChart
            data={data.time_series}
            series={[
              { dataKey: "warnings", name: "Warnings", tone: "warning" },
              { dataKey: "criticals", name: "Criticals", tone: "error" },
            ]}
            xKey="date"
            formatTick={(v) => v.slice(5)}
            ariaLabel={`Quality warnings trend over ${data.days} days`}
          />
        ) : undefined
      }
      stats={stats}
      loading={loading && !data}
    />
  );
}
```

- [ ] **Step 2: Typecheck**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc -b --noEmit
```

Expected: no errors.

- [ ] **Step 3: Verify no AntD / darkTheme imports**

```bash
cd /home/feuer/Documents/Projects/donna
grep -n "from \"antd\"\|from 'antd'\|@ant-design\|darkTheme" donna-ui/src/pages/Dashboard/QualityWarningsCard.tsx
```

Expected: no output.

- [ ] **Step 4: Commit**

```bash
cd /home/feuer/Documents/Projects/donna
git add donna-ui/src/pages/Dashboard/QualityWarningsCard.tsx
git commit -m "$(cat <<'EOF'
Rewrite QualityWarningsCard on ChartCard + LineChart

Warnings + criticals render as two hairline lines (warning tone,
error tone) — semantically required colors only, no decorative
rainbow. Drops the by_task_type table that duplicated Agents page
data. Closes audit [P2] inline valueStyles and [P2] inline hex
for this file.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: Rewrite `CostAnalyticsCard.tsx` using the ChartCard children escape hatch

The most complex card. Headline is today's spend, delta vs. yesterday if inferrable from the time series. The main chart is the daily cost trend with the `$20/day` reference line drawn in the `warning` tone. The stat strip holds Today / MTD / Projected / Remaining. The `children` slot holds the budget progress bar (a plain `<div>` with a width %, tinted by token) + the two horizontal bar charts for `by_task_type` and `by_model` side by side in a sub-grid.

**Files:**
- Rewrite: `donna-ui/src/pages/Dashboard/CostAnalyticsCard.tsx`

- [ ] **Step 1: Replace the file**

Overwrite `donna-ui/src/pages/Dashboard/CostAnalyticsCard.tsx`:

```tsx
import {
  AreaChart,
  BarChart,
  ChartCard,
  type ChartCardStat,
} from "../../charts";
import type { CostAnalyticsData } from "../../api/dashboard";

interface Props {
  data: CostAnalyticsData | null;
  loading: boolean;
}

function formatUsd(v: number, precision = 2): string {
  return `$${v.toFixed(precision)}`;
}

/** Compute day-over-day delta % from the last two time-series points. */
function computeDelta(data: CostAnalyticsData | null): number | null {
  const series = data?.time_series;
  if (!series || series.length < 2) return null;
  const last = series[series.length - 1].cost_usd;
  const prev = series[series.length - 2].cost_usd;
  if (prev === 0) return null;
  return ((last - prev) / prev) * 100;
}

/** Tonal progress bar — width % fills with accent, rest is accent-soft track. */
function BudgetBar({ pct }: { pct: number }) {
  const clamped = Math.min(Math.max(pct, 0), 100);
  return (
    <div
      style={{
        height: 6,
        width: "100%",
        background: "var(--color-accent-soft)",
        borderRadius: 2,
        overflow: "hidden",
      }}
      role="progressbar"
      aria-valuenow={clamped}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label="Monthly budget utilization"
    >
      <div
        style={{
          height: "100%",
          width: `${clamped}%`,
          background: "var(--color-accent)",
          transition: "width var(--duration-base) var(--ease-out)",
        }}
      />
    </div>
  );
}

export default function CostAnalyticsCard({ data, loading }: Props) {
  const s = data?.summary;
  const delta = computeDelta(data);

  const stats: ChartCardStat[] = [
    { label: "Today", value: s ? formatUsd(s.today_cost_usd, 3) : "—" },
    { label: "MTD", value: s ? formatUsd(s.monthly_cost_usd) : "—" },
    { label: "Projected", value: s ? formatUsd(s.projected_monthly_usd) : "—" },
    { label: "Remaining", value: s ? formatUsd(s.monthly_remaining_usd) : "—" },
  ];

  const byTaskType = (data?.by_task_type ?? []).slice(0, 6);
  const byModel = data?.by_model ?? [];

  return (
    <ChartCard
      eyebrow="Budget · Today"
      metric={s ? formatUsd(s.today_cost_usd, 3) : "—"}
      delta={
        delta != null
          ? { value: Math.round(delta), label: "vs yesterday" }
          : undefined
      }
      chart={
        data?.time_series && data.time_series.length > 0 ? (
          <AreaChart
            data={data.time_series}
            dataKey="cost_usd"
            xKey="date"
            name="Daily Cost"
            formatValue={(v) => `$${v.toFixed(2)}`}
            formatTick={(v) => v.slice(5)}
            referenceLine={
              s
                ? {
                    y: s.daily_budget_usd,
                    label: `$${s.daily_budget_usd}/day`,
                    tone: "warning",
                  }
                : undefined
            }
            ariaLabel={`Daily cost trend over ${data?.days ?? 30} days`}
          />
        ) : undefined
      }
      stats={stats}
      loading={loading && !data}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
        {s && (
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                fontSize: "var(--text-label)",
                color: "var(--color-text-muted)",
                fontFamily: "var(--font-mono)",
              }}
            >
              <span>
                Monthly Budget {formatUsd(s.monthly_cost_usd)} / {formatUsd(s.monthly_budget_usd)}
              </span>
              <span>{s.monthly_utilization_pct.toFixed(1)}%</span>
            </div>
            <BudgetBar pct={s.monthly_utilization_pct} />
          </div>
        )}

        {(byTaskType.length > 0 || byModel.length > 0) && (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
              gap: "var(--space-4)",
            }}
          >
            {byTaskType.length > 0 && (
              <div>
                <div
                  style={{
                    fontSize: "var(--text-eyebrow)",
                    letterSpacing: "var(--tracking-eyebrow)",
                    textTransform: "uppercase",
                    color: "var(--color-text-muted)",
                    marginBottom: "var(--space-2)",
                  }}
                >
                  By Task Type
                </div>
                <BarChart
                  data={byTaskType}
                  series={[{ dataKey: "cost_usd", name: "Cost" }]}
                  categoryKey="task_type"
                  orientation="vertical"
                  categoryWidth={110}
                  height={130}
                  formatValue={(v) => `$${v.toFixed(0)}`}
                  ariaLabel="Cost breakdown by task type"
                />
              </div>
            )}
            {byModel.length > 0 && (
              <div>
                <div
                  style={{
                    fontSize: "var(--text-eyebrow)",
                    letterSpacing: "var(--tracking-eyebrow)",
                    textTransform: "uppercase",
                    color: "var(--color-text-muted)",
                    marginBottom: "var(--space-2)",
                  }}
                >
                  By Model
                </div>
                <BarChart
                  data={byModel}
                  series={[{ dataKey: "cost_usd", name: "Cost", tone: "accentSoft" }]}
                  categoryKey="model"
                  orientation="vertical"
                  categoryWidth={90}
                  height={130}
                  formatValue={(v) => `$${v.toFixed(0)}`}
                  ariaLabel="Cost breakdown by model"
                />
              </div>
            )}
          </div>
        )}
      </div>
    </ChartCard>
  );
}
```

- [ ] **Step 2: Typecheck**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc -b --noEmit
```

Expected: no errors. If TypeScript complains about the `by_task_type` slice's inferred element type missing a `cost_usd` field, verify it matches `donna-ui/src/api/dashboard.ts:88-92`.

- [ ] **Step 3: Verify no AntD / darkTheme / hex imports**

```bash
cd /home/feuer/Documents/Projects/donna
grep -n "from \"antd\"\|from 'antd'\|@ant-design\|darkTheme" donna-ui/src/pages/Dashboard/CostAnalyticsCard.tsx
grep -nE "#[0-9a-fA-F]{3,6}" donna-ui/src/pages/Dashboard/CostAnalyticsCard.tsx
```

Expected: no output from either.

- [ ] **Step 4: Commit**

```bash
cd /home/feuer/Documents/Projects/donna
git add donna-ui/src/pages/Dashboard/CostAnalyticsCard.tsx
git commit -m "$(cat <<'EOF'
Rewrite CostAnalyticsCard on ChartCard with children escape hatch

Budget progress bar + by-task-type + by-model breakdowns live in
the ChartCard children slot. Daily cost chart uses the warning
token for the budget reference line, no inline hex. Closes audit
[P1] rainbow, [P2] inline valueStyles, [P2] inline hex for this
file — the last card to be migrated.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: Rewrite `pages/Dashboard/index.tsx` on primitives + sonner + signature motion

The page owner. Builds the `PageHeader` + `Segmented` + `Pill`-based health indicator + CSS-grid card layout, swaps `notification.warning` for `toast.warning` from `sonner`, preserves the existing anomaly-dedup refs, and adds the signature 50 ms staggered fade-in.

**Files:**
- Rewrite: `donna-ui/src/pages/Dashboard/index.tsx`
- Create: `donna-ui/src/pages/Dashboard/Dashboard.module.css`

- [ ] **Step 1: Create `Dashboard.module.css`**

```css
.page {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.controls {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.healthPill {
  /* Pill is already styled; we only need a margin-left hook */
}

.grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-4);
}

.grid > .fullWidth {
  grid-column: 1 / -1;
}

@media (min-width: 960px) {
  .grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

/* ========== Signature motion — staggered card fade-in on first mount ========== */

.grid > * {
  opacity: 1;
  transform: none;
}

.page[data-entered="false"] .grid > * {
  opacity: 0;
  transform: translateY(8px);
}

.page[data-entered="true"] .grid > * {
  animation: cardRise var(--duration-base) var(--ease-out) both;
}

.page[data-entered="true"] .grid > *:nth-child(1) { animation-delay: 0ms; }
.page[data-entered="true"] .grid > *:nth-child(2) { animation-delay: 50ms; }
.page[data-entered="true"] .grid > *:nth-child(3) { animation-delay: 100ms; }
.page[data-entered="true"] .grid > *:nth-child(4) { animation-delay: 150ms; }
.page[data-entered="true"] .grid > *:nth-child(5) { animation-delay: 200ms; }

@keyframes cardRise {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@media (prefers-reduced-motion: reduce) {
  .page[data-entered="false"] .grid > *,
  .page[data-entered="true"] .grid > * {
    opacity: 1;
    transform: none;
    animation: none;
  }
}
```

- [ ] **Step 2: Replace `index.tsx`**

Overwrite `donna-ui/src/pages/Dashboard/index.tsx`:

```tsx
import { useState, useCallback, useEffect, useRef } from "react";
import { toast } from "sonner";
import RefreshButton from "../../components/RefreshButton";
import CostAnalyticsCard from "./CostAnalyticsCard";
import ParseAccuracyCard from "./ParseAccuracyCard";
import AgentPerformanceCard from "./AgentPerformanceCard";
import TaskThroughputCard from "./TaskThroughputCard";
import QualityWarningsCard from "./QualityWarningsCard";
import { PageHeader } from "../../primitives/PageHeader";
import { Segmented } from "../../primitives/Segmented";
import { Pill } from "../../primitives/Pill";
import { Tooltip } from "../../primitives/Tooltip";
import {
  fetchCostAnalytics,
  fetchParseAccuracy,
  fetchQualityWarnings,
  fetchTaskThroughput,
  fetchAgentPerformance,
  type CostAnalyticsData,
  type ParseAccuracyData,
  type TaskThroughputData,
  type AgentPerformanceData,
  type QualityWarningsData,
} from "../../api/dashboard";
import { fetchAdminHealth, type AdminHealthData } from "../../api/health";
import styles from "./Dashboard.module.css";

const RANGE_OPTIONS = [
  { label: "7d", value: "7" },
  { label: "14d", value: "14" },
  { label: "30d", value: "30" },
  { label: "90d", value: "90" },
] as const;

type RangeValue = (typeof RANGE_OPTIONS)[number]["value"];

export interface DashboardData {
  cost: CostAnalyticsData | null;
  parse: ParseAccuracyData | null;
  tasks: TaskThroughputData | null;
  agents: AgentPerformanceData | null;
  quality: QualityWarningsData | null;
}

export default function Dashboard() {
  const [range, setRange] = useState<RangeValue>("30");
  const days = Number(range);
  const [health, setHealth] = useState<AdminHealthData | null>(null);
  const [data, setData] = useState<DashboardData>({
    cost: null,
    parse: null,
    tasks: null,
    agents: null,
    quality: null,
  });
  const [loading, setLoading] = useState(true);
  const [entered, setEntered] = useState(false);

  // Anomaly dedup: only fire a toast on state *transition*, not every poll.
  const prevOverdue = useRef<number | null>(null);
  const prevCostAlert = useRef(false);
  const prevParseAlert = useRef(false);
  const prevQualityAlert = useRef(false);

  const fetchAll = useCallback(async (d: number) => {
    setLoading(true);
    try {
      const [cost, parse, tasks, agents, quality] = await Promise.all([
        fetchCostAnalytics(d).catch(() => null),
        fetchParseAccuracy(d).catch(() => null),
        fetchTaskThroughput(d).catch(() => null),
        fetchAgentPerformance(d).catch(() => null),
        fetchQualityWarnings(d).catch(() => null),
      ]);

      setData({ cost, parse, tasks, agents, quality });

      // Deduplicated anomaly toasts — only on threshold crossing.
      if (cost) {
        const overBudget = cost.summary.today_cost_usd > 16;
        if (overBudget && !prevCostAlert.current) {
          toast.warning("Daily Cost Alert", {
            description: `Today's cost ($${cost.summary.today_cost_usd.toFixed(2)}) exceeds 80% of the $20 daily threshold.`,
            duration: 8000,
          });
        }
        prevCostAlert.current = overBudget;
      }

      if (parse) {
        const lowAccuracy = parse.summary.accuracy_pct < 85;
        if (lowAccuracy && !prevParseAlert.current) {
          toast.warning("Parse Accuracy Alert", {
            description: `Parse accuracy (${parse.summary.accuracy_pct.toFixed(1)}%) dropped below 85%.`,
            duration: 8000,
          });
        }
        prevParseAlert.current = lowAccuracy;
      }

      if (tasks) {
        const currentOverdue = tasks.summary.overdue_count;
        if (
          prevOverdue.current !== null &&
          currentOverdue > prevOverdue.current
        ) {
          toast.warning("Overdue Tasks Increased", {
            description: `Overdue tasks increased from ${prevOverdue.current} to ${currentOverdue}.`,
            duration: 8000,
          });
        }
        prevOverdue.current = currentOverdue;
      }

      if (quality) {
        const highRate = quality.summary.warning_rate_pct > 10;
        if (highRate && !prevQualityAlert.current) {
          toast.warning("Quality Warning Rate High", {
            description: `${quality.summary.warning_rate_pct.toFixed(1)}% of scored invocations are below quality thresholds.`,
            duration: 8000,
          });
        }
        prevQualityAlert.current = highRate;
      }
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshHealth = useCallback(() => {
    fetchAdminHealth().then(setHealth).catch(() => setHealth(null));
  }, []);

  useEffect(() => {
    fetchAll(days);
    refreshHealth();
  }, [days, fetchAll, refreshHealth]);

  // Signature motion: flip data-entered once on first mount inside a
  // requestAnimationFrame so the browser sees the initial false state
  // before the true state arrives, triggering the staggered animation.
  useEffect(() => {
    const raf = requestAnimationFrame(() => setEntered(true));
    return () => cancelAnimationFrame(raf);
  }, []);

  const handleRefresh = useCallback(async () => {
    refreshHealth();
    await fetchAll(days);
  }, [days, fetchAll, refreshHealth]);

  const healthVariant =
    health?.status === "healthy" ? "success" : health ? "warning" : "muted";
  const healthLabel =
    health?.status === "healthy" ? "Healthy" : health ? "Degraded" : "—";
  const healthTooltip = health
    ? Object.entries(health.checks)
        .map(([k, v]) => `${k}: ${v.ok ? "OK" : (v.detail ?? "down")}`)
        .join(" · ")
    : "System status unknown";

  return (
    <div
      className={styles.page}
      data-entered={entered ? "true" : "false"}
      data-testid="dashboard-root"
    >
      <PageHeader
        eyebrow="Overview"
        title="Dashboard"
        actions={
          <div className={styles.controls}>
            <Segmented
              value={range}
              onValueChange={(v) => setRange(v as RangeValue)}
              options={RANGE_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
              aria-label="Date range"
            />
            {health && (
              <Tooltip content={healthTooltip}>
                <span role="status" aria-label={`System status: ${health.status}`}>
                  <Pill variant={healthVariant}>{healthLabel}</Pill>
                </span>
              </Tooltip>
            )}
            <RefreshButton onRefresh={handleRefresh} autoRefreshMs={30000} />
          </div>
        }
      />

      <div className={styles.grid}>
        <div className={styles.fullWidth}>
          <CostAnalyticsCard data={data.cost} loading={loading} />
        </div>
        <ParseAccuracyCard data={data.parse} loading={loading} />
        <TaskThroughputCard data={data.tasks} loading={loading} />
        <AgentPerformanceCard data={data.agents} loading={loading} />
        <QualityWarningsCard data={data.quality} loading={loading} />
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Typecheck**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc -b --noEmit
```

Expected: no errors. `Segmented`'s generic narrows on the `value` prop's string type — the `RangeValue` union keeps it inferred correctly.

- [ ] **Step 4: Build the app**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npm run build
```

Expected: clean build. The bundle should now include the new `charts/` module (it's reachable from the Dashboard route).

- [ ] **Step 5: Manual dev-server spot-check (optional but recommended)**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npm run dev
```

Then open the printed URL, navigate to `/`, and verify:
1. The page renders with a `PageHeader` at the top, a `Segmented` 7d/14d/30d/90d, a `RefreshButton`, and 5 cards — CostAnalytics full width, the other 4 in a 2×2 grid at desktop width.
2. On initial load, the cards fade in staggered (you should be able to see each appearing ~50 ms after the previous).
3. Press `⌘.` (or `Ctrl+.`) — charts repaint to coral without a reload.
4. Reload with the browser DevTools emulating `prefers-reduced-motion: reduce` (rendering → emulate CSS) — no stagger animation, cards appear instantly.
5. If the backend is not running, cards show "—" placeholders but do not crash. If the backend is running, real numbers appear.

Stop the dev server with `Ctrl+C`.

- [ ] **Step 6: Verify no AntD / darkTheme imports in the whole Dashboard subtree**

```bash
cd /home/feuer/Documents/Projects/donna
grep -rn "from \"antd\"\|from 'antd'\|@ant-design\|darkTheme" donna-ui/src/pages/Dashboard/
```

Expected: no output. (Note: `RefreshButton.tsx` lives at `donna-ui/src/components/`, not `pages/Dashboard/`, so it will not appear here even though it still imports AntD.)

- [ ] **Step 7: Commit**

```bash
cd /home/feuer/Documents/Projects/donna
git add donna-ui/src/pages/Dashboard/index.tsx donna-ui/src/pages/Dashboard/Dashboard.module.css
git commit -m "$(cat <<'EOF'
Rewrite Dashboard page on primitives with sonner + signature motion

PageHeader + Segmented + Pill-based health tooltip replace the old
AntD controls bar. Cards live in a responsive CSS Grid with
CostAnalytics spanning both columns. notification.warning calls
become sonner toast.warning while preserving the existing
anomaly-dedup ref logic. Signature motion: 50ms staggered fade-in
on initial mount only, respecting prefers-reduced-motion.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: Expand the Playwright Dashboard smoke test

The existing `dashboard.spec.ts` only asserts the root is not empty and the theme shortcut works. After the rewrite we can make selector-level assertions: PageHeader renders, Segmented renders, five cards render, no AntD classes leak, and the signature motion attribute transitions `false → true`.

**Files:**
- Modify: `donna-ui/tests/e2e/smoke/dashboard.spec.ts`

**Important context for the "no AntD classes" assertion:** `RefreshButton` is still AntD internally and lives inside the rewritten `PageHeader`'s `actions` slot. It migrates in Wave 9. The assertion below intentionally scopes to the grid (card area) only and not the header, so RefreshButton's `.ant-*` classes do not cause a false positive. The comment in the test makes the debt visible.

- [ ] **Step 1: Replace the file**

Overwrite `donna-ui/tests/e2e/smoke/dashboard.spec.ts`:

```ts
import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Dashboard smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("renders PageHeader, Segmented, and five cards", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // PageHeader with the "Dashboard" title.
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();

    // Segmented range selector has all four options.
    const rangeTabs = page.locator('[role="tablist"] [role="tab"]');
    await expect(rangeTabs).toHaveCount(4);
    await expect(rangeTabs.nth(0)).toHaveText("7d");
    await expect(rangeTabs.nth(2)).toHaveText("30d");

    // The "30d" option is selected by default.
    await expect(rangeTabs.nth(2)).toHaveAttribute("aria-selected", "true");

    // The grid has exactly 5 direct children — one fullWidth Cost
    // wrapper plus four cards. The grid is the last <div> child of
    // [data-testid="dashboard-root"] (PageHeader renders a <header>).
    const gridChildren = page.locator(
      '[data-testid="dashboard-root"] > div:last-child > *',
    );
    await expect(gridChildren).toHaveCount(5);
  });

  test("changing range triggers a re-fetch", async ({ page }) => {
    const seen: string[] = [];
    await page.route("**/admin/dashboard/**", (route) => {
      seen.push(route.request().url());
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: "{}",
      });
    });

    await page.goto("/");
    await page.waitForLoadState("networkidle");
    const initialCount = seen.length;
    expect(initialCount).toBeGreaterThan(0);

    // Click the "7d" option.
    await page.locator('[role="tablist"] [role="tab"]').nth(0).click();
    await page.waitForLoadState("networkidle");

    // At least one new request should have been issued with days=7.
    expect(seen.length).toBeGreaterThan(initialCount);
    expect(seen.some((u) => u.includes("days=7"))).toBeTruthy();
  });

  test("data-entered transitions to true after mount", async ({ page }) => {
    await page.goto("/");
    // The attribute flips inside a requestAnimationFrame — wait for it.
    const root = page.locator('[data-testid="dashboard-root"]');
    await expect(root).toHaveAttribute("data-entered", "true", { timeout: 2000 });
  });

  test("no AntD class names inside the card grid", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // Scope check to the grid (card area), not the PageHeader —
    // RefreshButton is still AntD and lives in PageHeader's actions
    // slot. It migrates in Wave 9.
    const antdCount = await page
      .locator('[data-testid="dashboard-root"] > div:last-child [class*="ant-"]')
      .count();
    expect(antdCount).toBe(0);
  });

  test("theme shortcut toggles data-theme attribute", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    await expect(page.locator("html")).not.toHaveAttribute("data-theme", "coral");

    await page.keyboard.press("Meta+.");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "coral");

    await page.keyboard.press("Meta+.");
    await expect(page.locator("html")).not.toHaveAttribute("data-theme", "coral");
  });

  test("theme persists across page reload", async ({ page }) => {
    await page.goto("/");
    await page.keyboard.press("Meta+.");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "coral");

    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "coral");

    // Clean up for next test
    await page.keyboard.press("Meta+.");
  });
});
```

- [ ] **Step 2: Run the Playwright smoke test for Dashboard only**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx playwright test tests/e2e/smoke/dashboard.spec.ts
```

Expected: all six tests pass. If any fail:
- **"five cards" fails with count 4 or 6:** The `fullWidth` wrapper is collapsing or duplicated. Verify `Dashboard.module.css` `.grid > .fullWidth { grid-column: 1 / -1; }` is present and the `index.tsx` wraps Cost in `<div className={styles.fullWidth}>` exactly once.
- **"data-entered transitions" times out:** The `requestAnimationFrame` is not flipping. Check the `useEffect` is in the file and uses `cancelAnimationFrame` cleanup correctly.
- **"changing range triggers a re-fetch"** sees 0 requests: the mock is intercepting before the first render. Inspect `seen` contents — if they match `/admin/dashboard/cost-analytics?days=30` on the initial load, the test is fine. If the list is empty, the page is not fetching at all; check the `useEffect([days, fetchAll, refreshHealth])` dep list in `index.tsx`.
- **"no AntD classes inside the card grid"** finds classes: grep the offending element and identify which card is leaking. Most likely the stat strip on one of the cards is passing `className="ant-*"` through somewhere — fix in the card file, not in the test.

- [ ] **Step 3: Run the full smoke suite to make sure nothing else regressed**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npm run test:e2e
```

Expected: all smoke tests pass — other pages should not be affected because this wave only touched `pages/Dashboard/`, `charts/`, and `tests/e2e/smoke/dashboard.spec.ts`.

- [ ] **Step 4: Commit**

```bash
cd /home/feuer/Documents/Projects/donna
git add donna-ui/tests/e2e/smoke/dashboard.spec.ts
git commit -m "$(cat <<'EOF'
Expand Dashboard smoke test to cover primitives-based page

Asserts PageHeader + Segmented + five cards render, the range
Segmented triggers a re-fetch with the new days value, the
signature-motion data-entered attribute transitions to true on
mount, and the card grid is free of leaked AntD class names
(RefreshButton in PageHeader actions is still AntD; scoped out
until Wave 9).

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: Final verification sweep — grep, build, lint, manual theme flip

The stopping gate. Every check in the "Verification gates" list in the plan preamble runs here. Nothing new is written; this task only verifies and flags drift.

**Files:** none modified.

- [ ] **Step 1: Grep for AntD leakage in the Dashboard subtree**

```bash
cd /home/feuer/Documents/Projects/donna
grep -rn "from \"antd\"\|from 'antd'\|@ant-design" donna-ui/src/pages/Dashboard/
```

Expected: no output.

- [ ] **Step 2: Grep for `darkTheme` imports in the Dashboard subtree**

```bash
cd /home/feuer/Documents/Projects/donna
grep -rn "darkTheme" donna-ui/src/pages/Dashboard/ donna-ui/src/charts/
```

Expected: no output.

- [ ] **Step 3: Grep for inline hex literals in Dashboard + charts subtrees**

```bash
cd /home/feuer/Documents/Projects/donna
grep -rnE "#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b" donna-ui/src/pages/Dashboard/ donna-ui/src/charts/
```

Expected: only the SSR fallback defaults inside `donna-ui/src/charts/colors.ts` (`DEFAULT_CHART_COLORS`). No hits in any `.tsx` file or `.module.css` file in these subtrees. If any card file leaks a hex, fix it before marking this task done.

- [ ] **Step 4: Grep for AntD class leakage by scanning the built CSS**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npm run build
```

Expected: clean `vite build` output with no new chunk-size warnings for the Dashboard route. Note the gzipped bundle size line in the output (informational — not a pass/fail gate).

- [ ] **Step 5: Run lint**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npm run lint
```

Expected: no errors. If lint complains about unused imports in the rewritten card files, clean them up inline and re-commit as a fixup.

- [ ] **Step 6: Run the full Playwright suite**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npm run test:e2e
```

Expected: all smoke tests pass including `dashboard.spec.ts`.

- [ ] **Step 7: Manual theme flip check (informational, not a script gate)**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npm run dev
```

Open the printed URL and:
1. Navigate to `/`.
2. Press `⌘.` (macOS) or `Ctrl+.` (Linux/Windows). Verify every chart's accent strokes, gradient fills, tooltip border, and reference line repaint from champagne gold to electric coral without a full reload.
3. Press `⌘.` again and verify they repaint back to gold.
4. Open DevTools → Rendering → Emulate CSS media feature `prefers-reduced-motion: reduce` → reload. Verify the five cards appear instantly with no stagger animation.
5. Disable the emulation → reload. Verify the stagger returns.

Stop the dev server. Nothing to commit from this step.

- [ ] **Step 8: Compare bundle size against the Wave 0 baseline (informational)**

```bash
cd /home/feuer/Documents/Projects/donna
cat docs/superpowers/specs/bundle-baseline.txt 2>/dev/null || echo "no baseline recorded"
```

If a baseline exists, compare the Step 4 build output to it and note in the commit message whether Wave 4 grew or shrank the gzipped bundle. No action required either way — the 40% reduction gate is Wave 9's responsibility. If the baseline file is missing, that's a Wave 0 debt; flag it but do not block this wave.

- [ ] **Step 9: Push the branch for review**

```bash
cd /home/feuer/Documents/Projects/donna
git push -u origin wave-4-dashboard
```

Expected: branch published. The plan does not open the PR — the user opens it after verifying the subagent-driven execution produced clean commits.

---

## Summary of audit items closed

| Audit ID | Item | Closed by |
|---|---|---|
| [P1] | Dashboard rainbow chart colors → single accent | Tasks 2–12 (useChartColors + single-accent wrappers + pie removal in TaskThroughput) |
| [P2] | AntD `Statistic` hardcoded inline `valueStyle` on metric values | Tasks 8–12 (ChartCard Fraunces metric slot + stat-strip monospace values) |
| [P2] | Inline hex color references in dashboard cards | Tasks 8–12 + Task 15 Step 3 grep gate |

Spec requirements carried forward, unchanged:

- No nested Siders in Dashboard (already absent in the old AntD version — no action needed).
- Every card is keyboard-accessible (the old cards were as well — preserved via `Tooltip`, `role="img"`, `aria-label` on chart wrappers).
- `/admin/dashboard/*` and `/admin/health` API contracts preserved byte-for-byte (verified by Task 14 Step 3 checking the mocked request URLs match the existing endpoints).

---

## Handoff

This plan is ready to execute via `superpowers:subagent-driven-development`. The natural parallelism windows are:

- **Group A — charts/ module build (serial within the group, but independent of any page code):** Tasks 1 → 2 → 3 → 4 → 5 → 6 → 7. Each task's commit depends on the previous task's file being present, so they run in order. A single subagent can execute the whole group.
- **Group B — card rewrites (parallelizable after Group A lands):** Tasks 8, 9, 10, 11, 12 are independent of each other and only depend on the charts module from Group A. Dispatch them to parallel subagents.
- **Group C — page + test:** Task 13 (depends on all of Group B) → Task 14 (depends on Task 13) → Task 15 (depends on everything). Serial.

Total of roughly 7 sequential subagent dispatches if Group B is parallelized maximally, or 15 sequential if run one task at a time.
