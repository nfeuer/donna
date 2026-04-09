// donna-ui/src/pages/Agents/AgentDetail.tsx
import { useState, useEffect, useMemo } from "react";
import dayjs from "dayjs";
import { Card } from "../../primitives/Card";
import { Pill } from "../../primitives/Pill";
import { Stat } from "../../primitives/Stat";
import { Skeleton } from "../../primitives/Skeleton";
import { DataTable } from "../../primitives/DataTable";
import { LineChart, BarChart } from "../../charts";
import type { ColumnDef } from "@tanstack/react-table";
import {
  fetchAgentDetail,
  type AgentDetail as AgentDetailType,
  type AgentInvocation,
} from "../../api/agents";
import styles from "./AgentDetail.module.css";

interface Props {
  agentName: string;
}

const invocationColumns: ColumnDef<AgentInvocation>[] = [
  {
    accessorKey: "timestamp",
    header: "Time",
    size: 140,
    cell: ({ getValue }) => dayjs(getValue<string>()).format("MMM D HH:mm:ss"),
  },
  { accessorKey: "task_type", header: "Task Type", size: 150 },
  { accessorKey: "model_alias", header: "Model", size: 100 },
  {
    accessorKey: "latency_ms",
    header: "Latency",
    size: 80,
    cell: ({ getValue }) => `${getValue<number>()}ms`,
  },
  {
    accessorKey: "cost_usd",
    header: "Cost",
    size: 80,
    cell: ({ getValue }) => `$${getValue<number>().toFixed(4)}`,
  },
  {
    accessorKey: "is_shadow",
    header: "Shadow",
    size: 70,
    cell: ({ getValue }) =>
      getValue<boolean>() ? <Pill variant="accent">Yes</Pill> : "No",
  },
];

export default function AgentDetail({ agentName }: Props) {
  const [detail, setDetail] = useState<AgentDetailType | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchAgentDetail(agentName)
      .then(setDetail)
      .catch(() => setDetail(null))
      .finally(() => setLoading(false));
  }, [agentName]);

  const formatTick = useMemo(
    () => (d: string) => dayjs(d).format("M/D"),
    [],
  );

  if (loading) {
    return (
      <div className={styles.loading}>
        <Card><Skeleton height={120} /></Card>
        <Card><Skeleton height={80} /></Card>
        <Card><Skeleton height={220} /></Card>
      </div>
    );
  }

  if (!detail) return <p className={styles.error}>Failed to load agent details.</p>;

  return (
    <div className={styles.root}>
      {/* Configuration */}
      <Card>
        <h2 className={styles.sectionTitle}>Configuration</h2>
        <div className={styles.configGrid}>
          <div className={styles.configItem}>
            <span className={styles.configLabel}>Status</span>
            <span className={styles.configValue}>
              <Pill variant={detail.enabled ? "success" : "error"}>
                {detail.enabled ? "Active" : "Disabled"}
              </Pill>
            </span>
          </div>
          <div className={styles.configItem}>
            <span className={styles.configLabel}>Timeout</span>
            <span className={styles.configValue}>{detail.timeout_seconds}s</span>
          </div>
          <div className={styles.configItem}>
            <span className={styles.configLabel}>Autonomy</span>
            <span className={styles.configValue}>
              <Pill variant="muted">{detail.autonomy}</Pill>
            </span>
          </div>
          <div className={styles.configItem}>
            <span className={styles.configLabel}>Allowed Tools</span>
            <div className={styles.pillList}>
              {detail.allowed_tools.map((t) => (
                <Pill key={t} variant="muted">{t}</Pill>
              ))}
            </div>
          </div>
          <div className={styles.configItem}>
            <span className={styles.configLabel}>Task Types</span>
            <div className={styles.pillList}>
              {detail.task_types.map((t) => (
                <Pill key={t} variant="accent">{t}</Pill>
              ))}
            </div>
          </div>
        </div>
      </Card>

      {/* Cost Summary */}
      <Card>
        <h2 className={styles.sectionTitle}>Cost Summary</h2>
        <div className={styles.statStrip}>
          <Stat
            eyebrow="Total Invocations"
            value={detail.cost_summary.total_calls.toLocaleString()}
          />
          <Stat
            eyebrow="Total Cost"
            value={`$${detail.cost_summary.total_cost_usd.toFixed(4)}`}
          />
          <Stat
            eyebrow="Avg Cost / Call"
            value={`$${detail.cost_summary.avg_cost_per_call.toFixed(4)}`}
          />
        </div>
      </Card>

      {/* Latency Trend */}
      {detail.daily_latency.length > 0 && (
        <Card>
          <h2 className={styles.sectionTitle}>Latency Trend (30d)</h2>
          <div className={styles.chartSection}>
            <LineChart
              data={detail.daily_latency}
              series={[{ dataKey: "avg_latency_ms", name: "Avg Latency (ms)" }]}
              xKey="date"
              formatTick={formatTick}
              ariaLabel="Agent latency over 30 days"
            />
          </div>
        </Card>
      )}

      {/* Tool Usage */}
      {detail.tool_usage.length > 0 && (
        <Card>
          <h2 className={styles.sectionTitle}>Tool Usage</h2>
          <div className={styles.chartSection}>
            <BarChart
              data={detail.tool_usage}
              series={[{ dataKey: "count", name: "Calls" }]}
              categoryKey="tool"
              orientation="vertical"
              categoryWidth={120}
              ariaLabel="Tool usage counts"
            />
          </div>
        </Card>
      )}

      {/* Recent Invocations */}
      <Card>
        <h2 className={styles.sectionTitle}>
          Recent Invocations ({detail.recent_invocations.length})
        </h2>
        <DataTable
          data={detail.recent_invocations}
          columns={invocationColumns}
          getRowId={(row) => row.id}
          pageSize={10}
        />
      </Card>
    </div>
  );
}
