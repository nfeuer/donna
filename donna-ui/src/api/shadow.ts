import client from "./client";

export interface ShadowInvocation {
  id: string;
  timestamp: string;
  task_type: string;
  task_id: string | null;
  model_alias: string;
  model_actual: string;
  input_hash: string;
  latency_ms: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  output: Record<string, unknown> | null;
  quality_score: number | null;
  is_shadow: boolean;
  spot_check_queued: boolean;
  user_id: string;
}

export interface ShadowComparison {
  primary: ShadowInvocation;
  shadow: ShadowInvocation;
  quality_delta: number | null;
}

export interface ShadowComparisonsResponse {
  comparisons: ShadowComparison[];
  total: number;
}

export interface ShadowStats {
  primary_avg_quality: number | null;
  shadow_avg_quality: number | null;
  avg_delta: number | null;
  wins: number;
  losses: number;
  ties: number;
  primary_cost: number;
  shadow_cost: number;
  primary_count: number;
  shadow_count: number;
  trend: { date: string; avg_quality: number; count: number }[];
  days: number;
}

export interface SpotCheckItem {
  id: string;
  timestamp: string;
  task_type: string;
  task_id: string | null;
  model_alias: string;
  model_actual: string;
  latency_ms: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  quality_score: number | null;
  is_shadow: boolean;
  spot_check_queued: boolean;
  user_id: string;
}

export interface SpotChecksResponse {
  items: SpotCheckItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface ShadowComparisonFilters {
  task_type?: string;
  days?: number;
  limit?: number;
}

export async function fetchShadowComparisons(
  filters: ShadowComparisonFilters = {},
): Promise<ShadowComparisonsResponse> {
  const params: Record<string, string | number> = {};
  if (filters.task_type) params.task_type = filters.task_type;
  params.days = filters.days ?? 30;
  params.limit = filters.limit ?? 50;
  const { data } = await client.get("/admin/shadow/comparisons", { params });
  return data;
}

export async function fetchShadowStats(days = 30): Promise<ShadowStats> {
  const { data } = await client.get("/admin/shadow/stats", {
    params: { days },
  });
  return data;
}

export async function fetchSpotChecks(
  limit = 50,
  offset = 0,
): Promise<SpotChecksResponse> {
  const { data } = await client.get("/admin/shadow/spot-checks", {
    params: { limit, offset },
  });
  return data;
}
