# Changelog

Recent changes, summarized from commits and PRs.

## 2026-06-11

### Added
- **Skill-subsystem alerting**: new `skills/alerting.py` raises fallback alerts for skill-lifecycle paths that were previously silent — degradation demotions, run-persistence failures, shadow-sample-loss streaks, and evolution park-in-draft. ([Skill System](domain/skill-system/lifecycle.md))
- **Pending-approval surfacing**: a Discord ping fires when the nightly auto-draft creates skills awaiting approval, and a standing "⏳ Pending your approval" section in the EOD digest lists every skill parked in `draft` (auto-drafted or evolution-parked) until acted on. ([Skill System](domain/skill-system/lifecycle.md))

### Changed
- **Skill auto-draft now human-gated by default**: auto-drafted skills default to `requires_human_gate=1`, so a single human approval at `draft → sandbox` is mandatory before a skill leaves draft; the sandbox→shadow_primary→trusted promotions thereafter stay automatic, gated only by the §23.4 run-validity and shadow-agreement thresholds. `spec_v3.md` §23.5 and `lifecycle.md` were reconciled to match. ([Skill System](domain/skill-system/lifecycle.md), `spec_v3.md` §23.5)
- **Serialized placement choke point**: `Scheduler.schedule_task` / `schedule_dependency_chain` run the read→find-slot→create-event section under an `asyncio.Lock`, realizing the `spec_v3.md §3.7.1` double-booking guard (the earlier "async queue" wording was a design target). ([Scheduling](domain/scheduling.md#placement-safety-fable-scheduling-s1-2026-06-11), `spec_v3.md` §3.7.1)
- **Budget enforcement**: `BudgetGuard.check_pre_call` now enforces the `$100`/month hard cap (previously daily-only — the monthly check was dead code) and fires the 90% monthly warning. `BudgetPausedError` carries a `period` (`daily` / `monthly`). ([Cost & Escalation](domain/cost.md#budget-enforcement-flow))
- **Escalation-gate posture**: new `config/manual_escalation.yaml` → `gate.mode`. The default **`shadow`** consults the gate on every call and logs would-escalate events (`escalation_shadow_would_fire`) without prompting, persisting, or blocking; **`enforce`** runs the interactive decision tree. The router now derives a deterministic cost floor when a caller omits `estimate_usd`, so the gate is no longer dark. ([Cost & Escalation](domain/cost.md#gate-posture-shadow-vs-enforce))
- **Ledger integrity at the model choke point**: `ModelRouter.complete()` is now the *accounting* boundary, not just dispatch. Production routers are built via `build_model_router()`, which **requires** an `invocation_logger`, and `complete()` raises `RoutingError` rather than make an unlogged billed call — so all spend reaches `invocation_log`, the table `BudgetGuard` reads for the `$100`/month cap. Chat and bot routers are now wired through the factory. ([Model Layer](domain/model-layer.md#structured-invocation-logging))
- **Config-driven pricing**: per-call `cost_usd` is computed from the per-alias config rates (`input/output_cost_per_token_usd`) instead of hardcoded Sonnet `$3`/`$15`; the Anthropic provider **fails loud** on an unpriced model id rather than silently mispricing. ([Model Layer](domain/model-layer.md#structured-invocation-logging))
- **Dead config removed**: `confidence_threshold` (read nowhere) was removed from `donna_models.yaml` and the `RoutingEntry` model; re-add it with the consuming logic when confidence scoring lands. ([Model Layer](domain/model-layer.md))

### Fixed
- **Timezone-correct slot placement**: `Scheduler.find_next_slot` now steps candidates in UTC (DST-safe) but evaluates every time-window against the configured `calendar.yaml` zone, so the absolute blackout and domain windows are enforced on the user's wall clock instead of UTC — a work task can no longer land at ~4 AM local, and confirmations show the correct local time. ([Scheduling](domain/scheduling.md#placement-safety-fable-scheduling-s1-2026-06-11), `spec_v3.md` §6.3)
- **Deadline-aware horizon**: the search horizon is clamped to the task's deadline / `earliest` bound (honoring a `constrained` weekday); an unplaceable dated task now surfaces as `needs_scheduling` instead of being placed late within a flat 14-day window. ([Scheduling](domain/scheduling.md#placement-safety-fable-scheduling-s1-2026-06-11))
- **Fail-closed calendar reads**: placement now builds its busy-set from the union of *all* configured calendars (personal + work + family) and raises `CalendarReadError` (with a fallback alert) on any read error, rather than booking blind against an empty calendar. ([Scheduling](domain/scheduling.md#placement-safety-fable-scheduling-s1-2026-06-11))
- **Billed spend dropped on token-limit truncation**: a token-capped extension call raised `TokenLimitReachedError` *before* the `invocation_log` write, dropping real spend from budget accounting. The raise now happens after the log + payload writes; `auto_drafter` / `evolution` catch it; log-write failures now alert via `fallback_alert_fn` instead of warning silently. ([Cost & Escalation](domain/cost.md))
- **Skill trust-gate evidence loop wired**: the production executor factory now constructs `SkillRunRepository` and injects the bundle's `ShadowSampler` into `SkillExecutor`, with a boot invariant that alerts loudly if skills run live without run-persistence/sampler — previously the statistical trust gates ran on data that was never produced, so promotion and auto-demotion were inert in production. ([Skill System](domain/skill-system/lifecycle.md))
- **Skill trust-gate landmines**: the `requires_human_gate` check no longer blocks system-actor *demotions* (it is scoped to a promotion-destination allowlist); gate evidence is keyed on `skill_version_id`, so an evolved version no longer inherits its predecessor's track record; and a run counts as valid only if it succeeded with no `continued`/`step_failed`/`skill_failed` step, with a config-driven failure-rate ceiling guarding shadow→trusted. ([Skill System](domain/skill-system/lifecycle.md))
- **Dead evolution transition removed**: the `contextlib.suppress(IllegalTransitionError)` around an always-failing hop in `evolution.py` was deleted; an evolved version now parks in `draft` with an explicit alert, and `transition()` rejects `reason="human_approval"` from a system actor. ([Skill System](domain/skill-system/evolution.md))

## 2026-06-06

### Added
- **Time intent**: the parser now emits a structured `time_intent` classifying *when* a task happens (`exact` / `window` / `constrained` / `recurring` / `none`), persisted as `tasks.time_intent_json` (Alembic migration). `deadline` / `deadline_type` are derived from it. An LLM-free fallback re-extracts common date phrasings when the model omits it. ([Task System](domain/task-system.md#time-intent))
- **Routing gate**: a deterministic, LLM-free gate routes captured tasks to the scheduler (time-bound), automation (recurring), or backlog (undated). ([Scheduling](domain/scheduling.md#routing-gate))
- **`needs_scheduling` state**: time-bound tasks the scheduler can't place before their deadline surface in `needs_scheduling` instead of stranding in backlog. ([Task System](domain/task-system.md#valid-transitions))
- **Persona-voice capture confirmations**: slot-aware Discord confirmations (template-based, zero-token) replace the static "Scheduled: pending." reply. ([Capture a Task](workflows/capture-a-task.md))

### Fixed
- **Strand bug**: time-bound tasks are now scheduled immediately by the routing gate and no longer deferred for the Challenger, fixing cases where dated tasks stranded in `backlog`. ([Scheduling](domain/scheduling.md#routing-gate))

## 2026-05-18

### Added
- **Documentation system**: global `update-docs` skill and `docs-updater` agent for bootstrapping, updating, and auditing docs across projects ([Domain](domain/index.md))

### Changed
- **Documentation cleanup**: extracted all inline "not implemented" / "deferred" / "obsolete" callouts from 11 domain docs into [`open-backlog.md`](superpowers/followups/open-backlog.md) with stable gap IDs (G-1 through G-29)
- **Skill System docs refactored**: split 769-line `skill-system.md` into 5 focused subpages (index, setup, lifecycle, evolution, reference) under `domain/skill-system/`
- **Memory Vault docs refactored**: split 312-line `memory-vault.md` into 4 focused subpages (index, semantic, episodic, templates) under `domain/memory-vault/`
- **Management GUI docs refactored**: split 495-line `management-gui.md` into 4 focused subpages (index, api, pages, reference) under `domain/management-gui/`
- **Domain index enhanced**: added Mermaid architecture diagram and 7-step "Start Here" reading guide to [`domain/index.md`](domain/index.md)
- `spec_v3.md`: added §0 Implementation Status Matrix, removed 5 inline status blocks, moved Phase 6 details to appendix
- `followups.md`: archived 28 closed items, trimmed to open items only
- `properdocs.yml`: link validation tightened to `warn` (CI catches broken links via `--strict`)
- Clarified distinct purposes of `open-backlog.md` (feature gaps) vs `followups.md` (spec questions)

### Fixed
- PayloadWriter correctly wired into all ModelRouter instances
- Removed copy-paste error in `cost.md` (contained skill-system.md content)
- Fixed `backup-recovery.md` stub with proper intro text
- Updated stale "in flight" marker on slice 15 in `slices.md`
- Trimmed planned `donna_logs.db` schema from `observability.md` (archived to `archive/`)
- Expanded `slices.md` with slices 16–24 (escalation, budget, dashboard, chat, tool gaps)

## 2026-05-17

### Added
- **Claude Inspector**: full forensics UI for browsing LLM calls, comparing payloads, and analyzing cost/performance insights ([Insights](domain/insights.md), [Management GUI](domain/management-gui/index.md))
- **Payload collection subsystem**: `PayloadWriter` captures full request/response payloads; `PayloadEvictor` enforces disk budget ([Collection](domain/collection.md))
- Claude Inspector API endpoints for call browsing, payload retrieval, and insights queries
- Deep-link query parameter support on Claude Inspector page
- Claude Code project skills, agents, and hooks for development automation

### Changed
- Chat engine now supports session persistence, grouping, and optimistic message rendering

### Fixed
- Docker: added `DONNA_PAYLOAD_DIR` env var for payload storage path
- CI: resolved lint and typecheck failures

## 2026-05-16

### Changed
- Preferences: migrated to event-driven correction pipeline via `CorrectionSubscriber` and `TaskEventBus` ([Preferences](domain/preferences.md))

### Fixed
- UI: migrated drawers to inline expansion (Tasks, Preferences, Candidates) and CenterDialog (Logs, SkillSystem, Shadow)

## 2026-05-15

### Added
- **Chat action system**: `ActionRegistry` with handlers for tasks, vault, skills, automations, and debug commands ([Chat](domain/chat.md))
- Quick Chat panel with floating button and Cmd+J toggle
- `CorrectionSubscriber` for event-driven preference correction logging
- `DashboardContext` provider and `CenterDialog` primitive for UI
- Product watch v3 triage cascade with tool_use wiring
- Unit tests for executor tool_use loop and correction event flow e2e test

### Fixed
- Automations: atomic success reset in `advance_schedule`; success un-pauses, failure notifications routed to donna-debug
- Discord: text-based done intent, thread message routing guards
- Skills: `on_failure` added to `claude_with_triage`, fallback condition fix
- Calendar: delete Google Calendar event when task is cancelled
- Migration: widen `overdue_thread_map` snowflake column to BigInteger

## 2026-05-14

### Added
- **Automation alert pipeline**: defaults, notification channels, multi-channel routing

### Fixed
- Discord: text-based done intent and thread message routing
- Scheduler: authenticate calendar client in orchestrator startup

## 2026-05-13

### Added
- **Calendar page**: week view with time slots, data fetching, week navigation, completed task styling ([Management GUI](domain/management-gui/index.md))
- Calendar added to sidebar navigation and routing

### Fixed
- Calendar: auth token persistence, nginx proxy, day placement, DST index, overflow clipping
- Auth: allow internal-network requests to user routes without Immich login
- Docker: mount vault volume in API container
- UI: sentinel value for Vault folder select (Radix empty-string fix)
- Chat: use `chat_respond` template with JSON output instructions
