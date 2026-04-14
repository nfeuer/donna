# Donna UI Redesign — Wave 0 (Foundation) + Wave 1 (Primitives Library) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lay the foundation and build the complete primitive component library for the Donna UI redesign, without touching any existing pages. After this plan, the app looks identical to today but has a fully built parallel component system ready for page-by-page migration in subsequent plans.

**Architecture:** CSS custom properties drive the entire theme system, loaded once at app start. `useTheme` hook persists the gold/coral accent choice to `localStorage` and mirrors it to a `[data-theme]` attribute on `<html>`. Primitives are thin wrappers around Radix UI + TanStack Table; each primitive is its own file under `src/primitives/`. Every primitive ships with an automated visual test via a `/dev/primitives` story page gated to dev mode.

**Tech Stack:** React 18, TypeScript 5, Vite 6, Ant Design 5 (existing — not touched in this plan), Radix UI primitives, TanStack Table v8, TanStack Virtual, React Hook Form, Zod, Sonner, react-markdown, react-day-picker, lucide-react, clsx, @fontsource, Playwright.

**Spec reference:** `docs/superpowers/specs/2026-04-08-donna-ui-redesign-design.md` §1, §2, §4 (Waves 0-1).

## Next Waves (Follow-up Plans)

Once Wave 0 + Wave 1 are merged and the foundation is validated, write follow-up plans for these subsequent waves. Each is described in the spec at `docs/superpowers/specs/2026-04-08-donna-ui-redesign-design.md` §4.

| Wave | Scope | Spec section | Plan file (to be written) |
|------|-------|--------------|---------------------------|
| 2 | App Shell — replace `Layout.tsx`, build `AppShell`, `SideRail`, `TopBar` | §4 Wave 2 (line 326) | `docs/superpowers/plans/<date>-donna-ui-wave-2-shell.md` |
| 3 | Dashboard Migration — port all 5 cards + header to primitives, re-theme Recharts | §4 Wave 3 (line 341) | `docs/superpowers/plans/<date>-donna-ui-wave-3-dashboard.md` |
| 4 | Tasks Migration — DataTable + filters + drawer | §4 Wave 4 (line 357) | `docs/superpowers/plans/<date>-donna-ui-wave-4-tasks.md` |
| 5 | Logs Migration — virtualized log viewer | §4 Wave 5 (line 371) | `docs/superpowers/plans/<date>-donna-ui-wave-5-logs.md` |
| 6 | Agents Migration — agent cards + run history | §4 Wave 6 (line 384) | `docs/superpowers/plans/<date>-donna-ui-wave-6-agents.md` |
| 7 | Configs + Prompts Migration — Monaco-hosted editors with Web Worker validation | §4 Wave 7 (line 395) | `docs/superpowers/plans/<date>-donna-ui-wave-7-configs-prompts.md` |
| 8 | Shadow + Preferences Migration — RHF/Zod forms, preferences backend swap | §4 Wave 8 (line 422) | `docs/superpowers/plans/<date>-donna-ui-wave-8-shadow-prefs.md` |
| 9 | Cleanup — strip Ant Design 5, remove `darkTheme.ts`, verify ≥40% gzipped bundle reduction vs Wave 0 baseline | §4 Wave 9 (line 438) | `docs/superpowers/plans/<date>-donna-ui-wave-9-cleanup.md` |

**Pickup instructions for the next session:**
1. Verify this plan's tasks are all merged and `pages/DevPrimitives` story page renders cleanly in dev (`npm run dev` then `/dev/primitives`).
2. Read the spec section for Wave 2 (`docs/superpowers/specs/2026-04-08-donna-ui-redesign-design.md` line 326).
3. Invoke `superpowers:writing-plans` with the next wave's scope.
4. The Wave 0 bundle baseline is at `donna-ui/docs/bundle-baseline.txt` — Wave 9's cleanup task must compare against it.

---

## File Structure Overview

### Created in Wave 0 (tasks 1–8)

```
donna-ui/
├── package.json                           (modified: +dependencies)
├── vite.config.ts                         (modified: +chunking config)
├── playwright.config.ts                   (created)
├── tests/e2e/
│   ├── smoke/
│   │   ├── dashboard.spec.ts
│   │   ├── tasks.spec.ts
│   │   ├── logs.spec.ts
│   │   ├── agents.spec.ts
│   │   ├── configs.spec.ts
│   │   ├── prompts.spec.ts
│   │   ├── shadow.spec.ts
│   │   └── preferences.spec.ts
│   └── helpers.ts
│
└── src/
    ├── main.tsx                           (modified: +theme css imports)
    ├── theme/
    │   ├── tokens.css                     (created)
    │   ├── themes.css                     (created)
    │   ├── reset.css                      (created)
    │   └── index.ts                       (created — typed token exports)
    └── hooks/
        └── useTheme.ts                    (created)
```

### Created in Wave 1 (tasks 9–32)

```
donna-ui/src/
├── App.tsx                                (modified: +/dev/primitives route)
├── lib/
│   └── cn.ts                              (created — clsx helper)
├── primitives/
│   ├── Button.tsx                         (created)
│   ├── Button.module.css
│   ├── Card.tsx
│   ├── Card.module.css
│   ├── Pill.tsx
│   ├── Pill.module.css
│   ├── Input.tsx
│   ├── Input.module.css
│   ├── Select.tsx
│   ├── Select.module.css
│   ├── Checkbox.tsx
│   ├── Checkbox.module.css
│   ├── Switch.tsx
│   ├── Switch.module.css
│   ├── Tabs.tsx
│   ├── Tabs.module.css
│   ├── Tooltip.tsx
│   ├── Tooltip.module.css
│   ├── Dialog.tsx
│   ├── Dialog.module.css
│   ├── Drawer.tsx
│   ├── Drawer.module.css
│   ├── DropdownMenu.tsx
│   ├── DropdownMenu.module.css
│   ├── Popover.tsx
│   ├── Popover.module.css
│   ├── Skeleton.tsx
│   ├── Skeleton.module.css
│   ├── ScrollArea.tsx
│   ├── ScrollArea.module.css
│   ├── PageHeader.tsx
│   ├── PageHeader.module.css
│   ├── Stat.tsx
│   ├── Stat.module.css
│   ├── Segmented.tsx
│   ├── Segmented.module.css
│   ├── EmptyState.tsx
│   ├── EmptyState.module.css
│   ├── DataTable.tsx
│   ├── DataTable.module.css
│   └── index.ts                           (barrel export)
└── pages/
    └── DevPrimitives/
        ├── index.tsx                      (dev-only story page)
        ├── StorySection.tsx               (story layout helper)
        └── DevPrimitives.module.css
```

**Principle:** One primitive = one `.tsx` + one `.module.css`. CSS modules keep styles scoped, class names deterministic, and bundle analysis clean. Each primitive file stays under 80 lines.

---

## Wave 0 · Foundation

### Task 1: Install Wave 0 base dependencies

**Files:**
- Modify: `donna-ui/package.json`

- [ ] **Step 1: Install runtime deps**

Run from `donna-ui/` directory:

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npm install clsx lucide-react @fontsource/fraunces @fontsource/inter @fontsource/jetbrains-mono
```

Expected: new entries in `package.json` dependencies. No errors.

- [ ] **Step 2: Install Playwright as dev dep**

```bash
npm install -D @playwright/test
npx playwright install chromium
```

Expected: `@playwright/test` appears in `devDependencies`, chromium downloads.

- [ ] **Step 3: Verify install**

```bash
npx playwright --version
```

Expected output: `Version 1.xx.x`.

- [ ] **Step 4: Commit**

```bash
git add donna-ui/package.json donna-ui/package-lock.json
git commit -m "Install Wave 0 base deps (fonts, clsx, lucide, Playwright)"
```

---

### Task 2: Create CSS token file

**Files:**
- Create: `donna-ui/src/theme/tokens.css`

- [ ] **Step 1: Write the tokens**

Create `donna-ui/src/theme/tokens.css` with the complete contents:

```css
/*
 * Donna design tokens — refined dark luxury aesthetic.
 * All colors, typography, spacing, and motion live here.
 * Consumed by src/theme/themes.css and every primitive module.
 * See docs/superpowers/specs/2026-04-08-donna-ui-redesign-design.md §1
 */

:root {
  /* ========== Surfaces ========== */
  --color-bg: #1a1816;
  --color-surface: #1f1c18;
  --color-inset: #16140f;
  --color-border: #2a2724;
  --color-border-subtle: #221f1c;

  /* ========== Text ========== */
  --color-text: #e8e3d8;
  --color-text-secondary: #c7c0b2;
  --color-text-muted: #8a8378;
  --color-text-dim: #5e5850;

  /* ========== Semantic ========== */
  --color-success: #8aa672;
  --color-warning: #d4a943;
  --color-error: #c8665e;

  /* ========== Accent (default = champagne gold) ========== */
  /* Overridden by [data-theme="coral"] in themes.css */
  --color-accent: #d4a943;
  --color-accent-soft: rgba(212, 169, 67, 0.10);
  --color-accent-border: rgba(212, 169, 67, 0.28);
  --color-accent-contrast: var(--color-inset); /* text on accent background */

  /* ========== Typography ========== */
  --font-display: "Fraunces", Georgia, "Times New Roman", serif;
  --font-body: "Inter", system-ui, -apple-system, sans-serif;
  --font-mono: "JetBrains Mono", ui-monospace, "SF Mono", Menlo, monospace;

  --text-page-title: clamp(32px, 4vw, 44px);
  --text-section: 20px;
  --text-metric: clamp(40px, 5vw, 56px);
  --text-body-lg: 15px;
  --text-body: 13px;
  --text-label: 11px;
  --text-eyebrow: 9px;

  --tracking-tight: -0.025em;
  --tracking-normal: -0.01em;
  --tracking-wide: 0.04em;
  --tracking-eyebrow: 0.18em;

  --leading-tight: 1;
  --leading-snug: 1.2;
  --leading-normal: 1.5;

  /* ========== Spacing (asymmetric scale) ========== */
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 14px;
  --space-4: 20px;
  --space-5: 28px;
  --space-6: 44px;
  --space-7: 72px;

  /* ========== Radius ========== */
  --radius-control: 2px;
  --radius-card: 4px;

  /* ========== Motion ========== */
  --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  --duration-fast: 200ms;
  --duration-base: 300ms;

  /* ========== Shadows (used sparingly) ========== */
  --shadow-drawer: -16px 0 32px rgba(0, 0, 0, 0.3);
  --shadow-dialog: 0 16px 48px rgba(0, 0, 0, 0.4);

  /* ========== Layering ========== */
  --z-rail: 10;
  --z-popover: 20;
  --z-dialog: 30;
  --z-toast: 40;
}

@media (prefers-reduced-motion: reduce) {
  :root {
    --duration-fast: 0ms;
    --duration-base: 0ms;
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add donna-ui/src/theme/tokens.css
git commit -m "Add CSS design tokens (colors, type, spacing, motion)"
```

---

### Task 3: Create theme variant file

**Files:**
- Create: `donna-ui/src/theme/themes.css`

- [ ] **Step 1: Write the theme overrides**

Create `donna-ui/src/theme/themes.css`:

```css
/*
 * Theme variants. Selected by [data-theme] attribute on <html>.
 * useTheme hook (src/hooks/useTheme.ts) sets this attribute.
 * Default (no attribute or data-theme="gold") uses tokens.css values.
 */

[data-theme="coral"] {
  --color-accent: #f56960;
  --color-accent-soft: rgba(245, 105, 96, 0.10);
  --color-accent-border: rgba(245, 105, 96, 0.28);
  --color-accent-contrast: var(--color-inset);
}
```

- [ ] **Step 2: Commit**

```bash
git add donna-ui/src/theme/themes.css
git commit -m "Add coral theme variant"
```

---

### Task 4: Create reset + base body CSS

**Files:**
- Create: `donna-ui/src/theme/reset.css`

- [ ] **Step 1: Write the reset**

Create `donna-ui/src/theme/reset.css`:

```css
/*
 * Minimal CSS reset + base body styles.
 * Loaded AFTER tokens.css + themes.css so it can consume the variables.
 */

*, *::before, *::after {
  box-sizing: border-box;
}

html, body, #root {
  margin: 0;
  padding: 0;
  height: 100%;
}

body {
  background: var(--color-bg);
  color: var(--color-text);
  font-family: var(--font-body);
  font-size: var(--text-body);
  line-height: var(--leading-normal);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  text-rendering: optimizeLegibility;
}

/* Fraunces optical sizing */
.display,
h1, h2, h3, h4, h5, h6 {
  font-optical-sizing: auto;
  font-variation-settings: "opsz" 48;
}

/* Consistent focus ring — visible but not ugly */
:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 2px;
  border-radius: var(--radius-control);
}

/* Remove focus ring from mouse-only interactions */
:focus:not(:focus-visible) {
  outline: none;
}

/* Scrollbar — subtle, themed */
::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}
::-webkit-scrollbar-track {
  background: transparent;
}
::-webkit-scrollbar-thumb {
  background: var(--color-border);
  border-radius: var(--radius-control);
}
::-webkit-scrollbar-thumb:hover {
  background: var(--color-text-dim);
}
```

- [ ] **Step 2: Commit**

```bash
git add donna-ui/src/theme/reset.css
git commit -m "Add CSS reset and base body styles"
```

---

### Task 5: Create typed token export

**Files:**
- Create: `donna-ui/src/theme/index.ts`

- [ ] **Step 1: Write the token TS exports**

Create `donna-ui/src/theme/index.ts`:

```ts
/*
 * Typed re-exports of CSS tokens for JS consumers (Recharts, etc).
 * Values must stay in sync with tokens.css. If you add a color token
 * in tokens.css, add it here too.
 */

export const colors = {
  bg: "var(--color-bg)",
  surface: "var(--color-surface)",
  inset: "var(--color-inset)",
  border: "var(--color-border)",
  borderSubtle: "var(--color-border-subtle)",
  text: "var(--color-text)",
  textSecondary: "var(--color-text-secondary)",
  textMuted: "var(--color-text-muted)",
  textDim: "var(--color-text-dim)",
  accent: "var(--color-accent)",
  accentSoft: "var(--color-accent-soft)",
  accentBorder: "var(--color-accent-border)",
  success: "var(--color-success)",
  warning: "var(--color-warning)",
  error: "var(--color-error)",
} as const;

export const fonts = {
  display: "var(--font-display)",
  body: "var(--font-body)",
  mono: "var(--font-mono)",
} as const;

export const space = {
  1: "var(--space-1)",
  2: "var(--space-2)",
  3: "var(--space-3)",
  4: "var(--space-4)",
  5: "var(--space-5)",
  6: "var(--space-6)",
  7: "var(--space-7)",
} as const;

export const motion = {
  easeOut: "var(--ease-out)",
  fast: "var(--duration-fast)",
  base: "var(--duration-base)",
} as const;

export type Theme = "gold" | "coral";
```

- [ ] **Step 2: Commit**

```bash
git add donna-ui/src/theme/index.ts
git commit -m "Add typed token exports for JS consumers"
```

---

### Task 6: Load fonts + theme CSS in main.tsx

**Files:**
- Modify: `donna-ui/src/main.tsx`

- [ ] **Step 1: Read the current main.tsx**

```bash
cat donna-ui/src/main.tsx
```

Expected: imports StrictMode, createRoot, BrowserRouter, App, antd ConfigProvider, and the existing `./theme/darkTheme`.

- [ ] **Step 2: Update main.tsx to load fonts and CSS**

Replace `donna-ui/src/main.tsx` with:

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { ConfigProvider, theme } from "antd";

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

import { darkTheme } from "./theme/darkTheme";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ConfigProvider theme={{ ...darkTheme, algorithm: theme.darkAlgorithm }}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ConfigProvider>
  </StrictMode>
);
```

- [ ] **Step 3: Run the dev server to verify fonts load and nothing breaks**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npm run dev
```

Open `http://localhost:5173`. Expected: app renders identically to before (because AntD is still the active style layer). Open DevTools → Network → Fonts and verify `fraunces-*.woff2`, `inter-*.woff2`, `jetbrains-mono-*.woff2` are loaded.

Stop the dev server with Ctrl+C.

- [ ] **Step 4: Commit**

```bash
git add donna-ui/src/main.tsx
git commit -m "Load self-hosted fonts and theme CSS in main entry"
```

---

### Task 7: Create useTheme hook with tests

**Files:**
- Create: `donna-ui/src/hooks/useTheme.ts`
- Test: inline in Playwright smoke test (Task 8)

- [ ] **Step 1: Write the hook**

Create `donna-ui/src/hooks/useTheme.ts`:

```ts
import { useCallback, useEffect, useState } from "react";
import type { Theme } from "../theme";

const STORAGE_KEY = "donna-theme";
const DEFAULT_THEME: Theme = "gold";

function readStoredTheme(): Theme {
  try {
    const v = window.localStorage.getItem(STORAGE_KEY);
    return v === "coral" ? "coral" : DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}

function applyTheme(theme: Theme): void {
  // "gold" is the default (no attribute needed) — only set when coral
  if (theme === "coral") {
    document.documentElement.setAttribute("data-theme", "coral");
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
}

/**
 * Reads and writes the active accent theme.
 * Persisted to localStorage. Mirrored to [data-theme] on <html>.
 * All CSS uses var(--color-accent) so the flip is instant.
 */
export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(() => {
    if (typeof window === "undefined") return DEFAULT_THEME;
    const stored = readStoredTheme();
    applyTheme(stored);
    return stored;
  });

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next);
    applyTheme(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Ignore — private browsing, quota exceeded, etc.
    }
  }, []);

  const toggle = useCallback(() => {
    setTheme(theme === "gold" ? "coral" : "gold");
  }, [theme, setTheme]);

  // Global shortcut: ⌘. (Mac) or Ctrl+. (everywhere) flips the theme.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === ".") {
        e.preventDefault();
        toggle();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [toggle]);

  return { theme, setTheme, toggle };
}
```

- [ ] **Step 2: Use the hook in App.tsx so it activates globally**

Read current App.tsx:

```bash
cat donna-ui/src/App.tsx
```

Modify `donna-ui/src/App.tsx` — add the hook call at the top of the App component. The final file should be:

```tsx
import { Routes, Route } from "react-router-dom";
import AppLayout from "./components/Layout";
import ErrorBoundary from "./components/ErrorBoundary";
import Dashboard from "./pages/Dashboard";
import Logs from "./pages/Logs";
import ConfigsPage from "./pages/Configs";
import PromptsPage from "./pages/Prompts";
import AgentsPage from "./pages/Agents";
import TasksPage from "./pages/Tasks";
import TaskDetail from "./pages/Tasks/TaskDetail";
import ShadowPage from "./pages/Shadow";
import PreferencesPage from "./pages/Preferences";
import { useTheme } from "./hooks/useTheme";

export default function App() {
  // Activates theme + persists to localStorage + registers ⌘. shortcut
  useTheme();

  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/" element={<ErrorBoundary><Dashboard /></ErrorBoundary>} />
        <Route path="/logs" element={<ErrorBoundary><Logs /></ErrorBoundary>} />
        <Route path="/configs" element={<ErrorBoundary><ConfigsPage /></ErrorBoundary>} />
        <Route path="/prompts" element={<ErrorBoundary><PromptsPage /></ErrorBoundary>} />
        <Route path="/agents" element={<ErrorBoundary><AgentsPage /></ErrorBoundary>} />
        <Route path="/tasks" element={<ErrorBoundary><TasksPage /></ErrorBoundary>} />
        <Route path="/tasks/:id" element={<ErrorBoundary><TaskDetail /></ErrorBoundary>} />
        <Route path="/shadow" element={<ErrorBoundary><ShadowPage /></ErrorBoundary>} />
        <Route path="/preferences" element={<ErrorBoundary><PreferencesPage /></ErrorBoundary>} />
      </Route>
    </Routes>
  );
}
```

- [ ] **Step 3: Type-check**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Manual smoke test in browser**

```bash
npm run dev
```

Open `http://localhost:5173`. Open DevTools Console and run:

```js
localStorage.getItem("donna-theme")
```

Expected: `null` (nothing set yet).

Press `⌘.` (Mac) or `Ctrl+.` (Linux/Win). Check `<html>` in the Elements panel — should now have `data-theme="coral"`. Run again:

```js
localStorage.getItem("donna-theme")
```

Expected: `"coral"`.

Press `⌘.` again. `data-theme` attribute should disappear. localStorage should contain `"gold"`.

Reload the page. `data-theme` should still be absent (gold = default, no attribute). Localstorage still `"gold"`.

Press `⌘.`, reload. `data-theme="coral"` should persist.

Stop dev server.

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/hooks/useTheme.ts donna-ui/src/App.tsx
git commit -m "Add useTheme hook with localStorage and Cmd-. shortcut"
```

---

### Task 8: Playwright smoke tests for every page

**Files:**
- Create: `donna-ui/playwright.config.ts`
- Create: `donna-ui/tests/e2e/helpers.ts`
- Create: `donna-ui/tests/e2e/smoke/dashboard.spec.ts`
- Create: `donna-ui/tests/e2e/smoke/tasks.spec.ts`
- Create: `donna-ui/tests/e2e/smoke/logs.spec.ts`
- Create: `donna-ui/tests/e2e/smoke/agents.spec.ts`
- Create: `donna-ui/tests/e2e/smoke/configs.spec.ts`
- Create: `donna-ui/tests/e2e/smoke/prompts.spec.ts`
- Create: `donna-ui/tests/e2e/smoke/shadow.spec.ts`
- Create: `donna-ui/tests/e2e/smoke/preferences.spec.ts`
- Modify: `donna-ui/package.json` (add test scripts)
- Modify: `donna-ui/.gitignore` (ignore playwright output)

- [ ] **Step 1: Write playwright.config.ts**

Create `donna-ui/playwright.config.ts`:

```ts
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: "list",
  use: {
    baseURL: "http://localhost:5173",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  webServer: {
    // IMPORTANT: backend proxy is at /admin → http://localhost:8200
    // Smoke tests route-navigate only and don't depend on real API data.
    command: "npm run dev",
    url: "http://localhost:5173",
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
  projects: [
    { name: "chromium", use: { channel: "chromium" } },
  ],
});
```

- [ ] **Step 2: Write the helpers file**

Create `donna-ui/tests/e2e/helpers.ts`:

```ts
import type { Page } from "@playwright/test";

/**
 * Mock all /admin/** requests so smoke tests don't depend on a running backend.
 * Returns minimal empty-array / empty-object responses.
 */
export async function mockAdminApi(page: Page) {
  await page.route("**/admin/**", (route) => {
    const url = route.request().url();
    // Return empty array for list endpoints, empty object otherwise
    const body = url.match(/\/(logs|tasks|agents|configs|prompts|shadow|preferences|rules|corrections)(\?|$)/)
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

- [ ] **Step 3: Write dashboard smoke test**

Create `donna-ui/tests/e2e/smoke/dashboard.spec.ts`:

```ts
import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Dashboard smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("loads without crashing", async ({ page }) => {
    await page.goto("/");
    // App renders *something* — either a nav rail or a root div
    await expect(page.locator("#root")).not.toBeEmpty();
  });

  test("theme shortcut toggles data-theme attribute", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // Initial — no attribute (gold is default)
    await expect(page.locator("html")).not.toHaveAttribute("data-theme", "coral");

    // Press Cmd+.
    await page.keyboard.press("Meta+.");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "coral");

    // Press Cmd+. again
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

- [ ] **Step 4: Write tasks smoke test**

Create `donna-ui/tests/e2e/smoke/tasks.spec.ts`:

```ts
import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Tasks smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("loads without crashing", async ({ page }) => {
    await page.goto("/tasks");
    await expect(page.locator("#root")).not.toBeEmpty();
  });
});
```

- [ ] **Step 5: Write logs smoke test**

Create `donna-ui/tests/e2e/smoke/logs.spec.ts`:

```ts
import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Logs smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("loads without crashing", async ({ page }) => {
    await page.goto("/logs");
    await expect(page.locator("#root")).not.toBeEmpty();
  });
});
```

- [ ] **Step 6: Write agents smoke test**

Create `donna-ui/tests/e2e/smoke/agents.spec.ts`:

```ts
import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Agents smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("loads without crashing", async ({ page }) => {
    await page.goto("/agents");
    await expect(page.locator("#root")).not.toBeEmpty();
  });
});
```

- [ ] **Step 7: Write configs smoke test**

Create `donna-ui/tests/e2e/smoke/configs.spec.ts`:

```ts
import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Configs smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("loads without crashing", async ({ page }) => {
    await page.goto("/configs");
    await expect(page.locator("#root")).not.toBeEmpty();
  });
});
```

- [ ] **Step 8: Write prompts smoke test**

Create `donna-ui/tests/e2e/smoke/prompts.spec.ts`:

```ts
import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Prompts smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("loads without crashing", async ({ page }) => {
    await page.goto("/prompts");
    await expect(page.locator("#root")).not.toBeEmpty();
  });
});
```

- [ ] **Step 9: Write shadow smoke test**

Create `donna-ui/tests/e2e/smoke/shadow.spec.ts`:

```ts
import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Shadow smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("loads without crashing", async ({ page }) => {
    await page.goto("/shadow");
    await expect(page.locator("#root")).not.toBeEmpty();
  });
});
```

- [ ] **Step 10: Write preferences smoke test**

Create `donna-ui/tests/e2e/smoke/preferences.spec.ts`:

```ts
import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("Preferences smoke", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("loads without crashing", async ({ page }) => {
    await page.goto("/preferences");
    await expect(page.locator("#root")).not.toBeEmpty();
  });
});
```

- [ ] **Step 11: Add test scripts to package.json**

Open `donna-ui/package.json` and modify the `scripts` section. The full `scripts` block should read:

```json
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "lint": "eslint .",
    "test:e2e": "playwright test",
    "test:e2e:ui": "playwright test --ui"
  },
```

- [ ] **Step 12: Add Playwright output to gitignore**

Modify `donna-ui/.gitignore` — append at the bottom:

```
# Playwright
/test-results/
/playwright-report/
/playwright/.cache/
```

- [ ] **Step 13: Run the smoke tests**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npm run test:e2e
```

Expected: all 10 tests pass (3 dashboard + 1 per other page × 7). If a test fails because the dev server didn't boot, run `npm run dev` in a separate terminal and retry.

- [ ] **Step 14: Commit**

```bash
git add donna-ui/playwright.config.ts donna-ui/tests donna-ui/package.json donna-ui/package-lock.json donna-ui/.gitignore
git commit -m "Add Playwright smoke tests for all 8 pages"
```

---

### Task 9: Record bundle baseline

**Files:**
- Create: `docs/superpowers/specs/bundle-baseline.txt`

- [ ] **Step 1: Build the app**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npm run build
```

Expected: build succeeds. Vite prints a table of chunks with raw and gzipped sizes.

- [ ] **Step 2: Capture the gzipped JS total**

Copy the Vite build summary output. Identify every line with a `.js` file and its gzipped size (the column after `│ gzip:`). Sum the gzipped sizes.

For example, if the output looks like:

```
dist/assets/index-abc123.js       850.40 kB │ gzip: 260.00 kB
dist/assets/vendor-def456.js      900.00 kB │ gzip: 280.00 kB
dist/assets/monaco-ghi789.js      250.00 kB │ gzip:  75.00 kB
```

…then the total gzipped JS is `260 + 280 + 75 = 615 kB`.

- [ ] **Step 3: Write the baseline file**

Create `docs/superpowers/specs/bundle-baseline.txt`:

```
Donna UI bundle size baseline
Recorded: 2026-04-08 (Wave 0 Task 9)
Vite version: 6.x
Node version: <paste `node -v` output>

Raw Vite build output
---------------------
<paste the full Vite build summary here, all .js chunks with raw and gzip sizes>

Totals
------
Total uncompressed JS:  <sum of raw kB>
Total gzipped JS:       <sum of gzip kB>  ← Wave 9 measures against this
```

Fill in the placeholder values by running `node -v` and copying the Vite output.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/bundle-baseline.txt
git commit -m "Record Wave 0 bundle size baseline"
```

---

### Task 10: Install Wave 1 dependencies

**Files:**
- Modify: `donna-ui/package.json`

- [ ] **Step 1: Install Radix primitives**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npm install \
  @radix-ui/react-dialog \
  @radix-ui/react-dropdown-menu \
  @radix-ui/react-popover \
  @radix-ui/react-select \
  @radix-ui/react-switch \
  @radix-ui/react-tabs \
  @radix-ui/react-tooltip \
  @radix-ui/react-scroll-area \
  @radix-ui/react-checkbox \
  @radix-ui/react-slot \
  @radix-ui/react-visually-hidden
```

- [ ] **Step 2: Install data + form + toast libraries**

```bash
npm install \
  @tanstack/react-table \
  @tanstack/react-virtual \
  react-hook-form \
  @hookform/resolvers \
  zod \
  sonner \
  react-day-picker
```

- [ ] **Step 3: Install markdown libraries (needed by Wave 7, safe to install now)**

```bash
npm install react-markdown rehype-sanitize rehype-highlight
```

- [ ] **Step 4: Type check and build**

```bash
npx tsc --noEmit
npm run build
```

Both expected to succeed.

- [ ] **Step 5: Run smoke tests again to confirm nothing regressed**

```bash
npm run test:e2e
```

Expected: all 10 smoke tests still pass.

- [ ] **Step 6: Commit**

```bash
git add donna-ui/package.json donna-ui/package-lock.json
git commit -m "Install Wave 1 deps (Radix, TanStack, RHF, Sonner)"
```

---

## Wave 1 · Primitives Library

### Task 11: Create cn() helper

**Files:**
- Create: `donna-ui/src/lib/cn.ts`

- [ ] **Step 1: Write the helper**

Create `donna-ui/src/lib/cn.ts`:

```ts
import clsx, { type ClassValue } from "clsx";

/**
 * Compose className values. Thin alias around clsx so every primitive
 * imports from the same place. If we later want tailwind-merge we add
 * it here and only here.
 */
export function cn(...inputs: ClassValue[]): string {
  return clsx(inputs);
}
```

- [ ] **Step 2: Commit**

```bash
git add donna-ui/src/lib/cn.ts
git commit -m "Add cn() className composition helper"
```

---

### Task 12: Create dev-only primitives story route

Creates the host page for all subsequent primitives. Each later task appends a `<StorySection>` to this file.

**Files:**
- Create: `donna-ui/src/pages/DevPrimitives/index.tsx`
- Create: `donna-ui/src/pages/DevPrimitives/StorySection.tsx`
- Create: `donna-ui/src/pages/DevPrimitives/DevPrimitives.module.css`
- Modify: `donna-ui/src/App.tsx`

- [ ] **Step 1: Write the story page CSS**

Create `donna-ui/src/pages/DevPrimitives/DevPrimitives.module.css`:

```css
.root {
  min-height: 100vh;
  background: var(--color-bg);
  color: var(--color-text);
  padding: var(--space-6) var(--space-7);
  font-family: var(--font-body);
}

.header {
  margin-bottom: var(--space-6);
  padding-bottom: var(--space-5);
  border-bottom: 1px solid var(--color-border);
}

.eyebrow {
  font-size: var(--text-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--color-text-muted);
  font-weight: 500;
  margin-bottom: var(--space-3);
}

.title {
  font-family: var(--font-display);
  font-weight: 300;
  font-size: var(--text-page-title);
  letter-spacing: var(--tracking-tight);
  line-height: var(--leading-tight);
  margin: 0;
}

.meta {
  font-size: var(--text-label);
  color: var(--color-text-muted);
  margin-top: var(--space-2);
}

.section {
  margin-bottom: var(--space-6);
  padding-bottom: var(--space-5);
  border-bottom: 1px solid var(--color-border-subtle);
}

.sectionLabel {
  font-size: var(--text-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--color-text-muted);
  font-weight: 500;
  margin-bottom: var(--space-3);
}

.sectionTitle {
  font-family: var(--font-display);
  font-weight: 300;
  font-size: var(--text-section);
  margin: 0 0 var(--space-3) 0;
}

.sectionNote {
  font-size: var(--text-body);
  color: var(--color-text-muted);
  margin-bottom: var(--space-4);
}

.stage {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-card);
  padding: var(--space-5);
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  align-items: center;
}
```

- [ ] **Step 2: Write the StorySection helper**

Create `donna-ui/src/pages/DevPrimitives/StorySection.tsx`:

```tsx
import type { ReactNode } from "react";
import styles from "./DevPrimitives.module.css";

interface StorySectionProps {
  id: string;
  eyebrow: string;
  title: string;
  note?: string;
  children: ReactNode;
}

/**
 * Layout wrapper for a single primitive story.
 * Each primitive task appends one <StorySection> to the dev page.
 */
export function StorySection({ id, eyebrow, title, note, children }: StorySectionProps) {
  return (
    <section id={id} className={styles.section} data-testid={`story-${id}`}>
      <div className={styles.sectionLabel}>{eyebrow}</div>
      <h2 className={styles.sectionTitle}>{title}</h2>
      {note && <p className={styles.sectionNote}>{note}</p>}
      <div className={styles.stage}>{children}</div>
    </section>
  );
}
```

- [ ] **Step 3: Write the story page**

Create `donna-ui/src/pages/DevPrimitives/index.tsx`:

```tsx
import styles from "./DevPrimitives.module.css";
import { StorySection } from "./StorySection";

/**
 * Dev-only primitives gallery. Gated behind import.meta.env.DEV in App.tsx.
 * Each primitive task in the plan appends a StorySection below.
 * Stays after production launch for reference (see Wave 9 cleanup).
 */
export default function DevPrimitivesPage() {
  return (
    <div className={styles.root}>
      <header className={styles.header}>
        <div className={styles.eyebrow}>Dev · Primitives</div>
        <h1 className={styles.title}>Donna Primitive Library</h1>
        <p className={styles.meta}>
          Press Cmd+. to flip themes. Every primitive renders here first, before it lands on a page.
        </p>
      </header>

      {/* Stories appended by subsequent plan tasks */}
    </div>
  );
}
```

- [ ] **Step 4: Register the route behind a dev gate**

Modify `donna-ui/src/App.tsx`. The final file should read:

```tsx
import { Routes, Route } from "react-router-dom";
import AppLayout from "./components/Layout";
import ErrorBoundary from "./components/ErrorBoundary";
import Dashboard from "./pages/Dashboard";
import Logs from "./pages/Logs";
import ConfigsPage from "./pages/Configs";
import PromptsPage from "./pages/Prompts";
import AgentsPage from "./pages/Agents";
import TasksPage from "./pages/Tasks";
import TaskDetail from "./pages/Tasks/TaskDetail";
import ShadowPage from "./pages/Shadow";
import PreferencesPage from "./pages/Preferences";
import DevPrimitivesPage from "./pages/DevPrimitives";
import { useTheme } from "./hooks/useTheme";

export default function App() {
  useTheme();

  return (
    <Routes>
      {/* Dev-only primitives gallery — outside AppLayout so it renders standalone */}
      {import.meta.env.DEV && (
        <Route path="/dev/primitives" element={<DevPrimitivesPage />} />
      )}
      <Route element={<AppLayout />}>
        <Route path="/" element={<ErrorBoundary><Dashboard /></ErrorBoundary>} />
        <Route path="/logs" element={<ErrorBoundary><Logs /></ErrorBoundary>} />
        <Route path="/configs" element={<ErrorBoundary><ConfigsPage /></ErrorBoundary>} />
        <Route path="/prompts" element={<ErrorBoundary><PromptsPage /></ErrorBoundary>} />
        <Route path="/agents" element={<ErrorBoundary><AgentsPage /></ErrorBoundary>} />
        <Route path="/tasks" element={<ErrorBoundary><TasksPage /></ErrorBoundary>} />
        <Route path="/tasks/:id" element={<ErrorBoundary><TaskDetail /></ErrorBoundary>} />
        <Route path="/shadow" element={<ErrorBoundary><ShadowPage /></ErrorBoundary>} />
        <Route path="/preferences" element={<ErrorBoundary><PreferencesPage /></ErrorBoundary>} />
      </Route>
    </Routes>
  );
}
```

- [ ] **Step 5: Manual check**

```bash
npm run dev
```

Open `http://localhost:5173/dev/primitives`. Expected: page title "Donna Primitive Library", eyebrow "DEV · PRIMITIVES", the Fraunces display font, dark warm background.

Try `/dev/primitives` in a prod build:

```bash
npm run build && npm run preview
```

Expected: `/dev/primitives` returns a 404 / route-not-matched (blank content inside AppLayout) because `import.meta.env.DEV` is false. Stop preview server.

- [ ] **Step 6: Commit**

```bash
git add donna-ui/src/pages/DevPrimitives donna-ui/src/App.tsx
git commit -m "Add dev-only /dev/primitives story page"
```

---

### Task 13: Button primitive

**Files:**
- Create: `donna-ui/src/primitives/Button.tsx`
- Create: `donna-ui/src/primitives/Button.module.css`
- Modify: `donna-ui/src/pages/DevPrimitives/index.tsx`

- [ ] **Step 1: Write the CSS module**

Create `donna-ui/src/primitives/Button.module.css`:

```css
.button {
  font-family: var(--font-body);
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  padding: 10px 20px;
  border-radius: var(--radius-control);
  border: 1px solid transparent;
  cursor: pointer;
  transition: transform var(--duration-fast) var(--ease-out),
              border-color var(--duration-fast) var(--ease-out),
              color var(--duration-fast) var(--ease-out),
              background var(--duration-fast) var(--ease-out);
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  white-space: nowrap;
}

.button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.primary {
  background: var(--color-accent);
  color: var(--color-accent-contrast);
}
.primary:hover:not(:disabled) {
  transform: translateY(-1px);
}

.ghost {
  background: transparent;
  color: var(--color-text);
  border-color: var(--color-border);
}
.ghost:hover:not(:disabled) {
  border-color: var(--color-accent);
  color: var(--color-accent);
}

.text {
  background: transparent;
  color: var(--color-text-muted);
  padding: 10px 0;
  letter-spacing: 0.06em;
}
.text:hover:not(:disabled) {
  color: var(--color-accent);
}

.sm {
  padding: 6px 14px;
  font-size: 9px;
}

.lg {
  padding: 13px 26px;
  font-size: 11px;
}
```

- [ ] **Step 2: Write the Button component**

Create `donna-ui/src/primitives/Button.tsx`:

```tsx
import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "../lib/cn";
import styles from "./Button.module.css";

export type ButtonVariant = "primary" | "ghost" | "text";
export type ButtonSize = "sm" | "md" | "lg";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = "primary", size = "md", className, children, ...rest }, ref) => (
    <button
      ref={ref}
      className={cn(
        styles.button,
        styles[variant],
        size === "sm" && styles.sm,
        size === "lg" && styles.lg,
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  ),
);
Button.displayName = "Button";
```

- [ ] **Step 3: Append Button story to the dev page**

Modify `donna-ui/src/pages/DevPrimitives/index.tsx`. Add the import at the top:

```tsx
import { Button } from "../../primitives/Button";
```

Inside the `<div className={styles.root}>` replace the `{/* Stories appended by subsequent plan tasks */}` line with:

```tsx
      <StorySection
        id="button"
        eyebrow="Primitive · 01"
        title="Button"
        note="Three variants × three sizes. All use var(--color-accent), flip the theme with Cmd+. to see them update."
      >
        <Button>Primary</Button>
        <Button variant="ghost">Ghost</Button>
        <Button variant="text">Text →</Button>
        <Button size="sm">Small</Button>
        <Button size="lg">Large</Button>
        <Button disabled>Disabled</Button>
      </StorySection>

      {/* Stories appended by subsequent plan tasks */}
```

- [ ] **Step 4: Type check + visual check**

```bash
npx tsc --noEmit
npm run dev
```

Open `/dev/primitives`. Expected: Button section with six buttons. Press Cmd+. and all accent-colored buttons turn coral. Stop dev.

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/primitives/Button.tsx donna-ui/src/primitives/Button.module.css donna-ui/src/pages/DevPrimitives/index.tsx
git commit -m "Add Button primitive"
```

---

### Task 14: Card primitive (Card, CardHeader, CardTitle, CardEyebrow)

**Files:**
- Create: `donna-ui/src/primitives/Card.tsx`
- Create: `donna-ui/src/primitives/Card.module.css`
- Modify: `donna-ui/src/pages/DevPrimitives/index.tsx`

- [ ] **Step 1: Write the CSS**

Create `donna-ui/src/primitives/Card.module.css`:

```css
.card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-card);
  padding: var(--space-5);
  transition: border-color var(--duration-fast) var(--ease-out);
}

.card:hover {
  border-color: var(--color-text-dim);
}

.header {
  margin-bottom: var(--space-3);
}

.eyebrow {
  font-size: var(--text-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--color-text-muted);
  font-weight: 500;
  margin-bottom: var(--space-1);
}

.title {
  font-family: var(--font-display);
  font-weight: 300;
  font-size: var(--text-section);
  letter-spacing: var(--tracking-normal);
  line-height: var(--leading-snug);
  color: var(--color-text);
  margin: 0;
}
```

- [ ] **Step 2: Write the component**

Create `donna-ui/src/primitives/Card.tsx`:

```tsx
import { forwardRef, type HTMLAttributes } from "react";
import { cn } from "../lib/cn";
import styles from "./Card.module.css";

export const Card = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, children, ...rest }, ref) => (
    <div ref={ref} className={cn(styles.card, className)} {...rest}>
      {children}
    </div>
  ),
);
Card.displayName = "Card";

export const CardHeader = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, children, ...rest }, ref) => (
    <div ref={ref} className={cn(styles.header, className)} {...rest}>
      {children}
    </div>
  ),
);
CardHeader.displayName = "CardHeader";

export const CardEyebrow = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, children, ...rest }, ref) => (
    <div ref={ref} className={cn(styles.eyebrow, className)} {...rest}>
      {children}
    </div>
  ),
);
CardEyebrow.displayName = "CardEyebrow";

export const CardTitle = forwardRef<HTMLHeadingElement, HTMLAttributes<HTMLHeadingElement>>(
  ({ className, children, ...rest }, ref) => (
    <h3 ref={ref} className={cn(styles.title, className)} {...rest}>
      {children}
    </h3>
  ),
);
CardTitle.displayName = "CardTitle";
```

- [ ] **Step 3: Append story**

In `donna-ui/src/pages/DevPrimitives/index.tsx`, add to imports:

```tsx
import { Card, CardHeader, CardEyebrow, CardTitle } from "../../primitives/Card";
```

Append this `StorySection` before the `{/* Stories appended by subsequent plan tasks */}` marker:

```tsx
      <StorySection
        id="card"
        eyebrow="Primitive · 02"
        title="Card"
        note="Base container. Border lifts to text-dim on hover."
      >
        <Card style={{ width: 280 }}>
          <CardHeader>
            <CardEyebrow>Tasks Today</CardEyebrow>
            <CardTitle>Spend This Week</CardTitle>
          </CardHeader>
          <p style={{ color: "var(--color-text-muted)", fontSize: "var(--text-body)", margin: 0 }}>
            Card content. Reads from tokens, no inline hex anywhere.
          </p>
        </Card>
      </StorySection>

```

- [ ] **Step 4: Type check + visual**

```bash
npx tsc --noEmit
npm run dev
```

Verify the card appears under its section in `/dev/primitives`. Stop dev.

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/primitives/Card.tsx donna-ui/src/primitives/Card.module.css donna-ui/src/pages/DevPrimitives/index.tsx
git commit -m "Add Card primitive with Header/Eyebrow/Title"
```

---

### Task 15: Pill primitive

**Files:**
- Create: `donna-ui/src/primitives/Pill.tsx`
- Create: `donna-ui/src/primitives/Pill.module.css`
- Modify: `donna-ui/src/pages/DevPrimitives/index.tsx`

- [ ] **Step 1: Write the CSS**

Create `donna-ui/src/primitives/Pill.module.css`:

```css
.pill {
  display: inline-block;
  padding: 3px 9px;
  border-radius: var(--radius-control);
  font-size: var(--text-eyebrow);
  font-weight: 500;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  font-family: var(--font-body);
  border: 1px solid transparent;
}

.accent {
  background: var(--color-accent-soft);
  color: var(--color-accent);
  border-color: var(--color-accent-border);
}

.success {
  background: rgba(138, 166, 114, 0.10);
  color: var(--color-success);
  border-color: rgba(138, 166, 114, 0.28);
}

.warning {
  background: rgba(212, 169, 67, 0.10);
  color: var(--color-warning);
  border-color: rgba(212, 169, 67, 0.28);
}

.error {
  background: rgba(200, 102, 94, 0.10);
  color: var(--color-error);
  border-color: rgba(200, 102, 94, 0.28);
}

.muted {
  background: var(--color-inset);
  color: var(--color-text-muted);
  border-color: var(--color-border);
}
```

- [ ] **Step 2: Write the component**

Create `donna-ui/src/primitives/Pill.tsx`:

```tsx
import type { HTMLAttributes } from "react";
import { cn } from "../lib/cn";
import styles from "./Pill.module.css";

export type PillVariant = "accent" | "success" | "warning" | "error" | "muted";

interface PillProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: PillVariant;
}

export function Pill({ variant = "accent", className, children, ...rest }: PillProps) {
  return (
    <span className={cn(styles.pill, styles[variant], className)} {...rest}>
      {children}
    </span>
  );
}
```

- [ ] **Step 3: Append story**

In `donna-ui/src/pages/DevPrimitives/index.tsx`, add to imports:

```tsx
import { Pill } from "../../primitives/Pill";
```

Append before the tasks marker:

```tsx
      <StorySection
        id="pill"
        eyebrow="Primitive · 03"
        title="Pill"
        note="Status indicators. Semantic colors only appear when semantically required."
      >
        <Pill>Scheduled</Pill>
        <Pill variant="success">Done</Pill>
        <Pill variant="warning">At Risk</Pill>
        <Pill variant="error">Overdue</Pill>
        <Pill variant="muted">Backlog</Pill>
      </StorySection>

```

- [ ] **Step 4: Type check + visual**

```bash
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/primitives/Pill.tsx donna-ui/src/primitives/Pill.module.css donna-ui/src/pages/DevPrimitives/index.tsx
git commit -m "Add Pill primitive with 5 variants"
```

---

### Task 16: Input + Textarea + FormField primitives

**Files:**
- Create: `donna-ui/src/primitives/Input.tsx`
- Create: `donna-ui/src/primitives/Input.module.css`
- Modify: `donna-ui/src/pages/DevPrimitives/index.tsx`

- [ ] **Step 1: Write the CSS**

Create `donna-ui/src/primitives/Input.module.css`:

```css
.input,
.textarea {
  font-family: var(--font-body);
  font-size: var(--text-body);
  color: var(--color-text);
  background: var(--color-inset);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-control);
  padding: 10px 14px;
  width: 100%;
  transition: border-color var(--duration-fast) var(--ease-out);
  outline: none;
}

.textarea {
  resize: vertical;
  min-height: 80px;
  font-family: var(--font-body);
  line-height: var(--leading-normal);
}

.input::placeholder,
.textarea::placeholder {
  color: var(--color-text-dim);
}

.input:hover,
.textarea:hover {
  border-color: var(--color-text-dim);
}

.input:focus,
.textarea:focus {
  border-color: var(--color-accent);
}

.input[aria-invalid="true"],
.textarea[aria-invalid="true"] {
  border-color: var(--color-error);
}

.field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  width: 100%;
}

.label {
  font-size: var(--text-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--color-text-muted);
  font-weight: 500;
}

.error {
  font-size: var(--text-label);
  color: var(--color-error);
  margin-top: var(--space-1);
}
```

- [ ] **Step 2: Write the component**

Create `donna-ui/src/primitives/Input.tsx`:

```tsx
import { forwardRef, useId, type InputHTMLAttributes, type TextareaHTMLAttributes, type ReactNode } from "react";
import { cn } from "../lib/cn";
import styles from "./Input.module.css";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...rest }, ref) => (
    <input ref={ref} className={cn(styles.input, className)} {...rest} />
  ),
);
Input.displayName = "Input";

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...rest }, ref) => (
    <textarea ref={ref} className={cn(styles.textarea, className)} {...rest} />
  ),
);
Textarea.displayName = "Textarea";

interface FormFieldProps {
  label: string;
  error?: string;
  children: (props: { id: string; "aria-invalid"?: boolean; "aria-describedby"?: string }) => ReactNode;
}

/**
 * Label + input wrapper. Render-prop API so it works with any input primitive.
 * Generates stable ids and wires aria-invalid / aria-describedby for you.
 */
export function FormField({ label, error, children }: FormFieldProps) {
  const id = useId();
  const errorId = `${id}-error`;
  return (
    <div className={styles.field}>
      <label htmlFor={id} className={styles.label}>{label}</label>
      {children({
        id,
        "aria-invalid": error ? true : undefined,
        "aria-describedby": error ? errorId : undefined,
      })}
      {error && <div id={errorId} className={styles.error}>{error}</div>}
    </div>
  );
}
```

- [ ] **Step 3: Append story**

In `donna-ui/src/pages/DevPrimitives/index.tsx`, add to imports:

```tsx
import { Input, Textarea, FormField } from "../../primitives/Input";
```

Append before the tasks marker:

```tsx
      <StorySection
        id="input"
        eyebrow="Primitive · 04"
        title="Input, Textarea, FormField"
        note="FormField wires labels, ids, and aria-describedby automatically."
      >
        <div style={{ display: "grid", gap: "var(--space-3)", width: 320 }}>
          <FormField label="Task Title">
            {(p) => <Input placeholder="Draft Q2 budget memo" {...p} />}
          </FormField>
          <FormField label="Notes">
            {(p) => <Textarea placeholder="Include variance vs Q1…" {...p} />}
          </FormField>
          <FormField label="Invalid Example" error="Title is required">
            {(p) => <Input {...p} />}
          </FormField>
        </div>
      </StorySection>

```

- [ ] **Step 4: Type check**

```bash
npx tsc --noEmit
```

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/primitives/Input.tsx donna-ui/src/primitives/Input.module.css donna-ui/src/pages/DevPrimitives/index.tsx
git commit -m "Add Input, Textarea, FormField primitives"
```

---

### Task 17: Select primitive (Radix)

**Files:**
- Create: `donna-ui/src/primitives/Select.tsx`
- Create: `donna-ui/src/primitives/Select.module.css`
- Modify: `donna-ui/src/pages/DevPrimitives/index.tsx`

- [ ] **Step 1: Write the CSS**

Create `donna-ui/src/primitives/Select.module.css`:

```css
.trigger {
  font-family: var(--font-body);
  font-size: var(--text-body);
  color: var(--color-text);
  background: var(--color-inset);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-control);
  padding: 10px 14px;
  min-width: 180px;
  display: inline-flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  cursor: pointer;
  transition: border-color var(--duration-fast) var(--ease-out);
}
.trigger:hover { border-color: var(--color-text-dim); }
.trigger[data-state="open"],
.trigger:focus-visible { border-color: var(--color-accent); outline: none; }

.icon {
  color: var(--color-text-muted);
  display: inline-flex;
}

.content {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-card);
  padding: var(--space-1);
  box-shadow: var(--shadow-dialog);
  z-index: var(--z-popover);
  min-width: var(--radix-select-trigger-width);
  max-height: var(--radix-select-content-available-height);
  overflow: auto;
}

.item {
  font-family: var(--font-body);
  font-size: var(--text-body);
  color: var(--color-text-secondary);
  padding: 8px 12px;
  border-radius: var(--radius-control);
  cursor: pointer;
  outline: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.item[data-highlighted] {
  background: var(--color-accent-soft);
  color: var(--color-accent);
}
.item[data-state="checked"] { color: var(--color-accent); }
```

- [ ] **Step 2: Write the component**

Create `donna-ui/src/primitives/Select.tsx`:

```tsx
import * as RadixSelect from "@radix-ui/react-select";
import { ChevronDown, Check } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../lib/cn";
import styles from "./Select.module.css";

interface SelectProps {
  value: string;
  onValueChange: (v: string) => void;
  placeholder?: string;
  children: ReactNode;
  id?: string;
  "aria-invalid"?: boolean;
  "aria-describedby"?: string;
}

export function Select({ value, onValueChange, placeholder, children, ...aria }: SelectProps) {
  return (
    <RadixSelect.Root value={value} onValueChange={onValueChange}>
      <RadixSelect.Trigger className={styles.trigger} {...aria}>
        <RadixSelect.Value placeholder={placeholder} />
        <RadixSelect.Icon className={styles.icon}>
          <ChevronDown size={14} />
        </RadixSelect.Icon>
      </RadixSelect.Trigger>
      <RadixSelect.Portal>
        <RadixSelect.Content className={styles.content} position="popper" sideOffset={4}>
          <RadixSelect.Viewport>{children}</RadixSelect.Viewport>
        </RadixSelect.Content>
      </RadixSelect.Portal>
    </RadixSelect.Root>
  );
}

interface SelectItemProps {
  value: string;
  children: ReactNode;
}

export function SelectItem({ value, children }: SelectItemProps) {
  return (
    <RadixSelect.Item value={value} className={cn(styles.item)}>
      <RadixSelect.ItemText>{children}</RadixSelect.ItemText>
      <RadixSelect.ItemIndicator>
        <Check size={12} />
      </RadixSelect.ItemIndicator>
    </RadixSelect.Item>
  );
}
```

- [ ] **Step 3: Append story**

In `donna-ui/src/pages/DevPrimitives/index.tsx`, add to imports:

```tsx
import { useState } from "react";
import { Select, SelectItem } from "../../primitives/Select";
```

At the top of `DevPrimitivesPage()`, add:

```tsx
  const [selectValue, setSelectValue] = useState("scheduled");
```

Append before the tasks marker:

```tsx
      <StorySection
        id="select"
        eyebrow="Primitive · 05"
        title="Select"
        note="Radix Select wrapped with our chrome. Full keyboard nav built in."
      >
        <Select value={selectValue} onValueChange={setSelectValue} placeholder="Select a status">
          <SelectItem value="scheduled">Scheduled</SelectItem>
          <SelectItem value="in_progress">In Progress</SelectItem>
          <SelectItem value="blocked">Blocked</SelectItem>
          <SelectItem value="done">Done</SelectItem>
        </Select>
      </StorySection>

```

- [ ] **Step 4: Type check + visual**

```bash
npx tsc --noEmit
npm run dev
```

Open `/dev/primitives`, scroll to Select. Click — dropdown should open styled. Keyboard arrows should move highlight. Stop dev.

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/primitives/Select.tsx donna-ui/src/primitives/Select.module.css donna-ui/src/pages/DevPrimitives/index.tsx
git commit -m "Add Select primitive on Radix"
```

---

### Task 18: Checkbox primitive

**Files:**
- Create: `donna-ui/src/primitives/Checkbox.tsx`
- Create: `donna-ui/src/primitives/Checkbox.module.css`
- Modify: `donna-ui/src/pages/DevPrimitives/index.tsx`

- [ ] **Step 1: Write the CSS**

Create `donna-ui/src/primitives/Checkbox.module.css`:

```css
.root {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  cursor: pointer;
  font-family: var(--font-body);
  font-size: var(--text-body);
  color: var(--color-text);
  user-select: none;
}

.box {
  width: 16px;
  height: 16px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-control);
  background: var(--color-inset);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: border-color var(--duration-fast) var(--ease-out),
              background var(--duration-fast) var(--ease-out);
}

.root:hover .box { border-color: var(--color-text-dim); }
.box[data-state="checked"] {
  background: var(--color-accent);
  border-color: var(--color-accent);
  color: var(--color-accent-contrast);
}
```

- [ ] **Step 2: Write the component**

Create `donna-ui/src/primitives/Checkbox.tsx`:

```tsx
import * as RadixCheckbox from "@radix-ui/react-checkbox";
import { Check } from "lucide-react";
import { useId, type ReactNode } from "react";
import styles from "./Checkbox.module.css";

interface CheckboxProps {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  children: ReactNode;
  disabled?: boolean;
}

export function Checkbox({ checked, onCheckedChange, children, disabled }: CheckboxProps) {
  const id = useId();
  return (
    <label htmlFor={id} className={styles.root}>
      <RadixCheckbox.Root
        id={id}
        className={styles.box}
        checked={checked}
        onCheckedChange={(v) => onCheckedChange(v === true)}
        disabled={disabled}
      >
        <RadixCheckbox.Indicator>
          <Check size={11} />
        </RadixCheckbox.Indicator>
      </RadixCheckbox.Root>
      {children}
    </label>
  );
}
```

- [ ] **Step 3: Append story**

In `donna-ui/src/pages/DevPrimitives/index.tsx`, add to imports:

```tsx
import { Checkbox } from "../../primitives/Checkbox";
```

At the top of the component, alongside other useState calls:

```tsx
  const [cb1, setCb1] = useState(true);
  const [cb2, setCb2] = useState(false);
```

Append before the tasks marker:

```tsx
      <StorySection
        id="checkbox"
        eyebrow="Primitive · 06"
        title="Checkbox"
      >
        <Checkbox checked={cb1} onCheckedChange={setCb1}>Show completed</Checkbox>
        <Checkbox checked={cb2} onCheckedChange={setCb2}>Include archived</Checkbox>
      </StorySection>

```

- [ ] **Step 4: Type check**

```bash
npx tsc --noEmit
```

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/primitives/Checkbox.tsx donna-ui/src/primitives/Checkbox.module.css donna-ui/src/pages/DevPrimitives/index.tsx
git commit -m "Add Checkbox primitive"
```

---

### Task 19: Switch primitive

**Files:**
- Create: `donna-ui/src/primitives/Switch.tsx`
- Create: `donna-ui/src/primitives/Switch.module.css`
- Modify: `donna-ui/src/pages/DevPrimitives/index.tsx`

- [ ] **Step 1: Write the CSS**

Create `donna-ui/src/primitives/Switch.module.css`:

```css
.root {
  width: 32px;
  height: 18px;
  background: var(--color-inset);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  position: relative;
  cursor: pointer;
  transition: background var(--duration-fast) var(--ease-out),
              border-color var(--duration-fast) var(--ease-out);
}
.root[data-state="checked"] {
  background: var(--color-accent);
  border-color: var(--color-accent);
}
.thumb {
  display: block;
  width: 12px;
  height: 12px;
  background: var(--color-text-muted);
  border-radius: 50%;
  transform: translate(2px, 0);
  transition: transform var(--duration-fast) var(--ease-out),
              background var(--duration-fast) var(--ease-out);
  will-change: transform;
}
.root[data-state="checked"] .thumb {
  transform: translate(16px, 0);
  background: var(--color-accent-contrast);
}

.label {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-family: var(--font-body);
  font-size: var(--text-body);
  color: var(--color-text);
  cursor: pointer;
}
```

- [ ] **Step 2: Write the component**

Create `donna-ui/src/primitives/Switch.tsx`:

```tsx
import * as RadixSwitch from "@radix-ui/react-switch";
import { useId, type ReactNode } from "react";
import styles from "./Switch.module.css";

interface SwitchProps {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  children?: ReactNode;
  disabled?: boolean;
}

export function Switch({ checked, onCheckedChange, children, disabled }: SwitchProps) {
  const id = useId();
  const control = (
    <RadixSwitch.Root
      id={id}
      className={styles.root}
      checked={checked}
      onCheckedChange={onCheckedChange}
      disabled={disabled}
    >
      <RadixSwitch.Thumb className={styles.thumb} />
    </RadixSwitch.Root>
  );
  if (!children) return control;
  return (
    <label htmlFor={id} className={styles.label}>
      {control}
      {children}
    </label>
  );
}
```

- [ ] **Step 3: Append story**

Imports:

```tsx
import { Switch } from "../../primitives/Switch";
```

useState:

```tsx
  const [sw, setSw] = useState(false);
```

StorySection:

```tsx
      <StorySection
        id="switch"
        eyebrow="Primitive · 07"
        title="Switch"
      >
        <Switch checked={sw} onCheckedChange={setSw}>Notify on overdue</Switch>
      </StorySection>

```

- [ ] **Step 4: Type check**

```bash
npx tsc --noEmit
```

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/primitives/Switch.tsx donna-ui/src/primitives/Switch.module.css donna-ui/src/pages/DevPrimitives/index.tsx
git commit -m "Add Switch primitive"
```

---

### Task 20: Tabs primitive

**Files:**
- Create: `donna-ui/src/primitives/Tabs.tsx`
- Create: `donna-ui/src/primitives/Tabs.module.css`
- Modify: `donna-ui/src/pages/DevPrimitives/index.tsx`

- [ ] **Step 1: Write the CSS**

Create `donna-ui/src/primitives/Tabs.module.css`:

```css
.root { display: flex; flex-direction: column; gap: var(--space-4); width: 100%; }

.list {
  display: flex;
  gap: var(--space-5);
  border-bottom: 1px solid var(--color-border);
}

.trigger {
  padding: 10px 0;
  font-family: var(--font-body);
  font-size: 10px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--color-text-muted);
  background: transparent;
  border: 0;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  cursor: pointer;
  transition: color var(--duration-fast) var(--ease-out),
              border-color var(--duration-fast) var(--ease-out);
  outline: none;
}
.trigger:hover { color: var(--color-text-secondary); }
.trigger[data-state="active"] {
  color: var(--color-text);
  border-bottom-color: var(--color-accent);
}

.content {
  color: var(--color-text-secondary);
  font-size: var(--text-body);
  outline: none;
}
```

- [ ] **Step 2: Write the component**

Create `donna-ui/src/primitives/Tabs.tsx`:

```tsx
import * as RadixTabs from "@radix-ui/react-tabs";
import type { ReactNode } from "react";
import { cn } from "../lib/cn";
import styles from "./Tabs.module.css";

interface TabsProps {
  value: string;
  onValueChange: (v: string) => void;
  children: ReactNode;
}

export function Tabs({ value, onValueChange, children }: TabsProps) {
  return (
    <RadixTabs.Root className={styles.root} value={value} onValueChange={onValueChange}>
      {children}
    </RadixTabs.Root>
  );
}

export function TabsList({ children }: { children: ReactNode }) {
  return <RadixTabs.List className={styles.list}>{children}</RadixTabs.List>;
}

export function TabsTrigger({ value, children }: { value: string; children: ReactNode }) {
  return (
    <RadixTabs.Trigger value={value} className={cn(styles.trigger)}>
      {children}
    </RadixTabs.Trigger>
  );
}

export function TabsContent({ value, children }: { value: string; children: ReactNode }) {
  return (
    <RadixTabs.Content value={value} className={styles.content}>
      {children}
    </RadixTabs.Content>
  );
}
```

- [ ] **Step 3: Append story**

Imports:

```tsx
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../../primitives/Tabs";
```

useState:

```tsx
  const [tab, setTab] = useState("edit");
```

StorySection:

```tsx
      <StorySection
        id="tabs"
        eyebrow="Primitive · 08"
        title="Tabs"
        note="Used by the Prompts editor (Edit / Preview / Split)."
      >
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            <TabsTrigger value="edit">Edit</TabsTrigger>
            <TabsTrigger value="preview">Preview</TabsTrigger>
            <TabsTrigger value="split">Split</TabsTrigger>
          </TabsList>
          <TabsContent value="edit">Edit panel content.</TabsContent>
          <TabsContent value="preview">Preview panel content.</TabsContent>
          <TabsContent value="split">Split panel content.</TabsContent>
        </Tabs>
      </StorySection>

```

- [ ] **Step 4: Type check**

```bash
npx tsc --noEmit
```

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/primitives/Tabs.tsx donna-ui/src/primitives/Tabs.module.css donna-ui/src/pages/DevPrimitives/index.tsx
git commit -m "Add Tabs primitive"
```

---

### Task 21: Tooltip primitive

**Files:**
- Create: `donna-ui/src/primitives/Tooltip.tsx`
- Create: `donna-ui/src/primitives/Tooltip.module.css`
- Modify: `donna-ui/src/pages/DevPrimitives/index.tsx`
- Modify: `donna-ui/src/App.tsx` (mount TooltipProvider)

- [ ] **Step 1: Write the CSS**

Create `donna-ui/src/primitives/Tooltip.module.css`:

```css
.content {
  background: var(--color-surface);
  color: var(--color-text);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-control);
  padding: 6px 10px;
  font-family: var(--font-body);
  font-size: var(--text-label);
  box-shadow: var(--shadow-dialog);
  z-index: var(--z-popover);
  user-select: none;
}

.arrow {
  fill: var(--color-surface);
}
```

- [ ] **Step 2: Write the component**

Create `donna-ui/src/primitives/Tooltip.tsx`:

```tsx
import * as RadixTooltip from "@radix-ui/react-tooltip";
import type { ReactElement, ReactNode } from "react";
import styles from "./Tooltip.module.css";

/**
 * Wrap one child with a tooltip. 400ms delay (Radix default is 700).
 * TooltipProvider is mounted once in App.tsx — do NOT add it here.
 */
export function Tooltip({
  content,
  children,
  side = "top",
}: {
  content: ReactNode;
  children: ReactElement;
  side?: "top" | "right" | "bottom" | "left";
}) {
  return (
    <RadixTooltip.Root>
      <RadixTooltip.Trigger asChild>{children}</RadixTooltip.Trigger>
      <RadixTooltip.Portal>
        <RadixTooltip.Content className={styles.content} side={side} sideOffset={6}>
          {content}
          <RadixTooltip.Arrow className={styles.arrow} />
        </RadixTooltip.Content>
      </RadixTooltip.Portal>
    </RadixTooltip.Root>
  );
}
```

- [ ] **Step 3: Mount TooltipProvider globally**

Modify `donna-ui/src/App.tsx`. Final contents:

```tsx
import { Routes, Route } from "react-router-dom";
import * as RadixTooltip from "@radix-ui/react-tooltip";
import AppLayout from "./components/Layout";
import ErrorBoundary from "./components/ErrorBoundary";
import Dashboard from "./pages/Dashboard";
import Logs from "./pages/Logs";
import ConfigsPage from "./pages/Configs";
import PromptsPage from "./pages/Prompts";
import AgentsPage from "./pages/Agents";
import TasksPage from "./pages/Tasks";
import TaskDetail from "./pages/Tasks/TaskDetail";
import ShadowPage from "./pages/Shadow";
import PreferencesPage from "./pages/Preferences";
import DevPrimitivesPage from "./pages/DevPrimitives";
import { useTheme } from "./hooks/useTheme";

export default function App() {
  useTheme();

  return (
    <RadixTooltip.Provider delayDuration={400} skipDelayDuration={100}>
      <Routes>
        {import.meta.env.DEV && (
          <Route path="/dev/primitives" element={<DevPrimitivesPage />} />
        )}
        <Route element={<AppLayout />}>
          <Route path="/" element={<ErrorBoundary><Dashboard /></ErrorBoundary>} />
          <Route path="/logs" element={<ErrorBoundary><Logs /></ErrorBoundary>} />
          <Route path="/configs" element={<ErrorBoundary><ConfigsPage /></ErrorBoundary>} />
          <Route path="/prompts" element={<ErrorBoundary><PromptsPage /></ErrorBoundary>} />
          <Route path="/agents" element={<ErrorBoundary><AgentsPage /></ErrorBoundary>} />
          <Route path="/tasks" element={<ErrorBoundary><TasksPage /></ErrorBoundary>} />
          <Route path="/tasks/:id" element={<ErrorBoundary><TaskDetail /></ErrorBoundary>} />
          <Route path="/shadow" element={<ErrorBoundary><ShadowPage /></ErrorBoundary>} />
          <Route path="/preferences" element={<ErrorBoundary><PreferencesPage /></ErrorBoundary>} />
        </Route>
      </Routes>
    </RadixTooltip.Provider>
  );
}
```

- [ ] **Step 4: Append story**

Imports in DevPrimitives/index.tsx:

```tsx
import { Tooltip } from "../../primitives/Tooltip";
```

StorySection:

```tsx
      <StorySection
        id="tooltip"
        eyebrow="Primitive · 09"
        title="Tooltip"
        note="400ms delay (overrides Radix default of 700ms)."
      >
        <Tooltip content="Hover text uses the surface token">
          <Button variant="ghost">Hover me</Button>
        </Tooltip>
        <Tooltip content="Arrows render in the same color as the surface">
          <Button variant="text">And me →</Button>
        </Tooltip>
      </StorySection>

```

- [ ] **Step 5: Type check**

```bash
npx tsc --noEmit
```

- [ ] **Step 6: Commit**

```bash
git add donna-ui/src/primitives/Tooltip.tsx donna-ui/src/primitives/Tooltip.module.css donna-ui/src/pages/DevPrimitives/index.tsx donna-ui/src/App.tsx
git commit -m "Add Tooltip primitive with 400ms delay"
```

---

### Task 22: Dialog primitive (Modal)

**Files:**
- Create: `donna-ui/src/primitives/Dialog.tsx`
- Create: `donna-ui/src/primitives/Dialog.module.css`
- Modify: `donna-ui/src/pages/DevPrimitives/index.tsx`

- [ ] **Step 1: Write the CSS**

Create `donna-ui/src/primitives/Dialog.module.css`:

```css
.overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  z-index: var(--z-dialog);
  animation: fadeIn var(--duration-fast) var(--ease-out);
}

.content {
  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-card);
  padding: var(--space-5);
  width: 90vw;
  max-width: 560px;
  max-height: 85vh;
  overflow: auto;
  box-shadow: var(--shadow-dialog);
  z-index: var(--z-dialog);
  animation: fadeIn var(--duration-fast) var(--ease-out);
}

.header {
  margin-bottom: var(--space-4);
}

.title {
  font-family: var(--font-display);
  font-weight: 300;
  font-size: var(--text-section);
  margin: 0;
  color: var(--color-text);
}

.description {
  font-size: var(--text-body);
  color: var(--color-text-muted);
  margin: var(--space-2) 0 0 0;
}

.footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  margin-top: var(--space-5);
}

.close {
  position: absolute;
  top: var(--space-3);
  right: var(--space-3);
  background: transparent;
  border: 0;
  color: var(--color-text-muted);
  cursor: pointer;
  padding: 4px;
  border-radius: var(--radius-control);
}
.close:hover { color: var(--color-accent); }

@keyframes fadeIn {
  from { opacity: 0; }
  to   { opacity: 1; }
}
```

- [ ] **Step 2: Write the component**

Create `donna-ui/src/primitives/Dialog.tsx`:

```tsx
import * as RadixDialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import styles from "./Dialog.module.css";

interface DialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: ReactNode;
}

export function Dialog({ open, onOpenChange, children }: DialogProps) {
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className={styles.overlay} />
        <RadixDialog.Content className={styles.content}>
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

- [ ] **Step 3: Append story**

Imports:

```tsx
import { Dialog, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "../../primitives/Dialog";
```

useState:

```tsx
  const [dialogOpen, setDialogOpen] = useState(false);
```

StorySection:

```tsx
      <StorySection
        id="dialog"
        eyebrow="Primitive · 10"
        title="Dialog"
        note="Focus trap, Escape to close, backdrop click to close — all handled by Radix."
      >
        <Button onClick={() => setDialogOpen(true)}>Open Dialog</Button>
        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogHeader>
            <DialogTitle>Reschedule Task</DialogTitle>
            <DialogDescription>Pick a new time for "Draft Q2 budget memo."</DialogDescription>
          </DialogHeader>
          <p style={{ color: "var(--color-text-secondary)" }}>
            Dialog body content would go here.
          </p>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDialogOpen(false)}>Cancel</Button>
            <Button onClick={() => setDialogOpen(false)}>Confirm</Button>
          </DialogFooter>
        </Dialog>
      </StorySection>

```

- [ ] **Step 4: Type check + manual test**

```bash
npx tsc --noEmit
npm run dev
```

Open `/dev/primitives`, click "Open Dialog". Expected: focus trap works (Tab stays inside dialog), Escape closes it, backdrop click closes it. Stop dev.

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/primitives/Dialog.tsx donna-ui/src/primitives/Dialog.module.css donna-ui/src/pages/DevPrimitives/index.tsx
git commit -m "Add Dialog primitive (modal) on Radix"
```

---

### Task 23: Drawer primitive (side sheet)

**Files:**
- Create: `donna-ui/src/primitives/Drawer.tsx`
- Create: `donna-ui/src/primitives/Drawer.module.css`
- Modify: `donna-ui/src/pages/DevPrimitives/index.tsx`

- [ ] **Step 1: Write the CSS**

Create `donna-ui/src/primitives/Drawer.module.css`:

```css
.overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  z-index: var(--z-dialog);
}

.content {
  position: fixed;
  top: 0;
  bottom: 0;
  right: 0;
  width: 90vw;
  max-width: 480px;
  background: var(--color-surface);
  border-left: 1px solid var(--color-border);
  box-shadow: var(--shadow-drawer);
  padding: var(--space-5);
  z-index: var(--z-dialog);
  overflow: auto;
  animation: slideIn var(--duration-base) var(--ease-out);
}

.close {
  position: absolute;
  top: var(--space-3);
  right: var(--space-3);
  background: transparent;
  border: 0;
  color: var(--color-text-muted);
  cursor: pointer;
  padding: 4px;
}
.close:hover { color: var(--color-accent); }

.title {
  font-family: var(--font-display);
  font-weight: 300;
  font-size: var(--text-section);
  color: var(--color-text);
  margin: 0 0 var(--space-4) 0;
}

@keyframes slideIn {
  from { transform: translateX(100%); }
  to   { transform: translateX(0); }
}

@media (prefers-reduced-motion: reduce) {
  .content { animation: none; }
}
```

- [ ] **Step 2: Write the component**

Create `donna-ui/src/primitives/Drawer.tsx`:

```tsx
import * as RadixDialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import styles from "./Drawer.module.css";

/**
 * Side-sheet drawer — built on Radix Dialog since it gives us focus trap
 * and Escape handling for free. Slides in from the right.
 */
export function Drawer({
  open,
  onOpenChange,
  title,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  children: ReactNode;
}) {
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className={styles.overlay} />
        <RadixDialog.Content className={styles.content}>
          <RadixDialog.Title className={styles.title}>{title}</RadixDialog.Title>
          <RadixDialog.Description className="sr-only">
            Detail drawer
          </RadixDialog.Description>
          {children}
          <RadixDialog.Close className={styles.close} aria-label="Close">
            <X size={16} />
          </RadixDialog.Close>
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}
```

- [ ] **Step 3: Add .sr-only utility to reset.css**

Modify `donna-ui/src/theme/reset.css` — append at the bottom:

```css
/* Screen-reader-only utility for accessible hidden labels */
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
```

- [ ] **Step 4: Append story**

Imports:

```tsx
import { Drawer } from "../../primitives/Drawer";
```

useState:

```tsx
  const [drawerOpen, setDrawerOpen] = useState(false);
```

StorySection:

```tsx
      <StorySection
        id="drawer"
        eyebrow="Primitive · 11"
        title="Drawer"
        note="Side sheet on Radix Dialog. Slides in from the right, same a11y guarantees as Dialog."
      >
        <Button onClick={() => setDrawerOpen(true)}>Open Drawer</Button>
        <Drawer open={drawerOpen} onOpenChange={setDrawerOpen} title="Task Detail">
          <p style={{ color: "var(--color-text-secondary)" }}>
            Drawer body. Use this for task/log detail panels in later waves.
          </p>
        </Drawer>
      </StorySection>

```

- [ ] **Step 5: Type check**

```bash
npx tsc --noEmit
```

- [ ] **Step 6: Commit**

```bash
git add donna-ui/src/primitives/Drawer.tsx donna-ui/src/primitives/Drawer.module.css donna-ui/src/theme/reset.css donna-ui/src/pages/DevPrimitives/index.tsx
git commit -m "Add Drawer primitive and .sr-only utility"
```

---

### Task 24: DropdownMenu primitive

**Files:**
- Create: `donna-ui/src/primitives/DropdownMenu.tsx`
- Create: `donna-ui/src/primitives/DropdownMenu.module.css`
- Modify: `donna-ui/src/pages/DevPrimitives/index.tsx`

- [ ] **Step 1: Write the CSS**

Create `donna-ui/src/primitives/DropdownMenu.module.css`:

```css
.content {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-card);
  padding: var(--space-1);
  box-shadow: var(--shadow-dialog);
  min-width: 180px;
  z-index: var(--z-popover);
}

.item {
  font-family: var(--font-body);
  font-size: var(--text-body);
  color: var(--color-text-secondary);
  padding: 8px 12px;
  border-radius: var(--radius-control);
  cursor: pointer;
  outline: none;
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.item[data-highlighted] {
  background: var(--color-accent-soft);
  color: var(--color-accent);
}

.separator {
  height: 1px;
  background: var(--color-border);
  margin: var(--space-1) 0;
}
```

- [ ] **Step 2: Write the component**

Create `donna-ui/src/primitives/DropdownMenu.tsx`:

```tsx
import * as Radix from "@radix-ui/react-dropdown-menu";
import type { ReactNode } from "react";
import styles from "./DropdownMenu.module.css";

export function DropdownMenu({ children }: { children: ReactNode }) {
  return <Radix.Root>{children}</Radix.Root>;
}

export function DropdownMenuTrigger({ children }: { children: ReactNode }) {
  return <Radix.Trigger asChild>{children}</Radix.Trigger>;
}

export function DropdownMenuContent({ children }: { children: ReactNode }) {
  return (
    <Radix.Portal>
      <Radix.Content className={styles.content} sideOffset={4} align="end">
        {children}
      </Radix.Content>
    </Radix.Portal>
  );
}

export function DropdownMenuItem({ children, onSelect }: { children: ReactNode; onSelect?: () => void }) {
  return <Radix.Item className={styles.item} onSelect={onSelect}>{children}</Radix.Item>;
}

export function DropdownMenuSeparator() {
  return <Radix.Separator className={styles.separator} />;
}
```

- [ ] **Step 3: Append story**

Imports:

```tsx
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "../../primitives/DropdownMenu";
```

StorySection:

```tsx
      <StorySection
        id="dropdown"
        eyebrow="Primitive · 12"
        title="DropdownMenu"
      >
        <DropdownMenu>
          <DropdownMenuTrigger>
            <Button variant="ghost">Actions</Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent>
            <DropdownMenuItem>Reschedule</DropdownMenuItem>
            <DropdownMenuItem>Mark done</DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem>Delete</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </StorySection>

```

- [ ] **Step 4: Type check**

```bash
npx tsc --noEmit
```

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/primitives/DropdownMenu.tsx donna-ui/src/primitives/DropdownMenu.module.css donna-ui/src/pages/DevPrimitives/index.tsx
git commit -m "Add DropdownMenu primitive"
```

---

### Task 25: Popover primitive

**Files:**
- Create: `donna-ui/src/primitives/Popover.tsx`
- Create: `donna-ui/src/primitives/Popover.module.css`
- Modify: `donna-ui/src/pages/DevPrimitives/index.tsx`

- [ ] **Step 1: Write the CSS**

Create `donna-ui/src/primitives/Popover.module.css`:

```css
.content {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-card);
  padding: var(--space-4);
  box-shadow: var(--shadow-dialog);
  z-index: var(--z-popover);
  min-width: 240px;
  max-width: 360px;
  font-family: var(--font-body);
  font-size: var(--text-body);
  color: var(--color-text-secondary);
  outline: none;
}

.arrow { fill: var(--color-surface); }
```

- [ ] **Step 2: Write the component**

Create `donna-ui/src/primitives/Popover.tsx`:

```tsx
import * as Radix from "@radix-ui/react-popover";
import type { ReactNode } from "react";
import styles from "./Popover.module.css";

export function Popover({ children }: { children: ReactNode }) {
  return <Radix.Root>{children}</Radix.Root>;
}

export function PopoverTrigger({ children }: { children: ReactNode }) {
  return <Radix.Trigger asChild>{children}</Radix.Trigger>;
}

export function PopoverContent({ children }: { children: ReactNode }) {
  return (
    <Radix.Portal>
      <Radix.Content className={styles.content} sideOffset={6} collisionPadding={8}>
        {children}
        <Radix.Arrow className={styles.arrow} />
      </Radix.Content>
    </Radix.Portal>
  );
}
```

- [ ] **Step 3: Append story**

Imports:

```tsx
import { Popover, PopoverTrigger, PopoverContent } from "../../primitives/Popover";
```

StorySection:

```tsx
      <StorySection
        id="popover"
        eyebrow="Primitive · 13"
        title="Popover"
        note="Used by filter bars and date range pickers."
      >
        <Popover>
          <PopoverTrigger>
            <Button variant="ghost">Filter by date</Button>
          </PopoverTrigger>
          <PopoverContent>
            Popover body — use this to host date pickers, filter forms, etc.
          </PopoverContent>
        </Popover>
      </StorySection>

```

- [ ] **Step 4: Type check**

```bash
npx tsc --noEmit
```

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/primitives/Popover.tsx donna-ui/src/primitives/Popover.module.css donna-ui/src/pages/DevPrimitives/index.tsx
git commit -m "Add Popover primitive"
```

---

### Task 26: Skeleton primitive

**Files:**
- Create: `donna-ui/src/primitives/Skeleton.tsx`
- Create: `donna-ui/src/primitives/Skeleton.module.css`
- Modify: `donna-ui/src/pages/DevPrimitives/index.tsx`

- [ ] **Step 1: Write the CSS**

Create `donna-ui/src/primitives/Skeleton.module.css`:

```css
.skeleton {
  display: block;
  background: linear-gradient(
    90deg,
    var(--color-inset) 0%,
    var(--color-border) 50%,
    var(--color-inset) 100%
  );
  background-size: 200% 100%;
  border-radius: var(--radius-control);
  animation: shimmer 1.6s ease-in-out infinite;
}

@keyframes shimmer {
  0%   { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

@media (prefers-reduced-motion: reduce) {
  .skeleton {
    animation: none;
    background: var(--color-border);
  }
}
```

- [ ] **Step 2: Write the component**

Create `donna-ui/src/primitives/Skeleton.tsx`:

```tsx
import type { CSSProperties } from "react";
import { cn } from "../lib/cn";
import styles from "./Skeleton.module.css";

interface SkeletonProps {
  width?: string | number;
  height?: string | number;
  className?: string;
  style?: CSSProperties;
}

export function Skeleton({ width = "100%", height = 14, className, style }: SkeletonProps) {
  return (
    <div
      className={cn(styles.skeleton, className)}
      style={{ width, height, ...style }}
      aria-busy="true"
      aria-live="polite"
    />
  );
}
```

- [ ] **Step 3: Append story**

Imports:

```tsx
import { Skeleton } from "../../primitives/Skeleton";
```

StorySection:

```tsx
      <StorySection
        id="skeleton"
        eyebrow="Primitive · 14"
        title="Skeleton"
        note="Respects prefers-reduced-motion (no shimmer)."
      >
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)", width: 280 }}>
          <Skeleton height={14} width="60%" />
          <Skeleton height={32} />
          <Skeleton height={14} />
          <Skeleton height={14} width="80%" />
        </div>
      </StorySection>

```

- [ ] **Step 4: Type check**

```bash
npx tsc --noEmit
```

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/primitives/Skeleton.tsx donna-ui/src/primitives/Skeleton.module.css donna-ui/src/pages/DevPrimitives/index.tsx
git commit -m "Add Skeleton primitive"
```

---

### Task 27: ScrollArea primitive

**Files:**
- Create: `donna-ui/src/primitives/ScrollArea.tsx`
- Create: `donna-ui/src/primitives/ScrollArea.module.css`
- Modify: `donna-ui/src/pages/DevPrimitives/index.tsx`

- [ ] **Step 1: Write the CSS**

Create `donna-ui/src/primitives/ScrollArea.module.css`:

```css
.root { overflow: hidden; position: relative; }
.viewport { width: 100%; height: 100%; border-radius: inherit; }

.scrollbar {
  display: flex;
  user-select: none;
  touch-action: none;
  padding: 2px;
  background: transparent;
  transition: background var(--duration-fast) var(--ease-out);
}
.scrollbar[data-orientation="vertical"] { width: 8px; }
.scrollbar[data-orientation="horizontal"] { flex-direction: column; height: 8px; }

.thumb {
  flex: 1;
  background: var(--color-border);
  border-radius: 4px;
  position: relative;
}
.thumb:hover { background: var(--color-text-dim); }
```

- [ ] **Step 2: Write the component**

Create `donna-ui/src/primitives/ScrollArea.tsx`:

```tsx
import * as Radix from "@radix-ui/react-scroll-area";
import type { CSSProperties, ReactNode } from "react";
import { cn } from "../lib/cn";
import styles from "./ScrollArea.module.css";

interface ScrollAreaProps {
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
}

export function ScrollArea({ children, className, style }: ScrollAreaProps) {
  return (
    <Radix.Root className={cn(styles.root, className)} style={style}>
      <Radix.Viewport className={styles.viewport}>{children}</Radix.Viewport>
      <Radix.Scrollbar className={styles.scrollbar} orientation="vertical">
        <Radix.Thumb className={styles.thumb} />
      </Radix.Scrollbar>
      <Radix.Scrollbar className={styles.scrollbar} orientation="horizontal">
        <Radix.Thumb className={styles.thumb} />
      </Radix.Scrollbar>
      <Radix.Corner />
    </Radix.Root>
  );
}
```

- [ ] **Step 3: Append story**

Imports:

```tsx
import { ScrollArea } from "../../primitives/ScrollArea";
```

StorySection:

```tsx
      <StorySection
        id="scrollarea"
        eyebrow="Primitive · 15"
        title="ScrollArea"
      >
        <ScrollArea style={{ width: 320, height: 160, border: "1px solid var(--color-border)", borderRadius: "var(--radius-card)", padding: "var(--space-3)" }}>
          <div style={{ color: "var(--color-text-secondary)" }}>
            {Array.from({ length: 20 }).map((_, i) => (
              <p key={i} style={{ margin: "0 0 var(--space-2) 0" }}>
                Line {i + 1} — a scrolling line to prove the scrollbar themes correctly.
              </p>
            ))}
          </div>
        </ScrollArea>
      </StorySection>

```

- [ ] **Step 4: Type check**

```bash
npx tsc --noEmit
```

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/primitives/ScrollArea.tsx donna-ui/src/primitives/ScrollArea.module.css donna-ui/src/pages/DevPrimitives/index.tsx
git commit -m "Add ScrollArea primitive"
```

---

### Task 28: PageHeader primitive

**Files:**
- Create: `donna-ui/src/primitives/PageHeader.tsx`
- Create: `donna-ui/src/primitives/PageHeader.module.css`
- Modify: `donna-ui/src/pages/DevPrimitives/index.tsx`

- [ ] **Step 1: Write the CSS**

Create `donna-ui/src/primitives/PageHeader.module.css`:

```css
.root {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  margin-bottom: var(--space-5);
  gap: var(--space-4);
  flex-wrap: wrap;
}

.left { flex: 1; min-width: 0; }

.eyebrow {
  font-size: var(--text-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--color-text-muted);
  font-weight: 500;
  margin-bottom: var(--space-2);
}

.title {
  font-family: var(--font-display);
  font-weight: 300;
  font-size: var(--text-page-title);
  letter-spacing: var(--tracking-tight);
  line-height: var(--leading-tight);
  color: var(--color-text);
  margin: 0;
}

.meta {
  font-size: var(--text-label);
  color: var(--color-text-muted);
  letter-spacing: var(--tracking-wide);
  margin-top: var(--space-2);
}

.actions {
  display: flex;
  gap: var(--space-2);
  align-items: center;
}
```

- [ ] **Step 2: Write the component**

Create `donna-ui/src/primitives/PageHeader.tsx`:

```tsx
import type { ReactNode } from "react";
import styles from "./PageHeader.module.css";

interface PageHeaderProps {
  eyebrow?: string;
  title: string;
  meta?: string;
  actions?: ReactNode;
}

export function PageHeader({ eyebrow, title, meta, actions }: PageHeaderProps) {
  return (
    <header className={styles.root}>
      <div className={styles.left}>
        {eyebrow && <div className={styles.eyebrow}>{eyebrow}</div>}
        <h1 className={styles.title}>{title}</h1>
        {meta && <div className={styles.meta}>{meta}</div>}
      </div>
      {actions && <div className={styles.actions}>{actions}</div>}
    </header>
  );
}
```

- [ ] **Step 3: Append story**

Imports:

```tsx
import { PageHeader } from "../../primitives/PageHeader";
```

StorySection:

```tsx
      <StorySection
        id="pageheader"
        eyebrow="Primitive · 16"
        title="PageHeader"
        note="The opening move on every page. Eyebrow + Fraunces title + meta + action slot."
      >
        <div style={{ width: "100%" }}>
          <PageHeader
            eyebrow="Tuesday · April 8"
            title="Dashboard"
            meta="14 day window · last refreshed 2 min ago"
            actions={
              <>
                <Button variant="ghost" size="sm">24h</Button>
                <Button size="sm">14d</Button>
                <Button variant="ghost" size="sm">30d</Button>
              </>
            }
          />
        </div>
      </StorySection>

```

- [ ] **Step 4: Type check**

```bash
npx tsc --noEmit
```

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/primitives/PageHeader.tsx donna-ui/src/primitives/PageHeader.module.css donna-ui/src/pages/DevPrimitives/index.tsx
git commit -m "Add PageHeader primitive"
```

---

### Task 29: Stat primitive

**Files:**
- Create: `donna-ui/src/primitives/Stat.tsx`
- Create: `donna-ui/src/primitives/Stat.module.css`
- Modify: `donna-ui/src/pages/DevPrimitives/index.tsx`

- [ ] **Step 1: Write the CSS**

Create `donna-ui/src/primitives/Stat.module.css`:

```css
.root { display: flex; flex-direction: column; }

.eyebrow {
  font-size: var(--text-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--color-text-muted);
  font-weight: 500;
  margin-bottom: var(--space-1);
}

.value {
  font-family: var(--font-display);
  font-weight: 300;
  font-size: var(--text-metric);
  line-height: var(--leading-tight);
  letter-spacing: var(--tracking-tight);
  color: var(--color-accent);
  margin: 0;
}

.value.plain { color: var(--color-text); }

.suffix {
  font-size: 60%;
  color: var(--color-text-dim);
}

.sub {
  font-size: var(--text-label);
  color: var(--color-text-muted);
  margin-top: var(--space-1);
  letter-spacing: var(--tracking-wide);
}
```

- [ ] **Step 2: Write the component**

Create `donna-ui/src/primitives/Stat.tsx`:

```tsx
import type { ReactNode } from "react";
import { cn } from "../lib/cn";
import styles from "./Stat.module.css";

interface StatProps {
  eyebrow: string;
  value: string | number;
  suffix?: string;
  sub?: ReactNode;
  /** true to render in --color-text instead of --color-accent */
  plain?: boolean;
}

/**
 * Single headline metric: eyebrow + Fraunces display value + optional subline.
 * Used inside ChartCards (Wave 3) and as standalone dashboard stats.
 */
export function Stat({ eyebrow, value, suffix, sub, plain }: StatProps) {
  return (
    <div className={styles.root}>
      <div className={styles.eyebrow}>{eyebrow}</div>
      <p className={cn(styles.value, plain && styles.plain)}>
        {value}
        {suffix && <span className={styles.suffix}>{suffix}</span>}
      </p>
      {sub && <div className={styles.sub}>{sub}</div>}
    </div>
  );
}
```

- [ ] **Step 3: Append story**

Imports:

```tsx
import { Stat } from "../../primitives/Stat";
```

StorySection:

```tsx
      <StorySection
        id="stat"
        eyebrow="Primitive · 17"
        title="Stat"
        note="Eyebrow + Fraunces number + optional sub. Used in ChartCards and headline metrics."
      >
        <Stat eyebrow="Tasks Today" value={12} suffix=" / 18" sub="3 overdue · 2 blocked" />
        <Stat eyebrow="Spend · 14 days" value="$47.20" sub="↓ 12% vs prior period" />
        <Stat eyebrow="Total Runs" value="1,240" plain sub="claude-sonnet-4" />
      </StorySection>

```

- [ ] **Step 4: Type check**

```bash
npx tsc --noEmit
```

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/primitives/Stat.tsx donna-ui/src/primitives/Stat.module.css donna-ui/src/pages/DevPrimitives/index.tsx
git commit -m "Add Stat primitive"
```

---

### Task 30: Segmented primitive

**Files:**
- Create: `donna-ui/src/primitives/Segmented.tsx`
- Create: `donna-ui/src/primitives/Segmented.module.css`
- Modify: `donna-ui/src/pages/DevPrimitives/index.tsx`

- [ ] **Step 1: Write the CSS**

Create `donna-ui/src/primitives/Segmented.module.css`:

```css
.root {
  display: inline-flex;
  background: var(--color-inset);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-control);
  padding: 3px;
  gap: 2px;
  font-family: var(--font-body);
}

.item {
  background: transparent;
  border: 0;
  padding: 7px 16px;
  font-size: 10px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--color-text-muted);
  cursor: pointer;
  border-radius: var(--radius-control);
  font-weight: 500;
  transition: color var(--duration-fast) var(--ease-out),
              background var(--duration-fast) var(--ease-out);
  font-family: inherit;
}
.item:hover { color: var(--color-text); }
.item.active {
  background: var(--color-accent);
  color: var(--color-accent-contrast);
}
```

- [ ] **Step 2: Write the component**

Create `donna-ui/src/primitives/Segmented.tsx`:

```tsx
import { cn } from "../lib/cn";
import styles from "./Segmented.module.css";

interface SegmentedOption<T extends string> {
  value: T;
  label: string;
}

interface SegmentedProps<T extends string> {
  value: T;
  onValueChange: (v: T) => void;
  options: SegmentedOption<T>[];
  "aria-label"?: string;
}

export function Segmented<T extends string>({
  value,
  onValueChange,
  options,
  ...aria
}: SegmentedProps<T>) {
  return (
    <div className={styles.root} role="tablist" {...aria}>
      {options.map((opt) => (
        <button
          key={opt.value}
          role="tab"
          aria-selected={value === opt.value}
          className={cn(styles.item, value === opt.value && styles.active)}
          onClick={() => onValueChange(opt.value)}
          type="button"
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
```

- [ ] **Step 3: Append story**

Imports:

```tsx
import { Segmented } from "../../primitives/Segmented";
```

useState:

```tsx
  const [range, setRange] = useState<"24h" | "14d" | "30d">("14d");
```

StorySection:

```tsx
      <StorySection
        id="segmented"
        eyebrow="Primitive · 18"
        title="Segmented"
        note="Replaces AntD Segmented. Used for Dashboard time range and filter toggles."
      >
        <Segmented
          value={range}
          onValueChange={setRange}
          options={[
            { value: "24h", label: "24h" },
            { value: "14d", label: "14d" },
            { value: "30d", label: "30d" },
          ]}
          aria-label="Time range"
        />
      </StorySection>

```

- [ ] **Step 4: Type check**

```bash
npx tsc --noEmit
```

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/primitives/Segmented.tsx donna-ui/src/primitives/Segmented.module.css donna-ui/src/pages/DevPrimitives/index.tsx
git commit -m "Add Segmented primitive"
```

---

### Task 31: EmptyState primitive

**Files:**
- Create: `donna-ui/src/primitives/EmptyState.tsx`
- Create: `donna-ui/src/primitives/EmptyState.module.css`
- Modify: `donna-ui/src/pages/DevPrimitives/index.tsx`

- [ ] **Step 1: Write the CSS**

Create `donna-ui/src/primitives/EmptyState.module.css`:

```css
.root {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  padding: var(--space-6) var(--space-5);
  max-width: 420px;
  border-left: 2px solid var(--color-accent);
}

.eyebrow {
  font-size: var(--text-eyebrow);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  color: var(--color-text-muted);
  font-weight: 500;
  margin-bottom: var(--space-3);
}

.title {
  font-family: var(--font-display);
  font-weight: 300;
  font-size: var(--text-section);
  color: var(--color-text);
  margin: 0 0 var(--space-2) 0;
  letter-spacing: var(--tracking-normal);
}

.body {
  font-size: var(--text-body);
  color: var(--color-text-muted);
  margin: 0 0 var(--space-4) 0;
  line-height: var(--leading-normal);
}

.actions { display: flex; gap: var(--space-2); }
```

- [ ] **Step 2: Write the component**

Create `donna-ui/src/primitives/EmptyState.tsx`:

```tsx
import type { ReactNode } from "react";
import styles from "./EmptyState.module.css";

interface EmptyStateProps {
  eyebrow?: string;
  title: string;
  body?: ReactNode;
  actions?: ReactNode;
}

/**
 * Distinctive empty state — left-border accent, Fraunces title,
 * instructive-plus-personality voice. See spec §5 Voice.
 */
export function EmptyState({ eyebrow = "Nothing here", title, body, actions }: EmptyStateProps) {
  return (
    <div className={styles.root}>
      <div className={styles.eyebrow}>{eyebrow}</div>
      <h3 className={styles.title}>{title}</h3>
      {body && <p className={styles.body}>{body}</p>}
      {actions && <div className={styles.actions}>{actions}</div>}
    </div>
  );
}
```

- [ ] **Step 3: Append story**

Imports:

```tsx
import { EmptyState } from "../../primitives/EmptyState";
```

StorySection:

```tsx
      <StorySection
        id="empty"
        eyebrow="Primitive · 19"
        title="EmptyState"
        note="Instructive first, personality second. See spec §5 Voice."
      >
        <EmptyState
          title="Nothing captured yet."
          body="Press ⌘N to add one, or message Donna on Discord and she'll do it for you."
          actions={<Button>New Task</Button>}
        />
      </StorySection>

```

- [ ] **Step 4: Type check**

```bash
npx tsc --noEmit
```

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/primitives/EmptyState.tsx donna-ui/src/primitives/EmptyState.module.css donna-ui/src/pages/DevPrimitives/index.tsx
git commit -m "Add EmptyState primitive"
```

---

### Task 32: DataTable primitive (the big one)

**Files:**
- Create: `donna-ui/src/primitives/DataTable.tsx`
- Create: `donna-ui/src/primitives/DataTable.module.css`
- Modify: `donna-ui/src/pages/DevPrimitives/index.tsx`

- [ ] **Step 1: Write the CSS**

Create `donna-ui/src/primitives/DataTable.module.css`:

```css
.wrapper { width: 100%; }

.scroll {
  overflow-x: auto;
  border-top: 1px solid var(--color-border);
  border-bottom: 1px solid var(--color-border);
}

.table {
  width: 100%;
  border-collapse: collapse;
  font-family: var(--font-body);
  font-size: var(--text-body);
}

.headerRow { background: transparent; }

.headerCell {
  text-align: left;
  padding: 12px 14px 12px 0;
  font-size: var(--text-eyebrow);
  color: var(--color-text-muted);
  letter-spacing: var(--tracking-eyebrow);
  text-transform: uppercase;
  font-weight: 500;
  border-bottom: 1px solid var(--color-border);
  white-space: nowrap;
  cursor: default;
  user-select: none;
}
.headerCell.sortable { cursor: pointer; }
.headerCell.sortable:hover { color: var(--color-text-secondary); }
.sortIcon { margin-left: 6px; color: var(--color-text-dim); display: inline-block; vertical-align: middle; }
.sortIcon.active { color: var(--color-accent); }

.bodyCell {
  padding: 14px 14px 14px 0;
  color: var(--color-text-secondary);
  border-bottom: 1px solid var(--color-border-subtle);
  vertical-align: middle;
}

.row { transition: background var(--duration-fast) var(--ease-out); }
.row:hover > .bodyCell { background: rgba(212, 169, 67, 0.03); }
.row.selected > .bodyCell { background: var(--color-accent-soft); }
.row.clickable { cursor: pointer; }
.row:focus-visible {
  outline: none;
}
.row:focus-visible > .bodyCell:first-child {
  box-shadow: inset 2px 0 0 var(--color-accent);
}

.footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-top: var(--space-3);
  font-size: var(--text-label);
  color: var(--color-text-muted);
  letter-spacing: var(--tracking-wide);
}

.footerActions { display: flex; gap: var(--space-2); }

.empty { padding: var(--space-5); }
```

- [ ] **Step 2: Write the component**

Create `donna-ui/src/primitives/DataTable.tsx`:

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
import { ChevronDown, ChevronUp } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
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
  /** When true, ↑/↓ navigates rows and Enter activates onRowClick. */
  keyboardNav?: boolean;
}

/**
 * Single table component for the entire app. Built on TanStack Table.
 * Sort + paginate + row selection + keyboard nav. No virtualization in
 * this version — Wave 5 adds it for Logs using @tanstack/react-virtual.
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
}: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [focusIndex, setFocusIndex] = useState(0);
  const bodyRef = useRef<HTMLTableSectionElement>(null);

  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    getRowId,
    initialState: { pagination: { pageSize } },
  });

  const rows = table.getRowModel().rows;

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTableSectionElement>) => {
      if (!keyboardNav || rows.length === 0) return;
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
    [keyboardNav, rows, focusIndex, onRowClick],
  );

  useEffect(() => {
    if (!keyboardNav || !bodyRef.current) return;
    const el = bodyRef.current.querySelectorAll<HTMLTableRowElement>("tr")[focusIndex];
    el?.focus();
  }, [focusIndex, keyboardNav]);

  const pageIndex = table.getState().pagination.pageIndex;
  const pageCount = table.getPageCount();
  const totalRows = data.length;
  const start = pageIndex * pageSize + 1;
  const end = Math.min((pageIndex + 1) * pageSize, totalRows);

  if (loading) {
    return (
      <div className={styles.wrapper}>
        <div className={styles.scroll}>
          <table className={styles.table}>
            <tbody>
              {Array.from({ length: 5 }).map((_, i) => (
                <tr key={i}>
                  <td className={styles.bodyCell}><Skeleton height={16} /></td>
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
      <div className={styles.scroll}>
        <table className={styles.table}>
          <thead>
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
            {rows.map((row, idx) => {
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
                  tabIndex={keyboardNav ? (idx === focusIndex ? 0 : -1) : undefined}
                  aria-selected={selected}
                >
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id} className={styles.bodyCell}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {totalRows > pageSize && (
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
      {pageCount > 1 && totalRows <= pageSize && null}
    </div>
  );
}
```

- [ ] **Step 3: Append story**

Imports in DevPrimitives/index.tsx:

```tsx
import type { ColumnDef } from "@tanstack/react-table";
import { DataTable } from "../../primitives/DataTable";
```

Add sample data + columns + state at the top of the component:

```tsx
  interface DemoTask {
    id: string;
    title: string;
    status: "scheduled" | "in_progress" | "blocked" | "done";
    due: string;
  }
  const demoTasks: DemoTask[] = [
    { id: "1", title: "Draft Q2 budget memo", status: "scheduled", due: "Apr 8 16:00" },
    { id: "2", title: "Reply to legal review", status: "blocked", due: "Apr 7 12:00" },
    { id: "3", title: "Prep Friday 1:1 notes", status: "scheduled", due: "Apr 11 09:00" },
    { id: "4", title: "Sync with Carla", status: "done", due: "Apr 6 15:30" },
    { id: "5", title: "File expense report", status: "in_progress", due: "Apr 9 17:00" },
  ];
  const demoColumns: ColumnDef<DemoTask>[] = [
    { accessorKey: "title", header: "Title" },
    {
      accessorKey: "status",
      header: "Status",
      cell: (info) => <Pill variant={info.getValue() === "done" ? "success" : info.getValue() === "blocked" ? "error" : "accent"}>{String(info.getValue())}</Pill>,
    },
    { accessorKey: "due", header: "Due" },
  ];
  const [selectedDemo, setSelectedDemo] = useState<string | null>(null);
```

StorySection:

```tsx
      <StorySection
        id="datatable"
        eyebrow="Primitive · 20"
        title="DataTable"
        note="The single table replacement for Tasks, Logs, Shadow, Configs list, Prompts list, Preferences rules. Sort by clicking headers. Click a row to select."
      >
        <div style={{ width: "100%" }}>
          <DataTable
            data={demoTasks}
            columns={demoColumns}
            getRowId={(r) => r.id}
            onRowClick={(r) => setSelectedDemo(r.id)}
            selectedRowId={selectedDemo}
            keyboardNav
          />
        </div>
      </StorySection>

```

- [ ] **Step 4: Type check + manual test**

```bash
npx tsc --noEmit
npm run dev
```

Open `/dev/primitives`, scroll to DataTable. Click "Title" header — rows should sort. Click again — reverse sort. Click a row — it should highlight with accent-soft background. Press Tab until focus reaches the table body, then use ↑/↓ — keyboard focus should move between rows. Press Enter — row click should fire. Stop dev.

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/primitives/DataTable.tsx donna-ui/src/primitives/DataTable.module.css donna-ui/src/pages/DevPrimitives/index.tsx
git commit -m "Add DataTable primitive on TanStack Table"
```

---

### Task 33: Primitives barrel export

**Files:**
- Create: `donna-ui/src/primitives/index.ts`

- [ ] **Step 1: Write the barrel file**

Create `donna-ui/src/primitives/index.ts`:

```ts
export { Button, type ButtonVariant, type ButtonSize } from "./Button";
export { Card, CardHeader, CardEyebrow, CardTitle } from "./Card";
export { Pill, type PillVariant } from "./Pill";
export { Input, Textarea, FormField } from "./Input";
export { Select, SelectItem } from "./Select";
export { Checkbox } from "./Checkbox";
export { Switch } from "./Switch";
export { Tabs, TabsList, TabsTrigger, TabsContent } from "./Tabs";
export { Tooltip } from "./Tooltip";
export { Dialog, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "./Dialog";
export { Drawer } from "./Drawer";
export {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "./DropdownMenu";
export { Popover, PopoverTrigger, PopoverContent } from "./Popover";
export { Skeleton } from "./Skeleton";
export { ScrollArea } from "./ScrollArea";
export { PageHeader } from "./PageHeader";
export { Stat } from "./Stat";
export { Segmented } from "./Segmented";
export { EmptyState } from "./EmptyState";
export { DataTable } from "./DataTable";
```

- [ ] **Step 2: Type check**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
git add donna-ui/src/primitives/index.ts
git commit -m "Add primitives barrel export"
```

---

### Task 34: Playwright visual regression test for dev/primitives

**Files:**
- Create: `donna-ui/tests/e2e/dev-primitives.spec.ts`

- [ ] **Step 1: Write the test**

Create `donna-ui/tests/e2e/dev-primitives.spec.ts`:

```ts
import { test, expect } from "@playwright/test";

test.describe("Dev primitives gallery", () => {
  test("all primitives render in gold theme", async ({ page }) => {
    await page.goto("/dev/primitives");
    await page.waitForLoadState("networkidle");

    // Verify all 20 story sections are present
    const storyIds = [
      "button", "card", "pill", "input", "select", "checkbox", "switch",
      "tabs", "tooltip", "dialog", "drawer", "dropdown", "popover",
      "skeleton", "scrollarea", "pageheader", "stat", "segmented", "empty", "datatable",
    ];
    for (const id of storyIds) {
      await expect(page.getByTestId(`story-${id}`)).toBeVisible();
    }
  });

  test("all primitives render in coral theme", async ({ page }) => {
    await page.goto("/dev/primitives");
    await page.keyboard.press("Meta+.");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "coral");

    // Re-verify all story sections visible after theme flip
    await expect(page.getByTestId("story-button")).toBeVisible();
    await expect(page.getByTestId("story-datatable")).toBeVisible();

    // Reset for next test
    await page.keyboard.press("Meta+.");
  });

  test("dialog opens and closes via keyboard", async ({ page }) => {
    await page.goto("/dev/primitives");
    await page.getByRole("button", { name: "Open Dialog" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).not.toBeVisible();
  });

  test("data table sorts when header clicked", async ({ page }) => {
    await page.goto("/dev/primitives");
    // Find the DataTable story section
    const table = page.getByTestId("story-datatable").locator("table");
    // Capture the first row title before sorting
    const firstBefore = await table.locator("tbody tr").first().locator("td").first().textContent();
    // Click the Title header
    await table.locator("thead th").first().click();
    const firstAfter = await table.locator("tbody tr").first().locator("td").first().textContent();
    // Should have changed (sorted alphabetically)
    expect(firstAfter).not.toBe(firstBefore);
  });
});
```

- [ ] **Step 2: Run the tests**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npm run test:e2e -- dev-primitives
```

Expected: all 4 tests pass. If the dialog test fails because of selector ambiguity, use a more specific selector (e.g. `locator('[role="dialog"]')`).

- [ ] **Step 3: Run the FULL smoke suite one more time**

```bash
npm run test:e2e
```

Expected: all tests (original 10 smoke + 4 dev-primitives) pass. This is the Wave 0 + Wave 1 safety net confirmed.

- [ ] **Step 4: Commit**

```bash
git add donna-ui/tests/e2e/dev-primitives.spec.ts
git commit -m "Add Playwright tests for dev primitives gallery"
```

---

### Task 35: Wave 1 sanity check — full build + typecheck

**Files:**
- (verification only — no files modified)

- [ ] **Step 1: Clean build**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npm run build
```

Expected: build completes. New gzipped sizes printed. This is **not** the Wave 9 baseline comparison point — Wave 1 only adds code, so the bundle is expected to be slightly larger than Wave 0 baseline. That's fine. The comparison happens in Wave 9 after AntD is removed.

- [ ] **Step 2: Verify the app still runs identically**

```bash
npm run dev
```

Open `http://localhost:5173`. Expected: the actual Donna app looks exactly as it did before this plan started. The redesign is not visible yet. Only `/dev/primitives` shows the new primitives.

Press Cmd+. — the `<html>` `data-theme` attribute toggles, but the AntD-themed pages don't change appearance (they don't consume `var(--color-accent)` yet — that's Wave 2+). The dev primitives page flips colors correctly.

Stop dev.

- [ ] **Step 3: Run the full Playwright suite**

```bash
npm run test:e2e
```

Expected: all tests pass.

- [ ] **Step 4: Verify no uncommitted changes**

```bash
git status
```

Expected: "nothing to commit, working tree clean" (aside from the untracked prior-session dashboard changes if they're still present).

---

## Self-Review

Spec coverage check:

- **§1 Aesthetic foundation** (tokens, colors, type, spacing, motion, theme toggle) → Tasks 2–7 ✓
- **§2 Component architecture** (deps, directory, primitives, key contracts) → Tasks 10–33 ✓
- **§3 Page layouts** → Not in scope (Wave 2+) ✓
- **§4 Wave 0** (plumbing, fonts, useTheme, ⌘. shortcut, Playwright, Vite tree-shaking) → Tasks 1–9 ✓
  - Note: Vite tree-shaking for lucide-react is implicit — lucide imports are tree-shakeable by default with Vite ESM, so no explicit config needed. Verified by inspecting bundle in Task 9 baseline.
- **§4 Wave 1** (primitives library, /dev/primitives route, DataTable last) → Tasks 11–34 ✓
- **§4 Audit coverage for Waves 0–1**: spec says "none yet" for both → no issues to track ✓
- **§5 Confirmed decisions**: every relevant decision implemented
  - Gold default + coral via data-theme ✓
  - Fraunces + Inter self-hosted ✓
  - localStorage persistence ✓
  - ⌘. shortcut ✓
  - Tooltip 400ms override ✓
  - DataTable pagination footer "Showing 1–50 of 420" ✓
  - Skeleton respects prefers-reduced-motion ✓
- **§6 Risks**: no page migrations in this plan, so the "visual inconsistency window" risk does not apply yet
- **§8 Acceptance criteria for Waves 0-1 scope**: the primitives exist, smoke tests pass, bundle baseline recorded

Placeholder scan: searched for "TBD", "TODO", "implement later", "add appropriate", "similar to Task" — none present.

Type consistency check:
- `Theme` type defined once in `theme/index.ts`, imported by `useTheme` ✓
- `cn()` signature stable across all primitives ✓
- All primitives accept `className?: string` via `HTMLAttributes` spread or explicit prop ✓
- `Button` variant type exported and re-exported from barrel ✓
- `Select` uses `onValueChange` (Radix convention); `Checkbox`, `Switch`, `Tabs` also use `onValueChange` / `onCheckedChange` consistently ✓
- `DataTable` `getRowId` typed as `(row: T) => string` — matches TanStack convention ✓

One spec gap I'm choosing not to implement in this plan: the spec mentions `@axe-core/react` as a Wave 9 acceptance check. That's Wave 9 territory — deferred.

One concrete follow-up: after Wave 1 ships, the next plan (Waves 2+) will start consuming these primitives. When that plan is written, the `/dev/primitives` page stays as an internal reference.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-08-donna-ui-redesign-wave-0-and-1.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints for review.

Which approach?
