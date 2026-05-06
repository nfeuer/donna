import client from "./client";

// Slice 23 — dashboard runtime overrides for the manual-escalation
// subsystem. Backend at src/donna/api/routes/admin_escalation_settings.py.
// Spec: docs/superpowers/specs/manual-escalation.md §6.3(a) / §10.7 row 1.

export type EscalationSettingValueType = "bool" | "float" | "str";

export interface EscalationSetting {
  key: string;
  value: boolean | number | string;
  default: boolean | number | string;
  value_type: EscalationSettingValueType;
  description: string;
  updated_at: string | null;
  updated_by: string | null;
}

export type TaskTypeOverride =
  | "auto"
  | "force_api"
  | "force_manual"
  | "disabled";

export interface TaskTypeOverrideRow {
  task_type: string;
  key: string;
  manual_mode: "chat" | "claude_code";
  value: TaskTypeOverride;
  default: TaskTypeOverride;
  updated_at: string | null;
  updated_by: string | null;
}

export interface EscalationSettingsConstraints {
  task_type_override_values: TaskTypeOverride[];
  max_daily_extension_cap_usd: number;
  max_daily_extension_cap_basis: {
    hard_monthly_ceiling_usd: number;
    days_left_in_month: number;
  };
}

export interface EscalationSettingsResponse {
  settings: EscalationSetting[];
  task_type_overrides: TaskTypeOverrideRow[];
  constraints: EscalationSettingsConstraints;
}

export interface EscalationSettingWriteResponse {
  key: string;
  value: boolean | number | string;
  updated_at: string;
  updated_by: string;
}

export interface TaskTypeOverrideWriteResponse {
  task_type: string;
  key: string;
  value: TaskTypeOverride;
  updated_at: string;
  updated_by: string;
}

export interface SettingConflict {
  status: 409;
  current_value: boolean | number | string;
  current_updated_at: string;
  current_updated_by: string;
}

export async function fetchEscalationSettings(): Promise<EscalationSettingsResponse> {
  const { data } = await client.get("/admin/escalation-settings");
  return data;
}

export async function putEscalationSetting(
  key: string,
  value: boolean | number | string,
  expectedUpdatedAt: string | null,
): Promise<EscalationSettingWriteResponse> {
  const { data } = await client.put(
    `/admin/escalation-settings/${encodeURIComponent(key)}`,
    { value, expected_updated_at: expectedUpdatedAt },
  );
  return data;
}

export async function putTaskTypeOverride(
  taskType: string,
  value: TaskTypeOverride,
  expectedUpdatedAt: string | null,
): Promise<TaskTypeOverrideWriteResponse> {
  const { data } = await client.put(
    `/admin/escalation-settings/task-types/${encodeURIComponent(taskType)}`,
    { value, expected_updated_at: expectedUpdatedAt },
  );
  return data;
}
