import PageShell from "../../components/PageShell";

export default function TasksPage() {
  return (
    <PageShell
      title="Task Browser"
      description="Browse and inspect all tasks with rich filtering, state timeline visualization, and linked entities (invocations, nudges, corrections, subtasks)."
      session={2}
      features={[
        "Filterable task table — by status, domain, priority, agent, tags, search",
        "Task detail view with full field display and state history",
        "State timeline — visual progression through backlog -> scheduled -> in_progress -> done",
        "Linked invocations — all LLM calls made for this task",
        "Linked nudge events — nudge history with escalation tiers",
        "Linked corrections — user corrections that generated preference rules",
        "Subtask tree — hierarchical view of decomposed tasks",
        "Overdue highlighting and reschedule count badges",
      ]}
    />
  );
}
