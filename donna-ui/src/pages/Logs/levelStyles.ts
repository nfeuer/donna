import type { PillVariant } from "../../primitives/Pill";

/**
 * Single source of truth for how log levels render as Pills.
 * Replaces the `LEVEL_COLORS` import from `theme/darkTheme.ts`
 * (Wave 3 audit item P2: "Level tag colors scattered inline").
 *
 * DEBUG    → muted  (grey)
 * INFO     → accent (gold/coral, depending on theme)
 * WARNING  → warning
 * ERROR    → error
 * CRITICAL → error  (kept on the same variant; distinguished at
 *                    call sites by surrounding context, not colour,
 *                    to avoid inventing a sixth Pill variant)
 */
export function levelToPillVariant(level: string | undefined): PillVariant {
  switch (level?.toUpperCase()) {
    case "DEBUG":
      return "muted";
    case "INFO":
      return "accent";
    case "WARNING":
    case "WARN":
      return "warning";
    case "ERROR":
    case "CRITICAL":
      return "error";
    default:
      return "muted";
  }
}

export const LEVEL_OPTIONS = [
  { value: "", label: "All" },
  { value: "DEBUG", label: "Debug" },
  { value: "INFO", label: "Info" },
  { value: "WARNING", label: "Warn" },
  { value: "ERROR", label: "Error" },
  { value: "CRITICAL", label: "Critical" },
] as const;

export type LevelFilterValue = (typeof LEVEL_OPTIONS)[number]["value"];
