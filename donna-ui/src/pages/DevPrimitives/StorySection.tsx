import type { ReactNode } from "react";
import styles from "./DevPrimitives.module.css";

interface StorySectionProps {
  id: string;
  eyebrow: string;
  title: string;
  note?: string;
  children: ReactNode;
}

/**
 * Layout wrapper for a single primitive story.
 * Each primitive task appends one <StorySection> to the dev page.
 */
export function StorySection({ id, eyebrow, title, note, children }: StorySectionProps) {
  return (
    <section id={id} className={styles.section} data-testid={`story-${id}`}>
      <div className={styles.sectionLabel}>{eyebrow}</div>
      <h2 className={styles.sectionTitle}>{title}</h2>
      {note && <p className={styles.sectionNote}>{note}</p>}
      <div className={styles.stage}>{children}</div>
    </section>
  );
}
