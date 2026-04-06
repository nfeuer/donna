import client from "./client";

export interface AgentSummary {
  name: string;
  enabled: boolean;
  timeout_seconds: number;
  autonomy: string;
  allowed_tools: string[];
  task_types: string[];
  total_calls: number;
  avg_latency_ms: number;
  total_cost_usd: number;
  last_invocation: string | null;
}

export interface AgentInvocation {
  id: string;
  timestamp: string;
  task_type: string;
  model_alias: string;
  latency_ms: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  is_shadow: boolean;
  task_id: string | null;
}

export interface DailyLatency {
  date: string;
  avg_latency_ms: number;
  calls: number;
}

export interface ToolUsage {
  tool: string;
  count: number;
}

export interface CostSummary {
  total_calls: number;
  total_cost_usd: number;
  avg_cost_per_call: number;
}

export interface AgentDetail extends AgentSummary {
  recent_invocations: AgentInvocation[];
  daily_latency: DailyLatency[];
  tool_usage: ToolUsage[];
  cost_summary: CostSummary;
}

export async function fetchAgents(): Promise<AgentSummary[]> {
  const resp = await client.get("/admin/agents");
  return resp.data.agents;
}

export async function fetchAgentDetail(name: string): Promise<AgentDetail> {
  const resp = await client.get(`/admin/agents/${name}`);
  return resp.data;
}
