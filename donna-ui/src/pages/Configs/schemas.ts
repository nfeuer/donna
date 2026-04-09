// donna-ui/src/pages/Configs/schemas.ts
import { z } from "zod";

// ----- task_states.yaml -----
export const stateTransitionSchema = z.object({
  from: z.string(),
  to: z.string(),
  trigger: z.string(),
  side_effects: z.array(z.string()).optional(),
});

export const statesSchema = z.object({
  initial_state: z.string().optional(),
  states: z.array(z.string()),
  transitions: z.array(stateTransitionSchema),
});
export type StatesConfig = z.infer<typeof statesSchema>;

// ----- donna_models.yaml -----
export const modelEntrySchema = z.object({
  provider: z.string(),
  model: z.string(),
  estimated_cost_per_1k_tokens: z.number().nonnegative().optional(),
});

export const routingEntrySchema = z.object({
  model: z.string(),
  fallback: z.string().optional(),
  shadow: z.string().optional(),
  confidence_threshold: z.number().min(0).max(1).optional(),
});

export const modelsSchema = z.object({
  models: z.record(z.string(), modelEntrySchema).default({}),
  routing: z.record(z.string(), routingEntrySchema).default({}),
  cost: z
    .object({
      monthly_budget_usd: z.number().nonnegative().optional(),
      daily_pause_threshold_usd: z.number().nonnegative().optional(),
      task_approval_threshold_usd: z.number().nonnegative().optional(),
      monthly_warning_pct: z.number().min(0).max(1).optional(),
    })
    .default({}),
  quality_monitoring: z
    .object({
      enabled: z.boolean().optional(),
      spot_check_rate: z.number().min(0).max(1).optional(),
      flag_threshold: z.number().min(0).max(1).optional(),
    })
    .default({}),
});
export type ModelsConfig = z.infer<typeof modelsSchema>;

// ----- task_types.yaml -----
export const taskTypeEntrySchema = z.object({
  description: z.string().optional().default(""),
  model: z.string(),
  shadow: z.string().optional(),
  prompt_template: z.string().optional().default(""),
  output_schema: z.string().optional().default(""),
  tools: z.array(z.string()).optional().default([]),
});

export const taskTypesSchema = z.object({
  task_types: z.record(z.string(), taskTypeEntrySchema).default({}),
});
export type TaskTypesConfig = z.infer<typeof taskTypesSchema>;

// ----- agents.yaml -----
export const agentEntrySchema = z.object({
  enabled: z.boolean(),
  timeout_seconds: z.number().int().min(1),
  autonomy: z.enum(["low", "medium", "high"]),
  allowed_tools: z.array(z.string()).default([]),
});

export const agentsSchema = z.object({
  agents: z.record(z.string(), agentEntrySchema).default({}),
});
export type AgentsConfig = z.infer<typeof agentsSchema>;
