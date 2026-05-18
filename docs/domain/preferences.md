# User Preference Learning

> Split from Donna Project Spec v3.0 — Section 9

## Principle

Adapts to user behavior without model fine-tuning. Logs corrections, extracts patterns, applies learned rules. All preferences are transparent, editable, and reversible.

## Correction Logging

When the user corrects a system output (changes domain, priority, scheduled time), the correction is logged automatically via the event-driven pipeline.

### How it works

All user-initiated task updates flow through `Database.update_task()`, which emits a `task_updated` event on the `TaskEventBus` with field-level diffs and a `source` tag. `CorrectionSubscriber` listens for these events and logs corrections for changes to learnable fields.

```
User action → update_task(source="discord_modal") → TaskEventBus
    → CorrectionSubscriber.on_task_updated() → log_correction()
```

### Source tags

Each update path tags its source so the subscriber can distinguish user-initiated changes from system updates:

| Source | Origin |
|--------|--------|
| `discord_modal` | Discord edit modal |
| `discord_select` | Discord priority/domain select menus |
| `discord_command` | Discord slash commands (e.g., `/done`, `/priority`) |
| `api` | REST API (dashboard, Flutter app) |
| `calendar_sync` | Google Calendar time changes |
| `None` | System-initiated (ignored by subscriber) |

### Learnable fields

Only changes to these fields are logged as corrections: `priority`, `domain`, `title`, `description`, `scheduled_start`, `deadline`, `estimated_duration`, `tags`.

### Correction log schema

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Correction identifier |
| timestamp | DateTime | When corrected |
| user_id | String | Who made the correction |
| task_type | String | Source tag (e.g., `discord_modal`, `api`) |
| task_id | UUID | Specific task corrected |
| input_text | String | Original natural language input (empty for event-driven corrections) |
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

Applied **after** initial model processing as a post-processing step. Implemented in `src/donna/preferences/rule_applier.py` as `PreferenceApplier`.

1. Model produces structured `TaskParseResult` (first draft)
2. `PreferenceApplier.apply_for_user(result, user_id)` loads active rules from `learned_preferences` table (with a 60-second in-process TTL cache)
3. Rules are evaluated in confidence-descending order. For each rule, the condition is checked against the task's title + description (keyword substring match), domain (exact match), and task_type. The first matching rule per output field wins.
4. Matching rules override the corresponding field on the `TaskParseResult`
5. Orchestrator uses the final output for scheduling/routing

### Matching logic

| Condition type | How it matches |
|---------------|----------------|
| `keywords` | Case-insensitive substring match against `title + description` |
| `domain` | Exact match against the task's domain |
| `task_type` | Always matches `"parse_task"` at input time; other values skip |

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

## Module Reference

| Module | Role |
|--------|------|
| `correction_logger.py` | `log_correction()` — writes a row to the `correction_log` table when a learnable field changes. |
| `correction_subscriber.py` | `CorrectionSubscriber` — listens for `task_updated` events on the `TaskEventBus` and calls `log_correction()` for user-initiated changes to learnable fields. |
| `rule_extractor.py` | Batches recent corrections, sends to Claude for pattern analysis, and outputs structured `learned_preferences` rows. |
| `rule_applier.py` | `PreferenceApplier` — loads active rules for a user (with TTL cache), evaluates conditions against the task text, and overrides fields on `TaskParseResult`. Called in the input-parsing pipeline after the LLM produces its first draft. See [Preference Application](#preference-application). |
