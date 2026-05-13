import type { ContextStatus } from "../../api/chat";
import styles from "./Chat.module.css";

interface Props {
  status: ContextStatus | null;
}

export default function ContextMeter({ status }: Props) {
  if (!status || status.max_tokens === 0) return null;

  const ratio = status.used_tokens / status.max_tokens;
  const compactRatio = status.compact_threshold / status.max_tokens;
  const pct = Math.min(ratio * 100, 100);
  const compactPct = compactRatio * 100;

  let barClass = styles.meterBarAccent;
  if (ratio >= 0.9) barClass = styles.meterBarError;
  else if (ratio >= 0.7) barClass = styles.meterBarWarning;

  return (
    <div className={styles.contextMeter}>
      <div className={styles.meterLabels}>
        <span>
          Context: {status.used_tokens.toLocaleString()} /{" "}
          {status.max_tokens.toLocaleString()}
        </span>
        <span>Compacts at {status.compact_threshold.toLocaleString()}</span>
      </div>
      <div className={styles.meterTrack}>
        <div className={barClass} style={{ width: `${pct}%` }} />
        <div className={styles.meterCompactMark} style={{ left: `${compactPct}%` }} />
      </div>
    </div>
  );
}
