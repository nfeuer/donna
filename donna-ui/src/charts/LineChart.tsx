import {
  CartesianGrid,
  Line,
  LineChart as RLineChart,
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

export interface LineSeries {
  dataKey: string;
  name: string;
  /** "accent" (default), "muted", "warning", "error". */
  tone?: "accent" | "muted" | "warning" | "error";
}

interface LineChartProps<T extends object> {
  data: T[];
  series: LineSeries[];
  xKey?: keyof T & string;
  formatTick?: (value: string) => string;
  formatValue?: (value: number) => string;
  height?: number;
  ariaLabel?: string;
}

/**
 * Hairline line chart — 1.5 px strokes, no fill. Each series picks
 * its color from a semantic tone resolved against useChartColors().
 * Used for overlapping time series where stacked area would obscure
 * the signal. Shadow page (Wave 8) is the primary consumer.
 */
export function LineChart<T extends object>({
  data,
  series,
  xKey = "date" as keyof T & string,
  formatTick,
  formatValue,
  height = 160,
  ariaLabel,
}: LineChartProps<T>) {
  const colors = useChartColors();

  const toneColor = (tone: LineSeries["tone"]): string => {
    switch (tone) {
      case "warning":
        return colors.warning;
      case "error":
        return colors.error;
      case "muted":
        return colors.textDim;
      default:
        return colors.accent;
    }
  };

  return (
    <div role="img" aria-label={ariaLabel}>
      <ResponsiveContainer width="100%" height={height}>
        <RLineChart data={data} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
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
          />
          {series.map((s) => (
            <Line
              key={s.dataKey}
              type="monotone"
              dataKey={s.dataKey}
              name={s.name}
              stroke={toneColor(s.tone)}
              strokeWidth={1.5}
              dot={false}
              activeDot={{ r: 3, fill: toneColor(s.tone), stroke: colors.surface }}
              isAnimationActive={false}
            />
          ))}
        </RLineChart>
      </ResponsiveContainer>
    </div>
  );
}
