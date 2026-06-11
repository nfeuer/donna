# Cost & Escalation — Fable Critique & Redesign Spec

**Date:** 2026-06-11
**Status:** Triaged — pending owner decision on implementation scope (§6)
**Critic:** Fable 5 (adversarial design critique)
**Triage:** Opus (independent verification + disposition)
**Related:** `spec_v3.md §13` (Cost Management), `spec_v3.md §18` (Resilience & Failure Handling), `docs/superpowers/specs/manual-escalation.md` (canonical design), `docs/superpowers/plans/2026-06-11-fable-design-critique-program.md` (the program this is Wave A of), `docs/superpowers/specs/followups.md` (S18–S24), `docs/superpowers/followups/open-backlog.md` (G-15/G-16)

> This is the **redesign spec** produced by the first run of the Fable design-critique
> program. Fable's raw, full critique (20 ranked findings with evidence) is preserved
> in the session record; this document captures the **verified** findings, their
> **triage disposition**, and the recommended sequence. No code has been changed.

---

## 1. Executive finding (verified)

**The budget/escalation safety system is substantially dark in production.** The
7,182-LOC subsystem built across slices 17–24 to replace pause-only budgeting is, at
runtime today, *still pause-only* — and that one live control (the $20/day
`BudgetGuard`) is the pre-slice-17 behavior. Four independently-verified S1/S2 defects
mean the decision tree, the per-task >$5 approval, the manual modes, and the $100/month
hard cap do not actually fire. This is a stronger statement than the team's own G-15
("pause-only fallback still active") — the higher-tier controls aren't a fallback, they
are unreachable.

The component-level engineering is largely sound (idempotent resolve, row-as-state
polling, fail-closed `DiffValidator`). The failure is **system wiring and the
"every failure surfaces" guarantee**, not the individual mechanisms.

## 2. Verification log (Opus, independent of Fable)

Every load-bearing S1/S2 claim was re-checked against the code before acceptance:

| Finding | Claim | Verified? | Evidence |
|---|---|---|---|
| **#1** | Estimate-driven gate never fires for fresh work | ✅ Confirmed | `router.py:301-304` gates on `estimate_usd is not None`; the only `estimate_usd=` occurrences are internal pass-through (`router.py:309`) and re-dispatch of *already-escalated* rows (`cli_wiring.py:1869/1879`). No originating caller (agents, skills, auto_drafter, evolution, chat) supplies one. |
| **#2** | $100/month cap unenforced; warning is dead code | ✅ Confirmed | `budget.py:72-119` `check_pre_call` checks daily only; `check_monthly_warning` (`budget.py:121`) has zero callers; `_warned_months` is in-memory (`budget.py:70`) → re-warns each restart. |
| **#3** | Token-capped extension spend unlogged + uncaught | ✅ Confirmed | `router.py:563-568` raises `TokenLimitReachedError` *before* the `invocation_log.log()` at 570-592, after the billed API call returned (`metadata.cost_usd` populated). No `except TokenLimitReachedError` exists anywhere. Bonus: log-write failure is warn-only (`router.py:593-594`). |
| **#4** | Only live escalation path (tool builds) broken by un-substituted `target_paths` | ⚠️ Confirmed as a real code-vs-design tension | `escalation_gate.py:496,517` persist `_render_target_paths(...)` which is deliberately un-substituted (`escalation_gate.py:802-815` docstring) and **promises** `record_manual_handoff` later writes the substituted form. Needs implementation-time confirmation that that UPDATE is in fact missing (Fable's claim) — but either way the row fed to `DiffValidator` carries literal `{name}` globs. |
| **#10** | SMS fan-out never wired into the delivery loop | ✅ Confirmed | `EscalationDeliveryLoop` accepts `sms_manager` (`escalation_delivery_loop.py:70`) and uses it (228-235), but no construction site passes it — grep finds no `sms_manager=` argument anywhere. `self._sms_manager is None` always. |

**Conclusion:** Fable's critique is accurate and evidence-grounded, not hallucinated.
The remaining findings (#5–#9, #11–#20) are accepted at Fable's stated confidence and
re-verified at implementation time per the disposition below.

## 3. Triage dispositions

Legend: **ACCEPT** (sound, in-scope) · **ESCALATE** (safety/budget/scope decision for
owner) · **DEFER** (sound, trigger-gated) · **KEEP** (existing design is correct).

### S1 — make the budget cap real (ESCALATE as a group — changes runtime/safety posture)
| # | Finding | Disposition |
|---|---|---|
| 1 | Router-side deterministic estimation so the gate fires by default (don't rely on caller discipline) | **ESCALATE** — turning the gate on starts firing real escalations (Discord prompts, parked tasks). Owner decision §6. |
| 2 | Enforce $100/month cap + warning in `check_pre_call`; persist warned-month in DB | **ESCALATE** — pairs with #1; the actual hard-cap behavior. |
| 3 | Log the invocation *before* raising `TokenLimitReachedError`; add a catcher policy | **ACCEPT** — pure ordering/correctness fix; no posture change. Lands with the S1 PR. |

### S2 — correctness, wiring, and trust-envelope (mostly ACCEPT)
| # | Finding | Disposition |
|---|---|---|
| 4 | Persist substituted `target_paths` (or substitute at validation time) + ship the deferred real-worktree E2E | **ACCEPT** (confirm the missing-UPDATE at impl time) |
| 5 | `grant_budget_extension` returning `None` still resolves `api_extended` → phantom extension | **ACCEPT** — button callback must abort on `None`; `fire_and_wait` reads back the real row |
| 6 | Daily extension ceiling is offer-time-only (TOCTOU); re-check inside `grant()` in one txn | **ACCEPT** — also a `manual-escalation §10.6` spec change + doc fix |
| 7 | import-smoke executes unreviewed code unsandboxed, wrong tree, LLM string in `python -c` | **ESCALATE** — security posture. Cheap parts (`isidentifier()`, `python -I`, stripped env, pinned-SHA worktree) ACCEPT now; jail/namespace isolation DEFER (trigger: multi-user). |
| 8 | `import_io` AST lint bypassable by construction → reclassify as advisory; smoke is enforcement | **ACCEPT** (pairs with #7) |
| 9 | Tool-build scope grants whole-file write to shared config (`agents.yaml`, `skills.yaml`) | **ACCEPT** — add hunk-level additive-only check for config files in scope |
| 10 | SMS fan-out unwired; morning digest dead; ping failures unalerted | **ACCEPT** (one wiring line for SMS) — **challenges** `manual-escalation §12 Q4` which claims this was "resolved… slice 24 confirmed the wiring"; corroborates G-16/S22 |
| 11 | Dedup early-return reuses `fired=False` ("proceed and spend") | **ACCEPT** — add explicit `deduped_parked` outcome → `EscalationDecisionError` |
| 12 | Validation pipeline non-idempotent across crash/retry; skill promoted before row flip | **ACCEPT** — flip status first or one txn; fixture persist → upsert |
| 13 | Submitted rows can freeze forever, silently | **ACCEPT** — staleness sweep + `fallback_activated` alert |
| 14 | "Validated" is a property of a branch name, not a pinned commit; SHA pin optional/prefix | **ACCEPT** — resolve+stamp tip SHA at validation; make `sha` required (spec drift fix) |

### S3 — smaller correctness + hygiene (ACCEPT, lower urgency)
| # | Finding | Disposition |
|---|---|---|
| 15 | Day-boundary incoherence (UTC log vs local-date budgeting); refresh reopens *all* paused tasks | **ACCEPT** — standardize UTC; refresh only gate-paused tasks (migration note) |
| 16 | `apply_submission` validates payload mode against the NULL `mode` column, not `resolution` | **ACCEPT** — concrete exploit of S19; check `resolution`, then drop `mode` |
| 17 | Snooze doesn't silence high-severity re-pings; speculative tier is a black hole | **ACCEPT** — gate ping on `snoozed_until`; `ON CONFLICT` on insert; wire digest |
| 18 | Iteration-cap cancel orphans `tool_request` *and* re-arms duplicate escalations | **ACCEPT** — transition linked request `in_progress → open`; **sharpens S22** ("low priority" was wrong) |
| 19 | Config-contract drift: "all runtime controls overridable" is false; 3 copies of `manual_iteration_limit` | **ACCEPT** — fix doc; register threshold keys in catalog; single-source the limit |
| 20 | Estimates are static constants with no actuals reconciliation | **PARTIAL ACCEPT** (log `estimate_vs_actual`) + **DEFER** richer logic (trigger: first actual >2× estimate) |

### DEFER — sound but trigger-gated
- **`max_re_escalation_depth`** (S21/S24): **moot today** — nothing writes `parent_escalation_id`. Trigger: the slice that implements #3's re-escalation catcher.
- **Full sandbox isolation for import-smoke** (beyond the cheap parts in #7): trigger: multi-user Phase 2, or first tool build authored outside owner supervision.
- **Resume (vs rollback) for `api_extended` grants** (S18): keep void-on-boot. Trigger: first observed stale-grant void in prod logs.
- **Dependent-skill regression for tool builds** (S22/S24): agree with existing deferral.

### KEEP — existing design is correct; a naive critic would wrongly "fix" these
- `DiffValidator` conservative glob semantics (`diff_validator.py:87-127`) — fail-closed, dotfile-reject, empty-scope-fails. Fix the *inputs* (#4), not the matcher.
- The conditional-UPDATE optimistic lock (`escalation_repository.py:568-641`) — the UPDATE *is* the lock; reconcile the spec text (§10.7), not the code.
- Grant-before-resolve crash ordering in the button callback — fix the ignored `None` (#5), keep the sequence.
- State-in-rows + polling sweepers over per-request asyncio tasks (§15 decision) — this is what makes a future resume redesign cheap.
- Never-auto-merge / read-only host mount / human-as-operator — load-bearing for safety *and* ToS. Resist any "automate the merge" simplification.

## 4. Highest-leverage change

**Router-side deterministic cost estimation (#1)**, landed together with the two-line
monthly enforcement (#2) and the log-before-raise ordering (#3). The router already
imports `estimate_tokens` for Ollama budgeting; reuse it to compute a deterministic
floor (`tokens_in × input_rate + max_tokens × output_rate`) when no caller estimate is
supplied. This single change makes `spec_v3.md §13.1` true for the first time and turns
on everything downstream of the gate. Fix #4 immediately after (wiring #1 routes skill
builds into the same broken `target_paths` path).

## 5. Spec-sync obligations (when implemented)

- `spec_v3.md §13.1` — gate/monthly behavior becomes real; update the "current behavior" table and remove the pause-only roadmap note for the cap.
- `manual-escalation.md §10.5 row 6`, `§10.3 rows 4–5`, `§10.6 row 2`, `§10.7 row 1`, `§12 Q4`, `§15` — multiple code-vs-spec drifts identified above; reconcile each in the implementing PR.
- `docs/domain/cost.md:140` — "all runtime controls overridable" overclaim; correct.
- `open-backlog.md` G-15 — re-scope from "pause-only fallback" to "higher controls unreachable until #1/#2 land."

## 6. Owner decision required (escalation)

The S1 trio changes budget/safety posture, so per the program's guardrails it does not
auto-proceed. The pending fork is **how much to implement now** (asked separately).

---

## Appendix — program mechanics learned (Wave A pilot)

- **Context-pack sizing worked.** Pointing Fable at the largest modules first + the
  canonical design spec + the followups backlog produced precise, file:line-cited
  findings (~224K subagent tokens, ~19 min). No hallucinated evidence in the verified set.
- **Triage gate earned its keep.** Fable's framing ("structurally flawed") needed Opus
  verification to become actionable; and Fable *challenged* two existing followups
  (S20-FU2 is broader than one call site; §12 Q4's "resolved" is false) — exactly the
  uncorrelated-judgment value the program was designed for.
- **Adjust for next waves:** ask Fable to tag each finding "PROVEN vs SUSPECTED" (it
  did, well) and to flag spec-vs-code drift explicitly (it did) — keep both in the brief.
