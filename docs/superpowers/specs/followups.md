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

## S19 — Frontend route uses `/escalations`, spec says `/admin/escalations`

- **Surfaced by:** `slices/slice_19_dashboard_escalation_workspace.md`
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§6.3(b)`
- **Status:** resolved-in-slice-19
- **Decision / Reasoning:** Spec §6.3(b) reads "New dashboard area at
  `/admin/escalations`", but every existing donna-ui page lives at a
  root-level path (`/skill-system`, `/shadow`, etc.) — `/admin/*` is the
  *backend API* convention, not the SPA route convention. Slice 19
  follows the existing UI convention: SPA routes are `/escalations` and
  `/escalations/<correlation_id>`; the backend endpoints stay at
  `/admin/escalations*` per spec §5.2/§5.3. Discord notification deep
  links (slice 20) will use `/escalations/<correlation_id>` to match.
- **Follow-up:** Spec §6.3(b) updated in this slice's PR.

---

## S19 — `escalation_request` columns added that §8 didn't enumerate

- **Surfaced by:** `slices/slice_19_dashboard_escalation_workspace.md`
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§8`,
  `#§5.2`, `#§5.3`
- **Status:** resolved-in-slice-19
- **Decision / Reasoning:** §5.2 and §5.3 reference
  `escalation_request.prompt_body` (TEXT) inline, but §8's `CREATE TABLE`
  block only listed `prompt_path` (workspace path for claude_code mode).
  Slice 17's migration (`c7d8e9f0a1b2`) faithfully implemented §8 and
  therefore also missed `prompt_body`. Slice 19 ships migration
  `d8e9f0a1b2c3_escalation_workspace_columns.py` adding `prompt_body`,
  `summary` (the Discord-summary text), `mode` (the chosen manual mode
  for fast filtering), `result` (JSON of the submission payload), and
  `validation_result` (JSON for the validation panel). §8 updated in this
  PR to enumerate all five columns.
- **Follow-up:** None. Slices 20 and 21 will populate `prompt_body`,
  `summary`, and `validation_result` as part of their own scope.

---

## S19 — `mode` column duplicates `resolution`

- **Surfaced by:** `slices/slice_19_dashboard_escalation_workspace.md`
  (self-review of the submit endpoint and the new column set).
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§8`
- **Status:** open
- **Decision / Reasoning:** Slice 19 added `escalation_request.mode` as
  a discriminator the dashboard and submit endpoint can match on
  (`chat` | `claude_code` | NULL). But §8 already has `resolution` whose
  value covers the same space — when a Discord button picks "Manual
  handoff" the orchestrator writes `resolution = chat | claude_code`.
  These are two sources of truth: a future writer that updates one and
  not the other will produce silent drift, and the submit endpoint's
  mode-match logic could disagree with the row's `resolution`. Slice 19
  shipped both columns to unblock the dashboard, but the cleaner model
  is one of:
    1. Drop `mode`; derive at read time as
       `mode = resolution if resolution in ('chat','claude_code') else NULL`.
    2. Keep `mode` and add a CHECK / trigger that asserts
       `mode IS NULL OR mode = resolution`.
- **Follow-up:** Resolve in slice 20 or slice 21 — those slices add the
  first writers that set `resolution` and `mode` together, so the
  divergence cost is concrete then. Until then, slice 17/18 callers do
  not write `mode`, and slice 19's submit endpoint backfills `mode` from
  the payload only when NULL — so today the columns can't disagree.

---

## S19 — Submission endpoint URL contract

- **Surfaced by:** `slices/slice_19_dashboard_escalation_workspace.md`
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§5.2`,
  `#§9` (`schemas/escalation_submission.json`)
- **Status:** resolved-in-slice-19
- **Decision / Reasoning:** Spec §5.2 names
  `/admin/escalations/<correlation_id>/submit` for the dashboard POST.
  Slice 19 implements it exactly as written, with the JSON body
  validated against `schemas/escalation_submission.json` (a discriminated
  oneOf on `mode`: `chat` carries `answer` ≥ 50 chars; `claude_code`
  carries `branch` plus optional `sha`). The endpoint is mode-agnostic;
  slices 20 and 21 attach the mode-specific UI controls (textarea vs
  "Mark as built" modal) but POST the same payload here.
- **Follow-up:** None.

---

## S20 — Vault-name redaction in chat-mode prompts

- **Surfaced by:** `slices/slice_20_chat_mode.md` (open question 6).
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§5.2`,
  `#§10.8`.
- **Status:** wontfix-by-spec
- **Decision / Reasoning:** Spec §10.8 explicitly accepts raw delivery
  ("send raw") with the trust boundary being the owner-DM Discord channel.
  Slice 20 ships the rendered prompt body as-is, both inline as the
  Discord summary and as a `.md` attachment. Building a vault-name
  redaction step would conflict with the documented design and slow the
  hot path with extra LLM calls.
- **Follow-up:** Revisit if multi-user lands and the spec's privacy
  boundaries change. Track under §10.8 if the assumption inverts.

## S20 — Re-escalation textarea pre-fill deferred

- **Surfaced by:** `slices/slice_20_chat_mode.md` (open question 7).
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§5.2`,
  `#§10.4` row 1.
- **Status:** open (UX polish)
- **Decision / Reasoning:** When iteration > 1, the spec is silent on
  whether the dashboard textarea should pre-fill with the prior answer.
  Slice 20 ships an empty textarea on every render; the slice 19 SPA
  already has the row's last `result` available via `GET
  /admin/escalations/<cid>` so a future PR can flip this on without any
  backend changes.
- **Follow-up:** Schedule for slice 23 (dashboard runtime overrides) or
  whenever the SPA next gets a UX pass.

## S20 — Manual handoff button mode disambiguation

- **Surfaced by:** `slices/slice_20_chat_mode.md`
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§4`
- **Status:** resolved-in-slice-20
- **Decision / Reasoning:** Pre-slice-20, the four-button view shipped
  a "Manual handoff" button that hard-coded ``mode='manual'``. The gate's
  `_coerce_mode` rejected `manual` and silently downgraded to `pause`,
  which was harmless under slice 17's "Pause / Cancel only" surface but
  broke chat-mode resolution. Slice 20 picks the specific mode (`chat`
  for now, `claude_code` for slice 21) at view-construction time based
  on the row's ``offered_modes`` so the button click resolves the row
  to the correct terminal state.
- **Follow-up:** Slice 21 will add the `claude_code` branch to
  `_pick_manual_mode` (already a no-op stub) and `BudgetEscalationView`
  rendering picks it up automatically.

## S20 — Chat-mode submit shares HTTP path with slash command

- **Surfaced by:** `slices/slice_20_chat_mode.md`
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§5.2`,
  `#§10.10`.
- **Status:** resolved-in-slice-20
- **Decision / Reasoning:** Slice 19's `submit_escalation` HTTP handler
  contained validation + audit + optimistic-lock logic that slice 20's
  `/donna_submit` slash command needed verbatim. Rather than duplicate
  it, slice 20 lifted the body into
  :func:`donna.cost.escalation_submit_service.apply_submission` so both
  surfaces share the exact path. The HTTP handler is now a thin wrapper
  that translates :class:`SubmissionError` codes to FastAPI HTTP
  responses; the slash command translates them to ephemeral Discord
  replies.
- **Follow-up:** None.

---

## S20-FU1 — `EscalationRequestRow` missing slice 19 columns

- **Surfaced by:** slice 20 self-review (`slices/slice_20_chat_mode.md`).
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§5.2`,
  `#§8`.
- **Status:** resolved-in-slice-20-followup
- **Decision / Reasoning:** Slice 19 added the workspace columns
  (`prompt_body`, `summary`, `mode`, `prompt_path`, `result`,
  `validation_result`, `branch_name`) to the `escalation_request`
  table but the `EscalationRequestRow` dataclass in
  `donna.cost.escalation_repository` was never extended. The cli_wiring
  delivery callback used `getattr(row, "summary", None)` and
  `getattr(row, "prompt_path", None)`, both of which silently returned
  `None` in production — meaning chat-mode notifications shipped the
  legacy "Over-budget decision" body without the Ollama summary or the
  `.md` attachment, defeating slice 20's whole user-facing surface. The
  follow-up fixes the dataclass + `_row_to_request` to populate these
  fields and the integration test now exercises the full pipeline so a
  similar drift cannot reach production again.
- **Follow-up:** None.

## S20-FU2 — Conversation engine doesn't pass `estimate_usd` or catch `EscalationDecisionError(mode='chat')`

- **Surfaced by:** slice 20 self-review.
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§5.2`
  (chat mode protocol — currently only reachable for callers that pass
  `estimate_usd`).
- **Status:** open (upstream wiring; out of slice 20 scope)
- **Decision / Reasoning:** `donna.chat.engine.handle_escalation` calls
  `router.complete(task_type="chat_escalation")` without
  `estimate_usd`, so the over-budget gate never fires for the most
  natural production path that would trigger chat mode. Even if the
  estimate were threaded through, the conversation engine doesn't
  catch `EscalationDecisionError(mode='chat')` — the exception would
  surface to the user as the generic "Something went wrong" branch
  rather than a "Donna is asking you to answer this externally — see
  #donna-tasks" message. Both fixes belong in a conversation-engine
  PR with proper estimate plumbing and chat-state UX, not in slice 20.
- **Follow-up:** Schedule a small upstream PR that (a) plumbs an
  estimator into `ConversationEngine.handle_escalation`, and
  (b) catches `EscalationDecisionError` with mode-aware messaging.
  Probably best paired with the slice 24 hardening pass.

## S20-FU3 — Workspace path fallback when `DONNA_WORKSPACE_PATH` is unset

- **Surfaced by:** slice 20 self-review.
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§5.2`,
  `#§6.1`.
- **Status:** open (low priority)
- **Decision / Reasoning:** `ChatPromptBuilder._resolve_workspace_root`
  falls back to `<project_root>/var/workspace` when
  `DONNA_WORKSPACE_PATH` is unset, which keeps tests + dev boots
  functional but contradicts the spec's "always under
  `${DONNA_WORKSPACE_PATH}`" wording. In production the env var is
  always set (see `donna.setup.validators`). Either document the
  fallback in §5.2 / §6.1 or fail fast at boot when the env var is
  absent.
- **Follow-up:** Pick one of: (a) add a single-sentence note to §5.2
  describing the dev fallback, or (b) drop the fallback and require
  the env var at builder construction. (a) is lower-risk; (b) is
  cleaner but breaks the existing tests that don't set the env.

## S20-FU4 — Summarizer template not loaded through the router cache

- **Surfaced by:** slice 20 self-review.
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§5.2`,
  `#§9`.
- **Status:** open (cosmetic)
- **Decision / Reasoning:** `ChatPromptBuilder._render_summary_prompt`
  uses a transient `jinja2.Environment` to render the summarizer
  template instead of going through `router.get_prompt_template`'s
  cache + Jinja machinery the way `_render_prompt_body` does for
  `chat_question.md`. Functionally identical; aesthetically
  inconsistent. Refactor to either go through the router for both, or
  cache the summary template the same way the chat-question template
  is cached locally.
- **Follow-up:** Small refactor; bundle into the next slice that
  touches `ChatPromptBuilder`.

---

## S21 — `human_review_request` table vs reuse `escalation_request`

- **Surfaced by:** `slices/slice_21_claude_code_mode.md`
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§12` Q3,
  `#§10.4` row 2.
- **Status:** resolved-in-slice-21
- **Decision / Reasoning:** §12 Q3 asked whether to add a separate
  `human_review_request` table or reuse `escalation_request` for the
  iteration-cap-reached case. We reused `escalation_request` and added
  a `human_review` BOOLEAN column. The dashboard already projects all
  needed fields; a separate table would have meant a polymorphic queue
  with no win for slice 21 / 22 (tools also reuse this protocol). When
  Phase 2 introduces other intervention types, the polymorphic-queue
  decision can be revisited based on actual usage.
- **Follow-up:** None for slice 21. Slice 22 (tools) reuses the same
  flag. If Phase 2 adds non-skill / non-tool human-review surfaces, an
  ADR can split the flag into a dedicated table.

## S21 — Local-only branch read access via env-var mount

- **Surfaced by:** `slices/slice_21_claude_code_mode.md`
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§12` Q2,
  `#§5.3`.
- **Status:** resolved-in-slice-21
- **Decision / Reasoning:** §12 Q2 asked whether the orchestrator has a
  mount on the host repo's `.git` for branch-not-pushed verification.
  Slice 21 adds a new `DONNA_HOST_REPO_PATH` env var that points at a
  read-only mount of the host repo. The poller uses
  `git rev-parse` / `git diff --name-only` / `git show` against this
  mount — never writes. If the env var is unset or the path isn't a
  git repo, claude_code mode is disabled (logged once, fail-soft) and
  only `chat` / `pause` / `cancel` buttons render. Documented in
  `docker/.env.example` (slice 21 update) and `SETUP.md`.
- **Follow-up:** None.

## S21 — Re-escalation parent-chain depth limit

- **Surfaced by:** `slices/slice_21_claude_code_mode.md`
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§12` Q5.
- **Status:** open
- **Decision / Reasoning:** Slice 21 left `parent_escalation_id` in
  place but did not add a depth limit. The iteration cap on a single
  escalation already bounds the inner loop; cross-row chains haven't
  been observed yet. Slice 24 (escalation hardening) is the right
  place to introduce a cap once we have data on real chains.
- **Follow-up:** Slice 24 — add `max_re_escalation_depth` and reject
  new escalations whose ancestor chain exceeds it.

## S21 — `originating_entity_*` columns added to `escalation_request`

- **Surfaced by:** `slices/slice_21_claude_code_mode.md`
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§8`.
- **Status:** spec-update-pending
- **Decision / Reasoning:** §8 enumerates `task_id` as the FK to the
  originating row but every claude_code call site (auto_drafter,
  evolution) passes `task_id=None`. We added explicit
  `originating_entity_type` + `originating_entity_id` columns so the
  diff validator can render `{name}`-substituted target_paths globs
  without inferring identity. §8 needs an "Added by slice 21" note.
- **Follow-up:** Spec §8 already updated in this slice's PR.

## S21 — Manual claude_code lifecycle ends in `sandbox`, not `draft`

- **Surfaced by:** `slices/slice_21_claude_code_mode.md`
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§5.3`,
  `spec_v3.md#§23.4`.
- **Status:** resolved-in-slice-21
- **Decision / Reasoning:** AutoDrafter ends a generated skill in
  `draft` and requires a separate human approval to enter `sandbox`.
  Manual `claude_code` mode treats the user's "Mark as built" click +
  passing fixtures as the explicit human gate (`reason='human_approval'`,
  `actor='user'`, `actor_id=<discord_id>`), so it lands the skill one
  hop deeper at `sandbox`. From `sandbox` the existing automatic
  promotion gates take over.
- **Follow-up:** None. `docs/domain/skill-system.md` "Manual escalation"
  subsection updated to describe this asymmetry.

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
