import { useState, useCallback, useRef } from "react";
import { Row, Col, Segmented, Space, notification } from "antd";
import RefreshButton from "../../components/RefreshButton";
import ParseAccuracyCard from "./ParseAccuracyCard";
import AgentPerformanceCard from "./AgentPerformanceCard";
import TaskThroughputCard from "./TaskThroughputCard";
import CostAnalyticsCard from "./CostAnalyticsCard";
import {
  fetchCostAnalytics,
  fetchParseAccuracy,
  fetchTaskThroughput,
} from "../../api/dashboard";

const RANGE_OPTIONS = [
  { label: "7d", value: 7 },
  { label: "14d", value: 14 },
  { label: "30d", value: 30 },
  { label: "90d", value: 90 },
];

export default function Dashboard() {
  const [days, setDays] = useState(30);
  const [refreshKey, setRefreshKey] = useState(0);
  const prevOverdue = useRef<number | null>(null);

  const checkAnomalies = useCallback(async (d: number) => {
    try {
      const [cost, parse, tasks] = await Promise.all([
        fetchCostAnalytics(d),
        fetchParseAccuracy(d),
        fetchTaskThroughput(d),
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
    } catch {
      // Silent — anomaly checks are best-effort
    }
  }, []);

  const handleRefresh = useCallback(async () => {
    setRefreshKey((k) => k + 1);
    await checkAnomalies(days);
  }, [days, checkAnomalies]);

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
      </Row>
    </div>
  );
}
