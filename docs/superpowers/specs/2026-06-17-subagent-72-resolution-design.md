# §7.2 Sub-Agent System — Resolution Design ("keep the ideas, drop the framework")

**Date:** 2026-06-17
**Status:** Decided (owner) — **R1 + R2 shipped (2026-06-17)**; R3 remaining
**Decision owner:** Nick
**Related:** `spec_v3.md §7` (Sub-Agent System), §7.2 (Agent Execution Flow), §3.7 (concurrency); `CLAUDE.md` principles #2 (safety-first), #4 (internal API over MCP / direct module calls), #6 (tool-validation layer); prior critique `docs/superpowers/specs/2026-06-11-subagent-system-fable-critique-design.md`; feature gaps G-21 (Coding agent) / G-22 (Communication agent).

> Resolves the escalated wire-or-delete decision (critique §5, decision #1). The
> answer is neither "wire the pipeline" nor "delete everything": **keep the
> capability ideas, drop the generic dispatch framework.** This doc records the
> analysis, the resolution, and the sequenced slices. No code changed yet.

---

## 1. Decision (TL;DR)

The §7.2 multi-agent pipeline (`AgentDispatcher` + a uniform `Agent` protocol orchestrating `PMAgent` → `SchedulerAgent`/`PrepAgent`/Research) is **built, unit-tested, and never wired into production**. Rather than wire it or bulk-delete it, we split it by value:

| Component | Verdict | Action |
|---|---|---|
| `AgentDispatcher` (orchestration indirection) | Over-built; nothing live uses it; against principle #4 | **Delete** |
| `Agent` protocol as a *dispatch contract* | Premature generalization over a roster that's mostly phantom | **Delete** (keep only the shared result/record dataclasses if a salvaged service wants them) |
| `PMAgent` | Routes to phantom agents; clarifying-questions already live in Challenger | **Delete**; fold the unique increment (acceptance criteria) into the live Challenger path *if/when wanted* |
| `SchedulerAgent` | Fully superseded by the event-driven `AutoScheduler` + negotiation loop | **Delete** |
| `DecompositionService` | Clean, unique capability; already a direct service (principle-#4 shaped) | **Salvage** — re-home + wire to a trigger |
| Tool-validation seam (`ToolRegistry`, required caller identity, param schemas, no raw `db`) | The one idea worth more than the framework; principle #6 made real | **Make real on the live path** (own slice) |
| `config/agents.yaml` | **Live allowlist registry** (see correction below) — describes the live `challenger`/`research` agents and is consumed by the tool-lint + admin UI | **Keep**; reshape deferred to R3 |
| DB columns `agent_eligible` / `assigned_agent` / `agent_status` | `assigned_agent` never written; others minimally used by Prep | Keep `agent_eligible`/`agent_status` (Prep uses them); **drop `assigned_agent`** in a later cleanup migration |

> **Correction (2026-06-17, found during R1 recon).** The initial draft listed
> `config/agents.yaml` for deletion. That is **wrong** and is superseded: `agents.yaml`
> is not dead dispatcher config — it is the canonical per-agent **allowlist registry**
> that describes the *live* `challenger` and `research` agents and is consumed by the
> tool-lint safety check (`cost/tool_lint/allowlist.py`), the admin dashboards
> (`api/routes/admin_agents.py`, `admin_config.py`), and the `donna-ui` Agents page.
> Deleting it would break live machinery and remove the very allowlist source R3 wants
> to make *load-bearing*. **R1 keeps `agents.yaml` and all its consumers untouched;** any
> trimming of its now-dead `pm`/`scheduler` entries is folded into R3's registry reshape,
> not R1.

---

## 2. Context — what's actually there

The prior critique (`2026-06-11-subagent-system-fable-critique-design.md`) verified, in two greps, that the entire §7.2 pipeline is dormant: `AgentDispatcher(` is constructed nowhere outside its own docstring; `PMAgent`/`SchedulerAgent`/`DecompositionService` are never instantiated. The **actually-live** agent surface is narrower and different in shape:

```
DiscordIntentDispatcher → ChallengerAgent.match_and_extract → {ready | needs_input | escalate_to_claude}
                                                   escalate_to_claude → ClaudeNoveltyJudge
time-bound placement → AutoScheduler (event-driven; NOT an agent) → Scheduler.find_next_slot / negotiate_placement
prep research → PrepAgent (background loop; does NOT use the dispatcher)
```

The critique already landed the live-path safety fixes (#4 resume-escalation routing, #8 challenger fail-open alerting, #7 transition TOCTOU, #2-core required `task_type`+`agent_name`) and deferred the rest **to this decision**.

## 3. Analysis — there are two separations, judge them apart

### 3.1 The framework separation — over-built and toothless

A generic `Agent` protocol (`name`, `allowed_tools`, `timeout_seconds`, `execute(task, context) → AgentResult`; `base.py:54`) plus an `AgentDispatcher` that routes a task PM → execution agent. Judged cold:

1. **Nothing live flows through it.** Challenger, NoveltyJudge, AutoScheduler, and even PrepAgent all run *without* the dispatcher. The abstraction abstracts over a population that never uses it — the live code already voted against it.
2. **It's built around phantoms.** `PMAgent._recommend_agent` (`pm_agent.py:138`) routes to `"coding"` and `"communication"` agents that don't exist (G-21/G-22, unbuilt) and `"scheduler"`, now dead. The roster it orchestrates is mostly imaginary.
3. **No teeth where it matters.** A multi-agent framework earns its cost by *confining capability*. But `AgentContext` hands every agent raw `db` and `router` (`base.py:35-39`), so `allowed_tools` is advisory — an agent can bypass the registry entirely. The critique verified the allowlist was skippable and `agents.yaml` ceilings are enforced nowhere (#2/#3). **We'd be paying for separation (indirection, protocol ceremony) without buying its benefit (a hard safety boundary).**
4. **Against our own principles.** Principle #4 says the orchestrator calls integrations via *direct Python modules*. The dispatcher is an indirection layer the principles never asked for.

Verdict: the *degree of separation* is not good. Premature generalization with a decorative safety boundary.

### 3.2 The capability ideas — worth keeping, wrong packaging

- **Task decomposition (`DecompositionService`, `decomposition.py`)** — the standout. Production-quality: real prompt template (`prompts/task_decompose.md`), `validate_output` fail-loud, two-pass dependency-UUID resolution. Tellingly it is **not** an `Agent` — it's a plain service with a direct constructor (`decomposition.py:51`), already principle-#4 shaped. Nothing else in the codebase turns a big task into a sequenced subtask graph. **Unique value.**
- **Tool-validation boundary** — the idea (one registry, required caller identity, per-tool param JSON-schemas, `db` removed from `AgentContext` so agents act only through the registry) is the critique's "highest-leverage change" (§4) and is **principle #6 made real**. It's the precondition for *any* future autonomous agent. Worth doing on the live skills/registry path regardless of the framework's fate.
- **Completeness assessment (`PMAgent`)** — mostly redundant: the "ask targeted questions" step is already live in Challenger's `needs_input`. The only unique increment is "package requirements + acceptance criteria," which belongs folded into the Challenger/NoveltyJudge path, not a separate agent that routes to phantoms.

## 4. Resolution plan

1. **Delete the framework.** Remove `orchestrator/dispatcher.py` (`AgentDispatcher` + the `AgentActivityListener` protocol), `agents/pm_agent.py`, `agents/scheduler_agent.py`, `integrations/discord_agent_feed.py` (the inert `AgentActivityFeed` that only fed the dispatcher), and their tests. Trim `agents/base.py` to only what live consumers still import (keep the live agents — Challenger, NoveltyJudge, Prep — and shared infra `ToolRegistry` / `AgentContext` as needed). Surgically remove the dormant `_dispatcher`/`AgentDispatcher` references from `discord_bot.py` (lines 35/68/88/571-572/1113-1117) **without touching the live `_intent_dispatcher`**, and the inert `AgentActivityFeed` construction from `cli_wiring.py`. **Keep `config/agents.yaml` and its consumers** (see correction above). Rewrite `spec_v3.md §7.2`, `docs/domain/agents.md`, and `docs/domain/orchestrator.md` to describe the real flow.
2. **Salvage decomposition.** Re-home `DecompositionService` as a first-class direct service and give it a trigger — a Discord `/breakdown <task>` command and/or an auto-trigger when a task's `estimated_duration` exceeds a configurable threshold. Keep it principle-#4 shaped (orchestrator calls it directly; no dispatcher).
3. **Make the validation seam real.** Separate slice: one `ToolRegistry` (keep the live skills one), caller identity (`agent_name` + `task_type`) **required** with no default-skip, per-tool param JSON-schemas validated pre-handler, and `db` stripped from `AgentContext` so agents act only through the registry. This converts critique findings #2/#3/#9 from decorative to code-enforced and is the real prerequisite for G-21/G-22.
4. **Fold (deferred).** If/when we want richer intake, add acceptance-criteria packaging to the live Challenger/NoveltyJudge path. Not in scope now.

## 5. Sequenced slices

- **R1 — Framework deletion + spec/doc reconciliation.** Lowest risk, highest clarity. Removes the dormant landmine and makes the spec honest. Verify the live path (Challenger/NoveltyJudge/AutoScheduler/Prep) is untouched; confirm `ToolRegistry`/`AgentContext`/`base.py` keep only their live consumers.
- **R2 — Decomposition as a direct service. ✅ Shipped 2026-06-17.** `DecompositionService` is constructed in `cli_wiring` (where `router`/`project_root` are in scope) and injected into `register_commands`, which exposes the `/breakdown <task>` Discord command — task-id autocomplete, defers for the LLM call, persists the subtask graph, and renders the plan (durations, dependency back-references, open questions, deadline concern). Called directly, no dispatcher. The auto-threshold trigger on `estimated_duration` is deferred (config-gated, future).
- **R3 — Tool-validation seam (the real boundary).** The principle-#6 hardening. Gated/independent; the actual precondition before any future Coding/Communication agent. Larger and touches the live skills registry, so it lands last and on its own.

(R1 and R3 are independent; R2 depends on nothing. Recommended order R1 → R2 → R3, but R3 can move earlier if a write-capable agent gets prioritized.)

## 6. Spec & doc updates (tracked, executed per-slice)

- `spec_v3.md §7.2` — rewrite to describe the live flow (Challenger → NoveltyJudge; AutoScheduler placement; Prep loop); drop the PM/Dispatcher/SchedulerAgent narrative. **Lands with R1.** Until then, §7.2 carries a forward-pointer to this doc (added now).
- `docs/domain/agents.md` — same reconciliation (Agent Execution Flow section). **R1.**
- `config/agents.yaml` — **retained** (live allowlist registry). Any trim of its dead `pm`/`scheduler` entries is folded into R3's registry reshape, not R1.
- `docs/superpowers/specs/followups.md` — entry **SA-72** added now; closed per-slice.

## 7. Principle alignment

- **#2 (safety-first / minimal autonomy):** deleting a generic autonomous-dispatch pipeline with a decorative safety boundary is the conservative move; R3 then builds the *real* boundary before any autonomy is added.
- **#4 (internal API over MCP / direct module calls):** decomposition becomes a direct service the orchestrator calls; the dispatcher indirection goes away.
- **#6 (tool-validation layer):** R3 is this principle made load-bearing instead of decorative.

## 8. Non-goals / deferred / open questions

- **Non-goal:** reviving a multi-agent dispatcher now. If G-21/G-22 (Coding/Communication agents) are greenlit later, a dispatcher may return — but only *after* R3, and shaped by real need, not a phantom roster.
- **Deferred:** acceptance-criteria packaging (PMAgent's only unique increment) into the Challenger path.
- **DB cleanup:** `assigned_agent` is never written — drop it in a later migration (low priority; not R1, to keep R1 a pure code/doc delete with no schema churn). `agent_eligible` / `agent_status` stay (Prep uses them).
- **Open:** R2's trigger shape — `/breakdown` command vs. auto-threshold vs. both — decide at R2 kickoff. Recommendation: command first (explicit, safe), auto-threshold behind config later.
- **`git` preserves the deleted framework** — if the strategic call reverses, the pipeline is a `git revert` away as a starting point.
