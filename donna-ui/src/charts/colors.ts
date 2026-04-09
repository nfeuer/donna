import { useEffect, useState } from "react";

/**
 * Live-read color palette for Recharts.
 *
 * Every field is a resolved CSS value (hex, rgba, or oklch string —
 * whatever the token contains). Consumers pass these strings directly
 * to Recharts props like `stroke`, `fill`, `contentStyle`, `tick.fill`.
 *
 * The palette is theme-aware: when [data-theme="coral"] is toggled
 * on <html>, the MutationObserver in `useChartColors` fires and the
 * hook re-reads, causing subscribers to re-render with the new accent.
 */
export interface ChartColors {
  accent: string;
  accentSoft: string;
  accentBorder: string;
  borderSubtle: string;
  surface: string;
  textMuted: string;
  textDim: string;
  success: string;
  warning: string;
  error: string;
}

const TOKEN_MAP: Record<keyof ChartColors, string> = {
  accent: "--color-accent",
  accentSoft: "--color-accent-soft",
  accentBorder: "--color-accent-border",
  borderSubtle: "--color-border-subtle",
  surface: "--color-surface",
  textMuted: "--color-text-muted",
  textDim: "--color-text-dim",
  success: "--color-success",
  warning: "--color-warning",
  error: "--color-error",
};

// SSR-safe defaults; match tokens.css `:root` defaults exactly. Only hit
// when running outside a browser (unit tests without jsdom, SSR builds).
const DEFAULT_CHART_COLORS: ChartColors = {
  accent: "#d4a943",
  accentSoft: "rgba(212, 169, 67, 0.10)",
  accentBorder: "rgba(212, 169, 67, 0.28)",
  borderSubtle: "#221f1c",
  surface: "#1f1c18",
  textMuted: "#8a8378",
  textDim: "#5e5850",
  success: "#8aa672",
  warning: "#d4a943",
  error: "#c8665e",
};

function readChartColors(): ChartColors {
  if (typeof document === "undefined") return DEFAULT_CHART_COLORS;
  const style = getComputedStyle(document.documentElement);
  const out = {} as ChartColors;
  for (const [key, varName] of Object.entries(TOKEN_MAP) as Array<
    [keyof ChartColors, string]
  >) {
    const value = style.getPropertyValue(varName).trim();
    out[key] = value || DEFAULT_CHART_COLORS[key];
  }
  return out;
}

/**
 * Returns the live chart palette and re-renders the caller whenever
 * the theme changes. Internally subscribes to a MutationObserver on
 * <html>'s `data-theme` attribute — so the hook is decoupled from
 * React state ownership and works regardless of which component
 * happens to own the useTheme hook's state.
 */
export function useChartColors(): ChartColors {
  const [colors, setColors] = useState<ChartColors>(readChartColors);

  useEffect(() => {
    if (typeof document === "undefined") return;
    const root = document.documentElement;
    const observer = new MutationObserver(() => setColors(readChartColors()));
    observer.observe(root, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    return () => observer.disconnect();
  }, []);

  return colors;
}
