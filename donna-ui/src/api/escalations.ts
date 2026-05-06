import client from "./client";

export type EscalationStatus =
  | "open"
  | "resolved"
  | "submitted"
  | "validated"
  | "failed"
  | "cancelled";

export type EscalationMode = "chat" | "claude_code";

export interface EscalationSummary {
  id: number;
  correlation_id: string;
  user_id: string;
  task_id: string | null;
  task_type: string;
  estimate_usd: number;
  daily_remaining_usd: number;
  offered_modes: string[];
  resolution: string | null;
  mode: EscalationMode | null;
  status: EscalationStatus;
  iteration: number;
  priority: number;
  summary: string | null;
  branch_name: string | null;
  // Slice 21
  human_review?: boolean;
  merged_at?: string | null;
  created_at: string;
  resolved_at: string | null;
  submitted_at: string | null;
  validated_at: string | null;
}

export interface EscalationDetail extends EscalationSummary {
  prompt_path: string | null;
  prompt_body: string | null;
  result: string | null;
  validation_result: Record<string, unknown> | null;
  // Slice 21 additions
  target_paths?: Record<string, string> | null;
  originating_entity_type?: string | null;
  originating_entity_id?: string | null;
  base_sha?: string | null;
}

export interface EscalationTimelineEvent {
  id: string;
  timestamp: string;
  event: string | null;
  payload: Record<string, unknown>;
}

export interface EscalationListResponse {
  items: EscalationSummary[];
  total: number;
  status_counts: Record<string, number>;
  limit: number;
  offset: number;
}

export interface EscalationDetailResponse {
  escalation: EscalationDetail;
  timeline: EscalationTimelineEvent[];
}

export interface EscalationListFilters {
  status?: EscalationStatus | "";
  user_id?: string;
  limit?: number;
  offset?: number;
}

export type EscalationSubmissionPayload =
  | { mode: "chat"; answer: string }
  | { mode: "claude_code"; branch: string; sha?: string };

export interface EscalationSubmitResponse {
  correlation_id: string;
  status: EscalationStatus;
  submitted_at: string;
  iteration: number;
  mode: EscalationMode;
}

export async function fetchEscalations(
  filters: EscalationListFilters = {},
): Promise<EscalationListResponse> {
  const params: Record<string, string | number> = {};
  if (filters.status) params.status = filters.status;
  if (filters.user_id) params.user_id = filters.user_id;
  params.limit = filters.limit ?? 100;
  params.offset = filters.offset ?? 0;
  const { data } = await client.get("/admin/escalations", { params });
  return data;
}

export async function fetchEscalationDetail(
  correlationId: string,
): Promise<EscalationDetailResponse> {
  const { data } = await client.get(
    `/admin/escalations/${encodeURIComponent(correlationId)}`,
  );
  return data;
}

export async function submitEscalation(
  correlationId: string,
  payload: EscalationSubmissionPayload,
): Promise<EscalationSubmitResponse> {
  const { data } = await client.post(
    `/admin/escalations/${encodeURIComponent(correlationId)}/submit`,
    payload,
  );
  return data;
}

export interface EscalationMarkMergedResponse {
  correlation_id: string;
  merged_at: string;
}

// Slice 21: pure tracking write — Donna does NOT auto-merge (spec §15).
// The user runs `git checkout main && git merge --no-ff <branch>` themselves
// and clicks this when they're done so the dashboard reflects the new state.
export async function markEscalationMerged(
  correlationId: string,
): Promise<EscalationMarkMergedResponse> {
  const { data } = await client.post(
    `/admin/escalations/${encodeURIComponent(correlationId)}/mark-merged`,
  );
  return data;
}
