import client from "./client";

export interface TaskSummary {
  id: string;
  user_id: string;
  title: string;
  description: string | null;
  domain: string | null;
  priority: number;
  status: string;
  estimated_duration: string | null;
  deadline: string | null;
  deadline_type: string | null;
  scheduled_start: string | null;
  actual_start: string | null;
  completed_at: string | null;
  parent_task: string | null;
  prep_work_flag: boolean;
  agent_eligible: boolean;
  assigned_agent: string | null;
  agent_status: string | null;
  tags: string[] | null;
  reschedule_count: number;
  created_at: string;
  created_via: string | null;
  nudge_count: number;
  quality_score: number | null;
  donna_managed: boolean;
}

export interface TaskInvocation {
  id: string;
  timestamp: string;
  task_type: string;
  model_alias: string;
  latency_ms: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  is_shadow: boolean;
}

export interface NudgeEvent {
  id: string;
  nudge_type: string;
  channel: string;
  escalation_tier: number;
  message_text: string;
  llm_generated: boolean;
  created_at: string;
}

export interface Correction {
  id: string;
  timestamp: string;
  field_corrected: string;
  original_value: string;
  corrected_value: string;
  task_type: string;
  input_text: string | null;
}

export interface Subtask {
  id: string;
  title: string;
  status: string;
  priority: number;
  assigned_agent: string | null;
  agent_status: string | null;
}

export interface TaskDetail extends TaskSummary {
  recurrence: string | null;
  dependencies: string[] | null;
  prep_work_instructions: string | null;
  notes: Record<string, unknown>[] | null;
  estimated_cost: number | null;
  calendar_event_id: string | null;
  invocations: TaskInvocation[];
  nudge_events: NudgeEvent[];
  corrections: Correction[];
  subtasks: Subtask[];
}

export interface TaskFilters {
  status?: string;
  domain?: string;
  priority?: number;
  search?: string;
  agent?: string;
  limit?: number;
  offset?: number;
}

export interface TasksResponse {
  tasks: TaskSummary[];
  total: number;
  limit: number;
  offset: number;
}

export async function fetchTasks(filters: TaskFilters = {}): Promise<TasksResponse> {
  const params: Record<string, string | number> = {};
  if (filters.status) params.status = filters.status;
  if (filters.domain) params.domain = filters.domain;
  if (filters.priority) params.priority = filters.priority;
  if (filters.search) params.search = filters.search;
  if (filters.agent) params.agent = filters.agent;
  params.limit = filters.limit ?? 50;
  params.offset = filters.offset ?? 0;

  const { data } = await client.get("/admin/tasks", { params });
  return data;
}

export async function fetchTask(id: string): Promise<TaskDetail> {
  const { data } = await client.get(`/admin/tasks/${id}`);
  return data;
}
