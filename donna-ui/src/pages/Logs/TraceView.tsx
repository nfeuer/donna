import { useEffect, useState } from "react";
import { Drawer } from "../../primitives/Drawer";
import { Pill } from "../../primitives/Pill";
import { ScrollArea } from "../../primitives/ScrollArea";
import { Skeleton } from "../../primitives/Skeleton";
import { fetchTrace, type LogEntry } from "../../api/logs";
import { levelToPillVariant } from "./levelStyles";
import { formatTimestamp } from "./LogTable";
import styles from "./TraceView.module.css";

interface Props {
  correlationId: string | null;
  onClose: () => void;
}

/**
 * Right-side drawer showing every log entry that shares a
 * correlation ID, rendered as a vertical timeline. Falls back to
 * skeletons while loading and an empty hint if the trace is empty.
 */
export default function TraceView({ correlationId, onClose }: Props) {
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [source, setSource] = useState("");

  useEffect(() => {
    if (!correlationId) {
      setEntries([]);
      return;
    }
    setLoading(true);
    fetchTrace(correlationId)
      .then((resp) => {
        setEntries(resp?.entries ?? []);
        setSource(resp?.source ?? "");
      })
      .catch(() => setEntries([]))
      .finally(() => setLoading(false));
  }, [correlationId]);

  const totalDurationMs =
    entries.length >= 2
      ? new Date(entries[entries.length - 1].timestamp).getTime() -
        new Date(entries[0].timestamp).getTime()
      : 0;

  return (
    <Drawer
      open={!!correlationId}
      onOpenChange={(open) => !open && onClose()}
      title={correlationId ? `Trace · ${correlationId.slice(0, 12)}…` : "Trace"}
    >
      <dl className={styles.summary}>
        <div className={styles.summaryItem}>
          <dt>Correlation</dt>
          <dd className={styles.mono}>{correlationId ?? "—"}</dd>
        </div>
        <div className={styles.summaryItem}>
          <dt>Events</dt>
          <dd>{entries.length}</dd>
        </div>
        <div className={styles.summaryItem}>
          <dt>Duration</dt>
          <dd>{totalDurationMs > 0 ? `${totalDurationMs} ms` : "—"}</dd>
        </div>
        <div className={styles.summaryItem}>
          <dt>Source</dt>
          <dd>
            <Pill variant="muted">{source || "—"}</Pill>
          </dd>
        </div>
      </dl>

      {loading ? (
        <div className={styles.loading}>
          <Skeleton height={14} />
          <Skeleton height={14} />
          <Skeleton height={14} />
        </div>
      ) : entries.length === 0 ? (
        <div className={styles.emptyHint}>No events recorded for this trace.</div>
      ) : (
        <ol className={styles.timeline}>
          {entries.map((entry, idx) => (
            <li key={`${entry.timestamp}-${idx}`} className={styles.timelineItem}>
              <span className={styles.timelineDot} aria-hidden="true" />
              <div className={styles.timelineBody}>
                <div className={styles.timelineHeader}>
                  <Pill variant={levelToPillVariant(entry.level)}>
                    {entry.level?.toUpperCase() ?? "—"}
                  </Pill>
                  <span className={styles.eventName}>{entry.event_type}</span>
                  {entry.service && <span className={styles.dim}>{entry.service}</span>}
                </div>
                <div className={styles.message}>{entry.message || "—"}</div>
                <div className={styles.metaRow}>
                  <span className={styles.mono}>{formatTimestamp(entry.timestamp)}</span>
                  {entry.duration_ms != null && (
                    <span className={styles.dim}>{entry.duration_ms} ms</span>
                  )}
                  {entry.cost_usd != null && (
                    <span className={styles.dim}>${entry.cost_usd.toFixed(4)}</span>
                  )}
                </div>
                {entry.extra && Object.keys(entry.extra).length > 0 && (
                  <ScrollArea className={styles.extra} style={{ maxHeight: 200 }}>
                    <pre className={styles.pre}>
                      {JSON.stringify(entry.extra, null, 2)}
                    </pre>
                  </ScrollArea>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </Drawer>
  );
}
