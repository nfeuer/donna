# Skill System Follow-Ups

**Status:** Inventory. Each entry is a candidate future spec + plan.
**Scope:** Every gap flagged in Phase 3-5 drift logs plus every OOS-N deferral from the original spec §2.
**Date:** 2026-04-16

This is a backlog, not a roadmap. Priority suggestions are opinions — use them as a starting point for triage, not an execution order.

---

## Completed — Post-Wave waves archived 2026-04-21

Three follow-up waves from `open-backlog.md` shipped between 2026-04-16 and 2026-04-21 but were never trimmed from the tracker. Verified against code and archived here today.

### Wave 1 — Tool registration + F-13 (P1) — ✅ CLOSED

Wired the 4 Claude-native task-type capabilities seeded in `config/capabilities.yaml:100-135` so they stop running as `ad_hoc`. Evidence:

- Tools registered at `src/donna/skills/tools/__init__.py:35-85`: `calendar_read`, `task_db_read`, `cost_summary`, `email_read`.
- Tool modules: `src/donna/skills/tools/{calendar_read,task_db_read,cost_summary,email_read}.py`.
- `tools:` wired on 4 capabilities in `config/capabilities.yaml`:
  - `generate_digest` → `[calendar_read, task_db_read]` (line 103)
  - `prep_research` → `[task_db_read]` (line 114; `web_search` deferred per in-file comment)
  - `task_decompose` → `[]` (line 126)
  - `extract_preferences` → `[task_db_read]` (line 137; `notes_read` deferred per in-file comment)
- Unit tests: `tests/unit/test_skills_tools_{calendar_read,task_db_read,cost_summary,email_read}.py`.
- Integration test extended at `tests/integration/test_cli_wires_tools_and_capabilities.py:100-234` — asserts Wave-1 tools register and `SkillToolRequirementsLookup` passes for the seeded capabilities.

Deferred sub-items preserved as triggered entries in `open-backlog.md`: `web_search`, `notes_read`, `fs_read`.

**Closes:** F-13 (partial — `prep_research` / `extract_preferences` fully migrate once deferred tools land).

### Wave 3 — F-12 Grafana skill-system panels (P2) — ✅ CLOSED

Skill-specific observability dashboard shipped. Evidence:

- Dashboard JSON: `docker/grafana/dashboards/skill_system.json` (4 panels — State Distribution, Evolution Success Rate, Nightly Cron Outcomes, Cost Breakdown by Skill).
- `skill_state_transition` structlog event continues to be emitted from `src/donna/skills/lifecycle.py:242-250` (no code change needed; dashboard queries existing event).
- `skill_evolution_outcome` structlog event added to `src/donna/skills/evolution_scheduler.py:62-70` per-attempt (complements pre-existing aggregate `nightly_evolution_done`).

**Closes:** F-12.

### Wave 4 — F-4 Dashboard UI for skill system + automations (P1) — ✅ CLOSED

Full UI shipped per `docs/superpowers/plans/2026-04-21-wave-4-skill-system-ui.md`. Evidence:

- **Dashboard card:** `donna-ui/src/pages/Dashboard/SkillSystemCard.tsx`.
- **Page shell:** `donna-ui/src/pages/SkillSystem/index.tsx` registered at `donna-ui/src/App.tsx:13,52` under `/skill-system`.
- **Tabs + drawers:** `SkillsTab.tsx` + `SkillDrawer.tsx`, `CandidatesTab.tsx` + `CandidateDrawer.tsx`, `DraftsTab.tsx` (reuses SkillDrawer), `RunsTab.tsx` + `RunDrawer.tsx`, `AutomationsTab.tsx` + `AutomationDrawer.tsx`, shared `StateTransitionForm.tsx`.
- **API client:** `donna-ui/src/api/skillSystem.ts`.
- **Backend aggregator:** `GET /admin/dashboard/skill-system` at `src/donna/api/routes/admin_dashboard.py:759-841`, with thresholds loader and tests in `tests/unit/test_admin_dashboard.py:348-442`.
- **Transitions endpoint:** `GET /skills/_transitions` at `src/donna/api/routes/skills.py:58`, tested in `tests/unit/test_api_skills.py:106`.
- **Thresholds config:** `config/dashboard.yaml:13-17` (`skill_system.*` block).

**Closes:** F-4 and F-W4-E.

### Wave 5 — Polish sweep (P3) — ✅ CLOSED

Acceptance was: *"Each item either resolved or explicitly deferred with its trigger restated."* All four items now satisfy that bar.

- **Item 1 — dashboard `quality_score` thresholds.** Deferred. Trigger restated inline at `config/dashboard.yaml:6-8`: tune after ≥30 days of live data in `invocation_log` post-production enablement. Consumed by `src/donna/api/routes/admin_dashboard.py:_load_dashboard_config()` and `get_quality_warnings()` (lines 614–689); no code change needed.
- **Item 2 — `/admin/*` auth note.** Resolved. `docs/domain/management-gui.md:36` now carries the concrete future-auth note (bearer-token dependency, rate-limiting on write routes, reuse of `admin_access.py` infrastructure) alongside the pre-existing decision at line 122.
- **Item 3 — token counting heuristic.** Deferred. Module docstring in `src/donna/models/tokens.py:1-11` now quantifies the trigger (OOS-11, `context_overflow_escalation` rate > 10%) and cross-references the emission site at `src/donna/models/router.py:215`.
- **Item 4 — Slice 11 Flutter UI pointer.** Already in place. `slices/slice_11_flutter_ui.md:5` has been carrying the "separate Flutter repository (`donna-app`)" note since the slice was authored; the pointer is referenced in `docs/architecture/overview.md:166`.

Wave 5 is archived out of `docs/superpowers/followups/open-backlog.md`. Items 1/2 graduated into the *Triggered* section (as "Dashboard threshold tune" and "Admin auth") so the trigger conditions remain discoverable. Item 3 is covered by the existing OOS-11 entry; item 4 is called out as separate-repo work at the end of the OOS section.

---

## Status update — 2026-04-21 (verified against code)

A code-against-doc reconciliation found the prioritized "Open Follow-ups" sections below are stale — most P0/P1/P2 items have shipped in Waves 1–5 but were never trimmed from the detailed write-ups, the Recommended Sequencing, or the Priority Summary Table. The Wave-N "Completed" sections in this same file are authoritative; the detailed F-* entries below are kept for historical context but are tagged `✅ CLOSED` where verified.

**Verified closed (with evidence):**
- **F-1** sandbox executor → `src/donna/skills/validation_executor.py:29`
- **F-2** `automation_run.skill_run_id` linkage → `alembic/versions/add_automation_tables_phase_5.py:63`; `src/donna/automations/dispatcher.py:108,184`
- **F-3** Discord NL automation → `src/donna/orchestrator/discord_intent_dispatcher.py:39-49,263-273`; `src/donna/integrations/discord_bot.py:494-599`
- **F-5** sandbox in lifespan → `src/donna/cli_wiring.py:403`; `src/donna/skills/startup_wiring.py:54-68`
- **F-6** NotificationService in lifespan → `src/donna/cli_wiring.py:252-265,401`
- **F-7** correction-cluster fast path → `src/donna/skills/correction_cluster.py:61-80`; `src/donna/preferences/correction_logger.py:111-119`
- **F-10** `min_interval_seconds` enforcement → `src/donna/automations/cadence_reclamper.py:82-88`
- **F-11** real capabilities seeded → `alembic/versions/seed_product_watch_capability.py`; `alembic/versions/f3a4b5c6d7e8_seed_news_check_and_email_triage.py`
- **F-14** end-to-end smoke test → `tests/e2e/test_wave4_full_stack.py`; `tests/integration/test_cli_startup_wire_helpers.py:82-139`

**Partial (tracker-listed open, code work exists but gap remains):**
- **F-12** ✅ CLOSED 2026-04-21 — `docker/grafana/dashboards/skill_system.json` shipped with state distribution, evolution success, nightly-cron outcomes, and cost-breakdown panels.
- **F-13** ✅ CLOSED 2026-04-21 — `calendar_read`, `task_db_read`, `cost_summary`, `email_read` tools registered in `src/donna/skills/tools/__init__.py:35-85`; capability YAML wired at `config/capabilities.yaml:103,114,126,137`. `web_search` / `notes_read` / `fs_read` remain triggered items in `open-backlog.md`.
- **F-W1-A** DegradationDetector exists and reads the threshold (`src/donna/skills/degradation.py:37,99,116`), but the original concern is the *binary-classification semantics* on continuous agreement scores. Needs deeper code review to confirm whether the semantics issue was actually fixed or merely papered over.

**Genuinely still open:**
- **F-W4-A** `email_triage` unbounded-sender mode — by-design defer; awaits user trigger.

_(F-4, F-W4-E, and the tool-registration wave previously listed here have since shipped — see the "Post-Wave waves archived 2026-04-21" section above.)_

The detailed F-* entries below pre-date this update; trust the status tags here over the older priority labels.

---

## Wave 3 entry points (read first)

Wave 3 focus: **F-3 Discord natural-language automation creation** (see F-3 entry below). Wave 2 closed all F-W1-* items except F-W1-A (P2, degradation-threshold semantics — still unresolved, see below). Wave 2 also surfaced 6 new follow-ups from its own code review (F-W2-* entries below) — none blocking, but worth folding into Wave 3 or Wave 4 planning.

Predecessor specs for context:
- Wave 1: `docs/superpowers/specs/archive/2026-04-16-skill-system-wave-1-production-enablement-design.md` (PR #44).
- Wave 2: `docs/superpowers/specs/archive/2026-04-17-skill-system-wave-2-first-capability-design.md` (PR #46, depends on #44).
- Original skill-system: `docs/superpowers/specs/archive/2026-04-15-skill-system-and-challenger-refactor-design.md`.

---

## New follow-ups surfaced during Wave 2 code review (2026-04-17)

None are ship-blockers. Captured so a fresh session has the full picture.

- **F-W2-A — `SeedCapabilityLoader` vs seed-migration overlap.** *(Priority P3.)* `src/donna/cli.py` runs `SeedCapabilityLoader.load_and_upsert(config/capabilities.yaml)` on every startup AFTER the seed migration has also inserted the same row. UPSERT handles duplication, but description/input_schema drift between the migration's hardcoded blob and the YAML is silently overwritten by the loader on each boot. Fix options: (a) log a diff when the loader changes rows, (b) have the migration insert a placeholder and rely on the loader for semantic fields, (c) remove the loader and re-run migration-style seeding on every startup. Not blocking today because only one capability exists.

- **F-W2-B — `DEFAULT_TOOL_REGISTRY` thread-safety and test isolation.** *(Priority P3.)* `src/donna/skills/tools/__init__.py` holds a module-level `ToolRegistry()` instance mutated by `register_default_tools` at orchestrator boot. Two risks: (1) test isolation — once populated in one test, state persists across tests; no `clear()` method or pytest reset fixture. (2) Thread-safety — `dict` mutation isn't atomic; safe today because registration happens once before dispatch, but should be documented as a boot-time-only contract. Low priority until either risk manifests; consider adding `ToolRegistry.clear()` + a conftest fixture for long-term hygiene.

- **F-W2-C — E2E doesn't exercise the default-tool-registry wiring.** *(Priority P2.)* `tests/e2e/test_wave2_product_watch.py` covers the claude_native path only (skill state = sandbox), so `SkillExecutor` never dispatches. `tests/integration/test_cli_wires_tools_and_capabilities.py` proves `register_default_tools` is called, but NOT that `SkillExecutor(model_router=fake)` without explicit `tool_registry` ends up dispatching through `DEFAULT_TOOL_REGISTRY`. Add a unit test: construct `SkillExecutor(model_router=fake)` and assert `executor._tool_registry is DEFAULT_TOOL_REGISTRY`. Also revisit once a skill promotes to `shadow_primary` and the skill path actually fires in production — that's when a wiring gap would manifest.

- **F-W2-D — `on_failure` DSL is unimplemented.** *(Priority P2.)* Wave 2 removed `on_failure: fail_step` from `product_watch/skill.yaml` because no consumer reads it — `ToolDispatcher.run_invocation` ignores the key, and the existing behavior (exhausted retries → exception propagates → executor catches → escalate) is what `fail_step` would have meant anyway. But the spec's §6.3 DSL describes `on_failure: escalate | continue | fail_step | fail_skill`. `continue` and `fail_skill` have no current equivalent. Implement when a real skill needs them; today `product_watch` works fine without.

- **F-W2-E — `cli.py` is 770 lines.** *(Priority P3.)* `_run_orchestrator` is ~330 lines of imperative startup wiring. Extract `wire_skill_system()`, `wire_automation_subsystem()`, `wire_discord()` helpers — each takes a shared `StartupContext`/mini-state object. Clean target for a focused refactor commit, especially if Wave 3's Discord challenger refactor adds more wiring.

- **F-W2-F — `url_404.json` fixture could be stricter.** *(Priority P3.)* The fixture's `expected_output_shape` permits `ok` and `in_stock` but doesn't pin `triggers_alert`. A buggy skill that returns `triggers_alert: true` on a 404 would still validate. Tighten to `"required": ["ok", "in_stock", "triggers_alert"]` and add `"triggers_alert": {"enum": [false]}` for this case.

- **F-W2-G — Product_watch skill never exercised by E2E SkillExecutor path.** *(Priority P2 — coupled to F-W2-C.)* Wave 2 ships the skill in `sandbox` state; `AutomationDispatcher` routes `sandbox` skills to `claude_native`. The skill only runs on real traffic if it promotes to `shadow_primary` via the lifecycle gate. That requires 20 schema-valid shadow runs, which requires the skill system's shadow sampler to actually execute the skill in parallel with Claude — and THAT path depends on F-W2-C's gap being closed. Until the skill runs in shadow, no `tool_mocks` get exercised in production and evolution will never trigger. Unblock by writing the E2E gap in F-W2-C, then verifying shadow sampling fires at least one skill run.

---

## Completed — Wave 3 (2026-04-17)

- **F-3** Discord natural-language automation creation. ChallengerAgent now does unified intent-parse + capability-match + input-extraction in a single local-Ollama call. ClaudeNoveltyJudge handles no-match escalations. DiscordIntentDispatcher routes parse results; AutomationConfirmationView + AutomationCreationPath close the loop. See `docs/superpowers/specs/archive/2026-04-17-skill-system-wave-3-discord-nl-automation-design.md`.
- **F-W2-C** Unit test for SkillExecutor default-tool-registry wiring.
- **F-W2-D** on_failure DSL (escalate|continue|fail_step|fail_skill) implemented in ToolDispatcher + executor.
- **F-W2-E** cli.py refactored into StartupContext + wire_skill_system/wire_automation_subsystem/wire_discord helpers.
- **F-W2-G** E2E proves product_watch runs via SkillExecutor at shadow_primary (not claude_native).
- **F-10** min_interval_seconds enforced via cadence clamping (CadencePolicy + CadenceReclamper + SkillLifecycleManager.after_state_change hook).
- **New: CadenceReclamper registered in production cli_wiring** (was previously test-harness only).
- **New: claude_native placeholder capability** seeded so polling automations with capability_name=None can persist.
- **New: alert_conditions DSL alignment** between parse/novelty schemas/prompts and runtime AlertEvaluator.

## Completed — Wave 4 (2026-04-20)

- **news_check** seed capability — RSS/Atom monitoring with since-last-run semantics. `rss_fetch` tool + skill + 4 fixtures + Alembic seed. (Commits: `d00e5df`, `d87fb15`, `2d46b0a`.)
- **email_triage** seed capability — Gmail action-required scan with since-last-run semantics. `gmail_search` + `gmail_get_message` tools + 5-step skill (classify_snippets → for_each fetch_bodies → classify_bodies → render_digest) + 4 fixtures + Alembic seed. (Commits: `99abe04`, `abff1e2`, `f62d96d`, `5aa54bb`.)
- **Dispatcher `prior_run_end` injection** — `AutomationDispatcher` queries most recent successful `automation_run.finished_at` and injects as skill input. Zero schema changes. (Commit: `fc45021`.)
- **`register_default_tools(gmail_client=...)`** — optional GmailClient threading; Gmail tools register only when client is available. (Commits: `092d2a8`, `c0bbdea`.)
- **Capability-availability guard** — `AutomationCreationPath` rejects approval with actionable DM when a required tool is unregistered. (Commits: `1e545d9`, `19b4b1a`.)
- **Digest-shape alert contract** — codified as default for multi-hit capabilities via uniform `{ok, triggers_alert, message, meta}` output.
- **Cross-capability integration test** — single-tick dispatch of product_watch + news_check + email_triage with isolation assertions. Rolls in F-14 intent. (Commit: `2d97563`.)
- **Wave 3 P2/P3 rollup** — doc drift repaired; F-W3-A through K marked closed with commit refs.

See `docs/superpowers/specs/archive/2026-04-20-skill-system-wave-4-news-and-email-capabilities-design.md` and `docs/superpowers/plans/archive/2026-04-20-skill-system-wave-4-news-and-email-capabilities.md`.

## Follow-ups surfaced during Wave 4 (2026-04-20)

- **F-W4-A — `email_triage` unbounded-sender mode.** *(P2.)* Scan all inbound mail for action-required, not just a sender allow-list. Different privacy shape + token cost profile. Wait for concrete user ask.
- **F-W4-B — Pagination for `gmail_search` / `rss_fetch`.** *(P3.)* Trigger: observed context-overflow escalations on either capability.
- **F-W4-C — `html_extract` tool for non-RSS news sites.** *(P3.)* Trigger: a concrete user-named non-RSS source.
- **F-W4-D — Per-automation skill-state blob.** *(P3.)* Alternative to since-last-run semantics if a capability needs richer state carryover. Speculative today.
- **F-W4-E — Dashboard surfacing of `meta.*` per-run diagnostics.** *(P2.)* Depends on F-4 dashboard. Wave 5+.
- **F-W4-F — `ToolRegistry.clear()` + pytest fixture.** *(P3 — escalate to P1 if cross-test leakage surfaces.)* Upgrade of F-W2-B.
- **F-W4-G — First-run digest backlog cap at `NotificationService` layer.** *(P3.)* Today enforced in skill render prompt; eventually belongs in notification layer as generic protection.
- **F-W4-I — `GmailClient` not constructed in orchestrator boot path.** *(P1 — blocks `email_triage` in production.)* Surfaced during Task 8. `src/donna/cli.py` passes `gmail_client=None` to `wire_skill_system` with a TODO — the email subsystem isn't wired into boot yet. `email_triage` automations will hit the W4-D10 capability-availability guard at approval time until this is fixed. Trigger: first real email_triage usage by the user.
- **F-W4-J — `MockToolRegistry` doesn't support exception-raising mocks.** *(P3.)* The `{"__error__": "...", "__message__": "..."}` fixture shape in the Wave 4 plan was aspirational. MockToolRegistry just returns whatever dict is in the map. Error-path fixtures (`news_feed_unreachable.json`, `email_gmail_error.json`) currently return `{ok: false, ...}` and rely on `expected_output_shape: null` to avoid failing. Teach MockToolRegistry to recognize `__error__` keys and raise. Trigger: when proper error-path fixture coverage is needed for evolution gates.
- **F-W4-K — Jinja `{% if inputs.X %}` raises `UndefinedError` under `StrictUndefined` when `X` is absent.** *(P2.)* Surfaced during Task 20. `skills/email_triage/skill.yaml` was fixed (commit `5aa54bb`) by switching to `{% if inputs.X is defined and inputs.X %}`. BUT: the root cause is the challenger NL flow not always populating optional input fields with null — inputs extracted from NL may legitimately omit keys. Two fixes needed: (a) audit all future skill.yaml files during their initial seed PR for the pattern; (b) have `AutomationCreationPath` or challenger/novelty-judge pre-populate all optional schema fields with null defaults at draft time. Trigger: before next capability with optional inputs lands.
- **F-W4-L — `news_check` only monitors `feed_urls[0]`.** *(P2.)* The skill.yaml's `tool_invocations` indexes `feed_urls[0]`, ignoring additional URLs. Input schema accepts an array. User setting `feed_urls: [X, Y]` silently only gets X monitored. Fix: either loop over feeds in the skill (for_each over `inputs.feed_urls`) or document clearly and treat array as "primary + alternates" for failover. Trigger: concrete user ask for multi-feed monitoring.

## Plan-accuracy debt (surfaced by Wave 4 implementation)

The Wave 4 implementation plan document contained two bugs that implementing agents caught and fixed during execution:

- **Bug 1 (Task 3):** Plan referenced `mktime` for struct_time → ISO conversion. Correct function is `calendar.timegm` — `mktime` is the inverse of `localtime` and silently applies the host UTC offset. Fixed in commit `d87fb15`.
- **Bug 2 (Task 10):** Plan referenced `automation_run.end_time` + `status='ok'`. Real schema uses `finished_at` + `status='succeeded'`. Fixed in commit `fc45021`.

Lesson for future wave planners: validate any column names and status enum values against the actual Alembic migrations (and a quick `uv run grep` in the codebase) before writing tasks that query them.

## Follow-ups surfaced during Wave 3 (2026-04-17)

Captured during Wave 3 code reviews. Not blockers; document for planning.

- **F-W3-A — CadencePolicy override precedence.** ✅ Closed in commit `9ae2b8d` (2026-04-17). *(Was P3.)* Override dict can introduce new lifecycle states the base policy doesn't know about. Order fix: validate state is known before checking override. Low priority; current caller paths can't exercise this.
- **F-W3-B — PendingDraftRegistry sweeper race condition.** ✅ Closed in commit `9ae2b8d`. *(Was P3.)* When the async sweeper runs concurrently with set(), a newly-reset draft can be evicted. Re-check TTL at pop time to fix. Important once the sweeper is wired to a background task.
- **F-W3-C — Legacy dedup_pending + field-update handlers unreachable when intent_dispatcher wired.** ✅ Closed in commit `50794a1`. *(Was P2.)* DonnaBot.on_message puts Wave-3 dispatcher before the legacy dedup/field-update branches. Silent functional regression: "merge"/"keep" dedup replies and "change priority to 3" commands go through Claude novelty judge. Fix: move legacy stateful handlers above the Wave-3 dispatcher branch, OR migrate them into dispatcher intents.
- **F-W3-D — DonnaBot _TasksDbAdapter stuffs capability_name + inputs into notes JSON.** ✅ Closed in commit `50794a1`. *(Was P2.)* Awaiting a tasks-schema migration that adds first-class columns. Currently unwindable only by parsing notes.
- **F-W3-E — AutomationConfirmationView approval coroutine holds 30-min timeout in on_message.** *(P3, unchanged — not addressed.)* Not blocking at single-user scale. Consider asyncio.create_task fire-and-forget for scale.
- **F-W3-F — AutomationConfirmationView edit branch is log-only.** ✅ Closed in commit `50794a1`. *(Was P2.)* Card's "Edit" button prompts "What do you want to change?" but no thread is opened and nothing listens for the reply. UX regression risk.
- **F-W3-G — Challenger parse schema + prompt have drift risk.** ✅ Closed in commit `9ae2b8d`. *(Was P3.)* Prompt field list duplicates schema; no test asserts parity. Add a schema-keys-in-prompt check.
- **F-W3-H — Challenger parse schema validation not invoked on LLM response.** ✅ Closed in commit `9ae2b8d`. *(Was P3.)* Challenger calls router.complete but doesn't validate output against schemas/challenger_parse.json (ClaudeNoveltyJudge does via Task 6 fix). Harmonize.
- **F-W3-I — DiscordHandle.notification_service duplication.** ✅ Closed in commit `9ae2b8d`. *(Was P3.)* Delete the duplicated field once Task 8-style wiring is confirmed not to need it.
- **F-W3-J — SkillSystemHandle.skill_router naming misleading.** ✅ Closed in commit `9ae2b8d`. *(Was P3.)* The "skill_router" is also used by automation wiring. Rename or relocate.
- **F-W3-K — Challenger parse snapshot_capabilities is un-cached.** ✅ Closed in commit `9ae2b8d`. *(Was P3.)* Full CapabilityMatcher.list_all on every Discord message. Add TTL cache when volume matters.

## Completed — Wave 2 (2026-04-17)

- **F-W1-B** EvolutionGates now thread `tool_mocks` through all three gates. New `mock_synthesis.py` helper shared between runtime and migration. See `docs/superpowers/specs/archive/2026-04-17-skill-system-wave-2-first-capability-design.md`.
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

Wave 3 shipped **F-3** Discord natural-language automation creation — see the "Completed — Wave 3" section above.

## Completed — Wave 1 (2026-04-16)

- **F-1** Sandbox SkillExecutor → shipped as `ValidationExecutor`. See `docs/superpowers/specs/archive/2026-04-16-skill-system-wave-1-production-enablement-design.md`.
- **F-5** Wire ValidationExecutor into lifespan.
- **F-6** NotificationService wired; automation scheduler moved to orchestrator process.
- **F-14** End-to-end "enabled" smoke test.

New follow-ups surfaced during Wave 1 implementation and final code review:

- **F-W1-A — `DegradationDetector` threshold semantics.** *(Priority P2; not a ship-blocker, but produces silent gaps.)* **Status (2026-04-21):** ⚠️ **NEEDS VERIFICATION** — `DegradationDetector` exists at `src/donna/skills/degradation.py:37` and reads `degradation_agreement_threshold` (lines 99,116), but whether the binary-classification semantics issue was actually fixed (vs. the detector merely existing) requires deeper code review. The detector uses `degradation_agreement_threshold=0.5` as a binary success/failure classifier on each divergence, then computes a Wilson CI on the success count. Divergences with agreement between the threshold and the baseline (e.g., 0.65 when baseline is 0.90) all count as successes and never trigger degradation. A trusted skill that silently drifts to mid-confidence agreement will never flag, so no degradation notification fires. Current mitigation: `correction_cluster` path if the user issues corrections; EOD/nightly digest. Fix options: graded/continuous agreement in the CI, or lower the default threshold. E2E scenario 4 (`test_trusted_degrades_to_flagged`) seeds 0.30 agreement to work around the bug.

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

- **Status (2026-04-21):** ✅ **CLOSED** — shipped as `ValidationExecutor` (`src/donna/skills/validation_executor.py:29`). Section retained for context.
- **Origin:** Phase 3 Task 9 drift entry (AutoDrafter), Phase 4 Task 11 drift entry (Evolver + `assemble_skill_system` default).
- **Current state:** Both `AutoDrafter` and `Evolver` accept `executor_factory=None`, the lifespan wiring passes None, gates 2-4 return `pass_rate=1.0` (vacuous pass). Drafted / evolved skills still land in `draft` requiring human approval — so the safety posture holds, but validation is a stub.
- **What it unblocks:** Fully automated draft→sandbox promotion. Meaningful evolution 4-gate validation. Closes `R28` partial status.
- **Scope estimate:** Medium-large. Requires design decisions: process isolation (subprocess? container? sandbox threading?), tool mocking strategy (deny-all? allow-read? per-fixture allow-list?), timeouts, output capture. New module `src/donna/skills/sandbox_executor.py`.
- **Risk:** Any real executor brings network + DB side-effect surface. Getting the isolation wrong lets generated skills touch prod data.
- **Priority:** **P0**. Everything downstream of "skills evolve themselves" depends on this being real.

---

### F-2: `automation_run.skill_run_id` linkage

- **Status (2026-04-21):** ✅ **CLOSED** — column at `alembic/versions/add_automation_tables_phase_5.py:63`; dispatcher writes both directions at `src/donna/automations/dispatcher.py:108,184`. Section retained for context.
- **Origin:** Phase 5 final code review (Important).
- **Current state:** When an automation dispatches via the skill path, `automation_run.skill_run_id` is always `None`. The column exists for this linkage; the dispatcher just doesn't populate it because `SkillExecutor.execute()` doesn't return the persisted `run_id`.
- **What it unblocks:** Dashboard traceability (click through from an automation run to the underlying skill run). Attribution of automation costs to specific skill versions.
- **Scope estimate:** Small. Either (a) add `run_id` to `SkillRunResult`, or (b) pass `automation_run_id` into `executor.execute(...)` — there's already an unused `automation_run_id` parameter stub. Option (b) also writes the linkage back into `skill_run.automation_run_id`, which gives both directions.
- **Priority:** **P1**. Low effort, high clarity gain for debugging.

---

### F-3: Discord natural-language automation creation

- **Status (2026-04-21):** ✅ **CLOSED** — `src/donna/orchestrator/discord_intent_dispatcher.py:39-49,263-273`; `src/donna/integrations/discord_bot.py:494-599`. Section retained for context.
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

- **Status (2026-04-21):** ✅ **CLOSED** — `src/donna/cli_wiring.py:403`; `src/donna/skills/startup_wiring.py:54-68`. Section retained for context.
- **Origin:** Follow-up to F-1. Once the sandbox executor exists, `assemble_skill_system` needs to pass it as `executor_factory=...` instead of `lambda: None`.
- **Current state:** Line `executor_factory=None` in `src/donna/skills/startup_wiring.py`.
- **Scope estimate:** Trivial if F-1 has the right interface. Just a single wire-up line + an E2E regression test.
- **Priority:** **P0**, immediately after F-1 lands.

---

### F-6: NotificationService wired into FastAPI lifespan

- **Status (2026-04-21):** ✅ **CLOSED** — `src/donna/cli_wiring.py:252-265,401`. Section retained for context.
- **Origin:** Phase 5 drift log.
- **Current state:** `app.state.notification_service` is never populated in `src/donna/api/__init__.py`. `AutomationDispatcher` defensively checks `self._notifier is not None` and skips notification when absent — runs succeed but no alerts go out.
- **What it unblocks:** AS-5.4 in production (alert conditions fire → Discord DM). Currently alerts only fire in tests where the fixture explicitly injects the notifier.
- **Scope estimate:** Small. Construct a `NotificationService` in the lifespan, attach to `app.state.notification_service`, confirm the rest of the codebase doesn't try to instantiate a second one.
- **Priority:** **P0**. Without this, the automation subsystem is mechanically correct but operationally silent. Low effort.

---

### F-7: `CorrectionClusterDetector` frequency — hourly or on-correction

- **Status (2026-04-21):** ✅ **CLOSED** — sync fast-path at `src/donna/skills/correction_cluster.py:61-80`, triggered from `src/donna/preferences/correction_logger.py:111-119`; nightly fallback also present. Section retained for context.
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

- **Status (2026-04-21):** ✅ **CLOSED** — `src/donna/automations/cadence_reclamper.py:82-88`. Section retained for context.
- **Origin:** Phase 5 drift log (R31 partial-semantics note).
- **Current state:** The `automation.min_interval_seconds` column is persisted but not enforced at dispatch time. The scheduler trusts the cron expression. If a user creates an automation with `*/30 * * * * *` (every 30s) and `min_interval_seconds=300`, the scheduler will still fire every 30s.
- **What it unblocks:** Genuine rate-limit floor the spec described.
- **Scope estimate:** Small. In `AutomationDispatcher._compute_next_run`, clamp `next_run_at` to `max(next_run_at, last_run_at + timedelta(seconds=min_interval_seconds))`. Or reject at creation/edit time in the API route.
- **Priority:** **P2**. Currently there's no creation path that produces pathological cron expressions (dashboard doesn't exist, Discord flow doesn't exist). When F-3 or F-4 lands, revisit.

---

### F-11: Seed useful capabilities + skills for real usage

- **Status (2026-04-21):** ✅ **CLOSED** — `product_watch`, `news_check`, `email_triage` all seeded (`alembic/versions/seed_product_watch_capability.py`; `alembic/versions/f3a4b5c6d7e8_seed_news_check_and_email_triage.py`). Additional task-type capabilities seeded but pending tool registration — see F-13. Section retained for context.
- **Origin:** Implicit. Phase 1 seeded `parse_task`, `dedup_check`, `classify_priority` (three existing task types). Phase 5 delivered the automation subsystem with nothing to automate.
- **Current state:** No capabilities exist for the motivating examples (`product_watch`, `news_check`, `meeting_prep`). An empty capability registry means the challenger's match-and-route layer is permanently in "novelty" mode.
- **What it unblocks:** Real user flows. AS-5.1 refers to `product_watch` as if it already existed — it doesn't.
- **Scope estimate:** Small-medium per capability. Define the capability row + input schema, hand-write an initial skill YAML + step prompts + schemas + 3-5 fixtures per capability. First capabilities should be ones Nick actually wants to use — that's a user input, not a design decision.
- **Priority:** **P1**. Without this, nothing upstream matters — the whole pipeline has nothing to chew on.

---

### F-12: Observability dashboards

- **Status (2026-04-21):** ⚠️ **PARTIAL** — generic Grafana dashboards exist (`docker/grafana/dashboards/{error_exploration,task_pipeline,llm_cost,system_health}.json`), but skill-system-specific panels (state distribution, evolution success, cost-by-skill) are still missing.
- **Origin:** Implicit. Every Phase 3-5 component logs structured events but there's no aggregation or alerting on top.
- **Current state:** Events are logged to `invocation_log` and structlog. Grafana/Loki exists in the infra stack but no skill-specific dashboards.
- **What it unblocks:** Operational visibility. When a promotion gate is stuck or evolution is failing repeatedly across many skills, the user would otherwise only notice via EOD digest.
- **Scope estimate:** Small-medium. Add Grafana panels for: skill state distribution over time, daily nightly-cron outcomes, automation success/failure rates, evolution success rate per skill, cost breakdown by skill-system task type.
- **Priority:** **P2**. Not blocking; EOD digest covers the basics. Do when the first production incident reveals a gap.

---

### F-13: Migrate existing Claude-native task types to capabilities

- **Status (2026-04-21):** ⚠️ **PARTIAL** — `generate_digest`, `prep_research`, `task_decompose`, `extract_preferences` are seeded as capabilities in `config/capabilities.yaml:100-135`, but the underlying tools they reference (`calendar_read`, `task_db_read`, `cost_summary`, `web_search`, `email_read`, `notes_read`, `fs_read`) are NOT registered in `src/donna/skills/tools/__init__.py:31-56` (only `web_fetch`, `rss_fetch`, `html_extract`, `gmail_search`, `gmail_get_message` exist). Capabilities are inert until a tool-registration wave lands.
- **Origin:** Spec Open Questions #5 ("Migration strategy for existing task types").
- **Current state:** `parse_task`, `dedup_check`, `classify_priority` are seeded. The spec open-question lists `generate_digest` as a likely next candidate; `prep_research`, `task_decompose`, `extract_preferences` are also in `config/task_types.yaml` and currently run straight through Claude.
- **What it unblocks:** `SkillCandidateDetector` automatically surfaces these once they have capability rows. Opens them to drafting + evolution + shadow.
- **Scope estimate:** Small per task type. Write a migration that inserts the capability row for each, seed embeddings, confirm the task-type→capability-name linkage works.
- **Priority:** **P2**. Depends on F-11 (seeding infrastructure in shape) and user interest in which task types to target.

---

### F-14: End-to-end "enabled" smoke test

- **Status (2026-04-21):** ✅ **CLOSED** — `tests/e2e/test_wave4_full_stack.py`; `tests/integration/test_cli_startup_wire_helpers.py:82-139`. Section retained for context.
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

> **Updated 2026-04-21:** Waves 1–5 closed F-1, F-2, F-3, F-5, F-6, F-7, F-10, F-11, F-14. The historical Wave 1/Wave 2 sequencing below is preserved for reference; the current open backlog is in the section that follows.

### Current open backlog (verified 2026-04-21)

1. **Tool-registration wave** — register `calendar_read`, `task_db_read`, `cost_summary`, `web_search`, `email_read`, `notes_read`, `fs_read` on `DEFAULT_TOOL_REGISTRY` (`src/donna/skills/tools/__init__.py`). Activates the four claude-native task-type capabilities seeded for F-13.
2. **F-4** Dashboard UI for skill system + automations — separate frontend track. Skill/automation/skill-run/draft/evolution pages. Unblocks F-W4-E.
3. **F-W1-A** DegradationDetector threshold semantics — verify the binary-classification issue is actually fixed; revisit if not.
4. **F-12** Grafana skill-system panels — generic dashboards exist; skill-state distribution + evolution-success + cost-by-skill panels still missing.
5. **F-W4-A** `email_triage` unbounded-sender mode — by-design defer; build when user asks.
6. **F-W4-E** Dashboard `meta.*` per-run diagnostics — gated on F-4.

### Historical sequencing (waves 1 & 2 — now closed)

#### Wave 1 — Production enablement (P0)

Get `skill_system.enabled=true` safe to flip in production.

1. **F-1** sandbox SkillExecutor — keystone. Without it, validation is a stub. ✅ Closed
2. **F-5** wire sandbox executor into lifespan — trivial once F-1 exists. ✅ Closed
3. **F-6** wire NotificationService — alerts are silent without this. ✅ Closed

#### Wave 2 — Make it actually useful (P1)

Populate the pipeline and close UX gaps.

4. **F-11** seed real capabilities + skills Nick wants to use (depends on user input). ✅ Closed
5. **F-14** end-to-end smoke test as a regression trap. ✅ Closed
6. **F-2** `automation_run.skill_run_id` linkage — cheap, high debugging value. ✅ Closed
7. **F-7** correction-cluster frequency — matches spec's "fires immediately" intent. ✅ Closed
8. **F-3** Discord natural-language automation creation — unlocks Nick's primary use case. ✅ Closed
9. **F-4** Dashboard UI — biggest effort item, but "human retains judgment-level control" collapses without it. Separate brainstorm track. ❌ Still open

#### Wave 3 — When triggered (P2)

Do not build speculatively. Revisit with data or a concrete ask.

- **F-10** min_interval enforcement — when F-3/F-4 land. ✅ Closed
- **F-12** Grafana dashboards — when first production incident reveals a gap. ⚠️ Partial
- **F-13** migrate more task types — when F-11's infrastructure is mature. ⚠️ Partial (seeded; tools still need registration)
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

| Item | Priority | Origin | Effort | Status (2026-04-21) |
|---|---|---|---|---|
| F-1 sandbox executor | P0 | Phase 3/4 drift | Med-Large | ✅ Closed |
| F-5 wire sandbox executor | P0 | Follow-up to F-1 | Trivial | ✅ Closed |
| F-6 wire NotificationService | P0 | Phase 5 drift | Small | ✅ Closed |
| F-2 skill_run_id linkage | P1 | Phase 5 review | Small | ✅ Closed |
| F-3 Discord automation flow | P1 | Phase 5 drift | Medium | ✅ Closed |
| F-4 Dashboard UI | P1 | All phases | Large (separate track) | ❌ Open |
| F-7 correction frequency | P1 | Phase 4 notes | Small-Med | ✅ Closed |
| F-11 seed real capabilities | P1 | Implicit | Small-Med per cap | ✅ Closed |
| F-14 E2E smoke test | P1 | Implicit | Small-Med | ✅ Closed |
| F-8 evolution → sandbox | P2 | Phase 4 drift | Small (subsumed by F-4) | ⚠️ Subsumed (gated on F-4) |
| F-10 min_interval enforcement | P2 | Phase 5 drift | Small | ✅ Closed |
| F-12 Grafana dashboards | P2 | Implicit | Small-Med | ⚠️ Partial (skill panels missing) |
| F-13 migrate task types | P2 | Spec open Q#5 | Small per type | ⚠️ Partial (tools unregistered) |
| F-W1-A degradation semantics | P2 | Wave 1 review | Small-Med | ⚠️ Needs verification |
| F-W4-A unbounded-sender | P2 | Wave 4 review | Med | ❌ Open (by-design defer) |
| F-W4-E `meta.*` diagnostics | P2 | Wave 4 review | Small (gated on F-4) | ❌ Open |
| Tool-registration wave | P1 | Implicit (blocks F-13) | Small per tool | ❌ Open |
| F-9 configurable baseline | P3 | Phase 4 drift | Trivial | ✅ Closed (Wave 5) |
| OOS-1..12 | P2 | Spec §2 | Varies | ❌ Open (triggered) |

## Notes

- **F-1 blocks F-5 and effectively blocks the value of AutoDrafter and Evolver.** Everything downstream of "skills improve themselves autonomously" hinges on sandbox validation being real. If you only do one thing from this list, do F-1.
- **F-11 is the gating input for every data-driven decision.** Most OOS triggers read "X automations exist" or "Y months of data" — those only accumulate once there's actual usage.
- **F-4 is a separate project.** It belongs in its own brainstorm cycle, not the next spec + plan. When we get there, propose 2-3 UI approaches (new SPA, extend the existing Flutter work, admin-only plain HTML) before designing.
