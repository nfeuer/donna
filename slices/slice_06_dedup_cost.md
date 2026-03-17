# Slice 6: Deduplication & Cost Tracking

> **Goal:** Prevent duplicate tasks from cluttering the system. Wire up real-time cost tracking so spending is always visible.

## Relevant Docs

- `CLAUDE.md` (always)
- `docs/task-system.md` — Task deduplication (two-pass: fuzzy + LLM)
- `docs/model-layer.md` — Invocation logging, cost fields
- `docs/observability.md` — LLM & Cost dashboard panel

## What to Build

1. **Implement two-pass deduplication** (`src/donna/tasks/dedup.py`):
   - **Pass 1 (fuzzy):** On every new task, compare title against recent active tasks using `rapidfuzz` token-sort ratio
     - Above 85%: auto-flag as duplicate, ask user to confirm merge
     - Below 70%: clearly different, proceed
     - 70–85%: proceed to Pass 2
   - **Pass 2 (LLM):** Send both task descriptions to LLM with `prompts/dedup_check.md`
     - Returns: `same` (merge), `related` (link), or `different` (no action)
   - User prompt on same channel: "This looks like a duplicate of '[existing task]' (created [date]). Merge, keep both, or update existing?"
   - Handle user response: merge combines notes/description, keep both creates with link, update modifies existing

2. **Implement cost aggregation** (`src/donna/cost/tracker.py`):
   - Queries `invocation_log` table for cost aggregations
   - Methods: `get_daily_cost()`, `get_monthly_cost()`, `get_cost_by_task_type()`, `get_cost_by_agent()`
   - Projected monthly spend based on current daily average
   - Checks against budget thresholds from `config/donna_models.yaml`

3. **Implement budget enforcement** (`src/donna/cost/budget.py`):
   - Before every API call: check daily spend against $20 pause threshold
   - If threshold hit: pause autonomous agent work, notify user via Discord `#donna-debug`
   - At 90% monthly budget: send budget warning with breakdown
   - Cost notification format per the spec's Donna persona

4. **Wire dedup into the input parsing pipeline** (update `src/donna/orchestrator/input_parser.py`):
   - After parsing, before creating task: run dedup check
   - If duplicate detected: interrupt pipeline, send user prompt, wait for response

5. **Write tests:**
   - Unit test: fuzzy matching correctly buckets pairs into <70%, 70-85%, >85% ranges
   - Unit test: mock LLM dedup response, verify merge/link/different flows
   - Unit test: cost aggregation returns correct totals from test invocation log data
   - Unit test: budget enforcement triggers pause at $20 daily threshold

## Acceptance Criteria

- [ ] "Get oil change" and "Oil change needed" detected as duplicate (>85% fuzzy)
- [ ] "Oil change for car" vs "Oil change for lawn mower" correctly identified as different by LLM
- [ ] User prompted on duplicate detection with merge/keep/update options
- [ ] Merge combines task notes; original task marked as merged
- [ ] `get_daily_cost()` accurately totals from invocation_log
- [ ] Daily $20 threshold pauses autonomous work and notifies user
- [ ] 90% monthly budget triggers warning notification
- [ ] Cost data available for downstream consumers (digest, dashboard)
- [ ] All dedup decisions logged for tracking false positive/negative rates

## Not in Scope

- No Grafana dashboard wiring (data is queryable, visualization is separate)
- No preference engine adjustments based on dedup patterns

## Session Context

Load only: `CLAUDE.md`, this slice brief, `docs/task-system.md`, `docs/model-layer.md`, `prompts/dedup_check.md`
