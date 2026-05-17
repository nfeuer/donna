import client from "./client";

export interface ClaudeCall {
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
  user_id: string;
  estimated_tokens_in: number | null;
  overflow_escalated: boolean;
  has_payload: boolean;
}

export interface ClaudeCallsResponse {
  calls: ClaudeCall[];
  total: number;
  limit: number;
  offset: number;
}

export interface ClaudePayload {
  request: {
    messages: Array<{ role: string; content: string }>;
    model: string;
    tools: unknown[] | null;
    max_tokens: number | null;
  };
  response: {
    content: unknown;
    usage: { input_tokens: number; output_tokens: number };
    stop_reason: string;
    model_actual: string;
  };
}

export interface CostCenter {
  task_type: string;
  total_cost: number;
  call_count: number;
  avg_tokens_in: number;
  avg_tokens_out: number;
}

export interface SystemPromptGroup {
  hash: string;
  call_count: number;
  avg_tokens_in: number;
  estimated_weekly_cost: number;
  sample_invocation_id: string;
}

export interface QualityCostMismatch {
  task_type: string;
  avg_cost: number;
  avg_quality_score: number;
  call_count: number;
}

export interface TokenBloatOutlier {
  invocation_id: string;
  task_type: string;
  tokens_in: number;
  median_for_type: number;
  ratio: number;
  cost_usd: number;
}

export interface ClaudeInsights {
  top_cost_centers: CostCenter[];
  system_prompt_groups: SystemPromptGroup[];
  quality_cost_mismatches: QualityCostMismatch[];
  token_bloat_outliers: TokenBloatOutlier[];
}

export interface ClaudeCallsParams {
  task_type?: string;
  model?: string;
  date_from?: string;
  date_to?: string;
  min_cost?: number;
  min_tokens_in?: number;
  quality_score_below?: number;
  sort?: string;
  sort_dir?: string;
  limit?: number;
  offset?: number;
}

export async function fetchClaudeCalls(
  params: ClaudeCallsParams
): Promise<ClaudeCallsResponse> {
  const { data } = await client.get("/admin/claude/calls", { params });
  return data;
}

export async function fetchClaudePayload(
  invocationId: string
): Promise<ClaudePayload> {
  const { data } = await client.get(
    `/admin/claude/calls/${invocationId}/payload`
  );
  return data;
}

export async function fetchClaudeInsights(
  days: number = 7
): Promise<ClaudeInsights> {
  const { data } = await client.get("/admin/claude/insights", {
    params: { days },
  });
  return data;
}
