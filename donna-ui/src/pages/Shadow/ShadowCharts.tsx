import { ChartCard, LineChart, type ChartCardStat } from "../../charts";
import type { ShadowStats } from "../../api/shadow";
import styles from "./Shadow.module.css";

interface Props {
  stats: ShadowStats | null;
  loading: boolean;
}

export default function ShadowCharts({ stats, loading }: Props) {
  const trendData = stats?.trend ?? [];

  const qualityStats: ChartCardStat[] = [
    { label: "Wins", value: stats?.wins ?? 0 },
    { label: "Losses", value: stats?.losses ?? 0 },
    { label: "Ties", value: stats?.ties ?? 0 },
    { label: "Count", value: stats?.primary_count ?? 0 },
  ];

  const saved = stats ? stats.primary_cost - stats.shadow_cost : 0;
  const costStats: ChartCardStat[] = [
    { label: "Primary", value: `$${(stats?.primary_cost ?? 0).toFixed(2)}` },
    { label: "Shadow", value: `$${(stats?.shadow_cost ?? 0).toFixed(2)}` },
    { label: "Comparisons", value: stats?.primary_count ?? 0 },
  ];

  return (
    <div className={styles.chartGrid}>
      <ChartCard
        eyebrow="Quality Δ over time"
        metric={stats?.avg_delta != null ? (stats.avg_delta > 0 ? "+" : "") + stats.avg_delta.toFixed(4) : "—"}
        delta={
          stats?.avg_delta != null
            ? { value: stats.avg_delta * 100, label: "shadow vs primary" }
            : undefined
        }
        chart={
          trendData.length > 0 ? (
            <LineChart
              data={trendData}
              series={[{ dataKey: "avg_quality", name: "Avg Quality" }]}
              xKey="date"
              formatTick={(v) => v.slice(5)}
              formatValue={(v) => v.toFixed(2)}
              ariaLabel="Shadow quality trend over time"
            />
          ) : undefined
        }
        stats={qualityStats}
        loading={loading}
      />
      <ChartCard
        eyebrow="Cost savings"
        metric={`$${Math.abs(saved).toFixed(2)}`}
        metricSuffix={saved >= 0 ? "saved" : "overspend"}
        stats={costStats}
        loading={loading}
      />
    </div>
  );
}
