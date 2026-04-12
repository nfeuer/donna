// donna-ui/src/pages/Shadow/ComparisonTable.tsx
import { useMemo } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { DataTable } from "../../primitives/DataTable";
import { Pill, type PillVariant } from "../../primitives/Pill";
import type { ShadowComparison } from "../../api/shadow";

interface Props {
  comparisons: ShadowComparison[];
  loading: boolean;
  selectedId?: string | null;
  onRowClick?: (row: ShadowComparison) => void;
}

function getRowId(row: ShadowComparison): string {
  return `${row.primary.id}-${row.shadow.id}`;
}

function deltaVariant(delta: number | null): PillVariant {
  if (delta === null) return "muted";
  if (delta > 0.05) return "success";
  if (delta < -0.05) return "error";
  return "warning";
}

function formatTs(ts: string): string {
  return ts.replace("T", " ").substring(0, 19);
}

export default function ComparisonTable({ comparisons, loading, selectedId, onRowClick }: Props) {
  const columns = useMemo<ColumnDef<ShadowComparison>[]>(
    () => [
      {
        accessorFn: (row) => row.primary.task_type,
        id: "task_type",
        header: "Task Type",
        size: 140,
        cell: ({ getValue }) => (
          <Pill variant="accent">{getValue<string>()}</Pill>
        ),
      },
      {
        accessorFn: (row) => row.primary.timestamp,
        id: "timestamp",
        header: "Timestamp",
        size: 170,
        cell: ({ getValue }) => formatTs(getValue<string>()),
      },
      {
        accessorFn: (row) => row.primary.model_alias,
        id: "primary_model",
        header: "Primary",
        size: 120,
      },
      {
        accessorFn: (row) => row.shadow.model_alias,
        id: "shadow_model",
        header: "Shadow",
        size: 120,
      },
      {
        accessorFn: (row) => row.primary.quality_score,
        id: "primary_q",
        header: "P. Quality",
        size: 100,
        cell: ({ getValue }) => {
          const v = getValue<number | null>();
          return v != null ? v.toFixed(3) : "—";
        },
      },
      {
        accessorFn: (row) => row.shadow.quality_score,
        id: "shadow_q",
        header: "S. Quality",
        size: 100,
        cell: ({ getValue }) => {
          const v = getValue<number | null>();
          return v != null ? v.toFixed(3) : "—";
        },
      },
      {
        accessorKey: "quality_delta",
        header: "Δ",
        size: 90,
        cell: ({ getValue }) => {
          const v = getValue<number | null>();
          if (v == null) return "—";
          return (
            <Pill variant={deltaVariant(v)}>
              {v > 0 ? "+" : ""}{v.toFixed(4)}
            </Pill>
          );
        },
      },
      {
        id: "cost",
        header: "Cost (P/S)",
        size: 130,
        cell: ({ row }) => (
          <span style={{ fontSize: "var(--text-small)" }}>
            ${row.original.primary.cost_usd.toFixed(4)} / ${row.original.shadow.cost_usd.toFixed(4)}
          </span>
        ),
      },
    ],
    [],
  );

  return (
    <DataTable
      data={comparisons}
      columns={columns}
      getRowId={getRowId}
      onRowClick={onRowClick}
      selectedRowId={selectedId}
      keyboardNav
      loading={loading}
      pageSize={20}
      emptyState="No shadow comparisons found"
    />
  );
}
