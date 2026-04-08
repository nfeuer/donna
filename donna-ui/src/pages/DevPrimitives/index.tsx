import styles from "./DevPrimitives.module.css";
import { StorySection } from "./StorySection";
import { Button } from "../../primitives/Button";
import { Card, CardHeader, CardEyebrow, CardTitle } from "../../primitives/Card";

/**
 * Dev-only primitives gallery. Gated behind import.meta.env.DEV in App.tsx.
 * Each primitive task in the plan appends a StorySection below.
 * Stays after production launch for reference (see Wave 9 cleanup).
 */
export default function DevPrimitivesPage() {
  return (
    <div className={styles.root}>
      <header className={styles.header}>
        <div className={styles.eyebrow}>Dev · Primitives</div>
        <h1 className={styles.title}>Donna Primitive Library</h1>
        <p className={styles.meta}>
          Press Cmd+. to flip themes. Every primitive renders here first, before it lands on a page.
        </p>
      </header>

      <StorySection
        id="button"
        eyebrow="Primitive · 01"
        title="Button"
        note="Three variants × three sizes. All use var(--color-accent), flip the theme with Cmd+. to see them update."
      >
        <Button>Primary</Button>
        <Button variant="ghost">Ghost</Button>
        <Button variant="text">Text →</Button>
        <Button size="sm">Small</Button>
        <Button size="lg">Large</Button>
        <Button disabled>Disabled</Button>
      </StorySection>

      <StorySection
        id="card"
        eyebrow="Primitive · 02"
        title="Card"
        note="Base container. Border lifts to text-dim on hover."
      >
        <Card style={{ width: 280 }}>
          <CardHeader>
            <CardEyebrow>Tasks Today</CardEyebrow>
            <CardTitle>Spend This Week</CardTitle>
          </CardHeader>
          <p style={{ color: "var(--color-text-muted)", fontSize: "var(--text-body)", margin: 0 }}>
            Card content. Reads from tokens, no inline hex anywhere.
          </p>
        </Card>
      </StorySection>

      {/* Stories appended by subsequent plan tasks */}
    </div>
  );
}
