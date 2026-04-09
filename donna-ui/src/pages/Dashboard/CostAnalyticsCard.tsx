import {
  AreaChart,
  BarChart,
  ChartCard,
  type ChartCardStat,
} from "../../charts";
import type { CostAnalyticsData } from "../../api/dashboard";

interface Props {
  data: CostAnalyticsData | null;
  loading: boolean;
}

function formatUsd(v: number, precision = 2): string {
  return `$${v.toFixed(precision)}`;
}

/** Compute day-over-day delta % from the last two time-series points. */
function computeDelta(data: CostAnalyticsData | null): number | null {
  const series = data?.time_series;
  if (!series || series.length < 2) return null;
  const last = series[series.length - 1].cost_usd;
  const prev = series[series.length - 2].cost_usd;
  if (prev === 0) return null;
  return ((last - prev) / prev) * 100;
}

/** Tonal progress bar — width % fills with accent, rest is accent-soft track. */
function BudgetBar({ pct }: { pct: number }) {
  const clamped = Math.min(Math.max(pct, 0), 100);
  return (
    <div
      style={{
        height: 6,
        width: "100%",
        background: "var(--color-accent-soft)",
        borderRadius: 2,
        overflow: "hidden",
      }}
      role="progressbar"
      aria-valuenow={clamped}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label="Monthly budget utilization"
    >
      <div
        style={{
          height: "100%",
          width: `${clamped}%`,
          background: "var(--color-accent)",
          transition: "width var(--duration-base) var(--ease-out)",
        }}
      />
    </div>
  );
}

export default function CostAnalyticsCard({ data, loading }: Props) {
  const s = data?.summary;
  const delta = computeDelta(data);

  const stats: ChartCardStat[] = [
    { label: "Today", value: s ? formatUsd(s.today_cost_usd, 3) : "—" },
    { label: "MTD", value: s ? formatUsd(s.monthly_cost_usd) : "—" },
    { label: "Projected", value: s ? formatUsd(s.projected_monthly_usd) : "—" },
    { label: "Remaining", value: s ? formatUsd(s.monthly_remaining_usd) : "—" },
  ];

  const byTaskType = (data?.by_task_type ?? []).slice(0, 6);
  const byModel = data?.by_model ?? [];

  return (
    <ChartCard
      eyebrow="Budget · Today"
      metric={s ? formatUsd(s.today_cost_usd, 3) : "—"}
      delta={
        delta != null
          ? { value: Math.round(delta), label: "vs yesterday" }
          : undefined
      }
      chart={
        data?.time_series && data.time_series.length > 0 ? (
          <AreaChart
            data={data.time_series}
            dataKey="cost_usd"
            xKey="date"
            name="Daily Cost"
            formatValue={(v) => `$${v.toFixed(2)}`}
            formatTick={(v) => v.slice(5)}
            referenceLine={
              s
                ? {
                    y: s.daily_budget_usd,
                    label: `$${s.daily_budget_usd}/day`,
                    tone: "warning",
                  }
                : undefined
            }
            ariaLabel={`Daily cost trend over ${data?.days ?? 30} days`}
          />
        ) : undefined
      }
      stats={stats}
      loading={loading && !data}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
        {s && (
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                fontSize: "var(--text-label)",
                color: "var(--color-text-muted)",
                fontFamily: "var(--font-mono)",
              }}
            >
              <span>
                Monthly Budget {formatUsd(s.monthly_cost_usd)} / {formatUsd(s.monthly_budget_usd)}
              </span>
              <span>{s.monthly_utilization_pct.toFixed(1)}%</span>
            </div>
            <BudgetBar pct={s.monthly_utilization_pct} />
          </div>
        )}

        {(byTaskType.length > 0 || byModel.length > 0) && (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
              gap: "var(--space-4)",
            }}
          >
            {byTaskType.length > 0 && (
              <div>
                <div
                  style={{
                    fontSize: "var(--text-eyebrow)",
                    letterSpacing: "var(--tracking-eyebrow)",
                    textTransform: "uppercase",
                    color: "var(--color-text-muted)",
                    marginBottom: "var(--space-2)",
                  }}
                >
                  By Task Type
                </div>
                <BarChart
                  data={byTaskType}
                  series={[{ dataKey: "cost_usd", name: "Cost" }]}
                  categoryKey="task_type"
                  orientation="vertical"
                  categoryWidth={110}
                  height={130}
                  formatValue={(v) => `$${v.toFixed(0)}`}
                  ariaLabel="Cost breakdown by task type"
                />
              </div>
            )}
            {byModel.length > 0 && (
              <div>
                <div
                  style={{
                    fontSize: "var(--text-eyebrow)",
                    letterSpacing: "var(--tracking-eyebrow)",
                    textTransform: "uppercase",
                    color: "var(--color-text-muted)",
                    marginBottom: "var(--space-2)",
                  }}
                >
                  By Model
                </div>
                <BarChart
                  data={byModel}
                  series={[{ dataKey: "cost_usd", name: "Cost", tone: "accentSoft" }]}
                  categoryKey="model"
                  orientation="vertical"
                  categoryWidth={90}
                  height={130}
                  formatValue={(v) => `$${v.toFixed(0)}`}
                  ariaLabel="Cost breakdown by model"
                />
              </div>
            )}
          </div>
        )}
      </div>
    </ChartCard>
  );
}
