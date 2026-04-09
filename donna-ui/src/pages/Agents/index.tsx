// donna-ui/src/pages/Agents/index.tsx
import { useState, useEffect, useCallback, useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { PageHeader } from "../../primitives/PageHeader";
import { Card } from "../../primitives/Card";
import { Skeleton } from "../../primitives/Skeleton";
import { EmptyState } from "../../primitives/EmptyState";
import RefreshButton from "../../components/RefreshButton";
import { AreaChart } from "../../charts";
import AgentCard from "./AgentCard";
import AgentDetailView from "./AgentDetail";
import {
  fetchAgents,
  fetchAgentDetail,
  type AgentSummary,
  type DailyLatency,
} from "../../api/agents";
import styles from "./Agents.module.css";

export default function AgentsPage() {
  const { name } = useParams<{ name?: string }>();

  const [agents, setAgents] = useState<AgentSummary[]>([]);
  // Initialize true so the first paint shows the skeleton grid, not a flash
  // of EmptyState while the initial fetch is in-flight.
  const [loading, setLoading] = useState(true);
  const [featuredLatency, setFeaturedLatency] = useState<DailyLatency[]>([]);

  const doFetch = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchAgents();
      setAgents(data);
    } catch {
      setAgents([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    doFetch();
  }, [doFetch]);

  // Fetch mini chart data for the featured (most-recent-run) agent.
  const featured = useMemo(() => {
    if (agents.length === 0) return null;
    return agents.reduce((best, a) => {
      if (!a.last_invocation) return best;
      if (!best || !best.last_invocation) return a;
      return a.last_invocation > best.last_invocation ? a : best;
    }, null as AgentSummary | null);
  }, [agents]);

  useEffect(() => {
    setFeaturedLatency([]);
    if (!featured) return;
    let cancelled = false;
    fetchAgentDetail(featured.name)
      .then((d) => {
        if (!cancelled) setFeaturedLatency(d.daily_latency);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [featured]);

  const formatTick = useMemo(() => (d: string) => d.slice(5), []);

  // Featured agent first, then alphabetical. Memoized so the sort doesn't
  // run on unrelated re-renders (e.g. featuredLatency state changes).
  const sortedAgents = useMemo(() => {
    return [...agents].sort((a, b) => {
      if (featured && a.name === featured.name) return -1;
      if (featured && b.name === featured.name) return 1;
      return a.name.localeCompare(b.name);
    });
  }, [agents, featured]);

  // Detail view
  if (name) {
    // Convert "task_planner" / "task-planner" → "Task Planner" so the
    // display title reads naturally regardless of agent naming style.
    const displayName = name.replace(/[-_]+/g, " ");
    return (
      <div className={styles.root}>
        <div className={styles.detailHeader}>
          <Link to="/agents" className={styles.backLink}>
            <ArrowLeft size={16} />
            All Agents
          </Link>
          <h1 className={styles.detailTitle}>{displayName} Agent</h1>
        </div>
        <AgentDetailView agentName={name} />
      </div>
    );
  }

  // Grid view
  return (
    <div className={styles.root}>
      <PageHeader
        eyebrow="System"
        title="Agents"
        meta={
          loading
            ? "Loading…"
            : `${agents.length} agent${agents.length !== 1 ? "s" : ""}`
        }
        actions={<RefreshButton onRefresh={doFetch} />}
      />

      {loading ? (
        <div className={styles.skeletonGrid}>
          {Array.from({ length: 6 }).map((_, i) => (
            <Card key={i}>
              <Skeleton height={i === 0 ? 200 : 140} />
            </Card>
          ))}
        </div>
      ) : agents.length === 0 ? (
        <EmptyState
          title="No agents configured"
          body="Agent definitions live in config/agents.yaml. Add one and it'll show up here."
        />
      ) : (
        <div className={styles.grid}>
          {sortedAgents.map((agent) => (
            <AgentCard
              key={agent.name}
              agent={agent}
              chart={
                featured &&
                agent.name === featured.name &&
                featuredLatency.length > 0 ? (
                  <AreaChart
                    data={featuredLatency}
                    dataKey="avg_latency_ms"
                    xKey="date"
                    formatTick={formatTick}
                    name="Latency"
                    height={80}
                    ariaLabel={`${agent.name} latency sparkline`}
                  />
                ) : undefined
              }
            />
          ))}
        </div>
      )}
    </div>
  );
}
