import { useCallback, useEffect, useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { DataTable } from "../../primitives/DataTable";
import { Pill } from "../../primitives/Pill";
import { fetchSkillDrafts, type SkillDraft } from "../../api/skillSystem";
import styles from "./SkillSystem.module.css";

interface Props {
  selectedId: string | null;
  onRowClick: (id: string) => void;
  refreshToken: number;
}

export default function DraftsTab({
  selectedId,
  onRowClick,
  refreshToken,
}: Props) {
  const [rows, setRows] = useState<SkillDraft[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await fetchSkillDrafts();
      setRows(resp.drafts);
    } catch {
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, refreshToken]);

  const columns = useMemo<ColumnDef<SkillDraft>[]>(
    () => [
      { accessorKey: "capability_name", header: "Capability", size: 220 },
      {
        accessorKey: "requires_human_gate",
        header: "Human Gate",
        size: 110,
        cell: ({ getValue }) =>
          getValue<boolean>() ? (
            <Pill variant="warning">yes</Pill>
          ) : (
            <Pill variant="muted">no</Pill>
          ),
      },
      {
        accessorKey: "baseline_agreement",
        header: "Baseline",
        size: 100,
        cell: ({ getValue }) => {
          const v = getValue<number | null>();
          return v !== null ? v.toFixed(3) : "—";
        },
      },
      {
        accessorKey: "updated_at",
        header: "Updated",
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
        <div className={styles.toolbarSpacer} />
        <span style={{ fontSize: "var(--text-small)", color: "var(--color-text-muted)" }}>
          {rows.length} drafts
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
        emptyState="No drafts in flight."
      />
    </div>
  );
}
