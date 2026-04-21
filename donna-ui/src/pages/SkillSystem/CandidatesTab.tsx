import { useCallback, useEffect, useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { DataTable } from "../../primitives/DataTable";
import { Pill } from "../../primitives/Pill";
import { Select, SelectItem } from "../../primitives/Select";
import {
  fetchSkillCandidates,
  type SkillCandidate,
} from "../../api/skillSystem";
import styles from "./SkillSystem.module.css";

interface Props {
  selectedId: string | null;
  onRowClick: (id: string) => void;
  refreshToken: number;
}

const STATUS_OPTIONS = ["new", "dismissed", "drafted", "resolved", "all"];

export default function CandidatesTab({
  selectedId,
  onRowClick,
  refreshToken,
}: Props) {
  const [status, setStatus] = useState("new");
  const [rows, setRows] = useState<SkillCandidate[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await fetchSkillCandidates({
        status: status === "all" ? "" : status,
      });
      setRows(resp.candidates);
    } catch {
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => {
    load();
  }, [load, refreshToken]);

  const columns = useMemo<ColumnDef<SkillCandidate>[]>(
    () => [
      {
        accessorKey: "capability_name",
        header: "Capability",
        size: 200,
        cell: ({ getValue }) => getValue<string | null>() ?? "—",
      },
      {
        accessorKey: "status",
        header: "Status",
        size: 110,
        cell: ({ getValue }) => (
          <Pill variant="accent">{getValue<string>()}</Pill>
        ),
      },
      {
        accessorKey: "expected_savings_usd",
        header: "Est. Savings",
        size: 120,
        cell: ({ getValue }) => `$${getValue<number>().toFixed(2)}`,
      },
      {
        accessorKey: "volume_30d",
        header: "Volume 30d",
        size: 100,
      },
      {
        accessorKey: "variance_score",
        header: "Variance",
        size: 100,
        cell: ({ getValue }) => {
          const v = getValue<number | null>();
          return v !== null ? v.toFixed(3) : "—";
        },
      },
      {
        accessorKey: "reported_at",
        header: "Reported",
        size: 170,
        cell: ({ getValue }) =>
          getValue<string>().replace("T", " ").slice(0, 19),
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
        <div className={styles.toolbarSpacer} />
        <span style={{ fontSize: "var(--text-small)", color: "var(--color-text-muted)" }}>
          {rows.length} candidates
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
        emptyState="No candidates match the current filter."
      />
    </div>
  );
}
