import { AreaChart, ChartCard, type ChartCardStat } from "../../charts";
import { Pill } from "../../primitives/Pill";
import type { ParseAccuracyData } from "../../api/dashboard";

interface Props {
  data: ParseAccuracyData | null;
  loading: boolean;
}

function formatPct(v: number): string {
  return `${v.toFixed(1)}%`;
}

export default function ParseAccuracyCard({ data, loading }: Props) {
  const s = data?.summary;

  const stats: ChartCardStat[] = [
    { label: "Parses", value: (s?.total_parses ?? 0).toLocaleString() },
    { label: "Corrections", value: (s?.total_corrections ?? 0).toLocaleString() },
    { label: "Most Corrected", value: s?.most_corrected_field ?? "—" },
  ];

  const topFields = (data?.field_breakdown ?? []).slice(0, 4);

  return (
    <ChartCard
      eyebrow={`Parse Accuracy · ${data?.days ?? 30} days`}
      metric={s ? formatPct(s.accuracy_pct) : "—"}
      chart={
        data?.time_series && data.time_series.length > 0 ? (
          <AreaChart
            data={data.time_series}
            dataKey="accuracy"
            xKey="date"
            name="Accuracy"
            formatValue={(v) => `${v.toFixed(0)}%`}
            formatTick={(v) => v.slice(5)}
            ariaLabel={`Parse accuracy trend over ${data.days} days`}
          />
        ) : undefined
      }
      stats={stats}
      loading={loading && !data}
    >
      {topFields.length > 0 && (
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "var(--space-2)",
            alignItems: "center",
          }}
        >
          <span
            style={{
              fontSize: "var(--text-eyebrow)",
              letterSpacing: "var(--tracking-eyebrow)",
              textTransform: "uppercase",
              color: "var(--color-text-muted)",
              marginRight: "var(--space-1)",
            }}
          >
            Top Corrected
          </span>
          {topFields.map((f) => (
            <Pill key={f.field} variant="muted">
              {f.field} · {f.count}
            </Pill>
          ))}
        </div>
      )}
    </ChartCard>
  );
}
