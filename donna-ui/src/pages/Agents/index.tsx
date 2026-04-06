import PageShell from "../../components/PageShell";

export default function AgentsPage() {
  return (
    <PageShell
      title="Agent Details"
      description="Per-agent activity views showing execution history, performance metrics, tool usage patterns, and configuration for all 6 Donna agents."
      session={2}
      features={[
        "Agent cards: PM, Scheduler, Research, Coding, Challenger, Communication",
        "Per-agent activity feed — recent executions with status, duration, cost",
        "Performance charts — latency distribution, success/fail trend over time",
        "Tool usage breakdown — which tools each agent uses and how often",
        "Execution timeline — visual trace of agent dispatch flow (PM -> Challenger -> Execution)",
        "Agent config viewer — timeout, autonomy level, allowed tools",
        "Quality score trends per agent (when shadow scoring is enabled)",
      ]}
    />
  );
}
