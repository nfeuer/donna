import client from "./client";

export interface LogEntry {
  timestamp: string;
  level: string;
  event_type: string;
  message: string;
  service: string;
  component: string;
  correlation_id: string;
  task_id: string;
  user_id: string;
  agent_id?: string;
  duration_ms?: number;
  cost_usd?: number;
  extra: Record<string, unknown>;
}

export interface LogsResponse {
  entries: LogEntry[];
  total: number;
  limit: number;
  offset: number;
  source: string;
}

export interface LogFilters {
  event_type?: string;
  level?: string;
  service?: string;
  search?: string;
  correlation_id?: string;
  task_id?: string;
  start?: string;
  end?: string;
  limit?: number;
  offset?: number;
}

export interface TraceResponse {
  correlation_id: string;
  entries: LogEntry[];
  source: string;
  count: number;
}

export async function fetchLogs(filters: LogFilters): Promise<LogsResponse> {
  const { data } = await client.get("/admin/logs", { params: filters });
  return data;
}

export async function fetchTrace(
  correlationId: string,
): Promise<TraceResponse> {
  const { data } = await client.get(`/admin/logs/trace/${correlationId}`);
  return data;
}

export async function fetchEventTypes(): Promise<Record<string, string[]>> {
  const { data } = await client.get("/admin/logs/event-types");
  return data;
}
