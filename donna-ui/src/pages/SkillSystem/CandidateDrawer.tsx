import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Drawer } from "../../primitives/Drawer";
import { Button } from "../../primitives/Button";
import { Pill } from "../../primitives/Pill";
import { Skeleton } from "../../primitives/Skeleton";
import {
  dismissCandidate,
  draftCandidateNow,
  fetchSkillCandidates,
  type SkillCandidate,
} from "../../api/skillSystem";
import styles from "./SkillSystem.module.css";

interface Props {
  candidateId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onMutated: () => void;
}

export default function CandidateDrawer({
  candidateId,
  open,
  onOpenChange,
  onMutated,
}: Props) {
  const [row, setRow] = useState<SkillCandidate | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      if (!open || !candidateId) {
        setRow(null);
        return;
      }
      setLoading(true);
      try {
        // No per-id endpoint; fetch a wide list and filter client-side.
        const resp = await fetchSkillCandidates({ status: "", limit: 500 });
        if (cancelled) return;
        setRow(resp.candidates.find((c) => c.id === candidateId) ?? null);
      } catch {
        if (!cancelled) setRow(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [open, candidateId]);

  const handleDismiss = async () => {
    if (!candidateId) return;
    setBusy(true);
    try {
      await dismissCandidate(candidateId);
      toast.success("Candidate dismissed");
      onMutated();
      onOpenChange(false);
    } catch {
      toast.error("Dismiss failed");
    } finally {
      setBusy(false);
    }
  };

  const handleDraftNow = async () => {
    if (!candidateId) return;
    setBusy(true);
    try {
      const resp = await draftCandidateNow(candidateId);
      toast.success("Draft scheduled", {
        description: `manual_draft_at=${resp.manual_draft_at}`,
      });
      onMutated();
      onOpenChange(false);
    } catch (err: unknown) {
      const msg =
        typeof err === "object" &&
        err !== null &&
        "response" in err &&
        (err as { response?: { data?: { detail?: string } } }).response?.data
          ?.detail
          ? (err as { response: { data: { detail: string } } }).response.data
              .detail
          : "Draft-now failed";
      toast.error(msg);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      title={row ? (row.capability_name ?? "Candidate") : "Candidate"}
    >
      <div className={styles.drawerBody}>
        {loading && !row ? (
          <Skeleton width="100%" height={160} />
        ) : !row ? (
          <p className={styles.kvValue}>Candidate not found.</p>
        ) : (
          <>
            <section className={styles.drawerSection}>
              <h3 className={styles.drawerSectionTitle}>Details</h3>
              <div className={styles.kv}>
                <span className={styles.kvKey}>Status</span>
                <span className={styles.kvValue}>
                  <Pill variant="accent">{row.status}</Pill>
                </span>
                <span className={styles.kvKey}>Capability</span>
                <span className={styles.kvValue}>
                  {row.capability_name ?? "—"}
                </span>
                <span className={styles.kvKey}>Task pattern hash</span>
                <span className={styles.kvValue}>
                  {row.task_pattern_hash ?? "—"}
                </span>
                <span className={styles.kvKey}>Expected savings</span>
                <span className={styles.kvValue}>
                  ${row.expected_savings_usd.toFixed(2)}
                </span>
                <span className={styles.kvKey}>Volume (30d)</span>
                <span className={styles.kvValue}>{row.volume_30d}</span>
                <span className={styles.kvKey}>Variance</span>
                <span className={styles.kvValue}>
                  {row.variance_score !== null
                    ? row.variance_score.toFixed(3)
                    : "—"}
                </span>
                <span className={styles.kvKey}>Reported</span>
                <span className={styles.kvValue}>{row.reported_at}</span>
                <span className={styles.kvKey}>Resolved</span>
                <span className={styles.kvValue}>
                  {row.resolved_at ?? "—"}
                </span>
              </div>
            </section>
            <section className={styles.drawerSection}>
              <h3 className={styles.drawerSectionTitle}>Actions</h3>
              <div className={styles.actions}>
                <Button
                  onClick={handleDraftNow}
                  disabled={busy || row.status !== "new"}
                >
                  Draft now
                </Button>
                <Button
                  variant="ghost"
                  onClick={handleDismiss}
                  disabled={busy || row.status === "dismissed"}
                >
                  Dismiss
                </Button>
              </div>
            </section>
          </>
        )}
      </div>
    </Drawer>
  );
}
