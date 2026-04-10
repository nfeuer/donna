import { useMemo } from "react";
import { Download } from "lucide-react";
import type { ColumnDef } from "@tanstack/react-table";
import { DataTable, Pill, Button, type PillVariant } from "../../primitives";
import type { CorrectionEntry } from "../../api/preferences";
import { exportToCsv } from "../../utils/csvExport";

interface Props {
  corrections: CorrectionEntry[];
  loading: boolean;
}

const FIELD_VARIANT: Record<string, PillVariant> = {
  priority: "warning",
  domain: "success",
  scheduled_start: "accent",
  deadline: "error",
  title: "muted",
  status: "accent",
};

function formatTs(ts: string): string {
  return ts.replace("T", " ").substring(0, 19);
}

export default function CorrectionsTable({ corrections, loading }: Props) {
  const handleExport = () => {
    exportToCsv("corrections", [
      { key: "timestamp", title: "Timestamp" },
      { key: "task_type", title: "Task Type" },
      { key: "field_corrected", title: "Field" },
      { key: "original_value", title: "Original" },
      { key: "corrected_value", title: "Corrected" },
      { key: "input_text", title: "Input" },
    ], corrections as unknown as Record<string, unknown>[]);
  };

  const columns = useMemo<ColumnDef<CorrectionEntry>[]>(
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
        size: 130,
        cell: ({ getValue }) => <Pill variant="accent">{getValue<string>()}</Pill>,
      },
      {
        accessorKey: "field_corrected",
        header: "Field",
        size: 130,
        cell: ({ getValue }) => {
          const v = getValue<string>();
          return <Pill variant={FIELD_VARIANT[v] ?? "muted"}>{v}</Pill>;
        },
      },
      {
        accessorKey: "original_value",
        header: "Original",
        size: 150,
        cell: ({ getValue }) => (
          <span style={{ textDecoration: "line-through", color: "var(--color-error)", fontSize: "var(--text-small)" }}>
            {getValue<string>()}
          </span>
        ),
      },
      {
        accessorKey: "corrected_value",
        header: "Corrected",
        size: 150,
        cell: ({ getValue }) => (
          <span style={{ color: "var(--color-success)", fontSize: "var(--text-small)" }}>
            {getValue<string>()}
          </span>
        ),
      },
      {
        accessorKey: "input_text",
        header: "Input",
        cell: ({ getValue }) => {
          const v = getValue<string | null>();
          return (
            <span style={{ color: "var(--color-text-muted)", fontSize: "var(--text-small)" }}>
              {v ? (v.length > 80 ? v.substring(0, 80) + "..." : v) : "—"}
            </span>
          );
        },
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
        data={corrections}
        columns={columns}
        getRowId={(row) => row.id}
        loading={loading}
        pageSize={50}
        emptyState="No corrections logged yet"
      />
    </>
  );
}
