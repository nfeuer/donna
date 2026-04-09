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
  cancelled: "var(--color-text-muted)",
};

export function statePillVariant(state: string): PillVariant {
  return STATE_PILL_VARIANT[state] ?? "muted";
}

export function stateCssVar(state: string): string {
  return STATE_CSS_VAR[state] ?? "var(--color-text-muted)";
}
