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
- **Status:** resolved-in-bucket-1
- **Decision / Reasoning:** Picked option (b) per the original
  preference: ``EscalationGate._should_offer_extension`` now delegates
  to a new ``_extension_filter_reason`` that returns one of
  ``'disabled' | 'over_headroom' | 'over_ceiling' | None``. The public
  ``EscalationGate.extension_filter_reason`` accessor is consumed by
  ``donna.cli_wiring._make_escalation_delivery_callback``: when
  ``api_extended`` is missing from ``offered_modes`` and the reason is
  ``over_ceiling``, ``_build_escalation_message_body`` swaps the usual
  "Choose: …" line for the spec-literal "Monthly cap. Pause / Cancel
  only." string. Other reasons (``disabled``, ``over_headroom``) keep
  the normal Choose-line so the user isn't told there is a monthly cap
  when there isn't. §10.6 rows 2 and 5 rewritten to describe the
  ``offered_modes``-omission mechanism alongside the literal text.
- **Follow-up:** None.

---

## S18 — Crash-recovery uses `invocation_log` absence, not `task_status`

- **Surfaced by:** `slices/slice_18_budget_extension.md`
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§10.6`
  (row 4).
- **Status:** resolved-in-bucket-1
- **Decision / Reasoning:** Spec said *"scan `escalation_request WHERE
  resolution='api_extended' AND task_status NOT IN ('completed','failed')`"*.
  No `task_status` column exists on `escalation_request`; that column lives
  on `tasks`, but a granted extension is not always tied to a `task` row
  (e.g. drafting paths). `BudgetExtensionRepository.find_stale_grants()`
  uses `LEFT JOIN invocation_log ... WHERE il.id IS NULL` (excluding
  `escalation_lifecycle` rows) as a proxy: "the extension was granted but
  the actual API call never landed an invocation_log row." This works for
  the actual recovery objective (don't leave phantom headroom across
  restarts) and is uniform across drafting and task-bound paths. §10.6
  row 4 was rewritten to describe the actual `invocation_log`-presence
  check and links to the implementing repo method.
- **Follow-up:** None.

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
- **Status:** resolved-in-bucket-1
- **Decision / Reasoning:** Picked option (b): the
  ``<project_root>/var/workspace`` fallback was dropped from
  ``ChatPromptBuilder._resolve_workspace_root``. Construction now
  raises :class:`RuntimeError` when neither ``DONNA_WORKSPACE_PATH``
  nor an explicit ``workspace_root`` kwarg is provided. The original
  followup feared "(b) breaks existing tests that don't set the env"
  but every current test (``test_escalation_chat_prompt.py``,
  ``test_chat_mode_e2e.py``, ``test_section_10_residual_gaps.py``)
  passes ``workspace_root=tmp_path / "workspace"`` explicitly, and
  ``donna.setup.validators`` requires the env var at production boot
  — so the fallback was unreachable in both environments. Spec
  language in §5.2 / §6.1 stays literal; no doc change required. New
  unit-test class ``TestWorkspaceRootResolution`` pins the env-var,
  kwarg-override, and missing-env error paths.
- **Follow-up:** None.

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
- **Status:** open (deferred from slice 24, see S24 audit residue
  entry below)
- **Decision / Reasoning:** Slice 21 left `parent_escalation_id` in
  place but did not add a depth limit. The iteration cap on a single
  escalation already bounds the inner loop; cross-row chains haven't
  been observed yet. Slice 24 audited the path and confirmed the
  iteration cap keeps the inner loop bounded; adding a new
  cross-row depth limit is a product behaviour change and falls
  outside slice 24's hardening-only charter. Re-routed to whichever
  next slice touches the gate behaviourally.
- **Follow-up:** Tracked under
  ``S24 — Audit residue: §12 Q5 re-escalation depth limit deferred``
  below for the next behavioural slice. Concrete shape unchanged
  (`max_re_escalation_depth` config + gate reject path).

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

## S22 — Validation depth: lint + import-smoke only, no dependent-skill regression

- **Surfaced by:** `slices/slice_22_tool_gap_surfacing.md`
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§10.4`
  row 4, `#§10.5`.
- **Status:** open (deferred to slice 24)
- **Decision / Reasoning:** `ManualValidationRouter._validate_tool` runs
  the six §10.5 lint rules plus the subprocess import smoke
  (`python -c "import donna.skills.tools.<name>"`). It does **not**
  re-run dependent-skill fixtures (every skill whose YAML mentions the
  new tool) against the branch, even though §10.4 row 4 calls that out
  as the regression-protection step ("tool build passes validation but
  breaks an existing skill in shadow"). Slice 22 user direction was
  "lint + import-smoke only" — keeps the slice scope clean and mirrors
  the existing §10.4 row 4 deferral language.
- **Follow-up:** Slice 24 (escalation hardening) adds the regression
  step. It wraps `MockToolRegistry` around the new tool's mock fixture,
  runs the full skill-fixture suite, and only marks the `tool_request`
  completed if pass-rate ≥ threshold. Until then, regressions land at
  next orchestrator restart and surface through the existing skill
  shadow / divergence pipeline.

---

## S22 — `requires_rebuild=True` Discord nag deferred

- **Surfaced by:** `slices/slice_22_tool_gap_surfacing.md`
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§10.5`
  row 1.
- **Status:** open (deferred to slice 24)
- **Decision / Reasoning:** §10.5 row 1 says *"if `requires_rebuild=true`
  after merge: registry refuses to mark tool active until orchestrator
  restart with new build SHA. Discord nag posted hourly until rebuild."*
  Slice 22's `tool_lint/metadata.py` enforces the metadata declaration
  and emits a `requires_rebuild_warning` lint result that the dashboard
  panel renders, but the **hourly Discord nag** isn't wired. The
  registry-refusal half also isn't implemented because there is no tool
  lifecycle table — activation is manual merge + restart, period.
- **Follow-up:** Slice 24 — add an hourly job that scans
  `tool_request WHERE status='completed' AND resolved_at < now-1h` and
  posts a "Tool `X` is built but the orchestrator hasn't been restarted
  yet" reminder until the tool name appears in
  `ToolRegistry.list_tool_names()` after boot.

---

## S22 — No tool lifecycle table

- **Surfaced by:** `slices/slice_22_tool_gap_surfacing.md`
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§7`,
  `#§10.5`, `spec_v3.md#§23.3`.
- **Status:** resolved-in-slice-22
- **Decision / Reasoning:** Skills have a multi-state lifecycle
  (`claude_native → skill_candidate → draft → sandbox → shadow_primary
  → trusted`); tools don't. Tools live in source code, get registered
  by name at orchestrator boot via `register_default_tools`, and are
  either present or absent in the registry. Slice 22 considered adding
  a `tool` / `tool_version` parallel table but decided against it: it
  would duplicate `pyproject.toml` + the source tree + the registry as
  yet another source of truth, and the deployment cycle (manual merge
  + restart, plus rebuild when `requires_rebuild=True`) is the
  activation. `_validate_tool` only marks the `tool_request` row
  completed; the user runs `git merge` and restarts manually.
- **Follow-up:** None unless Phase 2 introduces hot-loadable tools or
  per-user tool catalogs. Logged here so future readers don't propose
  the table independently.

---

## S22 — Iteration cap on tool builds doesn't auto-reject linked tool_request

- **Surfaced by:** `slices/slice_22_tool_gap_surfacing.md` (self-review).
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§7`,
  `#§10.4` row 2.
- **Status:** open (low priority — non-blocking)
- **Decision / Reasoning:** When the slice-21 iteration cap fires on a
  `tool_request_fulfillment` escalation (`escalation_request.status
  → cancelled`, `human_review=1`), the linked `tool_request` row stays
  in `status='in_progress'`. This is non-blocking because the dedup
  index is `WHERE status='open'` — re-emission of the same gap creates
  a fresh `open` row alongside the orphaned `in_progress` one. The
  orphan never blocks anything; it just lingers in the dashboard list
  view as "stuck in progress" until manually rejected.
- **Follow-up:** Slice 24 — extend the iteration-cap sweep in
  `claude_code_poller._cancel_at_cap` so when the row's
  `originating_entity_type='tool_request'`, the linked
  `tool_request` row is also flipped to `status='rejected'` with a
  `tool_request_rejected` audit event.

---

## S22 — `_validate_tool` packs warnings into `failures` field

- **Surfaced by:** `slices/slice_22_tool_gap_surfacing.md` (self-review).
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§10.5`
  row 1.
- **Status:** open (cosmetic)
- **Decision / Reasoning:** `ValidationOutcome` (slice-21 dataclass) has
  `passed: bool` and `failures: list[dict]` but no `warnings` field. To
  surface `requires_rebuild_warning` on the dashboard validation-result
  panel without adding a slice-21 schema change, `_validate_tool`
  packs warnings into `failures` while keeping `passed=True`. Functional
  but misleading at the type level; the dashboard panel labels them
  `(warn:…)` so the user sees them as warnings.
- **Follow-up:** Add `warnings: list[dict]` to `ValidationOutcome` and
  `escalation_request.validation_result` schema in slice 24, then
  unpack into the cleaner location.

---

## S22 — MorningDigest production wiring depends on entrypoint that doesn't exist

- **Surfaced by:** `slices/slice_22_tool_gap_surfacing.md` (self-review).
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§7`
  (digest aggregation).
- **Status:** open (pre-existing, not introduced by slice 22)
- **Decision / Reasoning:** `MorningDigest.__init__` accepts a
  `tool_request_repo` kwarg (slice 22 addition) and `_assemble_data`
  queries it correctly when wired. But there is **no production
  construction site for `MorningDigest` anywhere in the repo** — neither
  `cli.py`, `cli_wiring.py`, nor `server.py` builds a `NotificationTasks`
  bundle. The digest is dead code in production today. Slice 22's
  plumbing is correct; it activates as soon as someone wires
  `NotificationTasks(... morning_digest=MorningDigest(...,
  tool_request_repo=ctx.tool_request_repository))` in the orchestrator
  boot path.
- **Follow-up:** Out of slice 22 scope. Whichever future slice adds the
  reminder/digest scheduling wiring needs to pass
  `ctx.tool_request_repository` through. Logged so the wiring is
  noticed when it lands.

---

## S22 — `escalation_request.originating_entity_type='tool_request'` not in skill router resolver

- **Surfaced by:** `slices/slice_22_tool_gap_surfacing.md` (self-review).
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§5.3`,
  `#§7`.
- **Status:** resolved-in-slice-22 (intentional asymmetry)
- **Decision / Reasoning:** `EscalationGate._resolve_capability_name`
  added a `tool_request` branch (returns `tool_request.tool_name` for
  `{name}` substitution). `ManualValidationRouter._resolve_capability_name`
  did **not** add the same branch — `_validate_tool` resolves through
  `tool_request_repo.get(id)` instead. Asymmetric but intentional:
  `_resolve_capability_name` is only called by `_validate_skill`, which
  doesn't run for tool builds. Adding a no-op branch would suggest a
  contract that doesn't exist.
- **Follow-up:** None.

---

## S22 — `BudgetGuard` cost-aggregation exclusions extended

- **Surfaced by:** `slices/slice_22_tool_gap_surfacing.md` (self-review).
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§10.10`.
- **Status:** resolved-in-slice-22-followup (commit `84c2a5e`)
- **Decision / Reasoning:** Slice 17 added
  `task_type='escalation_lifecycle'` to `BudgetGuard.check_pre_call`'s
  `exclude_task_types` list so audit rows don't pollute cost
  breakdowns. Slice 22's audit rows use a parallel
  `task_type='tool_gap_lifecycle'` (kept separate so per-subsystem
  queries stay clean). The exclusion list now includes both. Cost math
  was already correct (rows have `cost_usd=0.0`); this just keeps
  per-task-type breakdown counts honest.
- **Follow-up:** None.

---

## S22 — `find_open_for_originating_entity` keyword-only signature

- **Surfaced by:** `slices/slice_22_tool_gap_surfacing.md` (self-review,
  caught by `tests/cost/test_escalation_gate_tool_build.py`).
- **Spec section(s):** N/A (implementation contract).
- **Status:** resolved-in-slice-22-followup (commit `84c2a5e`)
- **Decision / Reasoning:** The slice-21 helper takes
  `*, entity_type, entity_id` (keyword-only). My initial slice-22 dedup
  call site in `EscalationGate.open_tool_build_escalation` passed
  positional args, which would `TypeError` the first time the
  `[File request]` button was clicked. Caught by the integration test
  that exercises `open_tool_build_escalation` end-to-end.
- **Follow-up:** None. Logged as a reminder that future call sites
  must use keyword args.

---

## S22 — `tool_gap.lint` config knobs wired through `extra_context`

- **Surfaced by:** `slices/slice_22_tool_gap_surfacing.md` (self-review).
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§6.1`
  (config block), `#§9` (template).
- **Status:** resolved-in-slice-22-followup (commit `84c2a5e`)
- **Decision / Reasoning:** `EscalationGate.open_tool_build_escalation`
  initially hardcoded `requires_rebuild_default=False` and
  `default_timeout_seconds=5` in the `extra_context` passed to
  `record_manual_handoff`. A deployment that flipped
  `tool_gap.lint.requires_rebuild_default: true` in
  `config/manual_escalation.yaml` (or set a different timeout) would
  silently render the wrong defaults into the spec template. Now reads
  from `self._config.tool_gap.lint`.
- **Follow-up:** None.

---

## S23 — Canonical `dashboard_setting` key namespace + legacy aliases

- **Surfaced by:** `slices/slice_23_dashboard_runtime_overrides.md`
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§6.3(a)`
- **Status:** resolved-in-slice-23
- **Decision / Reasoning:** Slice 17/18/21 wrote two
  `dashboard_setting` keys without the `manual_escalation.` prefix
  (`modes.claude_code.enabled`, `budget_extension.enabled`). Slice
  23 unifies the namespace so the resolver, write API, and YAML parser
  share one shape. Existing rows are still honoured via legacy aliases
  registered in `donna.cost.dashboard_settings_catalog`; new writes
  go to the canonical key only.
- **Follow-up:** Slice 24 owns the alias-retirement audit — confirm
  no rows reference legacy keys after a full deployment cycle and
  remove the alias map.

---

## S23 — Escalation settings page lives on its own route

- **Surfaced by:** `slices/slice_23_dashboard_runtime_overrides.md`
- **Spec section(s):** `docs/domain/management-gui.md` "Manual Escalation Surfaces" §
- **Status:** resolved-in-slice-23
- **Decision / Reasoning:** Brainstorm gap weighed putting the
  per-task-type override grid as a section on the existing
  `/escalations` workspace vs. a dedicated page. Picked dedicated
  `/escalation-settings`: the workspace is per-row (one escalation),
  the settings page is per-subsystem (toggles + grid). Mixing the two
  would bloat the detail view that slice 19 already shaped for the
  escalation lifecycle. Sidebar carries a separate nav entry.
- **Follow-up:** Phase 2 multi-user might need user-scoped overrides
  on the same page; revisit when that lands.

---

## S23 — UI 409 behaviour: visible toast over silent retry

- **Surfaced by:** `slices/slice_23_dashboard_runtime_overrides.md`
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§10.7` row 1
- **Status:** resolved-in-slice-23
- **Decision / Reasoning:** Brainstorm gap. Silent refetch + retry
  could race against an intentional change in another tab and leave
  the operator unsure which value is authoritative. The page now
  surfaces a `Setting changed in another tab. Showing latest.` toast
  with the conflict's live state and replaces the stale value
  in-place, so the user sees exactly what's stored.
- **Follow-up:** None.

---

## S24 — Per-row timeline merges `tool_gap_lifecycle` events

- **Surfaced by:** `slices/slice_24_escalation_hardening.md`
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§10.10`
- **Status:** resolved-in-slice-24
- **Decision / Reasoning:** Slice 19 shipped a per-row timeline that
  filtered ``invocation_log`` on
  ``task_type='escalation_lifecycle'`` only. Slice 22 added a
  parallel ``tool_gap_lifecycle`` task_type for tool-build audit
  rows; when a tool gap drove a ``tool_request_fulfillment``
  escalation, the lint outcome was invisible on the detail page.
  Slice 24 merges both task_types in
  :func:`donna.api.routes.admin_escalations._fetch_timeline` and
  adds a dedicated :http:get:`/admin/escalations/{id}/timeline`
  endpoint with a ``next_after_id`` cursor for append-only polling.
- **Follow-up:** None.

---

## S24 — Standalone timeline endpoint

- **Surfaced by:** `slices/slice_24_escalation_hardening.md`
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§10.10`
- **Status:** resolved-in-slice-24
- **Decision / Reasoning:** The slice-19 detail endpoint already
  embedded the timeline, but a full re-fetch on every 30-second
  refresh tick re-renders the entire submission UI and resets the
  scroll position. The new ``GET /timeline`` returns just the
  events plus a cursor, so the detail page polls it independently
  and appends new rows in place. Same backend join logic; lighter
  client.
- **Follow-up:** None.

---

## S24 — `find_open_for_originating_entity` now `user_id`-scoped

- **Surfaced by:** `slices/slice_24_escalation_hardening.md`
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§10.9`
  row 1.
- **Status:** resolved-in-slice-24
- **Decision / Reasoning:** Slice 21's helper missed a ``user_id``
  filter, so the dedup query for
  ``('skill_candidate_report', candidate_id)`` could return user
  B's open escalation when user A re-emitted the same gap once
  Phase 2 multi-user activated. Both call sites in
  :class:`EscalationGate` already had the owner in scope, so the
  signature change is purely additive at runtime; the regression is
  pinned by the parametrised
  ``tests/integration/test_multi_user_isolation.py`` fixture.
- **Follow-up:** Phase 2 multi-user activation needs to confirm the
  parametrised fixture stays green when ``auth.yaml`` flips to
  multi-user.

---

## S24 — ORM / Alembic schema drift regression guard

- **Surfaced by:** `slices/slice_24_escalation_hardening.md`
- **Spec section(s):** `spec_v3.md#§16.1` (schemas), `docs/superpowers/specs/manual-escalation.md#§8`.
- **Status:** resolved-in-slice-24
- **Decision / Reasoning:** Slice 21's migration added six columns
  to ``escalation_request`` and slice 22 added the ``tool_request``
  table; neither updated ``src/donna/tasks/db_models.py``, so any
  test fixture using ``Base.metadata.create_all`` (chat-mode E2E,
  api_extended E2E) silently dropped the columns and crashed at
  the first write. Earlier slices' invocation_log ALTERs had the
  same drift (``caller``, ``chain_id``, ``estimated_tokens_in``,
  ``interrupted``, ``overflow_escalated``, ``queue_wait_ms``).
  Slice 24 added the columns + indexes to the ORM and shipped
  ``tests/unit/test_orm_alembic_consistency.py``: parametrised over
  every manually-managed table, it diffs ``alembic upgrade head``
  against ``Base.metadata.create_all`` and fails on ANY drift.
- **Follow-up:** Whichever slice next adds an Alembic migration
  must update the ORM in the same PR — the consistency test now
  enforces this.

---

## S24 — `requires_rebuild=True` hourly Discord nag landed

- **Surfaced by:** `slices/slice_24_escalation_hardening.md`
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§10.5`
  row 1.
- **Status:** resolved-in-slice-24
- **Decision / Reasoning:** Closes the slice-22 deferral. New
  module ``donna.cost.requires_rebuild_nag.RequiresRebuildNagger``
  runs as an orchestrator tick: it pulls
  ``tool_request WHERE status='completed'`` rows resolved before a
  configurable grace window, diffs them against the live
  :meth:`ToolRegistry.list_tool_names`, and posts the reminder for
  any tool that hasn't appeared in the registry yet. Cooldown is
  enforced via ``tool_request.last_pinged_at`` so the nag stops
  pinging once an hour passes without restart and resumes on the
  next tick. Failed posts deliberately do NOT stamp
  ``last_pinged_at`` so a Discord 5xx never silently drops the
  user's reminder.
- **Follow-up:** ``cli_wiring.build_startup_context`` needs to
  construct the nagger and the bot-aware ``RequiresRebuildNagPoster``
  alongside the slice-22 ``ToolGapPingPoster`` once the bot is up.
  Logged here so the wiring isn't forgotten when the next operator
  slice touches that module.

---

## S24 — `escalation_lifecycle` audit row carries `extension_granted`

- **Surfaced by:** `slices/slice_24_escalation_hardening.md` (gap
  audit caught the missing test).
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§10.10`.
- **Status:** resolved-in-slice-24 (test added; emitter was already
  correct since slice 18).
- **Decision / Reasoning:** The audit-coverage matrix flagged
  ``extension_granted`` as untested even though
  :meth:`EscalationGate.grant_budget_extension` had been writing
  it since slice 18. Slice 24 added the regression test in
  ``tests/integration/test_section_10_residual_gaps.py::TestExtensionGrantedAudit``
  so future refactors of the grant path can't silently drop the
  audit row.
- **Follow-up:** None.

---

## S24 — Vault-name privacy regression on the deterministic summary

- **Surfaced by:** `slices/slice_24_escalation_hardening.md`
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§10.8`
  row 1.
- **Status:** resolved-in-slice-24
- **Decision / Reasoning:** §10.8 row 1 calls vault-data leakage
  an "accepted risk" mitigated by the OWNER_DISCORD_ID gate; the
  brainstorm gap asked for a regression that asserts the rendered
  Discord summary doesn't echo prompt_body content. Slice 24 pinned
  the contract on the ``ChatPromptBuilder._deterministic_summary``
  fallback path: it interpolates only ``task_type`` and
  ``estimate_usd``, never the body, so a future refactor that
  accidentally pulled secrets through would fail the regression.
- **Follow-up:** When the LLM-summary path lands proper prompt
  redaction (not in-scope for slice 24), extend the test to
  exercise that path too.

---

## S24 — Audit residue: `§10.4 row 4` dependent-skill regression still deferred

- **Surfaced by:** `slices/slice_24_escalation_hardening.md` (audit).
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§10.4`
  row 4.
- **Status:** open (deferred — explicit out-of-scope per slice 24
  brief)
- **Decision / Reasoning:** The slice 24 brief lists
  "§10.4 rows 3–4 (tool build pre-validation lint, shadow regression)"
  as targeted, but a real shadow-regression harness needs
  fixture-driven re-runs of every dependent skill against the new
  tool's mock entry. That requires non-trivial test infra
  (``MockToolRegistry`` shaping, fixture isolation across
  branches) which would balloon slice 24 beyond its hardening
  charter. The current state — failures land on next orchestrator
  reboot via the existing skill shadow / divergence pipeline — is
  acceptable as a holding pattern.
- **Follow-up:** Whichever slice owns the next round of skill
  validation infrastructure expands ``ManualValidationRouter._validate_tool``
  to include the regression step. Tracked here so the deferral
  doesn't slip a third time.

---

## S24 — Audit residue: §12 Q5 re-escalation depth limit deferred

- **Surfaced by:** `slices/slice_24_escalation_hardening.md` (audit).
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§12`
  Q5.
- **Status:** open (deferred — out-of-scope per slice 24's
  "Not in Scope" charter)
- **Decision / Reasoning:** Slice 21 followup S21 logged this for
  slice 24, but slice 24's brief explicitly bars new behaviours.
  Adding a `max_re_escalation_depth` config + reject path is a
  product change, not a hardening / audit deliverable. Slice 24
  confirmed the iteration cap (default 3) keeps the inner loop
  bounded and that no real cross-row chains have been observed in
  slice 17–23 deployments — leaving the depth limit out is safe
  for now. §12 Q5 in the canonical spec was updated to cite this
  reasoning.
- **Follow-up:** The next behavioural slice that touches the gate
  picks this up. Concrete shape: `manual_escalation.triggers
  .max_re_escalation_depth` (default 5), enforced in
  `EscalationGate.fire_and_wait` before the row is created.

---

## S24 — Audit residue: §11 Twilio-mock E2E for Discord-5xx retry

- **Surfaced by:** `slices/slice_24_escalation_hardening.md` (audit).
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§11`
  failure-mode regression tests row 2.
- **Status:** open (deferred — Twilio mock harness gap, not a
  spec-impossibility)
- **Decision / Reasoning:** The slice-17
  `EscalationDeliveryLoop._maybe_fan_out_sms` calls into the
  slice-7 SMS manager with `start_at_tier=2`, and the existing
  `tests/unit/test_escalation_tiers.py` covers the SMS-tier
  contract. What's missing is a single integration test that
  drives Discord-5xx → timeout → SMS-fanout end to end with
  mocked Twilio + Discord. Slice 24 confirmed every component
  works in isolation; the integration harness is not in scope.
- **Follow-up:** Whichever future slice introduces a unified
  Discord+Twilio integration harness picks this up. Test shape:
  drive the loop with a deliver-callback that returns False until
  the timeout fires, assert the SMS manager is called with
  `start_at_tier=2`.

---

## S24 — Audit residue: §10.6 row 1 re-estimate after overspend deferred

- **Surfaced by:** `slices/slice_24_escalation_hardening.md` (audit).
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§10.6`
  row 1.
- **Status:** open (deferred — instrumentation gap)
- **Decision / Reasoning:** The ``complete()`` hard token cap
  prevents over-spend (slice 18 mitigation), but the spec also
  calls for "re-estimate + re-escalation" when the cap fires. The
  re-estimate path requires the model layer to surface a
  ``token_limit_exceeded`` decision back to the gate and is more
  invasive than the rest of slice 24's surface. Slice 24 confirmed
  the cap-enforcement half is solid via the existing
  ``test_budget_extension_gate`` suite; the re-escalation half
  stays open.
- **Follow-up:** A future budget-hardening slice picks this up
  along with the per-user ``donna_models.yaml`` migration noted in
  §10.9 row 2.

---

## S23 — Slider cap is recomputed at GET time, not live

- **Surfaced by:** `slices/slice_23_dashboard_runtime_overrides.md`
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§6.3(a)`
- **Status:** resolved-in-slice-23
- **Decision / Reasoning:** Brainstorm gap (live "remaining headroom"
  vs page-load only). The cap is
  `hard_monthly_ceiling_usd / days_left_in_month`. Recomputing it
  live on every keystroke would require either a WebSocket or
  polling, neither of which the dashboard uses today (30 s manual
  refresh is the convention per `docs/domain/management-gui.md`). The
  slider's max is set from the cap returned by the GET response, and
  the PUT route re-validates server-side, so a stale cap can never
  produce an over-ceiling write — it just refuses with 422.
- **Follow-up:** None.

---

## Discord Onboarding & DM Delivery — deferred items

- **Surfaced by:** `docs/superpowers/specs/2026-05-12-discord-onboarding-dm-delivery-design.md`
- **Spec section(s):** `spec_v3.md#§28`, `docs/domain/notifications.md`
- **Status:** open
- **Decision / Reasoning:** Discord user auto-onboarding and DM delivery
  were implemented as a standalone feature (not tied to a numbered slice).
  Several items were explicitly deferred:
  1. **Immich account linking** for Discord-onboarded users — future feature
     when companion app is built.
  2. **Profile update commands** (email, phone) — users currently have no
     self-service way to add these fields after onboarding.
  3. **DM routing for reminders/nudges** — stays in shared channels for now;
     only automation alerts use DMs.
  4. **Companion app auth flow** — separate design when Flutter work begins;
     the nullable `immich_user_id` enables magic-link auth as an alternative
     to Immich login.
- **Follow-up:** Items 1–2 should be addressed when the Flutter companion
  app slice is planned. Item 3 can be enabled per-user via config when
  users have a way to opt in.

---

## Wiring Audit — `render_chat_prompt` tz not threaded at call sites

- **Surfaced by:** wiring audit remediation (PR #89, timezone unification)
- **Spec section(s):** `docs/domain/scheduling.md#timezone`
- **Status:** open (cosmetic — falls back to `America/New_York`)
- **Decision / Reasoning:** `render_chat_prompt` now accepts an optional
  `tz` parameter (defaults to `America/New_York`), but `ConversationEngine`
  calls it in 4 places without passing `tz`. The fallback is correct for
  the single-user case, but inconsistent with the pattern where other
  components receive `ctx.tz` explicitly. `ConversationEngine.__init__`
  would need a `tz` param threaded from the orchestrator boot path.
- **Follow-up:** Thread `tz` through `ConversationEngine` when it next
  gets a feature change. Low priority since the default matches the
  configured timezone.

---

### Calendar view — IN_PROGRESS tasks not shown

- **Surfaced by:** `docs/superpowers/specs/2026-05-13-calendar-view-design.md`
- **Spec section(s):** `spec_v3.md#§4.4`
- **Status:** open
- **Decision / Reasoning:** The calendar week endpoint only queries `TaskStatus.SCHEDULED` tasks, matching the existing `/schedule` endpoint behavior. However, tasks that transition to `IN_PROGRESS` still have a `scheduled_start` and should appear on the calendar for a complete scheduling picture. Kept consistent with `/schedule` for now to avoid scope creep.
- **Follow-up:** Extend `/calendar/week` to also include `IN_PROGRESS` tasks with `scheduled_start`. Consider adding a visual distinction (e.g., different opacity or badge) for in-progress vs scheduled.

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
