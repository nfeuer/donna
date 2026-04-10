import { useState, useCallback, useEffect } from "react";
import { PageHeader, Select, SelectItem } from "../../primitives";
import RefreshButton from "../../components/RefreshButton";
import ShadowCharts from "./ShadowCharts";
import ComparisonTable from "./ComparisonTable";
import SpotCheckTable from "./SpotCheckTable";
import ComparisonDrawer from "./ComparisonDrawer";
import {
  fetchShadowComparisons,
  fetchShadowStats,
  fetchSpotChecks,
  type ShadowComparison,
  type ShadowStats,
  type SpotCheckItem,
} from "../../api/shadow";
import styles from "./Shadow.module.css";

const TASK_TYPE_OPTIONS = [
  { value: "parse_task", label: "parse_task" },
  { value: "classify_priority", label: "classify_priority" },
  { value: "extract_deadline", label: "extract_deadline" },
  { value: "generate_nudge", label: "generate_nudge" },
  { value: "prep_work", label: "prep_work" },
];

const DAYS_OPTIONS = [
  { value: "7", label: "7 days" },
  { value: "14", label: "14 days" },
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
];

export default function ShadowPage() {
  const [taskType, setTaskType] = useState("");
  const [days, setDays] = useState("30");

  // Data
  const [comparisons, setComparisons] = useState<ShadowComparison[]>([]);
  const [stats, setStats] = useState<ShadowStats | null>(null);
  const [spotChecks, setSpotChecks] = useState<SpotCheckItem[]>([]);
  const [spotTotal, setSpotTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  // Drawer
  const [selectedComparison, setSelectedComparison] = useState<ShadowComparison | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const doFetch = useCallback(async () => {
    setLoading(true);
    try {
      const [compResp, statsResp, spotResp] = await Promise.all([
        fetchShadowComparisons({
          task_type: taskType || undefined,
          days: Number(days),
          limit: 50,
        }),
        fetchShadowStats(Number(days)),
        fetchSpotChecks(50, 0),
      ]);
      setComparisons(compResp.comparisons);
      setStats(statsResp);
      setSpotChecks(spotResp.items);
      setSpotTotal(spotResp.total);
    } catch {
      setComparisons([]);
      setStats(null);
      setSpotChecks([]);
      setSpotTotal(0);
    } finally {
      setLoading(false);
    }
  }, [taskType, days]);

  useEffect(() => {
    doFetch();
  }, [doFetch]);

  const handleRowClick = (row: ShadowComparison) => {
    setSelectedComparison(row);
    setDrawerOpen(true);
  };

  const selectedId = selectedComparison
    ? `${selectedComparison.primary.id}-${selectedComparison.shadow.id}`
    : null;

  return (
    <div>
      <PageHeader
        title="Shadow"
        meta="Evaluation comparisons"
        actions={
          <div className={styles.filters}>
            <Select
              value={taskType}
              onValueChange={setTaskType}
              placeholder="All task types"
              aria-label="Filter by task type"
            >
              {TASK_TYPE_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
              ))}
            </Select>
            <Select
              value={days}
              onValueChange={setDays}
              aria-label="Filter by time range"
            >
              {DAYS_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
              ))}
            </Select>
            <RefreshButton onRefresh={doFetch} />
          </div>
        }
      />

      <ShadowCharts comparisons={comparisons} stats={stats} loading={loading} />

      <section className={styles.section}>
        <div className={styles.sectionHeader}>
          <h2 className={styles.sectionTitle}>
            Comparisons
            <span className={styles.sectionCount}>{comparisons.length}</span>
          </h2>
        </div>
        <ComparisonTable
          comparisons={comparisons}
          loading={loading}
          selectedId={selectedId}
          onRowClick={handleRowClick}
        />
      </section>

      <section className={styles.section}>
        <div className={styles.sectionHeader}>
          <h2 className={styles.sectionTitle}>
            Spot Checks
            <span className={styles.sectionCount}>{spotTotal}</span>
          </h2>
        </div>
        <SpotCheckTable
          items={spotChecks}
          total={spotTotal}
          loading={loading}
        />
      </section>

      <ComparisonDrawer
        comparison={selectedComparison}
        open={drawerOpen}
        onOpenChange={setDrawerOpen}
      />
    </div>
  );
}
