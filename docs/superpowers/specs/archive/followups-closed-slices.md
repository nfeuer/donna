# Followups — Closed Items Archive

> Archived 2026-05-18. These items were resolved during slices 18–24.
> Open items remain in [`followups.md`](../followups.md).

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

## S19 — Submission endpoint URL contract

- **Surfaced by:** `slices/slice_19_dashboard_escalation_workspace.md`
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§5.2`,
  `#§9` (`schemas/escalation_submission.json`)
- **Status:** resolved-in-slice-19
- **Decision / Reasoning:** Spec §5.2 names
  `/admin/escalations/<correlation_id>/submit` for the dashboard POST.
  Slice 19 implements it exactly as written, with the JSON body
  validated against `schemas/escalation_submission.json` (a discriminated
  oneOf on `mode`: `chat` carries `answer` >= 50 chars; `claude_code`
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

---

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

---

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

---

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

---

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

---

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

---

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

## S22 — No tool lifecycle table

- **Surfaced by:** `slices/slice_22_tool_gap_surfacing.md`
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§7`,
  `#§10.5`, `spec_v3.md#§23.3`.
- **Status:** resolved-in-slice-22
- **Decision / Reasoning:** Skills have a multi-state lifecycle
  (`claude_native -> skill_candidate -> draft -> sandbox -> shadow_primary
  -> trusted`); tools don't. Tools live in source code, get registered
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

## S22 — `requires_rebuild=True` Discord nag deferred

- **Surfaced by:** `slices/slice_22_tool_gap_surfacing.md`
- **Spec section(s):** `docs/superpowers/specs/manual-escalation.md#§10.5`
  row 1.
- **Status:** resolved-in-slice-24 (see S24 entry)
- **Decision / Reasoning:** §10.5 row 1 says *"if `requires_rebuild=true`
  after merge: registry refuses to mark tool active until orchestrator
  restart with new build SHA. Discord nag posted hourly until rebuild."*
  Slice 22's `tool_lint/metadata.py` enforces the metadata declaration
  and emits a `requires_rebuild_warning` lint result that the dashboard
  panel renders, but the **hourly Discord nag** wasn't wired.
- **Follow-up:** Resolved by S24 — `RequiresRebuildNagger` module.

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
- **Spec section(s):** `docs/domain/management-gui.md` "Manual Escalation Surfaces" section
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
