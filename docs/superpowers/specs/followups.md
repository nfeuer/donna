# Spec Follow-ups — Open Items

> **Purpose:** Spec-level cross-slice follow-ups — implementation questions, spec drift, and deferred decisions discovered during the slice-driven build.
> Closed items archived in [`archive/followups-closed-slices.md`](archive/followups-closed-slices.md).
> **Related:** [`open-backlog.md`](../../superpowers/followups/open-backlog.md) tracks *feature gaps* (missing/incomplete features with stable G-\* IDs). This file tracks *spec questions*; that file tracks *feature gaps*.

---

## TI-FU1 — Urgency/deadline classification moved off the Challenger path

- **Spec:** `spec_v3.md §7.1.1` / `§7.2`; design `2026-06-05-challenger-and-scheduling-intake-design.md`
- **Status:** spec-update-pending
- **Gap:** Plan 1 (time-intent foundation, 2026-06-06) moved *when* a task happens to the
  input parser (`time_intent`) + a deterministic `routing_gate`, and stopped the
  AutoScheduler from deferring time-bound tasks for the Challenger. The Challenger no longer
  gates scheduling. `spec_v3.md §7.1.1/§7.2` still describe the Challenger as the pre-PM gate
  on the critical path — update when Plan 3 (Challenger off critical path) lands.

## TI-FU2 — `recurring` time-intent routing is a stub

- **Spec:** design `2026-06-05-challenger-and-scheduling-intake-design.md` §3
- **Status:** open (deferred to Plan 2/3)
- **Gap:** The routing gate returns `Route.AUTOMATION` for `kind="recurring"`, but the
  AutoScheduler only logs and skips — nothing yet creates a recurrence in the automation/cron
  pipeline. Also "recurring reminder" vs "automation that runs a capability" remains
  semantically muddy. Wire the handoff when the negotiation/constraint slice lands.

## S18 — Crash recovery only rolls back; no resume

- **Spec:** `manual-escalation.md#§10.6` row 4
- **Status:** open (deferred)
- **Gap:** Recovery always voids stale grants. "Resume" needs durable prompt storage, which landed with `prompt_body` column. Decide whether to add a resume path.

## S18 — Re-escalation parent chain not wired

- **Spec:** `manual-escalation.md#§12` Q5, `#§11`
- **Status:** open (spec question)
- **Gap:** `TokenLimitReachedError` carries IDs but no code path re-calls `complete()` with a higher estimate and `parent_escalation_id`. A token-limited extension effectively fails the task. Scope alongside S24 depth-limit residue.

## S19 — `mode` column duplicates `resolution`

- **Spec:** `manual-escalation.md#§8`
- **Status:** open
- **Gap:** Two sources of truth. Either drop `mode` (derive at read time) or add a CHECK asserting `mode = resolution`. Resolve when next slice writes both columns.

## S20 — Re-escalation textarea pre-fill deferred

- **Spec:** `manual-escalation.md#§5.2`, `#§10.4` row 1
- **Status:** open (UX polish)
- **Gap:** Dashboard textarea is empty on iteration > 1. SPA already has `result` from GET; frontend-only change to enable pre-fill.

## S20-FU2 — Conversation engine doesn't pass `estimate_usd`

- **Spec:** `manual-escalation.md#§5.2`
- **Status:** mostly-resolved-in-branch (`claude/awesome-allen-otrrka`, 2026-06-11)
- **Gap:** The "gate never fires without `estimate_usd`" half is **resolved**: the
  router now derives a deterministic cost floor when a caller omits `estimate_usd`
  (`ModelRouter._estimate_cost_floor`), so the gate is consulted on every call (Fable
  Wave A #1). The Fable critique found this affected *all* call sites, not just the
  conversation engine. **Still open:** `handle_escalation` doesn't catch
  `EscalationDecisionError(mode='chat')` — only reachable once `gate.mode: enforce`.

## S20-FU4 — Summarizer template not loaded through router cache

- **Spec:** `manual-escalation.md#§5.2`, `#§9`
- **Status:** open (cosmetic)
- **Gap:** `ChatPromptBuilder._render_summary_prompt` uses a transient Jinja env instead of `router.get_prompt_template`. Bundle into next `ChatPromptBuilder` touch.

## S21 — Re-escalation parent-chain depth limit

- **Spec:** `manual-escalation.md#§12` Q5
- **Status:** open (deferred from S21 and S24)
- **Gap:** No `max_re_escalation_depth` config. Iteration cap bounds inner loop; cross-row chains unobserved. Next behavioural gate slice adds `manual_escalation.triggers.max_re_escalation_depth` (default 5).

## S22 — Validation depth: lint + import-smoke only

- **Spec:** `manual-escalation.md#§10.4` row 4, `#§10.5`
- **Status:** open (deferred three times: S22, S24, S24 audit)
- **Gap:** `_validate_tool` does not re-run dependent-skill fixtures. Next skill validation infra slice adds the regression step.

## S22 — Iteration cap doesn't auto-reject linked tool_request

- **Spec:** `manual-escalation.md#§7`, `#§10.4` row 2
- **Status:** open (low priority)
- **Gap:** When iteration cap fires on `tool_request_fulfillment`, the linked `tool_request` stays `in_progress`. Non-blocking (dedup index is `WHERE status='open'`), but orphan lingers in dashboard.

## S22 — `_validate_tool` packs warnings into `failures` field

- **Spec:** `manual-escalation.md#§10.5` row 1
- **Status:** open (cosmetic)
- **Gap:** `ValidationOutcome` lacks a `warnings` field. Warnings packed into `failures` with `passed=True`. Add `warnings: list[dict]`.

## S22 — MorningDigest production wiring missing

- **Spec:** `manual-escalation.md#§7`
- **Status:** open (pre-existing)
- **Gap:** No production construction site for `MorningDigest`; digest is dead code. Activates when someone wires `NotificationTasks` in boot path with `ctx.tool_request_repository`.

## S24 — Audit residue: dependent-skill regression still deferred

- **Spec:** `manual-escalation.md#§10.4` row 4
- **Status:** open (deferred)
- **Gap:** Shadow-regression harness needs fixture-driven re-runs — non-trivial test infra. Next skill validation slice picks this up.

## S24 — Audit residue: re-escalation depth limit deferred

- **Spec:** `manual-escalation.md#§12` Q5
- **Status:** open (deferred)
- **Gap:** `max_re_escalation_depth` config + reject path is a product change. Iteration cap (default 3) keeps inner loop bounded; no cross-row chains observed. Next behavioural gate slice adds it.

## S24 — Audit residue: Twilio-mock E2E for Discord-5xx retry

- **Spec:** `manual-escalation.md#§11` row 2
- **Status:** open (deferred)
- **Gap:** Missing integration test: Discord-5xx -> timeout -> SMS-fanout. Components work in isolation; needs unified Discord+Twilio harness.

## S24 — Audit residue: re-estimate after overspend deferred

- **Spec:** `manual-escalation.md#§10.6` row 1
- **Status:** open (deferred)
- **Gap:** `complete()` hard token cap prevents over-spend, but the "re-estimate + re-escalation" path requires the model layer to surface `token_limit_exceeded` back to the gate. Future budget-hardening slice.

---

## Standalone Feature Follow-ups

### Discord Onboarding & DM Delivery

- **Spec:** `spec_v3.md#§28`, `docs/domain/notifications.md`
- **Status:** open
- **Deferred:** (1) Immich account linking, (2) profile update commands (email, phone), (3) DM routing for reminders/nudges, (4) companion app auth flow. Items 1-2 land with Flutter companion app; item 3 when users can opt in.

### `render_chat_prompt` tz not threaded

- **Spec:** `docs/domain/scheduling.md#timezone`
- **Status:** open (cosmetic)
- **Gap:** `ConversationEngine` calls `render_chat_prompt` in 4 places without passing `tz`. Falls back to `America/New_York` which is correct for now. Thread `tz` on next feature change.

### Calendar view — IN_PROGRESS tasks not shown

- **Spec:** `spec_v3.md#§4.4`
- **Status:** open
- **Gap:** `/calendar/week` only queries `SCHEDULED`. Tasks in `IN_PROGRESS` with `scheduled_start` should also appear. Extend endpoint with visual distinction.

### Event-driven corrections — uncovered call sites

- **Spec:** `spec_v3.md#§7.4`
- **Status:** open
- **Gap:** Several `update_task` calls lack `source=` tags (`/done`, `/reschedule`, `rename_task`, dedup merge). `CorrectionSubscriber` doesn't wire `cluster_detector`. `input_text` empty for event-driven corrections. `spec_v3.md` §9.1 still describes direct logging.

## S25 — Task parsing flipped to local-first + personal-context injection

- **Spec:** `spec_v3.md` model-routing and task-parsing sections (model layer §; parse pipeline §)
- **Status:** spec-update-pending
- **Gap:** `parse_task` now routes to `local_parser` (qwen2.5:32b) as primary with confidence-gated escalation to the cloud `reasoner` via a new `parse_task_cloud` route (threshold 0.7). The parse prompt gained calibrated duration anchors (15/30/60) and a `{{ personal_context }}` slot fed by vault notes + learned-preference rules. The `domain`/`estimated_duration` correction-learning loop was revived via the API (`PATCH /tasks/{id}`) and dashboard (`PATCH /admin/tasks/{id}`) edit pathways. `spec_v3.md` still describes cloud-first parsing with no local-first escalation or context injection — update the model-routing and parsing sections to match, and reconcile with the event-driven corrections follow-up above. Note: `confidence_threshold` (removed as dead config in the Fable Model-Layer wave) was re-added to `RoutingEntry` here as its consumer now exists.

### Fable Wave A (Cost & Escalation) — residue from the S1 trio

- **Spec:** `spec_v3.md#§13.1`, `manual-escalation.md#§4`, `#§10.6`; design
  `2026-06-11-cost-escalation-fable-critique-design.md`
- **Status:** open (deferred from the S1-trio branch `claude/awesome-allen-otrrka`)
- **Gap:** The S1 trio (router-side estimation #1, monthly cap #2, log-before-raise #3)
  shipped in **shadow** posture. Residual items:
  1. **Enforce flip pending calibration.** `gate.mode: shadow` is deployed. Flip to
     `enforce` once `escalation_shadow_would_fire` logs show the floor estimate is
     well-calibrated. *Trigger:* ≥14 days of shadow data, or first real overspend.
  2. **Monthly *increase* mechanism unwired.** `spec_v3.md §13.1` "Budget Increase
     Approved" should raise the monthly cap for the current month; no row/code exists,
     so the cap is the static `monthly_budget_usd`. Daily extensions count toward it.
  3. **Monthly-warning dedup is in-memory.** `BudgetGuard._warned_months` re-warns the
     debug channel after a restart (low harm). Persist (e.g. a sentinel audit row) if
     it gets noisy. *Trigger:* duplicate-warning complaint.
  4. **Per-call monthly aggregation.** `check_pre_call` now runs `get_monthly_cost`
     every call (plus the existing daily query). Fine at single-user volume; cache if
     call rate grows. *Trigger:* cost-query latency shows in p95.
  5. **#3 catcher dormant under shadow.** `TokenLimitReachedError` catchers were added
     to `auto_drafter` / `evolution`, but the raise is only reachable in `enforce`
     mode (needs a granted extension). Verify under load when #1 flips to enforce.

## NEG-A — Scheduling negotiation loop, Slice A (single-displacement, confirm-only)

- **Spec:** `spec_v3.md §6.1.2` (conflict table) / `§6.3` (Minimize Rescheduling / Get
  It Done); design `docs/superpowers/specs/2026-06-12-scheduling-negotiation-design.md`
- **Status:** spec-update-pending (intentional, accepted drift)
- **Gap / drift:** Slice A landed `Scheduler.negotiate_placement` /
  `negotiate_and_apply` / `_apply` (single displacement, cap 1), the
  `_iter_window_valid_slots` refactor of `find_next_slot`, the `negotiation_proposals`
  table + repo, `NegotiationProposalView`, `NOTIF_RESCHEDULE`, and the
  `auto_scheduler` `NoSlotFoundError` hook + gate (§1.2). **`spec_v3.md §6.1.2`
  licenses *silent* auto-moves for low-priority items, but the 2026-06-05
  confirmation invariant supersedes it: the loop ships propose-and-confirm by
  default (`auto_apply: false`).** Rewrite the §6.1.2 / `docs/domain/scheduling.md`
  conflict tables to describe confirm-by-default + the `auto_apply` dial-back when
  Slice B ships. **Deferred to later slices (NOT in Slice A):** `cascade_shift` +
  the overrun detector (Slice C), `auto_apply` of moves without confirmation +
  multi-displacement cap > 1 (Slice B). A latent `find_next_slot` quirk surfaced:
  it clamps only the slot *start* to the deadline, so a slot can end past the
  deadline; the negotiator adds a stronger `slot.end <= deadline` guard (hard-
  deadline-only), but `find_next_slot` itself keeps the pre-existing start-clamp
  behavior unchanged. Open owner decisions OD-1..OD-6 (design §8) were taken at the
  conservative defaults; revisit if accept-rate data warrants.

## SA-72 — Sub-Agent §7.2 resolution: keep the ideas, drop the framework

- **Spec:** `spec_v3.md §7.2`; design
  `docs/superpowers/specs/2026-06-17-subagent-72-resolution-design.md`; prior critique
  `2026-06-11-subagent-system-fable-critique-design.md` (decision #1, escalated)
- **Status:** R1 + R2 shipped (2026-06-17); **R3 remaining**
- **Gap / plan:** The dormant §7.2 pipeline's wire-or-delete decision is resolved as
  **keep-ideas/drop-framework**. **R1 (done)** delete the dispatch framework
  (`AgentDispatcher`, `PMAgent`, `SchedulerAgent`, the uniform `Agent` dispatch
  contract, the inert `AgentActivityFeed`, their tests) + rewrite `spec_v3.md §7.2`,
  `docs/domain/agents.md` and `orchestrator.md` to the live flow (Challenger →
  NoveltyJudge; AutoScheduler placement; Prep loop). **R1 keeps `config/agents.yaml`**
  — it is the *live* allowlist registry (challenger/research) behind the tool-lint +
  admin UI, not dead config; its dead `pm`/`scheduler` entries are reshaped in R3, not
  deleted in R1. **R2 (done)** salvaged `DecompositionService` as a direct service
  (principle #4) behind the `/breakdown` Discord command (`discord_commands.py`,
  injected from `cli_wiring`); the auto-threshold trigger is deferred (config-gated,
  future). **R3 (remaining)** make the tool-validation seam load-bearing (principle #6) — required caller
  identity, per-tool param JSON-schemas, `db` stripped from `AgentContext` — the real
  precondition for G-21/G-22. **Deferred:** acceptance-criteria packaging (PMAgent's
  only unique increment) folded into the live Challenger path; dropping the never-written
  `assigned_agent` column in a later cleanup migration (`agent_eligible`/`agent_status`
  stay — Prep uses them). `§7.2` carries a forward-pointer to the design doc until R1.
- **Known R1 doc residue (open — fold into R3 or a docs pass):** the **skill-system**
  docs still describe skill activation via the now-deleted `AgentDispatcher` and the
  `skill_routing_enabled` flag (both removed in R1) — `docs/domain/skill-system/setup.md`
  (§3 line 81, §4 wiring, §5.4 `dispatcher_skill_shadow`, §6 troubleshooting, §8
  checklist) and `index.md` (Current Status). Live skill routing is the `skill_executor`
  wired into the **automations dispatcher** (`automations/dispatcher.py`) via
  `cli_wiring.py`; `skill_routing_enabled` no longer exists in the source. Needs a proper
  reconciliation pass (trace the real activation + whether the `dispatcher_skill_shadow`
  event path survives) — not done in R1/R2 to avoid guess-rewriting a subsystem doc.
  Also verify whether `AgentApprovalView` (`discord_views.py`) is orphaned now that
  `AgentActivityFeed` was deleted.

## ML-FABLE-P2 — Shadow stable-state auto-disable job (design B)

- **Spec:** `spec_v3.md §4.4`; design
  `docs/superpowers/specs/2026-06-11-model-layer-fable-critique-design.md` (#3, design B)
- **Status:** open (trigger-gated)
- **Gap:** Phase-2 landed the shadow plumbing — shadow now routes through
  `complete(is_shadow=True)`, its spend is accounted on `invocation_log`
  (`is_shadow=1`), and a `shadow.enabled` config kill-switch gates it (default
  `false`). **Deferred:** the full statistical weekly job that auto-disables a
  shadow once the local model reaches a stable agreement/quality state (design B's
  "stable-state exit"). The config kill-switch + accounted spend are sufficient
  while shadow is off. **Trigger:** shadow actually enabled in prod
  (`shadow.enabled: true` for any alias) — at that point the doubled spend needs
  the automated exit so a monitoring window can't run unbounded.

---

## How to add an entry

```
### S<NN> — <short title>

- **Spec:** `<path/to/spec.md>#§<N.M>`
- **Status:** open | resolved-in-slice-<NN> | wontfix | spec-update-pending
- **Gap:** <2–3 sentences: what's missing and what to do>
```

Resolved entries go to the [closed archive](archive/followups-closed-slices.md).

---

- **2026-06-06 — Container health watcher.** ✅ RESOLVED 2026-06-10. Added
  `donna-healthwatch` sidecar + reciprocal orchestrator heartbeat monitor
  (observability). `spec_v3.md` reconciled: new §14.7 (Container Health
  Monitoring) + `health.*` event family in §14.4. Design doc:
  `docs/superpowers/specs/2026-06-05-container-health-watcher-design.md`.

## SKILL-FABLE — Skill-system critique residue (deferred findings)

- **Spec:** `spec_v3.md §23.3/§23.4`; design
  `docs/superpowers/specs/2026-06-11-skill-system-fable-critique-design.md`
- **Status:** open (trigger-gated / lower-urgency, intentionally out of scope for
  the safety-critical slice)
- **Gap:** The Wave-C critique slice implemented #1 (evidence loop), #2 (human-gate
  scoping), #3 (version-scoped gates), #5 (suppress removal + human_approval
  enforcement), #6 (sandbox/shadow gate rigor), #7 (skills-package alerting), and
  #10 (auto-draft human-gate default + doc/spec reconciliation). **#8 and #9 are now
  also implemented** (this slice). Still open:
  **#4** full dispatch-time tool-authorization intersection (step tools ∩ capability
  config grant, fail-closed) — trigger: first write-capable tool registers
  (`task_db_write`/`calendar_write`, §23.3 Stage 3); config-side `tools:`
  declarations completable now at zero risk.
  **#8 — DONE.** The `orchestrator/dispatcher.py` `_try_skill_shadow` trust-gate
  was **removed with the §7.2 dispatch framework in R1** (SA-72): the
  `AgentDispatcher` never ran in production, so the gate was dead defense-in-depth.
  The *live* skill-shadow gating remains in the automations dispatcher's
  `_decide_path` (`skill_row.state in ("shadow_primary", "trusted")`), so a
  DRAFT/sandbox skill still cannot run with real tools on the live path.
  **#9 — DONE.** The three evolution gates (`targeted`, `fixture_regression`,
  `recent_success`) no longer pass *silently* on an empty evidence set — the
  condition is tagged `no_evidence` and logged `fallback_activated`. Config
  `evolution_require_gate_evidence` (default **false**) additionally fails the
  gate closed; kept lenient-by-default because the targeted-case pipeline does
  not yet reliably populate evidence (flipping to true would block evolution of
  evidence-sparse skills). Trigger to flip the default: reliable fixture/targeted
  capture from successful runs.

## 2026-06-24 — Deploy snapshot resilience

- **Secrets out of the dev tree:** `donna-deploy.sh snapshot` overlays secrets
  (`docker/.env`, `config/google_credentials.json`, `config/token.json`,
  `docker/google_credentials.json`) from the repo working tree. Relocate to a
  dedicated secrets dir or `/mnt/donna/vault` so deploys don't read secrets from
  the IDE workspace.
- **Healthwatch alert gap:** `donna-healthwatch` ran throughout the 2026-06-22
  orchestrator crash loop (>18h) without paging. Investigate why and close.
- **Orchestrator startup guard (prevention #2):** as defense-in-depth behind the
  deploy-layer guard, have the orchestrator detect missing config at startup and
  emit a notification before exiting (ref `dispatch_fallback_alert`).
- **Self-sufficient snapshots for fresh hardware:** the snapshot excludes the image
  build context (`src/`, `pyproject.toml`, `alembic/`, `alembic.ini`), so a
  brand-new host with no cached image cannot rebuild the orchestrator from the
  snapshot, and `deploy` does not ship code changes. Decide whether to add those
  paths to `ARCHIVE_PATHS` and use `--build` on `deploy` (every deploy rebuilds —
  slower but fully reproducible) vs. keeping image builds a separate step.

## 2026-07-10 — Calendar OAuth permanence (fix/calendar-oauth-permanence)

- **Consent screen must be published to Production** (operator action, Google
  Cloud Console): the Testing-status 7-day refresh-token expiry is the root
  cause of the recurring calendar `invalid_grant`. Code-side hardening (typed
  `CalendarAuthError`, fallback alert on unavailable calendar, boot-time
  refresh-token probe in `SelfDiagnostic`) is merged, but the token will keep
  dying weekly until the app is published and re-linked once.
- **Gmail deserves the same treatment:** `_try_build_gmail_client` still
  degrades with a bare `logger.warning`, and `SelfDiagnostic` does not probe
  the Gmail token. Same disease, same fix; the consent-screen publication
  covers both, but the alerting/probe parity is open.
- **Spec drift note:** `spec_v3.md` §3.2.2 describes the calendar integration
  but not its failure/alerting contract; the new `CalendarAuthError` +
  fallback-alert behaviour is documented in `docs/operations/calendar-oauth.md`
  and should be folded into §3.2.2 on the next spec pass.

## 2026-07-10 — Output standard slice 1 (feat/output-standard)

- **Slice 2:** migrate reminders and the four proactive prompts onto
  `OutputRenderer` so every surface shares one voice (design
  2026-07-10-output-standard-design.md; today they format independently).
- **Slice 3:** consolidate the morning/EOD digest rendering onto the renderer
  and retire its bespoke embed construction.
- **SMS length:** renderer truncates at 1900 (Discord); SMS surfaces should get
  a tighter budget (~320 chars) once slice 2 touches multi-channel formats.

## 2026-07-10 — Local-LLM reliability (fix/local-llm-reliability)

- **Spec/doc touch-up:** `spec_v3.md` §4 (model layer) and
  `docs/domain/model-layer.md` describe Ollama's `format: json` mode; both
  should absorb the new config-gated structured-outputs contract
  (`ollama.structured_outputs` → per-task-type schema as `format`).
- **Next tuning candidates (from the 2026-07-10 capability review):**
  num_ctx 16384 + `OLLAMA_KV_CACHE_TYPE=q8_0`, and a qwen3:30b-a3b home-model
  trial gated on the scenario suites (fixtures/scenarios_*).
