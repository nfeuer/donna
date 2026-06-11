# Sub-Agent System — Fable Critique & Redesign Spec

**Date:** 2026-06-11
**Status:** Triaged — pending owner decision on scope + the §7.2 wire-or-delete call (§6)
**Critic:** Fable 5 (adversarial design critique)
**Triage:** Opus (independent verification)
**Related:** `spec_v3.md §7` (Sub-Agent System), §3.7 (concurrency), `CLAUDE.md` principles #2 (safety-first) / #6 (tool validation layer), Wave B-2 of the Fable critique program.

> Fourth Fable run. Captures the **verified** findings, **disposition**, and the
> two decisions only the owner can make. No code changed.

---

## 1. Executive finding (verified)

**Most of the §7.2 agent system is dormant, and the two safety seams Phase-6 agents
will stand on are decorative.** The built-and-tested pipeline — `AgentDispatcher`,
`PMAgent`, `SchedulerAgent.execute`, `PrepAgent`, `DecompositionService`, and the
agent-layer `ToolRegistry` — is **never constructed in production**. The live agent
surface is narrower and different: `DiscordIntentDispatcher → ChallengerAgent.match_and_extract
→ ClaudeNoveltyJudge`, with placement done by the event-driven `AutoScheduler` (not an
agent). This is the **same shape as the Scheduling critique** (a live bundle beside a
dormant-but-dangerous `CalendarSync`): the urgent work is in the live path; the dormant
seams are landmines that must be fixed *before* anything heavier is wired onto them.

**Good news (verified):** agent spend is **not** escaping accounting — every agent LLM
call routes through `ModelRouter.complete()`; there are **zero** direct provider imports
in `src/donna/agents/`. Principle #5 genuinely holds.

## 2. Verification log (Opus, independent)

| # | Claim | Verified? | Evidence |
|---|---|---|---|
| **1** | §7.2 dispatcher pipeline is dormant | ✅ | `AgentDispatcher(` appears only at `dispatcher.py:44` (its own docstring/factory); no construction in `cli_wiring`/`server`/`discord_bot`. `PrepAgent(`/`DecompositionService(` only inside their own factory methods. |
| **2** | Tool-validation layer is nominal/bypassable | ✅ | `tool_registry.py:87` — `if task_type is not None and not is_allowed(...)`: omit `task_type` (the default) → allowlist check **skipped**. `:101` `await handler(**params)` — raw params, **no schema validation**. |
| **3** | `config/agents.yaml` is decorative for dispatch | ✅ | Consumers are only dashboards (`admin_agents.py`, `admin_config.py`), a diff-lint (`tool_lint/allowlist.py`), and vault path redirection (`config.py:761`). Nothing in the dispatch path reads `enabled`/`autonomy`/`timeout`/`allowed_tools`. |
| **4** | **LIVE:** `_resume` silently drops escalations | ✅ | `discord_intent_dispatcher.py:317` discards the draft, then `:356` returns `no_action` for an `escalate_to_claude` re-parse → no judge, no task, no message. User input vanishes. |

**Conclusion:** the critique is accurate and the dormancy reframe is correct and load-bearing.

## 3. Triage dispositions

Legend: **ACCEPT** · **ESCALATE** (owner decision) · **DEFER** (trigger-gated) · **KEEP**.

### LIVE path (the actually-running system — fix now)
| # | Finding | Sev | Disposition |
|---|---|---|---|
| 4 | `_resume` drops `escalate_to_claude` (and `ready`+non-task/automation) → silent `no_action`; draft discarded before status check | S2 | **ACCEPT** — route resume-escalations to the novelty judge (as `_handle_escalate` does); never discard the draft before a terminal outcome; replace the terminal `no_action` with a "couldn't process, rephrase?" clarification. Highest-urgency: it's user-facing and live. |
| 8 | Challenger fail-open paths (exception → `challenger_skipped`; schema-validation failure → **proceeds with unvalidated LLM output**; transport-error → legacy matcher) emit **no** `dispatch_fallback_alert` | S2 | **ACCEPT** — keep fail-open (correct for a non-blocking quality gate, see §KEEP), but alert on all three paths, and on schema failure **degrade to `escalate_to_claude`** rather than trusting unvalidated output. |
| 7 | `transition_task_state` read-validate-**then**-lock (TOCTOU); §3.7.1 promises atomic | S2 | **ACCEPT (small)** — move the read+validate **inside** the write lock and re-read status; optionally `UPDATE … WHERE status=?` and treat rowcount 0 as invalid. Realizes §3.7.1 honestly. Two live writers exist (`AutoScheduler`, Discord done-intent). |
| 10 | In-memory `PendingDraft`/challenger threads orphan across restarts | S3 | **DEFER** — bounded blast radius (one-round Q&A; no longer gates scheduling). Trigger: repeated lost-clarification reports or multi-user. |

### DORMANT seams (the Phase-6 preconditions — gated on the §6 decision)
| # | Finding | Sev | Disposition |
|---|---|---|---|
| 1 | §7.2 pipeline built but never wired; docs/spec present it as live | S1 | **ESCALATE** — wire-or-delete is the owner's strategic call (§6). Drives whether #5/#6/#9 matter. |
| 2 | Tool-validation layer nominal (opt-in `task_type`, no param schemas, agents hold direct `db`/`router`) | S1 | **ESCALATE/ACCEPT** — the load-bearing Phase-6 seam. Make caller identity (`agent_name`, `task_type`) **required**; add per-tool param JSON-schemas; strip `db` from `AgentContext`; collapse the duplicate `agents/` vs `skills/` registries (keep the live skills one). |
| 3 | `agents.yaml` autonomy/enabled/timeout/allowlist enforced nowhere | S1 | **ACCEPT (with #2)** — the dispatcher becomes the enforcement point: skip `enabled: false`, apply config timeout, effective allowlist = config ∩ task-type. Agent code keeps no authority over its own ceiling. |
| 5 | Dispatcher failure→recovery leaky (no state transitions, swallowed PM/challenger failures, silent scheduler fallback, `cost_usd=0.0`, no alerts) | S2 | **ACCEPT-if-wired** — moot if §7.2 is deleted; required if wired. |
| 6 | `PrepAgent` strands rows in `in_progress`/`failed` forever; no sweeper, no timeout, no user notice | S2 | **ACCEPT-if-wired** — same gate. |
| 9 | `SchedulerAgent` checks `is_allowed("parse_task", "calendar_read")` (always False → empty calendar) and omits `task_type` | S2 | **ACCEPT-if-wired** — falls out of #2/#3; or deleted with §7.2. |

### Observability / hygiene
| # | Finding | Disposition |
|---|---|---|
| 11 | PM bills/logs as `task_decompose`; dispatcher reports `cost_usd=0.0`; PM prompt hardcoded (config-over-code drift) | **ACCEPT-if-wired** — give PM a `pm_assess` task type + externalized prompt; thread real cost. (Dormant today.) |

### KEEP — right as-is; a naive critic would break these
- **Challenger fail-open** (must never block task creation) — fix is the missing *alert*, not making it blocking.
- **Challenger off the scheduling critical path** (TI-FU1: time-bound tasks route straight to the scheduler regardless of pending Q&A) — the correct strand-bug fix; do not re-gate.
- **`ClaudeNoveltyJudge` fail-loud** (`validate_output` raises; `BudgetPausedError`/`ContextOverflowError` propagate) — don't wrap in catch-alls.
- **All agent LLM calls via `complete()`** — principle #5 genuinely held.
- **Fail-closed registry defaults** (unknown task type/tool → reject) — the holes are the optional `task_type` and missing param validation, not the check logic.
- **PrepAgent write-before-call idempotency** + **hallucinated-capability guard** — right patterns; need completion (sweeper/alert), not replacement.

## 4. Highest-leverage change

**Make the validation seam real before anything stands on it:** one `ToolRegistry`
(keep the live skills one), caller identity **required** (`agent_name`+`task_type`,
no defaults), per-tool param JSON-schemas validated pre-handler, and `db` removed from
`AgentContext` so agents act only through the registry — with the *dispatcher* (not the
agent) supplying the allowlist as `agents.yaml ∩ task_types.yaml`. This converts findings
#2, #3, #9 from decorative to code-enforced and is the precondition G-21/G-22 actually need.

## 5. The two decisions (escalated — §6)

1. **Fate of the dormant §7.2 pipeline (#1):** wire it (in shadow, behind a flag) or
   delete it and make §7.2/`orchestrator.md` describe the real flow. This determines
   whether #5/#6/#9/#11 are worth fixing at all.
2. **Scope now:** the live-path safety fixes (#4, #8, #7) are worth doing regardless;
   the dormant-seam hardening (#2, #3) is the Phase-6 precondition but not urgent.

## 6. Defer (trigger-gated)
- §3.7.2 worker pool / optimistic-lock version column / per-agent isolation — trigger: **G-21 enablement** (a second concurrently side-effecting agent). The in-lock recheck (#7) suffices for the single-loop present.
- Persisting pending drafts (#10) — trigger: repeated restart-orphan reports or multi-user.
- Multi-round challenger Q&A (OOS-2) — already deferred; do not build.

---

## Appendix — program mechanics (Wave B-2)

- Scoping Fable away from the unbuilt Coding/Communication agents (G-21/G-22) — while
  asking it to judge whether their *seams* are sound — produced the highest-value finding:
  those seams (tool validation, autonomy config) are decorative today, which is exactly
  what blocks G-21/G-22. The "dormant landmine" framing transferred cleanly from the
  Scheduling wave.
- PROVEN-vs-SUSPECTED tagging again isolated the one surprising structural claim (the
  whole §7.2 pipeline is unwired) for fast verification — confirmed in two greps.
