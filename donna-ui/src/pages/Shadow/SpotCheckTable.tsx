import { useMemo } from "react";
import { Download } from "lucide-react";
import type { ColumnDef } from "@tanstack/react-table";
import { DataTable, Pill, Button } from "../../primitives";
import type { SpotCheckItem } from "../../api/shadow";
import { exportToCsv } from "../../utils/csvExport";

interface Props {
  items: SpotCheckItem[];
  total: number;
  loading: boolean;
}

function formatTs(ts: string): string {
  return ts.replace("T", " ").substring(0, 19);
}

export default function SpotCheckTable({ items, total: _total, loading }: Props) {
  const handleExport = () => {
    exportToCsv("spot-checks", [
      { key: "timestamp", title: "Timestamp" },
      { key: "task_type", title: "Task Type" },
      { key: "model_alias", title: "Model" },
      { key: "quality_score", title: "Quality Score" },
      { key: "is_shadow", title: "Shadow" },
      { key: "spot_check_queued", title: "Queued" },
      { key: "latency_ms", title: "Latency (ms)" },
      { key: "cost_usd", title: "Cost (USD)" },
    ], items as unknown as Record<string, unknown>[]);
  };

  const columns = useMemo<ColumnDef<SpotCheckItem>[]>(
    () => [
      {
        accessorKey: "timestamp",
        header: "Timestamp",
        size: 170,
        cell: ({ getValue }) => formatTs(getValue<string>()),
      },
      {
        accessorKey: "task_type",
        header: "Task Type",
        size: 140,
        cell: ({ getValue }) => <Pill variant="accent">{getValue<string>()}</Pill>,
      },
      {
        accessorKey: "model_alias",
        header: "Model",
        size: 120,
      },
      {
        accessorKey: "quality_score",
        header: "Quality",
        size: 100,
        cell: ({ getValue }) => {
          const v = getValue<number | null>();
          if (v == null) return <Pill variant="muted">Pending</Pill>;
          const variant = v >= 0.7 ? "success" : "error";
          return <Pill variant={variant}>{Math.round(v * 100)}%</Pill>;
        },
      },
      {
        accessorKey: "is_shadow",
        header: "Shadow",
        size: 80,
        cell: ({ getValue }) => (
          <Pill variant={getValue<boolean>() ? "accent" : "muted"}>
            {getValue<boolean>() ? "Yes" : "No"}
          </Pill>
        ),
      },
      {
        accessorKey: "latency_ms",
        header: "Latency",
        size: 90,
        cell: ({ getValue }) => `${getValue<number>()}ms`,
      },
      {
        accessorKey: "cost_usd",
        header: "Cost",
        size: 90,
        cell: ({ getValue }) => `$${getValue<number>().toFixed(4)}`,
      },
    ],
    [],
  );

  return (
    <>
      <div style={{ marginBottom: "var(--space-2)", textAlign: "right" }}>
        <Button variant="ghost" size="sm" onClick={handleExport}>
          <Download size={14} />
          Export CSV
        </Button>
      </div>
      <DataTable
        data={items}
        columns={columns}
        getRowId={(row) => row.id}
        loading={loading}
        pageSize={50}
        emptyState="No spot-check items flagged for review"
      />
    </>
  );
}
