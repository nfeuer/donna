import client from "./client";

// --- Interfaces ---

export interface QueueItemPreview {
  sequence: number;
  caller: string | null;
  model: string;
  task_type: string | null;
  enqueued_at: string;
  prompt_preview: string;
}

export interface CurrentRequest {
  sequence: number;
  type: "internal" | "external";
  caller: string | null;
  model: string;
  started_at: string;
  task_type: string | null;
  prompt_preview: string;
}

export interface CallerRateLimit {
  minute_count: number;
  minute_limit: number;
  hour_count: number;
  hour_limit: number;
}

export interface LLMQueueStatusData {
  current_request: CurrentRequest | null;
  internal_queue: {
    pending: number;
    next_items: QueueItemPreview[];
  };
  external_queue: {
    pending: number;
    next_items: QueueItemPreview[];
  };
  stats_24h: {
    internal_completed: number;
    external_completed: number;
    external_interrupted: number;
  };
  rate_limits: Record<string, CallerRateLimit>;
  mode: "active" | "slow";
}

export interface LLMGatewayTimeSeriesEntry {
  date: string;
  internal: number;
  external: number;
  interrupted: number;
  avg_latency_ms: number;
}

export interface LLMGatewayCallerEntry {
  caller: string;
  call_count: number;
  avg_latency_ms: number;
  total_tokens_in: number;
  total_tokens_out: number;
  interrupted_count: number;
  rejected_count: number;
}

export interface LLMGatewayData {
  summary: {
    total_calls: number;
    internal_calls: number;
    external_calls: number;
    total_interrupted: number;
    avg_latency_ms: number;
    unique_callers: number;
  };
  time_series: LLMGatewayTimeSeriesEntry[];
  by_caller: LLMGatewayCallerEntry[];
  days: number;
}

export interface QueueItemDetail {
  sequence: number;
  type: "internal" | "external";
  caller: string | null;
  model: string;
  task_type: string | null;
  enqueued_at: string;
  prompt: string;
  max_tokens: number;
  json_mode: boolean;
}

// --- Fetch functions ---

export async function fetchLLMQueueStatus(): Promise<LLMQueueStatusData> {
  const { data } = await client.get("/llm/queue/status");
  return data;
}

export async function fetchLLMGatewayAnalytics(
  days: number,
): Promise<LLMGatewayData> {
  const { data } = await client.get("/admin/dashboard/llm-gateway", {
    params: { days },
  });
  return data;
}

export async function fetchQueueItemPrompt(
  sequence: number,
): Promise<QueueItemDetail> {
  const { data } = await client.get(`/llm/queue/item/${sequence}`);
  return data;
}

export function createQueueSSEUrl(): string {
  const base = import.meta.env.VITE_API_BASE_URL || "";
  return `${base}/llm/queue/stream`;
}
