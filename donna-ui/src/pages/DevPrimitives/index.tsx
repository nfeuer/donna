import styles from "./DevPrimitives.module.css";
import { StorySection } from "./StorySection";
import { Button } from "../../primitives/Button";
import { Card, CardHeader, CardEyebrow, CardTitle } from "../../primitives/Card";
import { Pill } from "../../primitives/Pill";
import { Input, Textarea, FormField } from "../../primitives/Input";

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

      <StorySection
        id="pill"
        eyebrow="Primitive · 03"
        title="Pill"
        note="Status indicators. Semantic colors only appear when semantically required."
      >
        <Pill>Scheduled</Pill>
        <Pill variant="success">Done</Pill>
        <Pill variant="warning">At Risk</Pill>
        <Pill variant="error">Overdue</Pill>
        <Pill variant="muted">Backlog</Pill>
      </StorySection>

      <StorySection
        id="input"
        eyebrow="Primitive · 04"
        title="Input, Textarea, FormField"
        note="FormField wires labels, ids, and aria-describedby automatically."
      >
        <div style={{ display: "grid", gap: "var(--space-3)", width: 320 }}>
          <FormField label="Task Title">
            {(p) => <Input placeholder="Draft Q2 budget memo" {...p} />}
          </FormField>
          <FormField label="Notes">
            {(p) => <Textarea placeholder="Include variance vs Q1…" {...p} />}
          </FormField>
          <FormField label="Invalid Example" error="Title is required">
            {(p) => <Input {...p} />}
          </FormField>
        </div>
      </StorySection>

      {/* Stories appended by subsequent plan tasks */}
    </div>
  );
}
