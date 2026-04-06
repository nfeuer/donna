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
} from "../../api/dashboard";
import { fetchAdminHealth, type AdminHealthData } from "../../api/health";

const RANGE_OPTIONS = [
  { label: "7d", value: 7 },
  { label: "14d", value: 14 },
  { label: "30d", value: 30 },
  { label: "90d", value: 90 },
];

export default function Dashboard() {
  const [days, setDays] = useState(30);
  const [refreshKey, setRefreshKey] = useState(0);
  const [health, setHealth] = useState<AdminHealthData | null>(null);
  const prevOverdue = useRef<number | null>(null);

  const refreshHealth = useCallback(() => {
    fetchAdminHealth().then(setHealth).catch(() => setHealth(null));
  }, []);

  useEffect(() => {
    refreshHealth();
  }, [refreshHealth]);

  const checkAnomalies = useCallback(async (d: number) => {
    try {
      const [cost, parse, tasks, qw] = await Promise.all([
        fetchCostAnalytics(d),
        fetchParseAccuracy(d),
        fetchTaskThroughput(d),
        fetchQualityWarnings(d),
      ]);

      // Daily cost > 80% of $20 threshold
      if (cost.summary.today_cost_usd > 16) {
        notification.warning({
          message: "Daily Cost Alert",
          description: `Today's cost ($${cost.summary.today_cost_usd.toFixed(2)}) exceeds 80% of the $20 daily threshold.`,
          duration: 8,
        });
      }

      // Parse accuracy < 85%
      if (parse.summary.accuracy_pct < 85) {
        notification.warning({
          message: "Parse Accuracy Alert",
          description: `Parse accuracy (${parse.summary.accuracy_pct.toFixed(1)}%) dropped below 85%.`,
          duration: 8,
        });
      }

      // Overdue count increased
      const currentOverdue = tasks.summary.overdue_count;
      if (prevOverdue.current !== null && currentOverdue > prevOverdue.current) {
        notification.warning({
          message: "Overdue Tasks Increased",
          description: `Overdue tasks increased from ${prevOverdue.current} to ${currentOverdue}.`,
          duration: 8,
        });
      }
      prevOverdue.current = currentOverdue;

      // Quality warning rate > 10%
      if (qw.summary.warning_rate_pct > 10) {
        notification.warning({
          message: "Quality Warning Rate High",
          description: `${qw.summary.warning_rate_pct.toFixed(1)}% of scored invocations are below quality thresholds.`,
          duration: 8,
        });
      }
    } catch {
      // Silent — anomaly checks are best-effort
    }
  }, []);

  const handleRefresh = useCallback(async () => {
    setRefreshKey((k) => k + 1);
    refreshHealth();
    await checkAnomalies(days);
  }, [days, checkAnomalies, refreshHealth]);

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
              <Badge
                status={health.status === "healthy" ? "success" : "warning"}
                text={health.status === "healthy" ? "Healthy" : "Degraded"}
                style={{ marginLeft: 8, fontSize: 12, color: "#8c8c8c" }}
              />
            </Tooltip>
          )}
        </Space>
        <RefreshButton onRefresh={handleRefresh} autoRefreshMs={30000} />
      </div>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <ParseAccuracyCard days={days} refreshKey={refreshKey} />
        </Col>
        <Col xs={24} lg={12}>
          <AgentPerformanceCard days={days} refreshKey={refreshKey} />
        </Col>
        <Col xs={24} lg={12}>
          <TaskThroughputCard days={days} refreshKey={refreshKey} />
        </Col>
        <Col xs={24} lg={12}>
          <CostAnalyticsCard days={days} refreshKey={refreshKey} />
        </Col>
        <Col xs={24} lg={12}>
          <QualityWarningsCard days={days} refreshKey={refreshKey} />
        </Col>
      </Row>
    </div>
  );
}
