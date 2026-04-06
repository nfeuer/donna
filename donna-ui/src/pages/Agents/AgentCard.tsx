import { Card, Tag, Badge, Space, Statistic, Row, Col } from "antd";
import {
  ClockCircleOutlined,
  ThunderboltOutlined,
  DollarOutlined,
} from "@ant-design/icons";
import type { AgentSummary } from "../../api/agents";

const AUTONOMY_COLORS: Record<string, string> = {
  low: "orange",
  medium: "blue",
  high: "green",
};

interface Props {
  agent: AgentSummary;
  selected: boolean;
  onClick: () => void;
}

export default function AgentCard({ agent, selected, onClick }: Props) {
  return (
    <Badge.Ribbon
      text={agent.enabled ? "Active" : "Disabled"}
      color={agent.enabled ? "green" : "red"}
    >
      <Card
        hoverable
        size="small"
        onClick={onClick}
        style={{
          border: selected ? "1px solid #1890ff" : undefined,
          opacity: agent.enabled ? 1 : 0.7,
        }}
      >
        <div style={{ marginBottom: 8 }}>
          <span style={{ fontSize: 16, fontWeight: 600, textTransform: "capitalize" }}>
            {agent.name}
          </span>
          <Tag color={AUTONOMY_COLORS[agent.autonomy]} style={{ marginLeft: 8 }}>
            {agent.autonomy}
          </Tag>
        </div>

        <Space size={4} wrap style={{ marginBottom: 8 }}>
          {agent.allowed_tools.map((t) => (
            <Tag key={t} style={{ fontSize: 11 }}>{t}</Tag>
          ))}
        </Space>

        <Row gutter={8}>
          <Col span={8}>
            <Statistic
              title="Calls"
              value={agent.total_calls}
              prefix={<ThunderboltOutlined />}
              valueStyle={{ fontSize: 14 }}
            />
          </Col>
          <Col span={8}>
            <Statistic
              title="Avg Latency"
              value={agent.avg_latency_ms}
              suffix="ms"
              prefix={<ClockCircleOutlined />}
              valueStyle={{ fontSize: 14 }}
            />
          </Col>
          <Col span={8}>
            <Statistic
              title="Cost"
              value={agent.total_cost_usd}
              prefix={<DollarOutlined />}
              precision={4}
              valueStyle={{ fontSize: 14 }}
            />
          </Col>
        </Row>
      </Card>
    </Badge.Ribbon>
  );
}
