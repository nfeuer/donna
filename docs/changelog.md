# Changelog

Recent changes, summarized from commits and PRs.

## 2026-05-18

### Added
- **Documentation system**: global `update-docs` skill and `docs-updater` agent for bootstrapping, updating, and auditing docs across projects ([Domain](domain/index.md))

### Fixed
- PayloadWriter correctly wired into all ModelRouter instances

## 2026-05-17

### Added
- **Claude Inspector**: full forensics UI for browsing LLM calls, comparing payloads, and analyzing cost/performance insights ([Insights](domain/insights.md), [Management GUI](domain/management-gui.md))
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
- **Calendar page**: week view with time slots, data fetching, week navigation, completed task styling ([Management GUI](domain/management-gui.md))
- Calendar added to sidebar navigation and routing

### Fixed
- Calendar: auth token persistence, nginx proxy, day placement, DST index, overflow clipping
- Auth: allow internal-network requests to user routes without Immich login
- Docker: mount vault volume in API container
- UI: sentinel value for Vault folder select (Radix empty-string fix)
- Chat: use `chat_respond` template with JSON output instructions
