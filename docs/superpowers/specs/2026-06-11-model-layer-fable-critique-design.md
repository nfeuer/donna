# Model Layer — Fable Critique & Redesign Spec

**Date:** 2026-06-11
**Status:** Phase-1 ledger fix IMPLEMENTED (see below); remaining items triaged
**Implemented (Phase 1):** #1 (choke-point ledger integrity — `build_model_router`
factory requires a logger; `complete()` refuses an unlogged billed call; chat +
bot routers wired), #4 (dead `confidence_threshold` removed from config + model),
#6 (single-source pricing — Anthropic provider prices from config rates, fails
loud on an unpriced model). **Deferred to Phase 2 (post-#112 rebase):** #2
per-attempt error logging, #3 shadow-through-`complete()`, #5 per-provider
breaker, #7 token tripwire, #8/#9 eval+spot-check activation.
**Critic:** Fable 5 (adversarial design critique)
**Triage:** Opus (independent verification + disposition)
**Related:** `spec_v3.md §4` (Model Abstraction & Evaluation Layer), `CLAUDE.md` principles #3 (log every model call) / #5 (model abstraction), `docs/superpowers/plans/2026-06-11-fable-design-critique-program.md` (Wave B-1), PR #112 (Cost & Escalation — interaction noted in §1)

> Wave B, subsystem 1. Fable's full 13-finding critique is preserved in the
> session record; this captures the **verified** findings, their **triage
> disposition**, and the recommended sequence. No code changed.

---

## 1. Executive finding (verified)

**The `ModelRouter.complete()` choke point has the right shape, but its guarantees
are optional — and production wiring omits them.** Logging, budget enforcement,
fallback, and shadow are all nullable constructor params; the chat router is built
with **neither a logger nor a budget guard**, the main router has no budget guard,
and two bare routers exist in `cli.py`. Because `BudgetGuard` computes spend *from*
`invocation_log`, **every unlogged call is invisible to the very $100 cap PR #112 is
adding** — the cap will enforce against an undercounted ledger.

> **Interaction with PR #112 (important):** #112 hardens `BudgetGuard` internals and
> adds router-side estimation + monthly enforcement. But #112's cap is only as
> trustworthy as the ledger's completeness. This critique shows the ledger is
> incomplete (chat + bare-router calls never logged). **#112 and this finding are
> complementary halves of the same guarantee** — the cap needs the complete ledger
> this work provides. That makes the ledger fix timely, not redundant.

Secondary danger: the routing config advertises resilience (`fallback`,
`confidence_threshold`) the code does not implement — the operator's failure-behavior
model is wrong.

## 2. Verification log (Opus, independent of Fable)

| # | Claim | Verified? | Evidence |
|---|---|---|---|
| **1** | Choke-point guarantees optional; production omits them | ✅ Confirmed | `api/__init__.py:241` `chat_router = ModelRouter(m_cfg, t_cfg, project_root, payload_writer=…)` — **no** `invocation_logger`, **no** `budget_guard`; `cli.py:436,715` bare positional routers; main router `cli_wiring.py:1247` passes logger but no budget_guard. |
| **3** | Shadow mode is dead config | ✅ Confirmed | `on_shadow_complete=` appears only as the `__init__` param (`router.py:136`); no production construction passes it → the shadow gate (`router.py:645`) is always false. |
| **4** | `fallback`/`confidence_threshold` are config theater | ✅ Confirmed | `confidence_threshold` appears **only** at `config.py:33` (consumed nowhere); fallback fires only on *estimated* context overflow, so a declared `fallback: parser` never triggers on an Ollama outage. |
| **6** | Two sources of price truth | ✅ Confirmed | `anthropic.py:20-30` hardcodes `_SONNET_*_COST_PER_MTOK` and `_compute_cost` applies it to **any** model id; router extension math uses per-alias config rates (`router.py:455-476`). Latent until a non-Sonnet alias exists. |
| **2** | Billed-but-unlogged on error/retry | ✅ Confirmed (structural) | `invocation_log` is written only after full success (`router.py:570-594`); `resilient_call` retries the whole billed call; a parse failure or all-attempts-fail logs zero rows for billed calls. |

**Conclusion:** Fable's critique is accurate and evidence-grounded. The unifying thesis
— *make `complete()` the accounting boundary, not just the dispatch boundary* — is
sound; findings 1, 2, 3, 8, 9 are the same defect in five places.

## 3. Triage dispositions

Legend: **ACCEPT** · **ESCALATE** (scope/posture for owner) · **DEFER** (trigger-gated) · **KEEP** (existing design is right).

### S1 — ledger integrity (the accounting boundary)
| # | Finding | Disposition |
|---|---|---|
| 1 | Wire `invocation_logger` (+ `budget_guard`) into the chat + main + cli routers so every production call is logged and budgeted; make the logger a required dep via a single sanctioned router factory; lint-ban bare `ModelRouter(` | **ESCALATE** — closes the "spend escapes the ledger" hole that undermines #112's cap. Touches wiring (`cli_wiring`, `api`, `cli`). Owner scope decision §6. |
| 2 | Log billed calls on every error/retry/overflow path (capture `usage` before parse; classify retryable vs not; per-attempt rows with `error`/`interrupted`) | **ACCEPT** — correctness; pairs with #1. (Touches `router.py`/`retry.py` — see #112 conflict note.) |
| 3 | Shadow mode dead + bypasses abstraction → re-express shadow as a recursive internal `complete(is_shadow=True)` so budget/logging/breaker come free; use `_lookup_routing_entry`; wire or delete the callback; drop the unused `TaskTypeEntry.shadow` | **ESCALATE** — turning shadow on is real (doubled) spend; needs the stable-state exit (design B) + owner intent. Until then, **the honest fix is to stop advertising it** (drop dead config). |

### S2 — config honesty & resilience correctness
| # | Finding | Disposition |
|---|---|---|
| 4 | `confidence_threshold` does nothing; `fallback` only on overflow | **ACCEPT** — implement the failure→fallback re-dispatch (alias swap + `dispatch_fallback_alert` + re-run budget at cloud rates) **or** delete `confidence_threshold` from config until implemented. A knob that does nothing must not exist. |
| 5 | One shared `CircuitBreaker` couples Ollama + Anthropic — a local-GPU outage blacks out cloud calls | **ACCEPT** — per-provider breaker dict; fallback consults the *target* provider's breaker; breaker-open alerts. |
| 6 | Hardcoded Sonnet pricing vs config rates (ledger corruption on first model change) | **ACCEPT** — single source: router computes `cost_usd` from the alias's config rates + provider `usage`; fail loud if an anthropic alias lacks rates. |
| 8 | Eval harness (§4.5) bypasses the abstraction; eval spend unlogged; §4.5.4/4.5.6 spec-only; fixture glob can't match dimension files | **ACCEPT (later slice)** — route eval through a router with a model-override hook; stamp `eval_session_id`; widen fixture loading. Plus the production→fixture capture loop (design D). |
| 9 | Spot-check (§4.6) skeleton-only; `enabled` flag dead; judge interface invites a provider bypass; malformed judge → silent 0.0 | **ACCEPT (later slice)** — type the judge as `complete()` with a `quality_judge` task_type; sampler decision in the router; batch writeback; decaying rate (design C). Partially by-design (Phase-3 deferred), but the dead switch + bypass-inviting interface are defects now. |

### S3 — metadata/forensics hygiene
| # | Finding | Disposition |
|---|---|---|
| 10 | Log/payload write failures warn-only (no alert); router reaches into `logger._conn`; `input_hash` logged as `""` then cross-matches in the shadow join | **ACCEPT** — compute `input_hash` up front; add `InvocationLogger.update_payload_path()`; route accounting-write failure through `dispatch_fallback_alert`. |
| 12 | Hardcoded `stop_reason="end_turn"`; missing Ollama usage → silent 0 tokens/$0; `output` column never populated; naive `utcnow()` | **ACCEPT** — thread real `stop_reason`; treat missing usage as a logged anomaly excluded from drift; UTC-aware timestamps. |
| 13 | §4.1 interface dropped `schema`; validation per-caller opt-in | **ACCEPT (small)** — optional `validate: bool = True` path in `complete()` using the task_type schema. |
| 11 | Router↔gateway doc drift (router bypasses `donna.llm` queue; `queue_wait_ms` never populated) | **DEFER (doc fix now)** — correct `docs/domain/llm.md`; the gateway-routing unification is a real project (trigger below). |

### Token estimation (#7, S2) — the safety-inversion
| # | Finding | Disposition |
|---|---|---|
| 7 | `len//4` **under**-estimate → silent Ollama truncation (the failure the loud `ContextOverflowError` exists to prevent); drift data logged but unused; **the G-28 upgrade trigger counts only *detected* overflows, so it structurally cannot fire on silent truncation** | **ACCEPT** — design A: post-call truncation tripwire (`tokens_in ≥ num_ctx − reserve` → alert + re-dispatch/loud-fail) + a self-calibrating per-task-type divisor EMA (no tokenizer dependency). **Fixes the G-28 trigger** by adding `truncation_suspected` to the gauge. Exact tokenization stays deferred. |

### DEFER — sound but trigger-gated
- **Exact tokenization (G-28/OOS-11)** — trigger: combined overflow + `truncation_suspected` rate >10% *after* design A ships (A repairs the trigger).
- **Logprob confidence (OOS-5)** — trigger: self-assessed-vs-`quality_score` correlation breaks (measurable only after #9 writes `quality_score`).
- **Per-alias daily caps (G-29)** — trigger: first sustained overflow/failure-escalation pattern; count failure-escalations too.
- **Router-through-gateway unification (#11)** — trigger: first observed GPU swap collision / internal `queue_wait` anomaly.
- **pgvector (G-27)** — not a model-layer item.

### KEEP — existing design is right; a naive critic would wrongly "fix" these
- **Loud-fail `ContextOverflowError`** when no fallback exists (`router.py:90-93`) — correct safety-first; the fix is closing the *under*-estimate hole, not softening the error.
- **Config-driven routing with longest-prefix resolution** (`router.py:198-238`).
- **Deferring a tokenizer dependency** — calibrate, don't import.
- **Fire-and-forget shadow task with strong refs** — correct asyncio hygiene; the problem is what runs *inside* it.
- **Ollama's loud `NotImplementedError` for tools/messages**; the **post-resolution fallback config validation** (drift check a naive critic would delete).

## 4. Highest-leverage change

**Make `complete()` the accounting boundary, not just the dispatch boundary.** One
sanctioned router factory with non-optional logger/budget/alert wiring; per-attempt
logging of billed calls; shadow/eval/judge re-expressed as internal `complete()`
calls. Findings **1, 2, 3, 8, 9 are the same defect in five places** — this retires
the class and makes #112's cap trustworthy.

## 5. Sequencing vs PR #112 (avoid router.py merge thrash)

#112 (in flight) heavily edits `router.py`. To minimize conflict:
- **Phase 1 (low conflict):** the wiring fix (#1) lives mostly in `cli_wiring.py` /
  `api/__init__.py` / `cli.py` (which #112 does not touch) + config honesty (#4 delete
  the dead key) + pricing single-source (#6, in `anthropic.py`). Ships ledger
  completeness without fighting #112's router diff.
- **Phase 2 (after #112 merges):** the `router.py`-heavy items — per-attempt logging
  (#2), shadow-through-`complete()` (#3), token tripwire (#7), per-provider breaker
  (#5) — rebased on the merged router.

## 6. Owner decision required (escalation)

Making the logger a *required* dep is a guardrail that could affect boot paths
(`cli.py` bare routers, eval/test construction), so the scope + sequencing is the
owner's call. Asked separately.

---

## Appendix — program mechanics (Wave B-1)

- Briefing Fable that #112 already fixes the gate-estimation/monthly-cap/log-ordering
  trio kept it from re-deriving them — it spent its budget on the *un*-addressed layer
  and surfaced the ledger-completeness gap that #112 itself depends on.
- PROVEN-vs-SUSPECTED tagging + spec-drift flagging again paid off: Fable correctly
  marked which of §4.5/§4.6/§4.7 are spec-only and flagged the G-28 trigger as
  structurally unable to fire — a second-order insight a checklist review would miss.
