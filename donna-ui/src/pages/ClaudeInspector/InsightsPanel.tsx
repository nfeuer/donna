import type { ClaudeInsights } from "../../api/claude";
import { Card } from "../../primitives/Card";
import { Skeleton } from "../../primitives/Skeleton";
import styles from "./claude-inspector.module.css";

interface Props {
  insights: ClaudeInsights | null;
  onFilterTaskType: (taskType: string) => void;
}

export default function InsightsPanel({ insights, onFilterTaskType }: Props) {
  if (!insights) {
    return (
      <div className={styles.insightsGrid}>
        {Array.from({ length: 4 }).map((_, i) => (
          <Card key={i} className={styles.skeletonCard}>
            <Skeleton width={100} height={10} />
            <Skeleton width={60} height={20} />
            <Skeleton width="100%" height={12} />
            <Skeleton width={80} height={10} />
          </Card>
        ))}
      </div>
    );
  }

  const topCenter = insights.top_cost_centers[0];
  const topPrompt = insights.system_prompt_groups[0];
  const mismatch = insights.quality_cost_mismatches[0];
  const bloat = insights.token_bloat_outliers[0];

  return (
    <div className={styles.insightsGrid}>
      {/* Top Cost Center */}
      <Card className={styles.insightCard}>
        <div className={styles.insightEyebrow}>Top Cost Center</div>
        <div className={styles.insightValue}>
          {topCenter ? `$${topCenter.total_cost.toFixed(2)}` : "—"}
        </div>
        <div className={styles.insightDesc}>
          {topCenter
            ? `${topCenter.task_type} — ${topCenter.call_count} calls, avg ${topCenter.avg_tokens_in.toLocaleString()} tokens in`
            : "No data"}
        </div>
        {topCenter && (
          <button
            type="button"
            className={styles.insightAction}
            onClick={() => onFilterTaskType(topCenter.task_type)}
          >
            View calls
          </button>
        )}
      </Card>

      {/* System Prompt Duplication */}
      <Card className={styles.insightCard}>
        <div className={styles.insightEyebrow}>Prompt Duplication</div>
        <div className={styles.insightValue}>
          {topPrompt ? topPrompt.call_count : "—"}
        </div>
        <div className={styles.insightDesc}>
          {topPrompt
            ? `${topPrompt.call_count} calls share same prompt hash — est. $${topPrompt.estimated_weekly_cost.toFixed(2)}/wk`
            : "No duplicated prompts found"}
        </div>
        {topPrompt && (
          <button
            type="button"
            className={styles.insightAction}
            onClick={() => onFilterTaskType("")}
          >
            View calls
          </button>
        )}
      </Card>

      {/* Quality-Cost Mismatch */}
      <Card className={styles.insightCard}>
        <div className={styles.insightEyebrow}>Quality-Cost Mismatch</div>
        <div className={styles.insightValue}>
          {mismatch
            ? `${(mismatch.avg_quality_score * 100).toFixed(0)}%`
            : "—"}
        </div>
        <div className={styles.insightDesc}>
          {mismatch
            ? `${mismatch.task_type} — avg $${mismatch.avg_cost.toFixed(3)}/call, ${mismatch.call_count} calls`
            : "All task types look well-matched"}
        </div>
        {mismatch && (
          <button
            type="button"
            className={styles.insightAction}
            onClick={() => onFilterTaskType(mismatch.task_type)}
          >
            View calls
          </button>
        )}
      </Card>

      {/* Token Bloat */}
      <Card className={styles.insightCard}>
        <div className={styles.insightEyebrow}>Token Bloat</div>
        <div className={styles.insightValue}>
          {bloat ? `${bloat.ratio.toFixed(1)}x` : "—"}
        </div>
        <div className={styles.insightDesc}>
          {bloat
            ? `${bloat.task_type} — ${bloat.tokens_in.toLocaleString()} tokens vs ${bloat.median_for_type.toLocaleString()} median`
            : "No token bloat outliers detected"}
        </div>
        {bloat && (
          <button
            type="button"
            className={styles.insightAction}
            onClick={() => onFilterTaskType(bloat.task_type)}
          >
            View calls
          </button>
        )}
      </Card>
    </div>
  );
}
