# Skill System — Open Backlog

**Date:** 2026-04-21 (revised)
**Scope:** Only genuinely-open items. Closed/shipped follow-ups (including Waves 1, 3, and 4 from the prior revision of this doc) live in the archived historical tracker: `docs/superpowers/followups/archive/2026-04-16-skill-system-followups.md`.

This doc is structured as **sequential waves** — pick one, do it, check in. Items under *Triggered* and *OOS* are deferred with explicit trigger conditions; don't build speculatively.

---

## Priority legend

- **P0** — Blocks `skill_system.enabled=true` in production.
- **P1** — Unlocks meaningful value; do after P0.
- **P2** — Deferred with a named trigger; don't build speculatively.
- **P3** — Exploratory / polish.

---

## Waves

### Wave 5 — Polish sweep  *(P3, as-convenient)*

**Goal:** Clear the low-priority items in one sitting.

**Scope:**
1. `config/dashboard.yaml:6` — evaluate + tune the `quality_score` thresholds once more production data has accumulated. Trigger: ≥30 days of live data in `invocation_log` post-production enablement.
2. `docs/domain/management-gui.md:36` — add a note describing what auth *would* look like if `/admin/*` is ever exposed externally. No implementation. **(Still outstanding — current line 36 only says "Auth will be added in a future session if needed.")**
3. Token counting — swap `len(prompt) // 4` heuristic in `src/donna/models/tokens.py:20` for an Ollama `/api/tokenize` call **only** when `context_overflow_escalation` rate > 10% (keep the trigger).
4. Slice 11 Flutter UI — tracked in `slices/slice_11_flutter_ui.md`; it's a separate-repo track (`donna-app`) and doesn't live in this backlog.

**Acceptance:** Each item either resolved or explicitly deferred with its trigger restated.

**Verification:** `pytest` passes; updated files read cleanly.

---

## Triggered — don't build speculatively

| ID | Trigger | One-liner |
|---|---|---|
| **F-W2-A** | Second real capability ships and drift between migration blob and YAML matters | Log a diff when `SeedCapabilityLoader` overwrites migration-seeded rows, or make migration placeholder-only and let loader supply semantics. |
| **F-W3-E** | Concurrent-user scale (currently single user) | `AutomationConfirmationView.on_message` holds a 30-min timeout coroutine. Convert to fire-and-forget `asyncio.create_task` for scale. |
| **F-W4-A** | User asks to scan all inbound mail instead of sender allowlist | `email_triage` unbounded-sender mode — different privacy + token cost profile. |
| **F-W1-A fix** | A production skill exhibits the drift pattern the Wave 2 test documents | Add correction-cluster fast path + EOD digest mechanism; or replace Wilson CI on binarized scores with a continuous-score drift detector. |
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
- **OOS-11** Exact tokenization for local context budgeting. *Trigger:* `context_overflow_escalation` rate > 10%.
- **OOS-12** Voice-triggered challenger interactions. *Trigger:* voice UX is prioritized.

See the archived tracker for full design notes on each OOS item.

---

## Summary table

| Wave | Bucket | Items | Priority |
|---|---|---|---|
| 5 | Polish sweep | 4 small items (only item 2 currently unresolved; 1/3/4 explicitly deferred) | P3 |
| — | Triggered (deferred) | 7 items | P2 |
| — | OOS (triggered by spec) | 12 items | P2 |

Historical closed items (Waves 1–4): see the archived tracker.
