import { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import dayjs from "dayjs";
import relativeTime from "dayjs/plugin/relativeTime";
import { Card, CardHeader, CardTitle } from "../../primitives/Card";
import { Pill } from "../../primitives/Pill";
import { Skeleton } from "../../primitives/Skeleton";
import { ChartCard, type ChartCardStat, BarChart } from "../../charts";
import { fetchPromptStats, type PromptStats } from "../../api/promptStats";
import styles from "./PromptWelcome.module.css";

dayjs.extend(relativeTime);

export default function PromptWelcome() {
  const [stats, setStats] = useState<PromptStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [entered, setEntered] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setStats(await fetchPromptStats());
    } catch {
      setStats(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    const raf = requestAnimationFrame(() => setEntered(true));
    return () => cancelAnimationFrame(raf);
  }, []);

  if (loading) {
    return (
      <div className={styles.root}>
        <div className={styles.grid}>
          <Card><Skeleton height={120} /></Card>
          <Card><Skeleton height={120} /></Card>
          <Card><Skeleton height={160} /></Card>
          <Card><Skeleton height={160} /></Card>
        </div>
      </div>
    );
  }

  if (!stats) return null;

  const folderStats: ChartCardStat[] = Object.entries(stats.by_folder).map(
    ([folder, count]) => ({ label: folder, value: String(count) }),
  );

  const modelChartData = Object.entries(stats.model_routing).map(
    ([model, count]) => ({ model, count }),
  );

  return (
    <div className={styles.root} data-entered={entered ? "true" : "false"}>
      <div className={styles.grid}>
        {/* Overview */}
        <ChartCard
          eyebrow="Prompt Templates"
          metric={String(stats.total)}
          stats={folderStats}
          loading={false}
        >
          <div className={styles.folderBreakdown}>
            {Object.entries(stats.by_folder).map(([folder, count]) => (
              <span key={folder}>{count} {folder}</span>
            ))}
          </div>
        </ChartCard>

        {/* Local vs Cloud */}
        <ChartCard
          eyebrow="Model Routing"
          metric={String(Object.values(stats.model_routing).reduce((a, b) => a + b, 0))}
          metricSuffix=" routed"
          chart={
            modelChartData.length > 0 ? (
              <BarChart
                data={modelChartData}
                series={[{ dataKey: "count", name: "Prompts" }]}
                categoryKey="model"
                orientation="vertical"
                categoryWidth={100}
                height={100}
                ariaLabel="Prompt count by model"
              />
            ) : undefined
          }
          loading={false}
        />

        {/* Most invoked */}
        <Card>
          <CardHeader><CardTitle>Most Invoked</CardTitle></CardHeader>
          {stats.most_invoked.length === 0 ? (
            <div className={styles.rankedMeta} style={{ padding: "var(--space-3)" }}>
              No invocations recorded yet.
            </div>
          ) : (
            <ul className={styles.rankedList}>
              {stats.most_invoked.slice(0, 5).map((item, i) => (
                <li key={item.prompt}>
                  <Link to={`/prompts/${item.prompt}`} className={styles.rankedItem}>
                    <span className={styles.rankedRank}>{i + 1}</span>
                    <span className={styles.rankedName}>{item.prompt.replace(/\.md$/, "")}</span>
                    <span className={styles.rankedMeta}>
                      {item.invocations.toLocaleString()} calls · ${item.cost_usd.toFixed(2)}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {/* Agent coverage */}
        <Card>
          <CardHeader><CardTitle>Agent Coverage</CardTitle></CardHeader>
          {stats.agent_coverage.length === 0 ? (
            <div className={styles.rankedMeta} style={{ padding: "var(--space-3)" }}>
              No agent mappings found.
            </div>
          ) : (
            <ul className={styles.rankedList}>
              {stats.agent_coverage.map((item) => (
                <li key={item.prompt}>
                  <Link to={`/prompts/${item.prompt}`} className={styles.rankedItem}>
                    <span className={styles.rankedName}>{item.prompt.replace(/\.md$/, "")}</span>
                    <div className={styles.pillList}>
                      {item.agents.map((a) => (
                        <Pill key={a} variant="muted">{a}</Pill>
                      ))}
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {/* Recently modified + Unused */}
        <div className={styles.fullWidth}>
          <Card>
            <CardHeader><CardTitle>Activity</CardTitle></CardHeader>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-4)", padding: "0 var(--space-3) var(--space-3)" }}>
              <div>
                <div className={styles.sectionTitle}>Recently Modified</div>
                <ul className={styles.rankedList}>
                  {stats.recently_modified.map((item) => (
                    <li key={item.name}>
                      <Link to={`/prompts/${item.name}`} className={styles.rankedItem}>
                        <span className={styles.rankedName}>{item.name.replace(/\.md$/, "")}</span>
                        <span className={styles.rankedMeta}>{dayjs(item.modified * 1000).fromNow()}</span>
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <div className={styles.sectionTitle}>
                  Unused {stats.unused.length > 0 && <Pill variant="warning">{stats.unused.length}</Pill>}
                </div>
                {stats.unused.length === 0 ? (
                  <div className={styles.rankedMeta}>All prompts are in use.</div>
                ) : (
                  <ul className={styles.unusedList}>
                    {stats.unused.map((name) => (
                      <li key={name} className={styles.unusedItem}>
                        {name.replace(/\.md$/, "")}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
