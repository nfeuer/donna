import { useState, useCallback, useEffect, useRef } from "react";
import { toast } from "sonner";
import { type ColumnDef } from "@tanstack/react-table";
import RefreshButton from "../../components/RefreshButton";
import { PageHeader } from "../../primitives/PageHeader";
import { Segmented } from "../../primitives/Segmented";
import { Pill } from "../../primitives/Pill";
import { Card } from "../../primitives/Card";
import { Stat } from "../../primitives/Stat";
import { Button } from "../../primitives/Button";
import { DataTable } from "../../primitives/DataTable";
import { Skeleton } from "../../primitives/Skeleton";
import { Tooltip } from "../../primitives/Tooltip";
import { BarChart, ChartCard, type ChartCardStat } from "../../charts";
import { fetchAdminHealth, type AdminHealthData } from "../../api/health";
import {
  fetchLLMGatewayAnalytics,
  fetchQueueItemPrompt,
  type LLMGatewayData,
  type LLMGatewayCallerEntry,
  type QueueItemPreview,
} from "../../api/llmGateway";
import { useLLMQueueStream } from "../../hooks/useLLMQueueStream";
import client from "../../api/client";
import styles from "./LLMGateway.module.css";

const RANGE_OPTIONS = [
  { label: "7d", value: "7" },
  { label: "14d", value: "14" },
  { label: "30d", value: "30" },
  { label: "90d", value: "90" },
] as const;

type RangeValue = (typeof RANGE_OPTIONS)[number]["value"];

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const secs = Math.floor(diff / 1000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  return `${Math.floor(mins / 60)}h ago`;
}

function QueuePreviewItem({ item }: { item: QueueItemPreview }) {
  const [expanded, setExpanded] = useState(false);
  const [fullPrompt, setFullPrompt] = useState<string | null>(null);

  const handleClick = async () => {
    if (expanded) {
      setExpanded(false);
      return;
    }
    setExpanded(true);
    if (!fullPrompt) {
      try {
        const detail = await fetchQueueItemPrompt(item.sequence);
        setFullPrompt(detail.prompt);
      } catch {
        setFullPrompt("[Could not load prompt]");
      }
    }
  };

  return (
    <div>
      <div className={styles.previewItem} onClick={handleClick}>
        <span style={{ fontFamily: "var(--font-mono)" }}>
          {item.caller ?? item.task_type ?? "internal"}
        </span>
        <span style={{ display: "flex", gap: "var(--space-2)", alignItems: "center" }}>
          <span style={{ color: "var(--color-text-muted)" }}>{item.model}</span>
          <span style={{ color: "var(--color-text-muted)" }}>{timeAgo(item.enqueued_at)}</span>
        </span>
      </div>
      {expanded && (
        <div className={styles.promptExpand}>
          {fullPrompt ?? item.prompt_preview}
        </div>
      )}
    </div>
  );
}

const callerColumns: ColumnDef<LLMGatewayCallerEntry>[] = [
  {
    accessorKey: "caller",
    header: "Caller",
    cell: ({ getValue }) => (
      <span style={{ fontFamily: "var(--font-mono)" }}>{getValue<string>()}</span>
    ),
  },
  {
    accessorKey: "call_count",
    header: "Calls",
    cell: ({ getValue }) => getValue<number>().toLocaleString(),
  },
  {
    accessorKey: "avg_latency_ms",
    header: "Avg Latency",
    cell: ({ getValue }) => `${getValue<number>().toLocaleString()}ms`,
  },
  {
    accessorKey: "total_tokens_in",
    header: "Tokens In",
    cell: ({ getValue }) => {
      const v = getValue<number>();
      return v >= 1000 ? `${(v / 1000).toFixed(0)}k` : v.toString();
    },
  },
  {
    accessorKey: "interrupted_count",
    header: "Interrupted",
    cell: ({ getValue }) => {
      const v = getValue<number>();
      return v > 0 ? (
        <span style={{ color: "var(--color-warning)" }}>{v}</span>
      ) : (
        "0"
      );
    },
  },
];

export default function LLMGateway() {
  const [range, setRange] = useState<RangeValue>("7");
  const days = Number(range);
  const [health, setHealth] = useState<AdminHealthData | null>(null);
  const [analytics, setAnalytics] = useState<LLMGatewayData | null>(null);
  const [analyticsLoading, setAnalyticsLoading] = useState(true);

  // Live data via SSE
  const { data: liveData, connected } = useLLMQueueStream();

  // Config state
  const [configRpm, setConfigRpm] = useState("");
  const [configRph, setConfigRph] = useState("");
  const [configDepth, setConfigDepth] = useState("");
  const [configSaving, setConfigSaving] = useState(false);

  // Expanded current request prompt
  const [currentPrompt, setCurrentPrompt] = useState<string | null>(null);
  const [currentExpanded, setCurrentExpanded] = useState(false);

  const fetchAnalytics = useCallback(async (d: number) => {
    setAnalyticsLoading(true);
    try {
      const data = await fetchLLMGatewayAnalytics(d);
      setAnalytics(data);
    } catch {
      // Error toast handled by client interceptor
    } finally {
      setAnalyticsLoading(false);
    }
  }, []);

  const refreshHealth = useCallback(() => {
    fetchAdminHealth().then(setHealth).catch(() => setHealth(null));
  }, []);

  // Load config defaults
  useEffect(() => {
    client
      .get("/admin/configs/llm_gateway.yaml")
      .then(({ data }) => {
        // Parse YAML values for the quick config fields
        const content = data.content as string;
        const rpmMatch = content.match(/requests_per_minute:\s*(\d+)/);
        const rphMatch = content.match(/requests_per_hour:\s*(\d+)/);
        const depthMatch = content.match(/max_external_depth:\s*(\d+)/);
        if (rpmMatch) setConfigRpm(rpmMatch[1]);
        if (rphMatch) setConfigRph(rphMatch[1]);
        if (depthMatch) setConfigDepth(depthMatch[1]);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetchAnalytics(days);
    refreshHealth();
  }, [days, fetchAnalytics, refreshHealth]);

  const handleRefresh = useCallback(async () => {
    refreshHealth();
    await fetchAnalytics(days);
  }, [days, fetchAnalytics, refreshHealth]);

  const handleConfigSave = async () => {
    setConfigSaving(true);
    try {
      // Read current config
      const { data: current } = await client.get("/admin/configs/llm_gateway.yaml");
      let content = current.content as string;

      // Replace values
      if (configRpm) {
        content = content.replace(
          /requests_per_minute:\s*\d+/,
          `requests_per_minute: ${configRpm}`,
        );
      }
      if (configRph) {
        content = content.replace(
          /requests_per_hour:\s*\d+/,
          `requests_per_hour: ${configRph}`,
        );
      }
      if (configDepth) {
        content = content.replace(
          /max_external_depth:\s*\d+/,
          `max_external_depth: ${configDepth}`,
        );
      }

      await client.put("/admin/configs/llm_gateway.yaml", { content });
      toast.success("Gateway config saved and reloaded");
    } catch {
      toast.error("Failed to save config");
    } finally {
      setConfigSaving(false);
    }
  };

  const handleCurrentRequestClick = async () => {
    if (!liveData?.current_request) return;
    if (currentExpanded) {
      setCurrentExpanded(false);
      return;
    }
    setCurrentExpanded(true);
    if (!currentPrompt) {
      try {
        const detail = await fetchQueueItemPrompt(liveData.current_request.sequence);
        setCurrentPrompt(detail.prompt);
      } catch {
        setCurrentPrompt("[Could not load prompt]");
      }
    }
  };

  // Reset expanded prompt when current request changes
  const prevSeq = useRef<number | null>(null);
  useEffect(() => {
    const seq = liveData?.current_request?.sequence ?? null;
    if (seq !== prevSeq.current) {
      setCurrentPrompt(null);
      setCurrentExpanded(false);
      prevSeq.current = seq;
    }
  }, [liveData?.current_request?.sequence]);

  const healthVariant =
    health?.status === "healthy" ? "success" : health ? "warning" : "muted";
  const healthLabel =
    health?.status === "healthy" ? "Healthy" : health ? "Degraded" : "—";

  const s = analytics?.summary;
  const chartStats: ChartCardStat[] = [
    { label: "Total", value: s?.total_calls.toLocaleString() ?? "—" },
    { label: "Internal", value: s?.internal_calls.toLocaleString() ?? "—" },
    { label: "External", value: s?.external_calls.toLocaleString() ?? "—" },
    { label: "Interrupted", value: s?.total_interrupted.toLocaleString() ?? "—" },
    { label: "Avg Latency", value: s ? `${s.avg_latency_ms.toLocaleString()}ms` : "—" },
    { label: "Callers", value: s?.unique_callers.toLocaleString() ?? "—" },
  ];

  const allNextItems = [
    ...(liveData?.internal_queue.next_items ?? []),
    ...(liveData?.external_queue.next_items ?? []),
  ];

  return (
    <div className={styles.page}>
      <PageHeader
        eyebrow="Infrastructure"
        title="LLM Gateway"
        actions={
          <div className={styles.controls}>
            <Segmented
              value={range}
              onValueChange={(v) => setRange(v as RangeValue)}
              options={RANGE_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
              aria-label="Date range"
            />
            {health && (
              <Tooltip content={`Ollama: ${health.checks?.ollama?.ok ? "OK" : "down"}`}>
                <span role="status">
                  <Pill variant={healthVariant}>{healthLabel}</Pill>
                </span>
              </Tooltip>
            )}
            <Pill variant={connected ? "success" : "muted"}>
              {connected ? "SSE" : "Disconnected"}
            </Pill>
            <RefreshButton onRefresh={handleRefresh} />
          </div>
        }
      />

      {/* Row 1: Live Status Strip */}
      <div className={styles.liveStrip}>
        <Card>
          <div style={{ padding: "var(--space-4)" }}>
            <Stat
              eyebrow="Queue Status"
              value={liveData?.mode === "active" ? "Active" : liveData?.mode === "slow" ? "Slow" : "—"}
              sub={
                liveData && (
                  <span style={{ fontSize: "var(--text-label)", color: "var(--color-text-muted)" }}>
                    Internal {liveData.internal_queue.pending} · External{" "}
                    {liveData.external_queue.pending}
                  </span>
                )
              }
              plain
            />
          </div>
        </Card>

        <Card>
          <div
            style={{ padding: "var(--space-4)", cursor: liveData?.current_request ? "pointer" : "default" }}
            onClick={handleCurrentRequestClick}
          >
            <div
              style={{
                fontSize: "var(--text-eyebrow)",
                letterSpacing: "var(--tracking-eyebrow)",
                textTransform: "uppercase",
                color: "var(--color-text-muted)",
                marginBottom: "var(--space-2)",
              }}
            >
              Current Request
            </div>
            {liveData?.current_request ? (
              <div
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: "var(--text-label)",
                  display: "flex",
                  flexDirection: "column",
                  gap: "var(--space-1)",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ color: "var(--color-text-muted)" }}>type</span>
                  <span>{liveData.current_request.type}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ color: "var(--color-text-muted)" }}>caller</span>
                  <span>{liveData.current_request.caller ?? "—"}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ color: "var(--color-text-muted)" }}>model</span>
                  <span>{liveData.current_request.model}</span>
                </div>
              </div>
            ) : (
              <span style={{ color: "var(--color-text-muted)", fontSize: "var(--text-label)" }}>
                Idle
              </span>
            )}
            {currentExpanded && (
              <div className={styles.promptExpand} style={{ marginTop: "var(--space-2)" }}>
                {currentPrompt ?? liveData?.current_request?.prompt_preview ?? ""}
              </div>
            )}
          </div>
        </Card>

        <Card>
          <div
            style={{
              padding: "var(--space-4)",
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: "var(--space-3)",
            }}
          >
            <Stat
              eyebrow="Internal"
              value={liveData?.stats_24h.internal_completed ?? 0}
              plain
            />
            <Stat
              eyebrow="External"
              value={liveData?.stats_24h.external_completed ?? 0}
              plain
            />
            <Stat
              eyebrow="Interrupted"
              value={liveData?.stats_24h.external_interrupted ?? 0}
              plain
            />
            <Stat eyebrow="Rejected" value={0} plain />
          </div>
        </Card>
      </div>

      {/* Queue Preview */}
      {allNextItems.length > 0 && (
        <div className={styles.queuePreview}>
          <div
            style={{
              fontSize: "var(--text-eyebrow)",
              letterSpacing: "var(--tracking-eyebrow)",
              textTransform: "uppercase",
              color: "var(--color-text-muted)",
            }}
          >
            Next in Queue
          </div>
          {allNextItems.map((item) => (
            <QueuePreviewItem key={item.sequence} item={item} />
          ))}
        </div>
      )}

      {/* Row 2: Historical Chart */}
      <ChartCard
        eyebrow={`Gateway Throughput · ${days} days`}
        metric={s?.total_calls.toLocaleString() ?? "—"}
        metricSuffix="calls"
        chart={
          analytics?.time_series && analytics.time_series.length > 0 ? (
            <BarChart
              data={analytics.time_series}
              series={[
                { dataKey: "internal", name: "Internal" },
                { dataKey: "external", name: "External", tone: "accentSoft" },
              ]}
              categoryKey="date"
              orientation="horizontal"
              formatCategoryTick={(v) => v.slice(5)}
              ariaLabel={`Gateway throughput over ${days} days`}
            />
          ) : undefined
        }
        stats={chartStats}
        loading={analyticsLoading && !analytics}
      />

      {/* Row 3: Caller Table + Config */}
      <div className={styles.detailSplit}>
        <Card>
          <div style={{ padding: "var(--space-4)" }}>
            <div
              style={{
                fontSize: "var(--text-eyebrow)",
                letterSpacing: "var(--tracking-eyebrow)",
                textTransform: "uppercase",
                color: "var(--color-text-muted)",
                marginBottom: "var(--space-3)",
              }}
            >
              Per-Caller Breakdown
            </div>
            {analyticsLoading && !analytics ? (
              <Skeleton height={200} />
            ) : (
              <DataTable
                data={analytics?.by_caller ?? []}
                columns={callerColumns}
                getRowId={(row) => row.caller}
                emptyState={
                  <span style={{ color: "var(--color-text-muted)" }}>
                    No external callers in this period
                  </span>
                }
              />
            )}
          </div>
        </Card>

        <Card>
          <div style={{ padding: "var(--space-4)" }}>
            <div
              style={{
                fontSize: "var(--text-eyebrow)",
                letterSpacing: "var(--tracking-eyebrow)",
                textTransform: "uppercase",
                color: "var(--color-text-muted)",
                marginBottom: "var(--space-3)",
              }}
            >
              Quick Config
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
              <div className={styles.configField}>
                <label className={styles.configLabel}>Default RPM</label>
                <input
                  type="number"
                  className={styles.configInput}
                  value={configRpm}
                  onChange={(e) => setConfigRpm(e.target.value)}
                />
              </div>
              <div className={styles.configField}>
                <label className={styles.configLabel}>Default RPH</label>
                <input
                  type="number"
                  className={styles.configInput}
                  value={configRph}
                  onChange={(e) => setConfigRph(e.target.value)}
                />
              </div>
              <div className={styles.configField}>
                <label className={styles.configLabel}>Max Queue Depth</label>
                <input
                  type="number"
                  className={styles.configInput}
                  value={configDepth}
                  onChange={(e) => setConfigDepth(e.target.value)}
                />
              </div>
              <div className={styles.configField}>
                <span className={styles.configLabel}>Active Hours</span>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-label)" }}>
                  {liveData?.mode === "active" ? "In active hours" : "Outside active hours"}
                </span>
              </div>
              <Button
                variant="primary"
                size="sm"
                onClick={handleConfigSave}
                disabled={configSaving}
              >
                {configSaving ? "Saving..." : "Save Changes"}
              </Button>
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}
