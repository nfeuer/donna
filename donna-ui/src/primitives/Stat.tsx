import type { ReactNode } from "react";
import { cn } from "../lib/cn";
import styles from "./Stat.module.css";

interface StatProps {
  eyebrow: string;
  value: string | number;
  suffix?: string;
  sub?: ReactNode;
  /** true to render in --color-text instead of --color-accent */
  plain?: boolean;
}

/**
 * Single headline metric: eyebrow + Fraunces display value + optional subline.
 * Used inside ChartCards (Wave 3) and as standalone dashboard stats.
 */
export function Stat({ eyebrow, value, suffix, sub, plain }: StatProps) {
  return (
    <div className={styles.root}>
      <div className={styles.eyebrow}>{eyebrow}</div>
      <p className={cn(styles.value, plain && styles.plain)}>
        {value}
        {suffix && <span className={styles.suffix}>{suffix}</span>}
      </p>
      {sub && <div className={styles.sub}>{sub}</div>}
    </div>
  );
}
