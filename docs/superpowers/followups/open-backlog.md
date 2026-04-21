# Skill System — Open Backlog

**Date:** 2026-04-21
**Scope:** Only genuinely-open items. Closed/shipped follow-ups (F-1, F-2, F-3, F-5, F-6, F-7, F-9, F-10, F-11, F-14, F-W1-B–H, F-W2-B–G, F-W3-A–K, F-W4-B, C, D, F, G, I, J, K, L) and Wave-N Completed sections live in the archived historical tracker:
`docs/superpowers/followups/archive/2026-04-16-skill-system-followups.md`.

---

## Priority legend

- **P0** — Blocks `skill_system.enabled=true` in production.
- **P1** — Unlocks meaningful value; do after P0.
- **P2** — Deferred with a named trigger; don't build speculatively.
- **P3** — Exploratory / polish.

---

## Genuinely open

### F-4 — Dashboard UI for skill system + automations  *(P1, large, separate track)*
- **Gap:** `donna-ui/src/pages/` has Dashboard / Configs / Tasks / Shadow / LLMGateway / Logs / Preferences / Agents / Prompts / DevPrimitives, but **no skill, automation, skill-run, draft, or evolution pages**. JSON routes exist at `/admin/skills`, `/admin/skill-candidates`, `/admin/skill-drafts`, `POST /skills/{id}/state`, etc. — no screens consume them.
- **Unblocks:** AS-3.3 (approve draft), AS-4.2 (reset baseline), AS-4.3 (approve evolution), `requires_human_gate` toggle, automation CRUD, run history browsing. Also unblocks **F-W4-E**.
- **Notes:** Needs its own design cycle — propose 2–3 UI approaches before writing a plan.

### Tool-registration wave  *(P1, pairs with F-13)*
- **Gap:** 4 claude-native task-type capabilities (`generate_digest`, `prep_research`, `task_decompose`, `extract_preferences`) are seeded in `config/capabilities.yaml:100-135` but the tools they reference (`calendar_read`, `task_db_read`, `cost_summary`, `web_search`, `email_read`, `notes_read`, `fs_read`) are not in `src/donna/skills/tools/__init__.py:31-56` (registry currently holds only `web_fetch`, `rss_fetch`, `html_extract`, `gmail_search`, `gmail_get_message`).
- **Effect:** Seeded capabilities inert until this lands.

### F-W1-A — DegradationDetector threshold semantics  *(P2, needs verification)*
- `src/donna/skills/degradation.py:37,99,116` — detector exists and reads `degradation_agreement_threshold`. Original concern was binary-classification on continuous agreement scores never triggering degradation for mid-confidence drift.
- Needs deeper review to confirm whether the semantics were actually fixed or only the threshold plumbing was wired.
- Workaround today: correction-cluster fast path + EOD digest.

---

## Partial — shipped but gap remains

### F-12 — Grafana skill-system panels  *(P2)*
- Generic Grafana dashboards exist: `docker/grafana/dashboards/{error_exploration,task_pipeline,llm_cost,system_health}.json`.
- Missing skill-specific panels: skill state distribution over time, evolution success rate per skill, nightly-cron outcomes, cost breakdown by skill-system task type.

### F-13 — Migrate Claude-native task types to capabilities  *(P2)*
- 4 task-type capabilities seeded (see Tool-registration wave above).
- Blocked on tool registration.

---

## Triggered — don't build speculatively

| ID | Trigger | One-liner |
|---|---|---|
| **F-W2-A** | Second real capability ships and drift between migration blob and YAML matters | Log a diff when `SeedCapabilityLoader` overwrites migration-seeded rows, or make migration placeholder-only and let loader supply semantics. |
| **F-W3-E** | Concurrent-user scale (currently single user) | `AutomationConfirmationView.on_message` holds a 30-min timeout coroutine. Convert to fire-and-forget `asyncio.create_task` for scale. |
| **F-W4-A** | User asks to scan all inbound mail instead of sender allowlist | `email_triage` unbounded-sender mode — different privacy + token cost profile. |
| **F-W4-E** | After F-4 lands | Surface per-run `meta.*` diagnostics in the dashboard. |

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

## Polish (low priority, accurate but not urgent)

- `config/dashboard.yaml:6` — "TODO: evaluate these thresholds with more test data" (tune once more data accumulates).
- `docs/management-gui.md:36` — `/admin/*` auth intentionally deferred (dev-tool posture); revisit if exposed externally.
- Token counting uses `len(prompt) // 4` heuristic; swap for Ollama `/api/tokenize` once `context_overflow_escalation` matters.
- **Slice 11 — Flutter UI** (`slices/slice_11_flutter_ui.md:73-86`): separate `donna-app` repo, not started. 11 unchecked acceptance criteria covering auth / dashboard / kanban / calendar / cost / FCM / Android build.

---

## Summary table

| Bucket | Count |
|---|---|
| Genuinely open (P1) | 3 |
| Needs verification | 1 |
| Partial (shipped but gap) | 2 |
| Triggered (P2) | 4 |
| OOS (P2, triggered) | 12 |
| Polish (P3) | 4 |

Historical closed items: see the archived tracker.
