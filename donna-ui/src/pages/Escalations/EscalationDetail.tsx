import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, Copy } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import { PageHeader } from "../../primitives/PageHeader";
import { Pill, type PillVariant } from "../../primitives/Pill";
import { Button } from "../../primitives/Button";
import { EmptyState } from "../../primitives/EmptyState";
import { Skeleton } from "../../primitives/Skeleton";
import {
  fetchEscalationDetail,
  type EscalationDetailResponse,
  type EscalationStatus,
  type EscalationTimelineEvent,
} from "../../api/escalations";
import RefreshButton from "../../components/RefreshButton";
import styles from "./Escalations.module.css";

const STATUS_VARIANT: Record<EscalationStatus, PillVariant> = {
  open: "warning",
  resolved: "accent",
  submitted: "accent",
  validated: "success",
  failed: "error",
  cancelled: "muted",
};

function formatTs(ts: string | null): string {
  if (!ts) return "—";
  return ts.replace("T", " ").substring(0, 19);
}

function MetaItem({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className={styles.metaItem}>
      <span className={styles.metaLabel}>{label}</span>
      <span className={styles.metaValue}>{value}</span>
    </div>
  );
}

function TimelineRow({ event }: { event: EscalationTimelineEvent }) {
  const eventName = event.event ?? "(unknown event)";
  const payload = { ...event.payload };
  if ("event" in payload) delete (payload as { event?: unknown }).event;
  const showPayload = Object.keys(payload).length > 0;
  return (
    <li className={styles.timelineItem}>
      <div className={styles.timelineEvent}>
        <Pill variant="muted">{eventName}</Pill>
        <span className={styles.muted}>{formatTs(event.timestamp)}</span>
      </div>
      {showPayload && (
        <pre className={styles.timelinePayload}>
          {JSON.stringify(payload, null, 2)}
        </pre>
      )}
    </li>
  );
}

export default function EscalationDetail() {
  const { correlation_id: correlationId } = useParams<{ correlation_id: string }>();
  const navigate = useNavigate();

  const [detail, setDetail] = useState<EscalationDetailResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copyState, setCopyState] = useState<string>("");

  const doFetch = useCallback(async () => {
    if (!correlationId) return;
    setLoading(true);
    setError(null);
    try {
      const data = await fetchEscalationDetail(correlationId);
      setDetail(data);
    } catch (err) {
      setDetail(null);
      const msg = err instanceof Error ? err.message : "Failed to load";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [correlationId]);

  useEffect(() => {
    doFetch();
  }, [doFetch]);

  const handleCopyPrompt = useCallback(async () => {
    if (!detail?.escalation.prompt_body) return;
    try {
      await navigator.clipboard.writeText(detail.escalation.prompt_body);
      setCopyState("Copied");
      setTimeout(() => setCopyState(""), 1500);
    } catch {
      setCopyState("Copy failed");
    }
  }, [detail]);

  if (!correlationId) {
    return (
      <EmptyState
        title="Missing correlation_id"
        body="The URL must include a correlation_id segment."
      />
    );
  }

  if (loading && !detail) {
    return (
      <div>
        <PageHeader
          eyebrow="Escalations"
          title="Loading…"
          actions={
            <Button variant="ghost" onClick={() => navigate("/escalations")}>
              <ArrowLeft size={14} /> Back
            </Button>
          }
        />
        <Skeleton width="100%" height={420} />
      </div>
    );
  }

  if (error || !detail) {
    return (
      <div>
        <PageHeader
          eyebrow="Escalations"
          title="Unable to load escalation"
          actions={
            <Button variant="ghost" onClick={() => navigate("/escalations")}>
              <ArrowLeft size={14} /> Back
            </Button>
          }
        />
        <EmptyState
          title={error ? "Error" : "Not found"}
          body={error ?? "No escalation matches this correlation_id."}
        />
      </div>
    );
  }

  const { escalation, timeline } = detail;
  const statusVariant = STATUS_VARIANT[escalation.status] ?? "muted";

  return (
    <div>
      <PageHeader
        eyebrow="Escalations"
        title={escalation.task_type}
        meta={
          <div className={styles.detailHeaderRow}>
            <Pill variant={statusVariant}>{escalation.status}</Pill>
            {escalation.mode && (
              <Pill variant="muted">{escalation.mode}</Pill>
            )}
            <span className={styles.muted}>{escalation.correlation_id}</span>
          </div>
        }
        actions={
          <div className={styles.filters}>
            <Button variant="ghost" onClick={() => navigate("/escalations")}>
              <ArrowLeft size={14} /> Back
            </Button>
            <RefreshButton onRefresh={doFetch} />
          </div>
        }
      />

      <div className={styles.detailLayout}>
        <div className={styles.panel}>
          <div className={styles.panelHeader}>
            <h2 className={styles.panelTitle}>Prompt</h2>
            <div className={styles.actionRow}>
              {copyState && (
                <span className={styles.copyState}>{copyState}</span>
              )}
              <Button
                variant="ghost"
                size="sm"
                onClick={handleCopyPrompt}
                disabled={!escalation.prompt_body}
              >
                <Copy size={14} /> Copy prompt
              </Button>
            </div>
          </div>
          {escalation.prompt_body ? (
            <div className={styles.promptBlock}>{escalation.prompt_body}</div>
          ) : (
            <div className={styles.detailEmpty}>
              No prompt body recorded for this escalation yet.
            </div>
          )}

          {/*
            Submission UI is mode-agnostic in slice 19 — slices 20 (chat textarea)
            and 21 (claude_code "Mark as built" modal) attach the actual controls
            to this panel using the /admin/escalations/{id}/submit endpoint.
          */}
          <div className={styles.panelHeader} style={{ marginTop: "var(--space-4)" }}>
            <h2 className={styles.panelTitle}>Submission</h2>
          </div>
          <div className={styles.submissionLocked}>
            Mode-specific submission UI lands in slice 20 (chat textarea) and slice
            21 ("Mark as built" modal). The submit endpoint already accepts the
            payload defined in <code>schemas/escalation_submission.json</code>.
          </div>

          <div className={styles.panelHeader} style={{ marginTop: "var(--space-4)" }}>
            <h2 className={styles.panelTitle}>Validation</h2>
          </div>
          {escalation.status === "open" || escalation.status === "resolved" ? (
            <div className={styles.detailEmpty}>
              Validation runs after submission.
            </div>
          ) : escalation.validation_result ? (
            <pre className={styles.promptBlock}>
              {JSON.stringify(escalation.validation_result, null, 2)}
            </pre>
          ) : (
            <div className={styles.detailEmpty}>
              No validation result recorded yet.
            </div>
          )}
        </div>

        <div className={styles.panel}>
          <div className={styles.panelHeader}>
            <h2 className={styles.panelTitle}>Details</h2>
          </div>
          <div className={styles.metaGrid}>
            <MetaItem label="Estimate" value={`$${escalation.estimate_usd.toFixed(4)}`} />
            <MetaItem
              label="Daily remaining"
              value={`$${escalation.daily_remaining_usd.toFixed(4)}`}
            />
            <MetaItem label="Iteration" value={escalation.iteration} />
            <MetaItem label="Priority" value={escalation.priority} />
            <MetaItem
              label="Offered modes"
              value={escalation.offered_modes.join(", ") || "—"}
            />
            <MetaItem label="Resolution" value={escalation.resolution ?? "—"} />
            <MetaItem label="Branch" value={escalation.branch_name ?? "—"} />
            <MetaItem label="User" value={escalation.user_id} />
            <MetaItem label="Task ID" value={escalation.task_id ?? "—"} />
            <MetaItem label="Created" value={formatTs(escalation.created_at)} />
            <MetaItem label="Resolved" value={formatTs(escalation.resolved_at)} />
            <MetaItem label="Submitted" value={formatTs(escalation.submitted_at)} />
            <MetaItem label="Validated" value={formatTs(escalation.validated_at)} />
          </div>

          <div className={styles.panelHeader} style={{ marginTop: "var(--space-4)" }}>
            <h2 className={styles.panelTitle}>Timeline</h2>
            <span className={styles.muted}>{timeline.length} event(s)</span>
          </div>
          {timeline.length === 0 ? (
            <div className={styles.timelineEmpty}>
              No lifecycle events have been recorded yet.
            </div>
          ) : (
            <ul className={styles.timelineList}>
              {timeline.map((evt) => (
                <TimelineRow key={evt.id} event={evt} />
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
