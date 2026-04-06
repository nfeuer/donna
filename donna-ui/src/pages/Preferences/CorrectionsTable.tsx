import { Table, Tag, Empty, Typography, Button } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import type { CorrectionEntry } from "../../api/preferences";
import { exportToCsv } from "../../utils/csvExport";

const { Text } = Typography;

interface Props {
  corrections: CorrectionEntry[];
  total: number;
  loading: boolean;
  page: number;
  pageSize: number;
  onPageChange: (page: number, pageSize: number) => void;
}

const FIELD_COLORS: Record<string, string> = {
  priority: "orange",
  domain: "green",
  scheduled_start: "blue",
  deadline: "red",
  title: "purple",
  status: "cyan",
};

const columns: ColumnsType<CorrectionEntry> = [
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
    width: 130,
    render: (val: string) => <Tag color="blue">{val}</Tag>,
  },
  {
    title: "Field",
    dataIndex: "field_corrected",
    key: "field",
    width: 130,
    render: (val: string) => (
      <Tag color={FIELD_COLORS[val] ?? "default"}>{val}</Tag>
    ),
  },
  {
    title: "Original",
    dataIndex: "original_value",
    key: "original",
    width: 150,
    ellipsis: true,
    render: (val: string) => (
      <Text delete type="danger" style={{ fontSize: 12 }}>
        {val}
      </Text>
    ),
  },
  {
    title: "Corrected",
    dataIndex: "corrected_value",
    key: "corrected",
    width: 150,
    ellipsis: true,
    render: (val: string) => (
      <Text type="success" style={{ fontSize: 12 }}>
        {val}
      </Text>
    ),
  },
  {
    title: "Input",
    dataIndex: "input_text",
    key: "input",
    ellipsis: true,
    render: (val: string | null) => (
      <span style={{ color: "#8c8c8c", fontSize: 12 }}>
        {val ? (val.length > 80 ? val.substring(0, 80) + "..." : val) : "-"}
      </span>
    ),
  },
];

export default function CorrectionsTable({
  corrections,
  total,
  loading,
  page,
  pageSize,
  onPageChange,
}: Props) {
  const handleExport = () => {
    exportToCsv("corrections", [
      { key: "timestamp", title: "Timestamp" },
      { key: "task_type", title: "Task Type" },
      { key: "field_corrected", title: "Field" },
      { key: "original_value", title: "Original" },
      { key: "corrected_value", title: "Corrected" },
      { key: "input_text", title: "Input" },
    ], corrections as unknown as Record<string, unknown>[]);
  };

  return (
    <>
    <div style={{ marginBottom: 8, textAlign: "right" }}>
      <Button size="small" icon={<DownloadOutlined />} onClick={handleExport}>
        Export CSV
      </Button>
    </div>
    <Table<CorrectionEntry>
      columns={columns}
      dataSource={corrections}
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
      locale={{ emptyText: <Empty description="No corrections logged yet" /> }}
    />
    </>
  );
}
