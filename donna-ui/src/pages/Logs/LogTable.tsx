import { useMemo } from "react";
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

/**
 * Single timestamp formatter used by the Logs table *and* the trace
 * view. Replaces the inline `.replace("T", " ").slice(0, 19)` dotted
 * around the old AntD code (Wave 3 audit item P2).
 */
export function formatTimestamp(iso: string | undefined | null): string {
  if (!iso) return "—";
  // Keep the format identical to the old AntD table:
  // "2026-04-08 13:05:47" — no timezone, second-precision.
  const cleaned = iso.replace("T", " ");
  return cleaned.length >= 19 ? cleaned.slice(0, 19) : cleaned;
}

export default function LogTable({
  entries,
  loading,
  onCorrelationClick,
  onTaskClick,
}: Props) {
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
        cell: (info) => <span className={styles.message}>{info.getValue<string>() || "—"}</span>,
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
    [onCorrelationClick, onTaskClick],
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
      <DataTable<LogEntry>
        data={entries}
        columns={columns}
        getRowId={(row) =>
          `${row.timestamp}-${row.correlation_id ?? ""}-${row.event_type ?? ""}`
        }
        loading={loading}
        virtual
        rowHeight={44}
        maxHeight={560}
        emptyState={
          <EmptyState
            eyebrow="No events"
            title="No events match."
            body="Widen the window or loosen the filters."
          />
        }
      />
    </div>
  );
}
