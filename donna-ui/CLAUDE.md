# donna-ui — Frontend Conventions

## Stack
- React 18 + TypeScript (strict mode) + Vite
- CSS Modules + CSS custom properties (no Tailwind, no styled-components)
- Radix UI primitives, TanStack Table, Recharts, Sonner toasts
- Axios for API calls, React Router v6

## Imports

### Primitives
Always import from the **direct file**, not the barrel (`primitives/index.ts`):
```ts
// Correct
import { Button } from "../../primitives/Button";
import { Pill, type PillVariant } from "../../primitives/Pill";

// Wrong — don't use the barrel
import { Button, Pill } from "../../primitives";
```

The barrel exists for external consumers but internal code uses direct imports for better tree-shaking and IDE navigation.

### Charts
Charts use their own barrel — this is fine since the chart module is small:
```ts
import { BarChart, ChartCard, type ChartCardStat } from "../../charts";
```

### Import ordering
1. React / external packages
2. Internal modules (api, charts, primitives, hooks, lib)
3. Sibling components
4. Styles (`.module.css`)

## API Files (`src/api/`)

### Response destructuring
Always destructure `data` from the axios response:
```ts
// Correct
const { data } = await client.get("/admin/endpoint");
return data;

// Wrong
const resp = await client.get("/admin/endpoint");
return resp.data;
```

When the API wraps a list in a named key, access it after destructuring:
```ts
const { data } = await client.get("/admin/configs");
return data.configs; // not resp.data.configs
```

### Function signatures
- Export async functions, not classes or objects
- Accept filter objects for list endpoints; use `Record<string, string | number>` for params
- Return typed promises: `Promise<ConfigFile[]>`

## Components

### Page components (`src/pages/`)
- Use `export default function PageName()` syntax
- Props interface named `interface Props` (short form — these are internal, single-consumer)
- One page component per directory with an `index.tsx` entry point
- Sub-components (tables, drawers, filters) in the same directory

### Primitive components (`src/primitives/`)
- Props interface named with component prefix: `interface ButtonProps`, `interface PillProps`
- Use `forwardRef` when wrapping native HTML elements
- Set `Component.displayName` on all forwardRef components
- Extend native HTML attributes where appropriate: `extends ButtonHTMLAttributes<HTMLButtonElement>`

### General
- Functional components only (except `ErrorBoundary` which requires a class)
- Destructure props in the function signature
- Use `useCallback` for event handlers passed to children
- Use `useMemo` sparingly — only for genuinely expensive computations (e.g., column definitions, markdown rendering)

## Styling

### CSS Modules
Every component has a paired `.module.css` file:
```ts
import styles from "./Component.module.css";
```

Use `cn()` from `../lib/cn` (thin wrapper around `clsx`) for composing class names:
```ts
import { cn } from "../lib/cn";
className={cn(styles.button, styles[variant], isActive && styles.active)}
```

### CSS class naming
- camelCase for CSS module classes: `.navList`, `.primaryButton`
- kebab-case for CSS custom properties: `--color-accent`, `--space-1`

### Design tokens
All colors, typography, spacing, and motion values come from `src/theme/tokens.css` as CSS custom properties. Never hardcode hex values, pixel sizes, or timing curves in component CSS.

### Themes
Two accent themes (gold/coral) switched via `[data-theme]` attribute on `<html>`. All accent references use `var(--color-accent)`. See `src/theme/` for token definitions.

## TypeScript
- `strict: true` with `noUnusedLocals` and `noUnusedParameters`
- Use `interface` for component props, `type` for unions and utility types
- Use `import type` when importing only types
- Avoid `any` — the single exception is `StructuredEditor.tsx` for dynamic YAML form data

## Error handling
- Global axios interceptor in `src/api/client.ts` handles 500+ and network errors with Sonner toasts
- Page components use try-catch with fallback state (e.g., `setData(null)`)
- Route-level `ErrorBoundary` wraps all pages

## Testing
- Vite build (`npx vite build`) must pass — this catches all import and type errors
- TypeScript check (`npx tsc --noEmit`) must pass
- Backend pytest suite covers API endpoints
