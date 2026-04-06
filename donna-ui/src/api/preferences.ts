import client from "./client";

export interface PreferenceRule {
  id: string;
  user_id: string;
  rule_type: string;
  rule_text: string;
  confidence: number;
  condition: Record<string, unknown> | null;
  action: Record<string, unknown> | null;
  supporting_corrections: string[];
  enabled: boolean;
  created_at: string;
  disabled_at: string | null;
}

export interface RulesResponse {
  rules: PreferenceRule[];
  total: number;
  limit: number;
  offset: number;
}

export interface RuleFilters {
  enabled?: boolean;
  rule_type?: string;
  limit?: number;
  offset?: number;
}

export interface CorrectionEntry {
  id: string;
  timestamp: string;
  user_id: string;
  task_type: string;
  task_id: string;
  input_text: string | null;
  field_corrected: string;
  original_value: string;
  corrected_value: string;
  rule_extracted: string | null;
}

export interface CorrectionsResponse {
  corrections: CorrectionEntry[];
  total: number;
  limit: number;
  offset: number;
}

export interface CorrectionFilters {
  field?: string;
  task_type?: string;
  limit?: number;
  offset?: number;
}

export interface PreferenceStats {
  total_rules: number;
  active_rules: number;
  disabled_rules: number;
  avg_confidence: number | null;
  total_corrections: number;
  top_fields: { field: string; count: number }[];
}

export async function fetchPreferenceRules(
  filters: RuleFilters = {},
): Promise<RulesResponse> {
  const params: Record<string, string | number | boolean> = {};
  if (filters.enabled !== undefined) params.enabled = filters.enabled;
  if (filters.rule_type) params.rule_type = filters.rule_type;
  params.limit = filters.limit ?? 50;
  params.offset = filters.offset ?? 0;
  const { data } = await client.get("/admin/preferences/rules", { params });
  return data;
}

export async function toggleRule(
  id: string,
  enabled: boolean,
): Promise<PreferenceRule> {
  const { data } = await client.patch(`/admin/preferences/rules/${id}`, {
    enabled,
  });
  return data;
}

export async function fetchCorrections(
  filters: CorrectionFilters = {},
): Promise<CorrectionsResponse> {
  const params: Record<string, string | number> = {};
  if (filters.field) params.field = filters.field;
  if (filters.task_type) params.task_type = filters.task_type;
  params.limit = filters.limit ?? 50;
  params.offset = filters.offset ?? 0;
  const { data } = await client.get("/admin/preferences/corrections", {
    params,
  });
  return data;
}

export async function fetchPreferenceStats(): Promise<PreferenceStats> {
  const { data } = await client.get("/admin/preferences/stats");
  return data;
}
