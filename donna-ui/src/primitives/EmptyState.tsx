import type { ReactNode } from "react";
import styles from "./EmptyState.module.css";

interface EmptyStateProps {
  eyebrow?: string;
  title: string;
  body?: ReactNode;
  actions?: ReactNode;
}

/**
 * Distinctive empty state — left-border accent, Fraunces title,
 * instructive-plus-personality voice. See spec §5 Voice.
 */
export function EmptyState({ eyebrow = "Nothing here", title, body, actions }: EmptyStateProps) {
  return (
    <div className={styles.root}>
      <div className={styles.eyebrow}>{eyebrow}</div>
      <h3 className={styles.title}>{title}</h3>
      {body && <p className={styles.body}>{body}</p>}
      {actions && <div className={styles.actions}>{actions}</div>}
    </div>
  );
}
