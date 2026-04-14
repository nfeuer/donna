# Donna UI Redesign — Wave 3 (Logs Migration) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate `src/pages/Logs/` off Ant Design onto the Wave 1 primitives, add opt-in virtualization to the shared `DataTable` primitive, and keep the `/admin/logs` API contract untouched. After this plan the Logs page renders with `PageHeader` + primitive filter bar + virtualized `DataTable` + `Drawer`-backed trace view, the nested `<Sider>` is gone, and no component in `src/pages/Logs/` imports from `antd` or `@ant-design/icons`.

**Architecture:**

- The shared `DataTable` primitive grows three optional, backward-compatible props: `virtual`, `rowHeight`, `maxHeight`. When `virtual={true}`, the table drops the client pagination row model, the scroll container is given a fixed `max-height`, and `useVirtualizer` from `@tanstack/react-virtual` renders only visible rows with top/bottom spacer `<tr>`s. Existing non-virtual consumers (`DevPrimitives` story, future Tasks/Shadow/Configs list pages) are unaffected because the prop defaults to `false`.
- The Logs page composition is rebuilt as a two-column CSS Grid: a fixed-width `<aside>` holding the custom `EventTypeTree` (no AntD `Tree` dependency — a small controlled tree built from `Checkbox` primitives plus a chevron disclosure button), and a `<section>` holding `PageHeader`, `FilterBar`, `LogTable`, pagination, and the `TraceView` drawer. The Logs page owns server-side pagination state (`page`, `pageSize`) and renders its own `Prev / Next / Page size` control below the virtualized table. Total-row count comes from the API response.
- Level colouring is centralised in a new `levelStyles.ts` helper that maps the raw level string to a `Pill` variant. This replaces the `LEVEL_COLORS` import from the doomed `darkTheme.ts` — the Logs page will no longer reach into that file, which unblocks Wave 9's deletion.
- The trace view migrates from AntD `Drawer` + `Timeline` + `Descriptions` + `Tag` to the primitive `Drawer` plus a custom vertical timeline rendered as a `<ol>` with a coloured left border keyed off the level `Pill`. Raw structured fields are shown with a `<dl>` in a `ScrollArea`.
- The "Save Preset" flow migrates from `Modal` to the `Dialog` primitive. The `localStorage` preset contract (`donna-log-presets` key, `FilterPreset` shape) is preserved byte-for-byte so existing saved presets keep working.
- `notification.success` becomes `toast.success` from `sonner` — the `<Toaster />` is already mounted globally by `AppShell` (Wave 2) but this plan does not assume Wave 2 is merged; sonner is a runtime dependency listed in `donna-ui/package.json` since Wave 0, so the import works regardless of which shell is active.

**Tech Stack:** React 18, TypeScript 5, `@tanstack/react-table` v8, `@tanstack/react-virtual` v3, Radix UI (Dialog, Checkbox via existing primitives), lucide-react, `sonner` 2, CSS Modules. No new dependencies — every package above is already in `donna-ui/package.json` since Wave 0.

**Spec reference:** `docs/superpowers/specs/2026-04-08-donna-ui-redesign-design.md` §4 Wave 5 (line 371) "Logs Migration" — note that the master plan's **Wave 5** is this plan's **Wave 3** because the user is sequencing waves locally (Wave 2 shell → Wave 3 logs, skipping Dashboard and Tasks for now).

**Precondition:**

- Branch off from `main` — NOT from `wave-2-shell`. The primitives from Wave 0 + Wave 1 are already on `main`; the AppShell migration from Wave 2 is on its own branch and may or may not be merged first. This plan does not depend on `AppShell`; the rewritten Logs page renders identically whether the surrounding shell is the old `components/Layout.tsx` (AntD) or the new `layout/AppShell.tsx`.
- `git log main --oneline | grep "Add primitives barrel export"` should return a hit — that's the Wave 0/1 completion marker.
- `donna-ui/src/primitives/` contains `DataTable.tsx`, `PageHeader.tsx`, `Drawer.tsx`, `Dialog.tsx`, `Input.tsx`, `Segmented.tsx`, `EmptyState.tsx`, `Pill.tsx`, `Button.tsx`, `Checkbox.tsx`, `Select.tsx`, `ScrollArea.tsx`.
- `@tanstack/react-virtual@^3.13.23` is present in `package.json`.
- The user is `nick` (branch owner `nfeuer`). The main branch is `main`. Worktree is `/home/feuer/Documents/Projects/donna`.

---

## Audit issues fixed in this wave

The spec (§4 Wave 5) lists four audit items this wave resolves. Each is addressed below; confirm at the end of Task 12.

- **[P0] Logs page responsive failure — nested `<Sider width={210}>` never collapsed on mobile.** Resolved by deleting the nested `<Layout>` + `<Sider>` entirely and replacing it with a CSS Grid whose sidebar column collapses to a row on `max-width: 900px` (Tasks 4, 10).
- **[P1] Logs filter form lacks ARIA labels.** Resolved in Task 6 — every control in `FilterBar.tsx` is wrapped in a labelled `FormField` or carries an explicit `aria-label` string. The `Segmented` primitive already emits `role="tab" aria-selected`.
- **[P2] Timestamp column format inconsistency.** Resolved in Task 8 — all timestamp rendering goes through a single `formatTimestamp(iso: string)` helper defined at the top of `LogTable.tsx`. The trace view uses the same helper (Task 9). No inline `.replace("T", " ").slice(...)` calls remain.
- **[P2] Level tag colors scattered inline.** Resolved in Task 1 — `levelStyles.ts` owns the single `levelToPillVariant` mapping; `LogTable`, `TraceView`, and any future log viewer import it. No component reads `LEVEL_COLORS` from `darkTheme.ts` after this wave.

---

## File Structure Overview

### Modified in Wave 3

```
donna-ui/src/
└── primitives/
    ├── DataTable.tsx                         (MODIFIED: +virtual/rowHeight/maxHeight props)
    ├── DataTable.module.css                  (MODIFIED: +.virtualScroll class)
    └── (no change to primitives/index.ts — the new props are not re-exported)
```

### Created in Wave 3

```
donna-ui/src/
├── pages/
│   └── Logs/
│       ├── index.tsx                         (REWRITTEN — see Task 10)
│       ├── Logs.module.css                   (CREATED)
│       ├── EventTypeTree.tsx                 (REWRITTEN — see Task 4)
│       ├── EventTypeTree.module.css          (CREATED)
│       ├── FilterBar.tsx                     (CREATED)
│       ├── FilterBar.module.css              (CREATED)
│       ├── DateRangePicker.tsx               (CREATED)
│       ├── DateRangePicker.module.css        (CREATED)
│       ├── SavePresetDialog.tsx              (CREATED)
│       ├── LogTable.tsx                      (REWRITTEN — see Task 8)
│       ├── LogTable.module.css               (CREATED)
│       ├── TraceView.tsx                     (REWRITTEN — see Task 9)
│       ├── TraceView.module.css              (CREATED)
│       └── levelStyles.ts                    (CREATED)
│
├── pages/DevPrimitives/
│   └── index.tsx                             (MODIFIED: +"Virtualized DataTable" story section)
│
└── tests/e2e/smoke/
    └── logs.spec.ts                          (EXPANDED: real selector assertions)
```

**Principle:** Each `.tsx` stays under ~130 lines and colocates its `.module.css`. No component in this wave imports from `antd`, `@ant-design/icons`, or `../../theme/darkTheme`. The Logs page's `RefreshButton` import is replaced inline by a small `<Button>` — the shared `src/components/RefreshButton.tsx` is left untouched because other AntD pages still consume it (they migrate in later waves).

**Out of scope for Wave 3 (called out explicitly to prevent scope creep):**

- `src/components/RefreshButton.tsx` — still consumed by Tasks/Agents/Dashboard/Shadow/Preferences pages, all of which are still AntD. Do not touch.
- `src/theme/darkTheme.ts` — the Logs page will stop importing from it, but the file stays for other pages. Deleted in Wave 9.
- `fetchLogs` / `fetchTrace` / `fetchEventTypes` in `src/api/logs.ts` — the API contract is frozen. Types stay exactly as they are. Do not touch.
- Auto-refresh. The original `RefreshButton` supports `autoRefreshMs` but the Logs page never passed it; this wave preserves that behaviour (manual refresh only).
- Tailwind. This project is CSS-modules-only.
- Mobile-first redesign of the event tree — the sidebar collapses to a row above the table on narrow viewports, but the tree UI stays identical. Proper mobile treatment would be its own design exercise.
- Any change to the Playwright `mockAdminApi` helper behaviour for other pages. Task 11 only touches `logs.spec.ts`.

---

## Wave 3 · Logs Migration

### Task 1: Create the wave-3-logs branch and the `levelStyles.ts` helper

The level-to-variant map is trivial, self-contained, and unblocks later tasks. Doing it first establishes the branch and makes it obvious that no code in this wave is allowed to import `LEVEL_COLORS` from `darkTheme.ts`.

**Files:**
- Create: `donna-ui/src/pages/Logs/levelStyles.ts`

- [ ] **Step 1: Branch off main**

```bash
cd /home/feuer/Documents/Projects/donna
git fetch origin
git checkout main
git pull origin main
git checkout -b wave-3-logs
```

Expected: new branch `wave-3-logs` created from `main`. `git status` clean. `git log --oneline -1` shows the most recent main commit (one of the Wave 0/1 "Add ... primitive" commits).

If `main` is behind what you expect (e.g. the Wave 2 shell commits show up), that's a sign wave-2-shell was merged while this plan was being written — continue anyway, the plan does not depend on shell state.

- [ ] **Step 2: Create `levelStyles.ts`**

Create `donna-ui/src/pages/Logs/levelStyles.ts`:

```ts
import type { PillVariant } from "../../primitives/Pill";

/**
 * Single source of truth for how log levels render as Pills.
 * Replaces the `LEVEL_COLORS` import from `theme/darkTheme.ts`
 * (Wave 3 audit item P2: "Level tag colors scattered inline").
 *
 * DEBUG    → muted  (grey)
 * INFO     → accent (gold/coral, depending on theme)
 * WARNING  → warning
 * ERROR    → error
 * CRITICAL → error  (kept on the same variant; distinguished at
 *                    call sites by surrounding context, not colour,
 *                    to avoid inventing a sixth Pill variant)
 */
export function levelToPillVariant(level: string | undefined): PillVariant {
  switch (level?.toUpperCase()) {
    case "DEBUG":
      return "muted";
    case "INFO":
      return "accent";
    case "WARNING":
    case "WARN":
      return "warning";
    case "ERROR":
    case "CRITICAL":
      return "error";
    default:
      return "muted";
  }
}

export const LEVEL_OPTIONS = [
  { value: "", label: "All" },
  { value: "DEBUG", label: "Debug" },
  { value: "INFO", label: "Info" },
  { value: "WARNING", label: "Warn" },
  { value: "ERROR", label: "Error" },
  { value: "CRITICAL", label: "Critical" },
] as const;

export type LevelFilterValue = (typeof LEVEL_OPTIONS)[number]["value"];
```

- [ ] **Step 3: Typecheck**

Run from `donna-ui/`:

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc -b --noEmit
```

Expected: no errors. The file imports `PillVariant` from the existing primitive — that type is already exported from `primitives/index.ts` and `primitives/Pill.tsx`.

- [ ] **Step 4: Commit**

```bash
cd /home/feuer/Documents/Projects/donna
git add donna-ui/src/pages/Logs/levelStyles.ts
git commit -m "$(cat <<'EOF'
Add levelStyles helper for Logs page

Centralises the level→Pill-variant mapping so LogTable and TraceView
share one source of truth, unblocking the deletion of LEVEL_COLORS
from darkTheme.ts in Wave 9.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Add opt-in virtualization to the `DataTable` primitive

Adds three new props to the shared primitive. Existing non-virtual consumers are untouched because the new props default to off.

**Files:**
- Modify: `donna-ui/src/primitives/DataTable.tsx`
- Modify: `donna-ui/src/primitives/DataTable.module.css`

- [ ] **Step 1: Replace `DataTable.tsx`**

Overwrite `donna-ui/src/primitives/DataTable.tsx` with:

```tsx
import {
  flexRender,
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ChevronDown, ChevronUp } from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { cn } from "../lib/cn";
import { Button } from "./Button";
import { Skeleton } from "./Skeleton";
import styles from "./DataTable.module.css";

interface DataTableProps<T> {
  data: T[];
  columns: ColumnDef<T>[];
  getRowId: (row: T) => string;
  onRowClick?: (row: T) => void;
  selectedRowId?: string | null;
  pageSize?: number;
  loading?: boolean;
  emptyState?: ReactNode;
  /** When true, ↑/↓ navigates rows and Enter activates onRowClick. Ignored in virtual mode. */
  keyboardNav?: boolean;
  /** Opt into virtualized rendering. Disables client pagination. */
  virtual?: boolean;
  /** Fixed row height in px when virtual=true. Default 44. */
  rowHeight?: number;
  /** Scroll container max-height in px when virtual=true. Default 600. */
  maxHeight?: number;
}

/**
 * Single table component for the entire app. Built on TanStack Table.
 * Sort + paginate + row selection + keyboard nav. Optional virtualization
 * via @tanstack/react-virtual for log-scale datasets (Wave 3).
 */
export function DataTable<T>({
  data,
  columns,
  getRowId,
  onRowClick,
  selectedRowId,
  pageSize = 50,
  loading = false,
  emptyState,
  keyboardNav = false,
  virtual = false,
  rowHeight = 44,
  maxHeight = 600,
}: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [focusIndex, setFocusIndex] = useState(0);
  const bodyRef = useRef<HTMLTableSectionElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    // Only wire up client pagination when not virtualizing.
    ...(virtual ? {} : { getPaginationRowModel: getPaginationRowModel() }),
    getRowId,
    initialState: { pagination: { pageSize } },
  });

  const rows = table.getRowModel().rows;
  const colCount = table.getVisibleFlatColumns().length;

  const virtualizer = useVirtualizer({
    count: virtual ? rows.length : 0,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => rowHeight,
    overscan: 8,
  });

  const virtualRows = virtual ? virtualizer.getVirtualItems() : [];
  const totalSize = virtual ? virtualizer.getTotalSize() : 0;
  const paddingTop = virtual && virtualRows.length > 0 ? virtualRows[0].start : 0;
  const paddingBottom =
    virtual && virtualRows.length > 0
      ? totalSize - virtualRows[virtualRows.length - 1].end
      : 0;

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTableSectionElement>) => {
      if (!keyboardNav || virtual || rows.length === 0) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setFocusIndex((i) => Math.min(i + 1, rows.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setFocusIndex((i) => Math.max(i - 1, 0));
      } else if (e.key === "Enter") {
        e.preventDefault();
        const row = rows[focusIndex];
        if (row && onRowClick) onRowClick(row.original);
      }
    },
    [keyboardNav, virtual, rows, focusIndex, onRowClick],
  );

  useEffect(() => {
    if (!keyboardNav || virtual || !bodyRef.current) return;
    const el = bodyRef.current.querySelectorAll<HTMLTableRowElement>("tr")[focusIndex];
    el?.focus();
  }, [focusIndex, keyboardNav, virtual]);

  const pageIndex = table.getState().pagination.pageIndex;
  const pageCount = table.getPageCount();
  const totalRows = data.length;
  const start = pageIndex * pageSize + 1;
  const end = Math.min((pageIndex + 1) * pageSize, totalRows);

  const wrapperStyle = useMemo(
    () => (virtual ? { maxHeight: `${maxHeight}px` } : undefined),
    [virtual, maxHeight],
  );

  if (loading) {
    return (
      <div className={styles.wrapper}>
        <div className={styles.scroll}>
          <table className={styles.table}>
            <tbody>
              {Array.from({ length: 5 }).map((_, i) => (
                <tr key={i}>
                  <td className={styles.bodyCell}>
                    <Skeleton height={16} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  if (rows.length === 0 && emptyState) {
    return <div className={styles.empty}>{emptyState}</div>;
  }

  return (
    <div className={styles.wrapper}>
      <div
        ref={scrollRef}
        className={cn(styles.scroll, virtual && styles.virtualScroll)}
        style={wrapperStyle}
      >
        <table className={styles.table}>
          <thead className={cn(virtual && styles.stickyHead)}>
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id} className={styles.headerRow}>
                {hg.headers.map((h) => {
                  const sortable = h.column.getCanSort();
                  const sort = h.column.getIsSorted();
                  return (
                    <th
                      key={h.id}
                      className={cn(styles.headerCell, sortable && styles.sortable)}
                      onClick={sortable ? h.column.getToggleSortingHandler() : undefined}
                      style={{ width: h.getSize() === 150 ? undefined : h.getSize() }}
                    >
                      {flexRender(h.column.columnDef.header, h.getContext())}
                      {sortable && (
                        <span className={cn(styles.sortIcon, sort && styles.active)}>
                          {sort === "desc" ? <ChevronDown size={11} /> : <ChevronUp size={11} />}
                        </span>
                      )}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody ref={bodyRef} onKeyDown={handleKeyDown}>
            {virtual && paddingTop > 0 && (
              <tr aria-hidden="true">
                <td colSpan={colCount} style={{ height: paddingTop, padding: 0, border: 0 }} />
              </tr>
            )}
            {(virtual ? virtualRows.map((vr) => rows[vr.index]) : rows).map((row, idx) => {
              const id = getRowId(row.original);
              const selected = selectedRowId === id;
              return (
                <tr
                  key={row.id}
                  className={cn(
                    styles.row,
                    selected && styles.selected,
                    onRowClick && styles.clickable,
                  )}
                  onClick={onRowClick ? () => onRowClick(row.original) : undefined}
                  tabIndex={keyboardNav && !virtual ? (idx === focusIndex ? 0 : -1) : undefined}
                  aria-selected={selected}
                  style={virtual ? { height: rowHeight } : undefined}
                >
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id} className={styles.bodyCell}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              );
            })}
            {virtual && paddingBottom > 0 && (
              <tr aria-hidden="true">
                <td colSpan={colCount} style={{ height: paddingBottom, padding: 0, border: 0 }} />
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {!virtual && totalRows > pageSize && (
        <div className={styles.footer}>
          <span>
            Showing {start}–{end} of {totalRows}
          </span>
          <div className={styles.footerActions}>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => table.previousPage()}
              disabled={!table.getCanPreviousPage()}
            >
              Prev
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => table.nextPage()}
              disabled={!table.getCanNextPage()}
            >
              Next
            </Button>
          </div>
        </div>
      )}
      {!virtual && pageCount > 1 && totalRows <= pageSize && null}
    </div>
  );
}
```

- [ ] **Step 2: Append virtualization styles to `DataTable.module.css`**

Open `donna-ui/src/primitives/DataTable.module.css` and append the following at the end of the file (keep existing rules intact):

```css
/* Virtualized mode — fixed-height scroll container with sticky header. */
.virtualScroll {
  overflow-y: auto;
  position: relative;
}

.stickyHead {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--color-bg);
}
.stickyHead .headerCell {
  background: var(--color-bg);
}
```

- [ ] **Step 3: Typecheck**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc -b --noEmit
```

Expected: no errors.

- [ ] **Step 4: Run existing tests**

```bash
npm run test:e2e
```

Expected: all existing tests (dashboard, tasks, logs, agents, configs, prompts, shadow, preferences, dev-primitives, app-shell) pass. Nothing should regress — every existing caller passes `virtual={undefined}` which defaults to `false`, preserving the old code path.

If `npm run test:e2e` takes too long or the environment is headless, run only the primitives smoke test:

```bash
npx playwright test tests/e2e/smoke/dev-primitives.spec.ts
```

- [ ] **Step 5: Commit**

```bash
cd /home/feuer/Documents/Projects/donna
git add donna-ui/src/primitives/DataTable.tsx donna-ui/src/primitives/DataTable.module.css
git commit -m "$(cat <<'EOF'
Add opt-in virtualization to DataTable primitive

Adds virtual/rowHeight/maxHeight props. When virtual=true, client
pagination is disabled, the scroll container gets a fixed max-height,
and @tanstack/react-virtual renders only visible rows via top/bottom
spacer <tr>s. Sticky header stays in view while scrolling. Defaults
off — existing callers are untouched.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Add a "Virtualized DataTable" story to `DevPrimitives`

Demonstrates and smoke-tests the new virtual mode via the existing gallery page.

**Files:**
- Modify: `donna-ui/src/pages/DevPrimitives/index.tsx`

- [ ] **Step 1: Add the story section**

Open `donna-ui/src/pages/DevPrimitives/index.tsx`. Two edits are needed.

First, just below the existing `demoColumns` declaration near the top of the file (around line 58, right before `export default function DevPrimitivesPage`), add a large fake dataset generator:

```tsx
// Large dataset to exercise virtualization. 2000 rows is enough to
// make non-virtualized rendering visibly chunky on a mid-range laptop.
const bigRows: DemoTask[] = Array.from({ length: 2000 }, (_, i) => ({
  id: `v${i}`,
  title: `Virtualized row ${i + 1}`,
  status: (["scheduled", "in_progress", "blocked", "done"] as const)[i % 4],
  due: `Apr ${(i % 28) + 1} 09:00`,
}));
```

Second, inside the component's return, just after the existing `StorySection id="datatable"` block and before the trailing `{/* Stories appended by subsequent plan tasks */}` comment, add a new story:

```tsx
<StorySection
  id="datatable-virtual"
  eyebrow="Primitive · 20 · Virtual"
  title="DataTable (virtualized)"
  note="Same primitive with virtual=true + rowHeight=44 + maxHeight=400. Scroll to verify the list is 2000 rows without dropping frames."
>
  <div style={{ width: "100%" }}>
    <DataTable
      data={bigRows}
      columns={demoColumns}
      getRowId={(r) => r.id}
      virtual
      rowHeight={44}
      maxHeight={400}
    />
  </div>
</StorySection>
```

- [ ] **Step 2: Typecheck**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc -b --noEmit
```

Expected: no errors.

- [ ] **Step 3: Manual smoke test**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npm run dev
```

In a browser, visit `http://localhost:5173/dev/primitives` and scroll down to the new "DataTable (virtualized)" section.

- [ ] Scrolling the virtualized table is smooth.
- [ ] Header stays sticky at the top of the scroll viewport.
- [ ] The scrollbar indicates 2000 rows (tall scroll content).
- [ ] Sorting by clicking "Title" still works.
- [ ] The existing non-virtualized "DataTable" story directly above still works and still paginates via Prev/Next.

Stop the dev server (`Ctrl+C`).

- [ ] **Step 4: Commit**

```bash
cd /home/feuer/Documents/Projects/donna
git add donna-ui/src/pages/DevPrimitives/index.tsx
git commit -m "$(cat <<'EOF'
Add virtualized DataTable story to dev primitives gallery

2000-row demo exercises the new virtual/rowHeight/maxHeight props and
gives reviewers a visible smoke test for scroll performance and
sticky-header behaviour.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Rewrite `EventTypeTree` without AntD `Tree`

Replaces AntD `Tree` + `Button` + `Spin` with a custom controlled tree built from `Checkbox` + a chevron disclosure button. Categories are expanded by default.

**Files:**
- Rewrite: `donna-ui/src/pages/Logs/EventTypeTree.tsx`
- Create: `donna-ui/src/pages/Logs/EventTypeTree.module.css`

- [ ] **Step 1: Rewrite `EventTypeTree.tsx`**

Overwrite `donna-ui/src/pages/Logs/EventTypeTree.tsx` with:

```tsx
import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Button } from "../../primitives/Button";
import { Checkbox } from "../../primitives/Checkbox";
import { Skeleton } from "../../primitives/Skeleton";
import { cn } from "../../lib/cn";
import { fetchEventTypes } from "../../api/logs";
import styles from "./EventTypeTree.module.css";

interface Props {
  selected: string[];
  onChange: (selected: string[]) => void;
}

/**
 * Sidebar event-type picker. Categories are collapsible; inside each
 * category every event is a Checkbox primitive. Key format matches the
 * AntD Tree version byte-for-byte: `${category}.${event}`, so the API
 * filter string (joined with commas) is unchanged.
 */
export default function EventTypeTree({ selected, onChange }: Props) {
  const [tree, setTree] = useState<Record<string, string[]>>({});
  const [loading, setLoading] = useState(true);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  useEffect(() => {
    fetchEventTypes()
      .then((data) => setTree(data ?? {}))
      .catch(() => setTree({}))
      .finally(() => setLoading(false));
  }, []);

  const allKeys = useMemo(
    () =>
      Object.entries(tree).flatMap(([cat, evts]) =>
        evts.map((e) => `${cat}.${e}`),
      ),
    [tree],
  );

  const selectedSet = useMemo(() => new Set(selected), [selected]);

  const toggleLeaf = (key: string) => {
    const next = new Set(selectedSet);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    onChange(Array.from(next));
  };

  const toggleCategory = (category: string) => {
    const next = new Set(collapsed);
    if (next.has(category)) next.delete(category);
    else next.add(category);
    setCollapsed(next);
  };

  if (loading) {
    return (
      <div className={styles.root}>
        <Skeleton height={14} />
        <Skeleton height={14} />
        <Skeleton height={14} />
      </div>
    );
  }

  const categories = Object.entries(tree);

  return (
    <div className={styles.root}>
      <div className={styles.actions}>
        <Button variant="ghost" size="sm" onClick={() => onChange(allKeys)}>
          All
        </Button>
        <Button variant="ghost" size="sm" onClick={() => onChange([])}>
          Clear
        </Button>
      </div>

      {categories.length === 0 ? (
        <div className={styles.emptyHint}>No event types registered.</div>
      ) : (
        <ul className={styles.list}>
          {categories.map(([category, events]) => {
            const isCollapsed = collapsed.has(category);
            return (
              <li key={category} className={styles.group}>
                <button
                  type="button"
                  className={styles.groupHeader}
                  onClick={() => toggleCategory(category)}
                  aria-expanded={!isCollapsed}
                >
                  <span className={styles.chevron} aria-hidden="true">
                    {isCollapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
                  </span>
                  <span className={styles.groupLabel}>{category}</span>
                  <span className={styles.groupCount}>{events.length}</span>
                </button>
                {!isCollapsed && (
                  <ul className={styles.children}>
                    {events.map((evt) => {
                      const key = `${category}.${evt}`;
                      const checked = selectedSet.has(key);
                      return (
                        <li key={key} className={cn(styles.leaf, checked && styles.leafActive)}>
                          <label className={styles.leafLabel}>
                            <Checkbox
                              checked={checked}
                              onCheckedChange={() => toggleLeaf(key)}
                              aria-label={`Toggle ${key}`}
                            />
                            <span className={styles.leafText}>{evt}</span>
                          </label>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
```

Confirm the `Checkbox` primitive's prop API matches the usage above — expected props are `checked` and `onCheckedChange`. Open `donna-ui/src/primitives/Checkbox.tsx` and scan its prop signature to confirm before continuing. If the primitive uses different prop names, adapt the two `Checkbox` call sites above (and only those two) to match — do **not** modify the primitive.

- [ ] **Step 2: Create `EventTypeTree.module.css`**

Create `donna-ui/src/pages/Logs/EventTypeTree.module.css`:

```css
.root {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-3);
  font-family: var(--font-body);
  font-size: var(--text-body);
  color: var(--color-text-secondary);
}

.actions {
  display: flex;
  gap: var(--space-2);
}

.list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.group {
  display: flex;
  flex-direction: column;
}

.groupHeader {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
  background: transparent;
  border: 0;
  color: var(--color-text);
  font-family: var(--font-body);
  font-size: var(--text-label);
  letter-spacing: var(--tracking-wide);
  text-transform: uppercase;
  padding: var(--space-2) 0;
  cursor: pointer;
  text-align: left;
}
.groupHeader:hover {
  color: var(--color-accent);
}

.chevron {
  display: inline-flex;
  color: var(--color-text-dim);
}

.groupLabel {
  flex: 1;
}

.groupCount {
  color: var(--color-text-dim);
  font-size: var(--text-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
}

.children {
  list-style: none;
  margin: 0;
  padding: 0 0 var(--space-2) var(--space-4);
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.leaf {
  padding: 4px 6px;
  border-radius: var(--radius-control);
  transition: background var(--duration-fast) var(--ease-out);
}
.leaf:hover {
  background: var(--color-accent-soft);
}
.leafActive {
  background: var(--color-accent-soft);
}

.leafLabel {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  cursor: pointer;
  font-size: var(--text-body);
  color: var(--color-text-secondary);
}

.leafText {
  font-family: var(--font-mono);
  font-size: var(--text-label);
}

.emptyHint {
  color: var(--color-text-dim);
  font-size: var(--text-label);
  font-style: italic;
  padding: var(--space-2) 0;
}
```

- [ ] **Step 3: Typecheck**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc -b --noEmit
```

Expected: no errors. (`index.tsx` still imports `EventTypeTree` via the default export at this point — the rewrite preserves it.)

- [ ] **Step 4: Commit**

```bash
cd /home/feuer/Documents/Projects/donna
git add donna-ui/src/pages/Logs/EventTypeTree.tsx donna-ui/src/pages/Logs/EventTypeTree.module.css
git commit -m "$(cat <<'EOF'
Rewrite Logs EventTypeTree without AntD Tree

Custom controlled tree on the Checkbox primitive with a chevron
disclosure per category. Preserves the ${category}.${event} key
format so the /admin/logs filter string is unchanged.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Create the `DateRangePicker` component

A two-input `datetime-local` range picker — zero dependencies, accessible, and sufficient for the Logs filter use case. Emits ISO strings via `onChange`.

**Files:**
- Create: `donna-ui/src/pages/Logs/DateRangePicker.tsx`
- Create: `donna-ui/src/pages/Logs/DateRangePicker.module.css`

- [ ] **Step 1: Create `DateRangePicker.tsx`**

Create `donna-ui/src/pages/Logs/DateRangePicker.tsx`:

```tsx
import { useId } from "react";
import styles from "./DateRangePicker.module.css";

export interface DateRangeValue {
  start: string | null; // ISO string or null
  end: string | null;
}

interface Props {
  value: DateRangeValue;
  onChange: (next: DateRangeValue) => void;
}

/**
 * Two-field datetime-local range picker. The native input format is
 * "YYYY-MM-DDTHH:mm" — we normalise to full ISO strings on the way out
 * so the /admin/logs API contract (ISO 8601) stays intact.
 */
export function DateRangePicker({ value, onChange }: Props) {
  const startId = useId();
  const endId = useId();

  const toLocalInput = (iso: string | null): string => {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };

  const fromLocalInput = (v: string): string | null => {
    if (!v) return null;
    const d = new Date(v);
    return Number.isNaN(d.getTime()) ? null : d.toISOString();
  };

  return (
    <div className={styles.root}>
      <label htmlFor={startId} className={styles.label}>
        From
      </label>
      <input
        id={startId}
        type="datetime-local"
        className={styles.input}
        value={toLocalInput(value.start)}
        onChange={(e) => onChange({ ...value, start: fromLocalInput(e.target.value) })}
        aria-label="Start time"
      />
      <span className={styles.separator} aria-hidden="true">
        →
      </span>
      <label htmlFor={endId} className={styles.label}>
        To
      </label>
      <input
        id={endId}
        type="datetime-local"
        className={styles.input}
        value={toLocalInput(value.end)}
        onChange={(e) => onChange({ ...value, end: fromLocalInput(e.target.value) })}
        aria-label="End time"
      />
      {(value.start || value.end) && (
        <button
          type="button"
          className={styles.clear}
          onClick={() => onChange({ start: null, end: null })}
          aria-label="Clear date range"
        >
          Clear
        </button>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Create `DateRangePicker.module.css`**

Create `donna-ui/src/pages/Logs/DateRangePicker.module.css`:

```css
.root {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-family: var(--font-body);
  font-size: var(--text-label);
}

.label {
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: var(--tracking-wide);
  font-size: var(--text-eyebrow);
}

.input {
  background: var(--color-inset);
  color: var(--color-text);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-control);
  padding: 6px 8px;
  font-family: var(--font-mono);
  font-size: var(--text-label);
  color-scheme: dark;
}
.input:focus {
  outline: none;
  border-color: var(--color-accent);
}

.separator {
  color: var(--color-text-dim);
}

.clear {
  background: transparent;
  border: 0;
  color: var(--color-text-muted);
  font-size: var(--text-label);
  letter-spacing: var(--tracking-wide);
  text-transform: uppercase;
  cursor: pointer;
  padding: 4px 6px;
}
.clear:hover {
  color: var(--color-accent);
}
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
git add donna-ui/src/pages/Logs/DateRangePicker.tsx donna-ui/src/pages/Logs/DateRangePicker.module.css
git commit -m "$(cat <<'EOF'
Add native DateRangePicker for Logs filter bar

Two-input datetime-local range picker. Zero deps, ISO strings on the
way out, preserves the /admin/logs start/end query contract.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Create the `FilterBar` component

The filter row: search `Input`, level `Segmented`, `DateRangePicker`, preset `Select`, save-preset button, source `Pill`, refresh button. All aria-labelled.

**Files:**
- Create: `donna-ui/src/pages/Logs/FilterBar.tsx`
- Create: `donna-ui/src/pages/Logs/FilterBar.module.css`

- [ ] **Step 1: Create `FilterBar.tsx`**

Create `donna-ui/src/pages/Logs/FilterBar.tsx`:

```tsx
import { RotateCw, Save, Trash2 } from "lucide-react";
import { Button } from "../../primitives/Button";
import { Input } from "../../primitives/Input";
import { Pill } from "../../primitives/Pill";
import { Segmented } from "../../primitives/Segmented";
import { Select, SelectItem } from "../../primitives/Select";
import { DateRangePicker, type DateRangeValue } from "./DateRangePicker";
import { LEVEL_OPTIONS, type LevelFilterValue } from "./levelStyles";
import styles from "./FilterBar.module.css";

export interface FilterPreset {
  name: string;
  eventTypes: string[];
  level: string;
  search: string;
}

interface Props {
  search: string;
  onSearchChange: (v: string) => void;
  level: LevelFilterValue;
  onLevelChange: (v: LevelFilterValue) => void;
  dateRange: DateRangeValue;
  onDateRangeChange: (v: DateRangeValue) => void;
  source: string;
  presets: FilterPreset[];
  onLoadPreset: (name: string) => void;
  onDeletePreset: (name: string) => void;
  onOpenSavePreset: () => void;
  onRefresh: () => void;
  refreshing: boolean;
}

/**
 * Single filter row for the Logs page. Every interactive element has
 * an explicit aria-label (audit item P1 "Logs filter form lacks ARIA
 * labels").
 */
export function FilterBar({
  search,
  onSearchChange,
  level,
  onLevelChange,
  dateRange,
  onDateRangeChange,
  source,
  presets,
  onLoadPreset,
  onDeletePreset,
  onOpenSavePreset,
  onRefresh,
  refreshing,
}: Props) {
  return (
    <div className={styles.root} role="search" aria-label="Log filters">
      <div className={styles.row}>
        <Input
          type="search"
          className={styles.search}
          placeholder="Search message text…"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          aria-label="Search logs"
        />
        <Segmented
          value={level}
          onValueChange={onLevelChange}
          options={LEVEL_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
          aria-label="Log level filter"
        />
      </div>

      <div className={styles.row}>
        <DateRangePicker value={dateRange} onChange={onDateRangeChange} />
        <div className={styles.presets}>
          <Select
            value=""
            onValueChange={(v) => v && onLoadPreset(v)}
            placeholder="Load preset…"
            aria-label="Load saved filter preset"
          >
            {presets.length === 0 ? (
              <SelectItem value="__none__">No presets saved</SelectItem>
            ) : (
              presets.map((p) => (
                <SelectItem key={p.name} value={p.name}>
                  {p.name}
                </SelectItem>
              ))
            )}
          </Select>
          {presets.length > 0 && (
            <Select
              value=""
              onValueChange={(v) => v && onDeletePreset(v)}
              placeholder="Delete preset…"
              aria-label="Delete saved filter preset"
            >
              {presets.map((p) => (
                <SelectItem key={p.name} value={p.name}>
                  <span className={styles.deletePresetItem}>
                    <Trash2 size={11} /> {p.name}
                  </span>
                </SelectItem>
              ))}
            </Select>
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={onOpenSavePreset}
            aria-label="Save current filters as preset"
          >
            <Save size={12} /> Save
          </Button>
        </div>
        <div className={styles.spacer} />
        {source && (
          <Pill variant="muted" aria-label={`Log source: ${source}`}>
            {source}
          </Pill>
        )}
        <Button
          variant="ghost"
          size="sm"
          onClick={onRefresh}
          disabled={refreshing}
          aria-label="Refresh log list"
        >
          <RotateCw size={12} className={refreshing ? styles.spin : undefined} /> Refresh
        </Button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Create `FilterBar.module.css`**

Create `donna-ui/src/pages/Logs/FilterBar.module.css`:

```css
.root {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-3) 0;
  border-bottom: 1px solid var(--color-border);
}

.row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.search {
  flex: 1 1 240px;
  min-width: 200px;
}

.presets {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

.deletePresetItem {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--color-error);
}

.spacer {
  flex: 1;
}

.spin {
  animation: spin 900ms linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
```

- [ ] **Step 3: Typecheck**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc -b --noEmit
```

Expected: no errors.

If the `Segmented` primitive's option type demands a narrower generic (it's generic over `T extends string`), the call-site above should infer `T = LevelFilterValue` automatically because `LEVEL_OPTIONS` is declared `as const` in `levelStyles.ts`. If TypeScript instead infers `T = string`, add an explicit generic: `<Segmented<LevelFilterValue> ... />`.

- [ ] **Step 4: Commit**

```bash
cd /home/feuer/Documents/Projects/donna
git add donna-ui/src/pages/Logs/FilterBar.tsx donna-ui/src/pages/Logs/FilterBar.module.css
git commit -m "$(cat <<'EOF'
Add FilterBar for Logs page on primitives

Single filter row: search Input, level Segmented, DateRangePicker,
preset Select, source Pill, refresh Button. Every control has an
explicit aria-label (Wave 3 audit item P1).

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Create `SavePresetDialog` on the `Dialog` primitive

Replaces the AntD `Modal` + `Input` combination used for preset saving.

**Files:**
- Create: `donna-ui/src/pages/Logs/SavePresetDialog.tsx`

- [ ] **Step 1: Create `SavePresetDialog.tsx`**

Create `donna-ui/src/pages/Logs/SavePresetDialog.tsx`:

```tsx
import { useEffect, useState } from "react";
import {
  Dialog,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "../../primitives/Dialog";
import { Button } from "../../primitives/Button";
import { FormField, Input } from "../../primitives/Input";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSave: (name: string) => void;
}

/**
 * Thin Dialog wrapper that captures a preset name. Submits on Enter
 * or the Save button; disabled while the name is empty.
 */
export function SavePresetDialog({ open, onOpenChange, onSave }: Props) {
  const [name, setName] = useState("");

  useEffect(() => {
    if (!open) setName("");
  }, [open]);

  const canSave = name.trim().length > 0;

  const submit = () => {
    if (!canSave) return;
    onSave(name.trim());
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogHeader>
        <DialogTitle>Save filter preset</DialogTitle>
        <DialogDescription>
          Save the current event types, level, and search query under a name you'll remember.
        </DialogDescription>
      </DialogHeader>
      <FormField label="Preset name">
        {(props) => (
          <Input
            {...props}
            placeholder="e.g. Agent errors last 24h"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submit();
            }}
            autoFocus
          />
        )}
      </FormField>
      <DialogFooter>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>
          Cancel
        </Button>
        <Button onClick={submit} disabled={!canSave}>
          Save preset
        </Button>
      </DialogFooter>
    </Dialog>
  );
}
```

Before continuing, open `donna-ui/src/primitives/Dialog.tsx` to confirm the Dialog primitive exposes `Dialog`, `DialogHeader`, `DialogTitle`, `DialogDescription`, and `DialogFooter` exports, and that the root `Dialog` accepts `open` + `onOpenChange` props. The barrel export at `primitives/index.ts` already re-exports all of these (verified in Task 0 of this plan's reconnaissance).

- [ ] **Step 2: Typecheck**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc -b --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
cd /home/feuer/Documents/Projects/donna
git add donna-ui/src/pages/Logs/SavePresetDialog.tsx
git commit -m "$(cat <<'EOF'
Add SavePresetDialog on Dialog primitive

Replaces the AntD Modal used in the Logs page preset-save flow.
Submits on Enter, disabled while name is empty.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Rewrite `LogTable` on the virtualized `DataTable`

Builds the column definitions, uses the new `virtual` prop, centralises timestamp formatting, emits the `Pill` for level via `levelToPillVariant`.

**Files:**
- Rewrite: `donna-ui/src/pages/Logs/LogTable.tsx`
- Create: `donna-ui/src/pages/Logs/LogTable.module.css`

- [ ] **Step 1: Rewrite `LogTable.tsx`**

Overwrite `donna-ui/src/pages/Logs/LogTable.tsx` with:

```tsx
import { useMemo } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { Download } from "lucide-react";
import { Button } from "../../primitives/Button";
import { DataTable } from "../../primitives/DataTable";
import { EmptyState } from "../../primitives/EmptyState";
import { Pill } from "../../primitives/Pill";
import type { LogEntry } from "../../api/logs";
import { exportToCsv } from "../../utils/csvExport";
import { levelToPillVariant } from "./levelStyles";
import styles from "./LogTable.module.css";

interface Props {
  entries: LogEntry[];
  loading: boolean;
  onCorrelationClick: (id: string) => void;
  onTaskClick: (id: string) => void;
}

/**
 * Single timestamp formatter used by the Logs table *and* the trace
 * view. Replaces the inline `.replace("T", " ").slice(0, 19)` dotted
 * around the old AntD code (Wave 3 audit item P2).
 */
export function formatTimestamp(iso: string | undefined | null): string {
  if (!iso) return "—";
  // Keep the format identical to the old AntD table:
  // "2026-04-08 13:05:47" — no timezone, second-precision.
  const cleaned = iso.replace("T", " ");
  return cleaned.length >= 19 ? cleaned.slice(0, 19) : cleaned;
}

export default function LogTable({
  entries,
  loading,
  onCorrelationClick,
  onTaskClick,
}: Props) {
  const columns = useMemo<ColumnDef<LogEntry>[]>(
    () => [
      {
        accessorKey: "timestamp",
        header: "Time",
        size: 170,
        cell: (info) => (
          <span className={styles.mono}>{formatTimestamp(info.getValue<string>())}</span>
        ),
      },
      {
        accessorKey: "level",
        header: "Level",
        size: 90,
        cell: (info) => {
          const v = info.getValue<string>();
          return <Pill variant={levelToPillVariant(v)}>{v?.toUpperCase() || "—"}</Pill>;
        },
      },
      {
        accessorKey: "event_type",
        header: "Event",
        size: 200,
        cell: (info) => <span className={styles.eventType}>{info.getValue<string>() || "—"}</span>,
      },
      {
        accessorKey: "message",
        header: "Message",
        cell: (info) => <span className={styles.message}>{info.getValue<string>() || "—"}</span>,
      },
      {
        accessorKey: "service",
        header: "Service",
        size: 130,
        cell: (info) => <span className={styles.dim}>{info.getValue<string>() || "—"}</span>,
      },
      {
        accessorKey: "task_id",
        header: "Task",
        size: 100,
        cell: (info) => {
          const v = info.getValue<string>();
          if (!v) return <span className={styles.dim}>—</span>;
          return (
            <button
              type="button"
              className={styles.idLink}
              onClick={(e) => {
                e.stopPropagation();
                onTaskClick(v);
              }}
            >
              {v.slice(0, 8)}…
            </button>
          );
        },
      },
      {
        accessorKey: "correlation_id",
        header: "Trace",
        size: 100,
        cell: (info) => {
          const v = info.getValue<string>();
          if (!v) return <span className={styles.dim}>—</span>;
          return (
            <button
              type="button"
              className={styles.idLink}
              onClick={(e) => {
                e.stopPropagation();
                onCorrelationClick(v);
              }}
            >
              {v.slice(0, 8)}…
            </button>
          );
        },
      },
    ],
    [onCorrelationClick, onTaskClick],
  );

  const handleExport = () => {
    exportToCsv(
      "logs",
      [
        { key: "timestamp", title: "Timestamp" },
        { key: "level", title: "Level" },
        { key: "event_type", title: "Event Type" },
        { key: "message", title: "Message" },
        { key: "service", title: "Service" },
        { key: "task_id", title: "Task ID" },
        { key: "correlation_id", title: "Correlation ID" },
      ],
      entries as unknown as Record<string, unknown>[],
    );
  };

  return (
    <div className={styles.wrapper}>
      <div className={styles.toolbar}>
        <Button
          variant="ghost"
          size="sm"
          onClick={handleExport}
          disabled={entries.length === 0}
        >
          <Download size={12} /> Export CSV
        </Button>
      </div>
      <DataTable<LogEntry>
        data={entries}
        columns={columns}
        getRowId={(row) =>
          `${row.timestamp}-${row.correlation_id ?? ""}-${row.event_type ?? ""}`
        }
        loading={loading}
        virtual
        rowHeight={44}
        maxHeight={560}
        emptyState={
          <EmptyState
            eyebrow="No events"
            title="No events match."
            body="Widen the window or loosen the filters."
          />
        }
      />
    </div>
  );
}
```

- [ ] **Step 2: Create `LogTable.module.css`**

Create `donna-ui/src/pages/Logs/LogTable.module.css`:

```css
.wrapper {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.toolbar {
  display: flex;
  justify-content: flex-end;
}

.mono {
  font-family: var(--font-mono);
  font-size: var(--text-label);
  color: var(--color-text-secondary);
}

.eventType {
  font-family: var(--font-mono);
  font-size: var(--text-label);
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: inline-block;
  max-width: 100%;
}

.message {
  font-family: var(--font-body);
  font-size: var(--text-body);
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: inline-block;
  max-width: 100%;
}

.dim {
  color: var(--color-text-muted);
  font-size: var(--text-label);
}

.idLink {
  background: transparent;
  border: 0;
  padding: 0;
  color: var(--color-accent);
  font-family: var(--font-mono);
  font-size: var(--text-label);
  cursor: pointer;
}
.idLink:hover {
  text-decoration: underline;
}
.idLink:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 2px;
}
```

- [ ] **Step 3: Typecheck**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc -b --noEmit
```

Expected: no errors.

If `exportToCsv` has a different signature than assumed, open `donna-ui/src/utils/csvExport.ts` and adapt the call site. Do not change the helper itself — it is shared.

- [ ] **Step 4: Commit**

```bash
cd /home/feuer/Documents/Projects/donna
git add donna-ui/src/pages/Logs/LogTable.tsx donna-ui/src/pages/Logs/LogTable.module.css
git commit -m "$(cat <<'EOF'
Rewrite Logs LogTable on virtualized DataTable

Columns use Pill for level via levelToPillVariant. Single
formatTimestamp helper replaces scattered inline formatters
(Wave 3 audit items P2 timestamp + P2 level colour).

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Rewrite `TraceView` on the primitive `Drawer`

Replaces AntD `Drawer` + `Timeline` + `Descriptions` + `Tag` with the primitive `Drawer` + a custom `<ol>` timeline + a `<dl>` header + `ScrollArea`-wrapped raw JSON.

**Files:**
- Rewrite: `donna-ui/src/pages/Logs/TraceView.tsx`
- Create: `donna-ui/src/pages/Logs/TraceView.module.css`

- [ ] **Step 1: Rewrite `TraceView.tsx`**

Overwrite `donna-ui/src/pages/Logs/TraceView.tsx` with:

```tsx
import { useEffect, useState } from "react";
import { Drawer } from "../../primitives/Drawer";
import { Pill } from "../../primitives/Pill";
import { ScrollArea } from "../../primitives/ScrollArea";
import { Skeleton } from "../../primitives/Skeleton";
import { fetchTrace, type LogEntry } from "../../api/logs";
import { levelToPillVariant } from "./levelStyles";
import { formatTimestamp } from "./LogTable";
import styles from "./TraceView.module.css";

interface Props {
  correlationId: string | null;
  onClose: () => void;
}

/**
 * Right-side drawer showing every log entry that shares a
 * correlation ID, rendered as a vertical timeline. Falls back to
 * skeletons while loading and an empty hint if the trace is empty.
 */
export default function TraceView({ correlationId, onClose }: Props) {
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [source, setSource] = useState("");

  useEffect(() => {
    if (!correlationId) {
      setEntries([]);
      return;
    }
    setLoading(true);
    fetchTrace(correlationId)
      .then((resp) => {
        setEntries(resp?.entries ?? []);
        setSource(resp?.source ?? "");
      })
      .catch(() => setEntries([]))
      .finally(() => setLoading(false));
  }, [correlationId]);

  const totalDurationMs =
    entries.length >= 2
      ? new Date(entries[entries.length - 1].timestamp).getTime() -
        new Date(entries[0].timestamp).getTime()
      : 0;

  return (
    <Drawer
      open={!!correlationId}
      onOpenChange={(open) => !open && onClose()}
      title={correlationId ? `Trace · ${correlationId.slice(0, 12)}…` : "Trace"}
    >
      <dl className={styles.summary}>
        <div className={styles.summaryItem}>
          <dt>Correlation</dt>
          <dd className={styles.mono}>{correlationId ?? "—"}</dd>
        </div>
        <div className={styles.summaryItem}>
          <dt>Events</dt>
          <dd>{entries.length}</dd>
        </div>
        <div className={styles.summaryItem}>
          <dt>Duration</dt>
          <dd>{totalDurationMs > 0 ? `${totalDurationMs} ms` : "—"}</dd>
        </div>
        <div className={styles.summaryItem}>
          <dt>Source</dt>
          <dd>
            <Pill variant="muted">{source || "—"}</Pill>
          </dd>
        </div>
      </dl>

      {loading ? (
        <div className={styles.loading}>
          <Skeleton height={14} />
          <Skeleton height={14} />
          <Skeleton height={14} />
        </div>
      ) : entries.length === 0 ? (
        <div className={styles.emptyHint}>No events recorded for this trace.</div>
      ) : (
        <ol className={styles.timeline}>
          {entries.map((entry, idx) => (
            <li key={`${entry.timestamp}-${idx}`} className={styles.timelineItem}>
              <span className={styles.timelineDot} aria-hidden="true" />
              <div className={styles.timelineBody}>
                <div className={styles.timelineHeader}>
                  <Pill variant={levelToPillVariant(entry.level)}>
                    {entry.level?.toUpperCase() ?? "—"}
                  </Pill>
                  <span className={styles.eventName}>{entry.event_type}</span>
                  {entry.service && <span className={styles.dim}>{entry.service}</span>}
                </div>
                <div className={styles.message}>{entry.message || "—"}</div>
                <div className={styles.metaRow}>
                  <span className={styles.mono}>{formatTimestamp(entry.timestamp)}</span>
                  {entry.duration_ms != null && (
                    <span className={styles.dim}>{entry.duration_ms} ms</span>
                  )}
                  {entry.cost_usd != null && (
                    <span className={styles.dim}>${entry.cost_usd.toFixed(4)}</span>
                  )}
                </div>
                {entry.extra && Object.keys(entry.extra).length > 0 && (
                  <ScrollArea className={styles.extra} style={{ maxHeight: 200 }}>
                    <pre className={styles.pre}>
                      {JSON.stringify(entry.extra, null, 2)}
                    </pre>
                  </ScrollArea>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </Drawer>
  );
}
```

- [ ] **Step 2: Create `TraceView.module.css`**

Create `donna-ui/src/pages/Logs/TraceView.module.css`:

```css
.summary {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--space-3);
  margin: 0 0 var(--space-4) 0;
  padding-bottom: var(--space-4);
  border-bottom: 1px solid var(--color-border);
}

.summaryItem {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 0;
}
.summaryItem dt {
  font-size: var(--text-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--color-text-muted);
}
.summaryItem dd {
  margin: 0;
  color: var(--color-text);
  font-size: var(--text-body);
}

.mono {
  font-family: var(--font-mono);
  font-size: var(--text-label);
  color: var(--color-text-secondary);
  overflow-wrap: anywhere;
}

.loading {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.emptyHint {
  color: var(--color-text-dim);
  font-size: var(--text-label);
  font-style: italic;
  padding: var(--space-4) 0;
  text-align: center;
}

.timeline {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.timelineItem {
  position: relative;
  padding-left: var(--space-4);
  border-left: 1px solid var(--color-border);
}

.timelineDot {
  position: absolute;
  left: -5px;
  top: 6px;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--color-accent);
  border: 2px solid var(--color-bg);
}

.timelineBody {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.timelineHeader {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.eventName {
  font-family: var(--font-mono);
  font-size: var(--text-label);
  color: var(--color-text);
}

.dim {
  color: var(--color-text-muted);
  font-size: var(--text-label);
}

.message {
  font-size: var(--text-body);
  color: var(--color-text);
  line-height: var(--leading-normal);
}

.metaRow {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.extra {
  background: var(--color-inset);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-control);
  padding: var(--space-2);
}

.pre {
  margin: 0;
  font-family: var(--font-mono);
  font-size: var(--text-label);
  color: var(--color-text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
}
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
git add donna-ui/src/pages/Logs/TraceView.tsx donna-ui/src/pages/Logs/TraceView.module.css
git commit -m "$(cat <<'EOF'
Rewrite Logs TraceView on primitive Drawer

Replaces AntD Drawer+Timeline+Descriptions+Tag with a custom ol
timeline inside the primitive Drawer. Uses formatTimestamp and
levelToPillVariant helpers so the trace view and main table stay
visually consistent.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Rewrite `Logs/index.tsx` composition

Glues every Wave 3 piece together. Replaces the nested `<Layout>` + `<Sider>` + `<Content>` with a CSS Grid shell, owns filter state and preset persistence, wires server-side pagination.

**Files:**
- Rewrite: `donna-ui/src/pages/Logs/index.tsx`
- Create: `donna-ui/src/pages/Logs/Logs.module.css`

- [ ] **Step 1: Rewrite `index.tsx`**

Overwrite `donna-ui/src/pages/Logs/index.tsx` with:

```tsx
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Button } from "../../primitives/Button";
import { PageHeader } from "../../primitives/PageHeader";
import { Select, SelectItem } from "../../primitives/Select";
import { fetchLogs, type LogEntry, type LogFilters } from "../../api/logs";
import EventTypeTree from "./EventTypeTree";
import { FilterBar, type FilterPreset } from "./FilterBar";
import type { DateRangeValue } from "./DateRangePicker";
import LogTable from "./LogTable";
import TraceView from "./TraceView";
import { SavePresetDialog } from "./SavePresetDialog";
import type { LevelFilterValue } from "./levelStyles";
import styles from "./Logs.module.css";

const PRESETS_KEY = "donna-log-presets";
const PAGE_SIZE_OPTIONS = ["25", "50", "100", "250"] as const;

function loadPresets(): FilterPreset[] {
  try {
    const raw = localStorage.getItem(PRESETS_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function savePresets(presets: FilterPreset[]): void {
  localStorage.setItem(PRESETS_KEY, JSON.stringify(presets));
}

export default function Logs() {
  // Data state
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [source, setSource] = useState("");

  // Filter state
  const [selectedEventTypes, setSelectedEventTypes] = useState<string[]>([]);
  const [level, setLevel] = useState<LevelFilterValue>("");
  const [search, setSearch] = useState("");
  const [dateRange, setDateRange] = useState<DateRangeValue>({ start: null, end: null });
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);

  // Presets
  const [presets, setPresets] = useState<FilterPreset[]>(loadPresets);
  const [savePresetOpen, setSavePresetOpen] = useState(false);

  // Trace drawer
  const [traceId, setTraceId] = useState<string | null>(null);

  const doFetch = useCallback(async () => {
    setLoading(true);
    try {
      const filters: LogFilters = {
        limit: pageSize,
        offset: (page - 1) * pageSize,
      };
      if (selectedEventTypes.length > 0) filters.event_type = selectedEventTypes.join(",");
      if (level) filters.level = level;
      if (search) filters.search = search;
      if (dateRange.start) filters.start = dateRange.start;
      if (dateRange.end) filters.end = dateRange.end;

      const resp = await fetchLogs(filters);
      setEntries(Array.isArray(resp?.entries) ? resp.entries : []);
      setTotal(typeof resp?.total === "number" ? resp.total : 0);
      setSource(resp?.source ?? "");
    } catch {
      setEntries([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [selectedEventTypes, level, search, dateRange, page, pageSize]);

  useEffect(() => {
    doFetch();
  }, [doFetch]);

  const handleLoadPreset = useCallback(
    (name: string) => {
      const preset = presets.find((p) => p.name === name);
      if (!preset) return;
      setSelectedEventTypes(preset.eventTypes);
      setLevel((preset.level as LevelFilterValue) || "");
      setSearch(preset.search);
      setPage(1);
      toast.success(`Preset "${name}" loaded`);
    },
    [presets],
  );

  const handleDeletePreset = useCallback(
    (name: string) => {
      const next = presets.filter((p) => p.name !== name);
      setPresets(next);
      savePresets(next);
      toast.success(`Preset "${name}" deleted`);
    },
    [presets],
  );

  const handleSavePreset = useCallback(
    (name: string) => {
      const newPreset: FilterPreset = {
        name,
        eventTypes: selectedEventTypes,
        level,
        search,
      };
      const next = [...presets.filter((p) => p.name !== name), newPreset];
      setPresets(next);
      savePresets(next);
      toast.success(`Preset "${name}" saved`);
    },
    [presets, selectedEventTypes, level, search],
  );

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const metaLine = useMemo(() => {
    if (total === 0) return "No events in range";
    const start = (page - 1) * pageSize + 1;
    const end = Math.min(page * pageSize, total);
    return `Showing ${start}–${end} of ${total}`;
  }, [total, page, pageSize]);

  return (
    <div className={styles.root}>
      <aside className={styles.sidebar} aria-label="Event type filter">
        <div className={styles.sidebarTitle}>Event Types</div>
        <EventTypeTree selected={selectedEventTypes} onChange={setSelectedEventTypes} />
      </aside>

      <section className={styles.main}>
        <PageHeader
          eyebrow="Observability"
          title="Logs"
          meta={metaLine}
        />

        <FilterBar
          search={search}
          onSearchChange={(v) => {
            setSearch(v);
            setPage(1);
          }}
          level={level}
          onLevelChange={(v) => {
            setLevel(v);
            setPage(1);
          }}
          dateRange={dateRange}
          onDateRangeChange={(v) => {
            setDateRange(v);
            setPage(1);
          }}
          source={source}
          presets={presets}
          onLoadPreset={handleLoadPreset}
          onDeletePreset={handleDeletePreset}
          onOpenSavePreset={() => setSavePresetOpen(true)}
          onRefresh={doFetch}
          refreshing={loading}
        />

        <LogTable
          entries={entries}
          loading={loading}
          onCorrelationClick={setTraceId}
          onTaskClick={(id) => window.open(`/tasks/${id}`, "_blank")}
        />

        <nav className={styles.pagination} aria-label="Logs pagination">
          <div className={styles.pageSizeGroup}>
            <span className={styles.pageSizeLabel}>Rows per page</span>
            <Select
              value={String(pageSize)}
              onValueChange={(v) => {
                setPageSize(Number(v));
                setPage(1);
              }}
              aria-label="Rows per page"
            >
              {PAGE_SIZE_OPTIONS.map((opt) => (
                <SelectItem key={opt} value={opt}>
                  {opt}
                </SelectItem>
              ))}
            </Select>
          </div>
          <div className={styles.pageControls}>
            <span className={styles.pageMeta}>
              Page {page} / {totalPages}
            </span>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1 || loading}
            >
              Prev
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages || loading}
            >
              Next
            </Button>
          </div>
        </nav>

        <TraceView correlationId={traceId} onClose={() => setTraceId(null)} />
        <SavePresetDialog
          open={savePresetOpen}
          onOpenChange={setSavePresetOpen}
          onSave={handleSavePreset}
        />
      </section>
    </div>
  );
}
```

- [ ] **Step 2: Create `Logs.module.css`**

Create `donna-ui/src/pages/Logs/Logs.module.css`:

```css
.root {
  display: grid;
  grid-template-columns: 260px 1fr;
  gap: var(--space-4);
  min-height: 100%;
  font-family: var(--font-body);
  color: var(--color-text);
}

.sidebar {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-card);
  padding: var(--space-2);
  align-self: start;
  position: sticky;
  top: var(--space-4);
  max-height: calc(100vh - var(--space-6));
  overflow-y: auto;
}

.sidebarTitle {
  font-size: var(--text-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--color-text-muted);
  padding: var(--space-2) var(--space-3) 0;
}

.main {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  min-width: 0; /* so the DataTable can shrink in a grid cell */
}

.pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding-top: var(--space-3);
}

.pageSizeGroup {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

.pageSizeLabel {
  font-size: var(--text-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--color-text-muted);
}

.pageControls {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

.pageMeta {
  font-size: var(--text-label);
  color: var(--color-text-muted);
  margin-right: var(--space-2);
}

@media (max-width: 900px) {
  .root {
    grid-template-columns: 1fr;
  }
  .sidebar {
    position: static;
    max-height: 220px;
  }
}
```

- [ ] **Step 3: Typecheck**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc -b --noEmit
```

Expected: no errors.

- [ ] **Step 4: Verify no AntD imports remain in `src/pages/Logs/`**

```bash
cd /home/feuer/Documents/Projects/donna
grep -rn "from \"antd\"\|@ant-design\|darkTheme" donna-ui/src/pages/Logs/
```

Expected: no output. Every file under `donna-ui/src/pages/Logs/` should be antd-free and should not touch `theme/darkTheme.ts`.

If any match is reported, fix it in the offending file (it's likely a leftover `exportToCsv`-related import or a stale `LEVEL_COLORS` reference) and re-run the grep before continuing.

- [ ] **Step 5: Run the dev server manually**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npm run dev
```

Visit `http://localhost:5173/logs`. Expected:

- [ ] The page renders without console errors (backend may not be running — the page should show an empty list or loading state, not crash).
- [ ] `PageHeader` shows "Observability / Logs / Showing … of 0" (or similar).
- [ ] Filter bar shows: search input, level Segmented (All / Debug / Info / Warn / Error / Critical), DateRangePicker (From / To), preset Select, Save button, Refresh button.
- [ ] Left sidebar shows "Event Types" title plus the tree (or a "No event types registered" hint if the API is unreachable).
- [ ] Click the Save preset button → a Dialog opens, captures a name, and shows a toast on save.
- [ ] Click a Trace ID in any row (if backend is up) → TraceView drawer slides in from the right, closes on Esc.
- [ ] Resize the window below 900 px → the sidebar stacks above the table.

Stop the dev server.

- [ ] **Step 6: Commit**

```bash
cd /home/feuer/Documents/Projects/donna
git add donna-ui/src/pages/Logs/index.tsx donna-ui/src/pages/Logs/Logs.module.css
git commit -m "$(cat <<'EOF'
Migrate Logs page off AntD onto Wave 1 primitives

Rebuilds the page as a CSS Grid composition: EventTypeTree sidebar +
PageHeader + FilterBar + virtualized LogTable + pagination + TraceView
drawer + SavePresetDialog. Preserves /admin/logs API contract and the
donna-log-presets localStorage format. Sidebar collapses above the
table below 900px viewport width, resolving Wave 3 P0 audit item
(nested AntD Sider responsive failure).

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Expand the Logs Playwright smoke test

The current test just asserts the root is non-empty. Tighten it to exercise the new page structure end-to-end with the mocked API.

**Files:**
- Modify: `donna-ui/tests/e2e/smoke/logs.spec.ts`

- [ ] **Step 1: Rewrite `logs.spec.ts`**

Overwrite `donna-ui/tests/e2e/smoke/logs.spec.ts` with:

```ts
import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Logs smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("renders page header, filter bar, sidebar, and pagination", async ({ page }) => {
    await page.goto("/logs");

    // PageHeader is the new primitive composition.
    await expect(page.getByRole("heading", { name: "Logs" })).toBeVisible();

    // Filter bar controls (audit item P1: aria-labels).
    await expect(page.getByLabel("Search logs")).toBeVisible();
    await expect(page.getByLabel("Log level filter")).toBeVisible();
    await expect(page.getByLabel("Start time")).toBeVisible();
    await expect(page.getByLabel("End time")).toBeVisible();

    // Sidebar label from the new CSS Grid shell.
    await expect(page.getByLabel("Event type filter")).toBeVisible();

    // Pagination nav.
    await expect(page.getByLabel("Logs pagination")).toBeVisible();
    await expect(page.getByRole("button", { name: "Prev" })).toBeDisabled();
  });

  test("save preset dialog opens and closes", async ({ page }) => {
    await page.goto("/logs");

    await page.getByRole("button", { name: /save current filters/i }).click();
    await expect(page.getByRole("heading", { name: "Save filter preset" })).toBeVisible();

    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByRole("heading", { name: "Save filter preset" })).not.toBeVisible();
  });
});
```

- [ ] **Step 2: Check the mocked API helper is sufficient**

Open `donna-ui/tests/e2e/helpers.ts` and confirm that `/admin/logs` returns `[]`. Because the new `Logs` page code defensively coerces `resp?.entries` to `[]` if it is not an array, the page renders without crashing even when the mock response is not an object. Do **not** edit the helper — this test run relies on the defensive path you built in Task 10.

If the test fails because the defensive path is missing, the fix is to revisit `index.tsx`'s `doFetch`, not the helper.

- [ ] **Step 3: Run the logs spec only**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx playwright test tests/e2e/smoke/logs.spec.ts
```

Expected: both tests pass. If any selector is not found, inspect the page output (add `await page.screenshot({ path: "debug.png" })` temporarily) and adjust the selector in the test — not the page.

- [ ] **Step 4: Run the full suite**

```bash
npm run test:e2e
```

Expected: every smoke test still passes. Dashboard, tasks, agents, configs, prompts, shadow, preferences, dev-primitives, and app-shell should be unaffected.

- [ ] **Step 5: Commit**

```bash
cd /home/feuer/Documents/Projects/donna
git add donna-ui/tests/e2e/smoke/logs.spec.ts
git commit -m "$(cat <<'EOF'
Expand Logs smoke test to cover primitives-based page

Asserts PageHeader, filter bar aria labels, sidebar landmark,
pagination nav, and the Save preset dialog open/close flow.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: Verification pass

Final walkthrough to confirm the wave is done, audit items are resolved, and nothing drifted.

**Files:** (no edits; verification only — unless defects are found, in which case fix-then-retest-then-commit)

- [ ] **Step 1: Typecheck the whole UI package**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc -b --noEmit
```

Expected: no errors.

- [ ] **Step 2: Lint (if configured)**

```bash
npm run lint 2>&1 | tail -40
```

Expected: no errors. If `lint` is not a defined script, skip this step without commentary.

- [ ] **Step 3: Full Playwright suite**

```bash
npm run test:e2e
```

Expected: all tests pass.

- [ ] **Step 4: Manual verification — smoke the page against a running backend if available**

Start the backend (`docker compose up -d` from the repo root if the user has the stack running locally) and then:

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npm run dev
```

Walk through the Logs page:

- [ ] Load `/logs`. Entries populate. Timestamps are monospaced, levels are Pills.
- [ ] Click the "Info" segment in the level filter — the list refetches and only shows INFO rows.
- [ ] Type into the search box — list refetches as you type (debounce is OK if present, but not required in this wave).
- [ ] Pick a From / To datetime — list refetches with ISO range.
- [ ] Expand and collapse an event-type category in the sidebar.
- [ ] Check a few event-type leaves — list refetches with the joined filter string.
- [ ] Click "All" / "Clear" in the sidebar — state resets.
- [ ] Click a Trace ID — the drawer opens, the timeline populates, Esc closes it.
- [ ] Click a Task ID — opens `/tasks/<id>` in a new tab.
- [ ] "Rows per page" select → pick 100 → list refetches with `limit=100`.
- [ ] "Next" pagination → `offset` increments, metadata updates.
- [ ] "Save preset" → dialog opens, type a name, Enter submits, toast appears, preset is in the "Load preset…" select and persists after reload.
- [ ] Switch theme (⌘.) → gold becomes coral; Pills, level filter, accent borders, and trace timeline dot update.
- [ ] Resize to phone width → sidebar stacks on top, table stays scrollable.

Stop the dev server.

- [ ] **Step 5: Confirm audit issues are resolved**

Walk through each audit item Wave 3 claims to fix:

- **[P0] Nested `<Sider>` responsive failure:**
  ```bash
  grep -n "Sider\|AntdLayout\|from \"antd\"" donna-ui/src/pages/Logs/*.tsx
  ```
  Expected: no output. The whole Logs directory is antd-free.
- **[P1] Logs filter form lacks ARIA labels:**
  ```bash
  grep -c "aria-label" donna-ui/src/pages/Logs/FilterBar.tsx donna-ui/src/pages/Logs/DateRangePicker.tsx
  ```
  Expected: at least 6 `aria-label` attributes across the two files (Search logs, Log level filter, Start time, End time, Load saved filter preset, Refresh log list, etc.).
- **[P2] Timestamp column format inconsistency:**
  ```bash
  grep -rn "formatTimestamp\|\.replace(\"T\"" donna-ui/src/pages/Logs/
  ```
  Expected: `formatTimestamp` referenced in `LogTable.tsx` and `TraceView.tsx`; no other `.replace("T"` sites anywhere in `pages/Logs/`.
- **[P2] Level tag colors scattered inline:**
  ```bash
  grep -rn "LEVEL_COLORS\|levelToPillVariant" donna-ui/src/pages/Logs/
  ```
  Expected: zero hits for `LEVEL_COLORS`; `levelToPillVariant` referenced in `LogTable.tsx`, `TraceView.tsx`, and `levelStyles.ts`.

- [ ] **Step 6: Verify the wave-3-logs branch is ready**

```bash
cd /home/feuer/Documents/Projects/donna
git status
git log main..HEAD --oneline
```

Expected:
- `git status` reports a clean working tree.
- `git log main..HEAD --oneline` shows **11 commits** (one per Task 1–11) on top of `main`.
- Every commit message starts with a capital verb and mentions Logs or DataTable.

If anything is dirty, diagnose before committing a cleanup.

- [ ] **Step 7: Do not merge**

Do NOT open a pull request or merge. This plan ends on the branch. The user will review the branch in their own workflow and merge or ask for follow-ups.

---

## Self-Review

**Spec coverage check against `docs/superpowers/specs/2026-04-08-donna-ui-redesign-design.md` §4 Wave 5 (line 371):**

- "Same pattern as Tasks: `<PageHeader>` + filter bar + `<DataTable>` + `<Drawer>`." → Task 10 (`PageHeader`), Task 6 (`FilterBar`), Task 8 (virtualized `DataTable`), Task 9 (`Drawer`). ✓
- "Virtualized rows via `@tanstack/react-virtual`." → Task 2 adds the virtualization path to `DataTable`; Task 8 opts the Logs table into it. ✓
- "Log detail drawer shows structured fields as a `<dl>` + raw JSON in `<pre>` inside `<ScrollArea>`." → Task 9 renders `<dl className={styles.summary}>` for summary and `<ScrollArea>` around `<pre>` for the `extra` payload. ✓
- "Level filter as pill group." → Task 6 uses the `Segmented` primitive which renders as a pill-shaped group per `Segmented.module.css`. This is the closest existing primitive to a pill group. If the user wants a literal `Pill`-styled radio group, the fix is localised to `FilterBar.tsx` — flag in review. ⚠ (minor)
- **Audit P0 "Logs page responsive failure"** → resolved in Task 10. ✓
- **Audit P1 "Logs filter form lacks ARIA labels"** → resolved in Task 6. ✓
- **Audit P2 "Timestamp column format inconsistency"** → resolved in Task 8 (`formatTimestamp` helper). ✓
- **Audit P2 "Level tag colors scattered inline"** → resolved in Task 1 (`levelStyles.ts`). ✓

**Placeholder scan:** searched the plan for "TBD", "TODO", "implement later", "fill in", "add appropriate", "handle edge cases", "similar to Task" — none present. Every step contains the exact code to paste.

**Type consistency check:**

- `DataTableProps<T>` additions: `virtual?: boolean; rowHeight?: number; maxHeight?: number` — consumed exactly in `LogTable.tsx` (`virtual rowHeight={44} maxHeight={560}`) and `DevPrimitives/index.tsx` (`virtual rowHeight={44} maxHeight={400}`). ✓
- `DateRangeValue` shape `{ start: string | null; end: string | null }` — created in `DateRangePicker.tsx`, consumed in `FilterBar.tsx` (`dateRange: DateRangeValue`), constructed in `Logs/index.tsx` initial state `{ start: null, end: null }`. ✓
- `FilterPreset` shape `{ name; eventTypes; level; search }` — defined in `FilterBar.tsx`, consumed by `Logs/index.tsx` save handler and the `loadPresets` helper. Byte-identical to the pre-existing `donna-log-presets` localStorage schema. ✓
- `LevelFilterValue` — derived from `LEVEL_OPTIONS as const`, propagates from `levelStyles.ts` → `FilterBar.tsx` prop → `Logs/index.tsx` state. ✓
- `formatTimestamp(iso)` — exported from `LogTable.tsx`, imported by `TraceView.tsx`. Single definition. ✓
- `levelToPillVariant(level)` — exported from `levelStyles.ts`, imported by `LogTable.tsx` and `TraceView.tsx`. Single definition. ✓
- `EventTypeTree` is a default export in both the old file (preserved) and the new file — `Logs/index.tsx` imports it without braces. ✓

**API contract check:** `fetchLogs`, `fetchTrace`, `fetchEventTypes` signatures in `src/api/logs.ts` are not touched. `LogFilters` is still `{ event_type?; level?; search?; start?; end?; limit?; offset?; ... }`. The page sends exactly those query params.

**Defensive-coerce check:** the Logs page in Task 10 defensively handles `resp?.entries` not being an array. This is required because `mockAdminApi` returns `"[]"` for `/admin/logs` (i.e. an array, not an object), so `resp.entries` would be `undefined`. The Playwright test in Task 11 relies on this defensive path; do not remove it in review.

**Files that change-together check:**
- `DataTable.tsx` + `DataTable.module.css` — Task 2. Ship together.
- `EventTypeTree.tsx` + `EventTypeTree.module.css` — Task 4. Ship together.
- `LogTable.tsx` + `LogTable.module.css` — Task 8. Ship together.
- `TraceView.tsx` + `TraceView.module.css` — Task 9. Ship together.
- `Logs/index.tsx` + `Logs.module.css` — Task 10. Ship together, since the new CSS Grid shell is required for the new `index.tsx` layout to render.

**Dependency check:** `@tanstack/react-virtual@^3.13.23`, `@tanstack/react-table@^8.21.3`, `sonner@^2.0.7`, `lucide-react`, and the Radix-backed primitives are all already in `donna-ui/package.json`. No `npm install` step is needed in this plan.

**Scope discipline:**
- No other pages are touched. Dashboard, Tasks, Agents, Configs, Prompts, Shadow, Preferences remain fully AntD.
- `src/components/RefreshButton.tsx` is intentionally left alone; the Logs page replaces its usage with an inline `<Button>` inside `FilterBar`.
- `src/theme/darkTheme.ts` still exists and is still imported by the other AntD pages. Only the Logs page stops importing from it. It will be deleted in Wave 9.
- `src/api/logs.ts` is untouched.
- `mockAdminApi` helper is untouched.

**Known minor rough edges accepted in this wave:**

1. The preset "delete" UX uses a second `Select` rather than an inline delete icon on each preset option. Building a truly composite preset-row (load + delete in the same item) would need either a popover per preset or a custom select item renderer; that's overkill for Wave 3. Fix is trivial in a follow-up if the user doesn't like the two-select approach.
2. Search input is not debounced. The old AntD `<Input.Search>` used `onSearch` (fire on Enter / blur) — the new primitive `<Input>` fires on every keystroke. This means typing sends one API request per character. Acceptable for this wave because the backend is local; can be debounced in a follow-up if it becomes a perf concern.
3. Keyboard navigation on the virtualized DataTable is explicitly disabled. The spec's keyboardNav contract applies only to non-virtual tables. If the user later wants arrow-key nav on logs, the fix is to teach the virtualized tbody branch to scroll-into-view via `virtualizer.scrollToIndex` — outside Wave 3 scope.
4. The `Segmented` level filter shows all six options. The `All` option carries `value: ""` and is explicitly accepted by `LevelFilterValue` via `LEVEL_OPTIONS[0]`. If review wants a separate "clear filters" button instead of an `All` segment, the change is localised to `levelStyles.ts`.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-08-donna-ui-wave-3-logs.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints for review.

Which approach?
