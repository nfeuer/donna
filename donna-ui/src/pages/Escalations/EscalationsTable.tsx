import { useMemo } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { DataTable } from "../../primitives/DataTable";
import { Pill, type PillVariant } from "../../primitives/Pill";
import type { EscalationStatus, EscalationSummary } from "../../api/escalations";

interface Props {
  items: EscalationSummary[];
  loading: boolean;
  selectedId?: string | null;
  onRowClick?: (row: EscalationSummary) => void;
}

const STATUS_VARIANT: Record<EscalationStatus, PillVariant> = {
  open: "warning",
  resolved: "accent",
  submitted: "accent",
  validated: "success",
  failed: "error",
  cancelled: "muted",
};

function ageMinutes(createdAt: string): number {
  const created = Date.parse(createdAt);
  if (Number.isNaN(created)) return 0;
  return Math.max(0, Math.floor((Date.now() - created) / 60_000));
}

function formatAge(mins: number): string {
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  return `${days}d`;
}

function formatTs(ts: string | null): string {
  if (!ts) return "—";
  return ts.replace("T", " ").substring(0, 19);
}

export default function EscalationsTable({
  items,
  loading,
  selectedId,
  onRowClick,
}: Props) {
  const columns = useMemo<ColumnDef<EscalationSummary>[]>(
    () => [
      {
        accessorKey: "task_type",
        header: "Task type",
        size: 160,
        cell: ({ getValue }) => <Pill variant="accent">{getValue<string>()}</Pill>,
      },
      {
        accessorKey: "status",
        header: "Status",
        size: 120,
        cell: ({ getValue }) => {
          const v = getValue<EscalationStatus>();
          return <Pill variant={STATUS_VARIANT[v] ?? "muted"}>{v}</Pill>;
        },
      },
      {
        accessorKey: "mode",
        header: "Mode",
        size: 110,
        cell: ({ getValue }) => {
          const v = getValue<string | null>();
          return v ? <Pill variant="muted">{v}</Pill> : <span>—</span>;
        },
      },
      {
        accessorKey: "estimate_usd",
        header: "Estimate",
        size: 100,
        cell: ({ getValue }) => `$${getValue<number>().toFixed(4)}`,
      },
      {
        accessorKey: "daily_remaining_usd",
        header: "Daily left",
        size: 110,
        cell: ({ getValue }) => `$${getValue<number>().toFixed(2)}`,
      },
      {
        accessorKey: "iteration",
        header: "Iter",
        size: 70,
      },
      {
        accessorKey: "summary",
        header: "Summary",
        size: 360,
        cell: ({ getValue }) => {
          const v = getValue<string | null>();
          if (!v) {
            return (
              <span style={{ color: "var(--color-text-muted)" }}>(no summary)</span>
            );
          }
          const text = v.length > 140 ? `${v.slice(0, 140)}…` : v;
          return <span>{text}</span>;
        },
      },
      {
        accessorKey: "created_at",
        header: "Age",
        size: 90,
        cell: ({ getValue }) => formatAge(ageMinutes(getValue<string>())),
      },
      {
        id: "created_at_full",
        header: "Created",
        size: 170,
        accessorFn: (row) => row.created_at,
        cell: ({ getValue }) => formatTs(getValue<string>()),
      },
    ],
    [],
  );

  return (
    <DataTable
      data={items}
      columns={columns}
      getRowId={(r) => r.correlation_id}
      onRowClick={onRowClick}
      selectedRowId={selectedId}
      keyboardNav
      loading={loading}
      pageSize={25}
      emptyState="No escalations to show"
    />
  );
}
