import { useCallback, useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { Download } from "lucide-react";
import { Button } from "../../primitives/Button";
import { DataTable } from "../../primitives/DataTable";
import { EmptyState } from "../../primitives/EmptyState";
import { Pill } from "../../primitives/Pill";
import type { LogEntry } from "../../api/logs";
import { exportToCsv } from "../../utils/csvExport";
import { levelToPillVariant } from "./levelStyles";
import styles from "./LogTable.module.css";

interface Props {
  entries: LogEntry[];
  loading: boolean;
  onCorrelationClick: (id: string) => void;
  onTaskClick: (id: string) => void;
}

export function formatTimestamp(iso: string | undefined | null): string {
  if (!iso) return "—";
  // Handle Loki nanosecond epoch strings that leaked through
  if (/^\d{16,}$/.test(iso)) {
    const ms = Number(BigInt(iso) / 1_000_000n);
    const d = new Date(ms);
    if (!isNaN(d.getTime())) return d.toLocaleString();
  }
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function rowId(row: LogEntry): string {
  return `${row.timestamp}-${row.correlation_id ?? ""}-${row.event_type ?? ""}`;
}

function MessageCell({ value, expanded }: { value: string; expanded: boolean }) {
  if (!value) return <span className={styles.dim}>—</span>;
  return (
    <span className={expanded ? styles.messageExpanded : styles.message}>
      {value}
    </span>
  );
}

export default function LogTable({
  entries,
  loading,
  onCorrelationClick,
  onTaskClick,
}: Props) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const handleRowClick = useCallback((row: LogEntry) => {
    const id = rowId(row);
    setExpandedId((prev) => (prev === id ? null : id));
  }, []);

  const columns = useMemo<ColumnDef<LogEntry>[]>(
    () => [
      {
        accessorKey: "timestamp",
        header: "Time",
        size: 170,
        cell: (info) => (
          <span className={styles.mono}>{formatTimestamp(info.getValue<string>())}</span>
        ),
      },
      {
        accessorKey: "level",
        header: "Level",
        size: 90,
        cell: (info) => {
          const v = info.getValue<string>();
          return <Pill variant={levelToPillVariant(v)}>{v?.toUpperCase() || "—"}</Pill>;
        },
      },
      {
        accessorKey: "event_type",
        header: "Event",
        size: 200,
        cell: (info) => <span className={styles.eventType}>{info.getValue<string>() || "—"}</span>,
      },
      {
        accessorKey: "message",
        header: "Message",
        cell: (info) => (
          <MessageCell
            value={info.getValue<string>()}
            expanded={rowId(info.row.original) === expandedId}
          />
        ),
      },
      {
        accessorKey: "service",
        header: "Service",
        size: 130,
        cell: (info) => <span className={styles.dim}>{info.getValue<string>() || "—"}</span>,
      },
      {
        accessorKey: "task_id",
        header: "Task",
        size: 100,
        cell: (info) => {
          const v = info.getValue<string>();
          if (!v) return <span className={styles.dim}>—</span>;
          return (
            <button
              type="button"
              className={styles.idLink}
              onClick={(e) => {
                e.stopPropagation();
                onTaskClick(v);
              }}
            >
              {v.slice(0, 8)}…
            </button>
          );
        },
      },
      {
        accessorKey: "correlation_id",
        header: "Trace",
        size: 100,
        cell: (info) => {
          const v = info.getValue<string>();
          if (!v) return <span className={styles.dim}>—</span>;
          return (
            <button
              type="button"
              className={styles.idLink}
              onClick={(e) => {
                e.stopPropagation();
                onCorrelationClick(v);
              }}
            >
              {v.slice(0, 8)}…
            </button>
          );
        },
      },
    ],
    [onCorrelationClick, onTaskClick, expandedId],
  );

  const handleExport = () => {
    exportToCsv(
      "logs",
      [
        { key: "timestamp", title: "Timestamp" },
        { key: "level", title: "Level" },
        { key: "event_type", title: "Event Type" },
        { key: "message", title: "Message" },
        { key: "service", title: "Service" },
        { key: "task_id", title: "Task ID" },
        { key: "correlation_id", title: "Correlation ID" },
      ],
      entries as unknown as Record<string, unknown>[],
    );
  };

  return (
    <div className={styles.wrapper}>
      <div className={styles.toolbar}>
        <Button
          variant="ghost"
          size="sm"
          onClick={handleExport}
          disabled={entries.length === 0}
        >
          <Download size={12} /> Export CSV
        </Button>
      </div>
      <div className={styles.scrollContainer}>
        <DataTable<LogEntry>
          data={entries}
          columns={columns}
          getRowId={rowId}
          onRowClick={handleRowClick}
          loading={loading}
          pageSize={500}
          emptyState={
            <EmptyState
              eyebrow="No events"
              title="No events match."
              body="Widen the window or loosen the filters."
            />
          }
        />
      </div>
    </div>
  );
}
