import { Drawer } from "../../primitives/Drawer";
import { Pill, type PillVariant } from "../../primitives/Pill";
import type { ShadowComparison } from "../../api/shadow";
import styles from "./ComparisonDrawer.module.css";

interface Props {
  comparison: ShadowComparison | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function outcomeLabel(delta: number | null): { text: string; variant: PillVariant } {
  if (delta === null) return { text: "N/A", variant: "muted" };
  if (delta > 0.05) return { text: "Shadow wins", variant: "success" };
  if (delta < -0.05) return { text: "Primary wins", variant: "error" };
  return { text: "Tie", variant: "warning" };
}

function formatTs(ts: string): string {
  return ts.replace("T", " ").substring(0, 19);
}

function formatOutput(output: Record<string, unknown> | null): string {
  if (!output) return "(no output)";
  return JSON.stringify(output, null, 2);
}

export default function ComparisonDrawer({ comparison, open, onOpenChange }: Props) {
  if (!comparison) return null;

  const { primary, shadow, quality_delta } = comparison;
  const outcome = outcomeLabel(quality_delta);

  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      title="Comparison Detail"
    >
      <div className={styles.header}>
        <Pill variant="accent">{primary.task_type}</Pill>
        <span style={{ color: "var(--color-text-muted)", fontSize: "var(--text-small)" }}>
          {formatTs(primary.timestamp)}
        </span>
        <Pill variant={outcome.variant}>{outcome.text}</Pill>
      </div>

      {primary.input_hash && (
        <div className={styles.inputBlock}>
          <div className={styles.inputLabel}>Input hash</div>
          <div className={styles.inputContent}>{primary.input_hash}</div>
        </div>
      )}

      <div className={styles.panels}>
        <div>
          <div className={styles.panelLabel}>Primary output</div>
          <div className={styles.panelContent}>{formatOutput(primary.output ?? null)}</div>
        </div>
        <div>
          <div className={styles.panelLabel}>Shadow output</div>
          <div className={styles.panelContent}>{formatOutput(shadow.output ?? null)}</div>
        </div>
      </div>

      <div className={styles.metaRow}>
        <div className={styles.metaItem}>
          <span className={styles.metaLabel}>Primary model</span>
          <span className={styles.metaValue}>{primary.model_alias}</span>
        </div>
        <div className={styles.metaItem}>
          <span className={styles.metaLabel}>Shadow model</span>
          <span className={styles.metaValue}>{shadow.model_alias}</span>
        </div>
        <div className={styles.metaItem}>
          <span className={styles.metaLabel}>P. latency</span>
          <span className={styles.metaValue}>{primary.latency_ms}ms</span>
        </div>
        <div className={styles.metaItem}>
          <span className={styles.metaLabel}>S. latency</span>
          <span className={styles.metaValue}>{shadow.latency_ms}ms</span>
        </div>
        <div className={styles.metaItem}>
          <span className={styles.metaLabel}>P. cost</span>
          <span className={styles.metaValue}>${primary.cost_usd.toFixed(4)}</span>
        </div>
        <div className={styles.metaItem}>
          <span className={styles.metaLabel}>S. cost</span>
          <span className={styles.metaValue}>${shadow.cost_usd.toFixed(4)}</span>
        </div>
        <div className={styles.metaItem}>
          <span className={styles.metaLabel}>Quality Δ</span>
          <span className={styles.metaValue}>
            {quality_delta != null ? (quality_delta > 0 ? "+" : "") + quality_delta.toFixed(4) : "—"}
          </span>
        </div>
      </div>
    </Drawer>
  );
}
