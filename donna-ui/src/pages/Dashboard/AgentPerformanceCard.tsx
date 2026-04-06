import { useEffect, useState } from "react";
import { Card, Statistic, Row, Col, Table, Tag, Spin } from "antd";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import {
  fetchAgentPerformance,
  type AgentPerformanceData,
} from "../../api/dashboard";
import { CHART_COLORS } from "../../theme/darkTheme";

interface Props {
  days: number;
  refreshKey: number;
}

export default function AgentPerformanceCard({ days, refreshKey }: Props) {
  const [data, setData] = useState<AgentPerformanceData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchAgentPerformance(days)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [days, refreshKey]);

  const latencyColor = (ms: number) =>
    ms < 500 ? "#52c41a" : ms < 2000 ? "#faad14" : "#ff4d4f";

  const s = data?.summary;

  return (
    <Card
      title="Agent Performance"
      size="small"
      styles={{ body: { padding: "12px 16px" } }}
    >
      <Spin spinning={loading}>
        <Row gutter={16} style={{ marginBottom: 16 }}>
          <Col span={6}>
            <Statistic
              title="Total Calls"
              value={s?.total_calls ?? 0}
              valueStyle={{ fontSize: 22 }}
            />
          </Col>
          <Col span={6}>
            <Statistic
              title="Avg Latency"
              value={s?.avg_latency_ms ?? 0}
              suffix="ms"
              valueStyle={{
                fontSize: 22,
                color: s ? latencyColor(s.avg_latency_ms) : undefined,
              }}
            />
          </Col>
          <Col span={6}>
            <Statistic
              title="P95 Latency"
              value={s?.p95_latency_ms ?? 0}
              suffix="ms"
              valueStyle={{ fontSize: 22 }}
            />
          </Col>
          <Col span={6}>
            <Statistic
              title="Total Cost"
              value={s?.total_cost_usd ?? 0}
              prefix="$"
              precision={2}
              valueStyle={{ fontSize: 22 }}
            />
          </Col>
        </Row>

        {data?.agents && data.agents.length > 0 && (
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={data.agents}>
              <CartesianGrid strokeDasharray="3 3" stroke="#303030" />
              <XAxis
                dataKey="task_type"
                tick={{ fill: "#8c8c8c", fontSize: 10 }}
                interval={0}
                angle={-20}
                textAnchor="end"
                height={50}
              />
              <YAxis tick={{ fill: "#8c8c8c", fontSize: 10 }} />
              <Tooltip
                contentStyle={{
                  background: "#1f1f1f",
                  border: "1px solid #303030",
                }}
              />
              <Bar
                dataKey="call_count"
                fill={CHART_COLORS[0]}
                name="Calls"
                radius={[4, 4, 0, 0]}
              />
            </BarChart>
          </ResponsiveContainer>
        )}

        {data?.agents && data.agents.length > 0 && (
          <Table
            size="small"
            dataSource={data.agents}
            rowKey="task_type"
            pagination={false}
            style={{ marginTop: 12 }}
            scroll={{ x: true }}
            columns={[
              {
                title: "Task Type",
                dataIndex: "task_type",
                render: (v: string) => <Tag color="blue">{v}</Tag>,
              },
              { title: "Calls", dataIndex: "call_count", width: 70 },
              {
                title: "Avg Latency",
                dataIndex: "avg_latency_ms",
                width: 100,
                render: (v: number) => (
                  <span style={{ color: latencyColor(v) }}>{v}ms</span>
                ),
              },
              {
                title: "Cost",
                dataIndex: "total_cost_usd",
                width: 80,
                render: (v: number) => `$${v.toFixed(3)}`,
              },
              {
                title: "Quality",
                dataIndex: "avg_quality_score",
                width: 70,
                render: (v: number | null) =>
                  v !== null ? v.toFixed(2) : "—",
              },
            ]}
          />
        )}
      </Spin>
    </Card>
  );
}
