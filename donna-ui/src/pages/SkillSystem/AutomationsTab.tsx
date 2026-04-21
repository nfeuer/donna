import { useCallback, useEffect, useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { DataTable } from "../../primitives/DataTable";
import { Button } from "../../primitives/Button";
import { Pill, type PillVariant } from "../../primitives/Pill";
import { Select, SelectItem } from "../../primitives/Select";
import {
  fetchAutomations,
  type Automation,
} from "../../api/skillSystem";
import styles from "./SkillSystem.module.css";

interface Props {
  selectedId: string | null;
  onRowClick: (id: string) => void;
  onNew: () => void;
  refreshToken: number;
}

const STATUS_OPTIONS = ["active", "paused", "deleted", "all"];

function statusVariant(status: string): PillVariant {
  if (status === "active") return "success";
  if (status === "paused") return "warning";
  if (status === "deleted") return "muted";
  return "accent";
}

export default function AutomationsTab({
  selectedId,
  onRowClick,
  onNew,
  refreshToken,
}: Props) {
  const [status, setStatus] = useState("active");
  const [rows, setRows] = useState<Automation[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await fetchAutomations({ status });
      setRows(resp.automations);
    } catch {
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => {
    load();
  }, [load, refreshToken]);

  const columns = useMemo<ColumnDef<Automation>[]>(
    () => [
      { accessorKey: "name", header: "Name", size: 200 },
      { accessorKey: "capability_name", header: "Capability", size: 200 },
      { accessorKey: "trigger_type", header: "Trigger", size: 110 },
      {
        accessorKey: "schedule",
        header: "Schedule",
        size: 160,
        cell: ({ getValue }) => (
          <code style={{ fontSize: "var(--text-small)" }}>
            {getValue<string | null>() ?? "—"}
          </code>
        ),
      },
      {
        accessorKey: "status",
        header: "Status",
        size: 100,
        cell: ({ getValue }) => (
          <Pill variant={statusVariant(getValue<string>())}>
            {getValue<string>()}
          </Pill>
        ),
      },
      {
        accessorKey: "next_run_at",
        header: "Next run",
        size: 170,
        cell: ({ getValue }) => {
          const v = getValue<string | null>();
          return v ? v.replace("T", " ").slice(0, 19) : "—";
        },
      },
      {
        id: "counts",
        header: "Runs (fail)",
        size: 110,
        cell: ({ row }) =>
          `${row.original.run_count} (${row.original.failure_count})`,
      },
    ],
    [],
  );

  return (
    <div className={styles.tabContent}>
      <div className={styles.toolbar}>
        <Select
          value={status}
          onValueChange={setStatus}
          placeholder="Status"
          aria-label="Filter by status"
        >
          {STATUS_OPTIONS.map((s) => (
            <SelectItem key={s} value={s}>
              {s}
            </SelectItem>
          ))}
        </Select>
        <Button onClick={onNew}>+ New Automation</Button>
        <div className={styles.toolbarSpacer} />
        <span style={{ fontSize: "var(--text-small)", color: "var(--color-text-muted)" }}>
          {rows.length} automations
        </span>
      </div>
      <DataTable
        data={rows}
        columns={columns}
        getRowId={(r) => r.id}
        onRowClick={(r) => onRowClick(r.id)}
        selectedRowId={selectedId}
        keyboardNav
        loading={loading}
        pageSize={25}
        emptyState="No automations match the current filter."
      />
    </div>
  );
}
