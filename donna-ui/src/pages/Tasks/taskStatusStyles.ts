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
