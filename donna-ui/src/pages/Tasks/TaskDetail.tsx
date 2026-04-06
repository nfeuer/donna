import { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  Card,
  Descriptions,
  Table,
  Tag,
  Steps,
  Button,
  Spin,
  Space,
  Tree,
  Typography,
} from "antd";
import { ArrowLeftOutlined } from "@ant-design/icons";
import dayjs from "dayjs";
import { fetchTask, type TaskDetail as TaskDetailType } from "../../api/tasks";

const { Title } = Typography;

const STATE_ORDER = [
  "backlog",
  "scheduled",
  "in_progress",
  "done",
];

const STATUS_TAG_COLORS: Record<string, string> = {
  backlog: "default",
  scheduled: "blue",
  in_progress: "processing",
  blocked: "error",
  waiting_input: "warning",
  done: "success",
  cancelled: "",
};

function getStateStepIndex(status: string): number {
  const idx = STATE_ORDER.indexOf(status);
  if (idx >= 0) return idx;
  if (status === "blocked" || status === "waiting_input") return 2;
  if (status === "cancelled") return -1;
  return 0;
}

export default function TaskDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [task, setTask] = useState<TaskDetailType | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    fetchTask(id)
      .then(setTask)
      .catch(() => setTask(null))
      .finally(() => setLoading(false));
  }, [id]);

  if (loading) {
    return (
      <div style={{ textAlign: "center", padding: 60 }}>
        <Spin size="large" />
      </div>
    );
  }

  if (!task) {
    return (
      <Card>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate("/tasks")}>
          Back to Tasks
        </Button>
        <div style={{ marginTop: 20 }}>Task not found.</div>
      </Card>
    );
  }

  const stateIdx = getStateStepIndex(task.status);
  const isCancelled = task.status === "cancelled";

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate("/tasks")}>
          Back
        </Button>
        <Title level={4} style={{ margin: 0 }}>
          {task.title}
        </Title>
        <Tag color={STATUS_TAG_COLORS[task.status]}>{task.status.replace("_", " ")}</Tag>
      </Space>

      {/* State Timeline */}
      <Card size="small" title="State Timeline" style={{ marginBottom: 16 }}>
        <Steps
          current={isCancelled ? undefined : stateIdx}
          status={
            task.status === "blocked"
              ? "error"
              : task.status === "waiting_input"
                ? "wait"
                : undefined
          }
          size="small"
          items={STATE_ORDER.map((s) => ({
            title: s.replace("_", " "),
          }))}
        />
        {isCancelled && (
          <Tag color="red" style={{ marginTop: 8 }}>
            Cancelled
          </Tag>
        )}
      </Card>

      {/* Core Fields */}
      <Card size="small" title="Task Details" style={{ marginBottom: 16 }}>
        <Descriptions column={2} size="small" bordered>
          <Descriptions.Item label="ID">{task.id}</Descriptions.Item>
          <Descriptions.Item label="Domain">{task.domain ?? "—"}</Descriptions.Item>
          <Descriptions.Item label="Priority">
            <Tag color={task.priority <= 2 ? "red" : task.priority <= 3 ? "orange" : "blue"}>
              P{task.priority}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Deadline Type">{task.deadline_type ?? "—"}</Descriptions.Item>
          <Descriptions.Item label="Deadline">
            {task.deadline ? dayjs(task.deadline).format("YYYY-MM-DD HH:mm") : "—"}
          </Descriptions.Item>
          <Descriptions.Item label="Scheduled Start">
            {task.scheduled_start ? dayjs(task.scheduled_start).format("YYYY-MM-DD HH:mm") : "—"}
          </Descriptions.Item>
          <Descriptions.Item label="Created">
            {dayjs(task.created_at).format("YYYY-MM-DD HH:mm")}
          </Descriptions.Item>
          <Descriptions.Item label="Created Via">{task.created_via ?? "—"}</Descriptions.Item>
          <Descriptions.Item label="Agent">{task.assigned_agent ?? "—"}</Descriptions.Item>
          <Descriptions.Item label="Agent Status">{task.agent_status ?? "—"}</Descriptions.Item>
          <Descriptions.Item label="Duration (est)">{task.estimated_duration ?? "—"}</Descriptions.Item>
          <Descriptions.Item label="Reschedules">{task.reschedule_count}</Descriptions.Item>
          <Descriptions.Item label="Nudge Count">{task.nudge_count}</Descriptions.Item>
          <Descriptions.Item label="Quality Score">
            {task.quality_score != null ? task.quality_score.toFixed(2) : "—"}
          </Descriptions.Item>
          <Descriptions.Item label="Donna Managed">{task.donna_managed ? "Yes" : "No"}</Descriptions.Item>
          <Descriptions.Item label="Prep Work">{task.prep_work_flag ? "Yes" : "No"}</Descriptions.Item>
        </Descriptions>
        {task.description && (
          <div style={{ marginTop: 12 }}>
            <strong>Description:</strong>
            <div
              style={{
                marginTop: 4,
                padding: 8,
                background: "#262626",
                borderRadius: 4,
                whiteSpace: "pre-wrap",
              }}
            >
              {task.description}
            </div>
          </div>
        )}
        {task.tags && task.tags.length > 0 && (
          <div style={{ marginTop: 8 }}>
            <strong>Tags: </strong>
            {task.tags.map((t) => (
              <Tag key={t}>{t}</Tag>
            ))}
          </div>
        )}
      </Card>

      {/* Linked Invocations */}
      <Card size="small" title={`Invocations (${task.invocations.length})`} style={{ marginBottom: 16 }}>
        <Table
          dataSource={task.invocations}
          rowKey="id"
          size="small"
          pagination={false}
          columns={[
            {
              title: "Time",
              dataIndex: "timestamp",
              render: (t: string) => dayjs(t).format("MMM D HH:mm:ss"),
              width: 130,
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

      {/* Nudge Events */}
      {task.nudge_events.length > 0 && (
        <Card size="small" title={`Nudge Events (${task.nudge_events.length})`} style={{ marginBottom: 16 }}>
          <Table
            dataSource={task.nudge_events}
            rowKey="id"
            size="small"
            pagination={false}
            columns={[
              {
                title: "Time",
                dataIndex: "created_at",
                render: (t: string) => dayjs(t).format("MMM D HH:mm"),
                width: 120,
              },
              { title: "Type", dataIndex: "nudge_type", width: 100 },
              { title: "Channel", dataIndex: "channel", width: 90 },
              { title: "Tier", dataIndex: "escalation_tier", width: 60 },
              { title: "Message", dataIndex: "message_text", ellipsis: true },
            ]}
          />
        </Card>
      )}

      {/* Corrections */}
      {task.corrections.length > 0 && (
        <Card size="small" title={`Corrections (${task.corrections.length})`} style={{ marginBottom: 16 }}>
          <Table
            dataSource={task.corrections}
            rowKey="id"
            size="small"
            pagination={false}
            columns={[
              {
                title: "Time",
                dataIndex: "timestamp",
                render: (t: string) => dayjs(t).format("MMM D HH:mm"),
                width: 120,
              },
              { title: "Field", dataIndex: "field_corrected", width: 120 },
              { title: "Original", dataIndex: "original_value", ellipsis: true },
              { title: "Corrected", dataIndex: "corrected_value", ellipsis: true },
            ]}
          />
        </Card>
      )}

      {/* Subtasks */}
      {task.subtasks.length > 0 && (
        <Card size="small" title={`Subtasks (${task.subtasks.length})`}>
          <Tree
            defaultExpandAll
            treeData={task.subtasks.map((s) => ({
              key: s.id,
              title: (
                <Space>
                  <span>{s.title}</span>
                  <Tag color={STATUS_TAG_COLORS[s.status]}>{s.status}</Tag>
                  <Tag>P{s.priority}</Tag>
                  {s.assigned_agent && <Tag color="blue">{s.assigned_agent}</Tag>}
                </Space>
              ),
            }))}
          />
        </Card>
      )}
    </div>
  );
}
