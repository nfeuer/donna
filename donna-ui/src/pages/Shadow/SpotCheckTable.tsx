import { Table, Tag, Progress, Empty, Button } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import type { SpotCheckItem } from "../../api/shadow";
import { exportToCsv } from "../../utils/csvExport";

interface Props {
  items: SpotCheckItem[];
  total: number;
  loading: boolean;
  page: number;
  pageSize: number;
  onPageChange: (page: number, pageSize: number) => void;
}

const columns: ColumnsType<SpotCheckItem> = [
  {
    title: "Timestamp",
    dataIndex: "timestamp",
    key: "timestamp",
    width: 170,
    render: (val: string) => val?.replace("T", " ").substring(0, 19),
  },
  {
    title: "Task Type",
    dataIndex: "task_type",
    key: "task_type",
    width: 140,
    render: (val: string) => <Tag color="blue">{val}</Tag>,
  },
  {
    title: "Model",
    dataIndex: "model_alias",
    key: "model",
    width: 120,
  },
  {
    title: "Quality Score",
    dataIndex: "quality_score",
    key: "quality_score",
    width: 180,
    render: (val: number | null) => {
      if (val === null) return <Tag>Pending</Tag>;
      const pct = Math.round(val * 100);
      const status = val >= 0.7 ? "normal" : "exception";
      return <Progress percent={pct} size="small" status={status} />;
    },
  },
  {
    title: "Shadow",
    dataIndex: "is_shadow",
    key: "is_shadow",
    width: 80,
    render: (val: boolean) => (
      <Tag color={val ? "purple" : "default"}>{val ? "Yes" : "No"}</Tag>
    ),
  },
  {
    title: "Queued",
    dataIndex: "spot_check_queued",
    key: "queued",
    width: 80,
    render: (val: boolean) => (
      <Tag color={val ? "orange" : "default"}>{val ? "Yes" : "No"}</Tag>
    ),
  },
  {
    title: "Latency",
    dataIndex: "latency_ms",
    key: "latency",
    width: 90,
    render: (val: number) => `${val}ms`,
  },
  {
    title: "Cost",
    dataIndex: "cost_usd",
    key: "cost",
    width: 90,
    render: (val: number) => `$${val.toFixed(4)}`,
  },
];

export default function SpotCheckTable({ items, total, loading, page, pageSize, onPageChange }: Props) {
  const handleExport = () => {
    exportToCsv("spot-checks", [
      { key: "timestamp", title: "Timestamp" },
      { key: "task_type", title: "Task Type" },
      { key: "model_alias", title: "Model" },
      { key: "quality_score", title: "Quality Score" },
      { key: "is_shadow", title: "Shadow" },
      { key: "spot_check_queued", title: "Queued" },
      { key: "latency_ms", title: "Latency (ms)" },
      { key: "cost_usd", title: "Cost (USD)" },
    ], items as unknown as Record<string, unknown>[]);
  };

  return (
    <>
    <div style={{ marginBottom: 8, textAlign: "right" }}>
      <Button size="small" icon={<DownloadOutlined />} onClick={handleExport}>
        Export CSV
      </Button>
    </div>
    <Table<SpotCheckItem>
      columns={columns}
      dataSource={items}
      rowKey="id"
      loading={loading}
      size="small"
      pagination={{
        current: page,
        pageSize,
        total,
        showSizeChanger: true,
        pageSizeOptions: ["25", "50", "100"],
        onChange: onPageChange,
      }}
      locale={{ emptyText: <Empty description="No spot-check items flagged for review" /> }}
    />
    </>
  );
}
