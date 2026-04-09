import {
  Bar,
  BarChart as RBarChart,
  CartesianGrid,
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

export interface BarSeries {
  dataKey: string;
  name: string;
  /** "accent" (default) or "accentSoft" for secondary series. */
  tone?: "accent" | "accentSoft";
}

interface BarChartProps<T extends object> {
  data: T[];
  series: BarSeries[];
  /** Categorical key (x for vertical, y for horizontal). */
  categoryKey: keyof T & string;
  /** "vertical" = horizontal bars running left→right. "horizontal" = time-series columns. */
  orientation?: "horizontal" | "vertical";
  formatCategoryTick?: (value: string) => string;
  formatValue?: (value: number) => string;
  /** Width of the category axis in horizontal orientation. Defaults to 100. */
  categoryWidth?: number;
  /** Tilt the category tick labels by N degrees (horizontal orientation only). */
  tickAngle?: number;
  height?: number;
  ariaLabel?: string;
}

/**
 * Tick-bar chart. Single accent fill by default; secondary series
 * render in the `accentSoft` wash so two series are still readable
 * without invoking a second hue.
 */
export function BarChart<T extends object>({
  data,
  series,
  categoryKey,
  orientation = "horizontal",
  formatCategoryTick,
  formatValue,
  categoryWidth = 100,
  tickAngle = 0,
  height = 160,
  ariaLabel,
}: BarChartProps<T>) {
  const colors = useChartColors();

  const toneFill = (tone: BarSeries["tone"]): string =>
    tone === "accentSoft" ? colors.accentBorder : colors.accent;

  const valueTickFormatter = formatValue
    ? (value: number) => formatValue(value)
    : undefined;

  return (
    <div role="img" aria-label={ariaLabel}>
      <ResponsiveContainer width="100%" height={height}>
        <RBarChart
          data={data}
          layout={orientation === "vertical" ? "vertical" : "horizontal"}
          margin={{ top: 4, right: 12, left: 0, bottom: tickAngle ? 40 : 0 }}
        >
          <CartesianGrid {...gridProps(colors)} />
          {orientation === "horizontal" ? (
            <>
              <XAxis
                dataKey={categoryKey as string}
                tick={axisTickStyle(colors)}
                tickFormatter={formatCategoryTick}
                tickLine={false}
                axisLine={axisLineStyle(colors)}
                interval={0}
                angle={tickAngle ? -tickAngle : 0}
                textAnchor={tickAngle ? "end" : "middle"}
                height={tickAngle ? 50 : 30}
              />
              <YAxis
                tick={axisTickStyle(colors)}
                tickFormatter={valueTickFormatter}
                tickLine={false}
                axisLine={axisLineStyle(colors)}
                width={48}
              />
            </>
          ) : (
            <>
              <XAxis
                type="number"
                tick={axisTickStyle(colors)}
                tickFormatter={valueTickFormatter}
                tickLine={false}
                axisLine={axisLineStyle(colors)}
              />
              <YAxis
                type="category"
                dataKey={categoryKey as string}
                tick={axisTickStyle(colors)}
                tickFormatter={formatCategoryTick}
                tickLine={false}
                axisLine={axisLineStyle(colors)}
                width={categoryWidth}
              />
            </>
          )}
          <Tooltip
            contentStyle={tooltipContentStyle(colors)}
            itemStyle={tooltipItemStyle(colors)}
            labelStyle={tooltipLabelStyle(colors)}
            cursor={{ fill: colors.accentSoft }}
          />
          {series.map((s) => (
            <Bar
              key={s.dataKey}
              dataKey={s.dataKey}
              name={s.name}
              fill={toneFill(s.tone)}
              radius={orientation === "vertical" ? [0, 2, 2, 0] : [2, 2, 0, 0]}
              isAnimationActive={false}
            />
          ))}
        </RBarChart>
      </ResponsiveContainer>
    </div>
  );
}
