import { BarChart, ChartCard, type ChartCardStat } from "../../charts";
import type { AgentPerformanceData } from "../../api/dashboard";

interface Props {
  data: AgentPerformanceData | null;
  loading: boolean;
}

function formatMs(v: number): string {
  if (v < 1000) return `${Math.round(v)} ms`;
  return `${(v / 1000).toFixed(1)} s`;
}

function formatUsd(v: number): string {
  return `$${v.toFixed(2)}`;
}

export default function AgentPerformanceCard({ data, loading }: Props) {
  const s = data?.summary;

  const stats: ChartCardStat[] = [
    { label: "Total Calls", value: (s?.total_calls ?? 0).toLocaleString() },
    { label: "P95 Latency", value: s ? formatMs(s.p95_latency_ms) : "—" },
    { label: "Total Cost", value: s ? formatUsd(s.total_cost_usd) : "—" },
  ];

  // Top 6 agents by call volume. Recharts gets cranky with >8 bars.
  const agents = (data?.agents ?? [])
    .slice()
    .sort((a, b) => b.call_count - a.call_count)
    .slice(0, 6);

  return (
    <ChartCard
      eyebrow={`Agent Latency · ${data?.days ?? 30} days`}
      metric={s ? formatMs(s.avg_latency_ms) : "—"}
      chart={
        agents.length > 0 ? (
          <BarChart
            data={agents}
            series={[{ dataKey: "call_count", name: "Calls" }]}
            categoryKey="task_type"
            orientation="vertical"
            categoryWidth={120}
            ariaLabel="Agent call volume by task type"
          />
        ) : undefined
      }
      stats={stats}
      loading={loading && !data}
    />
  );
}
