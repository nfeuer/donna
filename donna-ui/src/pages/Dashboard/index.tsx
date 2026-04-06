import { useState, useCallback } from "react";
import { Row, Col, Segmented, Space } from "antd";
import RefreshButton from "../../components/RefreshButton";
import ParseAccuracyCard from "./ParseAccuracyCard";
import AgentPerformanceCard from "./AgentPerformanceCard";
import TaskThroughputCard from "./TaskThroughputCard";
import CostAnalyticsCard from "./CostAnalyticsCard";

const RANGE_OPTIONS = [
  { label: "7d", value: 7 },
  { label: "14d", value: 14 },
  { label: "30d", value: 30 },
  { label: "90d", value: 90 },
];

export default function Dashboard() {
  const [days, setDays] = useState(30);
  const [refreshKey, setRefreshKey] = useState(0);

  const handleRefresh = useCallback(async () => {
    setRefreshKey((k) => k + 1);
  }, []);

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
