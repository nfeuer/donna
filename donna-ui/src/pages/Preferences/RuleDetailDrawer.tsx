import { useState, useEffect, useMemo } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { Drawer, Pill, DataTable } from "../../primitives";
import { fetchCorrections, type PreferenceRule, type CorrectionEntry } from "../../api/preferences";

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
      <dl style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "var(--space-2) var(--space-4)", marginBottom: "var(--space-4)" }}>
        <dt style={{ color: "var(--color-text-muted)", fontSize: "var(--text-small)" }}>Type</dt>
        <dd><Pill variant="accent">{rule.rule_type}</Pill></dd>

        <dt style={{ color: "var(--color-text-muted)", fontSize: "var(--text-small)" }}>Enabled</dt>
        <dd><Pill variant={rule.enabled ? "success" : "error"}>{rule.enabled ? "Yes" : "No"}</Pill></dd>

        <dt style={{ color: "var(--color-text-muted)", fontSize: "var(--text-small)" }}>Confidence</dt>
        <dd><Pill variant={rule.confidence >= 0.7 ? "success" : "error"}>{Math.round(rule.confidence * 100)}%</Pill></dd>

        <dt style={{ color: "var(--color-text-muted)", fontSize: "var(--text-small)" }}>Rule</dt>
        <dd style={{ color: "var(--color-text)" }}>{rule.rule_text}</dd>

        <dt style={{ color: "var(--color-text-muted)", fontSize: "var(--text-small)" }}>Condition</dt>
        <dd>
          <pre style={{ margin: 0, fontSize: "var(--text-small)", fontFamily: "var(--font-mono)", color: "var(--color-text)" }}>
            {rule.condition ? JSON.stringify(rule.condition, null, 2) : "any"}
          </pre>
        </dd>

        <dt style={{ color: "var(--color-text-muted)", fontSize: "var(--text-small)" }}>Action</dt>
        <dd>
          <pre style={{ margin: 0, fontSize: "var(--text-small)", fontFamily: "var(--font-mono)", color: "var(--color-text)" }}>
            {rule.action ? JSON.stringify(rule.action, null, 2) : "—"}
          </pre>
        </dd>

        <dt style={{ color: "var(--color-text-muted)", fontSize: "var(--text-small)" }}>Created</dt>
        <dd style={{ color: "var(--color-text)" }}>{rule.created_at?.substring(0, 10)}</dd>

        <dt style={{ color: "var(--color-text-muted)", fontSize: "var(--text-small)" }}>Disabled</dt>
        <dd style={{ color: "var(--color-text)" }}>{rule.disabled_at?.substring(0, 10) ?? "—"}</dd>
      </dl>

      <h4 style={{
        fontFamily: "var(--font-display)",
        fontWeight: 300,
        fontSize: "var(--text-section)",
        color: "var(--color-text)",
        marginBottom: "var(--space-3)",
      }}>
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
