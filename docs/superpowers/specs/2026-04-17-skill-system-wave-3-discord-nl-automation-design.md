# Skill System Wave 3 — Discord Natural-Language Automation Creation

**Status:** Draft
**Author:** Nick (with brainstorming assistance from Claude)
**Date:** 2026-04-17
**Scope:** Medium-large. Nineteen deliverables bundled. 6–9 days of focused work.
**Predecessors:**
- `2026-04-16-skill-system-wave-1-production-enablement-design.md` (Wave 1, PR #44)
- `2026-04-17-skill-system-wave-2-first-capability-design.md` (Wave 2, PR #46)
- `2026-04-15-skill-system-and-challenger-refactor-design.md` (original skill system + challenger refactor design)

---

## 1. Overview

Wave 2 made `product_watch` run end-to-end through `AutomationDispatcher`, but creation still requires `POST /admin/automations`. The challenger refactor promised in `2026-04-15-…-challenger-refactor-design.md` §6.7 was deferred at Phase 5 merge (see that spec line 1139: *"Natural-language creation flow deferred. AS-5.1 via Discord depends on a challenger refactor that outputs trigger_type=on_schedule… the challenger adapter is a downstream task."*). Requirements R4–R8 of that spec are still unchecked. Wave 3 closes them.

What changes conceptually:

1. `ChallengerAgent.match_and_extract` becomes the **unified entry point** for all free-text Discord input. One local-Ollama call per message performs intent parse + capability match + input extraction + quality self-assessment simultaneously. Task vs automation is *derived* from the extracted fields (presence of `schedule` + recurrence → automation), not pre-classified.
2. The Claude novelty call (fired when no capability matches with sufficient confidence) extends its output schema to return execution-ready extraction plus a `skill_candidate` verdict plus a `polling_interval_suggestion` for "when X happens" phrasings.
3. Multi-turn clarifications reuse the existing Discord thread-reply primitive, extended to hold automation partial drafts alongside task drafts. 30-minute TTL.
4. A confirmation card (discord.py `View`) appears **for automations only** before creation. Approve → `AutomationRepository.create()` via internal Python call. Tasks skip the card (existing UX).
5. `skill_candidate_report` gains a `claude_native_registered` status so Claude's "not a candidate" judgment persists and short-circuits the nightly detector.
6. **Cadence clamping by lifecycle.** Automations get a `target_cadence_cron` (user's wish) and `active_cadence_cron` (clamped by policy per skill lifecycle state). Dispatcher schedules from the active cadence. When a skill transitions state, a hook recomputes all affected automations' active cadence — user's "every 15 min" intent automatically lights up when the skill reaches `trusted`.

After Wave 3, Nick can DM *"watch https://cos.com/shirt daily for size L under $100"*, see a confirmation card, click Approve, and the automation runs on schedule with a Discord DM when the price drops — no REST call required.

Wave 3 is **user-visible feature delivery** on top of Wave 1/2 infrastructure. It also folds in five P2 cleanups (F-W2-C/D/E/G and F-10) that naturally pair with the Discord wiring touchpoint.

---

## 2. Out of Scope

| # | What | Why deferred | When to reconsider |
|---|---|---|---|
| OOS-W3-1 | Voice-triggered challenger (OOS-12 in original) | Orthogonal to skill system; voice pipeline is its own project. | When voice UX is prioritized. |
| OOS-W3-2 | Dashboard UI for automation/task creation or editing (F-4) | Separate project track; separate brainstorm cycle with frontend approach decided. | Wave 4+. |
| OOS-W3-3 | `on_event` trigger subsystem (OOS-1) | No push/webhook ingress in Wave 3. Most "when X happens" intents are polling + alert_conditions patterns; the challenger handles them as such (interval inferred from the phrasing per the parse prompt heuristic in W3-D3). Build the subsystem only when a concrete driving case emerges where polling is impossible (e.g., Stripe webhooks) — schema addition plus HTTPS ingress is a standalone wave. | When a push-only use case appears. |
| OOS-W3-4 | Self-hosting the challenger as a skill-system skill | Routing-layer-as-skill is a departure from Donna's precedent (CapabilityMatcher, ChallengerAgent, ModelRouter are all plain Python), not a Wave 3 optimization. The skill system just became production-ready — the first hot-path consumer should be the *capability* (product_watch), not the *router*. | Wave 4+ if real invocation-log fixtures accumulate and motivate it. |
| OOS-W3-5 | Additional seed capabilities beyond `product_watch` (e.g., `news_check`, `meeting_prep`, `email_triage`) | Validate the NL flow + cadence policy + skill executor path end-to-end with the one capability we have before replicating. | Wave 4+ on demand. |
| OOS-W3-6 | Per-capability alert-condition DSL | `product_watch` bakes `triggers_alert` into its own skill output; no Wave 3 use case demands a richer DSL. | When a capability needs conditions richer than its skill's own output booleans. |
| OOS-W3-7 | Confirmation card for tasks | Low-stakes; existing one-shot task flow works without it. Only automations get the card because scheduled recurrence is a bigger commitment. | If misclassification complaints accumulate in production. |
| OOS-W3-8 | Auto-modify existing automations from a new NL parse | E.g., user says "bump the jacket check to hourly" — mapping that to an existing automation_id is its own inference problem. | Wave 4+; F-4 dashboard editing is the natural interim UI. |
| OOS-W3-9 | Cost estimate on the confirmation card | Premature without F-12 Grafana cost attribution in place. | After F-12 lands. |

---

## 3. Core Concepts and Vocabulary

**Unified parse.** A single local-Ollama call that takes a free-text Discord message and returns `ChallengerMatchResult` with intent classification, capability match, input extraction, schedule/deadline/alert_conditions extraction, missing fields, self-assessed confidence, and low-quality signals — all in one round-trip. Replaces the two-step "classify then extract" pattern that earlier brainstorming considered.

**Intent kind derivation.** `intent_kind ∈ {task, automation, question, chat}` is *derived* from extracted fields, not pre-classified: presence of `schedule` + recurrence → `automation`; presence of a `deadline` or no timing → `task`; no actionable request → `question` / `chat`. The LLM emits the derived value but the downstream router treats field presence as authoritative.

**Novelty judgment.** The Claude call fired when `ChallengerAgent` emits `status=escalate_to_claude` (no capability match with `match_score ≥ 0.4`). Extended in Wave 3 to return: execution-ready extraction for the message, `trigger_type ∈ {on_schedule, on_manual, on_message}`, `polling_interval_suggestion` for "when X happens" phrasings that can't react instantly, and `skill_candidate: bool` + `skill_candidate_reasoning` for lifecycle registry decisions.

**Claude-native registered pattern.** A `skill_candidate_report` row with `status='claude_native_registered'` and a `pattern_fingerprint` hash. Records Claude's judgment that a given task pattern is genuinely one-off (not reusable). Nightly `SkillCandidateDetector` skips patterns matching this fingerprint, preventing drafting churn on personal errands.

**Cadence clamping.** Runtime policy enforcement on automation scheduling. Each automation stores `target_cadence_cron` (user's stated wish) and `active_cadence_cron` (policy-clamped). The dispatcher schedules from `active_cadence_cron`. Policy caps depend on the matched capability's current skill lifecycle state: `claude_native`/`sandbox` → 12h min interval; `shadow_primary` → 1h; `trusted` → 15m (inherits user target). Automations are auto-reclamped when the underlying skill transitions state.

**Pending draft.** A per-user in-memory record of a partial task or automation extraction awaiting clarification replies. Keyed by `thread_id`; 30-minute TTL; lost on orchestrator restart (acceptable for v1 per Wave 3's chosen state model). Promoted from the existing Wave 1/2 task-clarification primitive into a shared `PendingDraftRegistry` so both intent kinds reuse it.

**Confirmation card.** A discord.py `View` with Approve / Edit / Cancel buttons, rendered as an embed that surfaces every extracted field (capability, inputs, schedule, alert conditions, target vs active cadence if clamped). Posted only for automations — tasks skip it.

---

## 4. Architecture

### 4.1 No process split changes

Orchestrator + API split unchanged from Wave 2. `DonnaBot` lives in the orchestrator (has `NotificationService` in-process per Wave 1). The refactored challenger runs inside `DonnaBot.on_message`. API unchanged.

### 4.2 Message flow (end-to-end)

```
Discord DM arrives
  │
  ▼
DonnaBot.on_message(msg)
  │
  ├─ Is this a reply to an active pending-draft thread?
  │    YES → resume pending draft with msg as clarification input
  │    NO  → start new parse
  │
  ▼
ChallengerAgent.match_and_extract(msg, user_id)
     (local Ollama; task_type=challenge_task)
  │
  │  Returns ChallengerMatchResult:
  │    status: ready | needs_input | escalate_to_claude | ambiguous
  │    intent_kind: task | automation | question | chat
  │    capability: CapabilityRow | None
  │    match_score: 0..1
  │    extracted_inputs: {...}
  │    schedule: {cron, human_readable} | None
  │    deadline: datetime | None
  │    alert_conditions: {expression, channels} | None
  │    missing_fields: [...]
  │    clarifying_question: str | None
  │    confidence: 0..1
  │    low_quality_signals: [...]
  │
  ▼
DiscordIntentDispatcher.dispatch(result, user_id)
  │
  ├─ status == needs_input OR ambiguous:
  │    → post clarifying_question to thread
  │    → persist PendingDraft
  │    → return
  │
  ├─ status == escalate_to_claude:
  │    → ClaudeNoveltyJudge.evaluate(msg, registry_snapshot)  [Claude]
  │    │    Returns: { intent_kind, trigger_type, inputs,
  │    │              schedule, deadline, alert_conditions,
  │    │              skill_candidate: bool,
  │    │              skill_candidate_reasoning: str,
  │    │              polling_interval_suggestion: str | None }
  │    └─ proceed to intent_kind branching below
  │
  └─ status == ready:
       intent_kind == task?
         → TaskCreationPath.create(extracted)      [existing Wave 1/2 flow]
       intent_kind == automation?
         → AutomationCreationPath.prompt_and_create(extracted)
             ├─ compute active_cadence_cron from target + policy
             ├─ post confirmation card with target vs active if clamped
             ├─ on Approve → AutomationRepository.create(...)
             ├─ on Edit    → re-open clarification thread
             └─ on Cancel  → discard PendingDraft
       intent_kind == question | chat?
         → existing chat flow (unchanged)
```

### 4.3 Component inventory

| Component | Role | New / Changed | File |
|---|---|---|---|
| `ChallengerAgent.match_and_extract` | Unified parse. Output schema extended with automation fields, confidence, low_quality_signals. | **Changed** | `src/donna/agents/challenger_agent.py` |
| `ClaudeNoveltyJudge` | Claude call for no-match escalation. Returns extraction + skill_candidate verdict + polling_interval_suggestion. | **New** | `src/donna/agents/claude_novelty_judge.py` |
| `DiscordIntentDispatcher` | Post-challenger routing: clarify / escalate / create-task / create-automation. Owns thread-reply resumption. | **New** | `src/donna/orchestrator/discord_intent_dispatcher.py` |
| `AutomationCreationPath` | Renders the confirmation card, handles button interactions, calls `AutomationRepository.create()` on approve. | **New** | `src/donna/automations/creation_flow.py` |
| `PendingDraftRegistry` | Per-user in-memory map of pending drafts (task or automation). Thread-id keyed. Promoted from existing task-clarification primitive. | **Extended** | `src/donna/integrations/discord_pending_drafts.py` |
| `AutomationConfirmationView` | discord.py `View` with Approve / Edit / Cancel buttons + embed rendering. | **New** | `src/donna/integrations/discord_views.py` |
| `CadencePolicy` | Loads `config/automations.yaml`; exposes `min_interval_for_state(lifecycle_state, capability_override)`. | **New** | `src/donna/automations/cadence_policy.py` |
| `CadenceReclamper` | Recomputes `active_cadence_cron` for affected automations on state transition; persists + reschedules. | **New** | `src/donna/automations/cadence_reclamper.py` |
| `ChallengerParsePrompt` | Jinja2 prompt template emitting the extended schema. | **New** (replaces inline prompt) | `prompts/challenger_parse.md` |
| `ClaudeNoveltyPrompt` | Prompt for the novelty judge. | **New** | `prompts/claude_novelty.md` |
| `challenger_parse.json` / `claude_novelty.json` | Extended output schemas for validation. | **New/Changed** | `schemas/` |

### 4.4 Data model changes

| Change | Purpose | Migration |
|---|---|---|
| `skill_candidate_report.status` adds value `'claude_native_registered'` | Claude's "not a skill candidate" judgment persists; nightly detector short-circuits. | `alembic/versions/xxx_skill_candidate_status_claude_native.py` — extend CHECK constraint. |
| `skill_candidate_report.pattern_fingerprint` (TEXT, nullable, indexed) | Hash of normalized user phrase + capability shape for repeat-phrase lookup. | Same migration. |
| `automation.active_cadence_cron` (TEXT, nullable; NULL = paused) | Clamped cadence used for scheduling. NULL when the capability's skill is in `flagged_for_review`. Backfill `active = schedule` for existing rows. | `xxx_automation_active_cadence.py` |
| `automation.schedule` | Existing column; semantic clarified as "target cadence" (no rename — avoids churn). | No schema change. |
| `capability.cadence_policy_override` (JSON, nullable) | Per-capability override of global cadence policy. | Same migration as active_cadence. |

No new tables.

### 4.5 Cadence policy (new config file)

`config/automations.yaml`:

```yaml
cadence_policy:
  claude_native:
    min_interval_seconds: 43200   # 12h  → ≤ 2 runs/day
  sandbox:
    min_interval_seconds: 43200   # 12h  (Claude still primary during sandbox)
  shadow_primary:
    min_interval_seconds: 3600    # 1h   (local free; Claude shadow sampled)
  trusted:
    min_interval_seconds: 900     # 15m  (free; user target governs)
  degraded:
    min_interval_seconds: 43200   # 12h  (demoted → Claude takes over → retighten)
  flagged_for_review:
    pause: true                   # don't run until human review
```

### 4.6 State-transition hook for reclamping

Wave 3 adds `SkillLifecycleService.after_state_change` as a subscriber registration point (or the equivalent if the service already emits events). `CadenceReclamper.reclamp_for_capability(capability_name)` subscribes:

```python
lifecycle_service.after_state_change.register(
    CadenceReclamper(automation_repo, policy, scheduler).reclamp_for_capability
)
```

On transition, the reclamper:
1. Queries `automation` rows with matching `capability_name`.
2. Recomputes `active_cadence_cron` per the new state's policy.
3. Persists the change + reschedules `next_run_at` in the same transaction.
4. Emits structured log: `cadence_reclamped{automation_id, capability, old_state, new_state, old_active_cadence, new_active_cadence, target_cadence}`.
5. On uplift (toward user's target), posts a Discord DM via `NotificationService`: *"I've learned `product_watch` well enough to check hourly now."*

### 4.7 Fold-in architecture

**F-W2-E (`cli.py` refactor).** Extract three helpers from `_run_orchestrator` sharing a `StartupContext` dataclass:

```python
@dataclass
class StartupContext:
    config: AppConfig
    db_pool: Pool
    model_router: ModelRouter
    notification_service: NotificationService
    # … shared handles

def wire_skill_system(ctx: StartupContext) -> SkillSystemHandle: ...
def wire_automation_subsystem(ctx: StartupContext) -> AutomationHandle: ...
def wire_discord(ctx: StartupContext, skill_h, automation_h) -> DiscordHandle: ...
```

`_run_orchestrator` becomes ≤ 100 lines of sequencing. Each wire function is individually testable. Lands before Wave 3's new Discord components so they plug into `wire_discord` cleanly.

**F-W2-C + F-W2-G (`SkillExecutor` wiring test + shadow sampling for `product_watch`).**

- Unit test: `tests/integration/test_skill_executor_default_registry.py` constructs `SkillExecutor(model_router=fake)` without explicit `tool_registry`, asserts `executor._tool_registry is DEFAULT_TOOL_REGISTRY`.
- E2E extension in `tests/e2e/test_wave2_product_watch.py`: seed 20 successful shadow runs via fixture to bypass the counter, promote `product_watch` to `shadow_primary`, run the automation, assert `SkillExecutor` dispatched and `automation_run.skill_run_id` is populated.

**F-W2-D (`on_failure` DSL).** `ToolDispatcher.run_invocation` gains handling for four values:

- `escalate` (existing default) — raise, executor catches, escalates to Claude.
- `continue` — log failure, return `{tool_error: <message>}` to the step's output, continue to next step.
- `fail_step` — raise `StepFailedError`; executor treats the step as terminally failed (skips to end; no escalation).
- `fail_skill` — raise `SkillFailedError`; executor aborts the entire skill run, no escalation.

Schema update on `tools/<name>.yaml` + `skill.yaml` validates the enum. `product_watch`'s behavior (default `escalate`) is unchanged.

**F-10 (min_interval_seconds enforcement).** Already scoped into cadence clamping: `active_cadence_cron` is computed from `max(target_interval_seconds, policy_min_interval_seconds)`. Dispatcher's `_compute_next_run` uses `active_cadence_cron`. The clamping logic *is* the enforcement; no separate work item.

### 4.8 Observability

| Task_type | Provenance | Purpose |
|---|---|---|
| `challenge_task` | Extended — same name, new output schema | Per-message parse cost + latency + confidence signal |
| `claude_novelty` | **New** | Cost attribution for no-match escalations |
| `skill_candidate_eval` | Embedded in `claude_novelty` output (no new task_type) | `skill_candidate: bool` rate trackable from invocation_log |

Existing `invocation_log` aggregations pick up the new task_type automatically.

### 4.9 Failure modes

| Failure | Mitigation |
|---|---|
| Local Ollama down during parse | `ChallengerAgent` returns `escalate_to_claude` with reason; Claude handles parse via `ClaudeNoveltyJudge`. User sees no outage, only increased Claude cost. |
| Claude novelty call times out | Post: *"I couldn't process that — can you try again?"* Log to `invocation_log`. PendingDraft discarded. |
| User abandons mid-clarification | PendingDraft TTL = 30 min. Sweeper runs every 5 min. |
| User hits Approve twice | `AutomationRepository.create()` is idempotent on `(user_id, name)` uniqueness. Second click → no-op reply "Already created." |
| Confirmation card survives bot restart | PendingDraft is in-memory; on restart the card is orphaned. Approve → dispatcher sees no draft, replies *"That draft expired. Ask again?"* |
| State-transition hook fires expensive reclamp across many automations | Reclamp queries by `capability_name` (indexed). Batch size warned if > 50 rows. Single transaction keeps it atomic. |
| User confused why "every 15 min" became "every 12h" | Confirmation card copy explains the uplift path explicitly. State-transition DMs notify on uplift. Dashboard (F-4) will show both values when it exists. |

---

## 5. Deliverables

| # | Deliverable | Size | Depends on |
|---|---|---|---|
| **W3-D1** | Refactor `cli.py` into `wire_skill_system / wire_automation_subsystem / wire_discord` helpers with `StartupContext` dataclass. (F-W2-E) | S | — |
| **W3-D2** | Extend `ChallengerMatchResult` + `match_and_extract` output schema with `intent_kind`, `schedule`, `deadline`, `alert_conditions`, `confidence`, `low_quality_signals`. | S | — |
| **W3-D3** | Rewrite challenger parse prompt as `prompts/challenger_parse.md` (Jinja2). Single LLM call for intent + extraction + quality self-report. Includes "when X happens" → polling heuristic. | M | W3-D2 |
| **W3-D4** | `ClaudeNoveltyJudge` agent + `prompts/claude_novelty.md` + `schemas/claude_novelty.json`. Emits execution-ready extraction + `skill_candidate` verdict + `polling_interval_suggestion`. | M | W3-D2 |
| **W3-D5** | `DiscordIntentDispatcher` — post-challenger routing. Owns thread-reply resumption. | M | W3-D2, W3-D4 |
| **W3-D6** | Promote task-clarification primitive to shared `PendingDraftRegistry`; extend to hold automation partial drafts. 30-min TTL + 5-min sweeper. | S | W3-D5 |
| **W3-D7** | `AutomationCreationPath` — renders confirmation card, handles button interactions, calls `AutomationRepository.create()` on Approve. | M | W3-D5 |
| **W3-D8** | `AutomationConfirmationView` discord.py `View` (Approve / Edit / Cancel + embed rendering). | S | W3-D7 |
| **W3-D9** | Alembic migrations: `skill_candidate_report.status` adds `'claude_native_registered'`; add `pattern_fingerprint` column + index; `automation.active_cadence_cron` + `capability.cadence_policy_override`. | XS | — |
| **W3-D10** | `SkillCandidateDetector` short-circuits on `status='claude_native_registered'` rows matching the same `pattern_fingerprint`. `ClaudeNoveltyJudge` writes the row when `skill_candidate=false`. | S | W3-D4, W3-D9 |
| **W3-D11** | Rewire `DonnaBot.on_message` to call `ChallengerAgent` → `DiscordIntentDispatcher` instead of the current direct task-parse path. | S | W3-D1, W3-D2, W3-D5 |
| **W3-D12** | Unit test: `SkillExecutor(model_router=fake)` without explicit `tool_registry` uses `DEFAULT_TOOL_REGISTRY`. (F-W2-C) | XS | — |
| **W3-D13** | E2E test extension: seed 20 successful shadow runs → promote `product_watch` to `shadow_primary` → assert `SkillExecutor` dispatches in parallel with Claude shadow. (F-W2-G) | M | W3-D12 |
| **W3-D14** | Implement `on_failure` DSL in `ToolDispatcher`: `escalate | continue | fail_step | fail_skill`. Schema validation + per-value unit tests. (F-W2-D) | S | — |
| **W3-D15** | E2E scenario: user DMs *"watch https://cos.com/shirt daily for size L under $100"* → confirmation card → Approve → automation row exists → scheduled run fires → Discord DM on alert. (Closes AS-W3.1, R4–R8 from original spec.) | M | W3-D1–W3-D11 |
| **W3-D16** | E2E scenario: user DMs *"get oil change by Wednesday"* → routed to task path (not automation) → task row created. | S | W3-D1–W3-D11 |
| **W3-D17** | E2E scenario: user DMs *"when I get an email from jane@x.com, message me"* → challenger suggests 15-min polling → user accepts → automation with `capability_name=null` → `skill_candidate_report` pending. | M | W3-D1–W3-D11 |
| **W3-D18** | `config/automations.yaml` + `CadencePolicy` loader + `CadenceReclamper` + `SkillLifecycleService.after_state_change` hook registration. Folds in F-10 enforcement. | M | W3-D9 |
| **W3-D19** | Challenger + `AutomationCreationPath` surface target vs active cadence on the confirmation card with the "I'll speed up when I learn it" rationale. | S | W3-D18, W3-D7 |

**Totals:** 4 XS, 7 S, 8 M. Roughly 6–9 focused days.

### 5.1 Dependency graph

```
W3-D1 (cli refactor) ──────────────────────────────────────► W3-D11 (bot rewire) ──┐
                                                                                    │
W3-D2 (schema) ──► W3-D3 (parse prompt) ──┐                                        │
                   W3-D4 (novelty judge) ─┤                                        │
                                          ├──► W3-D5 (dispatcher) ──► W3-D6 (drafts)│
                                          │                                         │
W3-D9 (migration) ────────────────────────┤      │                                 │
                   │                             ├──► W3-D7 (creation) ──► W3-D8 (view)
                   └──► W3-D10 (detector short-circuit)                       │
                                                                              │
W3-D18 (cadence policy) ──► W3-D19 (card surfacing cadence, also needs W3-D7) ┤
                                                                              │
                                                                              ▼
                                                                    W3-D15 / D16 / D17 (E2E)

W3-D12 ──► W3-D13 (F-W2-C/G)   [independent track]
W3-D14 (on_failure DSL)         [independent track]
```

### 5.2 Suggested execution order

**Day 1 — Foundations (parallelizable).**
- W3-D1 (cli refactor) — land first; cleaner wiring for everything else.
- W3-D2 (schema extensions) — pure types/dataclasses.
- W3-D9 (migrations).
- W3-D14 (on_failure DSL).

**Day 2–3 — LLM integration.**
- W3-D3 (challenger parse prompt) with fixture-driven eval.
- W3-D4 (Claude novelty judge).
- W3-D12 (SkillExecutor registry test).
- W3-D18 (cadence policy + reclamper).

**Day 4–5 — Dispatcher + UI.**
- W3-D5 (intent dispatcher).
- W3-D6 (pending draft registry extraction).
- W3-D7 + W3-D8 (creation path + confirmation view).
- W3-D10 (detector short-circuit).
- W3-D19 (cadence surfacing on card).

**Day 6 — Wire-up.**
- W3-D11 (bot rewire).
- W3-D13 (shadow sampling E2E).

**Day 7–8 — Acceptance scenarios.**
- W3-D15 / W3-D16 / W3-D17 — full E2E.

### 5.3 Subagent parallelism opportunities

Per `superpowers:dispatching-parallel-agents`:

- **Group A (foundations):** W3-D1, W3-D2, W3-D9, W3-D14 — no shared files.
- **Group B (LLM work):** W3-D3, W3-D4, W3-D12 — independent once W3-D2 lands.
- **Group C (dispatcher + UI):** W3-D5→D8 — single sequence; not parallel among themselves.
- **Group D (E2E):** W3-D15/D16/D17 — parallel authoring against the same harness.

---

## 6. Acceptance Scenarios

**AS-W3.1 — High-confidence automation via NL (closes original-spec AS-5.1, R4–R8).**
User DMs: *"watch https://cos.com/shirt daily for size L under $100."*
- `ChallengerAgent` returns `status=ready, intent_kind=automation, capability=product_watch, match_score=0.9, schedule={cron="0 12 * * *", human_readable="daily at 12:00"}, inputs={url, required_size=L, max_price_usd=100}, missing_fields=[], confidence=0.92`.
- Dispatcher → `AutomationCreationPath` → confirmation card (with target == active since `product_watch` is `sandbox` and cadence is already within policy).
- User clicks **Approve** → `AutomationRepository.create(...)` writes row with `created_via='discord'`.
- Next scheduler tick dispatches the run. When skill is in sandbox, runs via `claude_native`; when promoted to `shadow_primary`, runs via `SkillExecutor` with Claude shadow.
- Product out of size or under price → `NotificationService` DMs alert.

**AS-W3.2 — Task routing (not automation).**
User DMs: *"get oil change by Wednesday."*
- Challenger: `status=ready, intent_kind=task, capability=parse_task, deadline=<Wednesday>, schedule=None`.
- Dispatcher → `TaskCreationPath` (existing). No confirmation card.
- Task row exists with deadline populated.

**AS-W3.3 — Clarification round-trip via thread.**
User DMs: *"watch the Patagonia jacket."*
- Challenger: `status=needs_input, intent_kind=automation, capability=product_watch, missing_fields=[url, max_price_usd, required_size], clarifying_question="Got it — which URL, what size, and what's the max price before you want me to ping you?"`
- Dispatcher posts clarifying question; `PendingDraft` persisted, `thread_id` recorded.
- User replies in thread with missing info.
- Bot resumes parse with merged context → `status=ready` → confirmation card → Approve → automation created.

**AS-W3.4 — "When X" → polling.**
User DMs: *"when I get an email from jane@x.com that needs a reply, message me."*
- No capability match (`email_triage` doesn't exist). `status=escalate_to_claude`.
- `ClaudeNoveltyJudge`: `intent_kind=automation, trigger_type=on_schedule, polling_interval_suggestion="0 */12 * * *", capability_name=null, skill_candidate=true, skill_candidate_reasoning="email triage is reusable"`.
- Challenger posts: *"I can check twice a day and DM you about anything from jane@x.com that looks action-required. Sound right?"*
- User confirms.
- Automation row created with `capability_name=null`, `target_cadence_cron="0 */12 * * *"`, `active_cadence_cron="0 */12 * * *"` (already at `claude_native` policy floor), `alert_channels=[discord_dm]`.
- `skill_candidate_report` row inserted (status=`new`, pattern_fingerprint=hash) — nightly detector surfaces it.

**AS-W3.5 — Cancel / Edit card interactions.**
- **Cancel** → `PendingDraft` discarded; bot replies *"Nothing created."*
- **Edit** → bot re-opens the thread: *"What do you want to change?"*; user's next reply merges into the draft; new card posted.

**AS-W3.6 — Duplicate-approval idempotency.**
User double-taps **Approve**. First click creates row. Second click → `AlreadyExistsError` on `(user_id, name)`; bot posts *"Already created."*

**AS-W3.7 — Personal errand (matches parse_task, no novelty call).**
User DMs: *"pick up my medication by 11:00am tomorrow. This is high priority."*
- Challenger: `status=ready, intent_kind=task, capability=parse_task, deadline=<tomorrow 11am>, priority=high`.
- Task flow handles it. No automation, no novelty call, no skill candidate row.

**AS-W3.8 — No-match automation, Claude marks non-candidate.**
User DMs: *"every Sunday at 10am, review my tax prep folder and summarize what's missing."*
- No capability matches (no `doc_folder_review`, no generic `weekly_summary`). `status=escalate_to_claude`.
- `ClaudeNoveltyJudge`: `intent_kind=automation, trigger_type=on_schedule, schedule="0 10 * * 0", inputs={folder_path, summary_style}, skill_candidate=false, skill_candidate_reasoning="Annual tax workflow — user-specific, low-frequency, not generalizable enough to justify a skill."`
- Automation created with `capability_name=null`, `target_cadence_cron="0 10 * * 0"`, `active_cadence_cron="0 10 * * 0"` (weekly > 12h floor, no clamp).
- `skill_candidate_report` row with `status=claude_native_registered` + `pattern_fingerprint`. Future "every Sunday review tax prep" phrasings short-circuit the detector.
- (Contrast with AS-W3.4 where `skill_candidate=true` — the difference is Claude's judgment on reusability.)

**AS-W3.9 — Shadow sampling fires for product_watch (closes F-W2-G).**
Seed `product_watch` promoted to `shadow_primary` via test harness.
- AS-W3.1 run: `AutomationDispatcher` routes to `SkillExecutor` (not claude_native).
- Claude runs in shadow in parallel. `skill_divergence` row recorded.
- `automation_run.skill_run_id` populated.

**AS-W3.10 — `on_failure: continue` (closes F-W2-D partial).**
Hand-authored test skill with step 1 marked `on_failure: continue`; step 2 depends on step 1's output.
- Step 1's tool raises `UnmockedToolError`.
- Step output captured as `{tool_error: "UnmockedToolError: ..."}`; step 2 proceeds.
- Skill run completes without escalation.

**AS-W3.11 — Cadence clamp + auto-uplift.**
User DMs: *"watch this URL every 15 minutes for size L under $100"* (capability `product_watch`, current state `sandbox`).
- Challenger extracts `target_interval=15min`. Policy clamps to `min_interval=12h`. Confirmation card surfaces both:
  - ✓ Watch Patagonia jacket — every 15 min (your target)
  - ⚠ Running every 12 hours for now — I'm using Claude to check until I learn this task
  - ↑ I'll speed up automatically: hourly once I'm shadowing, every 15 min once trusted
- User approves. `target_cadence_cron="*/15 * * * *"`, `active_cadence_cron="0 */12 * * *"`.
- Over time: 20 shadow runs → `shadow_primary`. `CadenceReclamper` fires: `active_cadence_cron="0 * * * *"` (hourly). User gets DM: *"I've learned product_watch well enough to check hourly now."*
- Further promotion → `trusted` → `active_cadence_cron="*/15 * * * *"`. User's original intent reached.

**AS-W3.12 — Flagged-for-review pauses scheduling.**
Skill transitions to `flagged_for_review`. `CadenceReclamper` sets `active_cadence_cron=NULL` for affected automations; scheduler skips NULL-cadence rows. User DM: *"I've paused X automations for `<capability>` pending review."* When the skill returns to `trusted`/`shadow_primary` (via manual review), the reclamper restores `active_cadence_cron` from `target_cadence_cron` + policy.

---

## 7. Requirements Matrix

| # | Requirement | Section | Scenario | Status |
|---|---|---|---|---|
| W3-R1 | `ChallengerMatchResult` output schema includes `intent_kind`, `schedule`, `deadline`, `alert_conditions`, `confidence`, `low_quality_signals`. | §4.2 | AS-W3.1, AS-W3.2 | [ ] |
| W3-R2 | Parser prompt unifies intent classification + extraction in one local-LLM call. | §4.2 | AS-W3.1 | [ ] |
| W3-R3 | When `status=needs_input`, bot posts clarifying question to a thread; reply resumes the draft. | §4.2 | AS-W3.3 | [ ] |
| W3-R4 | `ClaudeNoveltyJudge` outputs `{trigger_type, inputs, schedule, alert_conditions, skill_candidate, polling_interval_suggestion}`. | §4.3 | AS-W3.4, AS-W3.8 | [ ] |
| W3-R5 | "When X" phrasings route to `on_schedule` with inferred polling interval. | §4.2 | AS-W3.4 | [ ] |
| W3-R6 | Automation creation requires explicit user approval via confirmation card. | §4.2 | AS-W3.1, AS-W3.5 | [ ] |
| W3-R7 | `PendingDraftRegistry` holds task OR automation drafts; 30-min TTL; thread_id keyed. | §4.3 | AS-W3.3 | [ ] |
| W3-R8 | `AutomationRepository.create` enforces `(user_id, name)` idempotency. | §4.9 | AS-W3.6 | [ ] |
| W3-R9 | `skill_candidate_report.status='claude_native_registered'` persists Claude's non-candidate verdict; detector short-circuits on `pattern_fingerprint` match. | §4.4 | AS-W3.8 | [ ] |
| W3-R10 | `cli.py` refactored into `StartupContext` + three wire helpers; `_run_orchestrator` ≤ 100 lines. (F-W2-E) | §4.7 | Structural | [ ] |
| W3-R11 | `SkillExecutor(model_router=fake)` without `tool_registry` uses `DEFAULT_TOOL_REGISTRY`. (F-W2-C) | §4.7 | Structural | [ ] |
| W3-R12 | `product_watch` promoted to `shadow_primary` in E2E fires skill path (not claude_native); `automation_run.skill_run_id` populated. (F-W2-G) | §4.7 | AS-W3.9 | [ ] |
| W3-R13 | `on_failure` DSL supports `escalate | continue | fail_step | fail_skill` with schema validation + per-value unit tests. (F-W2-D) | §4.7 | AS-W3.10 | [ ] |
| W3-R14 | Original-spec R4: challenger matches user intent against registry with confidence scoring. | — | AS-W3.1 | [ ] |
| W3-R15 | Original-spec R5: challenger extracts inputs against matched capability's input schema. | — | AS-W3.1 | [ ] |
| W3-R16 | Original-spec R6: challenger asks clarifying questions via Discord thread. | — | AS-W3.3 | [ ] |
| W3-R17 | Original-spec R7: challenger escalates low-confidence matches to Claude. | — | AS-W3.4 | [ ] |
| W3-R18 | Original-spec R8: Claude novelty returns `match_existing | create_new` with full registry context. | — | AS-W3.4, AS-W3.8 | [ ] |
| W3-R19 | All NL-created automations log `created_via='discord'`. | §4.4 | AS-W3.1 | [ ] |
| W3-R20 | E2E: NL "watch X daily" → automation → scheduled run → Discord alert DM. | §4.2 | AS-W3.1, AS-W3.9 | [ ] |
| W3-R21 | `active_cadence_cron` computed from `target_cadence_cron` + lifecycle policy; dispatcher schedules from active. | §4.5 | AS-W3.11 | [ ] |
| W3-R22 | Confirmation card shows target vs active cadence when they differ + auto-uplift rationale. | §4.6 | AS-W3.11 | [ ] |
| W3-R23 | `SkillLifecycleService` state transitions reclamp all affected automations atomically. | §4.6 | AS-W3.11 | [ ] |
| W3-R24 | `flagged_for_review` lifecycle state pauses automation scheduling. | §4.5 | AS-W3.12 | [ ] |
| W3-R25 | F-10: `min_interval_seconds` enforced at dispatch time via active cadence. | §4.7 | AS-W3.11 | [ ] |

---

## 8. Risk Register

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Local Ollama parse hallucinates field values (wrong URL, wrong price). | Med | Med | Fixture-driven prompt eval (≥ 15 fixtures covering task/automation/ambiguous) before merge. Confidence < 0.7 routes to clarification. Confirmation card surfaces every field. |
| Confusing clarification threads (multiple pending drafts, user replies to wrong one). | Low | Low | Single active draft per user at a time; second new-parse while one is pending replies *"I'm still waiting on details for your last request — cancel or continue?"* |
| `on_failure: continue` lets a broken skill produce garbage output that still validates. | Med | Med | Schema validation on final skill output is already enforced by `ValidationExecutor`. `continue`'s `{tool_error}` injection is visible to downstream steps; W3-D14 tests assert the flag is present. |
| Shadow-sampling E2E flakiness (timing-sensitive promotion to shadow_primary). | Med | Low | W3-D13 uses deterministic fixture seeding to bypass the 20-run counter, not real scheduling. |
| F-W2-E refactor regresses orchestrator startup. | Low | High | `test_orchestrator_startup_full_wire.py` integration test runs the full lifespan against a throwaway DB before and after the refactor — byte-identical startup log sequence required. |
| PendingDraft TTL too short (user takes > 30 min to reply). | Low | Low | Log drop events to `invocation_log`; if observed, bump to 2 hours. Start at 30 min for predictability. |
| State-transition hook fires expensive reclamp across many automations. | Low | Med | Reclamp queries by `capability_name` (indexed). Batch > 50 rows emits warning. Single transaction keeps it atomic. |
| User confused why "every 15 min" became "every 12h". | Med | Low | Confirmation card copy explicitly explains uplift path. State-transition DMs notify on uplift. |
| Claude novelty judge hallucinates `skill_candidate=false` on a genuinely reusable pattern. | Med | Low | Not irreversible — `claude_native_registered` rows can be flipped to `new` manually; future detector scans can reconsider if same pattern appears N times. |

---

## 9. Open Questions Deliberately Deferred

- **Cost estimate on confirmation card.** Premature without F-12 (Grafana cost attribution). Revisit after F-12 lands.
- **Default alert channel per user.** Single-user means DM is the only destination; revisit at multi-user.
- **Auto-modify existing automations from new NL parse.** E.g., *"bump the jacket check to hourly"* — matching "the jacket check" to an existing `automation_id` is its own inference problem. Defer to F-4 dashboard + OOS-W3-8.
- **Re-evaluation of `claude_native_registered` patterns.** Should a pattern seen N more times flip back to `new`? Deferred — trust Claude's initial verdict for v1; revisit if false-negatives are observed.
- **Multi-turn clarification over more than one round.** Wave 3 supports *one* clarification round per draft (missing_fields → single question → merged parse). More rounds are feasible with the same primitive but add failure modes; defer until real usage demands it.

---

## 10. Predecessor Spec Touchpoints

This section enumerates the requirements in the original 2026-04-15 spec that Wave 3 closes or advances, so the requirements matrix in that spec can be updated when Wave 3 merges.

| Original req | Status after Wave 3 |
|---|---|
| R4 — Challenger matches intent against registry with confidence scoring | Delivered (W3-R14 / AS-W3.1) |
| R5 — Challenger extracts inputs against capability schema | Delivered (W3-R15 / AS-W3.1) |
| R6 — Challenger asks clarifying questions via Discord thread | Delivered (W3-R16 / AS-W3.3) |
| R7 — Challenger escalates low-confidence to Claude | Delivered (W3-R17 / AS-W3.4) |
| R8 — Claude novelty returns match_existing/create_new with full registry context | Delivered (W3-R18 / AS-W3.4, AS-W3.8) |
| AS-1.2 (original) — NL "monitor URL daily" | Delivered (AS-W3.1) |
| AS-1.4 (original) — Clarifying question via thread | Delivered (AS-W3.3) |
| AS-5.1 (original) — NL automation creation end-to-end | Delivered (AS-W3.1, AS-W3.9) |

---

## 11. Out-of-Wave Followups Surfaced by Wave 3

Wave 3 deliberately defers these — they'll land in the `docs/superpowers/followups/2026-04-16-skill-system-followups.md` doc on merge:

- **F-W3-A** — Fixtures for challenger parse prompt: capture production `invocation_log` rows with `task_type=challenge_task` into a versioned fixture set. Supports future Approach 3 migration (OOS-W3-4).
- **F-W3-B** — Per-user `default_alert_channel` preference (vs. always Discord DM). Wait for multi-user.
- **F-W3-C** — Multi-round clarification (> 1 back-and-forth). Extend `PendingDraftRegistry` state machine when observed.
- **F-W3-D** — Auto-modify existing automations from NL ("bump the jacket check to hourly").
- **F-W3-E** — Cost estimate on confirmation card (blocked on F-12).
- **F-W3-F** — `claude_native_registered` re-evaluation policy (flip back to `new` after N repeats).
- **F-W3-G** — Persistent `PendingDraftRegistry` (DB-backed, survives orchestrator restart). Only if in-memory state causes UX complaints.

---

## 12. Glossary (delta from Wave 2)

- **Confirmation card** — discord.py `View` with Approve/Edit/Cancel; embed of extracted fields; required before any automation write.
- **Target cadence** — user's stated polling cadence (e.g., "every 15 min" → `"*/15 * * * *"`).
- **Active cadence** — policy-clamped cadence actually used for scheduling. Auto-reclamped on skill state transition.
- **Cadence policy** — table mapping skill lifecycle state → minimum polling interval (claude_native: 12h; sandbox: 12h; shadow_primary: 1h; trusted: 15m; degraded: 12h; flagged_for_review: paused).
- **Claude-native registered** — `skill_candidate_report.status` value indicating Claude judged a pattern as non-reusable; short-circuits the nightly detector.
- **Pattern fingerprint** — hash over normalized user phrase + capability shape; used to dedupe repeat phrasings in the skill-candidate report.
- **Pending draft** — in-memory partial task/automation extraction awaiting clarification replies; 30-min TTL; thread_id keyed.
- **Unified parse** — single local-Ollama call returning intent + capability match + inputs + schedule/deadline/alert_conditions + quality self-report in one shot.
