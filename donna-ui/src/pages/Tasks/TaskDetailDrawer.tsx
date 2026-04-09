import { useEffect, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { DataTable } from "../../primitives/DataTable";
import { Drawer } from "../../primitives/Drawer";
import { Pill } from "../../primitives/Pill";
import { ScrollArea } from "../../primitives/ScrollArea";
import { Skeleton } from "../../primitives/Skeleton";
import {
  fetchTask,
  type Correction,
  type NudgeEvent,
  type Subtask,
  type TaskDetail,
  type TaskInvocation,
} from "../../api/tasks";
import {
  STATE_ORDER,
  formatStatusLabel,
  formatTaskTimestamp,
  getStateStepIndex,
  priorityToPillVariant,
  statusToPillVariant,
} from "./taskStatusStyles";
import styles from "./TaskDetailDrawer.module.css";

interface Props {
  taskId: string | null;
  onClose: () => void;
}

/**
 * Drawer-based task detail surface. Replaces the free-floating
 * /tasks/:id page and the AntD Card/Descriptions/Steps/Tree stack.
 * Focus trap + ESC close come free from the Radix-backed Drawer
 * primitive (audit item P1 "Task drawer a11y").
 */
export default function TaskDetailDrawer({ taskId, onClose }: Props) {
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    if (!taskId) {
      setTask(null);
      setNotFound(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setNotFound(false);
    fetchTask(taskId)
      .then((data) => {
        if (cancelled) return;
        setTask(data);
      })
      .catch(() => {
        if (cancelled) return;
        setTask(null);
        setNotFound(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [taskId]);

  const title = task?.title ?? (taskId ? `Task · ${taskId.slice(0, 8)}…` : "Task");

  return (
    <Drawer
      open={!!taskId}
      onOpenChange={(open) => !open && onClose()}
      title={title}
    >
      {loading ? (
        <div className={styles.loading}>
          <Skeleton height={16} />
          <Skeleton height={16} />
          <Skeleton height={16} />
        </div>
      ) : notFound ? (
        <div className={styles.emptyHint}>Task not found.</div>
      ) : task ? (
        <ScrollArea className={styles.scroll}>
          <TaskDrawerBody task={task} />
        </ScrollArea>
      ) : null}
    </Drawer>
  );
}

function TaskDrawerBody({ task }: { task: TaskDetail }) {
  const stepIdx = getStateStepIndex(task.status);
  const isCancelled = task.status === "cancelled";

  return (
    <div className={styles.body}>
      {/* Status + priority header row */}
      <div className={styles.headerRow}>
        <Pill variant={statusToPillVariant(task.status)}>
          {formatStatusLabel(task.status)}
        </Pill>
        <Pill variant={priorityToPillVariant(task.priority)}>P{task.priority}</Pill>
        {task.donna_managed && <Pill variant="accent">Donna-managed</Pill>}
      </div>

      {/* State timeline (no AntD Steps) */}
      <section className={styles.section} aria-label="State timeline">
        <div className={styles.eyebrow}>State timeline</div>
        <ol className={styles.timeline}>
          {STATE_ORDER.map((state, idx) => {
            const reached = !isCancelled && idx <= stepIdx;
            return (
              <li
                key={state}
                className={styles.timelineItem}
                data-reached={reached ? "true" : "false"}
                aria-current={idx === stepIdx ? "step" : undefined}
              >
                <span className={styles.timelineDot} aria-hidden="true" />
                <span className={styles.timelineLabel}>
                  {formatStatusLabel(state)}
                </span>
              </li>
            );
          })}
        </ol>
        {isCancelled && (
          <div className={styles.cancelledNote}>Cancelled — timeline abandoned.</div>
        )}
      </section>

      {/* Core fields (<dl> replaces AntD Descriptions) */}
      <section className={styles.section} aria-label="Task details">
        <div className={styles.eyebrow}>Details</div>
        <dl className={styles.fields}>
          <DetailField label="ID" value={task.id} mono />
          <DetailField label="Domain" value={task.domain ?? "—"} />
          <DetailField label="Deadline type" value={task.deadline_type ?? "—"} />
          <DetailField label="Deadline" value={formatTaskTimestamp(task.deadline)} mono />
          <DetailField
            label="Scheduled start"
            value={formatTaskTimestamp(task.scheduled_start)}
            mono
          />
          <DetailField label="Created" value={formatTaskTimestamp(task.created_at)} mono />
          <DetailField label="Created via" value={task.created_via ?? "—"} />
          <DetailField label="Agent" value={task.assigned_agent ?? "—"} />
          <DetailField label="Agent status" value={task.agent_status ?? "—"} />
          <DetailField label="Duration (est)" value={task.estimated_duration ?? "—"} />
          <DetailField label="Reschedules" value={String(task.reschedule_count)} />
          <DetailField label="Nudge count" value={String(task.nudge_count)} />
          <DetailField
            label="Quality score"
            value={task.quality_score != null ? task.quality_score.toFixed(2) : "—"}
          />
          <DetailField label="Prep work" value={task.prep_work_flag ? "Yes" : "No"} />
        </dl>

        {task.description && (
          <div className={styles.descriptionBlock}>
            <div className={styles.eyebrow}>Description</div>
            <pre className={styles.description}>{task.description}</pre>
          </div>
        )}

        {task.tags && task.tags.length > 0 && (
          <div className={styles.tagRow}>
            <span className={styles.eyebrow}>Tags</span>
            {task.tags.map((t) => (
              <Pill key={t} variant="muted">
                {t}
              </Pill>
            ))}
          </div>
        )}
      </section>

      {/* Invocations */}
      {task.invocations.length > 0 && (
        <section className={styles.section} aria-label="Invocations">
          <div className={styles.eyebrow}>Invocations ({task.invocations.length})</div>
          <DataTable<TaskInvocation>
            data={task.invocations}
            columns={INVOCATION_COLUMNS}
            getRowId={(row) => row.id}
          />
        </section>
      )}

      {/* Nudge events */}
      {task.nudge_events.length > 0 && (
        <section className={styles.section} aria-label="Nudge events">
          <div className={styles.eyebrow}>Nudge events ({task.nudge_events.length})</div>
          <DataTable<NudgeEvent>
            data={task.nudge_events}
            columns={NUDGE_COLUMNS}
            getRowId={(row) => row.id}
          />
        </section>
      )}

      {/* Corrections */}
      {task.corrections.length > 0 && (
        <section className={styles.section} aria-label="Corrections">
          <div className={styles.eyebrow}>Corrections ({task.corrections.length})</div>
          <DataTable<Correction>
            data={task.corrections}
            columns={CORRECTION_COLUMNS}
            getRowId={(row) => row.id}
          />
        </section>
      )}

      {/* Subtasks (<ul> replaces AntD Tree) */}
      {task.subtasks.length > 0 && (
        <section className={styles.section} aria-label="Subtasks">
          <div className={styles.eyebrow}>Subtasks ({task.subtasks.length})</div>
          <ul className={styles.subtaskList}>
            {task.subtasks.map((s: Subtask) => (
              <li key={s.id} className={styles.subtaskItem}>
                <span className={styles.subtaskTitle}>{s.title}</span>
                <Pill variant={statusToPillVariant(s.status)}>
                  {formatStatusLabel(s.status)}
                </Pill>
                <Pill variant={priorityToPillVariant(s.priority)}>P{s.priority}</Pill>
                {s.assigned_agent && (
                  <Pill variant="accent">{s.assigned_agent}</Pill>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function DetailField({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className={styles.field}>
      <dt className={styles.fieldLabel}>{label}</dt>
      <dd className={mono ? styles.fieldValueMono : styles.fieldValue}>{value}</dd>
    </div>
  );
}

const INVOCATION_COLUMNS: ColumnDef<TaskInvocation>[] = [
  {
    accessorKey: "timestamp",
    header: "Time",
    size: 150,
    cell: (info) => formatTaskTimestamp(info.getValue<string>()),
  },
  { accessorKey: "task_type", header: "Type", size: 140 },
  { accessorKey: "model_alias", header: "Model", size: 100 },
  {
    accessorKey: "latency_ms",
    header: "Latency",
    size: 80,
    cell: (info) => `${info.getValue<number>()}ms`,
  },
  {
    accessorKey: "cost_usd",
    header: "Cost",
    size: 80,
    cell: (info) => `$${info.getValue<number>().toFixed(4)}`,
  },
  {
    accessorKey: "is_shadow",
    header: "Shadow",
    size: 70,
    cell: (info) =>
      info.getValue<boolean>() ? <Pill variant="muted">Yes</Pill> : "No",
  },
];

const NUDGE_COLUMNS: ColumnDef<NudgeEvent>[] = [
  {
    accessorKey: "created_at",
    header: "Time",
    size: 140,
    cell: (info) => formatTaskTimestamp(info.getValue<string>()),
  },
  { accessorKey: "nudge_type", header: "Type", size: 110 },
  { accessorKey: "channel", header: "Channel", size: 90 },
  { accessorKey: "escalation_tier", header: "Tier", size: 60 },
  { accessorKey: "message_text", header: "Message" },
];

const CORRECTION_COLUMNS: ColumnDef<Correction>[] = [
  {
    accessorKey: "timestamp",
    header: "Time",
    size: 140,
    cell: (info) => formatTaskTimestamp(info.getValue<string>()),
  },
  { accessorKey: "field_corrected", header: "Field", size: 120 },
  { accessorKey: "original_value", header: "Original" },
  { accessorKey: "corrected_value", header: "Corrected" },
];
