# Donna UI Wave 7 — Configs + Prompts Migration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Strip AntD from `pages/Configs` and `pages/Prompts`, split each into `list` + `:file` subroutes, rewrite the unsafe `MarkdownPreview`, and migrate the four structured config forms to react-hook-form + zod.

**Architecture:** Two parallel tracks (Configs and Prompts) share a common "list to editor subroute" pattern. Foundation tasks (routing, Monaco theme util, YAML Web Worker, Dialog size prop, central state colors) land first — then the tracks fan out and can run in parallel. A dedicated security task rewrites `MarkdownPreview` and proves the old regex-based raw-HTML injection vector is closed via test.

**Tech Stack:** React 18, React Router 6, Radix primitives, `@monaco-editor/react`, `react-hook-form` + `@hookform/resolvers` + `zod`, `react-markdown` + `rehype-sanitize` + `rehype-highlight`, Vite Web Workers, Playwright.

---

## Reality-Check Preamble (read before starting)

**Spec says** `PreferencesForm` migrates — but `donna-ui/src/pages/Configs/forms/` has **no PreferencesForm**. The real forms on disk are:

- `AgentsForm.tsx`
- `ModelsForm.tsx`
- `StatesForm.tsx`
- `TaskTypesForm.tsx`

Treat the spec reference as a typo for `AgentsForm`. All four existing forms migrate.

**Dispatcher**: `donna-ui/src/pages/Configs/StructuredEditor.tsx` maps filenames to form components. No preferences.yaml entry exists; preferences falls through to `RawYamlEditor`. Keep that behavior.

**Color duplication fact check** (grep-verified):

- `STATE_COLORS` lives in `donna-ui/src/pages/Configs/forms/StatesForm.tsx:21-29` (AntD blues/greens).
- `TASK_STATUS_COLORS` lives in `donna-ui/src/theme/darkTheme.ts:64-72` (also AntD blues/greens, with divergent `waiting_input` and `cancelled` values).
- `darkTheme.ts` itself is scheduled for deletion in Wave 9 — so the new central home is a fresh file `src/theme/stateColors.ts` using CSS tokens, **not** the old AntD hexes.

**Backend**: `GET/PUT /admin/configs` and `/admin/prompts` are untouched. All migration is client-side.

**Branch**: work in `wave-7-configs-prompts` off `main`. If a git worktree is being used (recommended via `superpowers:using-git-worktrees`), this branch is created automatically.

---

## File Structure

### New files

- `donna-ui/src/theme/stateColors.ts` — central state to Pill variant + CSS var mapping
- `donna-ui/src/lib/monacoTheme.ts` — `setupDonnaMonacoTheme(monaco)` + `DONNA_MONACO_THEME = "donna-dark"`
- `donna-ui/src/workers/yamlValidator.worker.ts` — Vite module worker: parses YAML, returns `{ ok, data, error }`
- `donna-ui/src/hooks/useYamlValidator.ts` — debounced hook wrapping the worker
- `donna-ui/src/pages/Configs/Configs.module.css`
- `donna-ui/src/pages/Configs/ConfigsList.tsx` — list view (formerly sider contents)
- `donna-ui/src/pages/Configs/ConfigEditor.tsx` — single-file editor view
- `donna-ui/src/pages/Configs/schemas.ts` — zod schemas for the 4 structured files
- `donna-ui/src/pages/Prompts/Prompts.module.css`
- `donna-ui/src/pages/Prompts/PromptsList.tsx`
- `donna-ui/src/pages/Prompts/PromptEditor.tsx`
- `donna-ui/src/pages/Prompts/MarkdownPreview.module.css`

### Modified files

- `donna-ui/src/App.tsx` — add `/configs/:file` and `/prompts/:file` routes
- `donna-ui/src/primitives/Dialog.tsx` + `Dialog.module.css` — add `size?: "default" | "wide"` prop
- `donna-ui/src/primitives/index.ts` — re-export `DialogSize`
- `donna-ui/src/pages/Configs/index.tsx` — thin router shell dispatching to list or editor
- `donna-ui/src/pages/Configs/ConfigFileList.tsx` — strip AntD Menu/Spin
- `donna-ui/src/pages/Configs/SaveDiffModal.tsx` — strip AntD Modal, use primitive Dialog (wide)
- `donna-ui/src/pages/Configs/RawYamlEditor.tsx` — token-driven monaco theme
- `donna-ui/src/pages/Configs/forms/StatesForm.tsx` — RHF+zod, primitives, delete STATE_COLORS
- `donna-ui/src/pages/Configs/forms/ModelsForm.tsx` — RHF+zod, primitives
- `donna-ui/src/pages/Configs/forms/TaskTypesForm.tsx` — RHF+zod, primitives
- `donna-ui/src/pages/Configs/forms/AgentsForm.tsx` — RHF+zod, primitives
- `donna-ui/src/pages/Prompts/index.tsx` — thin router shell
- `donna-ui/src/pages/Prompts/PromptFileList.tsx` — strip AntD Menu/Spin
- `donna-ui/src/pages/Prompts/VariableInspector.tsx` — strip AntD Card/Tag/Empty
- `donna-ui/src/pages/Prompts/MarkdownPreview.tsx` — **complete rewrite**: `react-markdown` + `rehype-sanitize` + `rehype-highlight`
- `donna-ui/src/main.tsx` — import highlight.js stylesheet
- `donna-ui/tests/e2e/helpers.ts` — fix `/admin/configs` and `/admin/prompts` mock shapes
- `donna-ui/tests/e2e/smoke/configs.spec.ts` — expand coverage
- `donna-ui/tests/e2e/smoke/prompts.spec.ts` — expand coverage + XSS regression

### Deleted (end of wave)

Nothing deleted outright — AntD itself is removed in Wave 9. After this wave, `Configs` and `Prompts` should have **zero** `antd` imports (verify with `grep`).

---

## Phases

```
Phase 1 (serial): Foundation — Tasks 1..5
Phase 2a (parallel with 2b): Configs track — Tasks 6..14
Phase 2b (parallel with 2a): Prompts track — Tasks 15..18
Phase 3 (serial): Integration — Tasks 19..22
```

Subagents in phase 2 can be dispatched in parallel per track. Inside a track, tasks are serial.

---

# Phase 1 — Foundation

## Task 1: Add subroute routes for Configs and Prompts

**Files:**
- Modify: `donna-ui/src/App.tsx` (lines 30–31)

- [ ] **Step 1: Add routes**

Replace the single `/configs` and `/prompts` routes with list + detail variants:

```tsx
<Route path="/configs" element={<ErrorBoundary><ConfigsPage /></ErrorBoundary>} />
<Route path="/configs/:file" element={<ErrorBoundary><ConfigsPage /></ErrorBoundary>} />
<Route path="/prompts" element={<ErrorBoundary><PromptsPage /></ErrorBoundary>} />
<Route path="/prompts/:file" element={<ErrorBoundary><PromptsPage /></ErrorBoundary>} />
```

The page components remain `ConfigsPage`/`PromptsPage` — these shells read the `:file` param and decide between list and editor (implemented in Tasks 7 and 18). Wildcard param style matches the existing `/agents/:name` convention in the same file.

- [ ] **Step 2: Typecheck**

Run: `cd donna-ui && npx tsc -b --noEmit`
Expected: PASS (routes don't break types — pages still accept zero props).

- [ ] **Step 3: Commit**

```bash
git add donna-ui/src/App.tsx
git commit -m "feat(routing): add /configs/:file and /prompts/:file subroutes"
```

---

## Task 2: Central state colors + delete STATE_COLORS

**Files:**
- Create: `donna-ui/src/theme/stateColors.ts`

**Why this now:** Unblocks the StatesForm migration (Task 11) and the audit item about divergent `STATE_COLORS` / `TASK_STATUS_COLORS`. We use CSS tokens, not the old AntD hexes — Wave 9 deletes `darkTheme.ts` entirely.

- [ ] **Step 1: Create the module**

```ts
// donna-ui/src/theme/stateColors.ts
import type { PillVariant } from "../primitives";

/**
 * Maps a task/workflow state to a Pill variant (for inline badges)
 * and a CSS custom property reference (for SVG fills, where a Pill
 * can't render). All values come from src/theme/tokens.css — no hex.
 *
 * Semantic rationale:
 *   done          -> success (positive terminal)
 *   blocked       -> error   (needs attention)
 *   in_progress   -> warning (active, draws eye)
 *   waiting_input -> warning (user action required)
 *   scheduled     -> accent  (primary focus)
 *   cancelled     -> muted   (terminal, neutral)
 *   backlog       -> muted   (inert queue)
 */
export const STATE_PILL_VARIANT: Record<string, PillVariant> = {
  backlog: "muted",
  scheduled: "accent",
  in_progress: "warning",
  blocked: "error",
  waiting_input: "warning",
  done: "success",
  cancelled: "muted",
};

/**
 * CSS variable reference for each state — used for SVG diagram fills
 * where Pill components can't render. Mirrors STATE_PILL_VARIANT
 * semantically.
 */
export const STATE_CSS_VAR: Record<string, string> = {
  backlog: "var(--color-text-muted)",
  scheduled: "var(--color-accent)",
  in_progress: "var(--color-warning)",
  blocked: "var(--color-error)",
  waiting_input: "var(--color-warning)",
  done: "var(--color-success)",
  cancelled: "var(--color-text-dim)",
};

export function statePillVariant(state: string): PillVariant {
  return STATE_PILL_VARIANT[state] ?? "muted";
}

export function stateCssVar(state: string): string {
  return STATE_CSS_VAR[state] ?? "var(--color-text-muted)";
}
```

- [ ] **Step 2: Typecheck**

Run: `cd donna-ui && npx tsc -b --noEmit`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add donna-ui/src/theme/stateColors.ts
git commit -m "feat(theme): add central state color helper for StatesForm migration"
```

Note: `STATE_COLORS` in `StatesForm.tsx` is removed in Task 11 (it belongs with the form migration). `TASK_STATUS_COLORS` in `darkTheme.ts` is untouched here — Wave 9 deletes `darkTheme.ts` wholesale.

---

## Task 3: Shared Monaco theme util

**Files:**
- Create: `donna-ui/src/lib/monacoTheme.ts`

**Goal:** One place that registers a Donna-branded Monaco theme pulling colors from design tokens resolved at runtime via `getComputedStyle`. Eliminates hardcoded `theme="vs-dark"` from `RawYamlEditor`, `SaveDiffModal`, and the Prompts editor.

- [ ] **Step 1: Create the module**

```ts
// donna-ui/src/lib/monacoTheme.ts
import type { Monaco } from "@monaco-editor/react";

export const DONNA_MONACO_THEME = "donna-dark";

/**
 * Reads a CSS custom property from :root and returns a 6-digit hex.
 * Monaco requires literal hex — it can't consume `var(--x)`.
 * Falls back to a safe default if resolution fails (SSR/tests).
 */
function resolveTokenHex(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  if (!raw) return fallback;
  if (/^#[0-9a-fA-F]{6}$/.test(raw)) return raw;
  return fallback;
}

let registered = false;

/**
 * Registers "donna-dark" once per session. Idempotent.
 * Call from every Monaco `<Editor>` via the `beforeMount` prop.
 */
export function setupDonnaMonacoTheme(monaco: Monaco) {
  if (registered) return;
  const bg = resolveTokenHex("--color-inset", "#16140f");
  const surface = resolveTokenHex("--color-surface", "#1f1c18");
  const text = resolveTokenHex("--color-text", "#e8e3d8");
  const muted = resolveTokenHex("--color-text-muted", "#8a8378");
  const border = resolveTokenHex("--color-border", "#2a2724");
  const accent = resolveTokenHex("--color-accent", "#d4a943");

  monaco.editor.defineTheme(DONNA_MONACO_THEME, {
    base: "vs-dark",
    inherit: true,
    rules: [
      { token: "", foreground: text.slice(1) },
      { token: "comment", foreground: muted.slice(1), fontStyle: "italic" },
      { token: "string", foreground: accent.slice(1) },
      { token: "number", foreground: accent.slice(1) },
      { token: "keyword", foreground: text.slice(1), fontStyle: "bold" },
    ],
    colors: {
      "editor.background": bg,
      "editor.foreground": text,
      "editorLineNumber.foreground": muted,
      "editorLineNumber.activeForeground": text,
      "editorCursor.foreground": accent,
      "editor.selectionBackground": border,
      "editor.inactiveSelectionBackground": surface,
      "editorIndentGuide.background": border,
      "editorIndentGuide.activeBackground": accent + "66",
      "editorWidget.background": surface,
      "editorWidget.border": border,
    },
  });
  registered = true;
}
```

- [ ] **Step 2: Typecheck**

Run: `cd donna-ui && npx tsc -b --noEmit`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add donna-ui/src/lib/monacoTheme.ts
git commit -m "feat(monaco): add token-driven donna-dark editor theme"
```

---

## Task 4: YAML validation Web Worker + hook

**Files:**
- Create: `donna-ui/src/workers/yamlValidator.worker.ts`
- Create: `donna-ui/src/hooks/useYamlValidator.ts`

**Why:** `RawYamlEditor.tsx` + `index.tsx` parse YAML on every keystroke on the main thread. The spec requires a debounced (200 ms) Web Worker.

- [ ] **Step 1: Create the worker**

```ts
// donna-ui/src/workers/yamlValidator.worker.ts
// Vite module worker. Consumed via:
//   new Worker(new URL("./yamlValidator.worker.ts", import.meta.url), { type: "module" })
import yaml from "yaml";

export interface ValidationRequest {
  id: number;
  source: string;
}

export interface ValidationResult {
  id: number;
  ok: boolean;
  data: unknown;
  error: { message: string; line?: number; column?: number } | null;
}

self.addEventListener("message", (event: MessageEvent<ValidationRequest>) => {
  const { id, source } = event.data;
  try {
    const data = yaml.parse(source);
    const result: ValidationResult = { id, ok: true, data: data ?? {}, error: null };
    (self as unknown as Worker).postMessage(result);
  } catch (err) {
    const e = err as { message?: string; linePos?: Array<{ line: number; col: number }> };
    const pos = e.linePos?.[0];
    const result: ValidationResult = {
      id,
      ok: false,
      data: null,
      error: {
        message: e.message ?? "Invalid YAML",
        line: pos?.line,
        column: pos?.col,
      },
    };
    (self as unknown as Worker).postMessage(result);
  }
});

export {};
```

- [ ] **Step 2: Create the hook**

```ts
// donna-ui/src/hooks/useYamlValidator.ts
import { useEffect, useRef, useState } from "react";
import type { ValidationResult } from "../workers/yamlValidator.worker";

export interface YamlValidationState {
  validating: boolean;
  ok: boolean;
  data: unknown;
  error: ValidationResult["error"];
}

const DEBOUNCE_MS = 200;

/**
 * Debounced YAML validation via a module Web Worker.
 * Returns a stable state object reflecting the most recent validation.
 *
 * The worker is created once per hook instance and terminated on unmount.
 * Out-of-order responses are dropped by matching request ids.
 */
export function useYamlValidator(source: string): YamlValidationState {
  const [state, setState] = useState<YamlValidationState>({
    validating: false,
    ok: true,
    data: {},
    error: null,
  });
  const workerRef = useRef<Worker | null>(null);
  const nextId = useRef(0);
  const latestId = useRef(0);
  const timeoutRef = useRef<number | null>(null);

  useEffect(() => {
    const worker = new Worker(
      new URL("../workers/yamlValidator.worker.ts", import.meta.url),
      { type: "module" },
    );
    workerRef.current = worker;
    worker.addEventListener("message", (event: MessageEvent<ValidationResult>) => {
      const msg = event.data;
      if (msg.id !== latestId.current) return; // stale
      setState({
        validating: false,
        ok: msg.ok,
        data: msg.data ?? {},
        error: msg.error,
      });
    });
    return () => {
      worker.terminate();
      workerRef.current = null;
      if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
    };
  }, []);

  useEffect(() => {
    if (!workerRef.current) return;
    setState((s) => ({ ...s, validating: true }));
    if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
    timeoutRef.current = window.setTimeout(() => {
      const id = ++nextId.current;
      latestId.current = id;
      workerRef.current?.postMessage({ id, source });
    }, DEBOUNCE_MS);
  }, [source]);

  return state;
}
```

- [ ] **Step 3: Typecheck**

Run: `cd donna-ui && npx tsc -b --noEmit`
Expected: PASS. If Vite's worker types cause friction, confirm `tsconfig.json` has `"lib": ["ES2022", "DOM", "DOM.Iterable"]` — it already does.

- [ ] **Step 4: Commit**

```bash
git add donna-ui/src/workers/yamlValidator.worker.ts donna-ui/src/hooks/useYamlValidator.ts
git commit -m "feat(yaml): debounced web-worker validation for config editor"
```

---

## Task 5: Dialog `size="wide"` for save-diff modal

**Files:**
- Modify: `donna-ui/src/primitives/Dialog.tsx`
- Modify: `donna-ui/src/primitives/Dialog.module.css`
- Modify: `donna-ui/src/primitives/index.ts`

**Why:** The default Dialog content caps at `max-width: 560px`. `SaveDiffModal` needs a wide variant to render the side-by-side diff comfortably. Audit item P0 says the old `width={900}` broke mobile — the new variant is fluid (`min(96vw, 1120px)`).

- [ ] **Step 1: Add `size` prop to Dialog**

Edit `donna-ui/src/primitives/Dialog.tsx`:

```tsx
import * as RadixDialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../lib/cn";
import styles from "./Dialog.module.css";

export type DialogSize = "default" | "wide";

interface DialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: ReactNode;
  size?: DialogSize;
}

export function Dialog({ open, onOpenChange, children, size = "default" }: DialogProps) {
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className={styles.overlay} />
        <RadixDialog.Content className={cn(styles.content, size === "wide" && styles.wide)}>
          {children}
          <RadixDialog.Close className={styles.close} aria-label="Close">
            <X size={16} />
          </RadixDialog.Close>
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}

export function DialogHeader({ children }: { children: ReactNode }) {
  return <div className={styles.header}>{children}</div>;
}

export function DialogTitle({ children }: { children: ReactNode }) {
  return <RadixDialog.Title className={styles.title}>{children}</RadixDialog.Title>;
}

export function DialogDescription({ children }: { children: ReactNode }) {
  return <RadixDialog.Description className={styles.description}>{children}</RadixDialog.Description>;
}

export function DialogFooter({ children }: { children: ReactNode }) {
  return <div className={styles.footer}>{children}</div>;
}
```

- [ ] **Step 2: Add `.wide` modifier to CSS**

Append to `donna-ui/src/primitives/Dialog.module.css`:

```css
.wide {
  max-width: min(96vw, 1120px);
}

@media (max-width: 640px) {
  .wide {
    padding: var(--space-3);
  }
}
```

- [ ] **Step 3: Re-export type**

Edit `donna-ui/src/primitives/index.ts` — the `Dialog` export line already exists; add the type:

```ts
export { Dialog, DialogHeader, DialogTitle, DialogDescription, DialogFooter, type DialogSize } from "./Dialog";
```

- [ ] **Step 4: Typecheck**

Run: `cd donna-ui && npx tsc -b --noEmit`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/primitives/Dialog.tsx donna-ui/src/primitives/Dialog.module.css donna-ui/src/primitives/index.ts
git commit -m "feat(dialog): add size wide variant for diff/large content"
```

---

# Phase 2a — Configs track

## Task 6: Migrate ConfigFileList off AntD Menu

**Files:**
- Modify: `donna-ui/src/pages/Configs/ConfigFileList.tsx`
- Create: `donna-ui/src/pages/Configs/Configs.module.css` (shared with Task 7)

**Why:** Needed before the list page (Task 7) can use it. Also applies to the AntD Menu in PromptFileList (Task 16 — same pattern).

- [ ] **Step 1: Create shared CSS module**

```css
/* donna-ui/src/pages/Configs/Configs.module.css */
.root { display: flex; flex-direction: column; gap: var(--space-5); }

.list { display: flex; flex-direction: column; gap: 2px; }

.item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--space-2) var(--space-3);
  border: 1px solid transparent;
  border-left: 2px solid transparent;
  background: transparent;
  color: var(--color-text-secondary);
  text-decoration: none;
  font-size: var(--text-body);
  text-align: left;
  cursor: pointer;
  transition:
    color var(--duration-fast) var(--ease-out),
    border-color var(--duration-fast) var(--ease-out),
    background var(--duration-fast) var(--ease-out);
}

.item:hover { color: var(--color-text); background: var(--color-accent-soft); }
.item:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 2px;
  border-radius: var(--radius-control);
}

.itemActive {
  color: var(--color-text);
  border-left-color: var(--color-accent);
  background: var(--color-accent-soft);
}

.meta {
  font-size: var(--text-label);
  color: var(--color-text-muted);
}

.listHeader {
  font-family: var(--font-body);
  font-size: var(--text-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--color-text-muted);
  margin-bottom: var(--space-2);
}

.editorHeader {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}

.editorTitle {
  font-family: var(--font-display);
  font-weight: 300;
  font-size: var(--text-section);
  color: var(--color-text);
  margin: 0;
}

.editorSubtitle {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--color-text-muted);
  font-size: var(--text-body);
}

.warning { color: var(--color-warning); }

.invalid { color: var(--color-error); font-size: var(--text-label); }
```

- [ ] **Step 2: Rewrite ConfigFileList**

Replace `donna-ui/src/pages/Configs/ConfigFileList.tsx` entirely:

```tsx
import { Link } from "react-router-dom";
import { cn } from "../../lib/cn";
import { Skeleton } from "../../primitives/Skeleton";
import dayjs from "dayjs";
import type { ConfigFile } from "../../api/configs";
import styles from "./Configs.module.css";

interface Props {
  files: ConfigFile[];
  loading: boolean;
  selected: string | null;
}

export default function ConfigFileList({ files, loading, selected }: Props) {
  if (loading) {
    return (
      <div className={styles.list}>
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} height={36} />
        ))}
      </div>
    );
  }

  return (
    <nav className={styles.list} aria-label="Config files">
      {files.map((f) => {
        const active = f.name === selected;
        return (
          <Link
            key={f.name}
            to={`/configs/${encodeURIComponent(f.name)}`}
            className={cn(styles.item, active && styles.itemActive)}
            aria-current={active ? "page" : undefined}
          >
            <span>{f.name}</span>
            <span className={styles.meta}>
              {(f.size_bytes / 1024).toFixed(1)} KB · {dayjs(f.modified * 1000).format("MMM D")}
            </span>
          </Link>
        );
      })}
    </nav>
  );
}
```

- [ ] **Step 3: Typecheck + grep AntD**

Run: `cd donna-ui && npx tsc -b --noEmit`
Run: `grep -n "antd\|@ant-design" donna-ui/src/pages/Configs/ConfigFileList.tsx`
Expected: typecheck PASS, grep returns nothing.

- [ ] **Step 4: Commit**

```bash
git add donna-ui/src/pages/Configs/ConfigFileList.tsx donna-ui/src/pages/Configs/Configs.module.css
git commit -m "refactor(configs): strip AntD Menu from ConfigFileList; use Link-based list"
```

---

## Task 7: Split ConfigsPage into list + editor subroutes

**Files:**
- Create: `donna-ui/src/pages/Configs/ConfigsList.tsx`
- Create: `donna-ui/src/pages/Configs/ConfigEditor.tsx`
- Modify: `donna-ui/src/pages/Configs/index.tsx` (router shell)

**Why:** The AntD nested `Layout`/`Sider` is the P0 responsive failure. This task does the structural split. `SaveDiffModal` and form migrations come next.

- [ ] **Step 1: Create ConfigsList.tsx**

```tsx
import { useEffect, useState, useCallback } from "react";
import { PageHeader } from "../../primitives/PageHeader";
import { EmptyState } from "../../primitives/EmptyState";
import { fetchConfigs, type ConfigFile } from "../../api/configs";
import ConfigFileList from "./ConfigFileList";
import styles from "./Configs.module.css";

export default function ConfigsList() {
  const [files, setFiles] = useState<ConfigFile[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setFiles(await fetchConfigs());
    } catch {
      setFiles([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <div className={styles.root}>
      <PageHeader
        eyebrow="System"
        title="Configs"
        meta={loading ? "Loading…" : `${files.length} file${files.length === 1 ? "" : "s"}`}
      />
      {!loading && files.length === 0 ? (
        <EmptyState
          title="No config files"
          body="Donna reads YAML from the config/ directory. Add one and refresh."
        />
      ) : (
        <ConfigFileList files={files} loading={loading} selected={null} />
      )}
    </div>
  );
}
```

- [ ] **Step 2: Create ConfigEditor.tsx**

This is the new editor shell. It re-uses `StructuredEditor`, `RawYamlEditor`, `SaveDiffModal` from the existing tree — those get their own migrations in Tasks 8–14. For now, keep the Tabs/Save-button behavior but move to primitive `Tabs` and `Button`.

```tsx
import { useState, useEffect, useCallback } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Save } from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "../../primitives/PageHeader";
import { Button } from "../../primitives/Button";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../../primitives/Tabs";
import { Pill } from "../../primitives/Pill";
import ConfigFileList from "./ConfigFileList";
import StructuredEditor from "./StructuredEditor";
import RawYamlEditor from "./RawYamlEditor";
import SaveDiffModal from "./SaveDiffModal";
import { useYamlValidator } from "../../hooks/useYamlValidator";
import {
  fetchConfigs,
  fetchConfig,
  saveConfig,
  type ConfigFile,
} from "../../api/configs";
import yaml from "yaml";
import styles from "./Configs.module.css";

export default function ConfigEditor() {
  const { file } = useParams<{ file: string }>();
  const filename = file ? decodeURIComponent(file) : "";

  const [files, setFiles] = useState<ConfigFile[]>([]);
  const [originalContent, setOriginalContent] = useState("");
  const [editedContent, setEditedContent] = useState("");
  const [contentLoading, setContentLoading] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const [saving, setSaving] = useState(false);
  const [activeTab, setActiveTab] = useState("structured");

  const validation = useYamlValidator(editedContent);
  const parsedData = (validation.ok ? (validation.data as Record<string, unknown>) : {}) ?? {};
  const hasChanges = editedContent !== originalContent;

  const loadFiles = useCallback(async () => {
    try { setFiles(await fetchConfigs()); } catch { setFiles([]); }
  }, []);

  useEffect(() => { loadFiles(); }, [loadFiles]);

  useEffect(() => {
    if (!filename) return;
    let cancelled = false;
    setContentLoading(true);
    fetchConfig(filename)
      .then((d) => {
        if (cancelled) return;
        setOriginalContent(d.content);
        setEditedContent(d.content);
        setActiveTab("structured");
      })
      .catch(() => {
        if (cancelled) return;
        setOriginalContent("");
        setEditedContent("");
      })
      .finally(() => { if (!cancelled) setContentLoading(false); });
    return () => { cancelled = true; };
  }, [filename]);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const handleStructuredChange = (data: Record<string, any>) => {
    try { setEditedContent(yaml.stringify(data, { indent: 2 })); } catch { /* keep */ }
  };

  const handleSave = async () => {
    if (!filename) return;
    setSaving(true);
    try {
      await saveConfig(filename, editedContent);
      setOriginalContent(editedContent);
      setShowDiff(false);
      toast.success(`Saved ${filename}`);
      loadFiles();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className={styles.root}>
      <PageHeader
        eyebrow="System"
        title="Configs"
        meta={
          <Link to="/configs" className={styles.editorSubtitle}>
            <ArrowLeft size={14} /> All files
          </Link>
        }
        actions={
          <Button
            variant="primary"
            size="sm"
            disabled={!hasChanges || !validation.ok}
            onClick={() => setShowDiff(true)}
          >
            <Save size={14} /> Save
          </Button>
        }
      />

      <div className={styles.editorHeader}>
        <h2 className={styles.editorTitle}>{filename}</h2>
        <div className={styles.editorSubtitle}>
          {contentLoading && <span>Loading…</span>}
          {hasChanges && <Pill variant="warning">Unsaved</Pill>}
          {!validation.ok && validation.error && (
            <span className={styles.invalid}>
              YAML error: {validation.error.message}
            </span>
          )}
        </div>
      </div>

      <ConfigFileList files={files} loading={false} selected={filename} />

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="structured">Structured</TabsTrigger>
          <TabsTrigger value="raw">Raw YAML</TabsTrigger>
        </TabsList>
        <TabsContent value="structured">
          <StructuredEditor
            filename={filename}
            data={parsedData}
            rawYaml={editedContent}
            onDataChange={handleStructuredChange}
            onRawChange={setEditedContent}
          />
        </TabsContent>
        <TabsContent value="raw">
          <RawYamlEditor value={editedContent} onChange={setEditedContent} />
        </TabsContent>
      </Tabs>

      <SaveDiffModal
        open={showDiff}
        original={originalContent}
        modified={editedContent}
        filename={filename}
        saving={saving}
        onConfirm={handleSave}
        onCancel={() => setShowDiff(false)}
      />
    </div>
  );
}
```

- [ ] **Step 3: Turn index.tsx into a router shell**

Replace `donna-ui/src/pages/Configs/index.tsx`:

```tsx
import { useParams } from "react-router-dom";
import ConfigsList from "./ConfigsList";
import ConfigEditor from "./ConfigEditor";

export default function ConfigsPage() {
  const { file } = useParams<{ file?: string }>();
  return file ? <ConfigEditor /> : <ConfigsList />;
}
```

- [ ] **Step 4: Typecheck + lint + grep AntD**

Run: `cd donna-ui && npx tsc -b --noEmit && npm run lint`
Run: `grep -n "antd\|@ant-design" donna-ui/src/pages/Configs/index.tsx donna-ui/src/pages/Configs/ConfigEditor.tsx donna-ui/src/pages/Configs/ConfigsList.tsx`
Expected: typecheck/lint PASS, grep returns nothing.

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/pages/Configs/index.tsx donna-ui/src/pages/Configs/ConfigsList.tsx donna-ui/src/pages/Configs/ConfigEditor.tsx
git commit -m "refactor(configs): split into list + editor subroutes with primitives"
```

---

## Task 8: Migrate SaveDiffModal to primitive Dialog

**Files:**
- Modify: `donna-ui/src/pages/Configs/SaveDiffModal.tsx`

- [ ] **Step 1: Rewrite**

```tsx
import { DiffEditor } from "@monaco-editor/react";
import {
  Dialog,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "../../primitives/Dialog";
import { Button } from "../../primitives/Button";
import { DONNA_MONACO_THEME, setupDonnaMonacoTheme } from "../../lib/monacoTheme";

interface Props {
  open: boolean;
  original: string;
  modified: string;
  filename: string;
  saving: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function SaveDiffModal({
  open,
  original,
  modified,
  filename,
  saving,
  onConfirm,
  onCancel,
}: Props) {
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onCancel(); }} size="wide">
      <DialogHeader>
        <DialogTitle>Save changes to {filename}?</DialogTitle>
        <DialogDescription>
          Review the diff — left is on disk, right is your edits.
        </DialogDescription>
      </DialogHeader>

      <DiffEditor
        height="min(60vh, 480px)"
        language="yaml"
        theme={DONNA_MONACO_THEME}
        beforeMount={setupDonnaMonacoTheme}
        original={original}
        modified={modified}
        options={{
          readOnly: true,
          minimap: { enabled: false },
          fontSize: 12,
          scrollBeyondLastLine: false,
          renderSideBySide: true,
        }}
      />

      <DialogFooter>
        <Button variant="ghost" onClick={onCancel} disabled={saving}>Cancel</Button>
        <Button variant="primary" onClick={onConfirm} disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </DialogFooter>
    </Dialog>
  );
}
```

- [ ] **Step 2: Typecheck + grep AntD**

Run: `cd donna-ui && npx tsc -b --noEmit`
Run: `grep -n "antd\|@ant-design" donna-ui/src/pages/Configs/SaveDiffModal.tsx`
Expected: typecheck PASS, grep returns nothing.

- [ ] **Step 3: Commit**

```bash
git add donna-ui/src/pages/Configs/SaveDiffModal.tsx
git commit -m "refactor(configs): SaveDiffModal uses primitive Dialog + donna monaco theme"
```

---

## Task 9: RawYamlEditor on the shared Monaco theme

**Files:**
- Modify: `donna-ui/src/pages/Configs/RawYamlEditor.tsx`

- [ ] **Step 1: Rewrite**

```tsx
import Editor from "@monaco-editor/react";
import { DONNA_MONACO_THEME, setupDonnaMonacoTheme } from "../../lib/monacoTheme";

interface Props {
  value: string;
  onChange: (value: string) => void;
}

export default function RawYamlEditor({ value, onChange }: Props) {
  return (
    <Editor
      height="min(calc(100vh - 280px), 640px)"
      language="yaml"
      theme={DONNA_MONACO_THEME}
      beforeMount={setupDonnaMonacoTheme}
      value={value}
      onChange={(v) => onChange(v ?? "")}
      options={{
        minimap: { enabled: false },
        fontSize: 13,
        lineNumbers: "on",
        scrollBeyondLastLine: false,
        wordWrap: "on",
        tabSize: 2,
      }}
    />
  );
}
```

- [ ] **Step 2: Typecheck + commit**

Run: `cd donna-ui && npx tsc -b --noEmit`
Expected: PASS.

```bash
git add donna-ui/src/pages/Configs/RawYamlEditor.tsx
git commit -m "refactor(configs): RawYamlEditor uses donna monaco theme"
```

---

## Task 10: Zod schemas for the four structured config files

**Files:**
- Create: `donna-ui/src/pages/Configs/schemas.ts`

**Why:** Centralizes validation for all four forms. Each schema is loose on purpose — we validate *shape* (required keys and known types), not every business rule, to avoid blocking the user on benign YAML.

- [ ] **Step 1: Create schemas**

```ts
// donna-ui/src/pages/Configs/schemas.ts
import { z } from "zod";

// ----- task_states.yaml -----
export const stateTransitionSchema = z.object({
  from: z.string(),
  to: z.string(),
  trigger: z.string(),
  side_effects: z.array(z.string()).optional(),
});

export const statesSchema = z.object({
  initial_state: z.string().optional(),
  states: z.array(z.string()),
  transitions: z.array(stateTransitionSchema),
});
export type StatesConfig = z.infer<typeof statesSchema>;

// ----- donna_models.yaml -----
export const modelEntrySchema = z.object({
  provider: z.string(),
  model: z.string(),
  estimated_cost_per_1k_tokens: z.number().nonnegative().optional(),
});

export const routingEntrySchema = z.object({
  model: z.string(),
  fallback: z.string().optional(),
  shadow: z.string().optional(),
  confidence_threshold: z.number().min(0).max(1).optional(),
});

export const modelsSchema = z.object({
  models: z.record(z.string(), modelEntrySchema).default({}),
  routing: z.record(z.string(), routingEntrySchema).default({}),
  cost: z
    .object({
      monthly_budget_usd: z.number().nonnegative().optional(),
      daily_pause_threshold_usd: z.number().nonnegative().optional(),
      task_approval_threshold_usd: z.number().nonnegative().optional(),
      monthly_warning_pct: z.number().min(0).max(1).optional(),
    })
    .default({}),
  quality_monitoring: z
    .object({
      enabled: z.boolean().optional(),
      spot_check_rate: z.number().min(0).max(1).optional(),
      flag_threshold: z.number().min(0).max(1).optional(),
    })
    .default({}),
});
export type ModelsConfig = z.infer<typeof modelsSchema>;

// ----- task_types.yaml -----
export const taskTypeEntrySchema = z.object({
  description: z.string().optional().default(""),
  model: z.string(),
  shadow: z.string().optional(),
  prompt_template: z.string().optional().default(""),
  output_schema: z.string().optional().default(""),
  tools: z.array(z.string()).optional().default([]),
});

export const taskTypesSchema = z.object({
  task_types: z.record(z.string(), taskTypeEntrySchema).default({}),
});
export type TaskTypesConfig = z.infer<typeof taskTypesSchema>;

// ----- agents.yaml -----
export const agentEntrySchema = z.object({
  enabled: z.boolean(),
  timeout_seconds: z.number().int().min(1),
  autonomy: z.enum(["low", "medium", "high"]),
  allowed_tools: z.array(z.string()).default([]),
});

export const agentsSchema = z.object({
  agents: z.record(z.string(), agentEntrySchema).default({}),
});
export type AgentsConfig = z.infer<typeof agentsSchema>;
```

- [ ] **Step 2: Typecheck + commit**

Run: `cd donna-ui && npx tsc -b --noEmit`
Expected: PASS.

```bash
git add donna-ui/src/pages/Configs/schemas.ts
git commit -m "feat(configs): zod schemas for the 4 structured config files"
```

---

## Task 11: Migrate StatesForm (RHF + zod, central stateColors)

**Files:**
- Modify: `donna-ui/src/pages/Configs/forms/StatesForm.tsx`

**Notes:**
- Delete the local `STATE_COLORS` const. Use `stateCssVar` for SVG fills and `statePillVariant` + `Pill` for tags.
- Delete AntD imports (`Card`, `Table`, `Tag`, `Space`).
- `StatesForm` is effectively read-only; RHF's job here is *validation* of the data shape. Wire with `useForm({ values: data, resolver: zodResolver(statesSchema), mode: "onChange" })` and display errors inline. No `watch -> onChange` (form is non-editable).

- [ ] **Step 1: Rewrite**

```tsx
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Card, CardHeader, CardTitle } from "../../../primitives/Card";
import { Pill } from "../../../primitives/Pill";
import { DataTable } from "../../../primitives/DataTable";
import { stateCssVar, statePillVariant } from "../../../theme/stateColors";
import { statesSchema, type StatesConfig } from "../schemas";

/* eslint-disable @typescript-eslint/no-explicit-any */

interface Props {
  data: Record<string, any>;
  onChange: (data: Record<string, any>) => void;
}

const STATE_POSITIONS: Record<string, { x: number; y: number }> = {
  backlog: { x: 80, y: 140 },
  scheduled: { x: 250, y: 60 },
  in_progress: { x: 420, y: 140 },
  blocked: { x: 420, y: 260 },
  waiting_input: { x: 250, y: 260 },
  done: { x: 590, y: 60 },
  cancelled: { x: 590, y: 260 },
};

interface Transition {
  from: string;
  to: string;
  trigger: string;
  side_effects?: string[];
}

function StateMachineDiagram({ transitions }: { transitions: Transition[] }) {
  const getTransitionPath = (from: string, to: string): string => {
    const start = STATE_POSITIONS[from];
    const end = STATE_POSITIONS[to];
    if (!start || !end) return "";
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    const dist = Math.sqrt(dx * dx + dy * dy);
    const nodeR = 30;
    const sx = start.x + (dx / dist) * nodeR;
    const sy = start.y + (dy / dist) * nodeR;
    const ex = end.x - (dx / dist) * (nodeR + 6);
    const ey = end.y - (dy / dist) * (nodeR + 6);
    const midX = (sx + ex) / 2;
    const midY = (sy + ey) / 2;
    const perpX = -(ey - sy) * 0.15;
    const perpY = (ex - sx) * 0.15;
    return `M ${sx} ${sy} Q ${midX + perpX} ${midY + perpY} ${ex} ${ey}`;
  };

  const regularTransitions = transitions.filter((t) => t.from !== "*");

  return (
    <svg
      width="100%"
      viewBox="0 0 700 320"
      role="img"
      aria-label="State machine transition diagram"
      style={{ display: "block", maxWidth: 700, margin: "0 auto" }}
    >
      <defs>
        <marker id="arrowhead" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
          <polygon points="0 0, 8 3, 0 6" fill="var(--color-text-muted)" />
        </marker>
      </defs>

      {regularTransitions.map((t, i) => {
        const path = getTransitionPath(t.from, t.to);
        if (!path) return null;
        return (
          <path
            key={i}
            d={path}
            fill="none"
            stroke="var(--color-border)"
            strokeWidth={1.5}
            markerEnd="url(#arrowhead)"
          />
        );
      })}

      {Object.entries(STATE_POSITIONS).map(([state, pos]) => (
        <g key={state}>
          <circle cx={pos.x} cy={pos.y} r={30} fill={stateCssVar(state)} opacity={0.85} />
          <text
            x={pos.x}
            y={pos.y + 1}
            textAnchor="middle"
            dominantBaseline="middle"
            fill="var(--color-inset)"
            fontSize={10}
            fontWeight={600}
          >
            {state.replace("_", " ")}
          </text>
        </g>
      ))}

      <text x={10} y={310} fill="var(--color-text-muted)" fontSize={10}>
        * = any state can transition to cancelled
      </text>
    </svg>
  );
}

export default function StatesForm({ data }: Props) {
  const {
    formState: { errors },
  } = useForm<StatesConfig>({
    values: data as StatesConfig,
    resolver: zodResolver(statesSchema),
    mode: "onChange",
  });

  const states: string[] = (data.states as string[]) ?? [];
  const transitions: Transition[] = (data.transitions as Transition[]) ?? [];

  const topError = errors.root?.message ?? Object.values(errors)[0]?.message;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      {topError && (
        <div role="alert" style={{ color: "var(--color-error)", fontSize: "var(--text-body)" }}>
          Schema error: {String(topError)}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>State machine</CardTitle>
        </CardHeader>
        <StateMachineDiagram transitions={transitions} />
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>States</CardTitle>
        </CardHeader>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)" }}>
          {states.map((s) => (
            <Pill key={s} variant={statePillVariant(s)}>{s}</Pill>
          ))}
        </div>
        <div style={{ marginTop: "var(--space-3)", fontSize: "var(--text-label)", color: "var(--color-text-muted)" }}>
          Initial state: <strong>{data.initial_state ?? "backlog"}</strong>
        </div>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Transitions</CardTitle>
        </CardHeader>
        <DataTable
          data={transitions}
          columns={[
            {
              header: "From",
              accessorKey: "from",
              cell: ({ getValue }) => {
                const v = getValue() as string;
                return v === "*"
                  ? <Pill variant="muted">*</Pill>
                  : <Pill variant={statePillVariant(v)}>{v}</Pill>;
              },
            },
            {
              header: "To",
              accessorKey: "to",
              cell: ({ getValue }) => {
                const v = getValue() as string;
                return <Pill variant={statePillVariant(v)}>{v}</Pill>;
              },
            },
            {
              header: "Trigger",
              accessorKey: "trigger",
              cell: ({ getValue }) => (
                <code style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>
                  {String(getValue() ?? "")}
                </code>
              ),
            },
            {
              header: "Side effects",
              accessorKey: "side_effects",
              cell: ({ getValue }) => {
                const effects = (getValue() as string[] | undefined) ?? [];
                if (effects.length === 0) return "—";
                return (
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                    {effects.map((e, i) => <Pill key={i} variant="muted">{e}</Pill>)}
                  </div>
                );
              },
            },
          ]}
        />
      </Card>
    </div>
  );
}
```

- [ ] **Step 2: Verify no AntD, no STATE_COLORS**

Run: `grep -n "antd\|@ant-design\|STATE_COLORS" donna-ui/src/pages/Configs/forms/StatesForm.tsx`
Expected: no matches.

- [ ] **Step 3: Typecheck**

Run: `cd donna-ui && npx tsc -b --noEmit`
Expected: PASS. If `DataTable`'s generic signature complains, pin the type: `DataTable<Transition>`.

- [ ] **Step 4: Commit**

```bash
git add donna-ui/src/pages/Configs/forms/StatesForm.tsx
git commit -m "refactor(configs): StatesForm on RHF+zod + central stateColors"
```

---

## Task 12: Migrate ModelsForm (RHF + zod)

**Files:**
- Modify: `donna-ui/src/pages/Configs/forms/ModelsForm.tsx`

**Pattern:** interactive fields — wire `useForm` with `zodResolver(modelsSchema)` and use `watch` to sync changes back to parent. Fields use primitive `Input`/`Select`/`Switch`.

- [ ] **Step 1: Rewrite**

```tsx
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Card, CardHeader, CardTitle } from "../../../primitives/Card";
import { Input, FormField } from "../../../primitives/Input";
import { Switch } from "../../../primitives/Switch";
import { DataTable } from "../../../primitives/DataTable";
import { modelsSchema, type ModelsConfig } from "../schemas";

/* eslint-disable @typescript-eslint/no-explicit-any */

interface Props {
  data: Record<string, any>;
  onChange: (data: Record<string, any>) => void;
}

export default function ModelsForm({ data, onChange }: Props) {
  const form = useForm<ModelsConfig>({
    values: data as ModelsConfig,
    resolver: zodResolver(modelsSchema),
    mode: "onChange",
  });

  // Sync form state -> parent on every valid change.
  useEffect(() => {
    const sub = form.watch((values) => {
      onChange(values as Record<string, any>);
    });
    return () => sub.unsubscribe();
  }, [form, onChange]);

  const models = form.watch("models") ?? {};
  const routing = form.watch("routing") ?? {};

  const modelRows = Object.entries(models).map(([alias, cfg]) => ({
    alias,
    provider: cfg?.provider ?? "",
    model: cfg?.model ?? "",
  }));

  const routingRows = Object.entries(routing).map(([taskType, cfg]) => ({
    task_type: taskType,
    model: cfg?.model ?? "",
    fallback: cfg?.fallback ?? "",
    shadow: cfg?.shadow ?? "",
    confidence_threshold: cfg?.confidence_threshold,
  }));

  const topError = form.formState.errors.root?.message;

  return (
    <form
      style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}
      onSubmit={(e) => e.preventDefault()}
    >
      {topError && (
        <div role="alert" style={{ color: "var(--color-error)" }}>
          Schema error: {String(topError)}
        </div>
      )}

      <Card>
        <CardHeader><CardTitle>Model definitions</CardTitle></CardHeader>
        <DataTable
          data={modelRows}
          columns={[
            { header: "Alias", accessorKey: "alias" },
            {
              header: "Provider",
              accessorKey: "provider",
              cell: ({ row }) => (
                <Input
                  {...form.register(`models.${row.original.alias}.provider` as const)}
                />
              ),
            },
            {
              header: "Model",
              accessorKey: "model",
              cell: ({ row }) => (
                <Input
                  {...form.register(`models.${row.original.alias}.model` as const)}
                />
              ),
            },
          ]}
        />
      </Card>

      <Card>
        <CardHeader><CardTitle>Routing table</CardTitle></CardHeader>
        <DataTable
          data={routingRows}
          columns={[
            { header: "Task type", accessorKey: "task_type" },
            {
              header: "Model",
              accessorKey: "model",
              cell: ({ row }) => (
                <Input {...form.register(`routing.${row.original.task_type}.model` as const)} />
              ),
            },
            {
              header: "Fallback",
              accessorKey: "fallback",
              cell: ({ row }) => (
                <Input {...form.register(`routing.${row.original.task_type}.fallback` as const)} />
              ),
            },
            {
              header: "Shadow",
              accessorKey: "shadow",
              cell: ({ row }) => (
                <Input {...form.register(`routing.${row.original.task_type}.shadow` as const)} />
              ),
            },
            {
              header: "Threshold",
              accessorKey: "confidence_threshold",
              cell: ({ row }) => (
                <Input
                  type="number"
                  step={0.1}
                  min={0}
                  max={1}
                  {...form.register(
                    `routing.${row.original.task_type}.confidence_threshold` as const,
                    { valueAsNumber: true },
                  )}
                />
              ),
            },
          ]}
        />
      </Card>

      <Card>
        <CardHeader><CardTitle>Cost tracking</CardTitle></CardHeader>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: "var(--space-3)" }}>
          <FormField label="Monthly budget ($)">
            <Input type="number" step={10} min={0} {...form.register("cost.monthly_budget_usd", { valueAsNumber: true })} />
          </FormField>
          <FormField label="Daily pause ($)">
            <Input type="number" step={5} min={0} {...form.register("cost.daily_pause_threshold_usd", { valueAsNumber: true })} />
          </FormField>
          <FormField label="Task approval ($)">
            <Input type="number" step={1} min={0} {...form.register("cost.task_approval_threshold_usd", { valueAsNumber: true })} />
          </FormField>
          <FormField label="Warning %">
            <Input type="number" step={0.05} min={0} max={1} {...form.register("cost.monthly_warning_pct", { valueAsNumber: true })} />
          </FormField>
        </div>
      </Card>

      <Card>
        <CardHeader><CardTitle>Quality monitoring</CardTitle></CardHeader>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-4)", alignItems: "end" }}>
          <FormField label="Enabled">
            <Switch
              checked={!!form.watch("quality_monitoring.enabled")}
              onCheckedChange={(v) => form.setValue("quality_monitoring.enabled", v, { shouldDirty: true })}
            />
          </FormField>
          <FormField label="Spot check rate">
            <Input type="number" step={0.01} min={0} max={1} {...form.register("quality_monitoring.spot_check_rate", { valueAsNumber: true })} />
          </FormField>
          <FormField label="Flag threshold">
            <Input type="number" step={0.1} min={0} max={1} {...form.register("quality_monitoring.flag_threshold", { valueAsNumber: true })} />
          </FormField>
        </div>
      </Card>
    </form>
  );
}
```

> **Note on `FormField`**: `src/primitives/Input.tsx` exports a `FormField` helper. If its API differs from `<FormField label>`, adapt to whatever props it accepts (read the source first — this is a reasonable 2-minute adjustment).

- [ ] **Step 2: Typecheck + grep**

Run: `cd donna-ui && npx tsc -b --noEmit`
Run: `grep -n "antd\|@ant-design" donna-ui/src/pages/Configs/forms/ModelsForm.tsx`
Expected: typecheck PASS, grep empty.

- [ ] **Step 3: Commit**

```bash
git add donna-ui/src/pages/Configs/forms/ModelsForm.tsx
git commit -m "refactor(configs): ModelsForm on RHF+zod + primitives"
```

---

## Task 13: Migrate TaskTypesForm (RHF + zod)

**Files:**
- Modify: `donna-ui/src/pages/Configs/forms/TaskTypesForm.tsx`

**Pattern:** Collapsible sections per task type. Use the primitive `Card` for each entry (no primitive Collapse exists — `<details>`/`<summary>` is fine). `Select` comes from `primitives/Select`.

- [ ] **Step 1: Rewrite**

```tsx
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Card } from "../../../primitives/Card";
import { Input, FormField } from "../../../primitives/Input";
import { Select, SelectItem } from "../../../primitives/Select";
import { Pill } from "../../../primitives/Pill";
import { taskTypesSchema, type TaskTypesConfig } from "../schemas";

/* eslint-disable @typescript-eslint/no-explicit-any */

interface Props {
  data: Record<string, any>;
  onChange: (data: Record<string, any>) => void;
}

const MODEL_OPTIONS = ["parser", "reasoner", "fallback", "local_parser"];

export default function TaskTypesForm({ data, onChange }: Props) {
  const form = useForm<TaskTypesConfig>({
    values: data as TaskTypesConfig,
    resolver: zodResolver(taskTypesSchema),
    mode: "onChange",
  });

  useEffect(() => {
    const sub = form.watch((values) => onChange(values as Record<string, any>));
    return () => sub.unsubscribe();
  }, [form, onChange]);

  const taskTypes = form.watch("task_types") ?? {};

  return (
    <form
      onSubmit={(e) => e.preventDefault()}
      style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}
    >
      {Object.entries(taskTypes).map(([name, cfg]) => (
        <Card key={name}>
          <details open>
            <summary style={{ cursor: "pointer", fontSize: "var(--text-body)" }}>
              <strong>{name}</strong>{" "}
              <Pill variant="accent">{cfg?.model ?? "—"}</Pill>
            </summary>

            <div style={{ display: "grid", gap: "var(--space-3)", marginTop: "var(--space-3)" }}>
              <FormField label="Description">
                <Input {...form.register(`task_types.${name}.description` as const)} />
              </FormField>

              <FormField label="Model">
                <Select
                  value={form.watch(`task_types.${name}.model`) ?? ""}
                  onValueChange={(v) =>
                    form.setValue(`task_types.${name}.model`, v, { shouldDirty: true })
                  }
                >
                  {MODEL_OPTIONS.map((m) => (
                    <SelectItem key={m} value={m}>{m}</SelectItem>
                  ))}
                </Select>
              </FormField>

              <FormField label="Shadow model">
                <Select
                  value={form.watch(`task_types.${name}.shadow`) ?? ""}
                  onValueChange={(v) =>
                    form.setValue(
                      `task_types.${name}.shadow`,
                      v === "" ? undefined : v,
                      { shouldDirty: true },
                    )
                  }
                >
                  <SelectItem value="">(none)</SelectItem>
                  {MODEL_OPTIONS.map((m) => (
                    <SelectItem key={m} value={m}>{m}</SelectItem>
                  ))}
                </Select>
              </FormField>

              <FormField label="Prompt template">
                <Input {...form.register(`task_types.${name}.prompt_template` as const)} />
              </FormField>

              <FormField label="Output schema">
                <Input {...form.register(`task_types.${name}.output_schema` as const)} />
              </FormField>

              <FormField label="Tools (comma-separated)">
                <Input
                  value={(form.watch(`task_types.${name}.tools`) ?? []).join(", ")}
                  onChange={(e) =>
                    form.setValue(
                      `task_types.${name}.tools`,
                      e.target.value
                        .split(",")
                        .map((s) => s.trim())
                        .filter(Boolean),
                      { shouldDirty: true },
                    )
                  }
                />
              </FormField>
            </div>
          </details>
        </Card>
      ))}
    </form>
  );
}
```

> **Note on Select API**: if `primitives/Select` expects different props than shown (e.g. `options` instead of children), adapt. Read `src/primitives/Select.tsx` first.

- [ ] **Step 2: Typecheck + grep**

Run: `cd donna-ui && npx tsc -b --noEmit && grep -n "antd\|@ant-design" donna-ui/src/pages/Configs/forms/TaskTypesForm.tsx`
Expected: typecheck PASS, grep empty.

- [ ] **Step 3: Commit**

```bash
git add donna-ui/src/pages/Configs/forms/TaskTypesForm.tsx
git commit -m "refactor(configs): TaskTypesForm on RHF+zod + primitives"
```

---

## Task 14: Migrate AgentsForm (RHF + zod)

**Files:**
- Modify: `donna-ui/src/pages/Configs/forms/AgentsForm.tsx`

- [ ] **Step 1: Rewrite**

```tsx
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Card, CardHeader, CardTitle } from "../../../primitives/Card";
import { Input, FormField } from "../../../primitives/Input";
import { Select, SelectItem } from "../../../primitives/Select";
import { Switch } from "../../../primitives/Switch";
import { Checkbox } from "../../../primitives/Checkbox";
import { agentsSchema, type AgentsConfig } from "../schemas";

/* eslint-disable @typescript-eslint/no-explicit-any */

interface Props {
  data: Record<string, any>;
  onChange: (data: Record<string, any>) => void;
}

const ALL_TOOLS = [
  "task_db_read", "task_db_write", "calendar_read", "calendar_write",
  "web_search", "email_read", "email_draft", "notes_read",
  "fs_read", "fs_write", "github_read", "github_write",
  "docs_write", "discord_write", "cost_summary",
];

export default function AgentsForm({ data, onChange }: Props) {
  const form = useForm<AgentsConfig>({
    values: data as AgentsConfig,
    resolver: zodResolver(agentsSchema),
    mode: "onChange",
  });

  useEffect(() => {
    const sub = form.watch((values) => onChange(values as Record<string, any>));
    return () => sub.unsubscribe();
  }, [form, onChange]);

  const agents = form.watch("agents") ?? {};

  return (
    <form
      onSubmit={(e) => e.preventDefault()}
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
        gap: "var(--space-4)",
      }}
    >
      {Object.entries(agents).map(([name, cfg]) => {
        const selectedTools = new Set(cfg?.allowed_tools ?? []);
        return (
          <Card key={name}>
            <CardHeader>
              <CardTitle style={{ textTransform: "capitalize" }}>{name}</CardTitle>
              <Switch
                checked={!!cfg?.enabled}
                onCheckedChange={(v) =>
                  form.setValue(`agents.${name}.enabled`, v, { shouldDirty: true })
                }
                aria-label={`Enable ${name} agent`}
              />
            </CardHeader>

            <div style={{ display: "grid", gap: "var(--space-3)" }}>
              <FormField label="Timeout (seconds)">
                <Input
                  type="number"
                  min={10}
                  max={3600}
                  {...form.register(`agents.${name}.timeout_seconds` as const, {
                    valueAsNumber: true,
                  })}
                />
              </FormField>

              <FormField label="Autonomy level">
                <Select
                  value={cfg?.autonomy ?? "low"}
                  onValueChange={(v) =>
                    form.setValue(
                      `agents.${name}.autonomy`,
                      v as "low" | "medium" | "high",
                      { shouldDirty: true },
                    )
                  }
                >
                  <SelectItem value="low">Low</SelectItem>
                  <SelectItem value="medium">Medium</SelectItem>
                  <SelectItem value="high">High</SelectItem>
                </Select>
              </FormField>

              <FormField label="Allowed tools">
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
                    gap: "var(--space-2)",
                  }}
                >
                  {ALL_TOOLS.map((tool) => {
                    const checked = selectedTools.has(tool);
                    return (
                      <label
                        key={tool}
                        style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}
                      >
                        <Checkbox
                          checked={checked}
                          onCheckedChange={(v) => {
                            const next = new Set(selectedTools);
                            if (v) next.add(tool); else next.delete(tool);
                            form.setValue(
                              `agents.${name}.allowed_tools`,
                              Array.from(next),
                              { shouldDirty: true },
                            );
                          }}
                        />
                        {tool}
                      </label>
                    );
                  })}
                </div>
              </FormField>
            </div>
          </Card>
        );
      })}
    </form>
  );
}
```

- [ ] **Step 2: Typecheck + grep**

Run: `cd donna-ui && npx tsc -b --noEmit && grep -n "antd\|@ant-design" donna-ui/src/pages/Configs/forms/AgentsForm.tsx`
Expected: typecheck PASS, grep empty.

- [ ] **Step 3: Commit**

```bash
git add donna-ui/src/pages/Configs/forms/AgentsForm.tsx
git commit -m "refactor(configs): AgentsForm on RHF+zod + primitives"
```

---

# Phase 2b — Prompts track

## Task 15: Rewrite MarkdownPreview (SECURITY — XSS fix)

**Files:**
- Modify: `donna-ui/src/pages/Prompts/MarkdownPreview.tsx`
- Create: `donna-ui/src/pages/Prompts/MarkdownPreview.module.css`
- Modify: `donna-ui/src/main.tsx`

**Why this is a security task:** The current implementation (read `donna-ui/src/pages/Prompts/MarkdownPreview.tsx` before starting) concatenates regex-matched HTML strings and feeds them to React's raw-HTML injection prop (the one whose name starts with `dangerously`). The escaping is positional, brittle, and any future edit that reorders the regex passes or adds a new one (e.g. links) could introduce a live XSS vector. We delete the entire approach and replace it with `react-markdown` — which never uses that prop — plus `rehype-sanitize` as a defense-in-depth guard for anything that slips through.

**Test strategy:** The regression test lives in `tests/e2e/smoke/prompts.spec.ts` (Task 21) and is part of the same wave. The test loads an `<img onerror>` payload into the preview and asserts the preview contains the literal text but no executed JS. **Write the test BEFORE this task is reviewed complete.**

- [ ] **Step 1: Replace MarkdownPreview entirely**

```tsx
import { memo } from "react";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import rehypeHighlight from "rehype-highlight";
import styles from "./MarkdownPreview.module.css";

interface Props {
  content: string;
}

// Lock sanitization to defaultSchema. defaultSchema already disallows raw
// <script>, <iframe>, and on* handler attributes — we explicitly re-declare
// the choice here so a future diff that touches the schema is obvious.
const SANITIZE_SCHEMA = defaultSchema;

function MarkdownPreviewImpl({ content }: Props) {
  return (
    <div className={styles.root}>
      <ReactMarkdown
        rehypePlugins={[[rehypeSanitize, SANITIZE_SCHEMA], rehypeHighlight]}
        components={{
          // Highlight template variables: render `{{ foo }}` as an inline pill.
          // Runs on text nodes only — the regex has no HTML capture, so it
          // cannot reintroduce the old injection surface.
          p: ({ children }) => <p>{highlightVariables(children)}</p>,
          li: ({ children }) => <li>{highlightVariables(children)}</li>,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

function highlightVariables(children: ReactNode): ReactNode {
  if (typeof children === "string") return splitVars(children);
  if (Array.isArray(children)) {
    return children.map((c, i) =>
      typeof c === "string"
        ? <span key={i}>{splitVars(c)}</span>
        : c,
    );
  }
  return children;
}

function splitVars(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const regex = /\{\{\s*(\w+)\s*\}\}/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let i = 0;
  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) out.push(text.slice(lastIndex, match.index));
    out.push(
      <span key={`v-${i++}`} className={styles.variable}>
        {"{{ "}{match[1]}{" }}"}
      </span>,
    );
    lastIndex = regex.lastIndex;
  }
  if (lastIndex < text.length) out.push(text.slice(lastIndex));
  return out;
}

export default memo(MarkdownPreviewImpl);
```

- [ ] **Step 2: Add the stylesheet**

Create `donna-ui/src/pages/Prompts/MarkdownPreview.module.css`:

```css
.root {
  padding: var(--space-4);
  background: var(--color-inset);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-card);
  overflow: auto;
  max-height: 60vh;
  font-size: var(--text-body);
  line-height: var(--leading-normal);
  color: var(--color-text);
}

.root :global(h1),
.root :global(h2),
.root :global(h3),
.root :global(h4) {
  font-family: var(--font-display);
  font-weight: 300;
  color: var(--color-text);
  margin: var(--space-3) 0 var(--space-2);
  line-height: var(--leading-snug);
}

.root :global(h1) { font-size: 22px; }
.root :global(h2) { font-size: 18px; }
.root :global(h3) { font-size: 15px; }
.root :global(h4) { font-size: 13px; }

.root :global(p) { margin: 0 0 var(--space-2); }

.root :global(code) {
  font-family: var(--font-mono);
  font-size: 12px;
  background: var(--color-surface);
  padding: 1px 4px;
  border-radius: var(--radius-control);
}

.root :global(pre) {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-control);
  padding: var(--space-3);
  overflow-x: auto;
}

.root :global(pre code) {
  background: transparent;
  padding: 0;
}

.root :global(ul),
.root :global(ol) {
  margin: 0 0 var(--space-2) 20px;
  padding: 0;
}

.root :global(hr) {
  border: none;
  border-top: 1px solid var(--color-border);
  margin: var(--space-3) 0;
}

.variable {
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent-border);
  color: var(--color-accent);
  padding: 1px 6px;
  border-radius: var(--radius-control);
  font-family: var(--font-mono);
  font-size: 11px;
}
```

- [ ] **Step 3: Import highlight.js stylesheet**

Add to `donna-ui/src/main.tsx` (check the existing imports first — put this next to the other global stylesheet imports):

```ts
import "highlight.js/styles/github-dark.css";
```

If the import fails because `highlight.js` is not a direct dependency, skip this step — `rehype-highlight` pulls it in transitively. In that case, code-block syntax colors won't render (acceptable for MVP) but rendering still works.

- [ ] **Step 4: Grep for regression**

Run: `grep -n "dangerously" donna-ui/src/pages/Prompts/MarkdownPreview.tsx`
Expected: **no matches**. This is the core security assertion — the old raw-HTML prop usage must be gone.

- [ ] **Step 5: Typecheck**

Run: `cd donna-ui && npx tsc -b --noEmit`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add donna-ui/src/pages/Prompts/MarkdownPreview.tsx donna-ui/src/pages/Prompts/MarkdownPreview.module.css donna-ui/src/main.tsx
git commit -m "security(prompts): replace regex HTML injection with react-markdown+sanitize"
```

---

## Task 16: Migrate PromptFileList off AntD Menu

**Files:**
- Modify: `donna-ui/src/pages/Prompts/PromptFileList.tsx`
- Create: `donna-ui/src/pages/Prompts/Prompts.module.css`

- [ ] **Step 1: Create CSS module**

```css
/* donna-ui/src/pages/Prompts/Prompts.module.css */
.root { display: flex; flex-direction: column; gap: var(--space-5); }

.list { display: flex; flex-direction: column; gap: 2px; }

.item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--space-2) var(--space-3);
  border-left: 2px solid transparent;
  color: var(--color-text-secondary);
  text-decoration: none;
  font-size: var(--text-body);
  cursor: pointer;
  transition:
    color var(--duration-fast) var(--ease-out),
    border-color var(--duration-fast) var(--ease-out),
    background var(--duration-fast) var(--ease-out);
}

.item:hover { color: var(--color-text); background: var(--color-accent-soft); }
.item:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 2px;
}
.itemActive {
  color: var(--color-text);
  border-left-color: var(--color-accent);
  background: var(--color-accent-soft);
}

.meta {
  font-size: var(--text-label);
  color: var(--color-text-muted);
}

.editorHeader {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}

.editorTitle {
  font-family: var(--font-display);
  font-weight: 300;
  font-size: var(--text-section);
  color: var(--color-text);
  margin: 0;
}

.editorGrid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: var(--space-4);
}

@media (max-width: 900px) {
  .editorGrid { grid-template-columns: 1fr; }
}

.invalid { color: var(--color-error); font-size: var(--text-label); }
```

- [ ] **Step 2: Rewrite PromptFileList**

```tsx
import { Link } from "react-router-dom";
import dayjs from "dayjs";
import { cn } from "../../lib/cn";
import { Skeleton } from "../../primitives/Skeleton";
import type { PromptFile } from "../../api/configs";
import styles from "./Prompts.module.css";

interface Props {
  files: PromptFile[];
  loading: boolean;
  selected: string | null;
}

export default function PromptFileList({ files, loading, selected }: Props) {
  if (loading) {
    return (
      <div className={styles.list}>
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} height={36} />
        ))}
      </div>
    );
  }

  return (
    <nav className={styles.list} aria-label="Prompt templates">
      {files.map((f) => {
        const active = f.name === selected;
        return (
          <Link
            key={f.name}
            to={`/prompts/${encodeURIComponent(f.name)}`}
            className={cn(styles.item, active && styles.itemActive)}
            aria-current={active ? "page" : undefined}
          >
            <span>{f.name.replace(".md", "")}</span>
            <span className={styles.meta}>
              {(f.size_bytes / 1024).toFixed(1)} KB · {dayjs(f.modified * 1000).format("MMM D")}
            </span>
          </Link>
        );
      })}
    </nav>
  );
}
```

- [ ] **Step 3: Typecheck + grep AntD**

Run: `cd donna-ui && npx tsc -b --noEmit && grep -n "antd\|@ant-design" donna-ui/src/pages/Prompts/PromptFileList.tsx`
Expected: typecheck PASS, grep empty.

- [ ] **Step 4: Commit**

```bash
git add donna-ui/src/pages/Prompts/PromptFileList.tsx donna-ui/src/pages/Prompts/Prompts.module.css
git commit -m "refactor(prompts): strip AntD Menu from PromptFileList"
```

---

## Task 17: Migrate VariableInspector off AntD

**Files:**
- Modify: `donna-ui/src/pages/Prompts/VariableInspector.tsx`

- [ ] **Step 1: Rewrite**

```tsx
import { Card, CardHeader, CardTitle } from "../../primitives/Card";
import { Pill } from "../../primitives/Pill";

interface Props {
  content: string;
  schemaPath: string | null;
}

export default function VariableInspector({ content, schemaPath }: Props) {
  const matches = content.match(/\{\{\s*(\w+)\s*\}\}/g) ?? [];
  const variables = [...new Set(matches.map((m) => m.replace(/[{}\s]/g, "")))];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Template variables</CardTitle>
        {schemaPath && <Pill variant="accent">{schemaPath}</Pill>}
      </CardHeader>
      {variables.length === 0 ? (
        <div style={{ color: "var(--color-text-muted)", fontSize: "var(--text-body)" }}>
          No template variables found.
        </div>
      ) : (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {variables.map((v) => (
            <Pill key={v} variant="muted">{`{{ ${v} }}`}</Pill>
          ))}
        </div>
      )}
    </Card>
  );
}
```

- [ ] **Step 2: Typecheck + grep AntD**

Run: `cd donna-ui && npx tsc -b --noEmit && grep -n "antd\|@ant-design" donna-ui/src/pages/Prompts/VariableInspector.tsx`
Expected: typecheck PASS, grep empty.

- [ ] **Step 3: Commit**

```bash
git add donna-ui/src/pages/Prompts/VariableInspector.tsx
git commit -m "refactor(prompts): VariableInspector on primitives"
```

---

## Task 18: Split PromptsPage into list + editor with Tabs

**Files:**
- Create: `donna-ui/src/pages/Prompts/PromptsList.tsx`
- Create: `donna-ui/src/pages/Prompts/PromptEditor.tsx`
- Modify: `donna-ui/src/pages/Prompts/index.tsx`

- [ ] **Step 1: PromptsList.tsx**

```tsx
import { useEffect, useState, useCallback } from "react";
import { PageHeader } from "../../primitives/PageHeader";
import { EmptyState } from "../../primitives/EmptyState";
import { fetchPrompts, type PromptFile } from "../../api/configs";
import PromptFileList from "./PromptFileList";
import styles from "./Prompts.module.css";

export default function PromptsList() {
  const [files, setFiles] = useState<PromptFile[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try { setFiles(await fetchPrompts()); } catch { setFiles([]); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <div className={styles.root}>
      <PageHeader
        eyebrow="System"
        title="Prompts"
        meta={loading ? "Loading…" : `${files.length} template${files.length === 1 ? "" : "s"}`}
      />
      {!loading && files.length === 0 ? (
        <EmptyState
          title="No prompt templates"
          body="Donna reads prompt templates from prompts/. Add one and refresh."
        />
      ) : (
        <PromptFileList files={files} loading={loading} selected={null} />
      )}
    </div>
  );
}
```

- [ ] **Step 2: PromptEditor.tsx with Edit/Preview/Split tabs**

```tsx
import { useState, useEffect, useCallback } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Save } from "lucide-react";
import Editor from "@monaco-editor/react";
import { toast } from "sonner";
import { PageHeader } from "../../primitives/PageHeader";
import { Button } from "../../primitives/Button";
import { Pill } from "../../primitives/Pill";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../../primitives/Tabs";
import { DONNA_MONACO_THEME, setupDonnaMonacoTheme } from "../../lib/monacoTheme";
import PromptFileList from "./PromptFileList";
import MarkdownPreview from "./MarkdownPreview";
import VariableInspector from "./VariableInspector";
import SaveDiffModal from "../Configs/SaveDiffModal";
import {
  fetchPrompts,
  fetchPrompt,
  savePrompt,
  fetchConfigs,
  fetchConfig,
  type PromptFile,
} from "../../api/configs";
import styles from "./Prompts.module.css";

function useSchemaMap() {
  const [map, setMap] = useState<Record<string, string>>({});
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const configs = await fetchConfigs();
        if (!configs.some((c) => c.name === "task_types.yaml")) return;
        const data = await fetchConfig("task_types.yaml");
        const next: Record<string, string> = {};
        let currentPrompt = "";
        for (const line of data.content.split("\n")) {
          const p = line.match(/prompt_template:\s*(.+)/);
          const s = line.match(/output_schema:\s*(.+)/);
          if (p) currentPrompt = p[1].trim().split("/").pop() ?? "";
          if (s && currentPrompt) {
            next[currentPrompt] = s[1].trim();
            currentPrompt = "";
          }
        }
        if (!cancelled) setMap(next);
      } catch { /* non-critical */ }
    })();
    return () => { cancelled = true; };
  }, []);
  return map;
}

export default function PromptEditor() {
  const { file } = useParams<{ file: string }>();
  const filename = file ? decodeURIComponent(file) : "";

  const [files, setFiles] = useState<PromptFile[]>([]);
  const [originalContent, setOriginalContent] = useState("");
  const [editedContent, setEditedContent] = useState("");
  const [contentLoading, setContentLoading] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const [saving, setSaving] = useState(false);
  const [view, setView] = useState<"edit" | "preview" | "split">("split");
  const schemaMap = useSchemaMap();
  const hasChanges = editedContent !== originalContent;

  const loadFiles = useCallback(async () => {
    try { setFiles(await fetchPrompts()); } catch { setFiles([]); }
  }, []);
  useEffect(() => { loadFiles(); }, [loadFiles]);

  useEffect(() => {
    if (!filename) return;
    let cancelled = false;
    setContentLoading(true);
    fetchPrompt(filename)
      .then((d) => {
        if (cancelled) return;
        setOriginalContent(d.content);
        setEditedContent(d.content);
      })
      .catch(() => {
        if (cancelled) return;
        setOriginalContent("");
        setEditedContent("");
      })
      .finally(() => { if (!cancelled) setContentLoading(false); });
    return () => { cancelled = true; };
  }, [filename]);

  const handleSave = async () => {
    if (!filename) return;
    setSaving(true);
    try {
      await savePrompt(filename, editedContent);
      setOriginalContent(editedContent);
      setShowDiff(false);
      toast.success(`Saved ${filename}`);
      loadFiles();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Save failed");
    } finally { setSaving(false); }
  };

  const linkedSchema = filename ? schemaMap[filename] ?? null : null;

  const editorEl = (
    <Editor
      height="min(60vh, 560px)"
      language="markdown"
      theme={DONNA_MONACO_THEME}
      beforeMount={setupDonnaMonacoTheme}
      value={editedContent}
      onChange={(v) => setEditedContent(v ?? "")}
      options={{
        minimap: { enabled: false },
        fontSize: 13,
        lineNumbers: "on",
        scrollBeyondLastLine: false,
        wordWrap: "on",
        tabSize: 2,
      }}
    />
  );

  return (
    <div className={styles.root}>
      <PageHeader
        eyebrow="System"
        title="Prompts"
        meta={
          <Link to="/prompts" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <ArrowLeft size={14} /> All templates
          </Link>
        }
        actions={
          <Button
            variant="primary"
            size="sm"
            disabled={!hasChanges}
            onClick={() => setShowDiff(true)}
          >
            <Save size={14} /> Save
          </Button>
        }
      />

      <div className={styles.editorHeader}>
        <h2 className={styles.editorTitle}>{filename}</h2>
        <div>
          {contentLoading && <span>Loading…</span>}
          {hasChanges && <Pill variant="warning">Unsaved</Pill>}
        </div>
      </div>

      <PromptFileList files={files} loading={false} selected={filename} />

      <Tabs value={view} onValueChange={(v) => setView(v as typeof view)}>
        <TabsList>
          <TabsTrigger value="edit">Edit</TabsTrigger>
          <TabsTrigger value="preview">Preview</TabsTrigger>
          <TabsTrigger value="split">Split</TabsTrigger>
        </TabsList>
        <TabsContent value="edit">{editorEl}</TabsContent>
        <TabsContent value="preview">
          <MarkdownPreview content={editedContent} />
        </TabsContent>
        <TabsContent value="split">
          <div className={styles.editorGrid}>
            {editorEl}
            <MarkdownPreview content={editedContent} />
          </div>
        </TabsContent>
      </Tabs>

      <VariableInspector content={editedContent} schemaPath={linkedSchema} />

      <SaveDiffModal
        open={showDiff}
        original={originalContent}
        modified={editedContent}
        filename={filename}
        saving={saving}
        onConfirm={handleSave}
        onCancel={() => setShowDiff(false)}
      />
    </div>
  );
}
```

- [ ] **Step 3: index.tsx router shell**

```tsx
import { useParams } from "react-router-dom";
import PromptsList from "./PromptsList";
import PromptEditor from "./PromptEditor";

export default function PromptsPage() {
  const { file } = useParams<{ file?: string }>();
  return file ? <PromptEditor /> : <PromptsList />;
}
```

- [ ] **Step 4: Typecheck + lint + grep AntD across Prompts/**

Run: `cd donna-ui && npx tsc -b --noEmit && npm run lint`
Run: `grep -rn "antd\|@ant-design" donna-ui/src/pages/Prompts/`
Expected: typecheck/lint PASS, grep empty.

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/pages/Prompts/index.tsx donna-ui/src/pages/Prompts/PromptsList.tsx donna-ui/src/pages/Prompts/PromptEditor.tsx
git commit -m "refactor(prompts): split into list+editor subroutes with Edit/Preview/Split tabs"
```

---

## Phase 3 — Integration + Regression (Serial)

Runs after both Phase 2 tracks land. Fixes the shared smoke-test harness, expands coverage for both migrated pages, and performs the wave exit verification.

### Task 19: Fix helpers.ts mock shapes for configs + prompts

**Files:**
- Modify: `donna-ui/tests/e2e/helpers.ts`

**Why:** The current mock returns `"[]"` for `/admin/configs` and `/admin/prompts`, but the real API wraps entries in `{ configs: [...] }` / `{ prompts: [...] }`. Wave 7 smoke tests will navigate to the editor subroute, which requires at least one entry in the list response. The old smoke tests passed only because they checked `#root` wasn't empty.

- [ ] **Step 1: Patch the admin mock**

Replace the default fall-through case in `mockAdminApi` to match new endpoints explicitly, leaving the old array-based endpoints alone.

```ts
// donna-ui/tests/e2e/helpers.ts (relevant additions inside mockAdminApi)

// /admin/configs (list) returns { configs: [...] }
if (url.match(/\/admin\/configs(\?|$)/)) {
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      configs: [
        { file: "task_states.yaml", size: 512, modified: "2026-04-01T12:00:00Z" },
        { file: "models.yaml", size: 384, modified: "2026-04-01T12:00:00Z" },
      ],
    }),
  });
}

// /admin/configs/:file returns { file, content, modified }
if (url.match(/\/admin\/configs\/[^/?]+/)) {
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      file: "task_states.yaml",
      content:
        "states:\n  - name: backlog\n    color: muted\n  - name: done\n    color: success\n",
      modified: "2026-04-01T12:00:00Z",
    }),
  });
}

// /admin/prompts (list) returns { prompts: [...] }
if (url.match(/\/admin\/prompts(\?|$)/)) {
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      prompts: [
        { file: "intake.md", size: 256, modified: "2026-04-01T12:00:00Z" },
      ],
    }),
  });
}

// /admin/prompts/:file returns { file, content, variables, modified }
if (url.match(/\/admin\/prompts\/[^/?]+/)) {
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      file: "intake.md",
      content:
        "# Intake Prompt\n\nHello {{ name }}, today is {{ date }}.\n\n```python\nprint('hi')\n```\n",
      variables: ["name", "date"],
      modified: "2026-04-01T12:00:00Z",
    }),
  });
}
```

Remove `configs` and `prompts` from the default-empty-array regex, since they now have explicit handlers.

- [ ] **Step 2: Typecheck**

Run: `cd donna-ui && npx tsc -b --noEmit`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add donna-ui/tests/e2e/helpers.ts
git commit -m "test(helpers): mock wrapped configs/prompts list + detail payloads"
```

---

### Task 20: Configs smoke tests

**Files:**
- Create: `donna-ui/tests/e2e/smoke/configs.spec.ts`

**Why:** Audit items C1 (nested Sider responsive) and C3 (SaveDiffModal fixed width) require visual regression coverage; subroute navigation needs a test so Wave 8+ doesn't regress the router contract.

- [ ] **Step 1: Write the smoke suite**

```ts
// donna-ui/tests/e2e/smoke/configs.spec.ts
import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Configs smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("list view renders mocked configs", async ({ page }) => {
    await page.goto("/configs");
    await expect(page.getByRole("heading", { name: "Configs" })).toBeVisible();
    await expect(page.locator("text=task_states.yaml")).toBeVisible();
    await expect(page.locator("text=models.yaml")).toBeVisible();
  });

  test("no AntD Sider markup in Configs page", async ({ page }) => {
    await page.goto("/configs");
    await expect(page.locator(".ant-layout-sider")).toHaveCount(0);
    await expect(page.locator(".ant-menu")).toHaveCount(0);
  });

  test("navigates to editor subroute", async ({ page }) => {
    await page.goto("/configs");
    await page.click("text=task_states.yaml");
    await expect(page).toHaveURL(/\/configs\/task_states\.yaml/);
    await expect(page.locator("text=All Configs")).toBeVisible();
    await expect(page.getByRole("tab", { name: "Structured" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Raw YAML" })).toBeVisible();
  });

  test("empty state when no configs", async ({ page }) => {
    await page.route(/\/admin\/configs(\?|$)/, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ configs: [] }),
      }),
    );
    await page.goto("/configs");
    await expect(page.locator("text=No configs found")).toBeVisible();
  });
});
```

- [ ] **Step 2: Run the suite**

Run: `cd donna-ui && npx playwright test tests/e2e/smoke/configs.spec.ts`
Expected: 4 PASS.

- [ ] **Step 3: Commit**

```bash
git add donna-ui/tests/e2e/smoke/configs.spec.ts
git commit -m "test(configs): smoke tests for list+subroute+empty state"
```

---

### Task 21: Prompts smoke tests + XSS regression

**Files:**
- Create: `donna-ui/tests/e2e/smoke/prompts.spec.ts`

**Why:** Audit items P0 (nested Sider responsive), P1 (Col span=12 no breakpoints), and **P2 (MarkdownPreview XSS vector)** all require regression coverage. The XSS test is a hard requirement of this plan — it must load a payload through the exact path users take, and assert no script execution, no `img[onerror]`, no `body script`, and no `javascript:` hrefs survive sanitization.

- [ ] **Step 1: Write the smoke suite with XSS regression**

```ts
// donna-ui/tests/e2e/smoke/prompts.spec.ts
import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Prompts smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("list view renders mocked prompts", async ({ page }) => {
    await page.goto("/prompts");
    await expect(page.getByRole("heading", { name: "Prompts" })).toBeVisible();
    await expect(page.locator("text=intake.md")).toBeVisible();
  });

  test("no AntD Sider markup in Prompts page", async ({ page }) => {
    await page.goto("/prompts");
    await expect(page.locator(".ant-layout-sider")).toHaveCount(0);
  });

  test("navigates to editor with Edit/Preview/Split tabs", async ({ page }) => {
    await page.goto("/prompts");
    await page.click("text=intake.md");
    await expect(page).toHaveURL(/\/prompts\/intake\.md/);
    await expect(page.getByRole("tab", { name: "Edit" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Preview" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Split" })).toBeVisible();
  });

  test("variable inspector shows template variables", async ({ page }) => {
    await page.goto("/prompts/intake.md");
    await expect(page.locator("text=Variables")).toBeVisible();
    await expect(page.locator("text=name")).toBeVisible();
    await expect(page.locator("text=date")).toBeVisible();
  });

  test("preview renders code block", async ({ page }) => {
    await page.goto("/prompts/intake.md");
    await page.getByRole("tab", { name: "Preview" }).click();
    // rehype-highlight wraps the code block in <code class="hljs language-python">
    await expect(page.locator("code.hljs")).toBeVisible();
  });

  // ------------------------------------------------------------------
  // XSS regression — proves old MarkdownPreview vector is closed.
  // ------------------------------------------------------------------
  test("XSS: script tag payload does not execute", async ({ page }) => {
    // Override ONLY the detail endpoint (list stays mocked).
    await page.route(/\/admin\/prompts\/[^/?]+/, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          file: "xss.md",
          content:
            "# XSS Test\n\n" +
            "<script>window.__xss_script = true;</script>\n\n" +
            '<img src="x" onerror="window.__xss_img = true">\n\n' +
            '[click me](javascript:window.__xss_href=true)\n\n' +
            "<iframe src=\"javascript:window.__xss_iframe=true\"></iframe>\n",
          variables: [],
          modified: "2026-04-01T12:00:00Z",
        }),
      }),
    );

    await page.goto("/prompts/xss.md");
    await page.getByRole("tab", { name: "Preview" }).click();

    // Wait for render, then click the sanitized anchor if it still exists.
    await page.waitForTimeout(200);
    const link = page.locator("a", { hasText: "click me" });
    if (await link.count()) await link.click({ trial: true }).catch(() => {});

    // None of the payload flags should be set anywhere in the window.
    const flags = await page.evaluate(() => ({
      script: (window as any).__xss_script === true,
      img: (window as any).__xss_img === true,
      href: (window as any).__xss_href === true,
      iframe: (window as any).__xss_iframe === true,
    }));
    expect(flags.script).toBe(false);
    expect(flags.img).toBe(false);
    expect(flags.href).toBe(false);
    expect(flags.iframe).toBe(false);

    // Structural assertions — sanitizer must strip these entirely.
    await expect(page.locator("body script")).toHaveCount(0);
    await expect(page.locator("img[onerror]")).toHaveCount(0);
    await expect(page.locator("iframe")).toHaveCount(0);
    await expect(page.locator('a[href^="javascript:"]')).toHaveCount(0);
  });

  test("XSS: template variable in attribute context is not evaluated", async ({ page }) => {
    await page.route(/\/admin\/prompts\/[^/?]+/, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          file: "attr.md",
          content:
            "Hello {{ name }}, visit [our site](http://example.com).\n\n" +
            '![alt]({{ url }})\n',
          variables: ["name", "url"],
          modified: "2026-04-01T12:00:00Z",
        }),
      }),
    );
    await page.goto("/prompts/attr.md");
    await page.getByRole("tab", { name: "Preview" }).click();
    // Variable placeholders must render as visible text, not as attribute values.
    await expect(page.locator("text={{ name }}")).toBeVisible();
    // No images should point to the literal {{ url }} string — rehype-sanitize blocks it.
    const badImg = page.locator('img[src*="{{"]');
    expect(await badImg.count()).toBe(0);
  });
});
```

- [ ] **Step 2: Run the suite**

Run: `cd donna-ui && npx playwright test tests/e2e/smoke/prompts.spec.ts`
Expected: 7 PASS. If any XSS assertion fails, STOP — do not proceed. The security task (Task 15) regressed and must be fixed before wave exit.

- [ ] **Step 3: Commit**

```bash
git add donna-ui/tests/e2e/smoke/prompts.spec.ts
git commit -m "test(prompts): smoke suite + XSS regression closing MarkdownPreview vector"
```

---

### Task 22: Wave 7 exit verification

**Files:**
- No file changes; verification only.

**Why:** Every wave ends with a hard gate: typecheck, lint, production build, full smoke suite, AntD-leak grep, and a security grep. If any step fails, the wave is not done.

- [ ] **Step 1: Typecheck + lint**

Run: `cd donna-ui && npx tsc -b --noEmit && npm run lint`
Expected: PASS.

- [ ] **Step 2: Production build**

Run: `cd donna-ui && npm run build`
Expected: PASS, no warnings about missing chunks.

- [ ] **Step 3: Full smoke suite**

Run: `cd donna-ui && npx playwright test`
Expected: ALL PASS (including `agents`, `configs`, `prompts`, and any prior waves).

- [ ] **Step 4: AntD leak grep in migrated areas**

Run: `grep -rn "antd\|@ant-design" donna-ui/src/pages/Configs/ donna-ui/src/pages/Prompts/`
Expected: no output.

- [ ] **Step 5: Security grep — no raw-HTML injection props in migrated areas**

Run: `grep -rn "dangerously" donna-ui/src/pages/Prompts/ donna-ui/src/pages/Configs/`
Expected: no output. If anything matches, the MarkdownPreview rewrite regressed and must be fixed before wave exit.

- [ ] **Step 6: Verify old STATE_COLORS constant is gone**

Run: `grep -rn "STATE_COLORS" donna-ui/src/`
Expected: no output.

- [ ] **Step 7: Tag the wave**

```bash
git tag wave-7-complete
```

Then push branch + tag for PR review:

```bash
git push -u origin wave-7-configs-prompts
git push origin wave-7-complete
```

Open the PR with a body referencing this plan and the audit items it closes.

---

## Audit Items Resolution Map

Each audit item from `docs/superpowers/specs/2026-04-08-donna-ui-redesign-design.md` §"Wave 7 Audit" is closed by one or more tasks in this plan.

| ID  | Audit item                                                              | Closed by           |
| --- | ----------------------------------------------------------------------- | ------------------- |
| C1  | Configs nested `Sider` breaks responsive (fixed 220px, no collapse)     | Task 6, Task 7      |
| C2  | Configs tabs hardcoded AntD background `#1a1a2e`                        | Task 7              |
| C3  | SaveDiffModal fixed `width={900}` breaks on mobile                      | Task 5, Task 8      |
| C4  | YAML parsed synchronously on every keystroke                            | Task 4, Task 9      |
| C5  | Monaco theme hardcoded (`vs-dark`) — no token integration               | Task 3, Task 9      |
| C6  | `STATE_COLORS` duplicated + divergent from `TASK_STATUS_COLORS`         | Task 2, Task 11     |
| C7  | Scattered ad-hoc form validation instead of zod schemas                 | Task 10, Tasks 11–14 |
| C8  | StatesForm / ModelsForm / TaskTypesForm / AgentsForm on AntD primitives | Tasks 11–14         |
| P0  | Prompts nested `Sider` breaks responsive                                | Task 16, Task 18    |
| P1  | Prompts `Row/Col span={12}` with no breakpoints                         | Task 18             |
| P2  | **MarkdownPreview XSS vector** (regex-based HTML + raw-HTML injection)  | Task 15, Task 21    |
| P3  | Prompts Edit/Preview/Split not backed by accessible tabs                | Task 18             |
| P4  | Prompts VariableInspector on AntD Card/Tag                              | Task 17             |
| S1  | Smoke tests don't assert migrated page contracts                        | Tasks 19, 20, 21    |
| S2  | Smoke mock shape mismatch for configs/prompts                           | Task 19             |
| X1  | No wave exit gate for AntD leaks / raw-HTML props in migrated areas     | Task 22             |

---

## Self-Review Summary

- **Spec coverage:** Every Wave 7 requirement from the design spec (§"Wave 7 · Configs + Prompts Migration") has at least one task. The PreferencesForm reference in the spec is a typo — noted in the Reality-Check Preamble and mapped to AgentsForm (Task 14) since that is the actual fourth form in the directory.
- **Placeholder scan:** No `TBD`, no "similar to Task N", no "add validation" — every step has either literal code or a literal command with expected output.
- **Type consistency:** `STATE_PILL_VARIANT` / `STATE_CSS_VAR` (Task 2) are referenced with the same names in Task 11. `useYamlValidator` (Task 4) is consumed with the same signature in Task 7 and Task 9. `Dialog` `size` prop (Task 5) is consumed by SaveDiffModal with the literal `"wide"` value in Task 8. The `DONNA_MONACO_THEME` constant (Task 3) is the string used by `<Editor theme={...}>` in Task 9. `MarkdownPreview` export signature `{ content }: { content: string }` (Task 15) matches the consumer in Task 18.
- **Parallelism:** Phase 1 is serial (foundation). Phase 2a (Configs, Tasks 6–14) and Phase 2b (Prompts, Tasks 15–18) are fully independent once Phase 1 lands — subagent-driven execution can run them in parallel. Phase 3 is serial and must run after both tracks merge.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-09-donna-ui-wave-7-configs-prompts.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Phase 2a and Phase 2b can run in parallel subagents once Phase 1 lands, which is the main reason this plan is decomposed this way.

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints for review.

Which approach?
