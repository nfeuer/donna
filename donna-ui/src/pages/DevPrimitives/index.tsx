import { useState } from "react";
import styles from "./DevPrimitives.module.css";
import { StorySection } from "./StorySection";
import { Button } from "../../primitives/Button";
import { Card, CardHeader, CardEyebrow, CardTitle } from "../../primitives/Card";
import { Pill } from "../../primitives/Pill";
import { Input, Textarea, FormField } from "../../primitives/Input";
import { Select, SelectItem } from "../../primitives/Select";
import { Checkbox } from "../../primitives/Checkbox";
import { Switch } from "../../primitives/Switch";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../../primitives/Tabs";
import { Tooltip } from "../../primitives/Tooltip";
import { Dialog, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "../../primitives/Dialog";
import { Drawer } from "../../primitives/Drawer";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "../../primitives/DropdownMenu";
import { Popover, PopoverTrigger, PopoverContent } from "../../primitives/Popover";
import { Skeleton } from "../../primitives/Skeleton";
import { ScrollArea } from "../../primitives/ScrollArea";
import { PageHeader } from "../../primitives/PageHeader";

/**
 * Dev-only primitives gallery. Gated behind import.meta.env.DEV in App.tsx.
 * Each primitive task in the plan appends a StorySection below.
 * Stays after production launch for reference (see Wave 9 cleanup).
 */
export default function DevPrimitivesPage() {
  const [selectValue, setSelectValue] = useState("scheduled");
  const [cb1, setCb1] = useState(true);
  const [cb2, setCb2] = useState(false);
  const [sw, setSw] = useState(false);
  const [tab, setTab] = useState("edit");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
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

      <StorySection
        id="select"
        eyebrow="Primitive · 05"
        title="Select"
        note="Radix Select wrapped with our chrome. Full keyboard nav built in."
      >
        <Select value={selectValue} onValueChange={setSelectValue} placeholder="Select a status">
          <SelectItem value="scheduled">Scheduled</SelectItem>
          <SelectItem value="in_progress">In Progress</SelectItem>
          <SelectItem value="blocked">Blocked</SelectItem>
          <SelectItem value="done">Done</SelectItem>
        </Select>
      </StorySection>

      <StorySection
        id="checkbox"
        eyebrow="Primitive · 06"
        title="Checkbox"
      >
        <Checkbox checked={cb1} onCheckedChange={setCb1}>Show completed</Checkbox>
        <Checkbox checked={cb2} onCheckedChange={setCb2}>Include archived</Checkbox>
      </StorySection>

      <StorySection
        id="switch"
        eyebrow="Primitive · 07"
        title="Switch"
      >
        <Switch checked={sw} onCheckedChange={setSw}>Notify on overdue</Switch>
      </StorySection>

      <StorySection
        id="tabs"
        eyebrow="Primitive · 08"
        title="Tabs"
        note="Used by the Prompts editor (Edit / Preview / Split)."
      >
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            <TabsTrigger value="edit">Edit</TabsTrigger>
            <TabsTrigger value="preview">Preview</TabsTrigger>
            <TabsTrigger value="split">Split</TabsTrigger>
          </TabsList>
          <TabsContent value="edit">Edit panel content.</TabsContent>
          <TabsContent value="preview">Preview panel content.</TabsContent>
          <TabsContent value="split">Split panel content.</TabsContent>
        </Tabs>
      </StorySection>

      <StorySection
        id="tooltip"
        eyebrow="Primitive · 09"
        title="Tooltip"
        note="400ms delay (overrides Radix default of 700ms)."
      >
        <Tooltip content="Hover text uses the surface token">
          <Button variant="ghost">Hover me</Button>
        </Tooltip>
        <Tooltip content="Arrows render in the same color as the surface">
          <Button variant="text">And me →</Button>
        </Tooltip>
      </StorySection>

      <StorySection
        id="dialog"
        eyebrow="Primitive · 10"
        title="Dialog"
        note="Focus trap, Escape to close, backdrop click to close — all handled by Radix."
      >
        <Button onClick={() => setDialogOpen(true)}>Open Dialog</Button>
        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogHeader>
            <DialogTitle>Reschedule Task</DialogTitle>
            <DialogDescription>Pick a new time for "Draft Q2 budget memo."</DialogDescription>
          </DialogHeader>
          <p style={{ color: "var(--color-text-secondary)" }}>
            Dialog body content would go here.
          </p>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDialogOpen(false)}>Cancel</Button>
            <Button onClick={() => setDialogOpen(false)}>Confirm</Button>
          </DialogFooter>
        </Dialog>
      </StorySection>

      <StorySection
        id="drawer"
        eyebrow="Primitive · 11"
        title="Drawer"
        note="Side sheet on Radix Dialog. Slides in from the right, same a11y guarantees as Dialog."
      >
        <Button onClick={() => setDrawerOpen(true)}>Open Drawer</Button>
        <Drawer open={drawerOpen} onOpenChange={setDrawerOpen} title="Task Detail">
          <p style={{ color: "var(--color-text-secondary)" }}>
            Drawer body. Use this for task/log detail panels in later waves.
          </p>
        </Drawer>
      </StorySection>

      <StorySection
        id="dropdown"
        eyebrow="Primitive · 12"
        title="DropdownMenu"
      >
        <DropdownMenu>
          <DropdownMenuTrigger>
            <Button variant="ghost">Actions</Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent>
            <DropdownMenuItem>Reschedule</DropdownMenuItem>
            <DropdownMenuItem>Mark done</DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem>Delete</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </StorySection>

      <StorySection
        id="popover"
        eyebrow="Primitive · 13"
        title="Popover"
        note="Used by filter bars and date range pickers."
      >
        <Popover>
          <PopoverTrigger>
            <Button variant="ghost">Filter by date</Button>
          </PopoverTrigger>
          <PopoverContent>
            Popover body — use this to host date pickers, filter forms, etc.
          </PopoverContent>
        </Popover>
      </StorySection>

      <StorySection
        id="skeleton"
        eyebrow="Primitive · 14"
        title="Skeleton"
        note="Respects prefers-reduced-motion (no shimmer)."
      >
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)", width: 280 }}>
          <Skeleton height={14} width="60%" />
          <Skeleton height={32} />
          <Skeleton height={14} />
          <Skeleton height={14} width="80%" />
        </div>
      </StorySection>

      <StorySection
        id="scrollarea"
        eyebrow="Primitive · 15"
        title="ScrollArea"
      >
        <ScrollArea style={{ width: 320, height: 160, border: "1px solid var(--color-border)", borderRadius: "var(--radius-card)", padding: "var(--space-3)" }}>
          <div style={{ color: "var(--color-text-secondary)" }}>
            {Array.from({ length: 20 }).map((_, i) => (
              <p key={i} style={{ margin: "0 0 var(--space-2) 0" }}>
                Line {i + 1} — a scrolling line to prove the scrollbar themes correctly.
              </p>
            ))}
          </div>
        </ScrollArea>
      </StorySection>

      <StorySection
        id="pageheader"
        eyebrow="Primitive · 16"
        title="PageHeader"
        note="The opening move on every page. Eyebrow + Fraunces title + meta + action slot."
      >
        <div style={{ width: "100%" }}>
          <PageHeader
            eyebrow="Tuesday · April 8"
            title="Dashboard"
            meta="14 day window · last refreshed 2 min ago"
            actions={
              <>
                <Button variant="ghost" size="sm">24h</Button>
                <Button size="sm">14d</Button>
                <Button variant="ghost" size="sm">30d</Button>
              </>
            }
          />
        </div>
      </StorySection>

      {/* Stories appended by subsequent plan tasks */}
    </div>
  );
}
