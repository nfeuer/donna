import { ChartCard, BarChart, type ChartCardStat } from "../../charts";
import type { SkillSystemData } from "../../api/dashboard";

interface Props {
  data: SkillSystemData | null;
  loading: boolean;
}

function formatPct(v: number | null): string {
  return v === null ? "—" : `${v.toFixed(1)}%`;
}

const STATE_ORDER = [
  "claude_native",
  "skill_candidate",
  "draft",
  "sandbox",
  "shadow_primary",
  "trusted",
  "flagged_for_review",
  "degraded",
] as const;

function stateLabel(state: string): string {
  return state.replace(/_/g, " ");
}

export default function SkillSystemCard({ data, loading }: Props) {
  const s = data?.summary;

  const byState = data?.by_state ?? {};
  const chartData = STATE_ORDER.filter((state) => (byState[state] ?? 0) > 0).map(
    (state) => ({ state: stateLabel(state), count: byState[state] ?? 0 }),
  );

  const stats: ChartCardStat[] = [
    {
      label: "New Candidates 24h",
      value: (s?.new_candidates_24h ?? 0).toLocaleString(),
    },
    {
      label: "Evolution 24h",
      value: formatPct(s?.evolution_success_rate_24h ?? null),
    },
    {
      label: "Active Automations",
      value: (s?.active_automations ?? 0).toLocaleString(),
    },
    {
      label: "Automation Fails 24h",
      value: `${(s?.automation_failures_24h ?? 0).toLocaleString()} (${formatPct(
        s?.automation_failure_rate_pct ?? 0,
      )})`,
    },
  ];

  return (
    <ChartCard
      eyebrow="Skill System"
      metric={(s?.total_skills ?? 0).toLocaleString()}
      chart={
        chartData.length > 0 ? (
          <BarChart
            data={chartData}
            series={[{ dataKey: "count", name: "Skills" }]}
            categoryKey="state"
            orientation="vertical"
            categoryWidth={140}
            ariaLabel="Skill count by lifecycle state"
          />
        ) : undefined
      }
      stats={stats}
      loading={loading && !data}
    />
  );
}
