import type { CSSProperties } from "react";
import type { ChartColors } from "./colors";

/**
 * Recharts prop builders. Call these inside a component that has a
 * live `ChartColors` from useChartColors(), then spread the return
 * value onto the corresponding Recharts sub-component. Example:
 *
 *   const colors = useChartColors();
 *   <CartesianGrid {...gridProps(colors)} />
 *
 * Keeping these as pure builders (not JSX) means every chart wrapper
 * can opt out selectively (e.g. LineChart skips grid) without React
 * component overhead.
 */

export function gridProps(colors: ChartColors) {
  return {
    stroke: colors.borderSubtle,
    strokeDasharray: "3 3",
    vertical: false,
  } as const;
}

export function axisTickStyle(colors: ChartColors) {
  return {
    fill: colors.textMuted,
    fontSize: 10,
    fontFamily: "var(--font-mono)",
  } as const;
}

export function axisLineStyle(colors: ChartColors) {
  return {
    stroke: colors.borderSubtle,
  } as const;
}

export function tooltipContentStyle(colors: ChartColors): CSSProperties {
  return {
    background: colors.surface,
    border: `1px solid ${colors.accentBorder}`,
    borderRadius: 2,
    fontSize: 12,
    fontFamily: "var(--font-body)",
    color: "var(--color-text)",
    boxShadow: "0 8px 24px rgba(0, 0, 0, 0.35)",
  };
}

export function tooltipItemStyle(colors: ChartColors): CSSProperties {
  return {
    color: colors.accent,
    fontFamily: "var(--font-mono)",
  };
}

export function tooltipLabelStyle(colors: ChartColors): CSSProperties {
  return {
    color: colors.textMuted,
    fontSize: 11,
    marginBottom: 2,
  };
}
