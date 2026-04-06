import PageShell from "../../components/PageShell";

export default function ConfigsPage() {
  return (
    <PageShell
      title="Configuration Editor"
      description="Edit YAML configuration files that control Donna's behavior — agents, model routing, task types, state machine, and learned preferences."
      session={2}
      features={[
        "Structured form editing for agents.yaml (timeouts, tools, autonomy levels)",
        "Model routing editor for donna_models.yaml (task_type -> model mapping, shadow config)",
        "Task type registry editor (prompt templates, schemas, tool allowlists)",
        "State machine visualization and transition editor",
        "Preference rules viewer with enable/disable toggles",
        "Hot reload support — changes take effect without restarting Donna",
        "Diff view showing pending changes before saving",
        "Raw YAML editor fallback with syntax highlighting",
      ]}
    />
  );
}
