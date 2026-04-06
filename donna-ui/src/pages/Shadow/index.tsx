import PageShell from "../../components/PageShell";

export default function ShadowPage() {
  return (
    <PageShell
      title="Shadow Scoring"
      description="Compare primary model outputs against shadow model runs. Essential for validating local LLM migration quality before switching routing."
      session={3}
      features={[
        "Side-by-side diff view — primary vs shadow model output for the same prompt",
        "Quality score comparison — Claude judge scores for both outputs",
        "Filter by task_type — focus on specific agent workflows",
        "Aggregate stats — average quality delta between primary and shadow",
        "Spot-check queue — review items flagged by the ClaudeJudge (score < 0.7)",
        "Cost comparison — primary vs shadow cost for the same workload",
        "Trend charts — shadow quality improvement over time",
      ]}
    />
  );
}
