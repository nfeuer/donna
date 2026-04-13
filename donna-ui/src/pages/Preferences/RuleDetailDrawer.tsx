import { useState, useEffect, useMemo } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { Drawer } from "../../primitives/Drawer";
import { Pill } from "../../primitives/Pill";
import { DataTable } from "../../primitives/DataTable";
import { fetchCorrections, type PreferenceRule, type CorrectionEntry } from "../../api/preferences";
import styles from "./Preferences.module.css";

interface Props {
  rule: PreferenceRule | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function formatTs(ts: string): string {
  return ts.replace("T", " ").substring(0, 19);
}

export default function RuleDetailDrawer({ rule, open, onOpenChange }: Props) {
  const [corrections, setCorrections] = useState<CorrectionEntry[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!rule || !open) return;
    if (rule.supporting_corrections.length === 0) {
      setCorrections([]);
      return;
    }

    setLoading(true);
    fetchCorrections({ rule_id: rule.id, limit: 500 })
      .then((resp) => setCorrections(resp.corrections))
      .catch(() => setCorrections([]))
      .finally(() => setLoading(false));
  }, [rule, open]);

  const correctionColumns = useMemo<ColumnDef<CorrectionEntry>[]>(
    () => [
      {
        accessorKey: "timestamp",
        header: "Timestamp",
        size: 170,
        cell: ({ getValue }) => formatTs(getValue<string>()),
      },
      {
        accessorKey: "field_corrected",
        header: "Field",
        size: 120,
        cell: ({ getValue }) => <Pill variant="accent">{getValue<string>()}</Pill>,
      },
      {
        accessorKey: "original_value",
        header: "Original",
        size: 120,
      },
      {
        accessorKey: "corrected_value",
        header: "Corrected",
        size: 120,
      },
      {
        accessorKey: "task_type",
        header: "Task Type",
        size: 120,
      },
    ],
    [],
  );

  if (!rule) return null;

  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      title="Rule Details"
    >
      <dl className={styles.dlGrid}>
        <dt className={styles.dlLabel}>Type</dt>
        <dd><Pill variant="accent">{rule.rule_type}</Pill></dd>

        <dt className={styles.dlLabel}>Enabled</dt>
        <dd><Pill variant={rule.enabled ? "success" : "error"}>{rule.enabled ? "Yes" : "No"}</Pill></dd>

        <dt className={styles.dlLabel}>Confidence</dt>
        <dd><Pill variant={rule.confidence >= 0.7 ? "success" : "error"}>{Math.round(rule.confidence * 100)}%</Pill></dd>

        <dt className={styles.dlLabel}>Rule</dt>
        <dd className={styles.dlValue}>{rule.rule_text}</dd>

        <dt className={styles.dlLabel}>Condition</dt>
        <dd>
          <pre className={styles.dlPre}>
            {rule.condition ? JSON.stringify(rule.condition, null, 2) : "any"}
          </pre>
        </dd>

        <dt className={styles.dlLabel}>Action</dt>
        <dd>
          <pre className={styles.dlPre}>
            {rule.action ? JSON.stringify(rule.action, null, 2) : "—"}
          </pre>
        </dd>

        <dt className={styles.dlLabel}>Created</dt>
        <dd className={styles.dlValue}>{rule.created_at?.substring(0, 10)}</dd>

        <dt className={styles.dlLabel}>Disabled</dt>
        <dd className={styles.dlValue}>{rule.disabled_at?.substring(0, 10) ?? "—"}</dd>
      </dl>

      <h4 className={styles.drawerSubheading}>
        Supporting Corrections ({rule.supporting_corrections.length})
      </h4>

      <DataTable
        data={corrections}
        columns={correctionColumns}
        getRowId={(row) => row.id}
        loading={loading}
        pageSize={100}
        emptyState="No supporting corrections found"
      />
    </Drawer>
  );
}
