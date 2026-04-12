import { Link } from "react-router-dom";
import { ChartCard, type ChartCardStat } from "../../charts";
import type { LLMQueueStatusData } from "../../api/llmGateway";

interface Props {
  data: LLMQueueStatusData | null;
  loading: boolean;
}

function ModeIndicator({ mode }: { mode: "active" | "slow" }) {
  const color = mode === "active" ? "var(--color-success)" : "var(--color-warning)";
  const label = mode === "active" ? "Active" : "Slow";
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
      <span
        style={{
          width: 10,
          height: 10,
          borderRadius: "50%",
          background: color,
          display: "inline-block",
        }}
        aria-hidden="true"
      />
      {label}
    </span>
  );
}

function RateLimitBar({ count, limit }: { count: number; limit: number }) {
  const pct = limit > 0 ? Math.min((count / limit) * 100, 100) : 0;
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-2)",
      }}
    >
      <div
        style={{
          flex: 1,
          height: 4,
          background: "var(--color-accent-soft)",
          borderRadius: 2,
          overflow: "hidden",
        }}
        role="progressbar"
        aria-valuenow={count}
        aria-valuemax={limit}
        aria-label="Rate limit usage"
      >
        <div
          style={{
            height: "100%",
            width: `${pct}%`,
            background: pct > 80 ? "var(--color-warning)" : "var(--color-accent)",
            borderRadius: 2,
            transition: "width var(--duration-base) var(--ease-out)",
          }}
        />
      </div>
      <span
        style={{
          fontSize: "var(--text-label)",
          color: "var(--color-text-muted)",
          fontFamily: "var(--font-mono)",
          whiteSpace: "nowrap",
        }}
      >
        {count}/{limit}
      </span>
    </div>
  );
}

export default function LLMQueueCard({ data, loading }: Props) {
  const stats: ChartCardStat[] = [
    { label: "Internal Queue", value: data?.internal_queue.pending ?? 0 },
    { label: "External Queue", value: data?.external_queue.pending ?? 0 },
    {
      label: "Completed (24h)",
      value: data
        ? data.stats_24h.internal_completed + data.stats_24h.external_completed
        : 0,
    },
    {
      label: "Interrupted (24h)",
      value: data?.stats_24h.external_interrupted ?? 0,
    },
    {
      label: "Callers Active",
      value: data ? Object.keys(data.rate_limits).length : 0,
    },
  ];

  const rateLimitEntries = data ? Object.entries(data.rate_limits) : [];

  return (
    <ChartCard
      eyebrow="LLM Gateway · Live"
      metric={
        data ? <ModeIndicator mode={data.mode} /> : "—"
      }
      stats={stats}
      loading={loading && !data}
    >
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: "var(--space-4)",
        }}
      >
        {/* Current request */}
        <div>
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
          {data?.current_request ? (
            <div
              style={{
                background: "var(--color-surface)",
                borderRadius: 8,
                padding: "var(--space-3)",
                fontFamily: "var(--font-mono)",
                fontSize: "var(--text-label)",
                display: "flex",
                flexDirection: "column",
                gap: "var(--space-1)",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "var(--color-text-muted)" }}>type</span>
                <span>{data.current_request.type}</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "var(--color-text-muted)" }}>caller</span>
                <span>{data.current_request.caller ?? "—"}</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "var(--color-text-muted)" }}>model</span>
                <span>{data.current_request.model}</span>
              </div>
            </div>
          ) : (
            <div
              style={{
                background: "var(--color-surface)",
                borderRadius: 8,
                padding: "var(--space-3)",
                fontSize: "var(--text-label)",
                color: "var(--color-text-muted)",
              }}
            >
              Idle
            </div>
          )}
        </div>

        {/* Rate limits */}
        <div>
          <div
            style={{
              fontSize: "var(--text-eyebrow)",
              letterSpacing: "var(--tracking-eyebrow)",
              textTransform: "uppercase",
              color: "var(--color-text-muted)",
              marginBottom: "var(--space-2)",
            }}
          >
            Rate Limits
          </div>
          <div
            style={{
              background: "var(--color-surface)",
              borderRadius: 8,
              padding: "var(--space-3)",
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-2)",
            }}
          >
            {rateLimitEntries.length > 0 ? (
              rateLimitEntries.map(([caller, limits]) => (
                <div key={caller}>
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      marginBottom: "var(--space-1)",
                    }}
                  >
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: "var(--text-label)",
                      }}
                    >
                      {caller}
                    </span>
                    <span
                      style={{
                        fontSize: "var(--text-label)",
                        color: "var(--color-text-muted)",
                      }}
                    >
                      {limits.minute_count}/{limits.minute_limit} rpm
                    </span>
                  </div>
                  <RateLimitBar
                    count={limits.minute_count}
                    limit={limits.minute_limit}
                  />
                </div>
              ))
            ) : (
              <span
                style={{
                  fontSize: "var(--text-label)",
                  color: "var(--color-text-muted)",
                }}
              >
                No active callers
              </span>
            )}
          </div>
        </div>
      </div>

      {/* View full link */}
      <div style={{ marginTop: "var(--space-3)", textAlign: "right" }}>
        <Link
          to="/llm-gateway"
          style={{
            fontSize: "var(--text-label)",
            color: "var(--color-accent)",
            textDecoration: "none",
          }}
        >
          View full LLM Gateway →
        </Link>
      </div>
    </ChartCard>
  );
}
