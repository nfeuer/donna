# Skill System Follow-Ups

**Status:** Inventory. Each entry is a candidate future spec + plan.
**Scope:** Every gap flagged in Phase 3-5 drift logs plus every OOS-N deferral from the original spec §2.
**Date:** 2026-04-16

This is a backlog, not a roadmap. Priority suggestions are opinions — use them as a starting point for triage, not an execution order.

---

## Completed — Wave 2 (2026-04-17)

- **F-W1-B** EvolutionGates now thread `tool_mocks` through all three gates. New `mock_synthesis.py` helper shared between runtime and migration. See `docs/superpowers/specs/2026-04-17-skill-system-wave-2-first-capability-design.md`.
- **F-W1-C** Router kwargs mismatch resolved; executor + triage drop unsupported kwargs. `FakeRouter` in the E2E harness tightened to match `ModelRouter.complete` signature (catches future drift).
- **F-W1-D** Draft-now via `skill_candidate_report.manual_draft_at` + `ManualDraftPoller` (15s poll). API endpoint returns 202.
- **F-W1-E** Validation-mode per-step timeout wired (`validation_per_step_timeout_s`; fires only when `run_sink` + `config` are both set).
- **F-W1-F** `POST /admin/skill-runs/{id}/capture-fixture` endpoint using `json_to_schema` + `cache_to_mocks`.
- **F-W1-G** Validation LLM calls tagged with `skill_validation::<cap>::<step>` prefix via `ValidationExecutor`'s `task_type_prefix`.
- **F-W1-H** Automation subsystem runs independent of `skill_system.enabled`.
- **F-2** `automation_run.skill_run_id` ↔ `skill_run.automation_run_id` populated both directions via `SkillRunResult.run_id` + dispatcher plumbing.
- **F-7** `CorrectionClusterDetector.scan_for_capability` fires synchronously from `log_correction` write path.
- **F-11** `product_watch` capability + skill + 4 fixtures with `tool_mocks` seeded via Alembic migration. `SeedCapabilityLoader` syncs `config/capabilities.yaml` on orchestrator startup. `web_fetch` registered on module-level default registry.
- **Task 0 (Wave 2 prerequisite)** `ModelRouter._resolve_route` gained longest-prefix match fallback so dynamic `skill_step::*` and `skill_validation::*` task_types resolve without per-entry config.

Wave 3 plan: **F-3** Discord natural-language automation creation (OOS-W2-1 from Wave 2 spec).

## Completed — Wave 1 (2026-04-16)

- **F-1** Sandbox SkillExecutor → shipped as `ValidationExecutor`. See `docs/superpowers/specs/2026-04-16-skill-system-wave-1-production-enablement-design.md`.
- **F-5** Wire ValidationExecutor into lifespan.
- **F-6** NotificationService wired; automation scheduler moved to orchestrator process.
- **F-14** End-to-end "enabled" smoke test.

New follow-ups surfaced during Wave 1 implementation and final code review:

- **F-W1-A — `DegradationDetector` threshold semantics.** *(Priority P2; not a ship-blocker, but produces silent gaps.)* The detector uses `degradation_agreement_threshold=0.5` as a binary success/failure classifier on each divergence, then computes a Wilson CI on the success count. Divergences with agreement between the threshold and the baseline (e.g., 0.65 when baseline is 0.90) all count as successes and never trigger degradation. A trusted skill that silently drifts to mid-confidence agreement will never flag, so no degradation notification fires. Current mitigation: `correction_cluster` path if the user issues corrections; EOD/nightly digest. Fix options: graded/continuous agreement in the CI, or lower the default threshold. E2E scenario 4 (`test_trusted_degrades_to_flagged`) seeds 0.30 agreement to work around the bug.

- **F-W1-B — `EvolutionGates` does not thread `tool_mocks` through validation.** *(Priority P0 for Wave 2 — must land before F-11 seeds any tool-using skill.)* `src/donna/skills/evolution_gates.py` runs all three gates (targeted-case, fixture-regression, recent-success) against `self._executor.execute(...)` without passing the fixture's `tool_mocks` blob. `ValidationExecutor.execute` defaults `tool_mocks=None`, `MockToolRegistry.from_mocks(None)` produces an empty map, and the first tool step raises `UnmockedToolError` — which the gate catches and marks as a failure. Any skill with even one tool step (i.e., every Wave 2 capability: `product_watch`, `news_check`, `meeting_prep`) will permanently fail Gate 3 and never successfully evolve. Impact today is zero because no such skills exist. Fix: (1) for the fixture-regression gate, JOIN `skill_fixture.tool_mocks` into the query and `json.loads` it per fixture; (2) for the targeted-case and recent-success gates, synthesize mocks from `skill_run.tool_result_cache` on the captured run (the same transform the Alembic backfill performs in `alembic/versions/add_fixture_tool_mocks.py::_cache_to_mocks`). Unit tests in `test_skills_evolution_gates.py` use `MagicMock()` executors and miss this entirely — add a gate-level test using a real `ValidationExecutor` + `MockToolRegistry` populated from a seeded fixture row.

- **F-W1-C — `SkillExecutor._run_llm_step` passes unsupported kwargs to `ModelRouter.complete`.** *(Priority P0 for Wave 2 — must land before real-LLM validation runs in production.)* `executor.py` lines 452-458 and `triage.py` lines 78-84 call `self._router.complete(..., schema=..., model_alias=...)`. `ModelRouter.complete` does not accept those kwargs. The harness and all unit tests use fake routers with `**kwargs`, so the mismatch is undetectable by the current test suite. The first real-LLM invocation (including any `ValidationExecutor` run in production) will raise `TypeError: complete() got an unexpected keyword argument`. This is pre-existing Phase 2 debt, but Wave 1 is the first code path to hit it for real — AS-W1.1 and AS-W1.2 will fail under real conditions until this is fixed. Fix: update the call sites to pass the schema and alias through the actual `ModelRouter` interface (likely via `task_type` routing + a separate method, or by adding the kwargs to `ModelRouter.complete`).

- **F-W1-D — `POST /admin/skill-candidates/{id}/draft-now` has no HTTP trigger path after F-6.** *(Priority P1.)* The endpoint now returns `501 Not Implemented` with a pointer to this follow-up. The auto-drafter runs in the orchestrator process, but there is no API→orchestrator IPC for forcing a draft now (unlike automations, which use `next_run_at=now()`). Nick has two workarounds today: (a) wait for the 3 AM UTC nightly cron, (b) manually invoke via the orchestrator. A clean fix would mirror the automation pattern: add a `manual_draft_trigger_at` column to `skill_candidate_report`, have the API set it, and have the orchestrator poll. Smaller alternative: keep the endpoint `501` and document `donna draft --candidate-id <id>` as the supported flow (new CLI subcommand).

- **F-W1-E — `validation_per_step_timeout_s` is configured but unused.** *(Priority P3.)* Defined in `SkillSystemConfig` and asserted by `test_config_validation_timeouts.py`, but never consumed. Only the per-run timeout is wired (inside `ValidationExecutor.execute`). Either wire per-step in `SkillExecutor._run_llm_step` (wrap the `self._router.complete(...)` call with `asyncio.wait_for`), or delete the field. Currently misleading: operators who tune it will see no effect.

- **F-W1-F — `donna.skills.schema_inference.json_to_schema` is defined but never called.** *(Priority P3.)* Spec §5.2 says captured-run fixtures should use this helper for `expected_output_shape` inference. No production code imports it. Either wire it into a fixture-capture path (may also be dead — check `skill_fixture.source='captured_from_run'` writers) or remove the module.

- **F-W1-G — `FakeRouter`'s `skill_validation::*` prefix is dead.** *(Priority P3.)* `tests/e2e/harness.py:68` routes `skill_validation::*` task types to the fake Ollama, but production emits `skill_step::*` from `SkillExecutor._run_llm_step`. Spec §6.1 promises `skill_validation::<capability>::<step>` as the tagging convention for validation runs — this isn't implemented. Either (a) implement the convention in `ValidationExecutor` / `SkillExecutor` when `run_sink` is active, or (b) remove the dead prefix from the harness and update the spec.

- **F-W1-H — Automation subsystem is gated on `skill_config.enabled`.** *(Priority P2.)* `src/donna/cli.py` lines 321-423 wire automations only when `skill_config.enabled=True`. Automation is a Phase 5 subsystem that should be independent of the skill-system toggle. If an operator disables the skill system during an incident, they silently lose automation alerts too. Pre-existing on main (same coupling was in the API), so not a Wave 1 regression — decouple before the next automation-focused wave.

---

## Legend

- **Priority P0 — Ship-blocker.** Something promised-but-stubbed, silent footgun, or production-correctness risk. Do before enabling `skill_system.enabled=true` in production.
- **Priority P1 — Next wave.** Unlocks meaningful value or closes a drift-log gap. Do after P0.
- **Priority P2 — When triggered.** Deferred deliberately with a named trigger condition (most OOS items). Don't build speculatively.
- **Priority P3 — Exploratory.** Nice-to-have; no clear pain signal yet.

---

## Drift-log gaps (Phases 3-5)

### F-1: Sandbox SkillExecutor for validation gates

- **Origin:** Phase 3 Task 9 drift entry (AutoDrafter), Phase 4 Task 11 drift entry (Evolver + `assemble_skill_system` default).
- **Current state:** Both `AutoDrafter` and `Evolver` accept `executor_factory=None`, the lifespan wiring passes None, gates 2-4 return `pass_rate=1.0` (vacuous pass). Drafted / evolved skills still land in `draft` requiring human approval — so the safety posture holds, but validation is a stub.
- **What it unblocks:** Fully automated draft→sandbox promotion. Meaningful evolution 4-gate validation. Closes `R28` partial status.
- **Scope estimate:** Medium-large. Requires design decisions: process isolation (subprocess? container? sandbox threading?), tool mocking strategy (deny-all? allow-read? per-fixture allow-list?), timeouts, output capture. New module `src/donna/skills/sandbox_executor.py`.
- **Risk:** Any real executor brings network + DB side-effect surface. Getting the isolation wrong lets generated skills touch prod data.
- **Priority:** **P0**. Everything downstream of "skills evolve themselves" depends on this being real.

---

### F-2: `automation_run.skill_run_id` linkage

- **Origin:** Phase 5 final code review (Important).
- **Current state:** When an automation dispatches via the skill path, `automation_run.skill_run_id` is always `None`. The column exists for this linkage; the dispatcher just doesn't populate it because `SkillExecutor.execute()` doesn't return the persisted `run_id`.
- **What it unblocks:** Dashboard traceability (click through from an automation run to the underlying skill run). Attribution of automation costs to specific skill versions.
- **Scope estimate:** Small. Either (a) add `run_id` to `SkillRunResult`, or (b) pass `automation_run_id` into `executor.execute(...)` — there's already an unused `automation_run_id` parameter stub. Option (b) also writes the linkage back into `skill_run.automation_run_id`, which gives both directions.
- **Priority:** **P1**. Low effort, high clarity gain for debugging.

---

### F-3: Discord natural-language automation creation

- **Origin:** Phase 5 drift log (AS-5.1 partial).
- **Current state:** REST endpoint `POST /admin/automations` exists. The Discord creation flow ("watch this URL daily for size L under $100") requires the challenger to output `trigger_type=on_schedule` alongside extracted inputs, then post to the endpoint.
- **What it unblocks:** AS-5.1 as spec'd (Discord-driven creation). Currently automations can only be created via dashboard.
- **Scope estimate:** Medium. Changes needed:
  - Challenger prompt + output schema to add `trigger_type` + `schedule` + `alert_conditions` fields.
  - Discord chat adapter: detect "watch / monitor / daily / weekly" intent, route to automation creation path instead of task creation.
  - Clarifying questions for missing schedule/alert fields.
- **Priority:** **P1**. The spec flagged this as the motivating example; without it, the automation subsystem is dashboard-only and therefore dormant for Nick's actual usage.

---

### F-4: Dashboard UI for skill system + automations

- **Origin:** Phase 3, 4, 5 all shipped JSON routes only.
- **Current state:** All data is queryable via `/admin/*` endpoints. No rendered views.
- **What it unblocks:** AS-3.3 (user approves a draft), AS-4.2 (user clicks "save reset baseline"), AS-4.3 (user approves evolution), `requires_human_gate` toggle, automation CRUD, run history browsing. Currently these paths are testable but not user-operable.
- **Scope estimate:** Large. Separate track — frontend work has its own design cycle.
- **Priority:** **P1**. The whole "human retains judgment-level control" story collapses without this. But it's the biggest effort item on the list, and it's genuinely a separate project — needs its own brainstorm.

---

### F-5: Real sandbox executor wired into lifespan

- **Origin:** Follow-up to F-1. Once the sandbox executor exists, `assemble_skill_system` needs to pass it as `executor_factory=...` instead of `lambda: None`.
- **Current state:** Line `executor_factory=None` in `src/donna/skills/startup_wiring.py`.
- **Scope estimate:** Trivial if F-1 has the right interface. Just a single wire-up line + an E2E regression test.
- **Priority:** **P0**, immediately after F-1 lands.

---

### F-6: NotificationService wired into FastAPI lifespan

- **Origin:** Phase 5 drift log.
- **Current state:** `app.state.notification_service` is never populated in `src/donna/api/__init__.py`. `AutomationDispatcher` defensively checks `self._notifier is not None` and skips notification when absent — runs succeed but no alerts go out.
- **What it unblocks:** AS-5.4 in production (alert conditions fire → Discord DM). Currently alerts only fire in tests where the fixture explicitly injects the notifier.
- **Scope estimate:** Small. Construct a `NotificationService` in the lifespan, attach to `app.state.notification_service`, confirm the rest of the codebase doesn't try to instantiate a second one.
- **Priority:** **P0**. Without this, the automation subsystem is mechanically correct but operationally silent. Low effort.

---

### F-7: `CorrectionClusterDetector` frequency — hourly or on-correction

- **Origin:** Phase 4 Task 7 notes + Phase 5 nightly cron integration.
- **Current state:** `CorrectionClusterDetector.scan_once()` runs once per nightly cron (3am UTC). Spec §6.6 AS-4.5 says "fires immediately with a higher-urgency notification (not EOD digest)."
- **What it unblocks:** Real "immediate" signal from user corrections. Currently a user issuing 3 corrections at 9am won't see the skill flagged until 3am the next day.
- **Scope estimate:** Small-medium. Either (a) add a separate hourly scheduler, (b) wire it into a correction-log write hook so it fires after each user correction, or (c) both. Option (b) is the "fast path" the spec intended.
- **Priority:** **P1**. Not a correctness issue — the nightly scan still catches the cluster — but the UX is wrong.

---

### F-8: Evolution transition to `sandbox` requires human approval

- **Origin:** Phase 4 drift log.
- **Current state:** When `Evolver` produces a valid new version, it transitions `degraded → draft` (system actor, `reason=gate_passed` — legal), then attempts `draft → sandbox` which fails `IllegalTransitionError` because the transition table requires `reason=human_approval` and the system actor can't supply that. So evolved skills rest in `draft`. This matches the spec's safety posture but is never surfaced clearly to the user.
- **What it unblocks:** A clear "approve evolution" action in the dashboard that bumps the skill to sandbox. (The REST route `POST /admin/skills/{id}/state` already handles this — it just needs UI, F-4.)
- **Scope estimate:** Small. Only needed if F-4 is deprioritized: add a CLI or admin-only endpoint for "approve evolved skill". Otherwise F-4 solves this naturally.
- **Priority:** **P2**. Subsumed by F-4 in practice.

---

### F-9: Baseline reset window configurable

- **Origin:** Phase 4 Task 8.
- **Current state:** `POST /admin/skills/{id}/state` with `to_state=trusted, reason=human_approval` recomputes `baseline_agreement` from the last 100 divergence rows. The 100 is hardcoded.
- **What it unblocks:** Tuning baseline window without code changes.
- **Scope estimate:** Trivial. Read `config.shadow_primary_promotion_min_runs` (already exists) as the window size.
- **Priority:** **P3**. Works fine as-is. Small polish.

---

### F-10: `min_interval_seconds` enforcement

- **Origin:** Phase 5 drift log (R31 partial-semantics note).
- **Current state:** The `automation.min_interval_seconds` column is persisted but not enforced at dispatch time. The scheduler trusts the cron expression. If a user creates an automation with `*/30 * * * * *` (every 30s) and `min_interval_seconds=300`, the scheduler will still fire every 30s.
- **What it unblocks:** Genuine rate-limit floor the spec described.
- **Scope estimate:** Small. In `AutomationDispatcher._compute_next_run`, clamp `next_run_at` to `max(next_run_at, last_run_at + timedelta(seconds=min_interval_seconds))`. Or reject at creation/edit time in the API route.
- **Priority:** **P2**. Currently there's no creation path that produces pathological cron expressions (dashboard doesn't exist, Discord flow doesn't exist). When F-3 or F-4 lands, revisit.

---

### F-11: Seed useful capabilities + skills for real usage

- **Origin:** Implicit. Phase 1 seeded `parse_task`, `dedup_check`, `classify_priority` (three existing task types). Phase 5 delivered the automation subsystem with nothing to automate.
- **Current state:** No capabilities exist for the motivating examples (`product_watch`, `news_check`, `meeting_prep`). An empty capability registry means the challenger's match-and-route layer is permanently in "novelty" mode.
- **What it unblocks:** Real user flows. AS-5.1 refers to `product_watch` as if it already existed — it doesn't.
- **Scope estimate:** Small-medium per capability. Define the capability row + input schema, hand-write an initial skill YAML + step prompts + schemas + 3-5 fixtures per capability. First capabilities should be ones Nick actually wants to use — that's a user input, not a design decision.
- **Priority:** **P1**. Without this, nothing upstream matters — the whole pipeline has nothing to chew on.

---

### F-12: Observability dashboards

- **Origin:** Implicit. Every Phase 3-5 component logs structured events but there's no aggregation or alerting on top.
- **Current state:** Events are logged to `invocation_log` and structlog. Grafana/Loki exists in the infra stack but no skill-specific dashboards.
- **What it unblocks:** Operational visibility. When a promotion gate is stuck or evolution is failing repeatedly across many skills, the user would otherwise only notice via EOD digest.
- **Scope estimate:** Small-medium. Add Grafana panels for: skill state distribution over time, daily nightly-cron outcomes, automation success/failure rates, evolution success rate per skill, cost breakdown by skill-system task type.
- **Priority:** **P2**. Not blocking; EOD digest covers the basics. Do when the first production incident reveals a gap.

---

### F-13: Migrate existing Claude-native task types to capabilities

- **Origin:** Spec Open Questions #5 ("Migration strategy for existing task types").
- **Current state:** `parse_task`, `dedup_check`, `classify_priority` are seeded. The spec open-question lists `generate_digest` as a likely next candidate; `prep_research`, `task_decompose`, `extract_preferences` are also in `config/task_types.yaml` and currently run straight through Claude.
- **What it unblocks:** `SkillCandidateDetector` automatically surfaces these once they have capability rows. Opens them to drafting + evolution + shadow.
- **Scope estimate:** Small per task type. Write a migration that inserts the capability row for each, seed embeddings, confirm the task-type→capability-name linkage works.
- **Priority:** **P2**. Depends on F-11 (seeding infrastructure in shape) and user interest in which task types to target.

---

### F-14: End-to-end "enabled" smoke test

- **Origin:** Implicit. We have config-disabled behavior tested, but no single test proves "set enabled=true, boot the API, the whole pipeline works end-to-end."
- **Current state:** Unit + integration tests hit each component in isolation. No bootstrapping test that actually sets `enabled=true`, runs a full nightly cycle, and asserts the resulting DB state is coherent.
- **What it unblocks:** Confidence to flip `enabled=true` in production.
- **Scope estimate:** Small-medium. One FastAPI `TestClient` test that spins up the lifespan with a throwaway DB, seeds a capability + automation + some divergence data, forces `scheduler.run_once()`, asserts automation_run + skill_divergence + skill_state_transition rows landed correctly.
- **Priority:** **P1**. Should land before production toggle; gives you a regression trap.

---

## OOS items from spec §2

These were deliberately deferred in the original spec with explicit trigger conditions. Do not build speculatively.

### OOS-1: Event-triggered automations (`on_event`)

- **Trigger to build:** 3+ automations exist that clearly need event triggers (e.g., "when email arrives from X, do Y").
- **Scope:** Large. New event-source subsystem: webhook receiver, filesystem watchers, email-arrival hooks. New `on_event` trigger_type on `automation` table. Dispatcher extension.
- **Priority:** **P2**. Schedule triggers cover the motivating examples today. Reconsider when Nick has a concrete "when X happens, run Y" request that can't be polled.

---

### OOS-2: Per-capability specialized challenger runbooks

- **Trigger:** 6 months of challenger-usage data showing per-capability patterns.
- **Scope:** Medium. Add a per-capability `runbook` field to `capability`, update challenger to use it when present.
- **Priority:** **P2**. Generic challenger is working. Data-driven decision.

---

### OOS-3: Automation composition (chains)

- **Trigger:** A real use case emerges.
- **Scope:** Large. DAG execution model on top of automations.
- **Priority:** **P2**. No demand signal yet.

---

### OOS-4: Step-level shadow comparison

- **Trigger:** Evolution quality is poor across 5+ skills and 3+ attempts each.
- **Scope:** Medium. Instead of only comparing final outputs, compare per-step state objects.
- **Priority:** **P2**. End-to-end evolution should come first; quality assessment is premature.

---

### OOS-5: Logprob-based confidence scoring

- **Trigger:** Self-assessed `confidence` field in local LLM outputs proves uncorrelated with actual accuracy.
- **Scope:** Medium. Capture logprobs from Ollama, aggregate into per-step confidence.
- **Priority:** **P2**. No data yet that self-assessed confidence is wrong.

---

### OOS-6: Multiple skills per capability (A/B, per-input-branch)

- **Trigger:** A capability demonstrably needs divergent implementations beyond what flow control supports.
- **Scope:** Large. Schema change (composite key), dispatcher changes, matcher changes.
- **Priority:** **P2**. One-per-capability is structurally simpler. Wait for a real collision.

---

### OOS-7: Automation sharing / capability templates across users

- **Trigger:** A second real user exists.
- **Scope:** Large. Permissions, sharing URL scheme, sanitization.
- **Priority:** **P2**. Donna is single-user in practice. Revisit when that changes.

---

### OOS-8: Automatic `requires_human_gate` flagging from sensitive tools

- **Trigger:** Manual flagging produces misses on sensitive skills (e.g., a skill that touches email escapes review).
- **Scope:** Small. Scan the skill YAML for tool names in a "sensitive" list at draft creation.
- **Priority:** **P2**. Low effort if triggered. Currently manual flagging is fine.

---

### OOS-9: If-conditionals in the skill DSL

- **Trigger:** 3+ skills in production need real branching.
- **Scope:** Medium. DSL + executor support.
- **Priority:** **P2**. `escalate` short-circuit covers the motivating patterns.

---

### OOS-10: Nested DSL primitives (`for_each` inside `for_each`)

- **Trigger:** A real skill needs nesting and can't be decomposed into sequential steps.
- **Scope:** Medium. Executor + renderer complexity.
- **Priority:** **P2**. Flat DSL is Claude-friendlier.

---

### OOS-11: Exact tokenization for local context budgeting

- **Trigger:** `context_overflow_escalation` rate exceeds 10% of local calls.
- **Scope:** Small. Swap character-based estimate for actual tokenizer.
- **Priority:** **P2**. Dependent on observed metric. Ship F-12 first, then measure, then decide.

---

### OOS-12: Voice-triggered challenger interactions

- **Trigger:** Voice UX is prioritized.
- **Scope:** Large. Voice pipeline is a project of its own.
- **Priority:** **P2**. Orthogonal to skill system.

---

## Recommended sequencing

### Wave 1 — Production enablement (P0)

Get `skill_system.enabled=true` safe to flip in production.

1. **F-1** sandbox SkillExecutor — keystone. Without it, validation is a stub.
2. **F-5** wire sandbox executor into lifespan — trivial once F-1 exists.
3. **F-6** wire NotificationService — alerts are silent without this.

### Wave 2 — Make it actually useful (P1)

Populate the pipeline and close UX gaps.

4. **F-11** seed real capabilities + skills Nick wants to use (depends on user input).
5. **F-14** end-to-end smoke test as a regression trap.
6. **F-2** `automation_run.skill_run_id` linkage — cheap, high debugging value.
7. **F-7** correction-cluster frequency — matches spec's "fires immediately" intent.
8. **F-3** Discord natural-language automation creation — unlocks Nick's primary use case.
9. **F-4** Dashboard UI — biggest effort item, but "human retains judgment-level control" collapses without it. Separate brainstorm track.

### Wave 3 — When triggered (P2)

Do not build speculatively. Revisit with data or a concrete ask.

- **F-10** min_interval enforcement — when F-3/F-4 land.
- **F-12** Grafana dashboards — when first production incident reveals a gap.
- **F-13** migrate more task types — when F-11's infrastructure is mature.
- **OOS-1** event triggers — when 3+ automations need them.
- **OOS-2** per-capability runbooks — after 6 months of challenger data.
- **OOS-3** automation chains — when a real use case exists.
- **OOS-4** step-level shadow — when evolution quality reveals end-to-end comparison isn't enough.
- **OOS-5** logprob confidence — when self-assessed confidence proves uncorrelated.
- **OOS-6** multiple skills per capability — when a real collision occurs.
- **OOS-7** automation sharing — when a second user exists.
- **OOS-8** auto `requires_human_gate` — when manual flagging misses.
- **OOS-9** DSL conditionals — when 3+ skills need branching.
- **OOS-10** nested DSL — when a real skill can't be flattened.
- **OOS-11** exact tokenization — when context overflow rate exceeds 10%.
- **OOS-12** voice — when voice UX is prioritized.

### Wave 4 — Polish (P3)

- **F-9** configurable baseline window.

---

## Priority summary table

| Item | Priority | Origin | Effort |
|---|---|---|---|
| F-1 sandbox executor | P0 | Phase 3/4 drift | Med-Large |
| F-5 wire sandbox executor | P0 | Follow-up to F-1 | Trivial |
| F-6 wire NotificationService | P0 | Phase 5 drift | Small |
| F-2 skill_run_id linkage | P1 | Phase 5 review | Small |
| F-3 Discord automation flow | P1 | Phase 5 drift | Medium |
| F-4 Dashboard UI | P1 | All phases | Large (separate track) |
| F-7 correction frequency | P1 | Phase 4 notes | Small-Med |
| F-11 seed real capabilities | P1 | Implicit | Small-Med per cap |
| F-14 E2E smoke test | P1 | Implicit | Small-Med |
| F-8 evolution → sandbox | P2 | Phase 4 drift | Small (subsumed by F-4) |
| F-10 min_interval enforcement | P2 | Phase 5 drift | Small |
| F-12 Grafana dashboards | P2 | Implicit | Small-Med |
| F-13 migrate task types | P2 | Spec open Q#5 | Small per type |
| F-9 configurable baseline | P3 | Phase 4 drift | Trivial |
| OOS-1..12 | P2 | Spec §2 | Varies |

## Notes

- **F-1 blocks F-5 and effectively blocks the value of AutoDrafter and Evolver.** Everything downstream of "skills improve themselves autonomously" hinges on sandbox validation being real. If you only do one thing from this list, do F-1.
- **F-11 is the gating input for every data-driven decision.** Most OOS triggers read "X automations exist" or "Y months of data" — those only accumulate once there's actual usage.
- **F-4 is a separate project.** It belongs in its own brainstorm cycle, not the next spec + plan. When we get there, propose 2-3 UI approaches (new SPA, extend the existing Flutter work, admin-only plain HTML) before designing.
