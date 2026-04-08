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
