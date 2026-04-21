import { useCallback, useEffect, useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { DataTable } from "../../primitives/DataTable";
import { Pill } from "../../primitives/Pill";
import { Select, SelectItem } from "../../primitives/Select";
import { fetchSkills, type Skill } from "../../api/skillSystem";
import styles from "./SkillSystem.module.css";

interface Props {
  selectedId: string | null;
  onRowClick: (id: string) => void;
  refreshToken: number;
}

const STATE_FILTERS: string[] = [
  "claude_native",
  "skill_candidate",
  "draft",
  "sandbox",
  "shadow_primary",
  "trusted",
  "flagged_for_review",
  "degraded",
];

export default function SkillsTab({
  selectedId,
  onRowClick,
  refreshToken,
}: Props) {
  const ALL = "__all__";
  const [state, setState] = useState<string>(ALL);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await fetchSkills({
        state: state === ALL ? undefined : state,
      });
      setSkills(resp.skills);
    } catch {
      setSkills([]);
    } finally {
      setLoading(false);
    }
  }, [state]);

  useEffect(() => {
    load();
  }, [load, refreshToken]);

  const columns = useMemo<ColumnDef<Skill>[]>(
    () => [
      {
        accessorKey: "capability_name",
        header: "Capability",
        size: 200,
      },
      {
        accessorKey: "state",
        header: "State",
        size: 140,
        cell: ({ getValue }) => (
          <Pill variant="accent">{getValue<string>()}</Pill>
        ),
      },
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
        cell: ({ getValue }) => getValue<string>().replace("T", " ").slice(0, 19),
      },
    ],
    [],
  );

  return (
    <div className={styles.tabContent}>
      <div className={styles.toolbar}>
        <Select
          value={state}
          onValueChange={setState}
          placeholder="All states"
          aria-label="Filter by state"
        >
          <SelectItem value={ALL}>All states</SelectItem>
          {STATE_FILTERS.map((s) => (
            <SelectItem key={s} value={s}>
              {s}
            </SelectItem>
          ))}
        </Select>
        <div className={styles.toolbarSpacer} />
        <span style={{ fontSize: "var(--text-small)", color: "var(--color-text-muted)" }}>
          {skills.length} skills
        </span>
      </div>
      <DataTable
        data={skills}
        columns={columns}
        getRowId={(r) => r.id}
        onRowClick={(r) => onRowClick(r.id)}
        selectedRowId={selectedId}
        keyboardNav
        loading={loading}
        pageSize={25}
        emptyState="No skills match the current filters."
      />
    </div>
  );
}
