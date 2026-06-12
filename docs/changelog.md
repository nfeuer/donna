# Changelog

Recent changes, summarized from commits and PRs.

## 2026-06-11

### Fixed
- **Discord clarification replies no longer vanish**: `DiscordIntentDispatcher._resume` discarded the pending draft *before* checking the re-parse status, so a clarification reply that re-parsed to `escalate_to_claude` (or `ready`+`chat`) fell through to `no_action` and was silently dropped — no judge, no task, no message. `_resume` now mirrors `dispatch()`: escalations route to the novelty judge, `ready`/`chat` returns chat, an unknown status asks the user to rephrase, and the draft is discarded only on a terminal outcome. ([Orchestrator](domain/orchestrator.md))
- **Challenger fail-open is no longer silent**: the three fail-open paths (transport error, OSError, `execute()` exception) now emit `dispatch_fallback_alert`, and a schema-validation failure degrades to `escalate_to_claude` instead of proceeding on unvalidated model output. Fail-open is kept (the Challenger must never block task creation). ([Agents](domain/agents.md))
- **Atomic task-state transitions**: `Database.transition_task_state` read+validated status *before* taking the write lock (TOCTOU); read + validate + write now happen inside the lock, honoring the spec §3.7.1 atomicity guarantee.

### Changed
- **Tool-validation allowlist can no longer be bypassed**: `ToolRegistry.execute` now requires `task_type`+`agent_name`; previously omitting `task_type` skipped the allowlist entirely (principle #6). ([Orchestrator](domain/orchestrator.md))
- **§7.2 sub-agent pipeline documented as dormant**: `spec_v3.md §7.2`, `docs/domain/orchestrator.md`, and `docs/domain/agents.md` now state that `AgentDispatcher` + the PM/Prep/Scheduler/Decomposition agents + the agent-layer `ToolRegistry` are built-but-unwired, and describe the real live flow (`DiscordIntentDispatcher` → `ChallengerAgent` → `ClaudeNoveltyJudge` → `AutoScheduler`).

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
