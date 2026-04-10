# Wave 9 — Cleanup Design Spec

**Date:** 2026-04-10
**Status:** Approved
**Parent spec:** `2026-04-08-donna-ui-redesign-design.md` (section 4, Wave 9)
**Scope:** Remove Ant Design entirely, delete obsolete theme code, sweep hex literals, add accessibility tooling, verify bundle size target, ungrate dev primitives route.

---

## 1. Remove AntD from remaining consumers

Six files still import from `antd` or `@ant-design/icons`. Each is handled individually:

### `main.tsx`

Remove the `ConfigProvider` wrapper and the `darkTheme` import. The app's theming is fully driven by CSS custom properties via `useTheme` — `ConfigProvider` is vestigial.

**Before:**
```tsx
import { ConfigProvider } from "antd";
import darkTheme from "./theme/darkTheme";
// ...
<ConfigProvider theme={darkTheme}>
  <BrowserRouter><App /></BrowserRouter>
</ConfigProvider>
```

**After:**
```tsx
// No antd imports
<BrowserRouter><App /></BrowserRouter>
```

Additionally, wire `@axe-core/react` in dev mode here (see section 5).

### `api/client.ts`

Replace `notification.error/warning` from AntD with `toast.error/toast.warning` from Sonner. Sonner's `<Toaster />` is already mounted globally in `AppShell`.

### `components/ErrorBoundary.tsx`

Replace AntD `Result` and `Button` with plain markup styled via theme tokens + the primitives `Button`. The error state renders: a centered container with an error icon (lucide `AlertTriangle`), heading, error message, and a retry button.

### `components/RefreshButton.tsx`

Replace AntD `Button`, `Space`, `Typography` and `@ant-design/icons` `ReloadOutlined` with: primitives `Button` (ghost variant), lucide `RefreshCw` icon, plain `<span>` for the "ago" text. The spin-on-loading behavior uses a CSS `animation: spin` on the icon when `loading` is true.

### `components/PageShell.tsx`

**Delete entirely.** Zero importers — dead code from the pre-redesign era.

### `theme/darkTheme.ts`

**Delete entirely.** The only consumer is `main.tsx` (being rewritten above). All legacy color constants (`STATUS_COLORS`, `LEVEL_COLORS`, `CHART_COLORS`, `TASK_STATUS_COLORS`, `CHART_TOOLTIP_STYLE`, `CHART_GRID_STROKE`, `CHART_TICK`, `SECONDARY_TEXT_COLOR`) are superseded by `theme/stateColors.ts` and `charts/colors.ts`. No other file imports them.

---

## 2. Uninstall packages

Remove from `package.json` dependencies:
- `antd`
- `@ant-design/icons`

Add to `package.json` devDependencies:
- `@axe-core/react`

---

## 3. Hex literal sweep

After deleting `darkTheme.ts` and `PageShell.tsx`, the remaining hex literals outside `src/theme/` and `src/charts/` are:

| File | Hex values | Disposition |
|---|---|---|
| `layout/Sidebar.module.css` | `#d4a943`, `#f56960` | **Keep** — theme swatch dots showing literal brand accent colors. Existing comment documents the intent. |
| `lib/monacoTheme.ts` | Various token fallbacks | **Keep** — Monaco API requires literal hex strings; CSS `var()` is not supported. These are SSR/test fallbacks that mirror `tokens.css`. |
| `charts/colors.ts` | Various token fallbacks | **Keep** — SSR-safe defaults matching `tokens.css`. Already inside the `charts/` allowlist. |

Acceptance criterion #6 from the parent spec states hex is permitted in `src/theme/` and `src/charts/theme.ts`. The Monaco and sidebar swatch hex values are justified edge cases that don't violate the spirit of the rule (no decorative/arbitrary hex in page-level code).

---

## 4. Remove `/dev/primitives` dev-mode gate

In `App.tsx`, change:
```tsx
{import.meta.env.DEV && (
  <Route path="/dev/primitives" element={<DevPrimitivesPage />} />
)}
```
to:
```tsx
<Route path="/dev/primitives" element={<DevPrimitivesPage />} />
```

The route becomes available in production as an internal reference. It is not linked from the sidebar navigation.

---

## 5. Accessibility sweep with `@axe-core/react`

Install `@axe-core/react` as a dev dependency. Wire it into `main.tsx` with a dynamic import gated on `import.meta.env.DEV`:

```tsx
if (import.meta.env.DEV) {
  import("@axe-core/react").then((axe) => {
    axe.default(React, ReactDOM, 1000);
  });
}
```

This logs WCAG AA violations to the browser console in dev mode. Run through all 8 pages on both themes (gold + coral) and fix any violations found. Common expected findings:
- Missing ARIA labels on interactive elements
- Contrast ratio failures (unlikely given the token design, but verify)
- Missing landmark roles

---

## 6. Bundle size verification

Run `npm run build` after all changes. Compare the gzipped JS total against the Wave 0 baseline:

- **Baseline:** 603.50 kB gzipped JS
- **Target:** <= 362.10 kB (40% reduction)

If the target is missed, investigate with `npx vite-bundle-visualizer` (no install needed, one-shot). Primary suspects would be Recharts or Monaco not tree-shaking cleanly. Those are out of scope for this wave but would be documented as follow-up.

Record the final number by updating `docs/superpowers/specs/bundle-baseline.txt` with a Wave 9 section.

---

## 7. Smoke test pass

All existing Playwright smoke tests must pass after the changes. No new tests are needed for wave 9 — the existing suite covers all 8 pages.

---

## Files changed (summary)

| Action | File |
|---|---|
| Edit | `main.tsx` — remove ConfigProvider, add axe-core |
| Edit | `api/client.ts` — notification -> Sonner toast |
| Edit | `components/ErrorBoundary.tsx` — AntD -> primitives |
| Edit | `components/RefreshButton.tsx` — AntD -> primitives + lucide |
| Edit | `App.tsx` — remove dev gate on primitives route |
| Edit | `package.json` — remove antd, add axe-core |
| Delete | `components/PageShell.tsx` |
| Delete | `theme/darkTheme.ts` |
| Edit | `docs/superpowers/specs/bundle-baseline.txt` — add Wave 9 measurement |
