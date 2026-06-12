# Open Backlog

**Date:** 2026-05-18 (revised)
**Purpose:** Canonical tracker for missing or incomplete features across all Donna subsystems. Each gap has a stable ID (G-\*) referenced from domain docs.
**Scope:** Only genuinely-open items. Closed/shipped follow-ups live in the archived historical tracker: `docs/superpowers/followups/archive/2026-04-16-skill-system-followups.md`.
**Related:** [`followups.md`](../specs/followups.md) tracks spec-level cross-slice follow-ups (implementation questions, spec drift, deferred decisions). This file tracks *feature gaps*; that file tracks *spec questions*.

All remaining items are deferred with explicit trigger conditions — don't build speculatively. When a trigger fires, open a new wave.

---

## Priority legend

- **P0** — Blocks `skill_system.enabled=true` in production.
- **P1** — Unlocks meaningful value; do after P0.
- **P2** — Deferred with a named trigger; don't build speculatively.
- **P3** — Exploratory / polish.

---

## Gaps extracted from documentation audit (2026-05-18)

Items below were inline callouts in domain docs, now tracked here as the canonical gap list. Each has a stable ID (G-\*) referenced from the source doc.

### Critical — blocks a feature

| ID | Feature | Current State | What's Blocking | Source Doc | Spec § |
|----|---------|--------------|-----------------|-----------|--------|
| G-1 | GmailClient wiring | Not wired into orchestrator boot | email_triage automation can't run | domain/agents.md | §12.1 |
| G-2 | SkillSystemConfig runtime wiring | Pydantic model exists, fields not read by runtime | Thresholds hardcoded as module constants | domain/skill-system/setup.md | §23 |

### Partial — shipped with gaps

| ID | Feature | What's Shipped | What's Missing | Source Doc | Spec § |
|----|---------|---------------|---------------|-----------|--------|
| G-10 | Priority escalation | Deadline + workload pressure | Dependency-chain, user-lock flag | domain/task-system.md | §5.5.2 |
| G-11 | Scheduling conflict resolution | Basic overlap detection | Priority displacement, cascade-shift, dual-invite | domain/scheduling.md | §6.2 |
| G-12 | Time windows | 6 of 8 live | Extended Work, Emergency Work not configured | domain/scheduling.md | §6.1.2 |
| G-13 | Observability DB | invocation_log in donna_tasks.db + Loki | Dedicated donna_logs.db not implemented | domain/observability.md | §14.3.1 |
| G-14 | Notification tiers | Discord DM (tier 1-2) | Email tier 3 | domain/notifications.md | §11.1 |
| G-15 | Budget breach handling | Daily **and** monthly caps now enforced in BudgetGuard (2026-06-11); decision tree wired | Decision tree runs in **shadow** mode — flip `gate.mode: enforce` after calibration (see followups.md "Fable Wave A") | workflows/handle-budget-breach.md | §18 |
| G-16 | MorningDigest production wiring | Construction code exists | No production call site in orchestrator | domain/management-gui/index.md | §22 |
| G-17 | Tool gap queue UI | Data model + Discord ping shipped (slice 22) | Standalone dashboard queue surface | domain/management-gui/index.md | §22 |
| G-18 | Task soft-delete path | MemoryStore.delete() ready | No soft-delete on tasks table or Database API | domain/memory-vault/episodic.md | §30 |

### Deferred / Phase 6 — not started, by design

| ID | Feature | Rationale | Trigger Condition | Spec § |
|----|---------|-----------|-------------------|--------|
| G-20 | MCP Tier 2 (FastMCP) | Only Tier 1 needed currently | User needs GitHub/Notes/SearXNG integration | §3.2 |
| G-21 | Coding Agent | Safety gate: Phase 6 | Code generation use case arises | §7.1.1 |
| G-22 | Communication Agent | Safety gate: Phase 6 | Email/message drafting use case arises | §7.1.1 |
| G-23 | Off-server backup (GCS/Backblaze) | Local NVMe sufficient | Disaster recovery requirement | §16.3.2 |
| G-24 | Flutter app | API shipped, UI in sibling repo | Mobile use case prioritized | §20 |
| G-25 | donna_logs.db dedicated log DB | Loki pipeline works | Need SQLite-queryable structured logs | §14.3.1 |
| G-26 | Per-task-type compaction strategies | Heuristic token estimation sufficient | Context overflow rate > 10% | §4 |
| G-27 | pgvector brain on Supabase | Not needed for current scale | Long-history retrieval required | §4 |
| G-28 | Exact tokenization (Ollama /api/tokenize) | Heuristic sufficient | Token estimation drift causes problems | §4 |
| G-29 | Per-alias daily caps on overflow | No overflow pattern observed | Overflow escalation rate > threshold | §4 |

---

## Triggered — don't build speculatively

| ID | Trigger | One-liner |
|---|---|---|
| **F-W2-A** | Second real capability ships and drift between migration blob and YAML matters | Log a diff when `SeedCapabilityLoader` overwrites migration-seeded rows, or make migration placeholder-only and let loader supply semantics. |
| **F-W3-E** | Concurrent-user scale (currently single user) | `AutomationConfirmationView.on_message` holds a 30-min timeout coroutine. Convert to fire-and-forget `asyncio.create_task` for scale. |
| **F-W4-A** | User asks to scan all inbound mail instead of sender allowlist | `email_triage` unbounded-sender mode — different privacy + token cost profile. |
| **F-W1-A fix** | A production skill exhibits the drift pattern the Wave 2 test documents | Add correction-cluster fast path + EOD digest mechanism; or replace Wilson CI on binarized scores with a continuous-score drift detector. |
| **Dashboard threshold tune** | ≥30 days of live data in `invocation_log` post-production enablement | Tune `quality_score.{critical,warning}_threshold` in `config/dashboard.yaml`. |
| **Admin auth** | `/admin/*` is ever exposed outside the loopback | Implement the auth note documented in `docs/domain/management-gui/index.md`. |
| **Wave 1 sub-items** | | |
| • `web_search` | Productionize `prep_research` against real tasks | Deferred tool for `prep_research` capability. |
| • `notes_read` | A notes storage module exists | Deferred tool for `extract_preferences` capability. |
| • `fs_read` | A capability concretely needs read-only FS access | Deferred generic filesystem read tool. |

---

## OOS items (deferred by original spec §2)

These were deliberately scoped out with explicit trigger conditions. Do not build without the trigger.

- **OOS-1** Event-triggered automations (`on_event`). *Trigger:* 3+ automations clearly need it.
- **OOS-2** Per-capability challenger runbooks. *Trigger:* 6 months of challenger-usage data.
- **OOS-3** Automation composition (chains / DAG). *Trigger:* real use case.
- **OOS-4** Step-level shadow comparison. *Trigger:* evolution quality poor across 5+ skills.
- **OOS-5** Logprob-based confidence. *Trigger:* self-assessed confidence proves uncorrelated.
- **OOS-6** Multiple skills per capability. *Trigger:* demonstrated need for divergent implementations.
- **OOS-7** Automation sharing / templates. *Trigger:* second real user exists.
- **OOS-8** Auto `requires_human_gate` flagging from sensitive tools. *Trigger:* manual flagging misses.
- **OOS-9** If-conditionals in the skill DSL. *Trigger:* 3+ skills need branching.
- **OOS-10** Nested DSL primitives (`for_each` inside `for_each`). *Trigger:* a skill needs nesting that can't be flattened.
- **OOS-11** Exact tokenization for local context budgeting. *Trigger:* `context_overflow_escalation` rate > 10%. (See in-code note at `src/donna/models/tokens.py`.)
- **OOS-12** Voice-triggered challenger interactions. *Trigger:* voice UX is prioritized.

See the archived tracker for full design notes on each OOS item.

Slice 11 (Flutter Web + Android app) is tracked separately in `slices/slice_11_flutter_ui.md` and lives in its own repo (`donna-app`); it is not part of this backlog.

---

## Summary table

| Bucket | Items | Priority |
|---|---|---|
| Critical gaps | 2 items (G-1, G-2) | P1 |
| Partial implementations | 9 items (G-10 – G-18) | P2 |
| Deferred / Phase 6 | 10 items (G-20 – G-29) | P2 |
| Triggered (deferred) | 9 items | P2 |
| OOS (triggered by spec) | 12 items | P2 |

Historical closed items (Waves 1–5): see the archived tracker.
