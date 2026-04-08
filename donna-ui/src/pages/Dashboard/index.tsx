import { useState, useCallback, useEffect, useRef } from "react";
import { Row, Col, Segmented, Space, Badge, Tooltip, notification } from "antd";
import RefreshButton from "../../components/RefreshButton";
import ParseAccuracyCard from "./ParseAccuracyCard";
import AgentPerformanceCard from "./AgentPerformanceCard";
import TaskThroughputCard from "./TaskThroughputCard";
import CostAnalyticsCard from "./CostAnalyticsCard";
import QualityWarningsCard from "./QualityWarningsCard";
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
import { SECONDARY_TEXT_COLOR } from "../../theme/darkTheme";

const RANGE_OPTIONS = [
  { label: "7d", value: 7 },
  { label: "14d", value: 14 },
  { label: "30d", value: 30 },
  { label: "90d", value: 90 },
];

export interface DashboardData {
  cost: CostAnalyticsData | null;
  parse: ParseAccuracyData | null;
  tasks: TaskThroughputData | null;
  agents: AgentPerformanceData | null;
  quality: QualityWarningsData | null;
}

export default function Dashboard() {
  const [days, setDays] = useState(30);
  const [health, setHealth] = useState<AdminHealthData | null>(null);
  const [data, setData] = useState<DashboardData>({
    cost: null,
    parse: null,
    tasks: null,
    agents: null,
    quality: null,
  });
  const [loading, setLoading] = useState(true);

  // Anomaly dedup: only fire notification on state *transition*, not every poll
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

      // Deduplicated anomaly notifications — only on threshold crossing
      if (cost) {
        const overBudget = cost.summary.today_cost_usd > 16;
        if (overBudget && !prevCostAlert.current) {
          notification.warning({
            message: "Daily Cost Alert",
            description: `Today's cost ($${cost.summary.today_cost_usd.toFixed(2)}) exceeds 80% of the $20 daily threshold.`,
            duration: 8,
          });
        }
        prevCostAlert.current = overBudget;
      }

      if (parse) {
        const lowAccuracy = parse.summary.accuracy_pct < 85;
        if (lowAccuracy && !prevParseAlert.current) {
          notification.warning({
            message: "Parse Accuracy Alert",
            description: `Parse accuracy (${parse.summary.accuracy_pct.toFixed(1)}%) dropped below 85%.`,
            duration: 8,
          });
        }
        prevParseAlert.current = lowAccuracy;
      }

      if (tasks) {
        const currentOverdue = tasks.summary.overdue_count;
        if (
          prevOverdue.current !== null &&
          currentOverdue > prevOverdue.current
        ) {
          notification.warning({
            message: "Overdue Tasks Increased",
            description: `Overdue tasks increased from ${prevOverdue.current} to ${currentOverdue}.`,
            duration: 8,
          });
        }
        prevOverdue.current = currentOverdue;
      }

      if (quality) {
        const highRate = quality.summary.warning_rate_pct > 10;
        if (highRate && !prevQualityAlert.current) {
          notification.warning({
            message: "Quality Warning Rate High",
            description: `${quality.summary.warning_rate_pct.toFixed(1)}% of scored invocations are below quality thresholds.`,
            duration: 8,
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

  const handleRefresh = useCallback(async () => {
    refreshHealth();
    await fetchAll(days);
  }, [days, fetchAll, refreshHealth]);

  return (
    <div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 20,
        }}
      >
        <Space>
          <Segmented
            options={RANGE_OPTIONS}
            value={days}
            onChange={(v) => setDays(v as number)}
            aria-label="Date range"
          />
          {health && (
            <Tooltip
              title={Object.entries(health.checks)
                .map(
                  ([k, v]) =>
                    `${k}: ${v.ok ? "OK" : v.detail ?? "down"}`,
                )
                .join(" | ")}
            >
              <span role="status" aria-label={`System status: ${health.status}`}>
                <Badge
                  status={health.status === "healthy" ? "success" : "warning"}
                  text={health.status === "healthy" ? "Healthy" : "Degraded"}
                  style={{ marginLeft: 8, fontSize: 12, color: SECONDARY_TEXT_COLOR }}
                />
              </span>
            </Tooltip>
          )}
        </Space>
        <RefreshButton onRefresh={handleRefresh} autoRefreshMs={30000} />
      </div>

      <Row gutter={[16, 16]}>
        {/* Budget is the top constraint — give it full width for prominence */}
        <Col xs={24}>
          <CostAnalyticsCard data={data.cost} loading={loading} />
        </Col>
        <Col xs={24} lg={12}>
          <ParseAccuracyCard data={data.parse} loading={loading} />
        </Col>
        <Col xs={24} lg={12}>
          <TaskThroughputCard data={data.tasks} loading={loading} />
        </Col>
        <Col xs={24} lg={12}>
          <AgentPerformanceCard data={data.agents} loading={loading} />
        </Col>
        <Col xs={24} lg={12}>
          <QualityWarningsCard data={data.quality} loading={loading} />
        </Col>
      </Row>
    </div>
  );
}
