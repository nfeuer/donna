import { useState, useCallback, useEffect, useRef } from "react";
import { toast } from "sonner";
import RefreshButton from "../../components/RefreshButton";
import CostAnalyticsCard from "./CostAnalyticsCard";
import ParseAccuracyCard from "./ParseAccuracyCard";
import AgentPerformanceCard from "./AgentPerformanceCard";
import TaskThroughputCard from "./TaskThroughputCard";
import QualityWarningsCard from "./QualityWarningsCard";
import { PageHeader } from "../../primitives/PageHeader";
import { Segmented } from "../../primitives/Segmented";
import { Pill } from "../../primitives/Pill";
import { Tooltip } from "../../primitives/Tooltip";
import {
  fetchCostAnalytics,
  fetchParseAccuracy,
  fetchQualityWarnings,
  fetchTaskThroughput,
  fetchAgentPerformance,
  type CostAnalyticsData,
  type ParseAccuracyData,
  type TaskThroughputData,
  type AgentPerformanceData,
  type QualityWarningsData,
} from "../../api/dashboard";
import { fetchAdminHealth, type AdminHealthData } from "../../api/health";
import styles from "./Dashboard.module.css";

const RANGE_OPTIONS = [
  { label: "7d", value: "7" },
  { label: "14d", value: "14" },
  { label: "30d", value: "30" },
  { label: "90d", value: "90" },
] as const;

type RangeValue = (typeof RANGE_OPTIONS)[number]["value"];

export interface DashboardData {
  cost: CostAnalyticsData | null;
  parse: ParseAccuracyData | null;
  tasks: TaskThroughputData | null;
  agents: AgentPerformanceData | null;
  quality: QualityWarningsData | null;
}

export default function Dashboard() {
  const [range, setRange] = useState<RangeValue>("30");
  const days = Number(range);
  const [health, setHealth] = useState<AdminHealthData | null>(null);
  const [data, setData] = useState<DashboardData>({
    cost: null,
    parse: null,
    tasks: null,
    agents: null,
    quality: null,
  });
  const [loading, setLoading] = useState(true);
  const [entered, setEntered] = useState(false);

  // Anomaly dedup: only fire a toast on state *transition*, not every poll.
  const prevOverdue = useRef<number | null>(null);
  const prevCostAlert = useRef(false);
  const prevParseAlert = useRef(false);
  const prevQualityAlert = useRef(false);

  const fetchAll = useCallback(async (d: number) => {
    setLoading(true);
    try {
      const [cost, parse, tasks, agents, quality] = await Promise.all([
        fetchCostAnalytics(d).catch(() => null),
        fetchParseAccuracy(d).catch(() => null),
        fetchTaskThroughput(d).catch(() => null),
        fetchAgentPerformance(d).catch(() => null),
        fetchQualityWarnings(d).catch(() => null),
      ]);

      setData({ cost, parse, tasks, agents, quality });

      // Deduplicated anomaly toasts — only on threshold crossing.
      if (cost?.summary) {
        const overBudget = cost.summary.today_cost_usd > 16;
        if (overBudget && !prevCostAlert.current) {
          toast.warning("Daily Cost Alert", {
            description: `Today's cost ($${cost.summary.today_cost_usd.toFixed(2)}) exceeds 80% of the $20 daily threshold.`,
            duration: 8000,
          });
        }
        prevCostAlert.current = overBudget;
      }

      if (parse?.summary) {
        const lowAccuracy = parse.summary.accuracy_pct < 85;
        if (lowAccuracy && !prevParseAlert.current) {
          toast.warning("Parse Accuracy Alert", {
            description: `Parse accuracy (${parse.summary.accuracy_pct.toFixed(1)}%) dropped below 85%.`,
            duration: 8000,
          });
        }
        prevParseAlert.current = lowAccuracy;
      }

      if (tasks?.summary) {
        const currentOverdue = tasks.summary.overdue_count;
        if (
          prevOverdue.current !== null &&
          currentOverdue > prevOverdue.current
        ) {
          toast.warning("Overdue Tasks Increased", {
            description: `Overdue tasks increased from ${prevOverdue.current} to ${currentOverdue}.`,
            duration: 8000,
          });
        }
        prevOverdue.current = currentOverdue;
      }

      if (quality?.summary) {
        const highRate = quality.summary.warning_rate_pct > 10;
        if (highRate && !prevQualityAlert.current) {
          toast.warning("Quality Warning Rate High", {
            description: `${quality.summary.warning_rate_pct.toFixed(1)}% of scored invocations are below quality thresholds.`,
            duration: 8000,
          });
        }
        prevQualityAlert.current = highRate;
      }
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshHealth = useCallback(() => {
    fetchAdminHealth().then(setHealth).catch(() => setHealth(null));
  }, []);

  useEffect(() => {
    fetchAll(days);
    refreshHealth();
  }, [days, fetchAll, refreshHealth]);

  // Signature motion: flip data-entered once on first mount inside a
  // requestAnimationFrame so the browser sees the initial false state
  // before the true state arrives, triggering the staggered animation.
  useEffect(() => {
    const raf = requestAnimationFrame(() => setEntered(true));
    return () => cancelAnimationFrame(raf);
  }, []);

  const handleRefresh = useCallback(async () => {
    refreshHealth();
    await fetchAll(days);
  }, [days, fetchAll, refreshHealth]);

  const healthVariant =
    health?.status === "healthy" ? "success" : health ? "warning" : "muted";
  const healthLabel =
    health?.status === "healthy" ? "Healthy" : health ? "Degraded" : "—";
  const healthTooltip = health?.checks
    ? Object.entries(health.checks)
        .map(([k, v]) => `${k}: ${v.ok ? "OK" : (v.detail ?? "down")}`)
        .join(" · ")
    : "System status unknown";

  return (
    <div
      className={styles.page}
      data-entered={entered ? "true" : "false"}
      data-testid="dashboard-root"
    >
      <PageHeader
        eyebrow="Overview"
        title="Dashboard"
        actions={
          <div className={styles.controls}>
            <Segmented
              value={range}
              onValueChange={(v) => setRange(v as RangeValue)}
              options={RANGE_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
              aria-label="Date range"
            />
            {health && (
              <Tooltip content={healthTooltip}>
                <span role="status" aria-label={`System status: ${health.status}`}>
                  <Pill variant={healthVariant}>{healthLabel}</Pill>
                </span>
              </Tooltip>
            )}
            <RefreshButton onRefresh={handleRefresh} autoRefreshMs={30000} />
          </div>
        }
      />

      <div className={styles.grid}>
        <div className={styles.fullWidth}>
          <CostAnalyticsCard data={data.cost} loading={loading} />
        </div>
        <ParseAccuracyCard data={data.parse} loading={loading} />
        <TaskThroughputCard data={data.tasks} loading={loading} />
        <AgentPerformanceCard data={data.agents} loading={loading} />
        <QualityWarningsCard data={data.quality} loading={loading} />
      </div>
    </div>
  );
}
