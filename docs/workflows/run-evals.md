# Workflow: Run Evals

**Realizes:** [`spec_v3.md` §4.5 Offline Evaluation Harness](../reference-specs/spec-v3.md).

## Purpose

Regression-test prompt / model / skill changes against version-controlled
fixtures. Each fixture set is organized into tiers; tiers have numeric
pass gates so failures are deterministic.

## Tier Definitions

| Tier | Intent | Pass Gate |
|---|---|---|
| tier1 | Baseline — straightforward cases | ≥ 0.90 |
| tier2 | Nuance — plausible ambiguity | ≥ 0.85 |
| tier3 | Complexity — multi-clause / chained | ≥ 0.75 |
| tier4 | Adversarial — ambiguous / contradictory | ≥ 0.60 |

## Run It

```bash
# Single task type, single model
donna eval --task-type task_parse --model anthropic/claude-sonnet-4

# Compare models
donna eval --task-type task_parse --model ollama/qwen2.5:32b-instruct-q6_K
donna eval --task-type task_parse --model anthropic/claude-sonnet-4

# Specific tier
donna eval --task-type classify_priority --tier 3
```

## What Gets Measured

Per fixture:

- **Correctness** — structured output matches expected
- **Latency** — p50/p95
- **Cost** — $ per invocation (from `invocation_log`)
- **Token usage**

Per tier:

- Pass rate vs gate
- Regression vs last green run

## Where to Look

| Piece | Location |
|---|---|
| CLI | [`donna.cli`](../reference/donna/cli.md) |
| Fixtures | `fixtures/<task_type>/tierN/` |
| Invocation log | `donna_logs.db` — table `invocation_log` |
| Model aliases | [`config/donna_models.yaml`](../config/donna_models.md) |

## Related

- [Development → Evaluation Harness](../development/evaluation-harness.md)
- [Operations → Budget & Cost](../operations/budget-and-cost.md)
