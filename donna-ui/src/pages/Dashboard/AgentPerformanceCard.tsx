import { Card, Statistic, Row, Col, Table, Tag, Skeleton } from "antd";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import type { AgentPerformanceData } from "../../api/dashboard";
import {
  CHART_COLORS,
  CHART_TOOLTIP_STYLE,
  CHART_GRID_STROKE,
  CHART_TICK,
} from "../../theme/darkTheme";

interface Props {
  data: AgentPerformanceData | null;
  loading: boolean;
}

const latencyColor = (ms: number) =>
  ms < 500 ? "#52c41a" : ms < 2000 ? "#faad14" : "#ff4d4f";

export default function AgentPerformanceCard({ data, loading }: Props) {
  if (loading && !data) {
    return (
      <Card title="Agent Performance" size="small" styles={{ body: { padding: "12px 16px" } }}>
        <Skeleton active paragraph={{ rows: 6 }} />
      </Card>
    );
  }

  const s = data?.summary;

  return (
    <Card
      title="Agent Performance"
      size="small"
      styles={{ body: { padding: "12px 16px" } }}
    >
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col xs={12} sm={6}>
          <Statistic
            title="Total Calls"
            value={s?.total_calls ?? 0}
            valueStyle={{ fontSize: 22 }}
          />
        </Col>
        <Col xs={12} sm={6}>
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
        <Col xs={12} sm={6}>
          <Statistic
            title="P95 Latency"
            value={s?.p95_latency_ms ?? 0}
            suffix="ms"
            valueStyle={{ fontSize: 22 }}
          />
        </Col>
        <Col xs={12} sm={6}>
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
        <div role="img" aria-label="Agent call volume by task type">
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={data.agents}>
              <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID_STROKE} />
              <XAxis
                dataKey="task_type"
                tick={CHART_TICK}
                interval={0}
                angle={-20}
                textAnchor="end"
                height={50}
              />
              <YAxis tick={CHART_TICK} />
              <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
              <Bar
                dataKey="call_count"
                fill={CHART_COLORS[0]}
                name="Calls"
                radius={[4, 4, 0, 0]}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {data?.agents && data.agents.length > 0 && (
        <Table
          size="small"
          dataSource={data.agents}
          rowKey="task_type"
          pagination={false}
          style={{ marginTop: 12 }}
          scroll={{ x: 420 }}
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
    </Card>
  );
}
