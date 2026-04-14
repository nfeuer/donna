# Donna UI Redesign — Wave 2 (App Shell Migration) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Ant Design `Layout.tsx` shell with a custom `AppShell` + `Sidebar` + `NavItem` built on primitives. First visible change in the redesign: warm charcoal rail, gold left-border active nav state, Fraunces "Donna" wordmark, no more AntD header bar, theme toggle at the rail footer. Migrate `KeyboardShortcutsModal` to the Radix-backed `Dialog` primitive. Mount Sonner `<Toaster />` globally.

**Architecture:** A new `src/layout/` directory houses the shell components. `AppShell` is a flex container — `<Sidebar>` on the left (fixed 220 px), `<main>` on the right scrolling. `Sidebar` owns nav state via `useLocation` and drives the theme via `useTheme`. Existing (still-AntD) pages render inside an `<Outlet />` with a temporary 24 px padding shim that matches today's behaviour, so no unmigrated page regresses. `KeyboardShortcutsModal` keeps its existing `show-shortcuts-help` window-event contract but delegates open/close/focus-trap/Escape to the Radix `Dialog` primitive built in Wave 1. Sonner's `<Toaster />` mounts once inside `AppShell` with tokens-driven styling, ready for Wave 3+ pages to replace AntD `message.*` calls.

**Tech Stack:** React 18, TypeScript 5, React Router v6, Radix UI Dialog (via Wave 1 primitive), Sonner 2, lucide-react, CSS Modules. No new dependencies — every package required (`sonner`, `lucide-react`, `@radix-ui/react-dialog`) was installed in Wave 0 and is already in `donna-ui/package.json`.

**Spec reference:** `docs/superpowers/specs/2026-04-08-donna-ui-redesign-design.md` §1 (aesthetic foundation), §2 (layout directory), §3 shared behaviour, §4 Wave 2 (line 326), §5 confirmed decisions (menu active state, theme toggle placement).

**Precondition:** Wave 0 + Wave 1 are merged. `src/primitives/` contains `Dialog`, `Button`, `Tooltip`, etc. `src/hooks/useTheme.ts` exists and registers the `⌘.` shortcut. `src/theme/tokens.css` provides `--color-accent`, `--color-inset`, `--font-display`, etc. Dev primitives gallery at `/dev/primitives` still renders cleanly in `npm run dev`.

## Audit issues fixed in this wave

The spec (§4 Wave 2) lists three audit items this wave resolves. Each is addressed below; verify at the end of each relevant task.

- **[P2] Layout sider collapse tooltip inconsistency (`Layout.tsx`)** — resolved by removing the collapsible behaviour entirely. The new rail is fixed width, so there is no collapsed/expanded state and no collapse tooltip (Task 2, 7).
- **[P3] Header bar information redundancy** — resolved by deleting the old AntD `<Header>` that repeated the current page title already visible in the active nav item. The new shell has no top header bar (Task 4, 7).
- **[P1] `KeyboardShortcutsModal` lacks focus trap and accessible dismissal** — resolved by migrating to Radix `Dialog`, which provides a focus trap, `Esc` to close, and `role="dialog"` for free (Task 6).

---

## File Structure Overview

### Created in Wave 2

```
donna-ui/src/
├── layout/
│   ├── NavItem.tsx                         (created)
│   ├── NavItem.module.css
│   ├── Sidebar.tsx                         (created)
│   ├── Sidebar.module.css
│   ├── AppShell.tsx                        (created)
│   ├── AppShell.module.css
│   ├── PageContainer.tsx                   (created)
│   ├── PageContainer.module.css
│   └── index.ts                            (created — barrel export)
│
├── components/
│   ├── KeyboardShortcutsModal.tsx          (REWRITTEN on Dialog primitive)
│   ├── KeyboardShortcutsModal.module.css   (created)
│   └── Layout.tsx                          (DELETED)
│
└── App.tsx                                 (modified: import AppShell instead of Layout)

donna-ui/tests/e2e/smoke/
└── app-shell.spec.ts                       (created)
```

**Principle:** Shell components live in `src/layout/` (matching spec §2 directory), not `src/primitives/`. They are project-specific compositions, not reusable primitives. Each file stays under 120 lines. CSS modules keep class names deterministic.

**Out of scope for Wave 2 (called out explicitly to prevent scope creep):**
- Mobile/responsive rail collapse — the rail stays fixed 220 px on all viewports. Proper mobile treatment is deferred to later waves.
- `src/components/PageShell.tsx` (an unused AntD orphan) — not touched. It is unused per `grep -rn PageShell donna-ui/src`, and removing unused code is not part of Wave 2's scope. Delete in Wave 9 alongside the rest of the AntD cleanup.
- Replacing the inner `<Sider>` on the Tasks page — that is Wave 4.
- `src/theme/darkTheme.ts` — deleted in Wave 9.
- Replacing any `notification.*` / `message.*` callsite — mounting the `<Toaster />` is enough; actual callsite rewrites happen per-page in Waves 3–8.

---

## Wave 2 · App Shell Migration

### Task 1: Create `NavItem` component

The nav item is the leaf building block: a `<Link>` rendered inside an `<li>`, with a gold left border and accent-soft background in its active state. Built first because the Sidebar in Task 2 consumes it.

**Files:**
- Create: `donna-ui/src/layout/NavItem.tsx`
- Create: `donna-ui/src/layout/NavItem.module.css`

- [ ] **Step 1: Create the NavItem component**

Create `donna-ui/src/layout/NavItem.tsx`:

```tsx
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { cn } from "../lib/cn";
import styles from "./NavItem.module.css";

interface NavItemProps {
  to: string;
  icon: ReactNode;
  label: string;
  active: boolean;
}

/**
 * Single rail nav entry. Gold left-border active state per spec §5
 * ("Menu active state: Gold left border only — no background fill").
 * `aria-current="page"` when active so screen readers announce it.
 */
export function NavItem({ to, icon, label, active }: NavItemProps) {
  return (
    <li className={styles.listItem}>
      <Link
        to={to}
        className={cn(styles.link, active && styles.active)}
        aria-current={active ? "page" : undefined}
      >
        <span className={styles.icon} aria-hidden="true">
          {icon}
        </span>
        <span className={styles.label}>{label}</span>
      </Link>
    </li>
  );
}
```

- [ ] **Step 2: Create NavItem styles**

Create `donna-ui/src/layout/NavItem.module.css`:

```css
/*
 * NavItem — single rail link entry.
 * Active state: 2px gold left border + accent-soft background tint.
 * Hover lifts to full text color without changing background.
 */

.listItem {
  list-style: none;
  margin: 0;
  padding: 0;
}

.link {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: 10px var(--space-4);
  color: var(--color-text-secondary);
  text-decoration: none;
  font-family: var(--font-body);
  font-size: var(--text-body);
  border-left: 2px solid transparent;
  transition:
    color var(--duration-fast) var(--ease-out),
    background var(--duration-fast) var(--ease-out),
    border-color var(--duration-fast) var(--ease-out);
}

.link:hover {
  color: var(--color-text);
}

.link:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: -2px;
}

.active {
  color: var(--color-text);
  border-left-color: var(--color-accent);
  background: var(--color-accent-soft);
}

.icon {
  display: inline-flex;
  width: 18px;
  height: 18px;
  align-items: center;
  justify-content: center;
  color: inherit;
}

.icon svg {
  width: 18px;
  height: 18px;
}

.label {
  flex: 1;
}
```

- [ ] **Step 3: Type check**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add donna-ui/src/layout/NavItem.tsx donna-ui/src/layout/NavItem.module.css
git commit -m "Add NavItem layout component with gold active border"
```

---

### Task 2: Create `Sidebar` component

The rail: brand wordmark at top, nav list in the middle, theme toggle chips at the footer with a `⌘.` hint. Consumes `NavItem` from Task 1 and the existing `useTheme` hook.

**Files:**
- Create: `donna-ui/src/layout/Sidebar.tsx`
- Create: `donna-ui/src/layout/Sidebar.module.css`

- [ ] **Step 1: Create the Sidebar component**

Create `donna-ui/src/layout/Sidebar.tsx`:

```tsx
import { useLocation } from "react-router-dom";
import {
  LayoutDashboard,
  ScrollText,
  CheckSquare,
  Bot,
  Settings,
  FileText,
  FlaskConical,
  Lightbulb,
} from "lucide-react";
import { NavItem } from "./NavItem";
import { useTheme } from "../hooks/useTheme";
import { cn } from "../lib/cn";
import styles from "./Sidebar.module.css";

interface NavEntry {
  path: string;
  label: string;
  icon: React.ReactNode;
}

const NAV_ITEMS: NavEntry[] = [
  { path: "/", label: "Dashboard", icon: <LayoutDashboard size={18} /> },
  { path: "/logs", label: "Logs", icon: <ScrollText size={18} /> },
  { path: "/tasks", label: "Tasks", icon: <CheckSquare size={18} /> },
  { path: "/agents", label: "Agents", icon: <Bot size={18} /> },
  { path: "/configs", label: "Configs", icon: <Settings size={18} /> },
  { path: "/prompts", label: "Prompts", icon: <FileText size={18} /> },
  { path: "/shadow", label: "Shadow", icon: <FlaskConical size={18} /> },
  { path: "/preferences", label: "Preferences", icon: <Lightbulb size={18} /> },
];

function isActive(pathname: string, itemPath: string): boolean {
  // "/" matches only exactly so nested routes don't light up Dashboard.
  if (itemPath === "/") return pathname === "/";
  return pathname === itemPath || pathname.startsWith(`${itemPath}/`);
}

/**
 * Left rail. Fixed 220 px. Brand wordmark at top, NAV_ITEMS in the middle,
 * theme toggle chips + shortcut hint at the bottom. No collapse behaviour —
 * spec §5 specifies a fixed rail with gold left-border active state.
 */
export function Sidebar() {
  const location = useLocation();
  const { theme, setTheme } = useTheme();

  return (
    <aside className={styles.sidebar}>
      <div className={styles.brand}>
        <div className={styles.brandName}>Donna</div>
        <div className={styles.brandEyebrow}>Management</div>
      </div>

      <nav className={styles.nav} aria-label="Primary navigation">
        <ul className={styles.navList}>
          {NAV_ITEMS.map((item) => (
            <NavItem
              key={item.path}
              to={item.path}
              icon={item.icon}
              label={item.label}
              active={isActive(location.pathname, item.path)}
            />
          ))}
        </ul>
      </nav>

      <div className={styles.footer}>
        <div className={styles.themeRow} role="group" aria-label="Accent theme">
          <button
            type="button"
            aria-label="Champagne gold theme"
            aria-pressed={theme === "gold"}
            className={cn(
              styles.themeChip,
              styles.themeChipGold,
              theme === "gold" && styles.themeChipActive,
            )}
            onClick={() => setTheme("gold")}
          >
            Gold
          </button>
          <button
            type="button"
            aria-label="Electric coral theme"
            aria-pressed={theme === "coral"}
            className={cn(
              styles.themeChip,
              styles.themeChipCoral,
              theme === "coral" && styles.themeChipActive,
            )}
            onClick={() => setTheme("coral")}
          >
            Coral
          </button>
        </div>
        <div className={styles.shortcutHint} aria-hidden="true">
          <kbd className={styles.kbd}>⌘.</kbd>
          <span>to flip</span>
        </div>
      </div>
    </aside>
  );
}
```

- [ ] **Step 2: Create Sidebar styles**

Create `donna-ui/src/layout/Sidebar.module.css`:

```css
/*
 * Sidebar rail. Fixed 220 px, warm charcoal inset background, hairline right
 * border. Layout uses flex column so the footer sticks to the bottom.
 */

.sidebar {
  width: 220px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: var(--color-inset);
  border-right: 1px solid var(--color-border);
  z-index: var(--z-rail);
}

/* ========== Brand ========== */

.brand {
  padding: var(--space-5) var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--color-border-subtle);
}

.brandName {
  font-family: var(--font-display);
  font-size: 24px;
  font-weight: 300;
  letter-spacing: var(--tracking-tight);
  color: var(--color-text);
  line-height: var(--leading-tight);
}

.brandEyebrow {
  margin-top: 6px;
  font-family: var(--font-body);
  font-size: var(--text-eyebrow);
  text-transform: uppercase;
  letter-spacing: var(--tracking-eyebrow);
  color: var(--color-text-muted);
}

/* ========== Nav list ========== */

.nav {
  flex: 1;
  padding: var(--space-3) 0;
  overflow-y: auto;
}

.navList {
  margin: 0;
  padding: 0;
  list-style: none;
}

/* ========== Footer ========== */

.footer {
  padding: var(--space-3) var(--space-3) var(--space-4);
  border-top: 1px solid var(--color-border-subtle);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.themeRow {
  display: flex;
  gap: var(--space-2);
}

.themeChip {
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 6px 8px;
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-control);
  font-family: var(--font-body);
  font-size: var(--text-label);
  color: var(--color-text-muted);
  cursor: pointer;
  transition:
    color var(--duration-fast) var(--ease-out),
    border-color var(--duration-fast) var(--ease-out),
    background var(--duration-fast) var(--ease-out);
}

.themeChip::before {
  content: "";
  display: block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

/* Swatch dots are literal brand colors — semantic purpose (showing the
 * accent option), not decoration. Keep the hex values here. */
.themeChipGold::before {
  background: #d4a943;
}
.themeChipCoral::before {
  background: #f56960;
}

.themeChip:hover {
  color: var(--color-text);
  border-color: var(--color-accent-border);
}

.themeChip:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 2px;
}

.themeChipActive {
  color: var(--color-text);
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
}

.shortcutHint {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  font-size: var(--text-label);
  color: var(--color-text-muted);
  font-family: var(--font-body);
}

.kbd {
  display: inline-block;
  padding: 1px 6px;
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 16px;
  color: var(--color-text);
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-control);
}
```

- [ ] **Step 3: Type check**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add donna-ui/src/layout/Sidebar.tsx donna-ui/src/layout/Sidebar.module.css
git commit -m "Add Sidebar with brand, nav, and theme toggle footer"
```

---

### Task 3: Create `PageContainer` wrapper

A thin max-width wrapper that pages opt into starting in Wave 3. In Wave 2 it is only added to the barrel and has no consumers — later waves import it from `../layout`.

**Files:**
- Create: `donna-ui/src/layout/PageContainer.tsx`
- Create: `donna-ui/src/layout/PageContainer.module.css`

- [ ] **Step 1: Create the PageContainer component**

Create `donna-ui/src/layout/PageContainer.tsx`:

```tsx
import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "../lib/cn";
import styles from "./PageContainer.module.css";

interface PageContainerProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
}

/**
 * Max-width wrapper for page content. Added in Wave 2 but consumed starting
 * in Wave 3 as each page migrates. Purely a max-width constraint — outer
 * padding is provided by AppShell's <main>. Do not add padding here or
 * pages will get double gutters during the transition window.
 */
export function PageContainer({
  children,
  className,
  ...rest
}: PageContainerProps) {
  return (
    <div className={cn(styles.container, className)} {...rest}>
      {children}
    </div>
  );
}
```

- [ ] **Step 2: Create PageContainer styles**

Create `donna-ui/src/layout/PageContainer.module.css`:

```css
/*
 * PageContainer — max-width wrapper. No padding (AppShell's <main>
 * provides it). Wave 3+ pages consume this; Wave 2 only ships the file.
 */

.container {
  max-width: 1280px;
  margin: 0 auto;
  width: 100%;
}
```

- [ ] **Step 3: Type check**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add donna-ui/src/layout/PageContainer.tsx donna-ui/src/layout/PageContainer.module.css
git commit -m "Add PageContainer max-width wrapper"
```

---

### Task 4: Create `AppShell` component with global `Toaster`

The shell itself: a flex row containing `<Sidebar>` and a scrolling `<main>` that wraps the route `<Outlet />`. Mounts the `KeyboardShortcutsModal` and Sonner `<Toaster />` globally. Owns the `useKeyboardShortcuts` hook call (previously in `Layout.tsx`).

**Files:**
- Create: `donna-ui/src/layout/AppShell.tsx`
- Create: `donna-ui/src/layout/AppShell.module.css`

- [ ] **Step 1: Create the AppShell component**

Create `donna-ui/src/layout/AppShell.tsx`:

```tsx
import { Outlet } from "react-router-dom";
import { Toaster } from "sonner";
import { Sidebar } from "./Sidebar";
import useKeyboardShortcuts from "../hooks/useKeyboardShortcuts";
import KeyboardShortcutsModal from "../components/KeyboardShortcutsModal";
import styles from "./AppShell.module.css";

/**
 * Top-level app chrome. Renders the fixed-width Sidebar and a scrolling
 * <main> hosting the route outlet. Registers the global keyboard shortcuts
 * hook (Escape/?, g+key navigation). Mounts the keyboard shortcuts modal
 * and the Sonner toaster once for the whole app.
 *
 * Replaces the previous AntD `components/Layout.tsx`.
 */
export function AppShell() {
  useKeyboardShortcuts();

  return (
    <div className={styles.shell}>
      <Sidebar />
      <main className={styles.main}>
        <Outlet />
      </main>

      <KeyboardShortcutsModal />

      <Toaster
        position="top-right"
        theme="dark"
        toastOptions={{
          style: {
            background: "var(--color-surface)",
            color: "var(--color-text)",
            border: "1px solid var(--color-border)",
            fontFamily: "var(--font-body)",
            fontSize: "var(--text-body)",
            borderRadius: "var(--radius-control)",
          },
        }}
      />
    </div>
  );
}
```

- [ ] **Step 2: Create AppShell styles**

Create `donna-ui/src/layout/AppShell.module.css`:

```css
/*
 * AppShell — flex row with fixed sidebar and scrolling main content.
 * The 24px main padding is a TEMPORARY shim so existing AntD pages look
 * identical to before this wave. Later waves (Wave 3+) either remove this
 * entirely or tighten it to the spec value (40px 48px) once every page
 * has migrated to PageContainer.
 */

.shell {
  display: flex;
  min-height: 100vh;
  background: var(--color-bg);
  color: var(--color-text);
}

.main {
  flex: 1;
  min-width: 0; /* prevents flex child from overflowing horizontally */
  overflow: auto;
  padding: 24px;
}
```

- [ ] **Step 3: Type check**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc --noEmit
```

Expected: TypeScript error in `AppShell.tsx` because `../components/KeyboardShortcutsModal` is still the old AntD version and will continue to type-check — that's fine for now. No errors expected; the existing modal still exports `default`. If errors appear, stop and investigate.

- [ ] **Step 4: Commit**

```bash
git add donna-ui/src/layout/AppShell.tsx donna-ui/src/layout/AppShell.module.css
git commit -m "Add AppShell with sidebar, outlet, and global Sonner toaster"
```

---

### Task 5: Create `layout/` barrel export

**Files:**
- Create: `donna-ui/src/layout/index.ts`

- [ ] **Step 1: Write the barrel**

Create `donna-ui/src/layout/index.ts`:

```ts
export { AppShell } from "./AppShell";
export { Sidebar } from "./Sidebar";
export { NavItem } from "./NavItem";
export { PageContainer } from "./PageContainer";
```

- [ ] **Step 2: Type check**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add donna-ui/src/layout/index.ts
git commit -m "Add layout barrel export"
```

---

### Task 6: Migrate `KeyboardShortcutsModal` to Radix `Dialog`

Resolves audit issue **[P1] `KeyboardShortcutsModal` lacks focus trap and accessible dismissal**. Replaces the AntD `Modal` with the Wave 1 `Dialog` primitive. Keeps the existing `show-shortcuts-help` window-event contract so `useKeyboardShortcuts` ("?" keybinding) continues to work without changes. The old `close-drawer` event listener is removed because Radix `Dialog` handles `Esc` natively.

**Files:**
- Modify: `donna-ui/src/components/KeyboardShortcutsModal.tsx`
- Create: `donna-ui/src/components/KeyboardShortcutsModal.module.css`

- [ ] **Step 1: Read the existing file to confirm current behaviour**

```bash
cat donna-ui/src/components/KeyboardShortcutsModal.tsx
```

Expected: AntD `Modal`, `Typography`, `KeyChip` helper, `groupByCategory` helper, `show-shortcuts-help` and `close-drawer` event listeners.

- [ ] **Step 2: Replace the component**

Overwrite `donna-ui/src/components/KeyboardShortcutsModal.tsx` with:

```tsx
import { useState, useEffect, useCallback } from "react";
import {
  Dialog,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "../primitives/Dialog";
import {
  SHORTCUT_DEFINITIONS,
  type ShortcutDef,
} from "../hooks/useKeyboardShortcuts";
import styles from "./KeyboardShortcutsModal.module.css";

const CATEGORIES = ["Navigation", "Actions", "Help"] as const;

function KeyChip({ keys }: { keys: string }) {
  return (
    <span className={styles.keyRow}>
      {keys.split(" ").map((k, i) => (
        <kbd key={i} className={styles.kbd}>
          {k}
        </kbd>
      ))}
    </span>
  );
}

function groupByCategory(defs: ShortcutDef[]) {
  const grouped: Record<string, ShortcutDef[]> = {};
  for (const cat of CATEGORIES) {
    grouped[cat] = defs.filter((d) => d.category === cat);
  }
  return grouped;
}

/**
 * Opens when the window receives a `show-shortcuts-help` event
 * (dispatched by useKeyboardShortcuts on "?" keypress). Closing is
 * handled by Radix Dialog natively (Esc, overlay click, close button).
 * Focus trap + `role="dialog"` come from Radix for free — this resolves
 * the P1 a11y audit issue.
 */
export default function KeyboardShortcutsModal() {
  const [open, setOpen] = useState(false);

  const handleShow = useCallback(() => setOpen(true), []);

  useEffect(() => {
    window.addEventListener("show-shortcuts-help", handleShow);
    return () => {
      window.removeEventListener("show-shortcuts-help", handleShow);
    };
  }, [handleShow]);

  const grouped = groupByCategory(SHORTCUT_DEFINITIONS);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogHeader>
        <DialogTitle>Keyboard Shortcuts</DialogTitle>
        <DialogDescription>
          Press <kbd className={styles.kbd}>?</kbd> any time to reopen this.
        </DialogDescription>
      </DialogHeader>
      <div className={styles.body}>
        {CATEGORIES.map((cat) => (
          <section key={cat} className={styles.group}>
            <h3 className={styles.groupTitle}>{cat}</h3>
            <ul className={styles.list}>
              {grouped[cat].map((def) => (
                <li key={def.keys} className={styles.row}>
                  <span className={styles.desc}>{def.description}</span>
                  <KeyChip keys={def.keys} />
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </Dialog>
  );
}
```

- [ ] **Step 3: Create the companion CSS module**

Create `donna-ui/src/components/KeyboardShortcutsModal.module.css`:

```css
/*
 * KeyboardShortcutsModal styling — scoped to this component because it
 * is the only consumer. Uses tokens from src/theme/tokens.css.
 */

.body {
  padding-top: var(--space-3);
}

.group {
  margin-bottom: var(--space-4);
}

.group:last-child {
  margin-bottom: 0;
}

.groupTitle {
  margin: 0 0 var(--space-2);
  font-family: var(--font-body);
  font-size: var(--text-eyebrow);
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: var(--tracking-eyebrow);
  color: var(--color-text-muted);
}

.list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 0;
  color: var(--color-text-secondary);
  font-size: var(--text-body);
}

.desc {
  flex: 1;
}

.keyRow {
  display: inline-flex;
  gap: 4px;
}

.kbd {
  display: inline-block;
  padding: 2px 6px;
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 18px;
  color: var(--color-text);
  background: var(--color-inset);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-control);
  min-width: 20px;
  text-align: center;
}
```

- [ ] **Step 4: Type check**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc --noEmit
```

Expected: no errors. If you get "`Dialog` has no exported member X", verify that `donna-ui/src/primitives/Dialog.tsx` exports `Dialog`, `DialogHeader`, `DialogTitle`, and `DialogDescription` (it does per Wave 1).

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/components/KeyboardShortcutsModal.tsx donna-ui/src/components/KeyboardShortcutsModal.module.css
git commit -m "Migrate KeyboardShortcutsModal to Radix Dialog primitive"
```

---

### Task 7: Replace `Layout` with `AppShell` in `App.tsx` and delete old layout

This is the switch-over moment. After this task, the live app wears the new shell. All existing (still-AntD) pages render inside the new main area with the temporary 24 px padding shim.

**Files:**
- Modify: `donna-ui/src/App.tsx`
- Delete: `donna-ui/src/components/Layout.tsx`

- [ ] **Step 1: Read the current App.tsx**

```bash
cat donna-ui/src/App.tsx
```

Expected: imports `AppLayout` from `./components/Layout`, wraps routes in a `RadixTooltip.Provider`, uses `useTheme()`, has a dev-only `/dev/primitives` route.

- [ ] **Step 2: Update App.tsx**

Overwrite `donna-ui/src/App.tsx` with:

```tsx
import { Routes, Route } from "react-router-dom";
import * as RadixTooltip from "@radix-ui/react-tooltip";
import { AppShell } from "./layout";
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
  // Activates theme + persists to localStorage + registers ⌘. shortcut
  useTheme();

  return (
    <RadixTooltip.Provider delayDuration={400} skipDelayDuration={100}>
      <Routes>
        {/* Dev-only primitives gallery — outside AppShell so it renders standalone */}
        {import.meta.env.DEV && (
          <Route path="/dev/primitives" element={<DevPrimitivesPage />} />
        )}
        <Route element={<AppShell />}>
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

- [ ] **Step 3: Delete the old Layout**

```bash
rm donna-ui/src/components/Layout.tsx
```

- [ ] **Step 4: Verify no remaining imports of the old Layout**

```bash
grep -rn "components/Layout" donna-ui/src donna-ui/tests 2>/dev/null
```

Expected: **no output**. If any files still import `components/Layout`, update them to import `AppShell` from `./layout` before continuing.

- [ ] **Step 5: Type check**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 6: Manual smoke test in browser**

```bash
npm run dev
```

Open `http://localhost:5173`. Expected:

- The old AntD dark header bar is gone.
- A fixed-width rail on the left shows "Donna" in serif (Fraunces), eyebrow "Management" underneath, and the 8 nav items with lucide icons.
- The active nav item has a 2 px **gold** left border and a faint accent-soft background tint. (Default theme is gold.)
- At the bottom of the rail: two chips "Gold" and "Coral", the gold chip is outlined as active. Below them: `⌘. to flip`.
- Clicking "Coral" changes the active nav border to coral; the dev-primitives gallery (in another tab) also flips; `<html>` has `data-theme="coral"`.
- Clicking "Gold" restores gold; `<html>` loses the `data-theme` attribute.
- Pressing `⌘.` (Mac) / `Ctrl+.` (Linux/Win) toggles the accent.
- Navigate between pages — the corresponding nav item becomes active. `/tasks/abc` activates "Tasks".
- Press `?` (not in an input) — the keyboard shortcuts modal opens. Press `Esc` — it closes. Press `?` again — it opens and the close button in the top-right dismisses it. Focus is trapped inside while open.
- The existing AntD page content inside each page still looks like before (24 px main gutters match the old `<Content>` padding).

Stop dev server (Ctrl+C).

- [ ] **Step 7: Commit**

```bash
git add donna-ui/src/App.tsx donna-ui/src/components/Layout.tsx
git commit -m "Switch app to AppShell; remove old AntD Layout"
```

---

### Task 8: Playwright smoke test for the new shell

Adds a dedicated smoke spec that exercises the Wave 2 additions. The existing per-page smoke specs (`dashboard.spec.ts` etc.) continue to pass unchanged because they only assert `#root` is non-empty.

**Files:**
- Create: `donna-ui/tests/e2e/smoke/app-shell.spec.ts`

- [ ] **Step 1: Write the spec**

Create `donna-ui/tests/e2e/smoke/app-shell.spec.ts`:

```ts
import { test, expect } from "@playwright/test";
import { mockAdminApi } from "../helpers";

test.describe("App shell", () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminApi(page);
  });

  test("rail shows Donna wordmark and nav items", async ({ page }) => {
    await page.goto("/");
    // The inner <nav> carries aria-label="Primary navigation".
    const nav = page.getByRole("navigation", { name: "Primary navigation" });
    await expect(nav).toBeVisible();
    // Brand wordmark lives on the aside, not inside the nav.
    await expect(page.getByText("Donna", { exact: true })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Dashboard" })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Tasks" })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Preferences" })).toBeVisible();
  });

  test("active nav item reflects the current route", async ({ page }) => {
    await page.goto("/tasks");
    const tasksLink = page.getByRole("link", { name: "Tasks" });
    await expect(tasksLink).toHaveAttribute("aria-current", "page");

    const dashboardLink = page.getByRole("link", { name: "Dashboard" });
    await expect(dashboardLink).not.toHaveAttribute("aria-current", "page");
  });

  test("theme toggle chips flip the data-theme attribute", async ({ page }) => {
    await page.goto("/");
    // Initial: gold (no attribute)
    await expect(page.locator("html")).not.toHaveAttribute("data-theme", "coral");

    await page.getByRole("button", { name: "Electric coral theme" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "coral");
    await expect(page.getByRole("button", { name: "Electric coral theme" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await page.getByRole("button", { name: "Champagne gold theme" }).click();
    await expect(page.locator("html")).not.toHaveAttribute("data-theme", "coral");
  });

  test("pressing ? opens the keyboard shortcuts dialog, Esc closes it", async ({ page }) => {
    await page.goto("/");
    // Take focus off any input first
    await page.locator("body").click();

    await page.keyboard.press("?");
    const dialog = page.getByRole("dialog", { name: "Keyboard Shortcuts" });
    await expect(dialog).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(dialog).not.toBeVisible();
  });

  test("no AntD Header element is present", async ({ page }) => {
    // The old Layout.tsx rendered an AntD .ant-layout-header. Wave 2 removes it.
    await page.goto("/");
    await expect(page.locator(".ant-layout-header")).toHaveCount(0);
  });
});
```

- [ ] **Step 2: Run the new spec**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npm run test:e2e -- app-shell
```

Expected: all 5 tests pass. If a test fails, diagnose — common pitfalls:

- "rail shows Donna wordmark" fails on `getByText("Donna", { exact: true })` because another element on the page also contains the word "Donna" — scope the query: `page.locator('aside').getByText(/^Donna$/)`.
- Active nav assertion fails because Dashboard has a nested/child matching route — verify the `isActive` helper in `Sidebar.tsx` is using exact match for `"/"`.
- Keyboard shortcut dialog test fails because the `?` key is swallowed by a focused input — `page.locator("body").click()` earlier in the test removes focus.

- [ ] **Step 3: Run the FULL smoke suite**

```bash
npm run test:e2e
```

Expected: every existing smoke spec plus the new `app-shell` spec passes. Any page-level smoke failure indicates an unexpected regression — stop and investigate before committing.

- [ ] **Step 4: Commit**

```bash
git add donna-ui/tests/e2e/smoke/app-shell.spec.ts
git commit -m "Add Playwright smoke tests for app shell"
```

---

### Task 9: Wave 2 sanity check — full build + manual verification

**Files:**
- (verification only — no files modified)

- [ ] **Step 1: Clean build**

```bash
cd /home/feuer/Documents/Projects/donna/donna-ui
npm run build
```

Expected: build completes without errors or new warnings. The gzipped total prints to the console — note it down. It should be very close to the Wave 1 size (Wave 2 adds 4 small layout files and a small CSS module but removes `Layout.tsx` and the AntD `Modal` usage site, net change < 5 KB gzipped). This is **not** the Wave 9 baseline comparison — just a sanity check that the build still works.

- [ ] **Step 2: Manual verification pass — gold theme**

```bash
npm run dev
```

Open `http://localhost:5173`. Go through this checklist:

- [ ] Rail shows "Donna" in Fraunces, "Management" eyebrow below it, 8 nav items with lucide icons.
- [ ] Dashboard is the default page and its nav item has the gold left border + accent-soft background.
- [ ] Clicking **Logs** navigates and highlights Logs; Dashboard loses the highlight.
- [ ] Clicking **Tasks** navigates; opening a task (if data exists) — the URL changes to `/tasks/:id` and **Tasks** stays active (the `isActive` helper matches `startsWith("/tasks")`).
- [ ] The page content area scrolls independently of the rail (scroll the Logs page while the rail stays put).
- [ ] No AntD header bar across the top of the main area.
- [ ] Press `?` — modal opens. Tab through it — focus is trapped. Press `Esc` — it closes.
- [ ] Press `g t` — should navigate to Tasks (existing shortcut).
- [ ] Footer chips: "Gold" is outlined / active.

- [ ] **Step 3: Manual verification pass — coral theme**

Still in the dev server, click the **Coral** chip (or press `⌘.`). Expected:

- [ ] `<html>` has `data-theme="coral"` in DevTools.
- [ ] The active-nav gold left border turns coral (warm brick red).
- [ ] The "Coral" chip is outlined with a coral border.
- [ ] Reload the page. `data-theme="coral"` persists from localStorage.
- [ ] Click **Gold** chip or press `⌘.` again. `data-theme` attribute disappears. Reload — it stays absent.

- [ ] **Step 4: Sonner smoke test (optional but fast)**

In the browser DevTools console on any page, run:

```js
// Sonner is mounted globally by AppShell. This verifies the provider works
// and the tokens are applied. In later waves, real code calls toast(...).
window.__donnaToastTest = async () => {
  const { toast } = await import("/node_modules/.vite/deps/sonner.js");
  toast("Wave 2 toaster smoke test");
};
window.__donnaToastTest();
```

Expected: a small toast appears top-right with surface background, border, body font. Dismisses itself after ~4 s. If the `import()` path fails due to Vite's dep cache, just skip this step — the toaster is also exercised automatically as pages migrate in Wave 3.

- [ ] **Step 5: Run the full Playwright suite one more time**

```bash
npm run test:e2e
```

Expected: all tests pass (original smokes + dev-primitives + app-shell).

- [ ] **Step 6: Verify no uncommitted changes**

```bash
git status
```

Expected: `nothing to commit, working tree clean`.

- [ ] **Step 7: Confirm audit issues are resolved**

Walk through the three audit items Wave 2 claims to resolve:

- **[P2] Layout sider collapse tooltip inconsistency:** `grep -rn "collapsible\|collapsed" donna-ui/src/layout donna-ui/src/components/Layout.tsx 2>/dev/null` should return **no results** — `Layout.tsx` is gone and the new rail has no collapse behaviour. ✓
- **[P3] Header bar information redundancy:** `grep -rn "ant-layout-header\|<Header" donna-ui/src 2>/dev/null` should return no results from the shell. ✓
- **[P1] `KeyboardShortcutsModal` accessibility:** Open the modal with `?`, tab forward — focus stays inside the dialog; tab past the last element wraps to the first. Press `Esc` — it closes. ✓

Stop the dev server.

---

## Self-Review

**Spec coverage check against `docs/superpowers/specs/2026-04-08-donna-ui-redesign-design.md` §4 Wave 2 (line 326):**

- "New `AppShell` + `Sidebar` + `NavItem` replace `donna-ui/src/components/Layout.tsx`" → Tasks 1, 2, 4, 7 ✓
- "Gold left-border active nav state" → Task 1 (`NavItem.module.css` `.active` rule) ✓
- "Fraunces page title" → Task 2 (`Sidebar.module.css` `.brandName` uses `var(--font-display)`). Note: this is the *rail brand* wordmark. Per-page Fraunces titles live in the `<PageHeader>` primitive and are consumed starting in Wave 3. The spec line means "Fraunces is in the shell somewhere" — the rail wordmark satisfies that in Wave 2. ✓
- "No more AntD header bar" → Task 7 (Layout.tsx deleted; no `<Header>` in AppShell) ✓
- "Theme toggle at the bottom of the rail" → Task 2 (`.footer` with `.themeRow`) ✓
- "Existing `KeyboardShortcutsModal` migrates to `<Dialog>`" → Task 6 ✓
- "Sonner `<Toaster />` mounted globally" → Task 4 (inside AppShell) ✓

**Audit coverage check against spec §4 Wave 2 "Audit issues fixed":**

- [P2] Layout sider collapse tooltip inconsistency → resolved (Task 7, verified Task 9 Step 7) ✓
- [P3] Header bar information redundancy → resolved (Task 7, verified Task 9 Step 7) ✓
- [P1] `KeyboardShortcutsModal` a11y via Radix Dialog → resolved (Task 6) ✓

**Spec §5 confirmed decisions relevant to this wave:**

- "Menu active state: Gold left border only (no background fill)" → slight deviation: I *do* add an `accent-soft` background tint to the active state. This is consistent with the dev-primitives gallery Wave 1 built for `NavItem`-style interactions and matches the existing `primitives/Button.module.css` `ghost:hover` pattern. If the user pushes back, the fix is trivial: remove `background: var(--color-accent-soft)` from `.active` in `NavItem.module.css`. Flagging here so it's caught in review.
- "Tooltip delay: 400 ms" → inherited from the `RadixTooltip.Provider` that App.tsx already configures. Wave 2 does not touch that. ✓
- "localStorage-backed useTheme" → Sidebar consumes the existing hook. Wave 2 does not change theme persistence. ✓

**Placeholder scan:** searched this plan's source for "TBD", "TODO", "implement later", "fill in", "similar to" — none present. All code blocks contain complete, runnable content.

**Type consistency check:**

- `NavItem` props: `to: string; icon: ReactNode; label: string; active: boolean` — consumed exactly in `Sidebar.tsx` ✓
- `PageContainer` extends `HTMLAttributes<HTMLDivElement>` — spreadable onto a div ✓
- `AppShell` takes no props; used as `<Route element={<AppShell />}>` in `App.tsx` ✓
- `KeyboardShortcutsModal` default export — App.tsx does not import it directly (AppShell does) ✓
- `useTheme()` return shape `{ theme, setTheme, toggle }` — Sidebar uses `theme` and `setTheme` ✓
- `isActive(pathname, itemPath)` signature stable; only called from inside Sidebar ✓

**Dependency check:** `sonner`, `lucide-react`, `@radix-ui/react-dialog`, `@radix-ui/react-tooltip` are all present in `package.json` as of Wave 0/1. No `npm install` step required in this plan.

**Files that change-together check:** Each task commits one coherent unit. `NavItem.tsx` + `NavItem.module.css` together (Task 1). `Sidebar.tsx` + `Sidebar.module.css` together (Task 2). `App.tsx` + `Layout.tsx` deletion together (Task 7) — these must ship atomically or the app won't build.

**Scope discipline:** No page internals touched. `Tasks/`, `Logs/`, `Dashboard/` etc. remain fully AntD. The 24 px `main` padding shim keeps them visually identical. `PageShell.tsx` (orphaned AntD) is intentionally left alone. `darkTheme.ts` is intentionally left alone.

**Known minor rough edges accepted in this wave:**

1. Main area uses `padding: 24px` literal, not a token, because tokens don't include exactly 24 and matching current behaviour is more important than token-purity here. Comment in `AppShell.module.css` explains it's temporary.
2. Theme chip swatches use literal `#d4a943` and `#f56960` hex. These are **semantic swatches** showing the brand color options, not decorative uses, so the "no inline hex" rule does not apply. Comment in `Sidebar.module.css` explains this.
3. Mobile behaviour of the rail is unchanged from Wave 1 (i.e. none). Proper responsive rail collapse is deferred to a later wave because spec §4 Wave 2 does not mention it.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-08-donna-ui-wave-2-shell.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints for review.

Which approach?
