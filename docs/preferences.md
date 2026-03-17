# User Preference Learning

> Split from Donna Project Spec v3.0 — Section 9

## Principle

Adapts to user behavior without model fine-tuning. Logs corrections, extracts patterns, applies learned rules. All preferences are transparent, editable, and reversible.

## Correction Logging

When the user corrects a system output (changes domain, priority, scheduled time), the correction is logged:

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Correction identifier |
| timestamp | DateTime | When corrected |
| user_id | String | Who made the correction |
| task_type | String | Which task type was wrong (e.g., parse_task) |
| task_id | UUID | Specific task corrected |
| input_text | String | Original natural language input |
| field_corrected | String | Which field changed (domain, priority, etc.) |
| original_value | String | System's output |
| corrected_value | String | User's correction |
| rule_extracted | UUID? | Link to extracted rule, if one was created |

## Rule Extraction

Runs on configurable schedule (default: weekly) or on demand. Batches recent corrections → sends to Claude API for pattern analysis → outputs structured rules.

Example extracted rule:

```json
{
  "rule": "Tasks mentioning vehicle/car/automotive → domain: personal",
  "confidence": 0.9,
  "supporting_corrections": ["uuid1", "uuid3", "uuid7"],
  "rule_type": "domain_override",
  "condition": {"keywords": ["car", "oil change", "tire", "vehicle"]},
  "action": {"field": "domain", "value": "personal"}
}
```

## Learnable Preference Types

| Type | Mechanism | Example |
|------|-----------|---------|
| **Domain overrides** | Keyword-based rules | "Anything about cars is always personal." |
| **Priority adjustments** | Source/entity-based rules | "Tasks from [boss] are always priority 4 minimum." |
| **Scheduling preferences** | Extracted from reschedule patterns | "Nick never does deep work before 10am." |
| **Notification preferences** | Extracted from response patterns | "Nick ignores app notifications but responds to SMS within 10 min." |
| **Few-shot examples** | Well-handled corrections → examples in prompt templates | Prompt templates support `examples_file` field pointing to accumulated examples JSON. |

## Preference Application

Applied **after** initial model processing as a post-processing step:

1. Model produces structured output (first draft)
2. Preference engine checks applicable rules
3. Matching rules override relevant fields
4. Orchestrator uses final output for scheduling/routing

## Transparency & Control

All learned preferences stored as readable, editable entries:

```
Active Preferences:
1. Car/vehicle tasks → domain: personal (learned from 5 corrections)
2. Tasks from [boss] → priority: 4 minimum (learned from 3 corrections)
3. Never schedule personal tasks before 10am (learned from 8 reschedules)
[edit] [disable] [delete]
```

If a rule causes corrections in the opposite direction, it is auto-disabled and flagged for user review.

## Self-Learning Scope

System-level only:
- Rule extraction from corrections
- Routing threshold adjustment from evaluation data
- Few-shot example accumulation

**Not** model fine-tuning. All learned preferences must be transparent, editable, and reversible.
