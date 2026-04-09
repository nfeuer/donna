import { ChartCard, LineChart, type ChartCardStat } from "../../charts";
import type { QualityWarningsData } from "../../api/dashboard";

interface Props {
  data: QualityWarningsData | null;
  loading: boolean;
}

function formatPct(v: number): string {
  return `${v.toFixed(1)}%`;
}

export default function QualityWarningsCard({ data, loading }: Props) {
  const s = data?.summary;

  const stats: ChartCardStat[] = [
    { label: "Warnings", value: (s?.total_warnings ?? 0).toLocaleString() },
    { label: "Criticals", value: (s?.total_criticals ?? 0).toLocaleString() },
    { label: "Total Scored", value: (s?.total_scored ?? 0).toLocaleString() },
    {
      label: "Thresholds",
      value: data?.thresholds
        ? `warn < ${data.thresholds.warning_threshold} · crit < ${data.thresholds.critical_threshold}`
        : "—",
    },
  ];

  return (
    <ChartCard
      eyebrow={`Quality · ${data?.days ?? 30} days`}
      metric={s ? formatPct(s.warning_rate_pct) : "—"}
      chart={
        data?.time_series && data.time_series.length > 0 ? (
          <LineChart
            data={data.time_series}
            series={[
              { dataKey: "warnings", name: "Warnings", tone: "warning" },
              { dataKey: "criticals", name: "Criticals", tone: "error" },
            ]}
            xKey="date"
            formatTick={(v) => v.slice(5)}
            ariaLabel={`Quality warnings trend over ${data.days} days`}
          />
        ) : undefined
      }
      stats={stats}
      loading={loading && !data}
    />
  );
}
