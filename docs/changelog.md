# Changelog

Recent changes, summarized from commits and PRs.

## 2026-06-11

### Changed
- **Task parsing is now local-first.** `parse_task` routes to the local model (`local_parser`, qwen2.5:32b) as primary, with confidence-gated escalation to the cloud `reasoner` via a new `parse_task_cloud` route when the local parse confidence is below 0.7. Most tasks now parse at zero marginal cost; ambiguous ones get a cloud second opinion.
- **Calibrated duration estimates.** `prompts/parse_task.md` gained explicit duration anchors (quick comms 15 min / errands 30 / focused work 60), fixing the prior "every task is ~1 hour" behavior. The domain rubric was sharpened and now leans on injected personal context to disambiguate work vs personal.

### Added
- **Personal-context injection.** New `orchestrator/task_context.py` assembles a compact context block from vault notes (semantic search) + active learned-preference rules, injected into the parse prompt via a `{{ personal_context }}` slot. Degrades gracefully when the vault is empty. `PreferenceApplier` and `memory_store` are now wired into the live parser.
- **Domain/duration edit pathway.** `PATCH /tasks/{id}` (API) and `PATCH /admin/tasks/{id}` (dashboard) now accept `domain` and `estimated_duration`, with an inline editor in the dashboard task detail panel. These edits fire the `CorrectionSubscriber` learning loop, which was previously dormant for these fields.

### Notes
- `spec_v3.md` model-routing and task-parsing sections describe the old cloud-first behavior; reconciliation tracked as S25 in [`followups.md`](superpowers/specs/followups.md).

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
