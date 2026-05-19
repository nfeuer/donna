# Model Abstraction & Evaluation Layer

> Split from Donna Project Spec v3.0 — Section 4

## Core Principle

The orchestrator and agents never call a specific model provider directly. All LLM interactions go through a standardized interface handling provider abstraction, structured logging, routing, and shadow evaluation.

## Model Interface

Every model call goes through:

```python
complete(prompt, schema, model_alias) → (response, metadata)
```

Metadata always includes: `latency_ms`, `tokens_in`, `tokens_out`, `cost_usd`, `model_actual` (resolved provider + model name), `is_shadow`.

Two implementations: `AnthropicProvider` (Claude API) and `OllamaProvider` (local LLM). A third can be added without changing calling code.

### Source Files

```
src/donna/models/
├── __init__.py
├── router.py              # ModelRouter — config-driven routing, escalation, overflow handling
├── types.py               # CompletionMetadata dataclass
├── tokens.py              # estimate_tokens() — character-based heuristic (len // 4)
├── validation.py          # validate_output() — JSON Schema (draft-07) validation
├── quality.py             # Spot-check quality monitoring (Claude-as-judge)
└── providers/
    ├── __init__.py        # ModelProvider protocol
    ├── anthropic.py       # AnthropicProvider — Claude API
    ├── ollama.py          # OllamaProvider — local LLM
    └── _parsing.py        # parse_json_response() — strip markdown fences from LLM output
```

### ModelRouter (`router.py`)

The `ModelRouter` class is the central dispatch point. It loads routing configuration from `donna_models.yaml` and `task_types.yaml`, resolves `task_type` to `model_alias` to provider, and dispatches completions through the resilience layer (circuit breaker, retry).

Key error types raised by `ModelRouter.complete()`:

| Error | When Raised |
|-------|-------------|
| `EscalationDecisionError` | Over-budget gate resolves to a terminal mode (`pause`, `cancel`, `claude_code`, `chat`). Carries `mode`, `escalation_request_id`, `correlation_id`. |
| `TokenLimitReachedError` | Provider truncated output at extension-derived token cap. Caller should re-estimate and re-offer escalation. |
| `ContextOverflowError` | Prompt exceeds local model budget (`num_ctx - output_reserve`) and no fallback configured. |
| `RoutingError` | Task type has no routing entry in config. |

### CompletionMetadata (`types.py`)

Returned alongside every LLM completion:

| Field | Type | Purpose |
|-------|------|---------|
| `latency_ms` | int | Wall clock time |
| `tokens_in` | int | Input tokens consumed |
| `tokens_out` | int | Output tokens generated |
| `cost_usd` | float | Computed cost |
| `model_actual` | str | Resolved provider + model name |
| `is_shadow` | bool | Shadow run flag |
| `estimated_tokens_in` | int? | Pre-dispatch token estimate (for drift tracking) |
| `overflow_escalated` | bool | True if request fell back from local to cloud due to context overflow |
| `token_limited` | bool | True if provider truncated output at extension cap |

### Token Estimation (`tokens.py`)

`estimate_tokens(text)` uses `len(text) // 4` — zero dependencies, constant-time. Accuracy is tracked on the LLM Gateway dashboard via the `estimated_tokens_in` column. Upgrade trigger: swap for Ollama's `/api/tokenize` when `context_overflow_escalation` rate exceeds 10%.

### Output Validation (`validation.py`)

`validate_output(data, schema)` validates LLM JSON output against a JSON Schema (draft-07). On mismatch, raises `ValidationError` with a list of error messages. Used by agents and the orchestrator to enforce structured output contracts.

### Response Parsing (`providers/_parsing.py`)

`parse_json_response(text)` extracts clean JSON from LLM text responses, stripping markdown code fences if present. Shared by both `AnthropicProvider` and `OllamaProvider`.

## Routing Configuration

The routing table in `config/donna_models.yaml` maps model aliases to providers and defines per-task-type behavior. This is the primary control surface.

During Phase 1 (Claude API only), all aliases point to Anthropic. Switching a task type to local = change `provider` and `model` fields for the relevant alias.

The `shadow` key enables production monitoring (secondary model runs in parallel, output logged only, not used). Offline evaluation is triggered via CLI, not configured in routing.

## Structured Invocation Logging

Every model call is logged to the `invocation_log` table:

| Field | Type | Purpose |
|-------|------|---------|
| id | UUID | Unique invocation ID |
| timestamp | DateTime | When the call was made |
| task_type | String | Which task type (parse, classify, generate, etc.) |
| task_id | UUID? | Associated task if applicable |
| model_alias | String | Config alias used (parser, reasoner, etc.) |
| model_actual | String | Resolved provider + model |
| input_hash | String | Hash of input for dedup and comparison matching |
| latency_ms | Int | Wall clock time |
| tokens_in | Int | Input tokens consumed |
| tokens_out | Int | Output tokens generated |
| cost_usd | Float | Computed cost ($0.00 for local before cost approx configured) |
| output | JSON | Actual structured response |
| quality_score | Float? | Filled by spot-check or offline eval |
| is_shadow | Boolean | Shadow run (production monitoring) or eval run |
| eval_session_id | UUID? | Groups invocations from a single eval run |
| spot_check_queued | Boolean | Queued for Claude-as-judge review |
| user_id | String | User who triggered the call |
| queue_wait_ms | Int? | Time spent in LLM queue before dispatch |
| interrupted | Boolean | True if the call was interrupted before completion |
| chain_id | String? | Groups related calls in a multi-step chain |
| caller | String? | Identifies the calling module/agent |
| estimated_tokens_in | Int? | Pre-dispatch token estimate (for drift tracking vs actual `tokens_in`) |
| overflow_escalated | Boolean | True if request fell back from local to cloud due to context overflow |
| skill_id | String? | Associated skill if the call was skill-triggered |
| escalation_request_id | Int? | Links to an escalation request when the call is budget-gated |
| payload_path | String(300)? | Filesystem path to full request/response JSON (written by `PayloadWriter`, evicted by `PayloadEvictor`) |

## Shadow Mode (Production Monitoring)

Runs secondary model on same input without affecting primary output. Use case: after migrating `task_parse` from Claude to local, keep Claude as shadow for 2–4 weeks. If quality degrades, revert by changing routing config.

**Cost implication:** Doubles model cost for that task type. Intended as temporary — disable once confidence is established.

## Offline Evaluation Harness (Model Comparison)

Development tool for comparing models against the same test inputs. Triggered via CLI, not part of production routing. Primary purpose: model selection for local LLM.

### Tiered Test Fixtures

```
fixtures/
├── parse_task/
│   ├── tier1_baseline.json     # ~10 cases: simple, unambiguous
│   ├── tier2_nuance.json       # ~15 cases: implicit deadlines, ambiguity
│   ├── tier3_complexity.json   # ~10 cases: multi-part, dependencies
│   └── tier4_adversarial.json  # ~5 cases: edge cases, contradictions
├── classify_priority/
├── generate_digest/
├── deduplication/
├── escalation_awareness/
│   ├── should_escalate.json
│   └── should_handle.json
└── instruction_following/
    ├── claude_decomposition.json
    ├── constraint_compliance.json
    └── correction_application.json
```

### Tier Definitions

| Tier | Name | Cases | Pass Gate |
|------|------|-------|-----------|
| 1 | Baseline | ~10 | 90%+ to continue |
| 2 | Nuance | ~15 | 80%+ |
| 3 | Complexity | ~10 | 60%+ |
| 4 | Adversarial | ~5 | No gate — diagnostic only |

### Sequential Evaluation

One GPU runs one model at a time. Harness runs sequentially: load model A → all fixtures → save results → swap to model B → repeat.

```bash
donna eval --task-type task_parse --model ollama/llama3.1:8b-q4
```

### Evaluation Dimensions

**Escalation Awareness** — does the model know when NOT to try?

| Metric | Target | Rationale |
|--------|--------|-----------|
| Precision (correctly escalated) | 85%+ | Over-escalation wastes money but produces correct results |
| Recall (caught tasks it shouldn't handle) | 85%+ | Under-escalation produces garbage. Less tolerable. |
| False positive rate | < 25% | Above this, cost savings undermined |

**Instruction Following** — can the model execute Claude-generated directives?

| Metric | Target |
|--------|--------|
| Constraint compliance | 90%+ |
| Format adherence | 95%+ |
| Rule application accuracy | 85%+ |
| Rule false application | < 10% |

## Spot-Check Quality Monitoring

Active when local LLM is handling traffic (`quality_monitoring.enabled: true` in config). Implemented in `src/donna/models/quality.py`.

- `spot_check_rate: 0.05` (5% sampled, higher during early deployment)
- Batch job sends sampled outputs to Claude-as-judge
- Scores below `flag_threshold: 0.7` create a Donna task for user review
- Corrections flow into correction log (see `docs/preferences.md`)

## Confidence Scoring

- **Self-assessed (default):** Include `confidence` field (0.0–1.0) in output schema.
- **Logprob-based (optional):** Examine average token logprobs from Ollama API.

Start with self-assessed, correlate with actual accuracy, upgrade if unreliable.

## Local Model Cost Approximation

```yaml
models:
  parser:
    provider: ollama
    model: llama3.1:8b-q4
    estimated_cost_per_1k_tokens: 0.0001  # hardware amortization
```

Never show local inference as "free" — enables genuine cost-per-quality comparison.

## Local LLM Context Window Strategy

Ollama defaults to a 2048-token window unless `num_ctx` is explicitly set. Donna configures it on every Ollama call via two knobs in `config/donna_models.yaml`:

- `ollama.default_num_ctx` — the total window (prompt + output) for all Ollama aliases.
- `ollama.default_output_reserve` — tokens held aside for model output so the prompt budget never clips mid-generation.

Per-alias overrides live on the individual model entry: `models.<alias>.num_ctx`.

### Pre-dispatch budgeting

Before dispatching to a local alias, `ModelRouter` estimates prompt tokens (`len(prompt) // 4`) and compares against `num_ctx - output_reserve`. If the estimate exceeds the budget:

1. If the task type has a `fallback` configured, the call escalates to the cloud alias. A `context_overflow_escalation` warn event is logged, and `invocation_log.overflow_escalated` is set to `1`.
2. If no fallback exists, the router raises `ContextOverflowError`. This is deliberate — silent truncation produces silent garbage.

Every call to an Ollama alias records `invocation_log.estimated_tokens_in` alongside the actual `tokens_in` reported by Ollama. The LLM Gateway dashboard surfaces mean absolute error as a gauge for when to upgrade the estimator to exact tokenization.

### Future Enhancements

Documented in [`archive/2026-04-12-local-llm-context-strategy-design.md`](../superpowers/specs/archive/2026-04-12-local-llm-context-strategy-design.md):
per-task-type compaction strategies, `pgvector` brain on Supabase, exact tokenization via Ollama, and per-alias daily caps on overflow escalations. *Tracked as [G-26 – G-29](../superpowers/followups/open-backlog.md).*

## Cost Tracking Scope

Cost tracking and budget limits apply exclusively to **LLM API costs** (Claude API and local model cost approximations). External service costs (Twilio, Firebase, Supabase, Gmail API) have their own billing dashboards and are not tracked by Donna's cost system.

All LLM API costs are tracked via the `invocation_log` table. Budget limits are configurable in `config/donna_models.yaml` under the `cost` section:

- **Monthly budget:** Hard cap on total LLM spend.
- **Daily pause threshold:** Pauses autonomous agent work when reached.
- **Per-task approval threshold:** Requires user approval before executing expensive tasks.
- **Monthly warning percentage:** Alerts at configurable percentage of monthly budget.
- **Per-model-alias limits (optional):** Cap spend on specific model aliases (e.g., limit `reasoner` separately from `parser`) to control expensive task type spend independently.
