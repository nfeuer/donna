import client from "./client";

export interface ParseAccuracyData {
  summary: {
    total_parses: number;
    total_corrections: number;
    accuracy_pct: number;
    most_corrected_field: string | null;
  };
  time_series: Array<{
    date: string;
    parses: number;
    corrections: number;
    accuracy: number;
  }>;
  field_breakdown: Array<{ field: string; count: number }>;
  days: number;
}

export interface AgentPerformanceData {
  summary: {
    total_calls: number;
    avg_latency_ms: number;
    p95_latency_ms: number;
    total_cost_usd: number;
  };
  agents: Array<{
    task_type: string;
    call_count: number;
    avg_latency_ms: number;
    max_latency_ms: number;
    total_tokens_in: number;
    total_tokens_out: number;
    total_cost_usd: number;
    avg_cost_usd: number;
    scored_count: number;
    avg_quality_score: number | null;
  }>;
  time_series: Array<{
    date: string;
    breakdown: Array<{ task_type: string; count: number }>;
  }>;
  days: number;
}

export interface TaskThroughputData {
  summary: {
    total_created: number;
    total_completed: number;
    completion_rate: number;
    overdue_count: number;
    avg_reschedules: number;
    avg_completion_hours: number | null;
  };
  status_distribution: Record<string, number>;
  time_series: Array<{
    date: string;
    created: number;
    completed: number;
  }>;
  domain_breakdown: Array<{
    domain: string;
    total: number;
    completed: number;
  }>;
  days: number;
}

export interface CostAnalyticsData {
  summary: {
    today_cost_usd: number;
    today_calls: number;
    monthly_cost_usd: number;
    monthly_calls: number;
    projected_monthly_usd: number;
    daily_budget_usd: number;
    monthly_budget_usd: number;
    daily_utilization_pct: number;
    monthly_utilization_pct: number;
    daily_remaining_usd: number;
    monthly_remaining_usd: number;
  };
  time_series: Array<{
    date: string;
    cost_usd: number;
    calls: number;
  }>;
  by_task_type: Array<{
    task_type: string;
    cost_usd: number;
    calls: number;
  }>;
  by_model: Array<{
    model: string;
    cost_usd: number;
    calls: number;
  }>;
  days: number;
}

export async function fetchParseAccuracy(
  days: number,
): Promise<ParseAccuracyData> {
  const { data } = await client.get("/admin/dashboard/parse-accuracy", {
    params: { days },
  });
  return data;
}

export async function fetchAgentPerformance(
  days: number,
): Promise<AgentPerformanceData> {
  const { data } = await client.get("/admin/dashboard/agent-performance", {
    params: { days },
  });
  return data;
}

export async function fetchTaskThroughput(
  days: number,
): Promise<TaskThroughputData> {
  const { data } = await client.get("/admin/dashboard/task-throughput", {
    params: { days },
  });
  return data;
}

export async function fetchCostAnalytics(
  days: number,
): Promise<CostAnalyticsData> {
  const { data } = await client.get("/admin/dashboard/cost-analytics", {
    params: { days },
  });
  return data;
}

export interface QualityWarningsData {
  summary: {
    total_warnings: number;
    total_criticals: number;
    total_scored: number;
    warning_rate_pct: number;
  };
  thresholds: {
    warning_threshold: number;
    critical_threshold: number;
  };
  time_series: Array<{
    date: string;
    warnings: number;
    criticals: number;
  }>;
  by_task_type: Array<{
    task_type: string;
    warnings: number;
    criticals: number;
    total_scored: number;
  }>;
  days: number;
}

export async function fetchQualityWarnings(
  days: number,
): Promise<QualityWarningsData> {
  const { data } = await client.get("/admin/dashboard/quality-warnings", {
    params: { days },
  });
  return data;
}
