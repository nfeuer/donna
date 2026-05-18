import { useState, useEffect, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
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

const PARAM_KEYS: (keyof ClaudeCallsParams)[] = [
  "task_type", "model", "date_from", "date_to",
  "min_cost", "min_tokens_in", "quality_score_below",
  "sort", "sort_dir",
];

function paramsFromSearch(sp: URLSearchParams): ClaudeCallsParams {
  const p: ClaudeCallsParams = { ...DEFAULT_PARAMS };
  for (const key of PARAM_KEYS) {
    const val = sp.get(key);
    if (val) {
      if (key === "min_cost" || key === "min_tokens_in" || key === "quality_score_below") {
        const n = Number(val);
        if (!Number.isNaN(n)) (p as Record<string, unknown>)[key] = n;
      } else {
        (p as Record<string, unknown>)[key] = val;
      }
    }
  }
  const offset = sp.get("offset");
  if (offset) {
    const n = Number(offset);
    if (!Number.isNaN(n)) p.offset = n;
  }
  return p;
}

function paramsToSearch(params: ClaudeCallsParams): Record<string, string> {
  const out: Record<string, string> = {};
  for (const key of PARAM_KEYS) {
    const val = params[key];
    if (val !== undefined && val !== DEFAULT_PARAMS[key]) {
      out[key] = String(val);
    }
  }
  if (params.offset && params.offset > 0) out.offset = String(params.offset);
  return out;
}

export default function ClaudeInspector() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [insights, setInsights] = useState<ClaudeInsights | null>(null);
  const [callsData, setCallsData] = useState<ClaudeCallsResponse | null>(null);
  const [callsLoading, setCallsLoading] = useState(true);
  const [params, setParams] = useState<ClaudeCallsParams>(() => paramsFromSearch(searchParams));
  const [initialExpandId] = useState<string | null>(() => searchParams.get("id"));

  // Fetch insights once on mount
  useEffect(() => {
    fetchClaudeInsights(7)
      .then(setInsights)
      .catch(() => setInsights(null));
  }, []);

  // Sync params → URL
  useEffect(() => {
    const next = paramsToSearch(params);
    if (initialExpandId) next.id = initialExpandId;
    setSearchParams(next, { replace: true });
  }, [params, initialExpandId, setSearchParams]);

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
        initialExpandId={initialExpandId}
      />
    </div>
  );
}
