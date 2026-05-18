import { useState, useEffect } from "react";
import { fetchClaudePayload, type ClaudePayload, type ClaudeCall } from "../../api/claude";
import { Skeleton } from "../../primitives/Skeleton";
import styles from "./claude-inspector.module.css";

interface Props {
  callA: ClaudeCall;
  callB: ClaudeCall;
}

export default function CallCompare({ callA, callB }: Props) {
  const [payloadA, setPayloadA] = useState<ClaudePayload | null>(null);
  const [payloadB, setPayloadB] = useState<ClaudePayload | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    const fetchA = callA.has_payload
      ? fetchClaudePayload(callA.id).catch(() => null)
      : Promise.resolve(null);
    const fetchB = callB.has_payload
      ? fetchClaudePayload(callB.id).catch(() => null)
      : Promise.resolve(null);

    Promise.all([fetchA, fetchB]).then(([a, b]) => {
      if (!cancelled) {
        setPayloadA(a);
        setPayloadB(b);
        setLoading(false);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [callA.id, callA.has_payload, callB.id, callB.has_payload]);

  if (loading) {
    return (
      <div className={styles.compareGrid}>
        <div className={styles.comparePane}>
          <Skeleton width={180} height={12} />
          <Skeleton width="100%" height={200} />
        </div>
        <div className={styles.comparePane}>
          <Skeleton width={180} height={12} />
          <Skeleton width="100%" height={200} />
        </div>
      </div>
    );
  }

  const truncateId = (id: string) => id.slice(0, 12) + "...";

  return (
    <div className={styles.compareGrid}>
      <div className={styles.comparePane}>
        <div className={styles.comparePaneHeader}>
          {truncateId(callA.id)}
        </div>
        <div className={styles.comparePaneMeta}>
          <span>{callA.task_type}</span>
          <span>{callA.model_alias}</span>
          <span>${callA.cost_usd.toFixed(4)}</span>
        </div>
        {payloadA ? (
          <pre className={styles.json}>
            {JSON.stringify(payloadA.request, null, 2)}
          </pre>
        ) : (
          <div className={styles.evictedMsg}>Payload not available</div>
        )}
      </div>

      <div className={styles.comparePane}>
        <div className={styles.comparePaneHeader}>
          {truncateId(callB.id)}
        </div>
        <div className={styles.comparePaneMeta}>
          <span>{callB.task_type}</span>
          <span>{callB.model_alias}</span>
          <span>${callB.cost_usd.toFixed(4)}</span>
        </div>
        {payloadB ? (
          <pre className={styles.json}>
            {JSON.stringify(payloadB.request, null, 2)}
          </pre>
        ) : (
          <div className={styles.evictedMsg}>Payload not available</div>
        )}
      </div>
    </div>
  );
}
