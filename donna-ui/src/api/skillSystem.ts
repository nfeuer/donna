import client from "./client";

// ---------------------------------------------------------------------------
// Skills
// ---------------------------------------------------------------------------

export interface Skill {
  id: string;
  capability_name: string;
  state: string;
  requires_human_gate: boolean;
  baseline_agreement: number | null;
  current_version_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface SkillVersion {
  id: string;
  version_number: number;
  yaml_backbone: string;
  step_content: Record<string, unknown>;
  output_schemas: Record<string, unknown>;
  created_by: string;
  changelog: string | null;
}

export interface SkillDetail extends Skill {
  current_version?: SkillVersion;
}

export interface SkillsResponse {
  skills: Skill[];
  count: number;
}

export interface TransitionRow {
  from_state: string;
  to_state: string;
  allowed_reasons: string[];
}

export interface TransitionsResponse {
  transitions: TransitionRow[];
}

export async function fetchSkills(params: {
  state?: string;
  limit?: number;
} = {}): Promise<SkillsResponse> {
  const query: Record<string, string | number> = { limit: params.limit ?? 200 };
  if (params.state) query.state = params.state;
  const { data } = await client.get("/admin/skills", { params: query });
  return data;
}

export async function fetchSkillDetail(skillId: string): Promise<SkillDetail> {
  const { data } = await client.get(`/admin/skills/${skillId}`);
  return data;
}

export async function fetchSkillTransitions(): Promise<TransitionsResponse> {
  const { data } = await client.get("/admin/skills/_transitions");
  return data;
}

export async function transitionSkillState(
  skillId: string,
  body: { to_state: string; reason: string; notes?: string | null },
): Promise<{ skill_id: string; to_state: string; ok: boolean }> {
  const { data } = await client.post(`/admin/skills/${skillId}/state`, body);
  return data;
}

export async function setRequiresHumanGate(
  skillId: string,
  value: boolean,
): Promise<{ skill_id: string; requires_human_gate: boolean }> {
  const { data } = await client.post(
    `/admin/skills/${skillId}/flags/requires_human_gate`,
    { value },
  );
  return data;
}

// ---------------------------------------------------------------------------
// Candidates
// ---------------------------------------------------------------------------

export interface SkillCandidate {
  id: string;
  capability_name: string | null;
  task_pattern_hash: string | null;
  expected_savings_usd: number;
  volume_30d: number;
  variance_score: number | null;
  status: string;
  reported_at: string;
  resolved_at: string | null;
}

export interface CandidatesResponse {
  candidates: SkillCandidate[];
  count: number;
}

export async function fetchSkillCandidates(params: {
  status?: string;
  limit?: number;
} = {}): Promise<CandidatesResponse> {
  const query: Record<string, string | number> = {
    limit: params.limit ?? 100,
  };
  if (params.status !== undefined) query.status = params.status;
  const { data } = await client.get("/admin/skill-candidates", { params: query });
  return data;
}

export async function dismissCandidate(
  candidateId: string,
): Promise<{ candidate_id: string; status: string }> {
  const { data } = await client.post(
    `/admin/skill-candidates/${candidateId}/dismiss`,
  );
  return data;
}

export async function draftCandidateNow(
  candidateId: string,
): Promise<{ status: string; manual_draft_at: string }> {
  const { data } = await client.post(
    `/admin/skill-candidates/${candidateId}/draft-now`,
  );
  return data;
}

// ---------------------------------------------------------------------------
// Drafts
// ---------------------------------------------------------------------------

export interface SkillDraft {
  id: string;
  capability_name: string;
  current_version_id: string | null;
  state: string;
  requires_human_gate: boolean;
  baseline_agreement: number | null;
  created_at: string;
  updated_at: string;
}

export interface DraftsResponse {
  drafts: SkillDraft[];
  count: number;
}

export async function fetchSkillDrafts(limit = 100): Promise<DraftsResponse> {
  const { data } = await client.get("/admin/skill-drafts", {
    params: { limit },
  });
  return data;
}

// ---------------------------------------------------------------------------
// Runs
// ---------------------------------------------------------------------------

export interface SkillRun {
  id: string;
  skill_id: string;
  skill_version_id: string;
  status: string;
  total_latency_ms: number | null;
  total_cost_usd: number | null;
  escalation_reason: string | null;
  error: string | null;
  user_id: string;
  started_at: string;
  finished_at: string | null;
}

export interface SkillStepResult {
  id: string;
  step_name: string;
  step_index: number;
  step_kind: string;
  output: Record<string, unknown> | null;
  tool_calls: unknown[] | null;
  latency_ms: number | null;
  validation_status: string;
  error: string | null;
}

export interface SkillRunDetail extends SkillRun {
  state_object: Record<string, unknown>;
  final_output: Record<string, unknown> | null;
  step_results: SkillStepResult[];
}

export interface SkillRunsResponse {
  runs: SkillRun[];
  count: number;
}

export interface SkillDivergence {
  id: string;
  skill_run_id: string;
  shadow_invocation_id: string;
  overall_agreement: number;
  diff_summary: Record<string, unknown> | null;
  flagged_for_evolution: boolean;
  created_at: string;
}

export async function fetchSkillRuns(params: {
  status?: string;
  limit?: number;
} = {}): Promise<SkillRunsResponse> {
  const query: Record<string, string | number> = {
    limit: params.limit ?? 100,
  };
  if (params.status) query.status = params.status;
  const { data } = await client.get("/admin/skill-runs", { params: query });
  return data;
}

export async function fetchRunsForSkill(
  skillId: string,
  limit = 50,
): Promise<SkillRunsResponse> {
  const { data } = await client.get(`/admin/skills/${skillId}/runs`, {
    params: { limit },
  });
  return data;
}

export async function fetchSkillRunDetail(
  runId: string,
): Promise<SkillRunDetail> {
  const { data } = await client.get(`/admin/skill-runs/${runId}`);
  return data;
}

export async function fetchSkillRunDivergence(
  runId: string,
): Promise<SkillDivergence | null> {
  try {
    const { data } = await client.get(
      `/admin/skill-runs/${runId}/divergence`,
    );
    return data;
  } catch (err: unknown) {
    // 404 just means no divergence recorded — return null rather than throwing.
    if (
      typeof err === "object" &&
      err !== null &&
      "response" in err &&
      (err as { response?: { status?: number } }).response?.status === 404
    ) {
      return null;
    }
    throw err;
  }
}

export async function captureRunFixture(
  runId: string,
): Promise<{ fixture_id: string; source: string }> {
  const { data } = await client.post(
    `/admin/skill-runs/${runId}/capture-fixture`,
  );
  return data;
}

// ---------------------------------------------------------------------------
// Automations
// ---------------------------------------------------------------------------

export interface Automation {
  id: string;
  user_id: string;
  name: string;
  description: string | null;
  capability_name: string;
  inputs: Record<string, unknown>;
  trigger_type: string;
  schedule: string | null;
  alert_conditions: Record<string, unknown>;
  alert_channels: string[];
  max_cost_per_run_usd: number | null;
  min_interval_seconds: number;
  status: string;
  last_run_at: string | null;
  next_run_at: string | null;
  run_count: number;
  failure_count: number;
  created_at: string;
  updated_at: string;
  created_via: string;
}

export interface AutomationRun {
  id: string;
  automation_id: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  execution_path: string;
  skill_run_id: string | null;
  invocation_log_id: string | null;
  output: Record<string, unknown> | null;
  alert_sent: number;
  alert_content: string | null;
  error: string | null;
  cost_usd: number | null;
}

export interface AutomationsResponse {
  automations: Automation[];
  count: number;
}

export interface AutomationRunsResponse {
  runs: AutomationRun[];
  count: number;
}

export interface CreateAutomationBody {
  user_id: string;
  name: string;
  description?: string | null;
  capability_name: string;
  inputs: Record<string, unknown>;
  trigger_type: string;
  schedule?: string | null;
  alert_conditions?: Record<string, unknown>;
  alert_channels?: string[];
  max_cost_per_run_usd?: number | null;
  min_interval_seconds?: number;
  created_via?: string;
}

export interface UpdateAutomationBody {
  name?: string;
  description?: string | null;
  inputs?: Record<string, unknown>;
  schedule?: string | null;
  alert_conditions?: Record<string, unknown>;
  alert_channels?: string[];
  max_cost_per_run_usd?: number | null;
  min_interval_seconds?: number;
}

export async function fetchAutomations(params: {
  status?: string;
  capability_name?: string;
  limit?: number;
} = {}): Promise<AutomationsResponse> {
  const query: Record<string, string | number> = {
    status: params.status ?? "active",
    limit: params.limit ?? 100,
  };
  if (params.capability_name) query.capability_name = params.capability_name;
  const { data } = await client.get("/admin/automations", { params: query });
  return data;
}

export async function fetchAutomationDetail(id: string): Promise<Automation> {
  const { data } = await client.get(`/admin/automations/${id}`);
  return data;
}

export async function createAutomation(
  body: CreateAutomationBody,
): Promise<Automation> {
  const { data } = await client.post("/admin/automations", body);
  return data;
}

export async function updateAutomation(
  id: string,
  body: UpdateAutomationBody,
): Promise<Automation> {
  const { data } = await client.patch(`/admin/automations/${id}`, body);
  return data;
}

export async function deleteAutomation(
  id: string,
): Promise<{ id: string; status: string }> {
  const { data } = await client.delete(`/admin/automations/${id}`);
  return data;
}

export async function pauseAutomation(id: string): Promise<Automation> {
  const { data } = await client.post(`/admin/automations/${id}/pause`);
  return data;
}

export async function resumeAutomation(id: string): Promise<Automation> {
  const { data } = await client.post(`/admin/automations/${id}/resume`);
  return data;
}

export async function runAutomationNow(
  id: string,
): Promise<{ status: string; next_run_at: string }> {
  const { data } = await client.post(`/admin/automations/${id}/run-now`);
  return data;
}

export async function fetchAutomationRuns(
  id: string,
  limit = 50,
): Promise<AutomationRunsResponse> {
  const { data } = await client.get(`/admin/automations/${id}/runs`, {
    params: { limit },
  });
  return data;
}
