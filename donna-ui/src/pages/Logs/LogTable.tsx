import { Table, Tag, Typography, Button } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import type { LogEntry } from "../../api/logs";
import { LEVEL_COLORS } from "../../theme/darkTheme";
import { exportToCsv } from "../../utils/csvExport";

const { Text, Link } = Typography;

interface Props {
  entries: LogEntry[];
  total: number;
  loading: boolean;
  page: number;
  pageSize: number;
  onPageChange: (page: number, pageSize: number) => void;
  onCorrelationClick: (id: string) => void;
  onTaskClick: (id: string) => void;
}

export default function LogTable({
  entries,
  total,
  loading,
  page,
  pageSize,
  onPageChange,
  onCorrelationClick,
  onTaskClick,
}: Props) {
  const columns: ColumnsType<LogEntry> = [
    {
      title: "Time",
      dataIndex: "timestamp",
      width: 160,
      render: (v: string) => (
        <Text style={{ fontSize: 11, fontFamily: "monospace" }}>
          {v ? v.replace("T", " ").slice(0, 19) : "—"}
        </Text>
      ),
    },
    {
      title: "Level",
      dataIndex: "level",
      width: 80,
      render: (v: string) => (
        <Tag
          color={LEVEL_COLORS[v?.toUpperCase()] || "#8c8c8c"}
          style={{ fontSize: 11 }}
        >
          {v?.toUpperCase() || "—"}
        </Tag>
      ),
    },
    {
      title: "Event Type",
      dataIndex: "event_type",
      width: 180,
      render: (v: string) => (
        <Tag style={{ fontSize: 11 }}>{v || "—"}</Tag>
      ),
    },
    {
      title: "Message",
      dataIndex: "message",
      ellipsis: true,
      render: (v: string) => (
        <Text style={{ fontSize: 12 }}>{v || "—"}</Text>
      ),
    },
    {
      title: "Service",
      dataIndex: "service",
      width: 120,
      render: (v: string) => (
        <Text type="secondary" style={{ fontSize: 11 }}>
          {v || "—"}
        </Text>
      ),
    },
    {
      title: "Task",
      dataIndex: "task_id",
      width: 90,
      render: (v: string) =>
        v ? (
          <Link
            onClick={() => onTaskClick(v)}
            style={{ fontSize: 11, fontFamily: "monospace" }}
          >
            {v.slice(0, 8)}...
          </Link>
        ) : (
          "—"
        ),
    },
    {
      title: "Trace",
      dataIndex: "correlation_id",
      width: 90,
      render: (v: string) =>
        v ? (
          <Link
            onClick={() => onCorrelationClick(v)}
            style={{ fontSize: 11, fontFamily: "monospace" }}
          >
            {v.slice(0, 8)}...
          </Link>
        ) : (
          "—"
        ),
    },
  ];

  const handleExport = () => {
    exportToCsv("logs", [
      { key: "timestamp", title: "Timestamp" },
      { key: "level", title: "Level" },
      { key: "event_type", title: "Event Type" },
      { key: "message", title: "Message" },
      { key: "service", title: "Service" },
      { key: "task_id", title: "Task ID" },
      { key: "correlation_id", title: "Correlation ID" },
    ], entries as unknown as Record<string, unknown>[]);
  };

  return (
    <>
    <div style={{ marginBottom: 8, textAlign: "right" }}>
      <Button size="small" icon={<DownloadOutlined />} onClick={handleExport}>
        Export CSV
      </Button>
    </div>
    <Table<LogEntry>
      columns={columns}
      dataSource={entries}
      rowKey={(_, idx) => `${idx}`}
      loading={loading}
      size="small"
      pagination={{
        current: page,
        pageSize,
        total,
        showSizeChanger: true,
        pageSizeOptions: ["25", "50", "100"],
        onChange: onPageChange,
        size: "small",
      }}
      expandable={{
        expandedRowRender: (record) => (
          <pre
            style={{
              fontSize: 11,
              fontFamily: "monospace",
              background: "#141414",
              padding: 12,
              borderRadius: 4,
              maxHeight: 300,
              overflow: "auto",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}
          >
            {JSON.stringify(record.extra, null, 2)}
          </pre>
        ),
        rowExpandable: (record) =>
          record.extra != null && Object.keys(record.extra).length > 0,
      }}
    />
    </>
  );
}
