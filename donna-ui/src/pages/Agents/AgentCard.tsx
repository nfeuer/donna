// donna-ui/src/pages/Agents/AgentCard.tsx
import { Link } from "react-router-dom";
import type { ReactNode } from "react";
import { Card } from "../../primitives/Card";
import { Pill, type PillVariant } from "../../primitives/Pill";
import { Stat } from "../../primitives/Stat";
import { cn } from "../../lib/cn";
import type { AgentSummary } from "../../api/agents";
import styles from "./AgentCard.module.css";

const AUTONOMY_VARIANT: Record<string, PillVariant> = {
  low: "warning",
  medium: "accent",
  high: "success",
};

interface Props {
  agent: AgentSummary;
  /** Optional mini chart rendered between tools and stats (featured card). */
  chart?: ReactNode;
}

export default function AgentCard({ agent, chart }: Props) {
  return (
    <Link
      to={`/agents/${agent.name}`}
      className={cn(styles.link, !agent.enabled && styles.disabled)}
    >
      <Card className={styles.card}>
        <div className={styles.header}>
          <span
            className={cn(
              styles.statusDot,
              agent.enabled ? styles.active : styles.inactive,
            )}
            aria-label={agent.enabled ? "Active" : "Disabled"}
          />
          <h3 className={styles.name}>{agent.name}</h3>
          <Pill variant={AUTONOMY_VARIANT[agent.autonomy] ?? "muted"}>
            {agent.autonomy}
          </Pill>
        </div>

        <div className={styles.tools}>
          {agent.allowed_tools.map((t) => (
            <Pill key={t} variant="muted">{t}</Pill>
          ))}
        </div>

        {chart && <div className={styles.chartSlot}>{chart}</div>}

        <div className={styles.stats}>
          <Stat eyebrow="Calls" value={agent.total_calls.toLocaleString()} />
          <Stat eyebrow="Avg Latency" value={agent.avg_latency_ms} suffix="ms" />
          <Stat eyebrow="Cost" value={`$${agent.total_cost_usd.toFixed(4)}`} />
        </div>
      </Card>
    </Link>
  );
}
