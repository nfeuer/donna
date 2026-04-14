# Wave 9 — Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Ant Design entirely from donna-ui, delete obsolete theme code, add accessibility tooling, verify bundle size target, and ungate the dev primitives route.

**Architecture:** Six files still import from `antd` — each is migrated individually to use the existing primitives library, Sonner toasts, and lucide-react icons. After migration, `antd` and `@ant-design/icons` are uninstalled. `@axe-core/react` is added for dev-mode accessibility auditing.

**Tech Stack:** React 18, Radix UI primitives (already built), Sonner (already mounted), lucide-react, CSS Modules with CSS custom properties, Playwright for smoke tests.

**Spec:** `docs/superpowers/specs/2026-04-10-donna-ui-redesign-wave-9-cleanup-design.md`

---

## File Map

| Action | File | Responsibility |
|---|---|---|
| Edit | `donna-ui/src/api/client.ts` | Replace AntD `notification` with Sonner `toast` |
| Edit | `donna-ui/src/components/ErrorBoundary.tsx` | Replace AntD `Result`/`Button` with primitives + plain markup |
| Create | `donna-ui/src/components/ErrorBoundary.module.css` | Styles for the error state |
| Edit | `donna-ui/src/components/RefreshButton.tsx` | Replace AntD components with primitives + lucide icon |
| Create | `donna-ui/src/components/RefreshButton.module.css` | Styles for refresh button (spin animation) |
| Delete | `donna-ui/src/components/PageShell.tsx` | Dead code — zero importers |
| Delete | `donna-ui/src/theme/darkTheme.ts` | Obsolete AntD ConfigProvider token map + legacy color constants |
| Edit | `donna-ui/src/main.tsx` | Remove ConfigProvider wrapper, add axe-core dev import |
| Edit | `donna-ui/src/App.tsx` | Remove `import.meta.env.DEV` gate on `/dev/primitives` route |
| Edit | `donna-ui/package.json` | Remove antd + @ant-design/icons, add @axe-core/react |
| Edit | `donna-ui/tests/e2e/smoke/dashboard.spec.ts` | Update comment + expand AntD-free assertion to full page |
| Create | `donna-ui/tests/e2e/smoke/wave9-antd-free.spec.ts` | Global assertion: zero `ant-` class names across all pages |
| Edit | `docs/superpowers/specs/bundle-baseline.txt` | Record Wave 9 measurement |

---

## Task 1: Replace `notification` with Sonner in `api/client.ts`

**Files:**
- Modify: `donna-ui/src/api/client.ts`
- Test: `donna-ui/tests/e2e/smoke/wave9-antd-free.spec.ts` (created in Task 7)

This is the simplest migration — the Sonner `<Toaster>` is already mounted in `AppShell.tsx`.

- [ ] **Step 1: Edit `api/client.ts`**

Replace the entire file contents with:

```ts
import axios from "axios";
import { toast } from "sonner";

const client = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || "",
  timeout: 15000,
});

client.interceptors.response.use(
  (res) => res,
  (error) => {
    const status = error.response?.status;
    const detail = error.response?.data?.detail || error.message;

    if (status && status >= 500) {
      toast.error(`Server Error (${status})`, { description: detail });
    } else if (!error.response) {
      toast.warning("Network Error", {
        description: "Could not reach the Donna API. Is the backend running?",
      });
    }

    return Promise.reject(error);
  },
);

export default client;
```

- [ ] **Step 2: Verify no remaining antd imports in `api/`**

Run: `grep -r "antd" donna-ui/src/api/`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add donna-ui/src/api/client.ts
git commit -m "refactor(wave-9): replace antd notification with Sonner toast in API client"
```

---

## Task 2: Migrate `ErrorBoundary.tsx` to primitives

**Files:**
- Modify: `donna-ui/src/components/ErrorBoundary.tsx`
- Create: `donna-ui/src/components/ErrorBoundary.module.css`

- [ ] **Step 1: Create `ErrorBoundary.module.css`**

```css
.errorContainer {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 72px 28px;
  text-align: center;
  gap: var(--space-4);
}

.icon {
  color: var(--color-error);
  width: 48px;
  height: 48px;
  margin-bottom: var(--space-3);
}

.title {
  font-family: var(--font-display);
  font-size: 24px;
  font-weight: 300;
  letter-spacing: -0.025em;
  color: var(--color-text);
  margin: 0;
}

.message {
  font-family: var(--font-mono);
  font-size: var(--text-body);
  color: var(--color-text-muted);
  margin: 0;
  max-width: 480px;
}
```

- [ ] **Step 2: Rewrite `ErrorBoundary.tsx`**

Replace the entire file contents with:

```tsx
import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle } from "lucide-react";
import { Button } from "../primitives";
import styles from "./ErrorBoundary.module.css";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("ErrorBoundary caught:", error, info.componentStack);
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className={styles.errorContainer}>
          <AlertTriangle className={styles.icon} />
          <h2 className={styles.title}>Something went wrong</h2>
          {this.state.error?.message && (
            <p className={styles.message}>{this.state.error.message}</p>
          )}
          <Button variant="ghost" onClick={this.handleRetry}>
            Try Again
          </Button>
        </div>
      );
    }
    return this.props.children;
  }
}
```

- [ ] **Step 3: Verify no antd imports remain in ErrorBoundary**

Run: `grep "antd" donna-ui/src/components/ErrorBoundary.tsx`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add donna-ui/src/components/ErrorBoundary.tsx donna-ui/src/components/ErrorBoundary.module.css
git commit -m "refactor(wave-9): migrate ErrorBoundary from AntD Result to primitives"
```

---

## Task 3: Migrate `RefreshButton.tsx` to primitives

**Files:**
- Modify: `donna-ui/src/components/RefreshButton.tsx`
- Create: `donna-ui/src/components/RefreshButton.module.css`

`RefreshButton` is used by 5 pages (Dashboard, Tasks, Agents, Shadow, Preferences). The interface (`onRefresh`, `autoRefreshMs`) stays identical — only the rendering changes.

- [ ] **Step 1: Create `RefreshButton.module.css`**

```css
.wrapper {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

.ago {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-text-muted);
}

.icon {
  width: 14px;
  height: 14px;
}

.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
```

- [ ] **Step 2: Rewrite `RefreshButton.tsx`**

Replace the entire file contents with:

```tsx
import { useState, useEffect, useCallback } from "react";
import { RefreshCw } from "lucide-react";
import { Button } from "../primitives";
import { cn } from "../lib/cn";
import styles from "./RefreshButton.module.css";

interface RefreshButtonProps {
  onRefresh: () => Promise<void>;
  autoRefreshMs?: number;
}

export default function RefreshButton({
  onRefresh,
  autoRefreshMs,
}: RefreshButtonProps) {
  const [loading, setLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [ago, setAgo] = useState("");

  const doRefresh = useCallback(async () => {
    setLoading(true);
    try {
      await onRefresh();
      setLastUpdated(new Date());
    } finally {
      setLoading(false);
    }
  }, [onRefresh]);

  // Auto-refresh interval
  useEffect(() => {
    if (!autoRefreshMs) return;
    const id = setInterval(doRefresh, autoRefreshMs);
    return () => clearInterval(id);
  }, [autoRefreshMs, doRefresh]);

  // Update "ago" text every 5s
  useEffect(() => {
    const tick = () => {
      if (!lastUpdated) return;
      const secs = Math.floor((Date.now() - lastUpdated.getTime()) / 1000);
      if (secs < 5) setAgo("just now");
      else if (secs < 60) setAgo(`${secs}s ago`);
      else setAgo(`${Math.floor(secs / 60)}m ago`);
    };
    tick();
    const id = setInterval(tick, 5000);
    return () => clearInterval(id);
  }, [lastUpdated]);

  return (
    <span className={styles.wrapper}>
      {lastUpdated && <span className={styles.ago}>{ago}</span>}
      <Button variant="ghost" size="sm" onClick={doRefresh} disabled={loading}>
        <RefreshCw className={cn(styles.icon, loading && styles.spinning)} />
        Refresh
      </Button>
    </span>
  );
}
```

- [ ] **Step 3: Verify no antd or @ant-design imports remain**

Run: `grep -E "antd|@ant-design" donna-ui/src/components/RefreshButton.tsx`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add donna-ui/src/components/RefreshButton.tsx donna-ui/src/components/RefreshButton.module.css
git commit -m "refactor(wave-9): migrate RefreshButton from AntD to primitives + lucide"
```

---

## Task 4: Delete dead files (`PageShell.tsx`, `darkTheme.ts`)

**Files:**
- Delete: `donna-ui/src/components/PageShell.tsx`
- Delete: `donna-ui/src/theme/darkTheme.ts`

- [ ] **Step 1: Verify `PageShell.tsx` has zero importers**

Run: `grep -r "PageShell" donna-ui/src/ --include="*.ts" --include="*.tsx" | grep -v "PageShell.tsx"`
Expected: no output.

- [ ] **Step 2: Verify `darkTheme.ts` is only imported by `main.tsx`**

Run: `grep -r "darkTheme" donna-ui/src/ --include="*.ts" --include="*.tsx" | grep -v "darkTheme.ts"`
Expected: only `donna-ui/src/main.tsx` appears (will be fixed in Task 5).

- [ ] **Step 3: Delete both files**

```bash
rm donna-ui/src/components/PageShell.tsx
rm donna-ui/src/theme/darkTheme.ts
```

- [ ] **Step 4: Commit**

```bash
git add donna-ui/src/components/PageShell.tsx donna-ui/src/theme/darkTheme.ts
git commit -m "refactor(wave-9): delete dead PageShell.tsx and obsolete darkTheme.ts"
```

---

## Task 5: Strip AntD from `main.tsx` and add axe-core

**Files:**
- Modify: `donna-ui/src/main.tsx`
- Modify: `donna-ui/package.json`

- [ ] **Step 1: Install `@axe-core/react` as a dev dependency**

Run from `donna-ui/`:

```bash
cd donna-ui && npm install --save-dev @axe-core/react
```

- [ ] **Step 2: Rewrite `main.tsx`**

Replace the entire file contents with:

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

// Font faces — order matters: loaded before CSS vars use them
import "@fontsource/fraunces/300.css";
import "@fontsource/fraunces/400.css";
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/jetbrains-mono/400.css";

// Design tokens and base CSS — order matters: tokens → themes → reset
import "./theme/tokens.css";
import "./theme/themes.css";
import "./theme/reset.css";
import "highlight.js/styles/github-dark.css";

import App from "./App";

if (import.meta.env.DEV) {
  import("@axe-core/react").then((axe) => {
    axe.default(React, ReactDOM, 1000);
  });
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
```

- [ ] **Step 3: Verify no antd imports remain in `main.tsx`**

Run: `grep "antd\|darkTheme" donna-ui/src/main.tsx`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add donna-ui/src/main.tsx donna-ui/package.json donna-ui/package-lock.json
git commit -m "refactor(wave-9): remove AntD ConfigProvider from main.tsx, add axe-core"
```

---

## Task 6: Uninstall AntD packages and ungate dev primitives route

**Files:**
- Modify: `donna-ui/package.json`
- Modify: `donna-ui/src/App.tsx`

- [ ] **Step 1: Verify no remaining antd imports in `src/`**

Run: `grep -r "from ['\"]antd" donna-ui/src/`
Expected: no output. If any remain, stop and fix them before proceeding.

Run: `grep -r "@ant-design" donna-ui/src/`
Expected: no output.

- [ ] **Step 2: Uninstall antd packages**

Run from `donna-ui/`:

```bash
cd donna-ui && npm uninstall antd @ant-design/icons
```

- [ ] **Step 3: Remove dev gate on `/dev/primitives` route in `App.tsx`**

In `donna-ui/src/App.tsx`, replace:

```tsx
        {/* Dev-only primitives gallery — outside AppShell so it renders standalone */}
        {import.meta.env.DEV && (
          <Route path="/dev/primitives" element={<DevPrimitivesPage />} />
        )}
```

with:

```tsx
        {/* Primitives gallery — available as internal reference */}
        <Route path="/dev/primitives" element={<DevPrimitivesPage />} />
```

- [ ] **Step 4: Verify build succeeds**

Run from `donna-ui/`:

```bash
cd donna-ui && npx tsc -b --noEmit && npx vite build
```

Expected: clean compile, no errors referencing `antd`. Note the gzipped JS size from the build output — it will be formally recorded in Task 8.

- [ ] **Step 5: Commit**

```bash
git add donna-ui/package.json donna-ui/package-lock.json donna-ui/src/App.tsx
git commit -m "refactor(wave-9): uninstall antd, ungate /dev/primitives route"
```

---

## Task 7: Add AntD-free smoke test and update existing tests

**Files:**
- Create: `donna-ui/tests/e2e/smoke/wave9-antd-free.spec.ts`
- Modify: `donna-ui/tests/e2e/smoke/dashboard.spec.ts`

- [ ] **Step 1: Create `wave9-antd-free.spec.ts`**

This test navigates every page and asserts zero `ant-` class names anywhere in the DOM.

```ts
import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

const PAGES = [
  { path: "/", name: "Dashboard" },
  { path: "/tasks", name: "Tasks" },
  { path: "/logs", name: "Logs" },
  { path: "/agents", name: "Agents" },
  { path: "/configs", name: "Configs" },
  { path: "/prompts", name: "Prompts" },
  { path: "/shadow", name: "Shadow" },
  { path: "/preferences", name: "Preferences" },
];

test.describe("Wave 9: AntD fully removed", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  for (const { path, name } of PAGES) {
    test(`${name} (${path}) has zero ant- class names`, async ({ page }) => {
      await page.goto(path);
      await page.waitForLoadState("networkidle");

      const antdCount = await page.locator('[class*="ant-"]').count();
      expect(antdCount).toBe(0);
    });
  }

  test("antd is not in the JS bundle", async ({ page }) => {
    const scripts: string[] = [];
    page.on("response", (resp) => {
      if (resp.url().endsWith(".js")) {
        scripts.push(resp.url());
      }
    });

    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // Verify at least one JS file was loaded (sanity check)
    expect(scripts.length).toBeGreaterThan(0);

    // If antd were still bundled, its CSS-in-JS would inject ant- classes.
    // The per-page checks above already cover this, but this is a belt-and-suspenders check.
    const antdElements = await page.locator('[class*="ant-"]').count();
    expect(antdElements).toBe(0);
  });
});
```

- [ ] **Step 2: Update dashboard smoke test**

In `donna-ui/tests/e2e/smoke/dashboard.spec.ts`, replace the "no AntD class names inside the card grid" test:

```ts
  test("no AntD class names on the entire page", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    const antdCount = await page.locator('[class*="ant-"]').count();
    expect(antdCount).toBe(0);
  });
```

This replaces the old scoped check (which excluded RefreshButton because it was still AntD).

- [ ] **Step 3: Run smoke tests**

Run from `donna-ui/`:

```bash
cd donna-ui && npx playwright test
```

Expected: all tests pass. If any fail, debug and fix before committing.

- [ ] **Step 4: Commit**

```bash
git add donna-ui/tests/e2e/smoke/wave9-antd-free.spec.ts donna-ui/tests/e2e/smoke/dashboard.spec.ts
git commit -m "test(wave-9): add AntD-free assertion across all pages, update dashboard test"
```

---

## Task 8: Bundle size verification

**Files:**
- Modify: `docs/superpowers/specs/bundle-baseline.txt`

- [ ] **Step 1: Run production build and capture output**

Run from `donna-ui/`:

```bash
cd donna-ui && npm run build 2>&1 | tee /tmp/wave9-build-output.txt
```

- [ ] **Step 2: Extract gzipped JS size**

Read the build output. Look for the `index-*.js` line and note the gzip size. The target is **<= 362.10 kB** (40% reduction from the 603.50 kB baseline).

- [ ] **Step 3: Append Wave 9 measurement to `bundle-baseline.txt`**

Add the following section at the end of `docs/superpowers/specs/bundle-baseline.txt` (fill in the actual numbers from the build output):

```
Wave 9 measurement
------------------
Total gzipped JS:  <ACTUAL> kB
Reduction vs baseline: <PERCENT>%
Target met: <YES/NO> (target was <= 362.10 kB)
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/bundle-baseline.txt
git commit -m "docs(wave-9): record post-cleanup bundle size measurement"
```

---

## Task 9: Hex literal sweep verification

No files to create or modify — this is a verification step.

- [ ] **Step 1: Grep for hex literals outside allowed directories**

Run:

```bash
grep -rn "#[0-9a-fA-F]\{6\}" donna-ui/src/ \
  --include="*.ts" --include="*.tsx" --include="*.css" \
  | grep -v "src/theme/" \
  | grep -v "src/charts/"
```

Expected output should contain ONLY:
- `src/layout/Sidebar.module.css` — theme swatch dots (`#d4a943`, `#f56960`)
- `src/lib/monacoTheme.ts` — SSR/test fallback hex values

If any other files appear, fix them by replacing the hex with the appropriate `var(--color-*)` token.

- [ ] **Step 2: Verify darkTheme.ts is gone**

Run: `ls donna-ui/src/theme/darkTheme.ts 2>&1`
Expected: "No such file or directory"

- [ ] **Step 3: Verify PageShell.tsx is gone**

Run: `ls donna-ui/src/components/PageShell.tsx 2>&1`
Expected: "No such file or directory"

- [ ] **Step 4: No commit needed** — this is a verification-only task. If fixes were made, commit them with message: `fix(wave-9): replace remaining inline hex with CSS tokens`
