import { Table, Tag, Empty, Row, Col, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { CHART_COLORS } from "../../theme/darkTheme";
import type { ShadowComparison } from "../../api/shadow";

const { Text } = Typography;

interface Props {
  comparisons: ShadowComparison[];
  loading: boolean;
}

function DiffView({ primary, shadow }: { primary: Record<string, unknown> | null; shadow: Record<string, unknown> | null }) {
  const pStr = primary ? JSON.stringify(primary, null, 2) : "(no output)";
  const sStr = shadow ? JSON.stringify(shadow, null, 2) : "(no output)";

  return (
    <Row gutter={16} style={{ padding: "8px 0" }}>
      <Col span={12}>
        <Text type="secondary" style={{ fontSize: 11, marginBottom: 4, display: "block" }}>
          Primary Output
        </Text>
        <pre style={{
          background: "#141414",
          padding: 12,
          borderRadius: 6,
          fontSize: 11,
          maxHeight: 300,
          overflow: "auto",
          border: "1px solid #303030",
          margin: 0,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
        }}>
          {pStr}
        </pre>
      </Col>
      <Col span={12}>
        <Text type="secondary" style={{ fontSize: 11, marginBottom: 4, display: "block" }}>
          Shadow Output
        </Text>
        <pre style={{
          background: "#141414",
          padding: 12,
          borderRadius: 6,
          fontSize: 11,
          maxHeight: 300,
          overflow: "auto",
          border: "1px solid #303030",
          margin: 0,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
        }}>
          {sStr}
        </pre>
      </Col>
    </Row>
  );
}

function deltaColor(delta: number | null): string {
  if (delta === null) return "#8c8c8c";
  if (delta > 0.05) return "#52c41a";
  if (delta < -0.05) return "#ff4d4f";
  return "#faad14";
}

function qualityTag(score: number | null): React.ReactNode {
  if (score === null) return <Tag>N/A</Tag>;
  const color = score >= 0.8 ? "green" : score >= 0.6 ? "orange" : "red";
  return <Tag color={color}>{score.toFixed(3)}</Tag>;
}

const columns: ColumnsType<ShadowComparison> = [
  {
    title: "Task Type",
    dataIndex: ["primary", "task_type"],
    key: "task_type",
    width: 140,
    render: (val: string) => <Tag color={CHART_COLORS[0]}>{val}</Tag>,
  },
  {
    title: "Timestamp",
    dataIndex: ["primary", "timestamp"],
    key: "timestamp",
    width: 170,
    render: (val: string) => val?.replace("T", " ").substring(0, 19),
  },
  {
    title: "Primary Model",
    dataIndex: ["primary", "model_alias"],
    key: "primary_model",
    width: 120,
  },
  {
    title: "Shadow Model",
    dataIndex: ["shadow", "model_alias"],
    key: "shadow_model",
    width: 120,
  },
  {
    title: "Primary Quality",
    dataIndex: ["primary", "quality_score"],
    key: "primary_quality",
    width: 120,
    render: qualityTag,
  },
  {
    title: "Shadow Quality",
    dataIndex: ["shadow", "quality_score"],
    key: "shadow_quality",
    width: 120,
    render: qualityTag,
  },
  {
    title: "Delta",
    dataIndex: "quality_delta",
    key: "delta",
    width: 100,
    render: (val: number | null) => (
      <span style={{ color: deltaColor(val), fontWeight: 600 }}>
        {val !== null ? (val > 0 ? "+" : "") + val.toFixed(4) : "N/A"}
      </span>
    ),
  },
  {
    title: "Cost (P / S)",
    key: "cost",
    width: 130,
    render: (_: unknown, record: ShadowComparison) => (
      <span style={{ fontSize: 12 }}>
        ${record.primary.cost_usd.toFixed(4)} / ${record.shadow.cost_usd.toFixed(4)}
      </span>
    ),
  },
];

export default function ComparisonTable({ comparisons, loading }: Props) {
  return (
    <Table<ShadowComparison>
      columns={columns}
      dataSource={comparisons}
      rowKey={(r) => r.primary.id + "-" + r.shadow.id}
      loading={loading}
      size="small"
      expandable={{
        expandedRowRender: (record) => (
          <DiffView primary={record.primary.output ?? null} shadow={record.shadow.output ?? null} />
        ),
      }}
      pagination={{ pageSize: 20, showSizeChanger: true, pageSizeOptions: ["10", "20", "50"] }}
      locale={{ emptyText: <Empty description="No shadow comparisons found" /> }}
    />
  );
}
