import type { ReactNode } from "react";
import styles from "./PageHeader.module.css";

interface PageHeaderProps {
  eyebrow?: string;
  title: string;
  meta?: ReactNode;
  actions?: ReactNode;
}

export function PageHeader({ eyebrow, title, meta, actions }: PageHeaderProps) {
  return (
    <header className={styles.root}>
      <div className={styles.left}>
        {eyebrow && <div className={styles.eyebrow}>{eyebrow}</div>}
        <h1 className={styles.title}>{title}</h1>
        {meta && <div className={styles.meta}>{meta}</div>}
      </div>
      {actions && <div className={styles.actions}>{actions}</div>}
    </header>
  );
}
