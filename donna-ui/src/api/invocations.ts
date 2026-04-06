import client from "./client";

export interface Invocation {
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

export interface InvocationsResponse {
  invocations: Invocation[];
  total: number;
  limit: number;
  offset: number;
}

export interface InvocationDetail extends Invocation {
  input_hash: string;
  output: Record<string, unknown> | null;
  eval_session_id: string | null;
  linked_task: {
    id: string;
    title: string;
    status: string;
    domain: string;
    priority: number;
    created_at: string;
    assigned_agent: string | null;
    agent_status: string | null;
  } | null;
}

export async function fetchInvocations(params: {
  task_type?: string;
  model?: string;
  is_shadow?: boolean;
  task_id?: string;
  limit?: number;
  offset?: number;
}): Promise<InvocationsResponse> {
  const { data } = await client.get("/admin/invocations", { params });
  return data;
}

export async function fetchInvocation(id: string): Promise<InvocationDetail> {
  const { data } = await client.get(`/admin/invocations/${id}`);
  return data;
}
