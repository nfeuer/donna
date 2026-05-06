import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { ArrowLeft, Copy } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import { PageHeader } from "../../primitives/PageHeader";
import { Pill, type PillVariant } from "../../primitives/Pill";
import { Button } from "../../primitives/Button";
import { EmptyState } from "../../primitives/EmptyState";
import { Skeleton } from "../../primitives/Skeleton";
import {
  fetchEscalationDetail,
  fetchEscalationTimeline,
  markEscalationMerged,
  type EscalationDetailResponse,
  type EscalationStatus,
  type EscalationTimelineEvent,
} from "../../api/escalations";
import RefreshButton from "../../components/RefreshButton";
import MarkAsBuiltModal from "./MarkAsBuiltModal";
import styles from "./Escalations.module.css";

const STATUS_VARIANT: Record<EscalationStatus, PillVariant> = {
  open: "warning",
  resolved: "accent",
  submitted: "accent",
  validated: "success",
  failed: "error",
  cancelled: "muted",
};

// Spec §6.3(b) + §6.1: re-submit affordance lives "within iteration cap".
// Mirrors the backend constant; slice 23 will source this from
// dashboard_setting / config.
const MANUAL_ITERATION_LIMIT = 3;

function formatTs(ts: string | null): string {
  if (!ts) return "—";
  return ts.replace("T", " ").substring(0, 19);
}

function MetaItem({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className={styles.metaItem}>
      <span className={styles.metaLabel}>{label}</span>
      <span className={styles.metaValue}>{value}</span>
    </div>
  );
}

function ContextRow({
  label,
  value,
  onCopy,
}: {
  label: string;
  value: string;
  onCopy: () => void;
}) {
  return (
    <>
      <span className={styles.muted}>{label}</span>
      <span className={styles.worktreeCommand}>
        <code style={{ flex: 1 }}>{value}</code>
        <Button variant="ghost" size="sm" onClick={onCopy}>
          <Copy size={12} />
        </Button>
      </span>
    </>
  );
}

// Slice 24 — task_type drives the badge so tool-gap lifecycle events
// (slice 22) read distinctly from escalation lifecycle events (slice 17).
function timelineVariant(taskType: string | undefined): PillVariant {
  if (taskType === "tool_gap_lifecycle") return "accent";
  return "muted";
}

function TimelineRow({ event }: { event: EscalationTimelineEvent }) {
  const eventName = event.event ?? "(unknown event)";
  const payload = { ...event.payload };
  if ("event" in payload) delete (payload as { event?: unknown }).event;
  const showPayload = Object.keys(payload).length > 0;
  return (
    <li className={styles.timelineItem}>
      <div className={styles.timelineEvent}>
        <Pill variant={timelineVariant(event.task_type)}>{eventName}</Pill>
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

// Slice 24 — 30s interval matches the dashboard convention
// (`docs/domain/management-gui.md`).
const TIMELINE_POLL_INTERVAL_MS = 30_000;

export default function EscalationDetail() {
  const { correlation_id: correlationId } = useParams<{ correlation_id: string }>();
  const navigate = useNavigate();

  const [detail, setDetail] = useState<EscalationDetailResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copyState, setCopyState] = useState<string>("");
  const [markBuiltOpen, setMarkBuiltOpen] = useState(false);
  const [mergeBusy, setMergeBusy] = useState(false);
  // Slice 24 — append-only timeline poll. Stores the next cursor so
  // each tick only pulls newly-landed audit rows.
  const [timelineAfterId, setTimelineAfterId] = useState<string | null>(null);

  const doFetch = useCallback(async () => {
    if (!correlationId) return;
    setLoading(true);
    setError(null);
    try {
      const data = await fetchEscalationDetail(correlationId);
      setDetail(data);
      const lastId = data.timeline.length
        ? data.timeline[data.timeline.length - 1].id
        : null;
      setTimelineAfterId(lastId);
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

  // Slice 24 (spec §10.10) — poll the dedicated timeline endpoint so the
  // panel reflects new lifecycle / tool-gap events without refetching the
  // full detail blob. Append-only so the visible scroll position is
  // preserved across ticks.
  useEffect(() => {
    if (!correlationId || !detail) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const resp = await fetchEscalationTimeline(
          correlationId,
          timelineAfterId,
        );
        if (cancelled || resp.timeline.length === 0) return;
        setDetail((prev) =>
          prev
            ? { ...prev, timeline: [...prev.timeline, ...resp.timeline] }
            : prev,
        );
        if (resp.next_after_id) setTimelineAfterId(resp.next_after_id);
      } catch {
        // Silent — the global axios interceptor surfaces hard errors.
      }
    };
    const handle = window.setInterval(tick, TIMELINE_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
  }, [correlationId, detail, timelineAfterId]);

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

  const handleCopyText = useCallback(async (text: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopyState(`${label} copied`);
      setTimeout(() => setCopyState(""), 1500);
    } catch {
      setCopyState("Copy failed");
    }
  }, []);

  const handleMarkMerged = useCallback(async () => {
    if (!correlationId) return;
    setMergeBusy(true);
    try {
      await markEscalationMerged(correlationId);
      await doFetch();
    } finally {
      setMergeBusy(false);
    }
  }, [correlationId, doFetch]);

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
        <div className={styles.detailLoadingPanels}>
          <Skeleton width="100%" height={420} />
          <Skeleton width="100%" height={420} />
        </div>
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

  // The submission section is the slot where slices 20 (chat textarea)
  // and 21 (claude_code "Mark as built" modal) attach their controls.
  // Slice 19 only renders this section when the row is actually awaiting
  // input — so users on a submitted/validated/cancelled row don't see
  // a placeholder for tools that wouldn't help them.
  const awaitingSubmission =
    escalation.status === "resolved" ||
    (escalation.status === "failed" && escalation.iteration < MANUAL_ITERATION_LIMIT);
  const iterationCapped =
    escalation.status === "failed" && escalation.iteration >= MANUAL_ITERATION_LIMIT;

  const hasValidationContext =
    escalation.status !== "open" && escalation.status !== "resolved";

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
            <RefreshButton onRefresh={doFetch} autoRefreshMs={30_000} />
          </div>
        }
      />

      <div className={styles.detailLayout}>
        <div className={styles.panel}>
          {escalation.human_review && (
            <div className={styles.humanReviewBanner}>
              Needs human review — iteration cap was reached. Edit the
              row in the database or open a follow-up escalation manually.
            </div>
          )}
          {escalation.status === "validated" &&
            !escalation.merged_at &&
            escalation.branch_name && (
              <div className={styles.readyToMerge}>
                <div>
                  <strong>Validated.</strong> Skill is in sandbox. Merge
                  when ready:
                </div>
                <code className={styles.mergeCmd}>
                  git checkout main && git merge --no-ff{" "}
                  {escalation.branch_name} && git push
                </code>
                <div>
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={handleMarkMerged}
                    disabled={mergeBusy}
                  >
                    {mergeBusy ? "Marking…" : "Mark as merged"}
                  </Button>
                </div>
              </div>
            )}
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

          {awaitingSubmission && escalation.mode === "claude_code" && (
            <>
              <div className={styles.panelSubheader}>
                <h2 className={styles.panelTitle}>Build & submit</h2>
              </div>
              <div className={styles.markBuiltSection}>
                {escalation.target_paths && (
                  <div>
                    <div className={styles.muted}>Target paths</div>
                    <div className={styles.targetPathsGrid}>
                      {Object.entries(escalation.target_paths).map(([k, v]) => (
                        <ContextRow
                          key={k}
                          label={k}
                          value={v}
                          onCopy={() => handleCopyText(v, k)}
                        />
                      ))}
                    </div>
                  </div>
                )}
                {escalation.branch_name && (
                  <ContextRow
                    label="Branch"
                    value={escalation.branch_name}
                    onCopy={() =>
                      handleCopyText(escalation.branch_name ?? "", "Branch")
                    }
                  />
                )}
                {escalation.base_sha && (
                  <ContextRow
                    label="Base SHA"
                    value={escalation.base_sha}
                    onCopy={() =>
                      handleCopyText(escalation.base_sha ?? "", "Base SHA")
                    }
                  />
                )}
                <div>
                  <Button
                    variant="primary"
                    onClick={() => setMarkBuiltOpen(true)}
                  >
                    Mark as built
                  </Button>
                </div>
              </div>
            </>
          )}

          {awaitingSubmission && escalation.mode !== "claude_code" && (
            <>
              <div className={styles.panelSubheader}>
                <h2 className={styles.panelTitle}>Submission</h2>
              </div>
              {/*
                Slot for slice 20 (chat textarea). Other modes still
                fall through to this placeholder until their slice lands.
              */}
              <div className={styles.submissionLocked}>
                Awaiting your submission. The submission UI for{" "}
                <code>{escalation.mode ?? "this mode"}</code> ships in the
                next slice.
              </div>
            </>
          )}

          {iterationCapped && (
            <>
              <div className={styles.panelSubheader}>
                <h2 className={styles.panelTitle}>Submission</h2>
              </div>
              <div className={styles.submissionLocked}>
                Iteration cap of {MANUAL_ITERATION_LIMIT} reached. Cancel
                this escalation or escalate to human review.
              </div>
            </>
          )}

          {hasValidationContext && (
            <>
              <div className={styles.panelSubheader}>
                <h2 className={styles.panelTitle}>Validation</h2>
              </div>
              {escalation.validation_result ? (
                <pre className={styles.promptBlock}>
                  {JSON.stringify(escalation.validation_result, null, 2)}
                </pre>
              ) : (
                <div className={styles.detailEmpty}>
                  No validation result recorded yet.
                </div>
              )}
            </>
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

          <div className={styles.panelSubheader}>
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
      <MarkAsBuiltModal
        correlationId={escalation.correlation_id}
        defaultBranch={escalation.branch_name}
        open={markBuiltOpen}
        onOpenChange={setMarkBuiltOpen}
        onSubmitted={doFetch}
      />
    </div>
  );
}
