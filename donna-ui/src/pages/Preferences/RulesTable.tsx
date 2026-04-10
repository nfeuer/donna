import { useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { DataTable, Pill, Switch, type PillVariant } from "../../primitives";
import { toggleRule, type PreferenceRule } from "../../api/preferences";

interface Props {
  rules: PreferenceRule[];
  loading: boolean;
  onRuleClick: (rule: PreferenceRule) => void;
  onRuleToggled: () => void;
}

const RULE_TYPE_VARIANT: Record<string, PillVariant> = {
  scheduling: "accent",
  priority: "warning",
  domain: "success",
  formatting: "muted",
  delegation: "accent",
};

export default function RulesTable({ rules, loading, onRuleClick, onRuleToggled }: Props) {
  const [toggling, setToggling] = useState<string | null>(null);

  const columns = useMemo<ColumnDef<PreferenceRule>[]>(
    () => [
      {
        accessorKey: "rule_type",
        header: "Type",
        size: 110,
        cell: ({ getValue }) => {
          const v = getValue<string>();
          return <Pill variant={RULE_TYPE_VARIANT[v] ?? "muted"}>{v}</Pill>;
        },
      },
      {
        accessorKey: "rule_text",
        header: "Rule",
        cell: ({ getValue }) => (
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", display: "block" }}>
            {getValue<string>()}
          </span>
        ),
      },
      {
        accessorKey: "confidence",
        header: "Confidence",
        size: 100,
        cell: ({ getValue }) => {
          const v = getValue<number>();
          const pct = Math.round(v * 100);
          return (
            <Pill variant={v >= 0.7 ? "success" : "error"}>{pct}%</Pill>
          );
        },
      },
      {
        accessorKey: "enabled",
        header: "Enabled",
        size: 80,
        cell: ({ row }) => (
          <div onClick={(e) => e.stopPropagation()}>
            <Switch
              checked={row.original.enabled}
              onCheckedChange={(checked) => {
                setToggling(row.original.id);
                toggleRule(row.original.id, checked)
                  .then(() => onRuleToggled())
                  .catch(() => {})
                  .finally(() => setToggling(null));
              }}
              disabled={toggling === row.original.id}
              aria-label={`Toggle rule ${row.original.rule_text}`}
            />
          </div>
        ),
      },
      {
        id: "corrections_count",
        header: "Corrections",
        size: 100,
        cell: ({ row }) => row.original.supporting_corrections.length,
      },
      {
        accessorKey: "created_at",
        header: "Created",
        size: 100,
        cell: ({ getValue }) => getValue<string>()?.substring(0, 10),
      },
    ],
    [toggling],
  );

  return (
    <DataTable
      data={rules}
      columns={columns}
      getRowId={(row) => row.id}
      onRowClick={onRuleClick}
      keyboardNav
      loading={loading}
      pageSize={20}
      emptyState="No rules learned yet. Donna picks these up as you correct her."
    />
  );
}
