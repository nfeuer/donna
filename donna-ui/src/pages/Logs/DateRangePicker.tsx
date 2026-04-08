import { useId } from "react";
import styles from "./DateRangePicker.module.css";

export interface DateRangeValue {
  start: string | null; // ISO string or null
  end: string | null;
}

interface Props {
  value: DateRangeValue;
  onChange: (next: DateRangeValue) => void;
}

/**
 * Two-field datetime-local range picker. The native input format is
 * "YYYY-MM-DDTHH:mm" — we normalise to full ISO strings on the way out
 * so the /admin/logs API contract (ISO 8601) stays intact.
 */
export function DateRangePicker({ value, onChange }: Props) {
  const startId = useId();
  const endId = useId();

  const toLocalInput = (iso: string | null): string => {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };

  const fromLocalInput = (v: string): string | null => {
    if (!v) return null;
    const d = new Date(v);
    return Number.isNaN(d.getTime()) ? null : d.toISOString();
  };

  return (
    <div className={styles.root}>
      <label htmlFor={startId} className={styles.label}>
        From
      </label>
      <input
        id={startId}
        type="datetime-local"
        className={styles.input}
        value={toLocalInput(value.start)}
        onChange={(e) => onChange({ ...value, start: fromLocalInput(e.target.value) })}
        aria-label="Start time"
      />
      <span className={styles.separator} aria-hidden="true">
        →
      </span>
      <label htmlFor={endId} className={styles.label}>
        To
      </label>
      <input
        id={endId}
        type="datetime-local"
        className={styles.input}
        value={toLocalInput(value.end)}
        onChange={(e) => onChange({ ...value, end: fromLocalInput(e.target.value) })}
        aria-label="End time"
      />
      {(value.start || value.end) && (
        <button
          type="button"
          className={styles.clear}
          onClick={() => onChange({ start: null, end: null })}
          aria-label="Clear date range"
        >
          Clear
        </button>
      )}
    </div>
  );
}
