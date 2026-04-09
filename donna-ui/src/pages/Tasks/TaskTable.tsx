import { useMemo } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { Download } from "lucide-react";
import { Button } from "../../primitives/Button";
import { DataTable } from "../../primitives/DataTable";
import { EmptyState } from "../../primitives/EmptyState";
import { Pill } from "../../primitives/Pill";
import type { TaskSummary } from "../../api/tasks";
import { exportToCsv } from "../../utils/csvExport";
import {
  formatStatusLabel,
  formatTaskTimestamp,
  priorityToPillVariant,
  statusToPillVariant,
} from "./taskStatusStyles";
import styles from "./TaskTable.module.css";

interface Props {
  tasks: TaskSummary[];
  loading: boolean;
  selectedId: string | null;
  onTaskClick: (id: string) => void;
}

export default function TaskTable({
  tasks,
  loading,
  selectedId,
  onTaskClick,
}: Props) {
  const columns = useMemo<ColumnDef<TaskSummary>[]>(
    () => [
      {
        accessorKey: "title",
        header: "Title",
        cell: (info) => (
          <span className={styles.title}>{info.getValue<string>()}</span>
        ),
      },
      {
        accessorKey: "status",
        header: "Status",
        size: 130,
        cell: (info) => {
          const v = info.getValue<string>();
          return (
            <Pill variant={statusToPillVariant(v)}>{formatStatusLabel(v)}</Pill>
          );
        },
      },
      {
        accessorKey: "domain",
        header: "Domain",
        size: 100,
        cell: (info) => (
          <span className={styles.dim}>{info.getValue<string>() ?? "—"}</span>
        ),
      },
      {
        accessorKey: "priority",
        header: "Priority",
        size: 90,
        cell: (info) => {
          const v = info.getValue<number>();
          return <Pill variant={priorityToPillVariant(v)}>P{v}</Pill>;
        },
      },
      {
        accessorKey: "assigned_agent",
        header: "Agent",
        size: 120,
        cell: (info) => (
          <span className={styles.dim}>{info.getValue<string>() ?? "—"}</span>
        ),
      },
      {
        accessorKey: "created_at",
        header: "Created",
        size: 140,
        cell: (info) => (
          <span className={styles.mono}>
            {formatTaskTimestamp(info.getValue<string>())}
          </span>
        ),
      },
      {
        accessorKey: "deadline",
        header: "Deadline",
        size: 140,
        cell: (info) => (
          <span className={styles.mono}>
            {formatTaskTimestamp(info.getValue<string | null>())}
          </span>
        ),
      },
      {
        accessorKey: "nudge_count",
        header: "Nudges",
        size: 80,
        cell: (info) => {
          const n = info.getValue<number>();
          if (n === 0) return <span className={styles.dim}>0</span>;
          return <Pill variant="warning">{n}</Pill>;
        },
      },
      {
        accessorKey: "reschedule_count",
        header: "Resched",
        size: 80,
        cell: (info) => {
          const n = info.getValue<number>();
          if (n === 0) return <span className={styles.dim}>0</span>;
          return <Pill variant="warning">{n}</Pill>;
        },
      },
    ],
    [],
  );

  const handleExport = () => {
    exportToCsv(
      "tasks",
      [
        { key: "title", title: "Title" },
        { key: "status", title: "Status" },
        { key: "domain", title: "Domain" },
        { key: "priority", title: "Priority" },
        { key: "assigned_agent", title: "Agent" },
        { key: "created_at", title: "Created" },
        { key: "deadline", title: "Deadline" },
        { key: "nudge_count", title: "Nudges" },
        { key: "reschedule_count", title: "Reschedules" },
      ],
      tasks as unknown as Record<string, unknown>[],
    );
  };

  return (
    <div className={styles.wrapper}>
      <div className={styles.toolbar}>
        <Button
          variant="ghost"
          size="sm"
          onClick={handleExport}
          disabled={tasks.length === 0}
          aria-label="Export tasks to CSV"
        >
          <Download size={12} /> Export CSV
        </Button>
      </div>
      <DataTable<TaskSummary>
        data={tasks}
        columns={columns}
        getRowId={(row) => row.id}
        onRowClick={(row) => onTaskClick(row.id)}
        selectedRowId={selectedId}
        loading={loading}
        keyboardNav
        pageSize={50}
        emptyState={
          <EmptyState
            eyebrow="No tasks"
            title="Nothing captured yet."
            body="Press ⌘N to add one, or message Donna on Discord and she'll do it for you."
          />
        }
      />
    </div>
  );
}
