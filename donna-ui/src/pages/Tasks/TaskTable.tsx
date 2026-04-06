import { Table, Tag, Button } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import type { TaskSummary } from "../../api/tasks";
import { exportToCsv } from "../../utils/csvExport";

const STATUS_TAG_COLORS: Record<string, string> = {
  backlog: "default",
  scheduled: "blue",
  in_progress: "processing",
  blocked: "error",
  waiting_input: "warning",
  done: "success",
  cancelled: "",
};

const PRIORITY_COLORS: Record<number, string> = {
  1: "#ff4d4f",
  2: "#fa8c16",
  3: "#faad14",
  4: "#1890ff",
  5: "#8c8c8c",
};

interface Props {
  tasks: TaskSummary[];
  total: number;
  loading: boolean;
  page: number;
  pageSize: number;
  onPageChange: (page: number, pageSize: number) => void;
  onTaskClick: (id: string) => void;
}

export default function TaskTable({
  tasks,
  total,
  loading,
  page,
  pageSize,
  onPageChange,
  onTaskClick,
}: Props) {
  const columns: ColumnsType<TaskSummary> = [
    {
      title: "Title",
      dataIndex: "title",
      key: "title",
      ellipsis: true,
      width: "30%",
    },
    {
      title: "Status",
      dataIndex: "status",
      key: "status",
      width: 120,
      render: (status: string) => (
        <Tag color={STATUS_TAG_COLORS[status] ?? "default"}>
          {status.replace("_", " ")}
        </Tag>
      ),
    },
    {
      title: "Domain",
      dataIndex: "domain",
      key: "domain",
      width: 90,
      render: (d: string | null) => d ?? "—",
    },
    {
      title: "Priority",
      dataIndex: "priority",
      key: "priority",
      width: 80,
      render: (p: number) => (
        <Tag color={PRIORITY_COLORS[p]} style={{ fontWeight: 600 }}>
          P{p}
        </Tag>
      ),
    },
    {
      title: "Agent",
      dataIndex: "assigned_agent",
      key: "assigned_agent",
      width: 110,
      render: (a: string | null) => a ?? "—",
    },
    {
      title: "Created",
      dataIndex: "created_at",
      key: "created_at",
      width: 110,
      render: (d: string) => (d ? dayjs(d).format("MMM D, HH:mm") : "—"),
    },
    {
      title: "Deadline",
      dataIndex: "deadline",
      key: "deadline",
      width: 110,
      render: (d: string | null) => (d ? dayjs(d).format("MMM D, HH:mm") : "—"),
    },
    {
      title: "Nudges",
      dataIndex: "nudge_count",
      key: "nudge_count",
      width: 70,
      render: (n: number) => (n > 0 ? <Tag color="orange">{n}</Tag> : 0),
    },
    {
      title: "Resched",
      dataIndex: "reschedule_count",
      key: "reschedule_count",
      width: 70,
      render: (n: number) => (n > 0 ? <Tag color="volcano">{n}</Tag> : 0),
    },
  ];

  const handleExport = () => {
    exportToCsv("tasks", [
      { key: "title", title: "Title" },
      { key: "status", title: "Status" },
      { key: "domain", title: "Domain" },
      { key: "priority", title: "Priority" },
      { key: "assigned_agent", title: "Agent" },
      { key: "created_at", title: "Created" },
      { key: "deadline", title: "Deadline" },
      { key: "nudge_count", title: "Nudges" },
      { key: "reschedule_count", title: "Reschedules" },
    ], tasks as unknown as Record<string, unknown>[]);
  };

  return (
    <>
    <div style={{ marginBottom: 8, textAlign: "right" }}>
      <Button size="small" icon={<DownloadOutlined />} onClick={handleExport}>
        Export CSV
      </Button>
    </div>
    <Table<TaskSummary>
      columns={columns}
      dataSource={tasks}
      rowKey="id"
      loading={loading}
      size="small"
      onRow={(record) => ({
        onClick: () => onTaskClick(record.id),
        style: { cursor: "pointer" },
      })}
      pagination={{
        current: page,
        pageSize,
        total,
        showSizeChanger: true,
        pageSizeOptions: ["25", "50", "100"],
        onChange: onPageChange,
        size: "small",
      }}
    />
    </>
  );
}
