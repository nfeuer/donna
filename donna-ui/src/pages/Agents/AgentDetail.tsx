import { useState, useEffect } from "react";
import {
  Card,
  Descriptions,
  Table,
  Tag,
  Statistic,
  Row,
  Col,
  Spin,
  Space,
} from "antd";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  BarChart,
  Bar,
} from "recharts";
import dayjs from "dayjs";
import { fetchAgentDetail, type AgentDetail as AgentDetailType } from "../../api/agents";
import { CHART_COLORS } from "../../theme/darkTheme";

interface Props {
  agentName: string;
}

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

  if (loading) {
    return (
      <div style={{ textAlign: "center", padding: 40 }}>
        <Spin />
      </div>
    );
  }

  if (!detail) return <div>Failed to load agent details.</div>;

  return (
    <div>
      {/* Config */}
      <Card size="small" title="Configuration" style={{ marginBottom: 16 }}>
        <Descriptions column={3} size="small">
          <Descriptions.Item label="Enabled">
            <Tag color={detail.enabled ? "green" : "red"}>
              {detail.enabled ? "Yes" : "No"}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Timeout">{detail.timeout_seconds}s</Descriptions.Item>
          <Descriptions.Item label="Autonomy">
            <Tag>{detail.autonomy}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Allowed Tools" span={3}>
            <Space size={4} wrap>
              {detail.allowed_tools.map((t) => (
                <Tag key={t}>{t}</Tag>
              ))}
            </Space>
          </Descriptions.Item>
          <Descriptions.Item label="Task Types" span={3}>
            <Space size={4} wrap>
              {detail.task_types.map((t) => (
                <Tag key={t} color="blue">{t}</Tag>
              ))}
            </Space>
          </Descriptions.Item>
        </Descriptions>
      </Card>

      {/* Cost Summary */}
      <Card size="small" title="Cost Summary" style={{ marginBottom: 16 }}>
        <Row gutter={16}>
          <Col span={8}>
            <Statistic
              title="Total Invocations"
              value={detail.cost_summary.total_calls}
            />
          </Col>
          <Col span={8}>
            <Statistic
              title="Total Cost"
              value={detail.cost_summary.total_cost_usd}
              prefix="$"
              precision={4}
            />
          </Col>
          <Col span={8}>
            <Statistic
              title="Avg Cost / Call"
              value={detail.cost_summary.avg_cost_per_call}
              prefix="$"
              precision={4}
            />
          </Col>
        </Row>
      </Card>

      {/* Latency Chart */}
      {detail.daily_latency.length > 0 && (
        <Card size="small" title="Latency Trend (30d)" style={{ marginBottom: 16 }}>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={detail.daily_latency}>
              <CartesianGrid strokeDasharray="3 3" stroke="#333" />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 11, fill: "#999" }}
                tickFormatter={(d: string) => dayjs(d).format("M/D")}
              />
              <YAxis tick={{ fontSize: 11, fill: "#999" }} />
              <Tooltip
                contentStyle={{ background: "#1f1f1f", border: "1px solid #333" }}
                labelFormatter={(d: string) => dayjs(d).format("MMM D")}
              />
              <Line
                type="monotone"
                dataKey="avg_latency_ms"
                stroke={CHART_COLORS[0]}
                dot={false}
                name="Avg Latency (ms)"
              />
            </LineChart>
          </ResponsiveContainer>
        </Card>
      )}

      {/* Tool Usage */}
      {detail.tool_usage.length > 0 && (
        <Card size="small" title="Tool Usage" style={{ marginBottom: 16 }}>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={detail.tool_usage} layout="vertical">
              <CartesianGrid strokeDasharray="3 3" stroke="#333" />
              <XAxis type="number" tick={{ fontSize: 11, fill: "#999" }} />
              <YAxis
                type="category"
                dataKey="tool"
                tick={{ fontSize: 11, fill: "#999" }}
                width={120}
              />
              <Tooltip contentStyle={{ background: "#1f1f1f", border: "1px solid #333" }} />
              <Bar dataKey="count" fill={CHART_COLORS[1]} name="Calls" />
            </BarChart>
          </ResponsiveContainer>
        </Card>
      )}

      {/* Recent Invocations */}
      <Card size="small" title={`Recent Invocations (${detail.recent_invocations.length})`}>
        <Table
          dataSource={detail.recent_invocations}
          rowKey="id"
          size="small"
          pagination={{ pageSize: 10, size: "small" }}
          columns={[
            {
              title: "Time",
              dataIndex: "timestamp",
              width: 140,
              render: (t: string) => dayjs(t).format("MMM D HH:mm:ss"),
            },
            { title: "Task Type", dataIndex: "task_type", width: 150 },
            { title: "Model", dataIndex: "model_alias", width: 100 },
            {
              title: "Latency",
              dataIndex: "latency_ms",
              width: 80,
              render: (v: number) => `${v}ms`,
            },
            {
              title: "Cost",
              dataIndex: "cost_usd",
              width: 80,
              render: (v: number) => `$${v.toFixed(4)}`,
            },
            {
              title: "Shadow",
              dataIndex: "is_shadow",
              width: 70,
              render: (v: boolean) => (v ? <Tag color="purple">Yes</Tag> : "No"),
            },
          ]}
        />
      </Card>
    </div>
  );
}
