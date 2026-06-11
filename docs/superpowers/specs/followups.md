# Spec Follow-ups — Open Items

> **Purpose:** Spec-level cross-slice follow-ups — implementation questions, spec drift, and deferred decisions discovered during the slice-driven build.
> Closed items archived in [`archive/followups-closed-slices.md`](archive/followups-closed-slices.md).
> **Related:** [`open-backlog.md`](../../superpowers/followups/open-backlog.md) tracks *feature gaps* (missing/incomplete features with stable G-\* IDs). This file tracks *spec questions*; that file tracks *feature gaps*.

---

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
- **Status:** open (upstream wiring)
- **Gap:** `handle_escalation` calls `router.complete()` without `estimate_usd`, so the over-budget gate never fires. Also doesn't catch `EscalationDecisionError(mode='chat')`. Needs small upstream PR.

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
- **Gap:** `parse_task` now routes to `local_parser` (qwen2.5:32b) as primary with confidence-gated escalation to the cloud `reasoner` via a new `parse_task_cloud` route (threshold 0.7). The parse prompt gained calibrated duration anchors (15/30/60) and a `{{ personal_context }}` slot fed by vault notes + learned-preference rules. The `domain`/`estimated_duration` correction-learning loop was revived via the API (`PATCH /tasks/{id}`) and dashboard (`PATCH /admin/tasks/{id}`) edit pathways. `spec_v3.md` still describes cloud-first parsing with no local-first escalation or context injection — update the model-routing and parsing sections to match, and reconcile with the event-driven corrections follow-up above.

---

## How to add an entry

```
### S<NN> — <short title>

- **Spec:** `<path/to/spec.md>#§<N.M>`
- **Status:** open | resolved-in-slice-<NN> | wontfix | spec-update-pending
- **Gap:** <2–3 sentences: what's missing and what to do>
```

Resolved entries go to the [closed archive](archive/followups-closed-slices.md).
