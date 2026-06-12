# Fable Design-Critique Program — High-Level Plan

**Date:** 2026-06-11
**Status:** Proposed — program scaffolding, pending first critique wave
**Owner:** Nick
**Related:** `spec_v3.md` (all §), `docs/superpowers/specs/` (design docs), `docs/superpowers/plans/` (implementation plans), `docs/superpowers/specs/followups.md`, `docs/superpowers/followups/open-backlog.md`, `CLAUDE.md` (design principles)

---

## 1. Premise — why two models

Donna is now a large, mature system (~24 slices shipped, 20+ subsystems, a
canonical 4,200-line `spec_v3.md`). Most of it was designed and built by the same
model lineage that runs the codebase day to day. That produces **correlated blind
spots**: the same assumptions that shaped the design also shaped the review of the
design. The cheapest way to break that correlation is to put a *different, equally
capable* model in the critic's chair.

This program splits the work along the axis each model is best at:

| Role | Model | What it does | Output artifact |
|---|---|---|---|
| **Critic / Architect** | **Fable 5** (`claude-fable-5`) | Reads a subsystem's design with fresh eyes, *not* anchored on the current implementation. Surfaces design flaws, hidden coupling, silent-failure paths, unjustified LLM-judgment, config-contract drift, and proposes a concrete improved design with trade-offs. Divergent, adversarial, redesign-oriented. | A **critique + redesign spec** under `docs/superpowers/specs/` |
| **Implementer / Executor** | **Opus 4.8** | Triages Fable's findings, decides what to accept/defer/escalate, turns accepted redesigns into a task-by-task plan, writes the code, tests, migrations, and keeps `spec_v3.md` in sync. Convergent, execution-oriented. | An **implementation plan** under `docs/superpowers/plans/` + the code/tests/spec-sync |

This deliberately mirrors the workflow the repo already runs on — the
**`specs/` → `plans/`** split. Fable owns the `specs/` side of a critique cycle;
Opus owns the `plans/` side and the implementation. Nothing about the existing
conventions changes; we are inserting a sharper critic upstream of the plan.

> **Why not just have Opus critique its own design?** It can and does (see
> `/spec-check`, `/code-review`). But a self-review shares the author's priors.
> Fable's value is *uncorrelated* judgment, not *better* judgment.

---

## 2. The operating model (critique → improve → implement)

Each subsystem runs through the same seven-step cycle. Steps 1, 3–7 are Opus;
step 2 is Fable.

1. **Context pack (Opus).** Assemble a self-contained bundle for the target area:
   the `docs/domain/*.md` page, the cited `spec_v3.md §`, the relevant source
   modules, and the open items for that area from `followups.md` /
   `open-backlog.md`. The pack must be readable without the rest of the repo so
   Fable can reason about it in one sitting.
2. **Fable critique (Fable).** Dispatch a subagent **with the Fable model
   override** and the critique brief (§5 template + the area-specific questions
   in §4). Fable returns a structured critique-and-redesign spec: ranked findings
   (severity + confidence), the recommended design change, trade-offs, and the
   `spec_v3.md §` each finding touches. Mechanism:
   `Agent(subagent_type: "Plan", model: "fable", prompt: <brief>)`.
3. **Triage (Opus).** Sort every finding into one of four buckets:
   - **Accept & implement** — sound, in-scope, consistent with `CLAUDE.md`.
   - **Escalate to Nick** — changes safety posture, scope, or budget; or Fable and
     the current design genuinely disagree. Use `AskUserQuestion`. **Never** quietly
     act on these.
   - **Defer** — sound but trigger-gated; append to `open-backlog.md` (feature gap,
     `G-*`) or `followups.md` (spec question) per their existing conventions.
   - **Reject** — record one line of rationale (e.g. "violates *safety first, dial
     back later*", or "already solved at `file:line`, Fable lacked that context").
4. **Redesign spec (Opus).** Promote the accepted findings into a dated design doc
   `docs/superpowers/specs/YYYY-MM-DD-<area>-fable-critique-design.md`, in the
   house style (Problem / Goals / Non-Goals / Design / Trade-offs), citing
   `spec_v3.md §`. Fable's raw critique is attached or linked as the rationale.
5. **Implementation plan (Opus).** Produce
   `docs/superpowers/plans/YYYY-MM-DD-<area>.md` — task-by-task, failing-test-first,
   the same format as the existing plans.
6. **Execute (Opus).** Implement on a feature branch: code, tests, Alembic
   migration if schema changes, `spec_v3.md §` updated in the same change, and the
   relevant `followups.md` / `open-backlog.md` entries closed or added.
7. **Verify (Opus).** Run `/pre-pr`, `/spec-check`, and `/code-review` before the
   change is considered done.

### Guardrails on the program itself
- **`CLAUDE.md` principles are non-negotiable inputs to every critique.** Fable is
  told to evaluate each design *against* them (config-over-code, safety-first,
  structured logging on every model call, internal-API-over-MCP, model
  abstraction, tool-validation layer) — not to relitigate them.
- **"Don't build speculatively" still governs.** Fable will surface more good ideas
  than should be built now. The backlog's trigger-gated discipline
  (`open-backlog.md`) is the throttle; most findings land as deferred items with a
  named trigger, not as immediate work.
- **The spec stays canonical.** A Fable critique never silently overrides
  `spec_v3.md`. Either the spec is updated in the same change, or the divergence is
  logged in `followups.md` — exactly the rule in `CLAUDE.md`.

---

## 3. Prioritization

Every subsystem was scored on four dimensions (1 = low, 3 = high concern):

- **Blast** — how many other subsystems break if this design is wrong (coupling).
- **Stakes** — safety/correctness/financial cost of a design defect (irreversible
  user-facing harm, real-money spend, autonomous writes).
- **Gap** — design-maturity gap: volume of open questions, hedges, deferred
  decisions, and `followups`/`G-*` items.
- **LLM** — how much correctness is delegated to model judgment without a
  deterministic guardrail (design quality → output quality).

| # | Subsystem | Spec § | Blast | Stakes | Gap | LLM | Score | Tier |
|---|---|---|:--:|:--:|:--:|:--:|:--:|:--:|
| 1 | Cost & Escalation | §13, §18, manual-escalation.md | 2 | 3 | 3 | 2 | **10** | **1** |
| 2 | Model Layer & Routing | §4 | 3 | 3 | 2 | 2 | **10** | **1** |
| 3 | Skill System | §23 | 2 | 2 | 3 | 3 | **10** | **1** |
| 4 | Sub-Agent System | §7, §8 | 2 | 3 | 2 | 3 | **10** | **1** |
| 5 | Scheduling Engine | §6 | 2 | 3 | 3 | 1 | **9** | **1** |
| 6 | Orchestrator & Intake | §3.4, §10 | 3 | 2 | 2 | 2 | **9** | 2 |
| 7 | Task System | §5 | 3 | 2 | 2 | 2 | **9** | 2 |
| 8 | Notifications & Escalation Delivery | §11 | 2 | 2 | 3 | 2 | **9** | 2 |
| 9 | Chat / Conversation Engine | §24 | 2 | 2 | 2 | 2 | **8** | 2 |
| 10 | Memory & Vault | §30 | 1 | 2 | 2 | 2 | **7** | 2 |
| 11 | Preferences / Learning | §9 | 1 | 2 | 2 | 2 | **7** | 3 |
| 12 | Resilience | §3.6, §18 | 2 | 2 | 2 | 1 | **7** | 3 |
| 13 | Integrations | §3.2, §12 | 2 | 2 | 2 | 1 | **7** | 3 |
| 14 | Replies (Universal Handler) | §10.3 | 1 | 2 | 2 | 2 | **7** | 3 |
| 15 | LLM Gateway & Queue | §26 | 2 | 1 | 2 | 1 | **6** | 3 |
| 16 | Automations | §25 | 1 | 2 | 2 | 1 | **6** | 3 |
| 17 | Observability / Insights / Collection | §14, §15 | 1 | 1 | 2 | 1 | **5** | 3 |
| 18 | API & Auth | §27, §28 | 1 | 2 | 1 | 1 | **5** | 4 |
| 19 | Setup Wizard | §29 | 1 | 1 | 1 | 1 | **4** | 4 |
| 20 | Management GUI | §15, §27 | 1 | 1 | 1 | 1 | **4** | 4 |

**Tier 1 — critique first (highest leverage).** Items 1–5. Safety-critical money,
the routing layer every call flows through, the two most LLM-judgment-heavy
autonomy surfaces, and the one subsystem whose defects are user-visible and hard to
walk back (double-booking).

**Tier 2 — critique next (the dependable core).** Items 6–10. High blast radius or
real stakes, but more battle-tested than Tier 1.

**Tier 3 — critique opportunistically.** Items 11–17. Worth a pass, lower stakes or
more stable; fold into a wave when an adjacent Tier 1/2 critique touches them.

**Tier 4 — light-touch / defer.** Items 18–20. Recently hardened (API auth) or low
architectural stakes (setup, admin UI). Critique only if a concrete problem
appears.

### Suggested sequencing (waves)
- **Wave A:** Cost & Escalation → Scheduling Engine. The two highest-stakes,
  highest-open-question surfaces; both have live `followups` backlogs to close.
- **Wave B:** Model Layer & Routing → Sub-Agent System. The cross-cutting routing
  spine, then the autonomy layer that rides on it.
- **Wave C:** Skill System (largest single critique; budget a full cycle for it
  alone).
- **Wave D:** Tier 2 (Orchestrator, Task System, Notifications, Chat, Memory/Vault),
  one per cycle.
- **Wave E+:** Tier 3 opportunistically; Tier 4 on demand.

---

## 4. Critique-area inventory

Cross-cutting lenses (§5) apply to **every** area, so they are not repeated below.
Each block lists only the area-specific signals and the sharpened questions to put
to Fable. Design-risk signals are drawn from the domain docs, the open-item logs,
and a subsystem-maturity survey.

### Tier 1

#### 1. Cost & Escalation — `§13`, `§18`, `manual-escalation.md`
The single most safety-critical subsystem: it stands between autonomous work and a
real-money API bill, and it is the most complex by file count (20+ modules,
slices 17–24) with the largest open backlog (`followups.md` S18–S24).

- **Signals:** Multiple overlapping controls (BudgetGuard pre-call, per-task
  EscalationGate, $20/day pause, 90% monthly warning, >$5 per-task approval) with no
  documented interaction matrix — which wins when two fire? Re-escalation parent
  chain unwired (S18); no `max_re_escalation_depth` (S21/S24). Token-limited
  extension silently fails the task (S18). `mode` duplicates `resolution` (S19).
  ClaudeCodePoller routes validation failures to an undefined "human review" path.
  Tool-gap surfacing fires from four detection points with severity promotion on
  re-emission → alert-spam risk.
- **Ask Fable:**
  1. Draw the precedence/interaction matrix for the five budget controls. Where can
     two fire on the same call, and is the resolution defined and safe?
  2. Is the over-budget decision tree (Approve/Manual/Pause/Cancel) closed under all
     failure modes — token-limit-during-extension, re-escalation depth, poller
     validation failure? Where does a task die silently instead of surfacing?
  3. The system trusts user-submitted Claude-Code branches validated by
     `DiffValidator` (glob `target_paths`) + `ToolLint` (6 rules). Is that envelope
     sound, or can a branch touch state outside its declared paths?
  4. Propose a single source of truth for escalation state that removes the
     `mode`/`resolution` duplication without losing audit fidelity.

#### 2. Model Layer & Routing — `§4`
Every LLM interaction in the system flows through here, so a design defect has
maximum blast radius. Core routing is stable; the edges are heuristic.

- **Signals:** Token estimation is `len(text)//4` with an upgrade *trigger*
  (overflow rate >10%) but **no mechanism to auto-upgrade** (G-26/G-28). On
  `ContextOverflowError` with no fallback configured, the task fails rather than
  re-estimating at lower precision. Shadow mode silently doubles cost for a task
  type with no dashboard kill-switch. Confidence scoring is "optional" with no
  guidance on when self-assessed vs logprob. Eval fixtures are hand-maintained;
  no path from production traffic to fixtures.
- **Ask Fable:**
  1. Is `len//4` tokenization a latent correctness bug or an acceptable heuristic?
     Design the smallest control loop that detects drift and degrades gracefully
     instead of failing the task on overflow.
  2. Shadow mode doubles spend with no off-switch. Design a stable-state exit
     (auto-disable once quality variance is bounded) consistent with the $100 cap.
  3. The router is the chokepoint for *model abstraction* (`CLAUDE.md` #5). Audit
     whether anything bypasses `complete()` or hardcodes a provider/alias.
  4. Propose a production-traffic → eval-fixture pipeline so the harness doesn't rot.

#### 3. Skill System — `§23`
The largest, most LLM-judgment-dependent subsystem (auto-draft → shadow → trusted →
degraded → evolve), shipped across five phases but **disabled by default** with
known config-wiring gaps (G-2). Its design quality directly governs autonomous
behavior quality.

- **Signals:** `skill_routing_enabled` is a code change, not config (violates
  *config over code*). `SkillSystemConfig` fields exist but aren't read at runtime
  (G-2) — possible dead code. Promotion/demotion uses Wilson-score CIs on
  *binarized* quality scores (F-W1-A flags this as the wrong statistic). Evolution
  loop rewrites degraded skills with **no defined success metric** (better quality?
  lower cost?) and **no defined detection** for a rewriter that produces a broken
  skill. Capability matching (MiniLM embeddings) can match two semantically-similar
  skills with no dedup. Shadow path runs in background — unclear if it can block the
  user-facing response.
- **Ask Fable:**
  1. Critique the lifecycle gates end-to-end. Is Wilson-on-binarized-scores the
     right promotion/demotion statistic, or should it be a continuous-score drift
     detector? What's the minimum-sample stability story?
  2. Define the evolution loop's success criterion and its safety envelope: how does
     a rewrite that *regresses* get caught before it reaches `trusted`?
  3. The whole subsystem is gated behind a hardcoded flag. Design the config-driven
     enablement path (closing G-2) so production turn-on is a YAML change with
     observable thresholds.

#### 4. Sub-Agent System — `§7`, `§8`
The autonomy layer. Safety-critical because agents take actions; design defects here
are the ones that act on the world.

- **Signals:** Tool access is declared in `task_types.yaml` but enforced **post-hoc**
  by ToolRegistry — no pre-flight schema check. Local-LLM tool-use progression
  (Stage 1→3) is gated on "90%+ over 100+ samples" with **no defined data-collection
  harness**. Coding Agent (G-21) and Communication Agent (G-22) are Phase-6 stubs
  with safety gates that must hold. The Challenger was just moved *off* the critical
  path (TI-FU1) but `spec_v3.md §7.1.1/§7.2` still describe it as the pre-PM gate —
  live spec drift. Escalation to the Claude Novelty Judge couples local-model
  confidence to cloud spend.
- **Ask Fable:**
  1. Is post-hoc tool validation sufficient, or does *tool validation layer*
     (`CLAUDE.md` #6) demand a pre-flight gate? Design the stricter version if so.
  2. The Stage 1→3 promotion criteria reference data that isn't being collected.
     Design the measurement harness, or make the criteria honest.
  3. Reconcile the Challenger's real (off-critical-path) role with `§7.1.1/§7.2`
     and close TI-FU1.
  4. Stress-test the Phase-6 agent safety gates *as designed* (before they ship):
     where could a Coding/Communication agent exceed its sandbox?

#### 5. Scheduling Engine — `§6`
Defects are directly user-visible and hard to undo (double-booking, missed
deadlines). Routing is now deterministic (good), but placement and negotiation are
incomplete.

- **Signals:** Conflict resolution ships 2 of 5 strategies (G-11: priority
  displacement, cascade-shift, dual-invite missing). Constraint-aware *negotiation*
  (propose alternative → on reject, propose moving other items) is designed but
  deferred to Plans 2/3. `constrained` time-intent syntax is informal/unspecified.
  Calendar poll is every 5 min — a 5-minute window where a new meeting can collide
  with a freshly-scheduled task. Timezone hardcoded to `America/New_York` fallback;
  DST transition unaddressed. User edits to Donna events are treated as implicit
  reschedule with no sanity bounds (move to midnight = "valid"). Weekly plan has no
  confirmation mechanism — silent acceptance?
- **Ask Fable:**
  1. Design the full conflict-resolution strategy set (closing G-11) and the
     negotiation loop, with the invariant *"any move of an existing item requires
     user confirmation"* held throughout.
  2. Formalize the `constrained` time-intent grammar so it's machine-checkable.
  3. Close the 5-minute collision window and the DST/midnight-reschedule edge cases
     with deterministic guards.

### Tier 2

#### 6. Orchestrator & Intake — `§3.4`, `§10`
The coupling hub: every channel enters here, so its design constraints everything
downstream.
- **Signals:** `PendingDraft`/clarification state is in-memory — lost on restart.
  Dedup pass-2 LLM failure is silent (output ignored, no escalation). Preference
  application happens *after* parse; a malformed rule can lose the captured task via
  a catch-all. Skill shadow path's blocking behavior under slowness is unspecified.
- **Ask Fable:** (1) Which in-memory state must survive a restart, and what's the
  cheapest durable store? (2) Audit every intake path for the "captured task silently
  lost" failure mode. (3) Define the shadow-path timeout/non-blocking contract.

#### 7. Task System — `§5`
The central data model everything else revolves around.
- **Signals:** Dedup pass-2 can return `related` with **undefined** semantics (merge?
  link? keep separate?). Priority escalation is partial (G-10: dependency-chain,
  user-lock missing) and re-evaluated "daily" at an unspecified time. Dependency-chain
  priority inflation is unbounded on a cycle. Phase-1 single-thread assumed, but
  transition side-effects could still race.
- **Ask Fable:** (1) Pin down `related`-match semantics. (2) Design bounded
  dependency-chain escalation (cycle-safe). (3) Is the state machine's
  read→validate→write→side-effect atomicity actually airtight under the current
  concurrency model?

#### 8. Notifications & Escalation Delivery — `§11`
- **Signals:** Tier 3 (email) unimplemented (G-14). Four independent proactive loops
  (post-meeting, evening, stale-task, inactivity) with **no dedup** between them →
  nag pile-up. Context expiry uses a day-boundary heuristic with unhandled DST.
  Acknowledgment detection is LLM-classified — "sure, soon" could reset a tier
  incorrectly. LLM nudge falls back to template strings of unknown quality.
- **Ask Fable:** (1) Design a single coordinator over the four proactive loops with
  shared rate/dedup. (2) Make tier-reset acknowledgment robust to misclassification.
  (3) Specify the template-fallback quality bar.

#### 9. Chat / Conversation Engine — `§24`
- **Signals:** Escalation cost *estimate* method is undefined (the gate that protects
  spend depends on it; see S20-FU2 — `estimate_usd` not passed). Session auto-summary
  failure → silent context loss. LLM proposes actions validated against ActionRegistry,
  but schema-injection isn't discussed.
- **Ask Fable:** (1) Define the escalation cost-estimation method and wire it to the
  gate. (2) Make summary failure observable, not silent. (3) Threat-model action
  proposal for injection.

#### 10. Memory & Vault — `§30`
- **Signals:** WebDAV (Obsidian) sync shares the git repo with agent writes →
  concurrent-write merge conflicts surface as broken markdown to the user.
  `sqlite-vec` load failure takes search offline silently while the orchestrator runs
  on. VaultWriter rejects on a `sensitive` reason that's **undefined**. No off-server
  backup (single-disk-loss = vault loss). Scope guardrails (`§30.7`) are the safety
  boundary on what memory can read/write.
- **Ask Fable:** (1) Design the concurrent-write reconciliation between human Obsidian
  edits and agent writes. (2) Make `sqlite-vec`-offline a surfaced, degraded state.
  (3) Define `sensitive` precisely and stress-test the scope guardrails.

### Tier 3 (condensed)

#### 11. Preferences / Learning — `§9`
Conflicting rules: lower-confidence silently ignored; no audit trail of application;
60s rule cache can apply a just-deleted rule; auto-disable threshold unspecified;
event-driven corrections have uncovered call sites (missing `source=` tags).
**Ask Fable:** rule-conflict resolution + an application audit trail; close the
event-driven coverage gaps; define the auto-disable threshold.

#### 12. Resilience — `§3.6`, `§18`
Circuit breaker resets on a single success (a flaky success masks an outage); degraded
parse sets `needs_reparse` with no defined re-parse trigger; external watchdog
under-specified; backup recovery is manual with no post-recovery validation;
crash-recovery only rolls back, no resume (S18). **Ask Fable:** breaker reset
hysteresis; the re-parse trigger; automated post-recovery validation.

#### 13. Integrations — `§3.2`, `§12`
11 integrations, 11 auth/rate/audit patterns — large consistency surface. Supabase
"recovery queue" unspecified. Discord pending drafts in-memory (no crash backup).
MCP context-cost mitigations (Tool Search, CodeMode) unvetted. Gmail not wired into
boot (G-1). **Ask Fable:** a uniform integration contract (auth/rate/audit/retry);
specify the Supabase recovery queue; durability for pending drafts.

#### 14. Replies (Universal Handler) — `§10.3`
FastPath multi-intent heuristics ("but"/"and also"/>2 commas) are fragile;
plan-confirm is keyword-matched ("yes"/"go ahead") and rejects unmatched phrasings
with no hint; unknown actions stripped silently; handler imported by dotted path with
no import-failure catch (can crash the handler). **Ask Fable:** robustify
intent/confirm classification; make silent action-stripping observable; guard the
dynamic import.

#### 15. LLM Gateway & Queue — `§26`
Preemption only during active hours → external requests can block internal work all
night; queue-depth alert at >10 with no auto-remediation; model-affinity gives no load
balancing if all items prefer one model; rate limiter rebuilt from `invocation_log`
with "synthetic timestamps" of unspecified accuracy. **Ask Fable:** a starvation-proof
night policy; an auto-remediation for sustained queue depth.

#### 16. Automations — `§25`
CadencePolicy matrix (per lifecycle state) unspecified; `AutomationConfirmationView`
holds a 30-min timeout coroutine (F-W3-A, won't scale); event-triggered (OOS-1) and
composition/DAG (OOS-3) deferred. **Ask Fable:** specify the cadence-policy matrix;
critique the confirmation-coroutine lifecycle.

#### 17. Observability / Insights / Collection — `§14`, `§15`
Four log sinks (stdout, Loki, `invocation_log`, planned `donna_logs.db`) risk
drifting out of consistency; dedicated log DB deferred (G-13/G-25); payload eviction
runs hourly (up-to-60-min over-budget window) and the in-memory byte counter can drift
on a mid-write crash; insights quality scores come from a 5% spot-check (sparse →
misleading). **Ask Fable:** a log-sink consistency contract; tighten payload eviction;
the minimum sample for trustworthy insights.

### Tier 4 (light-touch — critique only on a concrete trigger)

- **18. API & Auth — `§27`, `§28`.** Recently hardened. Multi-layer auth (IP → device
  token → Immich SSO) is a large surface that must fail *closed*; CSRF/SameSite
  strategy not documented; service keys trust internal CIDR. *Critique trigger:*
  `/admin/*` exposed beyond loopback.
- **19. Setup Wizard — `§29`.** Resumable; low stakes. State file has no schema
  versioning; `--reconfigure` step dependencies unchecked. *Critique trigger:* a real
  re-deploy hits a dependency-ordering bug.
- **20. Management GUI — `§15`, `§27`.** Admin UI. Config editing writes to disk with
  no validation/lint/preview. *Critique trigger:* a bad config write breaks a
  service.

---

## 5. The Fable critique brief (reusable template)

Use this as the prompt skeleton for every step-2 dispatch. Fill the `{{area}}`
specifics from §4.

```
You are a principal systems architect reviewing ONE subsystem of "Donna", an
async-Python AI personal assistant. You are a fresh, adversarial critic: assume
the current design is wrong until it proves itself. You are NOT implementing —
you are critiquing and redesigning.

CONTEXT PACK (everything you need is below; do not assume anything not here):
- Domain doc: {{docs/domain/...}}
- Canonical spec section(s): {{spec_v3.md §...}}
- Source modules: {{paths}}
- Known open items: {{followups / G-* entries}}

NON-NEGOTIABLE PRINCIPLES (evaluate the design AGAINST these; do not relitigate):
1. Config over code   2. Safety first, dial back later
3. Structured logging on every model call   4. Internal API over MCP
5. Model abstraction (all LLM calls via complete())   6. Tool validation layer
Plus: every fallback/except path must alert (no silent degradation); spec_v3.md
is canonical; don't propose speculative builds without a named trigger.

APPLY THESE CROSS-CUTTING LENSES, THEN THE AREA-SPECIFIC QUESTIONS:
A. Silent-failure audit — every except/fallback/timeout: does a real failure get
   hidden? Where does a user-captured intent get lost with no signal?
B. LLM-judgment soundness — where is correctness delegated to a model with no
   deterministic guardrail? Is that the right call?
C. Coupling & restart-fragility — hidden coupling, shared mutable state, in-memory
   state that dies on restart.
D. Config-contract drift — thresholds hardcoded that should be YAML/JSON config.
E. Spec drift — where does the design/implementation diverge from spec_v3.md §?
F. Failure & recovery — what happens on a crash mid-operation?

AREA-SPECIFIC QUESTIONS:
{{the §4 "Ask Fable" list}}

OUTPUT (markdown, ranked, decision-ready):
For each finding: Title | Severity (S1–S3) | Confidence (High/Med/Low) |
  Evidence (file:line or doc §) | Why it matters | Recommended redesign |
  Trade-offs / what it costs | spec_v3.md § affected.
End with: the single highest-leverage change, and anything you'd defer with its
trigger condition.
```

**Mechanism:** `Agent(subagent_type: "Plan", model: "fable", prompt: <brief above>)`,
run one subsystem per dispatch (do not batch — each needs the full context window).
Capture the returned markdown verbatim as the rationale attached to the step-4
redesign spec.

---

## 6. Definition of done (per critique cycle)

A subsystem's critique cycle is complete when:

- [ ] Fable critique captured as a dated artifact under `docs/superpowers/specs/`.
- [ ] Every finding triaged into accept / escalate / defer / reject **with a recorded
      disposition** (no finding silently dropped).
- [ ] Accepted findings promoted to a redesign spec citing `spec_v3.md §`.
- [ ] Deferred findings appended to `open-backlog.md` (`G-*`) or `followups.md` with a
      named trigger.
- [ ] Escalations resolved with Nick via `AskUserQuestion` before any related code
      lands.
- [ ] If code shipped: implementation plan exists, tests pass, migration present (if
      schema changed), `spec_v3.md §` updated in the same change, `/pre-pr` +
      `/spec-check` + `/code-review` green.

---

## 7. Risks of this meta-process (and mitigations)

- **Fable critiques a problem already solved.** Its context pack is a snapshot; the
  code may be ahead of the doc. *Mitigation:* Opus's triage step rejects with a
  `file:line` pointer; the cross-cutting lens E (spec drift) often *is* the finding.
- **Fable over-engineers / proposes speculative builds.** *Mitigation:* the
  "don't build speculatively" guardrail and the backlog's trigger discipline — most
  findings become deferred items, not work.
- **Fable's redesign conflicts with "safety first, dial back later."** *Mitigation:*
  any change to safety posture is an automatic escalation to Nick, never
  auto-accepted.
- **Critique fatigue / no throughput.** *Mitigation:* the wave sequencing in §3 — one
  subsystem per cycle, Tier 1 first; a critique with zero shipped changes is still a
  success if it produces triaged backlog items.

---

## 8. First action

Run **Wave A, subsystem 1 (Cost & Escalation)** end-to-end as the pilot — it has the
highest stakes, the richest open backlog (S18–S24) to validate findings against, and
will shake out the context-pack and triage mechanics before the program scales to the
rest of the inventory.
