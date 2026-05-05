# Spec Follow-ups & Drift Log

> **Purpose:** Running log of known gaps, drifts, and deferred decisions across
> the slice-driven build. Each entry names the slice that surfaced the issue,
> the relevant spec section, the decision (if made), and the proposed
> follow-up.
>
> This file does **not** replace updating the canonical specs. When behaviour
> changes, the affected `§` of the relevant spec doc must be updated in the
> same PR per `CLAUDE.md`. This log captures items that are either *deferred*
> (intentional, scheduled for a later slice) or *known gaps* (called out so
> reviewers and future implementers can find them).
>
> **For future slices:** When you finish a slice, scan your work for spec
> drift, deferred decisions, or gaps you accepted as out-of-scope. Add an
> entry here following the format below. Updating this file does not exempt
> you from updating the canonical spec when behaviour diverges from it.

---

## Format

For each entry use:

```
### Sxx — short title

- **Surfaced by:** slice `slices/slice_xx_<name>.md`
- **Spec section(s):** `<doc>#§N.M`
- **Status:** open | resolved-in-slice-yy | wontfix | spec-update-pending
- **Decision / Reasoning:** What was decided and why.
- **Follow-up:** What still needs to happen, and where it is scheduled.
```

Append in slice order. Resolved entries stay in the log so the trail is
visible.

---

## S18 — Buttons "omitted" instead of "disabled" when over ceiling

- **Surfaced by:** `slices/slice_18_budget_extension.md`
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§5.1`,
  `#§10.6` (rows 2 and 5).
- **Status:** spec-update-pending
- **Decision / Reasoning:** Spec wording says *"button disabled if remaining
  headroom < estimate"* and *"All extension buttons disabled. Discord message
  reads 'Monthly cap. Pause / Cancel only.'"*. The implementation in
  `EscalationGate._should_offer_extension()` instead **omits** the
  `api_extended` mode from `offered_modes`, so the button is never rendered.
  Functionally identical from the user's perspective — they cannot approve
  an extension over ceiling — but the explanatory "Monthly cap" text is
  missing from the Discord message.
- **Follow-up:** Either (a) update §10.6 to describe the rendered
  `offered_modes` mechanism, or (b) add an explicit "Monthly cap. Pause /
  Cancel only." string to the Discord summary when extensions are filtered
  out at render time. Preferred: (b), small UX win and keeps the spec
  literal. Schedule for Slice 19 (dashboard escalation detail) or earlier
  if a UX fix is requested.

---

## S18 — Crash-recovery uses `invocation_log` absence, not `task_status`

- **Surfaced by:** `slices/slice_18_budget_extension.md`
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§10.6`
  (row 4).
- **Status:** spec-update-pending
- **Decision / Reasoning:** Spec says *"scan `escalation_request WHERE
  resolution='api_extended' AND task_status NOT IN ('completed','failed')`"*.
  No `task_status` column exists on `escalation_request`; that column lives
  on `tasks`, but a granted extension is not always tied to a `task` row
  (e.g. drafting paths). My `BudgetExtensionRepository.find_stale_grants()`
  uses `LEFT JOIN invocation_log ... WHERE il.id IS NULL` (excluding
  `escalation_lifecycle` rows) as a proxy: "the extension was granted but
  the actual API call never landed an invocation_log row." This works for
  the actual recovery objective (don't leave phantom headroom across
  restarts) and is uniform across drafting and task-bound paths.
- **Follow-up:** Update §10.6 row 4 to describe the `invocation_log`
  presence check rather than `task_status`. No code change needed.

---

## S18 — Crash recovery only rolls back; no resume

- **Surfaced by:** `slices/slice_18_budget_extension.md`
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§10.6`
  (row 4) — *"resume or rollback the extension"*.
- **Status:** open (deferred to later slice)
- **Decision / Reasoning:** The recovery path always voids stale grants and
  emits an `extension_voided` audit event. "Resume" — re-running the
  deferred task with the existing extension — is not implemented because
  (a) there is no durable record of the prompt to resume in the current
  slice scope (prompts live on the caller's stack), and (b) auto-resume
  after a crash carries the risk of running stale work. Voiding is
  conservative: the user re-approves on the next attempt.
- **Follow-up:** When the manual-handoff slices land (`chat`/`claude_code`
  modes — Slices 20, 21), the prompt body will be persisted in
  `escalation_request.prompt_body`. At that point a resume path becomes
  feasible. Decide then whether to add it; spec wording can stay as-is
  ("resume or rollback") with this log explaining the current behaviour.

---

## S18 — Re-escalation parent chain not wired

- **Surfaced by:** `slices/slice_18_budget_extension.md`
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§12`
  (open question 5), `#§11` (acceptance: "Truncated output triggers
  re-estimate + re-escalation").
- **Status:** open (open spec question)
- **Decision / Reasoning:** When the extension token cap is hit,
  `ModelRouter.complete()` raises `TokenLimitReachedError`. The exception
  carries the original `escalation_request_id` and `correlation_id`, but
  there is no code path that re-calls `complete()` with a higher
  `estimate_usd` and a `parent_escalation_id` set. The "re-escalation"
  half of the spec acceptance criterion is therefore deferred to the
  caller — and no current caller does this, so a token-limited extension
  effectively fails the task.
- **Follow-up:** Tracked under spec §12 question 5 ("re-escalation parent
  chains"). Scope the answer when the manual-handoff slices land
  (Slices 20/21) — they introduce the same need for re-escalation chains
  on validation failure. Until then, a `TokenLimitReachedError` surfaces
  to the orchestrator's task error handler and the task transitions to
  `failed` with the `escalation_request_id` linked.

---

## S18 — Idempotency index is non-partial

- **Surfaced by:** `slices/slice_18_budget_extension.md` (plan vs migration
  drift).
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§8` —
  schema mentions FK only, no index spec.
- **Status:** resolved-in-slice-18
- **Decision / Reasoning:** The plan called for
  `WHERE escalation_request_id IS NOT NULL` to allow rows with NULL FKs
  (e.g. dashboard-issued grants). The Alembic revision
  `e2f3a4b5c6d7_budget_extension_mode.py` creates a non-partial unique
  index. SQLite treats NULLs as distinct in unique constraints, so
  multiple NULL-FK rows never conflict — equivalent behaviour to a
  partial index for this column. No follow-up.

---

## How to add an entry (template)

Copy this when you finish a slice:

```
### S<NN> — <short title>

- **Surfaced by:** `slices/slice_<NN>_<name>.md`
- **Spec section(s):** `<path/to/spec.md>#§<N.M>`
- **Status:** open | resolved-in-slice-<NN> | wontfix | spec-update-pending
- **Decision / Reasoning:** <2–4 sentences>
- **Follow-up:** <what to do next, and where>
```

Resolved entries are kept (do not delete) so the trail of decisions is
visible to future readers.
