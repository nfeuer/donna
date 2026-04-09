import type { ReactNode } from "react";
import { Card } from "../primitives/Card";
import { Pill } from "../primitives/Pill";
import { Skeleton } from "../primitives/Skeleton";
import styles from "./ChartCard.module.css";

export interface ChartCardDelta {
  /** Signed percentage, e.g. -12 for "down 12%". */
  value: number;
  /** Human label such as "vs prior period". */
  label: string;
}

export interface ChartCardStat {
  label: string;
  value: ReactNode;
}

interface ChartCardProps {
  eyebrow: string;
  metric: ReactNode;
  /** Optional small suffix rendered inline after the metric (e.g. "ms", "%"). */
  metricSuffix?: string;
  delta?: ChartCardDelta;
  chart?: ReactNode;
  stats?: ChartCardStat[];
  /** Escape hatch rendered below the stat strip, inside the card. */
  children?: ReactNode;
  /** When true, chart + stats + children collapse to Skeletons. */
  loading?: boolean;
  /** Applied to the root Card element. */
  className?: string;
}

/**
 * Canonical dashboard card. Eyebrow + Fraunces headline metric +
 * optional delta pill + chart slot + optional stat strip + optional
 * children escape hatch. When loading, the chart and body areas are
 * replaced by Skeletons that match the real layout's density.
 *
 * All five Wave 4 dashboard cards compose from this.
 */
export function ChartCard({
  eyebrow,
  metric,
  metricSuffix,
  delta,
  chart,
  stats,
  children,
  loading,
  className,
}: ChartCardProps) {
  const deltaVariant = delta
    ? delta.value > 0
      ? "success"
      : delta.value < 0
        ? "error"
        : "muted"
    : "muted";

  const deltaLabel = delta
    ? `${delta.value > 0 ? "+" : ""}${delta.value.toFixed(0)}% ${delta.label}`
    : null;

  return (
    <Card className={className}>
      <div className={styles.card}>
        <header className={styles.header}>
          <div className={styles.headlineLeft}>
            <div className={styles.eyebrow}>{eyebrow}</div>
            <p className={styles.metric}>
              {loading ? <Skeleton width={140} height={44} /> : metric}
              {metricSuffix && !loading && (
                <span className={styles.metricSuffix}>{metricSuffix}</span>
              )}
            </p>
          </div>
          {deltaLabel && !loading && (
            <Pill variant={deltaVariant} className={styles.delta}>
              {deltaLabel}
            </Pill>
          )}
        </header>

        {chart && (
          <div className={styles.chart}>
            {loading ? <Skeleton className={styles.skeletonChart} /> : chart}
          </div>
        )}

        {stats && stats.length > 0 && (
          <dl className={styles.stats}>
            {stats.map((s) => (
              <div key={s.label} className={styles.statItem}>
                <dt className={styles.statLabel}>{s.label}</dt>
                <dd className={styles.statValue}>
                  {loading ? <Skeleton width={60} height={14} /> : s.value}
                </dd>
              </div>
            ))}
          </dl>
        )}

        {children && <div className={styles.extra}>{children}</div>}
      </div>
    </Card>
  );
}
