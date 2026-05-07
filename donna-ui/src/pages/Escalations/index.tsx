import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { PageHeader } from "../../primitives/PageHeader";
import { Select, SelectItem } from "../../primitives/Select";
import RefreshButton from "../../components/RefreshButton";
import EscalationsTable from "./EscalationsTable";
import {
  fetchEscalations,
  type EscalationListResponse,
  type EscalationStatus,
  type EscalationSummary,
} from "../../api/escalations";
import styles from "./Escalations.module.css";

const STATUS_OPTIONS: { value: "all" | EscalationStatus; label: string }[] = [
  { value: "all", label: "All statuses" },
  { value: "open", label: "Open" },
  { value: "resolved", label: "Resolved" },
  { value: "submitted", label: "Submitted" },
  { value: "validated", label: "Validated" },
  { value: "failed", label: "Failed" },
  { value: "cancelled", label: "Cancelled" },
];

export default function EscalationsPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<"all" | EscalationStatus>("all");
  const [resp, setResp] = useState<EscalationListResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const doFetch = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchEscalations({ status: status === "all" ? undefined : status });
      setResp(data);
    } catch {
      setResp(null);
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => {
    doFetch();
  }, [doFetch]);

  const items = resp?.items ?? [];
  const counts = resp?.status_counts ?? {};

  const handleRowClick = useCallback(
    (row: EscalationSummary) => {
      navigate(`/escalations/${encodeURIComponent(row.correlation_id)}`);
    },
    [navigate],
  );

  return (
    <div>
      <PageHeader
        eyebrow="Operations"
        title="Escalations"
        meta="Manual handoff workspace"
        actions={
          <div className={styles.filters}>
            <Select
              value={status}
              onValueChange={(v) => setStatus(v as "all" | EscalationStatus)}
              aria-label="Filter by status"
            >
              {STATUS_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </Select>
            <RefreshButton onRefresh={doFetch} autoRefreshMs={30_000} />
          </div>
        }
      />

      <section className={styles.section}>
        <div className={styles.sectionHeader}>
          <h2 className={styles.sectionTitle}>
            Escalation Requests
            <span className={styles.sectionCount}>
              {items.length} shown · {resp?.total ?? 0} total
            </span>
          </h2>
          <div className={styles.muted}>
            {Object.entries(counts).map(([k, v]) => (
              <span key={k} className={styles.statCount}>
                {k}: {v}
              </span>
            ))}
          </div>
        </div>
        <EscalationsTable
          items={items}
          loading={loading}
          onRowClick={handleRowClick}
        />
      </section>
    </div>
  );
}
