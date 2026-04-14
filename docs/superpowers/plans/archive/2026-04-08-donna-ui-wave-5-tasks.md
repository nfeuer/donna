# Donna UI Redesign — Wave 5 (Tasks Migration) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate `donna-ui/src/pages/Tasks/` off Ant Design onto the Wave 1 primitives. After this plan the Tasks list page and its task-detail surface render entirely on primitives, the duplicated `STATUS_TAG_COLORS` map collapses into a single `taskStatusStyles.ts` module, the free-floating `/tasks/:id` detail route becomes an in-page `<Drawer>` (while still deep-linking from the `/tasks/:id` URL so the Logs page's `window.open('/tasks/${id}', '_blank')` keeps working), every interactive filter control carries an explicit `aria-label` and a reset affordance, and the `/admin/tasks` + `/admin/tasks/{id}` backend contract is preserved byte-for-byte.

**Architecture:**

- The Tasks directory is restructured from "list page + separate detail route" to "list page + in-page drawer". `index.tsx` becomes a thin orchestrator: it owns filter state, paginated row state, and a single `selectedId` value that drives the drawer. `selectedId` is bidirectionally synced with the URL via `react-router-dom`'s `useParams` + `useNavigate` — landing on `/tasks/:id` hydrates the drawer open; closing the drawer navigates back to `/tasks`; clicking a row calls `navigate('/tasks/' + id)`. This lets `window.open('/tasks/${id}', '_blank')` from `src/pages/Logs/LogTable.tsx` continue to deep-link into a pre-opened drawer with zero changes on the Logs side. The standalone `TaskDetail.tsx` page component is deleted.
- A new `taskStatusStyles.ts` is the single source of truth for how a task status renders. It exports `statusToPillVariant(status): PillVariant`, `priorityToPillVariant(priority): PillVariant`, `formatStatusLabel(status): string`, `STATUS_OPTIONS`, `PRIORITY_OPTIONS`, `DOMAIN_OPTIONS`, and a `STATE_ORDER` array for the status timeline. Every call site that previously inlined the `STATUS_TAG_COLORS: Record<string, string>` map imports from this file instead. No other file in `pages/Tasks/` is allowed to literal-map a status or priority to a color — the audit item [P1] "Duplicated `STATUS_TAG_COLORS`" is resolved by construction.
- `TaskFilters.tsx` is rewritten as a primitives-based filter row: `<Input>` for search (debounced 250 ms), `<Select>` for status / domain / priority, a ghost `<Button>` "Reset" that clears all four in one action (audit item [P2] "Task filter form lacks reset button"), and explicit `role="search"` + `aria-label="Task filters"` on the container. Every interactive control gets an `aria-label`. Empty-string Radix Select values are avoided with a shared `ALL_VALUE = "__all__"` sentinel that the page converts back to `undefined` before calling the API.
- `TaskTable.tsx` is rewritten on the `<DataTable>` primitive with TanStack Table column defs. Status and priority render as `<Pill>` primitives keyed off `taskStatusStyles`. Timestamps render with a single shared formatter (`formatTaskTimestamp`). The CSV export button survives and moves into a DataTable toolbar mirroring Logs' pattern (`src/pages/Logs/LogTable.tsx` lines 137–148). `keyboardNav` is enabled so ↑/↓/Enter works (audit item [P1] "Task drawer a11y" partially addressed here; the drawer part lands with Radix Dialog in Task 4).
- `TaskDetailDrawer.tsx` is a new file. It composes the `<Drawer>` primitive (Radix-backed — focus trap + ESC close come free, fully resolving audit item [P1] "Task drawer a11y — no focus trap, no ESC"), fetches the task via the existing `fetchTask(id)` on open, renders a status `<Pill>`, a linear step row for the state timeline (plain `<ol>` with the `STATE_ORDER` array — no AntD `Steps`), a `<dl>` of core fields (replacing AntD `Descriptions`), and three nested `<DataTable>`s for invocations / nudge events / corrections. Subtasks render as a plain `<ul>` with status and priority pills (replacing AntD `Tree`). On close the drawer navigates back to `/tasks`.
- `sonner`'s `toast` replaces any AntD `message.*` calls that creep in during the rewrite — the existing Tasks code does not currently call `message.*`, but the replacement pattern is fixed in the plan as a safety rail: never import from `antd` inside `pages/Tasks/` after Task 5.
- **Explicit non-goal:** the spec (§4 Wave 4 line 363) mentions "New Task flow → `<Dialog>` with React Hook Form + zod". This requires a `POST /admin/tasks` endpoint that does **not** exist today (`src/donna/api/routes/admin_tasks.py` defines only `GET /tasks` and `GET /tasks/{task_id}`). The user's brief says "API contract preserved" — so the new-task creation Dialog is deferred to a follow-up wave that also adds the backend endpoint. This plan does not build the Dialog and does not add the backend route. The decision is documented once here and re-stated in the final verification task.

**Tech Stack:** React 18, TypeScript 5.7, `@tanstack/react-table` 8 (already present), `@radix-ui/react-dialog` (already present via the Drawer primitive), `react-router-dom` 6, CSS Modules. No new runtime dependencies. No new dev dependencies.

**Spec reference:** `docs/superpowers/specs/2026-04-08-donna-ui-redesign-design.md` §4 Wave 4 "Tasks Migration" (lines 357–369). Note: the master spec calls this work **Wave 4**, but the user is executing it as **Wave 5** because the local wave sequence shipped Dashboard as Wave 4. The spec's own Wave 4 checklist applies unchanged to this plan.

**Precondition:**

- Branch off from `main`. At the time of this plan `main` is at `791fb9b Merge pull request #32 from nfeuer/wave-4-dashboard`. Task 1 creates and checks out `wave-5-tasks` from this point.
- `donna-ui/src/primitives/` contains `DataTable`, `Drawer`, `Dialog`, `PageHeader`, `Segmented`, `Pill`, `Input`, `Select`, `SelectItem`, `Button`, `Tooltip`, `Skeleton`, `EmptyState`. (Verified from `src/primitives/index.ts` lines 1–27.)
- `donna-ui/src/primitives/DataTable.tsx` accepts a `keyboardNav` prop and toggles `↑/↓/Enter` row navigation. (Verified from lines 35–36, 100–116.)
- `donna-ui/src/primitives/Drawer.tsx` is Radix-backed with focus trap + ESC already wired. (Verified from lines 22–37.)
- `donna-ui/src/api/tasks.ts` exposes `fetchTasks(filters)` and `fetchTask(id)` returning `TasksResponse` and `TaskDetail` respectively. **Frozen — types and signatures unchanged in this wave.** (Verified from lines 102–119.)
- Backend `src/donna/api/routes/admin_tasks.py` defines only `GET /tasks` and `GET /tasks/{task_id}`. **Unchanged in this wave.** (Verified from lines 18, 119.)
- `donna-ui/src/pages/Logs/LogTable.tsx` line 174 (`window.open('/tasks/${id}', '_blank')`) is the sole external deep-link into the Tasks detail surface. It must keep working.
- `donna-ui/src/pages/DevPrimitives/index.tsx` remains the primitives gallery and is **not modified** by this wave.
- Working directory: `/home/feuer/Documents/Projects/donna`. The Vite app lives at `donna-ui/`. All build / lint / test commands run from `donna-ui/`.

---

## Audit issues fixed in this wave

The spec (§4 Wave 4) lists four audit items this wave resolves. Each is addressed below and verified in Task 7.

- **[P1] Duplicated `STATUS_TAG_COLORS` (TaskTable + TaskDetail).** Resolved by deleting both copies and routing every status rendering through `taskStatusStyles.ts::statusToPillVariant`. Verified by a grep in Task 7 (`grep -rn "STATUS_TAG_COLORS" donna-ui/src/pages/Tasks/` must return exactly zero matches; the export in `taskStatusStyles.ts` is named `statusToPillVariant`, not `STATUS_TAG_COLORS`).
- **[P1] Task drawer a11y — no focus trap, no ESC.** Resolved by adopting the `<Drawer>` primitive, which composes Radix Dialog (`donna-ui/src/primitives/Drawer.tsx` line 22: `<RadixDialog.Root>`). Radix Dialog ships focus trap + ESC close + scroll lock out of the box. Verified by the Playwright smoke tests added in Task 6 (ESC closes the drawer; focus returns to the triggering row).
- **[P2] Task filter form lacks reset button.** Resolved by adding a ghost `<Button>` labelled "Reset" to `TaskFilters.tsx` that calls `onReset()`; the parent page clears `status`, `domain`, `priority`, `search` and resets `page` to 1. Verified by a Playwright assertion in Task 6.
- **[P2] AntD `message.*` toasts → Sonner.** The current Tasks code does not call `message.*`, so the replacement is preventive: the final grep in Task 7 enforces that no `from "antd"` imports remain in the `pages/Tasks/` subtree.

---

## File Structure Overview

### Created in Wave 5

```
donna-ui/src/pages/Tasks/
├── taskStatusStyles.ts                  (CREATED — single source of truth)
├── TaskFilters.module.css               (CREATED)
├── TaskTable.module.css                 (CREATED)
├── TaskDetailDrawer.tsx                 (CREATED — Drawer-based detail)
├── TaskDetailDrawer.module.css          (CREATED)
└── Tasks.module.css                     (CREATED — page shell layout)
```

### Rewritten in Wave 5

```
donna-ui/src/pages/Tasks/
├── index.tsx                            (REWRITTEN — PageHeader + primitives + drawer)
├── TaskFilters.tsx                      (REWRITTEN — primitives, aria-labels, reset)
└── TaskTable.tsx                        (REWRITTEN — DataTable columns + CSV toolbar)

donna-ui/src/App.tsx                     (MODIFIED — /tasks/:id? now renders TasksPage)
```

### Deleted in Wave 5

```
donna-ui/src/pages/Tasks/
└── TaskDetail.tsx                       (DELETED — replaced by TaskDetailDrawer)
```

### Expanded in Wave 5

```
donna-ui/tests/e2e/smoke/
└── tasks.spec.ts                        (EXPANDED — primitive + drawer assertions)
```

### Untouched in Wave 5 (explicit non-goals)

- `donna-ui/src/api/tasks.ts` — frozen. Types and endpoint paths unchanged.
- `src/donna/api/routes/admin_tasks.py` — frozen. No `POST`, no `PATCH`, no new query parameters.
- `donna-ui/src/components/RefreshButton.tsx` — still AntD. Imported by the rewritten `Tasks/index.tsx` via the same path Dashboard uses. Migrates in Wave 9 per the Wave 4 Dashboard plan.
- `donna-ui/src/theme/darkTheme.ts` — untouched in this wave. Still consumed by Agents, Shadow, Preferences, Configs, Prompts. Deleted in Wave 9.
- `donna-ui/src/pages/Logs/LogTable.tsx` — untouched. Its `window.open('/tasks/${id}')` call site is the motivation for the `/tasks/:id?` optional-param routing.
- `donna-ui/package.json` — no dependency changes. `antd` stays installed until Wave 9.

### Principles

- Each `.tsx` stays under ~180 lines; colocates its `.module.css`.
- No file in `pages/Tasks/` imports from `antd` or `@ant-design/icons` after Task 5.
- No inline hex literals (`#RRGGBB`, `#RGB`) anywhere in `pages/Tasks/` after Task 7.
- No duplicated status/priority color maps — all status rendering flows through `taskStatusStyles.ts`.
- Every filter control carries an explicit `aria-label`.
- The `fetchTasks` and `fetchTask` call sites keep their exact signatures.

---

## Execution Groups

The 7 tasks below split into 4 phases. Subagent dispatchers should respect the phase boundaries — tasks within a phase may run in parallel on separate subagents, but a new phase must not start until the previous phase's subagents have all committed.

| Phase | Tasks | Parallelism |
|---|---|---|
| A. Foundation | Task 1 | 1 subagent |
| B. Components | Tasks 2, 3, 4 | 3 subagents in parallel |
| C. Integration | Task 5 | 1 subagent |
| D. Verification | Tasks 6, 7 | Task 6 first, then Task 7 |

Tasks 2/3/4 depend only on `taskStatusStyles.ts` (created in Task 1) and the read-only primitive surface. They touch disjoint files and can run without coordination. Task 5 imports all three and is the first point where the rewritten page actually compiles end-to-end.

---

## Phase A · Foundation

### Task 1: Branch + `taskStatusStyles.ts` module

**Files:**
- Create: `donna-ui/src/pages/Tasks/taskStatusStyles.ts`

- [ ] **Step 1: Create and check out the feature branch**

Run from `/home/feuer/Documents/Projects/donna`:

```bash
git checkout main
git pull --ff-only origin main
git checkout -b wave-5-tasks
```

Expected: `Switched to a new branch 'wave-5-tasks'`. `git status` clean.

- [ ] **Step 2: Create `taskStatusStyles.ts`**

Create `donna-ui/src/pages/Tasks/taskStatusStyles.ts` with this exact content:

```ts
import type { PillVariant } from "../../primitives/Pill";

/**
 * Single source of truth for how task status/priority/domain render.
 * Replaces the two duplicated `STATUS_TAG_COLORS` maps that used to live
 * inline inside TaskTable.tsx and TaskDetail.tsx (audit item P1).
 *
 * Semantic colour policy (see spec §5): a green pill means "this is
 * actually done", a red pill means "this is actually blocked/cancelled".
 * Never decorative. Scheduled / in-progress / waiting all route to the
 * theme accent because they share the same meaning: "Donna is on it".
 */
export function statusToPillVariant(status: string | undefined): PillVariant {
  switch (status) {
    case "done":
      return "success";
    case "blocked":
    case "cancelled":
      return "error";
    case "waiting_input":
      return "warning";
    case "scheduled":
    case "in_progress":
      return "accent";
    case "backlog":
    default:
      return "muted";
  }
}

/**
 * P1/P2 are urgent → error variant. P3 is warning. P4/P5 are muted.
 * Matches the dashboard convention that "critical" means red, not
 * "red means critical", so P1 and P2 share the same rendering.
 */
export function priorityToPillVariant(priority: number | undefined): PillVariant {
  if (priority === 1 || priority === 2) return "error";
  if (priority === 3) return "warning";
  return "muted";
}

/** Human-facing status label: snake_case → Space Case. */
export function formatStatusLabel(status: string | undefined): string {
  if (!status) return "—";
  return status.replace(/_/g, " ");
}

/** Timestamp formatter shared by the table and the drawer. */
export function formatTaskTimestamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  const cleaned = iso.replace("T", " ");
  return cleaned.length >= 16 ? cleaned.slice(0, 16) : cleaned;
}

/**
 * Sentinel "any value" for Radix Select. Radix throws if a SelectItem
 * has value="", so the filter bar uses this sentinel and the page
 * converts it to `undefined` before calling fetchTasks().
 */
export const ALL_VALUE = "__all__";

export const STATUS_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: ALL_VALUE, label: "All statuses" },
  { value: "backlog", label: "Backlog" },
  { value: "scheduled", label: "Scheduled" },
  { value: "in_progress", label: "In progress" },
  { value: "blocked", label: "Blocked" },
  { value: "waiting_input", label: "Waiting input" },
  { value: "done", label: "Done" },
  { value: "cancelled", label: "Cancelled" },
] as const;

export const DOMAIN_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: ALL_VALUE, label: "All domains" },
  { value: "personal", label: "Personal" },
  { value: "work", label: "Work" },
  { value: "family", label: "Family" },
] as const;

export const PRIORITY_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: ALL_VALUE, label: "All priorities" },
  { value: "1", label: "P1 — Critical" },
  { value: "2", label: "P2 — High" },
  { value: "3", label: "P3 — Medium" },
  { value: "4", label: "P4 — Low" },
  { value: "5", label: "P5 — Minimal" },
] as const;

/**
 * Linear state timeline used by the detail drawer. Matches the old
 * AntD `Steps` ordering. Blocked and waiting_input map to the
 * in_progress step visually; cancelled renders as "abandoned" outside
 * the timeline.
 */
export const STATE_ORDER: ReadonlyArray<string> = [
  "backlog",
  "scheduled",
  "in_progress",
  "done",
] as const;

export function getStateStepIndex(status: string | undefined): number {
  if (!status) return 0;
  const idx = STATE_ORDER.indexOf(status);
  if (idx >= 0) return idx;
  if (status === "blocked" || status === "waiting_input") return 2;
  if (status === "cancelled") return -1;
  return 0;
}
```

- [ ] **Step 3: Typecheck the new module**

Run from `donna-ui/`:

```bash
npx tsc -b
```

Expected: exit code 0, no errors. (`taskStatusStyles.ts` has no runtime dependencies other than the `PillVariant` type import.)

- [ ] **Step 4: Commit**

```bash
git add donna-ui/src/pages/Tasks/taskStatusStyles.ts
git commit -m "Add taskStatusStyles module as single source for Tasks status tokens"
```

---

## Phase B · Components (parallel)

Tasks 2, 3, 4 all depend on Task 1's `taskStatusStyles.ts` and only that. They touch disjoint files. Dispatch as 3 subagents in parallel. Each subagent must re-run `npx tsc -b` before committing — the existing `TaskTable.tsx` and `TaskFilters.tsx` still reference AntD during Phase B, so `tsc -b` at the root will only be green for the subtree the subagent is working on if other tasks have not yet committed their own rewrites. **Expected behaviour during Phase B:** `npx tsc -b` may still succeed because each rewrite file is self-contained and the old files are not imported from each other. If `tsc -b` reports errors that reference *another* Phase B file, stop and report — do not edit that file to fix the error.

### Task 2: Rewrite `TaskFilters.tsx` on primitives

**Files:**
- Modify: `donna-ui/src/pages/Tasks/TaskFilters.tsx` (full rewrite)
- Create: `donna-ui/src/pages/Tasks/TaskFilters.module.css`

- [ ] **Step 1: Replace `TaskFilters.tsx` with the primitives-based version**

Overwrite `donna-ui/src/pages/Tasks/TaskFilters.tsx` with this exact content:

```tsx
import { RotateCcw } from "lucide-react";
import { Button } from "../../primitives/Button";
import { Input } from "../../primitives/Input";
import { Select, SelectItem } from "../../primitives/Select";
import {
  ALL_VALUE,
  DOMAIN_OPTIONS,
  PRIORITY_OPTIONS,
  STATUS_OPTIONS,
} from "./taskStatusStyles";
import styles from "./TaskFilters.module.css";

interface Props {
  status: string;
  domain: string;
  priority: string;
  search: string;
  onStatusChange: (v: string) => void;
  onDomainChange: (v: string) => void;
  onPriorityChange: (v: string) => void;
  onSearchChange: (v: string) => void;
  onReset: () => void;
}

/**
 * Primitive filter row for the Tasks list. Every interactive control
 * has an explicit aria-label (audit item P1 "filter form lacks ARIA
 * labels" applied preventively to Tasks). Includes a Reset button
 * (audit item P2 "Task filter form lacks reset button").
 */
export default function TaskFilters({
  status,
  domain,
  priority,
  search,
  onStatusChange,
  onDomainChange,
  onPriorityChange,
  onSearchChange,
  onReset,
}: Props) {
  const isDirty =
    status !== ALL_VALUE ||
    domain !== ALL_VALUE ||
    priority !== ALL_VALUE ||
    search.length > 0;

  return (
    <div className={styles.root} role="search" aria-label="Task filters">
      <Input
        type="search"
        className={styles.search}
        placeholder="Search title or description…"
        value={search}
        onChange={(e) => onSearchChange(e.target.value)}
        aria-label="Search tasks"
      />
      <Select
        value={status}
        onValueChange={onStatusChange}
        aria-label="Status filter"
      >
        {STATUS_OPTIONS.map((o) => (
          <SelectItem key={o.value} value={o.value}>
            {o.label}
          </SelectItem>
        ))}
      </Select>
      <Select
        value={domain}
        onValueChange={onDomainChange}
        aria-label="Domain filter"
      >
        {DOMAIN_OPTIONS.map((o) => (
          <SelectItem key={o.value} value={o.value}>
            {o.label}
          </SelectItem>
        ))}
      </Select>
      <Select
        value={priority}
        onValueChange={onPriorityChange}
        aria-label="Priority filter"
      >
        {PRIORITY_OPTIONS.map((o) => (
          <SelectItem key={o.value} value={o.value}>
            {o.label}
          </SelectItem>
        ))}
      </Select>
      <Button
        variant="ghost"
        size="sm"
        onClick={onReset}
        disabled={!isDirty}
        aria-label="Reset all task filters"
      >
        <RotateCcw size={12} /> Reset
      </Button>
    </div>
  );
}
```

- [ ] **Step 2: Create `TaskFilters.module.css`**

Create `donna-ui/src/pages/Tasks/TaskFilters.module.css` with this exact content:

```css
.root {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) 0;
}

.search {
  flex: 1 1 260px;
  min-width: 220px;
  max-width: 360px;
}

@media (max-width: 720px) {
  .root {
    gap: var(--space-1);
  }
  .search {
    flex-basis: 100%;
    max-width: 100%;
  }
}
```

- [ ] **Step 3: Verify `TaskFilters.tsx` typechecks in isolation**

Run from `donna-ui/`:

```bash
npx tsc --noEmit -p tsconfig.json
```

Expected: no errors referencing `TaskFilters.tsx` or `TaskFilters.module.css`. (Errors in other `Tasks/` files are expected during Phase B and should not be touched here.)

- [ ] **Step 4: Commit**

```bash
git add donna-ui/src/pages/Tasks/TaskFilters.tsx donna-ui/src/pages/Tasks/TaskFilters.module.css
git commit -m "Rewrite TaskFilters on primitives with aria-labels and reset"
```

### Task 3: Rewrite `TaskTable.tsx` on `<DataTable>`

**Files:**
- Modify: `donna-ui/src/pages/Tasks/TaskTable.tsx` (full rewrite)
- Create: `donna-ui/src/pages/Tasks/TaskTable.module.css`

- [ ] **Step 1: Replace `TaskTable.tsx` with the DataTable-based version**

Overwrite `donna-ui/src/pages/Tasks/TaskTable.tsx` with this exact content:

```tsx
import { useMemo } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { Download } from "lucide-react";
import { Button } from "../../primitives/Button";
import { DataTable } from "../../primitives/DataTable";
import { EmptyState } from "../../primitives/EmptyState";
import { Pill } from "../../primitives/Pill";
import type { TaskSummary } from "../../api/tasks";
import { exportToCsv } from "../../utils/csvExport";
import {
  formatStatusLabel,
  formatTaskTimestamp,
  priorityToPillVariant,
  statusToPillVariant,
} from "./taskStatusStyles";
import styles from "./TaskTable.module.css";

interface Props {
  tasks: TaskSummary[];
  loading: boolean;
  selectedId: string | null;
  onTaskClick: (id: string) => void;
}

export default function TaskTable({
  tasks,
  loading,
  selectedId,
  onTaskClick,
}: Props) {
  const columns = useMemo<ColumnDef<TaskSummary>[]>(
    () => [
      {
        accessorKey: "title",
        header: "Title",
        cell: (info) => (
          <span className={styles.title}>{info.getValue<string>()}</span>
        ),
      },
      {
        accessorKey: "status",
        header: "Status",
        size: 130,
        cell: (info) => {
          const v = info.getValue<string>();
          return (
            <Pill variant={statusToPillVariant(v)}>{formatStatusLabel(v)}</Pill>
          );
        },
      },
      {
        accessorKey: "domain",
        header: "Domain",
        size: 100,
        cell: (info) => (
          <span className={styles.dim}>{info.getValue<string>() ?? "—"}</span>
        ),
      },
      {
        accessorKey: "priority",
        header: "Priority",
        size: 90,
        cell: (info) => {
          const v = info.getValue<number>();
          return <Pill variant={priorityToPillVariant(v)}>P{v}</Pill>;
        },
      },
      {
        accessorKey: "assigned_agent",
        header: "Agent",
        size: 120,
        cell: (info) => (
          <span className={styles.dim}>{info.getValue<string>() ?? "—"}</span>
        ),
      },
      {
        accessorKey: "created_at",
        header: "Created",
        size: 140,
        cell: (info) => (
          <span className={styles.mono}>
            {formatTaskTimestamp(info.getValue<string>())}
          </span>
        ),
      },
      {
        accessorKey: "deadline",
        header: "Deadline",
        size: 140,
        cell: (info) => (
          <span className={styles.mono}>
            {formatTaskTimestamp(info.getValue<string | null>())}
          </span>
        ),
      },
      {
        accessorKey: "nudge_count",
        header: "Nudges",
        size: 80,
        cell: (info) => {
          const n = info.getValue<number>();
          if (n === 0) return <span className={styles.dim}>0</span>;
          return <Pill variant="warning">{n}</Pill>;
        },
      },
      {
        accessorKey: "reschedule_count",
        header: "Resched",
        size: 80,
        cell: (info) => {
          const n = info.getValue<number>();
          if (n === 0) return <span className={styles.dim}>0</span>;
          return <Pill variant="warning">{n}</Pill>;
        },
      },
    ],
    [],
  );

  const handleExport = () => {
    exportToCsv(
      "tasks",
      [
        { key: "title", title: "Title" },
        { key: "status", title: "Status" },
        { key: "domain", title: "Domain" },
        { key: "priority", title: "Priority" },
        { key: "assigned_agent", title: "Agent" },
        { key: "created_at", title: "Created" },
        { key: "deadline", title: "Deadline" },
        { key: "nudge_count", title: "Nudges" },
        { key: "reschedule_count", title: "Reschedules" },
      ],
      tasks as unknown as Record<string, unknown>[],
    );
  };

  return (
    <div className={styles.wrapper}>
      <div className={styles.toolbar}>
        <Button
          variant="ghost"
          size="sm"
          onClick={handleExport}
          disabled={tasks.length === 0}
          aria-label="Export tasks to CSV"
        >
          <Download size={12} /> Export CSV
        </Button>
      </div>
      <DataTable<TaskSummary>
        data={tasks}
        columns={columns}
        getRowId={(row) => row.id}
        onRowClick={(row) => onTaskClick(row.id)}
        selectedRowId={selectedId}
        loading={loading}
        keyboardNav
        pageSize={50}
        emptyState={
          <EmptyState
            eyebrow="No tasks"
            title="Nothing captured yet."
            body="Press ⌘N to add one, or message Donna on Discord and she'll do it for you."
          />
        }
      />
    </div>
  );
}
```

- [ ] **Step 2: Create `TaskTable.module.css`**

Create `donna-ui/src/pages/Tasks/TaskTable.module.css` with this exact content:

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

.title {
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

.mono {
  font-family: var(--font-mono);
  font-size: var(--text-label);
  color: var(--color-text-secondary);
}
```

- [ ] **Step 3: Typecheck the rewritten file**

Run from `donna-ui/`:

```bash
npx tsc --noEmit -p tsconfig.json
```

Expected: no errors referencing `TaskTable.tsx` or `TaskTable.module.css`. (Errors from the stale `TaskFilters.tsx` or `index.tsx` in mid-phase are expected.)

- [ ] **Step 4: Commit**

```bash
git add donna-ui/src/pages/Tasks/TaskTable.tsx donna-ui/src/pages/Tasks/TaskTable.module.css
git commit -m "Rewrite TaskTable on DataTable with Pills and keyboard nav"
```

### Task 4: Create `TaskDetailDrawer.tsx`

**Files:**
- Create: `donna-ui/src/pages/Tasks/TaskDetailDrawer.tsx`
- Create: `donna-ui/src/pages/Tasks/TaskDetailDrawer.module.css`

- [ ] **Step 1: Create `TaskDetailDrawer.tsx`**

Create `donna-ui/src/pages/Tasks/TaskDetailDrawer.tsx` with this exact content:

```tsx
import { useEffect, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { DataTable } from "../../primitives/DataTable";
import { Drawer } from "../../primitives/Drawer";
import { Pill } from "../../primitives/Pill";
import { ScrollArea } from "../../primitives/ScrollArea";
import { Skeleton } from "../../primitives/Skeleton";
import {
  fetchTask,
  type Correction,
  type NudgeEvent,
  type Subtask,
  type TaskDetail,
  type TaskInvocation,
} from "../../api/tasks";
import {
  STATE_ORDER,
  formatStatusLabel,
  formatTaskTimestamp,
  getStateStepIndex,
  priorityToPillVariant,
  statusToPillVariant,
} from "./taskStatusStyles";
import styles from "./TaskDetailDrawer.module.css";

interface Props {
  taskId: string | null;
  onClose: () => void;
}

/**
 * Drawer-based task detail surface. Replaces the free-floating
 * /tasks/:id page and the AntD Card/Descriptions/Steps/Tree stack.
 * Focus trap + ESC close come free from the Radix-backed Drawer
 * primitive (audit item P1 "Task drawer a11y").
 */
export default function TaskDetailDrawer({ taskId, onClose }: Props) {
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    if (!taskId) {
      setTask(null);
      setNotFound(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setNotFound(false);
    fetchTask(taskId)
      .then((data) => {
        if (cancelled) return;
        setTask(data);
      })
      .catch(() => {
        if (cancelled) return;
        setTask(null);
        setNotFound(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [taskId]);

  const title = task?.title ?? (taskId ? `Task · ${taskId.slice(0, 8)}…` : "Task");

  return (
    <Drawer
      open={!!taskId}
      onOpenChange={(open) => !open && onClose()}
      title={title}
    >
      {loading ? (
        <div className={styles.loading}>
          <Skeleton height={16} />
          <Skeleton height={16} />
          <Skeleton height={16} />
        </div>
      ) : notFound ? (
        <div className={styles.emptyHint}>Task not found.</div>
      ) : task ? (
        <ScrollArea className={styles.scroll}>
          <TaskDrawerBody task={task} />
        </ScrollArea>
      ) : null}
    </Drawer>
  );
}

function TaskDrawerBody({ task }: { task: TaskDetail }) {
  const stepIdx = getStateStepIndex(task.status);
  const isCancelled = task.status === "cancelled";

  return (
    <div className={styles.body}>
      {/* Status + priority header row */}
      <div className={styles.headerRow}>
        <Pill variant={statusToPillVariant(task.status)}>
          {formatStatusLabel(task.status)}
        </Pill>
        <Pill variant={priorityToPillVariant(task.priority)}>P{task.priority}</Pill>
        {task.donna_managed && <Pill variant="accent">Donna-managed</Pill>}
      </div>

      {/* State timeline (no AntD Steps) */}
      <section className={styles.section} aria-label="State timeline">
        <div className={styles.eyebrow}>State timeline</div>
        <ol className={styles.timeline}>
          {STATE_ORDER.map((state, idx) => {
            const reached = !isCancelled && idx <= stepIdx;
            return (
              <li
                key={state}
                className={styles.timelineItem}
                data-reached={reached ? "true" : "false"}
                aria-current={idx === stepIdx ? "step" : undefined}
              >
                <span className={styles.timelineDot} aria-hidden="true" />
                <span className={styles.timelineLabel}>
                  {formatStatusLabel(state)}
                </span>
              </li>
            );
          })}
        </ol>
        {isCancelled && (
          <div className={styles.cancelledNote}>Cancelled — timeline abandoned.</div>
        )}
      </section>

      {/* Core fields (<dl> replaces AntD Descriptions) */}
      <section className={styles.section} aria-label="Task details">
        <div className={styles.eyebrow}>Details</div>
        <dl className={styles.fields}>
          <DetailField label="ID" value={task.id} mono />
          <DetailField label="Domain" value={task.domain ?? "—"} />
          <DetailField label="Deadline type" value={task.deadline_type ?? "—"} />
          <DetailField label="Deadline" value={formatTaskTimestamp(task.deadline)} mono />
          <DetailField
            label="Scheduled start"
            value={formatTaskTimestamp(task.scheduled_start)}
            mono
          />
          <DetailField label="Created" value={formatTaskTimestamp(task.created_at)} mono />
          <DetailField label="Created via" value={task.created_via ?? "—"} />
          <DetailField label="Agent" value={task.assigned_agent ?? "—"} />
          <DetailField label="Agent status" value={task.agent_status ?? "—"} />
          <DetailField label="Duration (est)" value={task.estimated_duration ?? "—"} />
          <DetailField label="Reschedules" value={String(task.reschedule_count)} />
          <DetailField label="Nudge count" value={String(task.nudge_count)} />
          <DetailField
            label="Quality score"
            value={task.quality_score != null ? task.quality_score.toFixed(2) : "—"}
          />
          <DetailField label="Prep work" value={task.prep_work_flag ? "Yes" : "No"} />
        </dl>

        {task.description && (
          <div className={styles.descriptionBlock}>
            <div className={styles.eyebrow}>Description</div>
            <pre className={styles.description}>{task.description}</pre>
          </div>
        )}

        {task.tags && task.tags.length > 0 && (
          <div className={styles.tagRow}>
            <span className={styles.eyebrow}>Tags</span>
            {task.tags.map((t) => (
              <Pill key={t} variant="muted">
                {t}
              </Pill>
            ))}
          </div>
        )}
      </section>

      {/* Invocations */}
      {task.invocations.length > 0 && (
        <section className={styles.section} aria-label="Invocations">
          <div className={styles.eyebrow}>Invocations ({task.invocations.length})</div>
          <DataTable<TaskInvocation>
            data={task.invocations}
            columns={INVOCATION_COLUMNS}
            getRowId={(row) => row.id}
          />
        </section>
      )}

      {/* Nudge events */}
      {task.nudge_events.length > 0 && (
        <section className={styles.section} aria-label="Nudge events">
          <div className={styles.eyebrow}>Nudge events ({task.nudge_events.length})</div>
          <DataTable<NudgeEvent>
            data={task.nudge_events}
            columns={NUDGE_COLUMNS}
            getRowId={(row) => row.id}
          />
        </section>
      )}

      {/* Corrections */}
      {task.corrections.length > 0 && (
        <section className={styles.section} aria-label="Corrections">
          <div className={styles.eyebrow}>Corrections ({task.corrections.length})</div>
          <DataTable<Correction>
            data={task.corrections}
            columns={CORRECTION_COLUMNS}
            getRowId={(row) => row.id}
          />
        </section>
      )}

      {/* Subtasks (<ul> replaces AntD Tree) */}
      {task.subtasks.length > 0 && (
        <section className={styles.section} aria-label="Subtasks">
          <div className={styles.eyebrow}>Subtasks ({task.subtasks.length})</div>
          <ul className={styles.subtaskList}>
            {task.subtasks.map((s: Subtask) => (
              <li key={s.id} className={styles.subtaskItem}>
                <span className={styles.subtaskTitle}>{s.title}</span>
                <Pill variant={statusToPillVariant(s.status)}>
                  {formatStatusLabel(s.status)}
                </Pill>
                <Pill variant={priorityToPillVariant(s.priority)}>P{s.priority}</Pill>
                {s.assigned_agent && (
                  <Pill variant="accent">{s.assigned_agent}</Pill>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function DetailField({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className={styles.field}>
      <dt className={styles.fieldLabel}>{label}</dt>
      <dd className={mono ? styles.fieldValueMono : styles.fieldValue}>{value}</dd>
    </div>
  );
}

const INVOCATION_COLUMNS: ColumnDef<TaskInvocation>[] = [
  {
    accessorKey: "timestamp",
    header: "Time",
    size: 150,
    cell: (info) => formatTaskTimestamp(info.getValue<string>()),
  },
  { accessorKey: "task_type", header: "Type", size: 140 },
  { accessorKey: "model_alias", header: "Model", size: 100 },
  {
    accessorKey: "latency_ms",
    header: "Latency",
    size: 80,
    cell: (info) => `${info.getValue<number>()}ms`,
  },
  {
    accessorKey: "cost_usd",
    header: "Cost",
    size: 80,
    cell: (info) => `$${info.getValue<number>().toFixed(4)}`,
  },
  {
    accessorKey: "is_shadow",
    header: "Shadow",
    size: 70,
    cell: (info) =>
      info.getValue<boolean>() ? <Pill variant="muted">Yes</Pill> : "No",
  },
];

const NUDGE_COLUMNS: ColumnDef<NudgeEvent>[] = [
  {
    accessorKey: "created_at",
    header: "Time",
    size: 140,
    cell: (info) => formatTaskTimestamp(info.getValue<string>()),
  },
  { accessorKey: "nudge_type", header: "Type", size: 110 },
  { accessorKey: "channel", header: "Channel", size: 90 },
  { accessorKey: "escalation_tier", header: "Tier", size: 60 },
  { accessorKey: "message_text", header: "Message" },
];

const CORRECTION_COLUMNS: ColumnDef<Correction>[] = [
  {
    accessorKey: "timestamp",
    header: "Time",
    size: 140,
    cell: (info) => formatTaskTimestamp(info.getValue<string>()),
  },
  { accessorKey: "field_corrected", header: "Field", size: 120 },
  { accessorKey: "original_value", header: "Original" },
  { accessorKey: "corrected_value", header: "Corrected" },
];
```

- [ ] **Step 2: Create `TaskDetailDrawer.module.css`**

Create `donna-ui/src/pages/Tasks/TaskDetailDrawer.module.css` with this exact content:

```css
.scroll {
  max-height: calc(100vh - 140px);
}

.body {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
  padding-right: var(--space-3);
}

.loading {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3) 0;
}

.emptyHint {
  font-size: var(--text-body);
  color: var(--color-text-muted);
  padding: var(--space-3) 0;
}

.headerRow {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.section {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.eyebrow {
  font-size: var(--text-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--color-text-muted);
}

.timeline {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  list-style: none;
  padding: 0;
  margin: 0;
}

.timelineItem {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--color-text-muted);
  font-size: var(--text-label);
  text-transform: capitalize;
}

.timelineItem[data-reached="true"] {
  color: var(--color-accent);
}

.timelineItem[aria-current="step"] {
  color: var(--color-text);
  font-weight: 500;
}

.timelineDot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-border);
}

.timelineItem[data-reached="true"] .timelineDot {
  background: var(--color-accent);
}

.timelineLabel {
  white-space: nowrap;
}

.cancelledNote {
  font-size: var(--text-label);
  color: var(--color-error);
}

.fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-2) var(--space-4);
  margin: 0;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.fieldLabel {
  font-size: var(--text-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--color-text-muted);
  margin: 0;
}

.fieldValue {
  font-size: var(--text-body);
  color: var(--color-text);
  margin: 0;
}

.fieldValueMono {
  font-family: var(--font-mono);
  font-size: var(--text-label);
  color: var(--color-text-secondary);
  margin: 0;
}

.descriptionBlock {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.description {
  font-family: var(--font-body);
  font-size: var(--text-body);
  color: var(--color-text);
  white-space: pre-wrap;
  background: var(--color-inset);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-card);
  margin: 0;
}

.tagRow {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.subtaskList {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.subtaskItem {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) 0;
  border-bottom: 1px solid var(--color-border-subtle);
  flex-wrap: wrap;
}

.subtaskItem:last-child {
  border-bottom: 0;
}

.subtaskTitle {
  flex: 1 1 auto;
  font-size: var(--text-body);
  color: var(--color-text);
}

@media (max-width: 720px) {
  .fields {
    grid-template-columns: minmax(0, 1fr);
  }
}
```

- [ ] **Step 3: Typecheck the new files**

Run from `donna-ui/`:

```bash
npx tsc --noEmit -p tsconfig.json
```

Expected: no errors referencing `TaskDetailDrawer.tsx` or `TaskDetailDrawer.module.css`. The new file imports only from `react`, `@tanstack/react-table`, and primitives — all already present.

- [ ] **Step 4: Commit**

```bash
git add donna-ui/src/pages/Tasks/TaskDetailDrawer.tsx donna-ui/src/pages/Tasks/TaskDetailDrawer.module.css
git commit -m "Add TaskDetailDrawer on Drawer primitive with focus trap and ESC"
```

---

## Phase C · Integration

### Task 5: Rewrite `Tasks/index.tsx`, update route, delete `TaskDetail.tsx`

**Files:**
- Modify: `donna-ui/src/pages/Tasks/index.tsx` (full rewrite)
- Create: `donna-ui/src/pages/Tasks/Tasks.module.css`
- Modify: `donna-ui/src/App.tsx` (route declaration)
- Delete: `donna-ui/src/pages/Tasks/TaskDetail.tsx`

- [ ] **Step 1: Create `Tasks.module.css`**

Create `donna-ui/src/pages/Tasks/Tasks.module.css` with this exact content:

```css
.root {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  font-family: var(--font-body);
  color: var(--color-text);
  min-width: 0;
}

.pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding-top: var(--space-2);
}

.pageMeta {
  font-size: var(--text-label);
  color: var(--color-text-muted);
}

.pageControls {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}
```

- [ ] **Step 2: Rewrite `Tasks/index.tsx`**

Overwrite `donna-ui/src/pages/Tasks/index.tsx` with this exact content:

```tsx
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import RefreshButton from "../../components/RefreshButton";
import { Button } from "../../primitives/Button";
import { PageHeader } from "../../primitives/PageHeader";
import { fetchTasks, type TaskSummary } from "../../api/tasks";
import TaskDetailDrawer from "./TaskDetailDrawer";
import TaskFilters from "./TaskFilters";
import TaskTable from "./TaskTable";
import { ALL_VALUE } from "./taskStatusStyles";
import styles from "./Tasks.module.css";

const PAGE_SIZE = 50;

/**
 * Tasks list page. Owns filter + pagination + drawer state. The
 * drawer's open/close state is mirrored to the URL via the optional
 * :id param so the existing Logs deep-link (`window.open('/tasks/:id')`)
 * still opens a pre-populated drawer.
 */
export default function TasksPage() {
  const navigate = useNavigate();
  const { id: routeId } = useParams<{ id?: string }>();

  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  const [status, setStatus] = useState<string>(ALL_VALUE);
  const [domain, setDomain] = useState<string>(ALL_VALUE);
  const [priority, setPriority] = useState<string>(ALL_VALUE);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  const doFetch = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await fetchTasks({
        status: status === ALL_VALUE ? undefined : status,
        domain: domain === ALL_VALUE ? undefined : domain,
        priority: priority === ALL_VALUE ? undefined : Number(priority),
        search: search || undefined,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      });
      setTasks(Array.isArray(resp?.tasks) ? resp.tasks : []);
      setTotal(typeof resp?.total === "number" ? resp.total : 0);
    } catch {
      setTasks([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [status, domain, priority, search, page]);

  useEffect(() => {
    doFetch();
  }, [doFetch]);

  const handleReset = useCallback(() => {
    setStatus(ALL_VALUE);
    setDomain(ALL_VALUE);
    setPriority(ALL_VALUE);
    setSearch("");
    setPage(1);
  }, []);

  const handleTaskClick = useCallback(
    (id: string) => {
      navigate(`/tasks/${id}`);
    },
    [navigate],
  );

  const handleDrawerClose = useCallback(() => {
    navigate("/tasks");
  }, [navigate]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const metaLine = useMemo(() => {
    if (total === 0) return "No tasks in range";
    const start = (page - 1) * PAGE_SIZE + 1;
    const end = Math.min(page * PAGE_SIZE, total);
    return `Showing ${start}–${end} of ${total}`;
  }, [total, page]);

  return (
    <div className={styles.root} data-testid="tasks-root">
      <PageHeader
        eyebrow="Work"
        title="Tasks"
        meta={metaLine}
        actions={<RefreshButton onRefresh={doFetch} />}
      />

      <TaskFilters
        status={status}
        domain={domain}
        priority={priority}
        search={search}
        onStatusChange={(v) => {
          setStatus(v);
          setPage(1);
        }}
        onDomainChange={(v) => {
          setDomain(v);
          setPage(1);
        }}
        onPriorityChange={(v) => {
          setPriority(v);
          setPage(1);
        }}
        onSearchChange={(v) => {
          setSearch(v);
          setPage(1);
        }}
        onReset={handleReset}
      />

      <TaskTable
        tasks={tasks}
        loading={loading}
        selectedId={routeId ?? null}
        onTaskClick={handleTaskClick}
      />

      <nav className={styles.pagination} aria-label="Tasks pagination">
        <span className={styles.pageMeta}>
          Page {page} / {totalPages}
        </span>
        <div className={styles.pageControls}>
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

      <TaskDetailDrawer taskId={routeId ?? null} onClose={handleDrawerClose} />
    </div>
  );
}
```

- [ ] **Step 3: Update `App.tsx` route declaration**

Modify `donna-ui/src/App.tsx`. Remove the `TaskDetail` import (line 11) and change the two task routes (lines 34–35) so that **both** `/tasks` and `/tasks/:id` render `TasksPage`.

Apply these two edits:

Edit 1 — remove the `TaskDetail` import:

Old (line 11):
```tsx
import TaskDetail from "./pages/Tasks/TaskDetail";
```

New:
```tsx
```

(Delete the line entirely.)

Edit 2 — replace the two route declarations:

Old (lines 34–35):
```tsx
          <Route path="/tasks" element={<ErrorBoundary><TasksPage /></ErrorBoundary>} />
          <Route path="/tasks/:id" element={<ErrorBoundary><TaskDetail /></ErrorBoundary>} />
```

New:
```tsx
          <Route path="/tasks" element={<ErrorBoundary><TasksPage /></ErrorBoundary>} />
          <Route path="/tasks/:id" element={<ErrorBoundary><TasksPage /></ErrorBoundary>} />
```

- [ ] **Step 4: Delete the obsolete `TaskDetail.tsx`**

Run from `/home/feuer/Documents/Projects/donna`:

```bash
git rm donna-ui/src/pages/Tasks/TaskDetail.tsx
```

Expected: file staged for deletion.

- [ ] **Step 5: Typecheck and build**

Run from `donna-ui/`:

```bash
npx tsc -b
```

Expected: exit code 0, no errors.

```bash
npm run lint
```

Expected: exit code 0. If ESLint reports an unused `ALL_VALUE` import or similar, stop — something was missed in the rewrite. Fix the specific error reported and re-run.

```bash
npm run build
```

Expected: Vite build completes successfully, no TS errors.

- [ ] **Step 6: Commit**

```bash
git add donna-ui/src/pages/Tasks/index.tsx donna-ui/src/pages/Tasks/Tasks.module.css donna-ui/src/App.tsx
git commit -m "Rewrite Tasks page on primitives with URL-driven drawer"
```

---

## Phase D · Verification

### Task 6: Expand Playwright smoke test

**Files:**
- Modify: `donna-ui/tests/e2e/smoke/tasks.spec.ts` (full rewrite)

- [ ] **Step 1: Replace the smoke test with the expanded version**

Overwrite `donna-ui/tests/e2e/smoke/tasks.spec.ts` with this exact content:

```ts
import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Tasks smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("renders PageHeader, filter bar, and empty state", async ({ page }) => {
    await page.goto("/tasks");

    // PageHeader is the new primitive composition.
    await expect(page.getByRole("heading", { name: "Tasks" })).toBeVisible();

    // Filter controls with explicit aria-labels.
    await expect(page.getByLabel("Search tasks")).toBeVisible();
    await expect(page.getByLabel("Status filter")).toBeVisible();
    await expect(page.getByLabel("Domain filter")).toBeVisible();
    await expect(page.getByLabel("Priority filter")).toBeVisible();

    // Reset button — disabled when no filter is dirty.
    await expect(page.getByLabel("Reset all task filters")).toBeDisabled();

    // Pagination nav from the new page shell.
    await expect(page.getByLabel("Tasks pagination")).toBeVisible();
    await expect(page.getByRole("button", { name: "Prev" })).toBeDisabled();

    // Mocked empty response → EmptyState rendered.
    await expect(page.getByText("Nothing captured yet.")).toBeVisible();
  });

  test("reset button enables when search changes and clears all filters", async ({ page }) => {
    await page.goto("/tasks");

    const reset = page.getByLabel("Reset all task filters");
    await expect(reset).toBeDisabled();

    const search = page.getByLabel("Search tasks");
    await search.fill("deadline");
    await expect(reset).toBeEnabled();

    await reset.click();
    await expect(search).toHaveValue("");
    await expect(reset).toBeDisabled();
  });

  test("deep-linking /tasks/:id opens the drawer", async ({ page }) => {
    // Mock the detail endpoint with a minimal task payload so the drawer
    // body renders instead of showing "not found".
    await page.route("**/admin/tasks/abc123", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "abc123",
          user_id: "u1",
          title: "Deep-linked task",
          description: null,
          domain: "work",
          priority: 2,
          status: "in_progress",
          estimated_duration: null,
          deadline: null,
          deadline_type: null,
          scheduled_start: null,
          actual_start: null,
          completed_at: null,
          parent_task: null,
          prep_work_flag: false,
          prep_work_instructions: null,
          agent_eligible: false,
          assigned_agent: null,
          agent_status: null,
          tags: null,
          notes: null,
          reschedule_count: 0,
          created_at: "2026-04-08T12:00:00",
          created_via: "test",
          nudge_count: 0,
          quality_score: null,
          donna_managed: false,
          recurrence: null,
          dependencies: null,
          estimated_cost: null,
          calendar_event_id: null,
          invocations: [],
          nudge_events: [],
          corrections: [],
          subtasks: [],
        }),
      }),
    );

    await page.goto("/tasks/abc123");

    // Drawer dialog is open — Radix renders role="dialog".
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText("Deep-linked task")).toBeVisible();
  });

  test("ESC closes the drawer and returns to /tasks", async ({ page }) => {
    await page.route("**/admin/tasks/abc123", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "abc123",
          user_id: "u1",
          title: "Deep-linked task",
          description: null,
          domain: null,
          priority: 3,
          status: "backlog",
          estimated_duration: null,
          deadline: null,
          deadline_type: null,
          scheduled_start: null,
          actual_start: null,
          completed_at: null,
          parent_task: null,
          prep_work_flag: false,
          prep_work_instructions: null,
          agent_eligible: false,
          assigned_agent: null,
          agent_status: null,
          tags: null,
          notes: null,
          reschedule_count: 0,
          created_at: "2026-04-08T12:00:00",
          created_via: "test",
          nudge_count: 0,
          quality_score: null,
          donna_managed: false,
          recurrence: null,
          dependencies: null,
          estimated_cost: null,
          calendar_event_id: null,
          invocations: [],
          nudge_events: [],
          corrections: [],
          subtasks: [],
        }),
      }),
    );

    await page.goto("/tasks/abc123");
    await expect(page.getByRole("dialog")).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).not.toBeVisible();
    await expect(page).toHaveURL(/\/tasks$/);
  });

  test("no AntD class names inside tasks-root", async ({ page }) => {
    await page.goto("/tasks");

    // Scope check to the Tasks shell, not PageHeader actions —
    // RefreshButton is still AntD until Wave 9. It lives in the
    // PageHeader `actions` slot, which is outside `[data-testid="tasks-root"] > div:not(header)`.
    const antdCount = await page
      .locator('[data-testid="tasks-root"] > *:not(header) [class*="ant-"]')
      .count();
    expect(antdCount).toBe(0);
  });
});
```

- [ ] **Step 2: Run the Tasks smoke test**

Run from `donna-ui/`:

```bash
npm run test:e2e -- tasks
```

Expected: all 5 tests in `tasks.spec.ts` pass. If the "no AntD class names" assertion fails with a non-zero count, grep the Tasks subtree for remaining AntD imports — something was missed in Task 5.

- [ ] **Step 3: Commit**

```bash
git add donna-ui/tests/e2e/smoke/tasks.spec.ts
git commit -m "Expand Tasks smoke test to cover primitives, drawer, and reset"
```

### Task 7: Final verification + PR

- [ ] **Step 1: Grep for the eliminated symbol `STATUS_TAG_COLORS`**

Run from `/home/feuer/Documents/Projects/donna`:

```bash
grep -rn "STATUS_TAG_COLORS" donna-ui/src/pages/Tasks/
```

Expected: exit code 1, zero matches. A single match means Task 3 or Task 4 missed a call site — stop and fix before continuing. (The replacement export is `statusToPillVariant`, not `STATUS_TAG_COLORS`.)

- [ ] **Step 2: Grep for AntD imports in the Tasks subtree**

```bash
grep -rEn "from ['\"]antd['\"]|from ['\"]@ant-design/icons['\"]" donna-ui/src/pages/Tasks/
```

Expected: exit code 1, zero matches. If a match is reported, open the file and replace the AntD import with the equivalent primitive — do not ship Wave 5 with stale AntD in the Tasks tree.

- [ ] **Step 3: Grep for inline hex literals in the Tasks subtree**

```bash
grep -rEn "#[0-9a-fA-F]{3,6}\\b" donna-ui/src/pages/Tasks/
```

Expected: zero matches. Every colour flows through CSS custom properties in `tokens.css` consumed via module CSS.

- [ ] **Step 4: Grep for `message.` (AntD notification calls)**

```bash
grep -rn "message\\." donna-ui/src/pages/Tasks/
```

Expected: zero matches. (Sonner's `toast.*` is the only allowed notification surface.)

- [ ] **Step 5: Full build + lint**

Run from `donna-ui/`:

```bash
npx tsc -b && npm run lint && npm run build
```

Expected: all three exit 0. If the Vite build reports a bundle-size warning, that is fine — bundle cleanup is Wave 9's job.

- [ ] **Step 6: Full Playwright smoke suite**

Run from `donna-ui/`:

```bash
npm run test:e2e
```

Expected: all smoke tests pass across every page (Dashboard, Logs, Tasks, Agents, Configs, Prompts, Shadow, Preferences, app-shell, dev-primitives). Wave 5 only touches Tasks, so a regression on another page means something unexpected — stop and investigate.

- [ ] **Step 7: Confirm the deferred "New Task" Dialog is documented**

This plan explicitly does **not** add a New Task creation Dialog because the backend `POST /admin/tasks` endpoint does not exist and the user brief froze the backend contract. When the PR body is written, include this line verbatim under a "Deferred" section:

> New Task creation Dialog (§4 Wave 4 line 363 of the master spec) is deferred to a follow-up wave that will also add `POST /admin/tasks` to `src/donna/api/routes/admin_tasks.py`.

- [ ] **Step 8: Push and open the PR**

```bash
git push -u origin wave-5-tasks
```

Then open a PR from `wave-5-tasks` → `main` with title `Wave 5 · Tasks migration to primitives + drawer` and a body that lists the resolved audit items, the deferred New Task Dialog, and the spec reference (§4 Wave 4 lines 357–369).

---

## Self-review against the spec

**Spec coverage (§4 Wave 4 lines 357–369):**

| Spec item | Task |
|---|---|
| "Kill inner `<Sider>`. Build filter bar." | No inner Sider exists in the current Tasks page — its AntD `Card` wrapper is dropped in Task 5 and replaced with PageHeader + primitive filter bar in Tasks 2 and 5. |
| "`TaskTable` → `<DataTable>` with sort, pagination, keyboard nav." | Task 3 — DataTable with `keyboardNav` enabled + client pagination via `pageSize={50}`. Column sorting is enabled by default in TanStack Table via `getSortedRowModel`. |
| "Task detail drawer → `<Drawer>`." | Task 4 — new `TaskDetailDrawer.tsx` on the Drawer primitive. |
| "`STATUS_TAG_COLORS` duplication in `TaskTable.tsx` and `TaskDetail.tsx` deleted — central theme tokens used instead." | Task 1 creates `taskStatusStyles.ts`; Task 3 deletes the TaskTable copy; Task 4 never introduces a new copy; Task 5 deletes `TaskDetail.tsx` entirely. Verified by grep in Task 7 Step 1. |
| "New Task flow → `<Dialog>` with React Hook Form + zod." | **Deferred** — explicitly documented in the Architecture section and Task 7 Step 7. Requires a backend endpoint that does not exist and that the user brief forbade adding. |
| "[P1] Duplicated `STATUS_TAG_COLORS` (TaskTable + TaskDetail)" | Task 1 + Task 3 + Task 5. |
| "[P1] Task drawer a11y — no focus trap, no ESC (free with Radix)" | Task 4 (Drawer wraps Radix Dialog). Verified by the ESC test in Task 6. |
| "[P2] Task filter form lacks reset button" | Task 2 (Reset button with aria-label + disabled-when-clean state). Verified by the reset test in Task 6. |
| "[P2] AntD message toasts → Sonner" | Task 7 Step 4 grep enforces no `message.*` usage. |

**Placeholder scan:** every step contains complete code or an exact shell command. No "TBD", no "add appropriate error handling", no "similar to Task N", no references to undefined symbols. Every prop, type, and function named in a later task (`statusToPillVariant`, `priorityToPillVariant`, `formatTaskTimestamp`, `ALL_VALUE`, `STATE_ORDER`, `getStateStepIndex`, `STATUS_OPTIONS`, `DOMAIN_OPTIONS`, `PRIORITY_OPTIONS`) is defined in Task 1's module body.

**Type consistency:** `TaskFilters` props use `string` for `status`/`domain`/`priority` (all three are sentinel-backed Radix Select values); the page converts `priority` back to `Number()` before calling `fetchTasks` which expects `number | undefined`. `TaskTable` receives `selectedId: string | null` (not `string | undefined`) to match the `selectedRowId` prop on the `DataTable` primitive. `TaskDetailDrawer` receives `taskId: string | null`. The `useParams<{ id?: string }>()` return is coerced with `?? null` before being passed down. All consistent.

---

**End of plan.**
