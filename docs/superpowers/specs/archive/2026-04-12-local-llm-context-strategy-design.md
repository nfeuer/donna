# Local LLM Context Window Strategy

**Status:** Draft
**Date:** 2026-04-12
**Owner:** Nick

## Problem

Donna routes a subset of task types to a local Ollama model (`qwen2.5:32b-instruct-q6_K` on an RTX 3090). Two context-window issues exist today, and a third is coming:

1. **Silent misconfiguration.** `OllamaProvider.complete()` in `src/donna/models/providers/ollama.py` sets `options.num_predict` but never sets `options.num_ctx`. Ollama therefore uses its default window of **2048 tokens** (prompt + output combined), not the 32K that `qwen2.5` advertises. Any prompt larger than ~2K is being silently truncated at inference time with no log signal. The weekly digest and challenger prompts are already uncomfortably close to this ceiling given current formatting.

2. **No budgeting at the router.** Nothing in `ModelRouter` checks whether a prompt will fit the window before dispatching. An oversized prompt produces a silently truncated result, not an error. This violates the project's "safety first" principle — a loud failure is strictly better than a quiet garbage output.

3. **Future scale pressure.** As more task types migrate to local (task decomposition, preference extraction, prep-research over long history), some prompts *will* legitimately exceed whatever window we configure. There's no graceful degradation path today.

## Goals

- **Fix the footgun.** `num_ctx` becomes an explicit, configurable value for every Ollama alias, sent on every call.
- **Budget before dispatch.** The router estimates prompt size and, for local aliases, escalates to the cloud fallback when the prompt would overflow the window.
- **Observe escalations.** Every overflow escalation produces a distinct log signal and a boolean column on `invocation_log` so the LLM Gateway dashboard can count and filter them.
- **Measure estimation accuracy.** Because we're budgeting off a cheap heuristic, the dashboard surfaces heuristic-vs-actual drift so we know when to upgrade to exact tokenization.
- **Keep it YAGNI.** No compaction, no RAG, no vector brain — design hooks mentioned as future extensions only.

## Non-Goals

- Per-task-type compaction / summarization / map-reduce strategies.
- Vector retrieval or a Postgres "brain" for long-history lookups.
- Exact token counting via Ollama's `/api/tokenize` endpoint.
- Per-alias daily cost caps on overflow escalations.

All four are captured in [Future Extensions](#future-extensions) and deliberately deferred.

## Architecture

Three changes, in the order a call flows through them:

1. **Config** — new `default_num_ctx` / `default_output_reserve` on the `ollama:` block in `config/donna_models.yaml`, plus optional per-alias `num_ctx` override.
2. **Router** — before dispatching to a local alias, estimate prompt tokens and compare against the alias's window minus reserved output space. Overflow → use `fallback`. No fallback configured → raise `ContextOverflowError`.
3. **Observability** — new `invocation_log` columns, a structured log event, and extensions to the existing LLM Gateway dashboard.

The `OllamaProvider` is a dumb pipe: it receives a resolved `num_ctx` from the router, sends it to Ollama, and reports actual token counts back.

Anthropic aliases are unaffected — the budgeting path only runs when the resolved alias's provider is `ollama`.

## Config Changes

`config/donna_models.yaml`:

```yaml
ollama:
  base_url: http://localhost:11434
  timeout_s: 120
  keepalive: 5m
  default_num_ctx: 8192          # total window (prompt + output) for all ollama aliases
  default_output_reserve: 1024   # tokens held aside for model output

models:
  local_parser:
    provider: ollama
    model: qwen2.5:32b-instruct-q6_K
    estimated_cost_per_1k_tokens: 0.0001
    num_ctx: 8192                # optional per-alias override
```

**Why two knobs.** `num_ctx` is the total window Ollama allocates; `default_output_reserve` is how much of that the router holds aside so generation can't clip mid-response. The budget check is `estimated_prompt_tokens + output_reserve <= num_ctx`.

**Starting values.** `default_num_ctx: 8192` is a safe headroom on a 24 GB RTX 3090 running q6_K weights — roughly +2 GB of KV cache beyond the model itself. `default_output_reserve: 1024` matches the existing `max_tokens=1024` default in `OllamaProvider.complete()`. Both are easy to raise once real invocation sizes show up in the logs.

## Router Logic

`src/donna/models/router.py` gains a new step between "resolve alias" and "dispatch to provider":

```
resolve task_type → alias                                 (existing)

if alias.provider == "ollama":
    estimated_in = estimate_tokens(prompt)
    budget       = alias.num_ctx - alias.output_reserve
    if estimated_in > budget:
        if alias.fallback:
            logger.warn("context_overflow_escalation",
                task_type=task_type,
                from_alias=alias.name,
                to_alias=fallback.name,
                estimated_tokens=estimated_in,
                budget=budget,
                user_id=user_id)
            alias = resolve(fallback)        # cloud
            overflow_escalated = True
        else:
            raise ContextOverflowError(...)  # explicit, loud

dispatch to provider                                      (existing, now passes num_ctx)
record invocation_log with estimated_tokens_in + overflow_escalated
```

### Token estimation

Start with the `len(prompt) // 4` heuristic. Zero dependencies, constant-time, well-understood. Record the estimate on every local-bound call and compare against Ollama's actual `tokens_in` in the logs. If accuracy drifts past a threshold (surfaced on the dashboard, see below), upgrade to Ollama's `/api/tokenize` endpoint as a follow-up.

### Explicit failure when no fallback

If a local-only alias has no `fallback` configured and the prompt overflows, the router raises `ContextOverflowError` rather than silently truncating. A truncated prompt produces silent garbage; a loud failure is strictly better. This matches the project's "safety first, dial back later" principle.

### Passing `num_ctx` to the provider

This is the fix for the current footgun. `OllamaProvider.complete()` gains a new `num_ctx: int` kwarg and writes it into `options.num_ctx` alongside `options.num_predict`. The router passes the resolved value (per-alias override or `default_num_ctx`) on every call. Without this, every other change in this spec is cosmetic.

## Ollama Provider Changes

Minimal. `src/donna/models/providers/ollama.py`:

- `complete()` gains a `num_ctx: int` kwarg.
- The `options` dict in the payload gets `"num_ctx": num_ctx` alongside the existing `"num_predict": max_tokens`.
- No budgeting logic in the provider — it receives a value, sends it, reports back actual `tokens_in`.

## Logging and Schema

### Alembic migration

Two new columns on `invocation_log`:

| Field | Type | Purpose |
|-------|------|---------|
| `estimated_tokens_in` | `INTEGER NULL` | Router's pre-call token estimate. Null for non-local calls and pre-migration rows. |
| `overflow_escalated` | `BOOLEAN NOT NULL DEFAULT FALSE` | True when a call was routed from local to cloud because of a window overflow. |

Existing rows get `NULL` / `FALSE`. No backfill; the dashboard renders "—" for pre-migration estimates.

### Structured log event

Every overflow escalation emits:

```python
logger.warn("context_overflow_escalation",
    task_type=task_type,
    from_alias=local_alias_name,
    to_alias=cloud_alias_name,
    estimated_tokens=estimated_in,
    budget=budget,
    user_id=user_id)
```

`warn` level so it surfaces clearly in Loki. Grafana/Loki are already wired up, so building an alert later (if the escalation rate climbs) is a follow-up, not part of this spec.

### LLM Gateway dashboard extensions

The existing `donna-ui/src/pages/LLMGateway/` page gains:

- **Summary tile:** mean absolute estimation error across recent Ollama calls, plus an "overflow escalations this week" counter — the specific signal the project owner asked to watch.
- **Invocation list column:** `est / actual` (e.g. `1,840 / 2,103`) with a color indicator: green at `< 15%` error, yellow at `< 30%`, red at `>= 30%`.
- **Filter — "High estimation error":** shows only calls where `|error| > threshold` (default 25%, configurable). This is the signal used to decide when to swap the `len // 4` heuristic for `/api/tokenize`.
- **Filter — "Overflow escalations only":** directly answers "how often am I pushing to cloud because of context window."

**Scoping subtlety.** Estimation accuracy is only meaningful for calls that *actually ran on Ollama*. Escalated calls never get an Ollama `tokens_in`, so the accuracy aggregations must restrict to rows where `model_actual` starts with `ollama/`. The dashboard must make that scoping explicit so the numbers are not misleading.

## Testing

### Unit — router

- Ollama alias + small prompt → dispatches local, records `estimated_tokens_in`, `overflow_escalated=False`.
- Ollama alias + large prompt + configured fallback → escalates, `overflow_escalated=True`, `context_overflow_escalation` warn event emitted, final call goes to the cloud alias.
- Ollama alias + large prompt + no fallback → raises `ContextOverflowError`.
- Anthropic alias → budgeting path skipped entirely (no `estimated_tokens_in` recorded).
- Per-alias `num_ctx` override takes precedence over `default_num_ctx`.

### Unit — provider

- `OllamaProvider.complete(..., num_ctx=8192)` produces a request payload whose `options.num_ctx == 8192`.

### Integration

Optional and skipped by default. Deliberately hitting real Ollama with a deliberately oversized prompt is slow and fragile. The unit tests above are sufficient for this spec.

## Rollout

1. Add the migration; run it locally; verify the new columns exist.
2. Ship the provider + router changes with `default_num_ctx: 8192`. All current local task types have prompts well under this, so the escalation path will rarely fire initially.
3. Ship the dashboard extensions.
4. Watch `context_overflow_escalation` events and estimation accuracy for one week. If accuracy drift is under the red threshold and escalations are rare, no further action. If either is off, follow up with `/api/tokenize` or per-task tuning.

No feature flag — this is a targeted fix, and the old behavior (silent 2048 truncation) is a bug we want off immediately.

## Future Extensions

Deliberately out of scope for this spec. Captured here so future work has a clear hook point.

- **Per-task-type compaction strategies.** A `compaction_strategy` key on each routed task type in `config/donna_models.yaml` (e.g. `rolling_summary`, `map_reduce`, `rag`). The router would call `compact_prompt(prompt, budget)` before the budget check when a strategy is configured. Different task types will likely want very different compaction shapes (the digest wants stat pre-aggregation; prep-research wants retrieval; decomposition wants summarization), so designing a generic hook now would almost certainly be wrong. Wait until a specific task needs it.
- **Postgres vector "brain" on Supabase (`pgvector`).** Useful for "what did Nick say about X" history lookups. Again, driven by a specific task that needs it, not built speculatively.
- **Exact tokenization via Ollama `/api/tokenize`.** Upgrade path from the `len // 4` heuristic, triggered when the estimation-accuracy dashboard shows consistent drift past the red threshold.
- **Per-alias daily cost caps on overflow escalations.** If cloud escalations start eating budget noticeably, cap how many escalations a local alias can trigger per day before the router just fails the call instead of escalating.
