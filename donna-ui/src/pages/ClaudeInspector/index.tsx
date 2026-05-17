import { useState, useEffect, useCallback } from "react";
import { PageHeader } from "../../primitives/PageHeader";
import {
  fetchClaudeCalls,
  fetchClaudeInsights,
  type ClaudeCallsResponse,
  type ClaudeCallsParams,
  type ClaudeInsights,
} from "../../api/claude";
import InsightsPanel from "./InsightsPanel";
import CallBrowser from "./CallBrowser";
import styles from "./claude-inspector.module.css";

const DEFAULT_PARAMS: ClaudeCallsParams = {
  limit: 25,
  offset: 0,
  sort: "timestamp",
  sort_dir: "desc",
};

export default function ClaudeInspector() {
  const [insights, setInsights] = useState<ClaudeInsights | null>(null);
  const [callsData, setCallsData] = useState<ClaudeCallsResponse | null>(null);
  const [callsLoading, setCallsLoading] = useState(true);
  const [params, setParams] = useState<ClaudeCallsParams>(DEFAULT_PARAMS);

  // Fetch insights once on mount
  useEffect(() => {
    fetchClaudeInsights(7)
      .then(setInsights)
      .catch(() => setInsights(null));
  }, []);

  // Fetch calls when params change
  useEffect(() => {
    let cancelled = false;
    setCallsLoading(true);

    fetchClaudeCalls(params)
      .then((data) => {
        if (!cancelled) setCallsData(data);
      })
      .catch(() => {
        if (!cancelled) setCallsData(null);
      })
      .finally(() => {
        if (!cancelled) setCallsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [params]);

  const handleParamsChange = useCallback((partial: Partial<ClaudeCallsParams>) => {
    setParams((prev) => ({ ...prev, ...partial }));
  }, []);

  const handleFilterTaskType = useCallback((taskType: string) => {
    setParams((prev) => ({
      ...prev,
      task_type: taskType || undefined,
      offset: 0,
    }));
  }, []);

  return (
    <div className={styles.page}>
      <PageHeader eyebrow="Forensics" title="Claude Inspector" />
      <InsightsPanel insights={insights} onFilterTaskType={handleFilterTaskType} />
      <CallBrowser
        data={callsData}
        loading={callsLoading}
        params={params}
        onParamsChange={handleParamsChange}
      />
    </div>
  );
}
