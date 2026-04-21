import { useCallback, useEffect, useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { DataTable } from "../../primitives/DataTable";
import { Pill, type PillVariant } from "../../primitives/Pill";
import { Select, SelectItem } from "../../primitives/Select";
import {
  fetchSkillRuns,
  fetchRunsForSkill,
  type SkillRun,
} from "../../api/skillSystem";
import styles from "./SkillSystem.module.css";

interface Props {
  selectedId: string | null;
  onRowClick: (id: string) => void;
  skillIdFilter: string | null;
  onClearSkillFilter: () => void;
  refreshToken: number;
}

const STATUS_OPTIONS = ["all", "succeeded", "failed", "escalated", "running"];

function statusVariant(status: string): PillVariant {
  if (status === "succeeded") return "success";
  if (status === "failed") return "error";
  if (status === "escalated") return "warning";
  return "muted";
}

export default function RunsTab({
  selectedId,
  onRowClick,
  skillIdFilter,
  onClearSkillFilter,
  refreshToken,
}: Props) {
  const [status, setStatus] = useState("all");
  const [rows, setRows] = useState<SkillRun[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      if (skillIdFilter) {
        const resp = await fetchRunsForSkill(skillIdFilter);
        const filtered =
          status === "all"
            ? resp.runs
            : resp.runs.filter((r) => r.status === status);
        setRows(filtered);
      } else {
        const resp = await fetchSkillRuns({
          status: status === "all" ? undefined : status,
        });
        setRows(resp.runs);
      }
    } catch {
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [status, skillIdFilter]);

  useEffect(() => {
    load();
  }, [load, refreshToken]);

  const columns = useMemo<ColumnDef<SkillRun>[]>(
    () => [
      {
        accessorKey: "skill_id",
        header: "Skill",
        size: 220,
        cell: ({ getValue }) => (
          <code style={{ fontSize: "var(--text-small)" }}>
            {getValue<string>().slice(0, 12)}
          </code>
        ),
      },
      {
        accessorKey: "status",
        header: "Status",
        size: 120,
        cell: ({ getValue }) => (
          <Pill variant={statusVariant(getValue<string>())}>
            {getValue<string>()}
          </Pill>
        ),
      },
      {
        accessorKey: "total_cost_usd",
        header: "Cost",
        size: 90,
        cell: ({ getValue }) => {
          const v = getValue<number | null>();
          return v !== null ? `$${v.toFixed(4)}` : "—";
        },
      },
      {
        accessorKey: "total_latency_ms",
        header: "Latency",
        size: 100,
        cell: ({ getValue }) => {
          const v = getValue<number | null>();
          if (v === null) return "—";
          return v < 1000 ? `${v} ms` : `${(v / 1000).toFixed(1)} s`;
        },
      },
      {
        accessorKey: "escalation_reason",
        header: "Escalation",
        size: 140,
        cell: ({ getValue }) => getValue<string | null>() ?? "—",
      },
      {
        accessorKey: "started_at",
        header: "Started",
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
          aria-label="Filter runs by status"
        >
          {STATUS_OPTIONS.map((s) => (
            <SelectItem key={s} value={s}>
              {s}
            </SelectItem>
          ))}
        </Select>
        {skillIdFilter && (
          <button
            type="button"
            onClick={onClearSkillFilter}
            style={{
              background: "transparent",
              border: "1px solid var(--color-border-subtle)",
              color: "var(--color-text-primary)",
              padding: "6px 10px",
              borderRadius: "var(--radius-sm)",
              cursor: "pointer",
              fontSize: "var(--text-small)",
            }}
          >
            Clear skill filter ({skillIdFilter.slice(0, 8)}…)
          </button>
        )}
        <div className={styles.toolbarSpacer} />
        <span style={{ fontSize: "var(--text-small)", color: "var(--color-text-muted)" }}>
          {rows.length} runs
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
        emptyState="No runs match the current filter."
      />
    </div>
  );
}
