import client from "./client";

export interface PromptInvocationStat {
  prompt: string;
  task_type: string;
  invocations: number;
  cost_usd: number;
}

export interface PromptAgentCoverage {
  prompt: string;
  agents: string[];
}

export interface PromptRecentlyModified {
  name: string;
  modified: number;
}

export interface PromptStats {
  total: number;
  by_folder: Record<string, number>;
  most_invoked: PromptInvocationStat[];
  agent_coverage: PromptAgentCoverage[];
  model_routing: Record<string, number>;
  recently_modified: PromptRecentlyModified[];
  unused: string[];
}

export async function fetchPromptStats(): Promise<PromptStats> {
  const { data } = await client.get("/admin/prompts/stats");
  return data;
}
