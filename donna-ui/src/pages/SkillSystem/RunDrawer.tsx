import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Drawer } from "../../primitives/Drawer";
import { Button } from "../../primitives/Button";
import { Pill, type PillVariant } from "../../primitives/Pill";
import { Skeleton } from "../../primitives/Skeleton";
import { JsonViewer } from "../../primitives/JsonViewer";
import {
  captureRunFixture,
  fetchSkillRunDetail,
  fetchSkillRunDivergence,
  type SkillRunDetail,
  type SkillDivergence,
} from "../../api/skillSystem";
import styles from "./SkillSystem.module.css";

interface Props {
  runId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onMutated: () => void;
}

function statusVariant(status: string): PillVariant {
  if (status === "succeeded") return "success";
  if (status === "failed") return "error";
  if (status === "escalated") return "warning";
  return "muted";
}

export default function RunDrawer({
  runId,
  open,
  onOpenChange,
  onMutated,
}: Props) {
  const [detail, setDetail] = useState<SkillRunDetail | null>(null);
  const [divergence, setDivergence] = useState<SkillDivergence | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  const refetch = useCallback(async () => {
    if (!runId) return;
    setLoading(true);
    try {
      const [d, div] = await Promise.all([
        fetchSkillRunDetail(runId),
        fetchSkillRunDivergence(runId).catch(() => null),
      ]);
      setDetail(d);
      setDivergence(div);
    } catch {
      setDetail(null);
      setDivergence(null);
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    if (open && runId) {
      refetch();
    } else {
      setDetail(null);
      setDivergence(null);
    }
  }, [open, runId, refetch]);

  const handleCapture = async () => {
    if (!runId) return;
    setBusy(true);
    try {
      const resp = await captureRunFixture(runId);
      toast.success("Fixture captured", {
        description: `fixture_id=${resp.fixture_id}`,
      });
      onMutated();
    } catch (err: unknown) {
      const msg =
        typeof err === "object" &&
        err !== null &&
        "response" in err &&
        (err as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail
          ? (err as { response: { data: { detail: string } } }).response.data
              .detail
          : "Capture failed";
      toast.error(msg);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      title={detail ? `Run ${detail.id.slice(0, 8)}` : "Skill Run"}
    >
      <div className={styles.drawerBody}>
        {loading && !detail ? (
          <Skeleton width="100%" height={200} />
        ) : !detail ? (
          <p className={styles.kvValue}>Run not found.</p>
        ) : (
          <>
            <section className={styles.drawerSection}>
              <h3 className={styles.drawerSectionTitle}>Summary</h3>
              <div className={styles.kv}>
                <span className={styles.kvKey}>Status</span>
                <span className={styles.kvValue}>
                  <Pill variant={statusVariant(detail.status)}>
                    {detail.status}
                  </Pill>
                </span>
                <span className={styles.kvKey}>Skill id</span>
                <span className={styles.kvValue}>
                  <code>{detail.skill_id}</code>
                </span>
                <span className={styles.kvKey}>Version id</span>
                <span className={styles.kvValue}>
                  <code>{detail.skill_version_id}</code>
                </span>
                <span className={styles.kvKey}>Cost</span>
                <span className={styles.kvValue}>
                  {detail.total_cost_usd !== null
                    ? `$${detail.total_cost_usd.toFixed(4)}`
                    : "—"}
                </span>
                <span className={styles.kvKey}>Latency</span>
                <span className={styles.kvValue}>
                  {detail.total_latency_ms !== null
                    ? `${detail.total_latency_ms} ms`
                    : "—"}
                </span>
                <span className={styles.kvKey}>Started</span>
                <span className={styles.kvValue}>{detail.started_at}</span>
                <span className={styles.kvKey}>Finished</span>
                <span className={styles.kvValue}>
                  {detail.finished_at ?? "—"}
                </span>
                <span className={styles.kvKey}>User</span>
                <span className={styles.kvValue}>{detail.user_id}</span>
              </div>
              {detail.escalation_reason && (
                <p className={styles.kvValue}>
                  <strong>Escalation:</strong> {detail.escalation_reason}
                </p>
              )}
              {detail.error && (
                <p className={styles.formError}>
                  <strong>Error:</strong> {detail.error}
                </p>
              )}
            </section>

            <section className={styles.drawerSection}>
              <h3 className={styles.drawerSectionTitle}>
                Step results ({detail.step_results.length})
              </h3>
              {detail.step_results.length === 0 ? (
                <p className={styles.kvValue}>No step results recorded.</p>
              ) : (
                detail.step_results.map((step) => (
                  <div key={step.id} className={styles.step}>
                    <div className={styles.stepHeader}>
                      <span className={styles.stepName}>
                        #{step.step_index} · {step.step_name}
                      </span>
                      <Pill
                        variant={
                          step.validation_status === "valid"
                            ? "success"
                            : step.validation_status === "skipped"
                              ? "muted"
                              : "warning"
                        }
                      >
                        {step.validation_status}
                      </Pill>
                    </div>
                    <div className={styles.inlineMeta}>
                      <span>kind: {step.step_kind}</span>
                      {step.latency_ms !== null && (
                        <span>latency: {step.latency_ms} ms</span>
                      )}
                      {step.error && <span>error: {step.error}</span>}
                    </div>
                    {step.output && (
                      <details>
                        <summary style={{ cursor: "pointer", fontSize: "var(--text-small)", color: "var(--color-text-muted)" }}>
                          output
                        </summary>
                        <JsonViewer value={step.output} />
                      </details>
                    )}
                    {step.tool_calls && step.tool_calls.length > 0 && (
                      <details>
                        <summary style={{ cursor: "pointer", fontSize: "var(--text-small)", color: "var(--color-text-muted)" }}>
                          tool_calls ({step.tool_calls.length})
                        </summary>
                        <JsonViewer value={step.tool_calls} />
                      </details>
                    )}
                  </div>
                ))
              )}
            </section>

            <section className={styles.drawerSection}>
              <h3 className={styles.drawerSectionTitle}>state_object</h3>
              <JsonViewer value={detail.state_object} maxHeight={240} />
            </section>

            {detail.final_output && (
              <section className={styles.drawerSection}>
                <h3 className={styles.drawerSectionTitle}>final_output</h3>
                <JsonViewer value={detail.final_output} maxHeight={240} />
              </section>
            )}

            {divergence && (
              <section className={styles.drawerSection}>
                <h3 className={styles.drawerSectionTitle}>Shadow divergence</h3>
                <div className={styles.kv}>
                  <span className={styles.kvKey}>Agreement</span>
                  <span className={styles.kvValue}>
                    {divergence.overall_agreement.toFixed(3)}
                  </span>
                  <span className={styles.kvKey}>Flagged for evolution</span>
                  <span className={styles.kvValue}>
                    {divergence.flagged_for_evolution ? "yes" : "no"}
                  </span>
                  <span className={styles.kvKey}>Recorded</span>
                  <span className={styles.kvValue}>{divergence.created_at}</span>
                </div>
                {divergence.diff_summary && (
                  <JsonViewer value={divergence.diff_summary} />
                )}
              </section>
            )}

            <section className={styles.drawerSection}>
              <h3 className={styles.drawerSectionTitle}>Actions</h3>
              <div className={styles.actions}>
                <Button
                  onClick={handleCapture}
                  disabled={busy || detail.status !== "succeeded"}
                >
                  Capture fixture
                </Button>
              </div>
            </section>
          </>
        )}
      </div>
    </Drawer>
  );
}
