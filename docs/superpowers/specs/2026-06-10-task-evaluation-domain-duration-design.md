# Task Evaluation: Domain & Duration — Design

**Date:** 2026-06-10
**Status:** Approved (brainstorming)

## Problem

Donna's task parsing produces two unreliable fields:

1. **Duration** — nearly every task is estimated at ~60 minutes. Quick tasks
   (send an email, a call to schedule an appointment, touch base with someone)
   should be ~15 minutes.
2. **Domain** — work vs personal vs family is frequently wrong, because the
   classifier has no personal context to disambiguate genuinely ambiguous
   inputs (e.g. an email, "touch base with someone").

### Root causes (verified in code)

- Task parsing (`parse_task`) is routed to the **`parser` alias = Claude
  Sonnet 4** (`config/donna_models.yaml`), *not* the local LLM. The bad guesses
  come from an uncalibrated prompt, not the local model.
- `prompts/parse_task.md` gives the model **no duration calibration**: its
  entire instruction is *"infer from task complexity, default: 30."* No
  anchors, no examples, no per-task-type heuristics. The schema only enforces
  `minimum: 5`. The model free-guesses and anchors high/uniform.
- The domain rubric in the prompt is generic, and Donna injects **no personal
  context** (who a contact is, what a project is) to disambiguate.
- **No edit pathway exists for these fields.** `UpdateTaskRequest`
  (`api/routes/tasks.py`) only allows editing `title`, `description`,
  `priority`, `status`. Discord only *displays* duration. Because the fields
  can't be edited, the `correction_subscriber` learning loop — which *is* wired
  to learn from `domain`/`estimated_duration` edits — never fires. The bad
  first guess is effectively permanent.
- **`confidence_threshold` is declared but never consumed.** It exists in
  `config.py` and the routing YAML, but no code reads it. The router's only
  fallback trigger is *context overflow* (prompt exceeds the local context
  window), not low parse confidence. Confidence-gated escalation does not exist
  yet.

## Goal

Donna makes a better *first guess* at domain and duration, and *learns* from
corrections — running **local-first** (qwen2.5:32b) with Claude as a
**confidence-gated** fallback.

## Routing posture (decided)

**Local-first with Claude fallback.** Flip `parse_task` primary from `parser`
(Sonnet) to `local_parser` (qwen2.5:32b). Most tasks parse locally at zero
marginal cost; only low-confidence/ambiguous parses escalate to Claude.

Rationale: a calibrated, example-driven prompt turns parsing into
rubric-following, which is exactly the regime where a 32b local model is
strong. Embeddings (`MiniLMProvider`) are already local, so context retrieval
is also free.

## Components

### 1. Calibrated parse prompt — `prompts/parse_task.md`

- **Duration rubric with anchors:**
  - Quick comms (email / text / a call or message to schedule / touch base) =
    **15 min**
  - Errands, forms, short admin = **30 min**
  - Focused work, meetings, anything requiring sustained attention = **60 min**
  - Instruction: **"Default to the lower anchor; only inflate when the task
    text explicitly justifies it."**
- **Sharper domain rubric**, plus instruction to use the injected personal
  context (Component 2) and to **report low confidence when genuinely
  ambiguous**.
- Tuned to be explicit and example-driven, since the local model is now
  primary.

The 15/30/60 buckets and the 0.7 escalation threshold are starting values to
be tuned during eval (the threshold lives in config as a tunable).

### 2. Personal-context injection — new logic in `InputParser.parse`

Before parsing, gather:
- (a) the user's active `learned_preferences` rules (already loaded by
  `PreferenceApplier`), and
- (b) top-k vault hits via
  `MemoryStore.search(query=raw_text, sources=["vault"])` — People/Projects
  notes when present.

Render a compact context block into the prompt. **Degrades gracefully:** empty
vault → preferences only → bare rubric. Embeddings are local, so retrieval adds
no cloud cost.

### 3. Confidence-gated escalation — new logic in `InputParser.parse`

- Flip `parse_task` primary to `local_parser`, fallback `reasoner`.
- After the local parse, if `result.confidence < threshold` (config-tunable,
  default 0.7), re-run the parse on Claude and use that result. Log the
  escalation (reuse existing escalation logging patterns).
- This is net-new: today the router escalates only on context overflow.

### 4. Edit pathway + revived learning loop

- Add `domain` and `estimated_duration` to `UpdateTaskRequest`
  (`api/routes/tasks.py`) and the underlying update DB call, **with `source`
  set** so the `task_updated` event causes `correction_subscriber` to fire.
- Surface the edit in the UI (and optionally a Discord command).
- This revives the existing chain:
  `correction_subscriber → extract_preferences → rule_applier`. Correcting a
  duration/domain teaches a `learned_preferences` rule that
  `rule_applier.apply` (already runs post-parse) applies to future parses.

## Data flow

```
raw text → InputParser
        → gather context (active prefs + vault search)
        → render calibrated prompt
        → local parse (qwen2.5:32b)
        → confidence < threshold? → escalate to Claude, use that result
        → apply learned-preference rules
        → dedup
        → persist

correction: user edits domain/duration in UI
        → task_updated event (source set)
        → correction_subscriber logs to correction_log
        → extract_preferences generalizes to a rule
        → learned_preferences row
        → rule_applier applies it to future parses
```

## Validation

Use the existing `donna eval --task-type parse_task_local` harness to confirm
local parse quality against a small labeled set — covering the failure cases
(emails, scheduling calls, work/personal ambiguity) — **before** flipping the
primary to local. Use eval results to tune the duration buckets and the
confidence threshold.

## Testing

- **Unit:** prompt renders the context block correctly (with/without vault
  hits); confidence-escalation branch (below/above threshold);
  `UpdateTaskRequest` accepts `domain`/`estimated_duration` and emits a
  correction event with `source` set.
- **Integration:** low-confidence local parse → Claude escalation path;
  correction → preference rule → applied on the next parse.

## Out of scope (YAGNI)

- Tracking *actual* task durations (timers / completion feedback) to
  auto-calibrate estimates. Corrections cover learning for now.
- Moving `extract_preferences` off Claude. It is low-frequency (periodic, not
  per-task); leave as-is.

## Known risk

The vault's `People/` and `Projects/` folders are currently empty, so
Component 2's domain lift is modest until they fill. Components 1 (prompt
calibration), 3 (escalation), and 4 (learning loop) deliver value immediately
regardless of vault state.
