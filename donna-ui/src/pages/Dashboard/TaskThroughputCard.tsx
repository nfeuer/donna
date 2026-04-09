import { BarChart, ChartCard, type ChartCardStat } from "../../charts";
import { Pill, type PillVariant } from "../../primitives/Pill";
import type { TaskThroughputData } from "../../api/dashboard";

interface Props {
  data: TaskThroughputData | null;
  loading: boolean;
}

function formatPct(v: number): string {
  return `${v.toFixed(0)}%`;
}

/** Status → Pill variant. No rainbow — only meaningful semantics. */
function statusVariant(status: string): PillVariant {
  const normalized = status.toLowerCase();
  if (normalized.includes("done") || normalized.includes("complete")) return "success";
  if (normalized.includes("overdue") || normalized.includes("block")) return "error";
  if (normalized.includes("progress") || normalized.includes("doing")) return "accent";
  return "muted";
}

export default function TaskThroughputCard({ data, loading }: Props) {
  const s = data?.summary;

  const stats: ChartCardStat[] = [
    { label: "Created", value: (s?.total_created ?? 0).toLocaleString() },
    { label: "Completed", value: (s?.total_completed ?? 0).toLocaleString() },
    {
      label: "Overdue",
      value: s && s.overdue_count > 0 ? `${s.overdue_count}` : "0",
    },
    {
      label: "Avg Hours",
      value: s?.avg_completion_hours != null ? s.avg_completion_hours.toFixed(1) : "—",
    },
  ];

  const statusEntries = Object.entries(data?.status_distribution ?? {});

  return (
    <ChartCard
      eyebrow={`Task Throughput · ${data?.days ?? 30} days`}
      metric={s ? formatPct(s.completion_rate) : "—"}
      chart={
        data?.time_series && data.time_series.length > 0 ? (
          <BarChart
            data={data.time_series}
            series={[
              { dataKey: "created", name: "Created" },
              { dataKey: "completed", name: "Completed", tone: "accentSoft" },
            ]}
            categoryKey="date"
            orientation="horizontal"
            formatCategoryTick={(v) => v.slice(5)}
            ariaLabel={`Task creation and completion trend over ${data.days} days`}
          />
        ) : undefined
      }
      stats={stats}
      loading={loading && !data}
    >
      {statusEntries.length > 0 && (
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
            Status
          </span>
          {statusEntries.map(([name, count]) => (
            <Pill key={name} variant={statusVariant(name)}>
              {name} · {count}
            </Pill>
          ))}
        </div>
      )}
    </ChartCard>
  );
}
