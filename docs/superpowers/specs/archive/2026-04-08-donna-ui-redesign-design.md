# Donna UI Redesign — Design Spec

**Date:** 2026-04-08
**Status:** Approved (brainstorming phase)
**Scope:** Complete frontend overhaul of `donna-ui/` — replace Ant Design with a custom primitive library on Radix UI, apply a refined dark luxury aesthetic, and resolve 27 audit issues across 8 pages.

---

## Purpose

The current management UI reads as templated Ant Design dark-mode. Two prior audits surfaced 27 technical issues (P0–P3) across all 8 pages plus aesthetic anti-patterns including rainbow chart palettes, nested responsive-failure Siders, an unsafe regex-based markdown renderer that injects raw HTML, duplicated status color tables, and a bundle size over 2 MB.

This redesign replaces the Ant Design dependency entirely with a custom primitive library on top of Radix UI, TanStack Table, React Hook Form, and Sonner, applies a distinctive "refined dark luxury" aesthetic, and resolves every audit issue in the process. Donna's voice — sharp, confident, efficient, never sycophantic — should be visible in the typography, the spacing rhythm, and the empty states.

---

## 1. Aesthetic Foundation

### Direction

**Refined dark luxury.** Warm charcoal neutrals (tinted toward brown, never blue). Generous asymmetric spacing. A single bold accent that does all the emphasis work. Quiet motion. No nested cards, no glass morphism, no rainbow chart colors, no hardcoded inline hex.

### Color tokens

All colors are exposed as CSS custom properties on `:root`. Theme variants override the accent via `[data-theme="coral"]`.

| Token | Value | Usage |
|---|---|---|
| `--bg` | `#1a1816` | Base page background |
| `--surface` | `#1f1c18` | Cards, drawers, editor background |
| `--inset` | `#16140f` | Rail, inset panels, kbd chrome |
| `--border` | `#2a2724` | Standard borders, dividers |
| `--border-subtle` | `#221f1c` | Row dividers inside tables |
| `--text` | `#e8e3d8` | Primary text |
| `--text-secondary` | `#c7c0b2` | Table rows, body copy |
| `--text-muted` | `#8a8378` | Eyebrows, labels, meta |
| `--text-dim` | `#5e5850` | Deepest de-emphasis |
| `--accent` (gold) | `#d4a943` | Default accent. `oklch(0.78 0.14 85)` |
| `--accent` (coral) | `#f56960` | Alt accent. `oklch(0.68 0.22 25)` |
| `--accent-soft` | `rgba(accent, 0.10)` | Pill backgrounds, active row tint |
| `--accent-border` | `rgba(accent, 0.28)` | Pill borders, hover outlines |
| `--success` | `#8aa672` | Semantic only — warm sage |
| `--warning` | `#d4a943` | Semantic only — coincides with gold accent intentionally |
| `--error` | `#c8665e` | Semantic only — warm brick, never pure red |

**Rule:** Semantic colors only appear when semantically required. No decorative green, red, or blue anywhere.

### Typography

| Token | Font | Usage |
|---|---|---|
| `--font-display` | Fraunces 300, optical size variable | Page titles, metric numbers, card titles |
| `--font-body` | Inter 400 / 500 | All body, controls, rows, labels |
| `--font-mono` | JetBrains Mono (system fallback) | Timestamps, IDs, code, keyboard hints |

**Hosting:** Self-hosted via `@fontsource/fraunces` and `@fontsource/inter`. No external request, no FOUT.

**Scale (fluid):**
- Page title — `clamp(32px, 4vw, 44px)`, letter-spacing `-0.025em`
- Section title — `20px`, letter-spacing `-0.01em`
- Metric number — `clamp(40px, 5vw, 56px)`, letter-spacing `-0.025em`
- Body — `13px` / `line-height 1.5`
- Eyebrow — `9px` uppercase, letter-spacing `0.18em`

### Spacing rhythm

Asymmetric scale: **4 / 8 / 14 / 20 / 28 / 44 / 72 px**. Never use the same padding everywhere. Cards get `28px` padding; rail gets `22px` horizontal; page content gets `40px 48px`.

### Radius

`2px` on all controls; `4px` on top-level cards; nothing higher. No pills, no blobs.

### Motion

All motion uses `cubic-bezier(0.16, 1, 0.3, 1)` (exponential ease-out), 200–300 ms. Only `transform` and `opacity` are animated. `prefers-reduced-motion: reduce` respected globally.

One signature moment: dashboard initial load staggers the 5 cards in with 50 ms delays between each (opacity 0→1, translateY 8→0). Every other page loads instantly.

### Theme toggle

- Default: **Champagne gold**
- Alternate: **Electric coral**
- Persistence: `localStorage` keyed per-browser
- Switched via: Preferences page toggle, or `⌘.` global shortcut (new)
- Mechanism: `useTheme` hook sets `[data-theme]` attribute on `<html>`; all CSS reads `var(--accent)`. No JS recomputes colors.

---

## 2. Component Architecture

### Dependencies

**Added:**
```
@radix-ui/react-dialog, react-dropdown-menu, react-popover, react-select,
react-switch, react-tabs, react-tooltip, react-scroll-area, react-checkbox,
react-slot, react-visually-hidden
@tanstack/react-table
@tanstack/react-virtual
react-hook-form
@hookform/resolvers
zod
sonner
react-day-picker
clsx
lucide-react
react-markdown
rehype-sanitize
rehype-highlight
@fontsource/fraunces
@fontsource/inter
```

**Removed:**
```
antd
@ant-design/icons
```

**Kept:**
```
react, react-dom, react-router-dom v6
vite 6
monaco-editor (framework-agnostic)
recharts (re-themed, not replaced)
```

### Directory layout

```
donna-ui/src/
├── theme/
│   ├── tokens.css          CSS custom properties — colors, type, spacing, motion
│   ├── themes.css          [data-theme="gold"], [data-theme="coral"]
│   ├── reset.css           Minimal reset + base body styles
│   └── index.ts            Typed token exports for JS consumers (chart theme)
│
├── primitives/
│   ├── Button.tsx          primary | ghost | text variants
│   ├── Card.tsx            Card, CardHeader, CardTitle, CardEyebrow
│   ├── Pill.tsx            accent | success | error | warning | muted
│   ├── Input.tsx           Input, Textarea, FormField wrapper
│   ├── Select.tsx          Radix Select
│   ├── Checkbox.tsx        Radix Checkbox
│   ├── Switch.tsx          Radix Switch
│   ├── Tabs.tsx            Radix Tabs
│   ├── Tooltip.tsx         Radix Tooltip, 400ms delay
│   ├── Dialog.tsx          Modal — Radix Dialog
│   ├── Drawer.tsx          Side-sheet — Radix Dialog with side variant
│   ├── DropdownMenu.tsx    Radix DropdownMenu
│   ├── Popover.tsx         Radix Popover
│   ├── Skeleton.tsx        Loading shimmer
│   ├── ScrollArea.tsx      Radix ScrollArea
│   ├── DataTable.tsx       TanStack Table wrapper (sort, paginate, keyboard nav)
│   ├── PageHeader.tsx      Eyebrow + Fraunces title + meta + actions
│   ├── EmptyState.tsx      Distinctive empty states with personality
│   ├── Stat.tsx            Eyebrow + Fraunces metric + sub
│   ├── Segmented.tsx       Replaces AntD Segmented
│   ├── ErrorBoundary.tsx   Existing, restyled
│   └── index.ts            Barrel export
│
├── charts/
│   ├── theme.ts            Recharts theme — axes, grid, tooltip, colors
│   ├── AreaChart.tsx       Soft-wash area chart wrapper
│   ├── LineChart.tsx       Hairline line chart wrapper
│   ├── BarChart.tsx        Tick-bar variant
│   ├── ChartCard.tsx       Eyebrow + metric + delta + chart + stat strip
│   └── colors.ts           Theme-aware Recharts color accessor
│
├── layout/
│   ├── AppShell.tsx        Replaces current Layout.tsx
│   ├── Sidebar.tsx         Rail with brand, nav, theme toggle footer
│   ├── NavItem.tsx         Gold left-border active state
│   └── PageContainer.tsx   Consistent padding + max-width
│
├── hooks/                  Existing hooks retained, extended
├── pages/                  Existing structure retained, internals refactored
└── api/                    Unchanged
```

### Key contracts

**`useTheme`:**
```ts
type Theme = "gold" | "coral";
function useTheme(): { theme: Theme; setTheme: (t: Theme) => void; toggle: () => void };
```
Reads/writes `localStorage` key `donna-theme`. Sets `[data-theme]` on `<html>`. Defaults to `"gold"` if no stored value.

**`<DataTable>`** — the single table replacement used by Tasks, Logs, Shadow, Configs list, Prompts list, Preferences rules:
```tsx
<DataTable
  data={rows}
  columns={columnDefs}
  onRowClick={(row) => setSelected(row)}
  selectedRowId={selected?.id}
  pageSize={50}
  loading={isLoading}
  emptyState={<EmptyState variant="tasks" />}
  virtualized={rows.length > 200}
  keyboardNav
/>
```
Built on TanStack Table. Sticky header. Keyboard navigation (`↑/↓` to move focus, `Enter` to activate `onRowClick`). Virtualized rows via `@tanstack/react-virtual` when `virtualized` is true (Logs especially). Sort indicators are small chevrons in muted text.

Pagination footer shows `Showing 1–50 of 420` in `text-muted` + prev/next ghost buttons. No AntD-style page number row, no jump-to-page, no page size selector.

**`<Card>`:**
```tsx
<Card>
  <CardEyebrow>Tasks Today</CardEyebrow>
  <CardTitle>Spend This Week</CardTitle>
  {children}
</Card>
```
Just a `<div>` with border + padding tokens. No hidden chrome.

**`<ChartCard>`** — subsumes all five dashboard cards:
```tsx
<ChartCard
  eyebrow="Spend · 14 days"
  metric="$47.20"
  delta={{ value: -12, label: "vs prior period" }}
  chart={<AreaChart data={timeSeries} dataKey="cost" />}
  stats={[
    { label: "Avg / Day", value: "$3.37" },
    { label: "Peak", value: "$8.10" },
    { label: "Calls", value: "1,420" },
  ]}
/>
```

### Thinness principle

Every primitive is thin. Radix gives us behavior + a11y; we provide markup, classes, and tokens. No primitive should exceed ~80 lines. If a primitive grows past that, it's either wanting to split or hiding logic that belongs in the page.

---

## 3. Page-Level Layouts

All 8 pages share: `<AppShell>` → `<Sidebar>` + content. Content always starts with `<PageHeader>`.

### 3.1 Dashboard

Same five-card structure as post-fix. Each card becomes a `<ChartCard>` instance. Recharts re-themed via `charts/theme.ts`. Time-range selector becomes the custom `<Segmented>` primitive. Signature motion: 50 ms staggered card fade-in on initial load.

### 3.2 Tasks

Remove inner `<Sider>`. Structure: `<PageHeader>` with "New Task" primary action → sticky **filter bar** (status pills, priority, search input, date range popover) → `<DataTable>` → row click opens task in a `<Drawer>` (Radix Dialog side="right"). Keyboard: `↑/↓` to navigate rows, `Enter` to open. Task detail drawer shows fields as a definition list, status pill, action buttons (`Mark done`, `Reschedule`). All status colors reference central theme tokens — `STATUS_TAG_COLORS` duplicates in `TaskTable.tsx` and `TaskDetail.tsx` are deleted.

### 3.3 Logs

Same shape as Tasks: `<PageHeader>` → filter bar (level pills, source select, search, time range popover) → `<DataTable>` with **virtualized rows** → log row click opens detail `<Drawer>`. Detail drawer renders structured fields as a `<dl>` and the raw JSON payload inside a `<ScrollArea>` containing a `<pre>`. Timestamps always monospace.

### 3.4 Agents

`<PageHeader>` with "Run Agent" primary. Content is an **asymmetric editorial grid**: the most recently run agent is a tall "featured" card spanning two rows with a mini area chart and a stat strip (avg latency, success, cost/run). The remaining agents are compact cards in a 2-column layout. All agent cards are real `<Link>` elements with visible focus rings. `AgentDetail` page migrates to primitives; inline `#333` / `#999` hex codes deleted.

### 3.5 Configs

**Structural split:** `/configs` (list page) + `/configs/:file` (editor page), via React Router subroute.

- **List page:** `<PageHeader>` + `<DataTable>` of config files showing filename, last modified, validation status pill.
- **Editor page:** `<PageHeader>` with breadcrumb back + `Save`/`Discard` actions. Monaco editor fills viewport. **Validation moves to a debounced Web Worker (200 ms)** — no more main-thread `yaml.parse` on every keystroke. Validation errors shown in a sticky bottom status bar.
- **Save diff modal:** `<Dialog>` with `max-width: 960px; width: 90vw` — responsive, no fixed `width={900}`.
- **Form-driven config pages** (`StatesForm`, `ModelsForm`, `PreferencesForm`, `TaskTypesForm`) all migrate to React Hook Form + zod schemas. `STATE_COLORS` deleted — state pills reference the central theme tokens, unifying with `TASK_STATUS_COLORS`.

### 3.6 Prompts

Same subroute split as Configs: `/prompts` + `/prompts/:file`.

- **Editor page:** `<Tabs>` with Edit / Preview / Split. Split tab only available at `lg+` breakpoint; mobile defaults to Edit.
- **Markdown rendering** replaced with **`react-markdown` + `rehype-sanitize` + `rehype-highlight`**. The regex-based HTML-injection pattern in `MarkdownPreview.tsx` is deleted entirely. This closes the XSS concern AND removes the maintenance burden.

### 3.7 Shadow Scoring

`<PageHeader>` + two comparison `<ChartCard>`s (win rate, cost delta) + `<DataTable>` of experiments. Comparison drawer for shadow-vs-production diffs, built on `<Drawer>`.

### 3.8 Preferences

`<PageHeader>` → **Appearance card** (theme toggle with Champagne/Coral buttons, `⌘.` shortcut hint as `<kbd>`) → `<DataTable>` of learned rules. Row click opens `RuleDetailDrawer` as a `<Drawer>`.

**Backend fix:** Add `?rule_id=<id>` query parameter to the corrections API endpoint. `RuleDetailDrawer` fetches only the corrections for the opened rule, not 500 rows for client-side filtering. This is the single backend change in the whole migration.

### Shared behavior

- Global `⌘.` shortcut flips the theme.
- Sonner `<Toaster />` mounted globally, replacing all AntD `message.*` calls. Position: top-right, sonner default, themed via tokens.
- Error boundaries wrap each page, restyled to match.
- All empty states use `<EmptyState>` primitive with instructive-plus-personality voice (see §5, Voice).
- All skeletons use the `<Skeleton>` primitive.

---

## 4. Migration Waves

Nine waves. Each is a self-contained PR. The app stays working after every merge.

### Wave 0 · Foundation

Plumbing only. Zero visible change.

- Install new dependencies (§2).
- Create `src/theme/tokens.css`, `themes.css`, `reset.css`.
- Load Fraunces + Inter via `@fontsource`.
- Implement `useTheme` hook with `localStorage` persistence.
- Implement `⌘.` global shortcut (extension of existing `useKeyboardShortcuts`).
- Configure Vite to tree-shake `lucide-react` imports.
- **Add Playwright smoke tests** — one happy-path test per page navigating the current app. These become the safety net for subsequent waves. New dev dependency: `@playwright/test`.
- Add CI step to run Playwright tests on PR.
- **Record baseline bundle size** to `docs/superpowers/specs/bundle-baseline.txt` — run `npm run build`, save the gzipped JS total. Wave 9 measures the delta against this baseline.

**Audit coverage:** none (pure plumbing).

### Wave 1 · Primitives Library

Build `src/primitives/*` in isolation. No pages consume them yet.

- All primitives listed in §2.
- Each primitive has a story entry on a new `/dev/primitives` route gated to `import.meta.env.DEV`.
- Tests: Playwright visits `/dev/primitives` and snapshots each primitive (visual-regression-lite via Playwright's `toHaveScreenshot`).
- `DataTable` built last — most complex; verify sort / paginate / keyboard nav / virtualized modes.

**Audit coverage:** none (no pages touched yet).

### Wave 2 · App Shell

First visible change.

- New `AppShell` + `Sidebar` + `NavItem` replace `donna-ui/src/components/Layout.tsx`.
- Gold left-border active nav state. Fraunces page title. No more AntD header bar.
- Theme toggle at the bottom of the rail.
- Existing `KeyboardShortcutsModal` migrates to `<Dialog>`.
- Sonner `<Toaster />` mounted globally.

**Audit issues fixed:**
- [P2] Layout sider collapse tooltip inconsistency (`Layout.tsx`)
- [P3] Header bar information redundancy — removed entirely
- [P1] `KeyboardShortcutsModal` migrates to accessible Radix Dialog with focus trap

### Wave 3 · Dashboard Migration

Biggest visible aesthetic shift for the user.

- All 5 dashboard cards become `<ChartCard>` instances.
- Recharts rethemed via `charts/theme.ts`.
- Time-range selector becomes `<Segmented>` primitive.
- Skeletons port from existing fix to new `<Skeleton>` primitive.
- AntD `message.*` notifications replaced with Sonner.
- **Signature motion:** 50 ms staggered card fade-in on initial page load.

**Audit issues fixed:**
- [P1] Dashboard rainbow chart colors → single accent
- [P2] AntD `Statistic` hardcoded inline styles on metric values
- [P2] Inline hex color references in dashboard cards

### Wave 4 · Tasks Migration

- Kill inner `<Sider>`. Build filter bar.
- `TaskTable` → `<DataTable>` with sort, pagination, keyboard nav.
- Task detail drawer → `<Drawer>`.
- `STATUS_TAG_COLORS` duplication in `TaskTable.tsx` and `TaskDetail.tsx` deleted — central theme tokens used instead.
- New Task flow → `<Dialog>` with React Hook Form + zod.

**Audit issues fixed:**
- [P1] Duplicated `STATUS_TAG_COLORS` (TaskTable + TaskDetail)
- [P1] Task drawer a11y — no focus trap, no ESC (free with Radix)
- [P2] Task filter form lacks reset button
- [P2] AntD message toasts → Sonner

### Wave 5 · Logs Migration

- Same pattern as Tasks: `<PageHeader>` + filter bar + `<DataTable>` + `<Drawer>`.
- Virtualized rows via `@tanstack/react-virtual`.
- Log detail drawer shows structured fields as a `<dl>` + raw JSON in `<pre>` inside `<ScrollArea>`.
- Level filter as pill group.

**Audit issues fixed:**
- [P0] Logs page responsive failure — nested `<Sider width={210}>` never collapsed on mobile
- [P1] Logs filter form lacks ARIA labels
- [P2] Timestamp column format inconsistency
- [P2] Level tag colors scattered inline

### Wave 6 · Agents Migration

- Asymmetric editorial grid: featured card (most-recent-run) spans two rows with a mini area chart.
- All agent cards become `<Link>` elements with visible focus rings.
- `AgentDetail.tsx` migrates to primitives; inline `#333` / `#999` hex deleted.

**Audit issues fixed:**
- [P1] `AgentCard` clickable div with no keyboard role or focus ring
- [P1] `AgentDetail.tsx:119,124` inline hex instead of tokens
- [P2] Agents same-size card grid → editorial grid (anti-pattern call-out from audit)

### Wave 7 · Configs + Prompts Migration

Both share the "list → editor subroute" pattern. Done together.

**Configs:**
- Subroute split: `/configs` (list) + `/configs/:file` (editor).
- Monaco kept. YAML validation moves to a debounced Web Worker (200 ms).
- Save diff modal → `<Dialog>` with responsive width.
- `StatesForm`, `ModelsForm`, `PreferencesForm`, `TaskTypesForm` migrate to React Hook Form + zod.
- `STATE_COLORS` deleted — central theme used.

**Prompts:**
- Subroute split: `/prompts` (list) + `/prompts/:file` (editor).
- `<Tabs>` for Edit / Preview / Split.
- **Markdown rendering replaced** with `react-markdown` + `rehype-sanitize` + `rehype-highlight`. The unsafe regex-based HTML injection in `MarkdownPreview.tsx` is deleted.

**Audit issues fixed:**
- [P0] Configs nested Sider responsive failure
- [P0] Prompts nested Sider responsive failure
- [P0] `SaveDiffModal` fixed `width={900}` breaks mobile
- [P0] Prompts editor/preview `Col span={12}` with no breakpoints
- [P0/P1] `MarkdownPreview` XSS vector via unsafe innerHTML injection with regex escape
- [P1] YAML parsed on every keystroke without debounce (perf)
- [P1] `STATE_COLORS` overlap with `TASK_STATUS_COLORS` at divergent values
- [P2] Config forms use scattered validation → zod schemas
- [P2] Monaco theme hardcoded → reads from tokens

### Wave 8 · Shadow + Preferences Migration

**Shadow:**
- `<PageHeader>` + two comparison `<ChartCard>`s + `<DataTable>` of experiments + comparison drawer.

**Preferences:**
- `<PageHeader>` → Appearance card (theme toggle + shortcut hint) → learned rules `<DataTable>`.
- `RuleDetailDrawer` migrates to `<Drawer>`.
- **Backend change:** add `?rule_id=<id>` query parameter to the corrections endpoint; drawer fetches only that rule's corrections.

**Audit issues fixed:**
- [P1] `RuleDetailDrawer` fetches 500 rows for client-side filter (perf)
- [P1] Shadow table missing keyboard row navigation
- [P2] Shadow chart inline hex → tokens
- [P3] Preferences page lacks empty state for zero rules

### Wave 9 · Cleanup

- Remove `antd` + `@ant-design/icons` from `package.json`.
- `npm run build` — expected bundle drop: **at least 40% reduction in total gzipped JS vs. current baseline** (baseline measured in Wave 0 before any changes and recorded in `docs/superpowers/specs/bundle-baseline.txt`).
- Delete `src/theme/darkTheme.ts` (obsolete AntD ConfigProvider token map).
- Grep for remaining `#[0-9a-f]{6}` literals; replace with tokens.
- Accessibility sweep using `@axe-core/react` in dev mode on every page.
- Remove `/dev/primitives` dev-mode gate (keep the route as internal reference).

**Audit issues fixed:**
- [P1] Bundle size > 2 MB chunk warning — resolved
- [P2] Remaining inline hex color instances
- [P3] Lighthouse accessibility score sweep

### Audit coverage summary

| Severity | Count | Resolved in waves |
|---|---|---|
| P0 Blocking | 6 | 5, 7 |
| P1 Major | 12 | 2, 3, 4, 5, 6, 7, 8, 9 |
| P2 Minor | 7 | 4, 5, 6, 7, 8, 9 |
| P3 Polish | 2 | 8, 9 |
| **Total** | **27** | |

---

## 5. Design Decisions & Constraints

### Confirmed decisions

- **Aesthetic direction:** Refined dark luxury on warm charcoal, not blue.
- **Accent:** Champagne gold `#d4a943` default + electric coral `#f56960` alternate, switchable via `localStorage`-backed `useTheme` hook and `⌘.` shortcut.
- **Typography:** Fraunces 300 (display) + Inter 400/500 (body), self-hosted via `@fontsource`.
- **Navigation shell:** Refined left rail with gold left-border active state; no top header bar; theme toggle in rail footer.
- **Charts:** Soft-wash area with low-opacity gradient fill + 1.5 px line; Recharts rethemed, not replaced.
- **Component strategy:** Strip Ant Design entirely. Build on Radix UI + TanStack Table + React Hook Form + Sonner.
- **Accessibility target:** WCAG **AA** across all pages (contrast ratios ≥ 4.5:1).
- **Theme persistence:** `localStorage` per-browser; no backend sync for now.
- **Font hosting:** Self-hosted (no external requests, no FOUT).
- **Testing:** Playwright smoke tests, one happy-path per page, added in Wave 0 and grown per wave.
- **Tooltip delay:** 400 ms (Radix default 700 ms overridden).
- **Menu active state:** Gold left border only (no background fill) — intentional departure from AntD.
- **Pagination footer:** Minimal `Showing 1–50 of 420` + prev/next ghost buttons; no page numbers, no page-size selector.
- **Animation budget:** Minimal motion everywhere; one signature staggered fade-in on dashboard initial load (50 ms per card).
- **Command palette (`⌘K`):** Skipped entirely. Add later when daily use creates genuine friction. YAGNI.

### Voice — empty states

Donna is sharp, confident, efficient, never sycophantic. Empty states should lean into that. Mix of **instructive** with a dash of **personality**:

- Tasks page empty: _"Nothing captured yet. Press ⌘N to add one, or message Donna on Discord and she'll do it for you."_
- Logs page empty (filtered): _"No events match. Widen the window or loosen the filters."_
- Agents page empty: _"No agents configured. Check `config/models.yaml`."_
- Shadow empty: _"No experiments running."_ (neutral — no personality needed here)
- Preferences empty: _"No rules learned yet. Donna picks these up as you correct her."_

Voice rule: instructive first, personality second, never cute, never apologetic.

### Semantic color policy

Semantic colors (`--success`, `--warning`, `--error`) only appear when semantically required. A green pill means "this is actually done." A red pill means "this is actually overdue." Never decorative. No green for emphasis. No red for "cool accent."

---

## 6. Risks & Mitigations

### Visual inconsistency window (Waves 3–8)

Between the first page migration (Wave 3, Dashboard) and the final cleanup (Wave 9), the app will have a mixed look — new primitives on migrated pages, AntD on unmigrated ones.

**Mitigation:** Order pages by visibility. Dashboard first (user looks at it every day), then Tasks (next most visible), then Logs, Agents, Configs + Prompts, Shadow + Preferences. Merge waves quickly so the mixed window is short. Individual waves should not linger in review.

### Chart theme as load-bearing module

`charts/theme.ts` is touched by Wave 3 (dashboard) but its consumers reappear in Wave 8 (shadow). Any change in Wave 3 has a second-order effect in Wave 8.

**Mitigation:** Treat `charts/theme.ts` as an API. Change it in Wave 3 and do not touch it again until Wave 8 verification.

### Web Worker validation (Wave 7)

Moving YAML validation to a Web Worker is a net perf win but introduces async behavior where there was none. Monaco's change handler will no longer receive synchronous validation results.

**Mitigation:** The sticky bottom status bar is the only validation UI; it updates on worker response. No feature in the app depends on synchronous validation. Test: type a malformed YAML line, confirm status bar turns red within 250 ms, confirm editor does not block.

### Preferences backend change (Wave 8)

Adding `?rule_id=<id>` to the corrections endpoint is the only backend change in the migration. If the backend team (or you) push back on this change, `RuleDetailDrawer` stays inefficient.

**Mitigation:** The change is additive — old callers without `?rule_id=` still work. Ship the backend change first (small PR, separate from the frontend migration), then consume it in Wave 8. If blocked, Wave 8 still ships the visual migration; the perf fix becomes a follow-up.

### Bundle size expectation

The target bundle drop (~2 MB → ~800 KB) assumes Radix is tree-shaken aggressively and Recharts survives mostly as-is. Actual drop may be ±200 KB from that target.

**Mitigation:** Measure after Wave 9. If we overshoot (larger than expected), investigate with `rollup-plugin-visualizer`. If we undershoot, we're ahead and should celebrate.

---

## 7. What Does Not Change

Scope discipline — these are explicitly out of scope for this redesign:

- Backend API contracts (except the single `?rule_id=` addition in Wave 8)
- Database schema (no migrations)
- Discord bot, SMS, Gmail integrations
- Agent orchestration, state machine, model routing
- Monaco Editor (stays)
- Recharts (retheme only, not replace)
- React Router v6 routing structure (except two new subroutes for Configs and Prompts)
- Vite build configuration (except Playwright CI addition)
- Authentication, user model, any multi-user concerns

---

## 8. Acceptance Criteria

The redesign is considered complete when:

1. `package.json` no longer contains `antd` or `@ant-design/icons`.
2. All 27 audit issues are resolved (tracked in wave-level PRs with explicit issue callouts in commit messages).
3. Every page uses `<PageHeader>`, `<AppShell>`, and primitives from `src/primitives/`.
4. Every `<DataTable>` consumer supports keyboard navigation (`↑/↓/Enter`).
5. All unsafe raw-HTML injection sites are removed from `src/` — markdown goes through `rehype-sanitize`.
6. `grep -r "#[0-9a-f]\{6\}" src/` returns only files in `src/theme/` and `src/charts/theme.ts`.
7. Bundle size from `npm run build` is reduced by at least 40% in gzipped JS vs. the Wave 0 baseline recorded in `bundle-baseline.txt`.
8. Playwright smoke tests pass for all 8 pages on both themes.
9. `@axe-core/react` reports zero WCAG AA violations in dev mode.
10. Theme toggle flip time (measured from `⌘.` press to full repaint) is under 16 ms.
11. Dashboard initial-load staggered fade-in completes within 300 ms total.
12. `RuleDetailDrawer` fetches only the corrections for the opened rule (verified via Network panel).
