import type { CSSProperties } from "react";
import styles from "./JsonViewer.module.css";

interface JsonViewerProps {
  value: unknown;
  maxHeight?: number | string;
}

/**
 * Read-only JSON payload renderer. Uses `JSON.stringify(value, null, 2)` —
 * collapsible-tree UI is deferred until a real need appears. Renders "—" for
 * null / undefined so callers don't have to special-case empty payloads.
 */
export function JsonViewer({ value, maxHeight = 320 }: JsonViewerProps) {
  if (value === null || value === undefined) {
    return <span className={styles.empty}>—</span>;
  }
  const style: CSSProperties = {
    maxHeight: typeof maxHeight === "number" ? `${maxHeight}px` : maxHeight,
  };
  let text: string;
  try {
    text = JSON.stringify(value, null, 2);
  } catch {
    text = String(value);
  }
  return (
    <pre className={styles.pre} style={style}>
      {text}
    </pre>
  );
}
