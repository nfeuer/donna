import { useId } from "react";
import {
  Area,
  AreaChart as RAreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useChartColors } from "./colors";
import {
  axisLineStyle,
  axisTickStyle,
  gridProps,
  tooltipContentStyle,
  tooltipItemStyle,
  tooltipLabelStyle,
} from "./theme";

export interface AreaChartReference {
  /** Y value where the line is drawn. */
  y: number;
  /** Optional label rendered at the right end of the line. */
  label?: string;
  /** Which semantic token to color it with. Defaults to "warning". */
  tone?: "warning" | "error" | "muted";
}

interface AreaChartProps<T extends object> {
  data: T[];
  /** Numeric field on each row that becomes the area series. */
  dataKey: keyof T & string;
  /** Categorical/time field for the X axis. Defaults to "date". */
  xKey?: keyof T & string;
  /** Format the tooltip value (e.g. `(v) => `$${v.toFixed(2)}``). */
  formatValue?: (value: number) => string;
  /** Format the X-axis tick (e.g. `(v) => v.slice(5)` for MM-DD). */
  formatTick?: (value: string) => string;
  /** Human label for the tooltip series row. */
  name?: string;
  referenceLine?: AreaChartReference;
  /** Fixed pixel height. Defaults to 160 — matches the spec's dashboard density. */
  height?: number;
  /** ARIA label for the chart region (wraps ResponsiveContainer). */
  ariaLabel?: string;
}

/**
 * Soft-wash area chart — single accent series, 10% gradient fill,
 * 1.5 px line, grid on, Y-axis hidden by default? No — shown, muted.
 *
 * All colors flow through useChartColors() so the chart repaints on
 * the cmd+. theme flip without a reload.
 */
export function AreaChart<T extends object>({
  data,
  dataKey,
  xKey = "date" as keyof T & string,
  formatValue,
  formatTick,
  name,
  referenceLine,
  height = 160,
  ariaLabel,
}: AreaChartProps<T>) {
  const colors = useChartColors();
  const gradientId = useId();
  const refTone = referenceLine?.tone ?? "warning";
  const refColor =
    refTone === "error"
      ? colors.error
      : refTone === "muted"
        ? colors.textDim
        : colors.warning;

  return (
    <div role="img" aria-label={ariaLabel}>
      <ResponsiveContainer width="100%" height={height}>
        <RAreaChart data={data} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={colors.accent} stopOpacity={0.22} />
              <stop offset="100%" stopColor={colors.accent} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid {...gridProps(colors)} />
          <XAxis
            dataKey={xKey}
            tick={axisTickStyle(colors)}
            tickFormatter={formatTick}
            tickLine={false}
            axisLine={axisLineStyle(colors)}
          />
          <YAxis
            tick={axisTickStyle(colors)}
            tickFormatter={formatValue ? (value: number) => formatValue(value) : undefined}
            tickLine={false}
            axisLine={axisLineStyle(colors)}
            width={48}
          />
          <Tooltip
            contentStyle={tooltipContentStyle(colors)}
            itemStyle={tooltipItemStyle(colors)}
            labelStyle={tooltipLabelStyle(colors)}
            formatter={
              formatValue
                ? (value: number) => [formatValue(value), name ?? dataKey]
                : undefined
            }
          />
          {referenceLine && (
            <ReferenceLine
              y={referenceLine.y}
              stroke={refColor}
              strokeDasharray="4 4"
              label={
                referenceLine.label
                  ? {
                      value: referenceLine.label,
                      fill: refColor,
                      fontSize: 10,
                      position: "right",
                    }
                  : undefined
              }
            />
          )}
          <Area
            type="monotone"
            dataKey={dataKey as string}
            stroke={colors.accent}
            strokeWidth={1.5}
            fill={`url(#${gradientId})`}
            name={name ?? (dataKey as string)}
            activeDot={{ r: 3, fill: colors.accent, stroke: colors.surface }}
            isAnimationActive={false}
          />
        </RAreaChart>
      </ResponsiveContainer>
    </div>
  );
}
