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
  // Slice 24 — task_type lets the UI distinguish escalation lifecycle
  // events from tool-gap lifecycle events (slice 22) on the same row.
  // Optional for backward compatibility with old detail responses.
  task_type?: string;
}

export interface EscalationTimelineResponse {
  escalation_id: number;
  correlation_id: string;
  timeline: EscalationTimelineEvent[];
  next_after_id: string | null;
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

// Slice 24 (spec §10.10) — standalone timeline poll. Pass `afterId` from
// the previous response's `next_after_id` to fetch only newly-landed
// audit events. Backed by GET /admin/escalations/{id}/timeline which
// merges escalation_lifecycle (slice 17) and tool_gap_lifecycle
// (slice 22) audit rows for the same escalation_request_id.
export async function fetchEscalationTimeline(
  correlationId: string,
  afterId: string | null = null,
  limit = 200,
): Promise<EscalationTimelineResponse> {
  const params: Record<string, string | number> = { limit };
  if (afterId) params.after_id = afterId;
  const { data } = await client.get(
    `/admin/escalations/${encodeURIComponent(correlationId)}/timeline`,
    { params },
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
