# Slice 24: Escalation Hardening

> **Goal:** Close every failure-mode row in canonical spec §10 with a regression test, ship the audit-timeline view that consumes the lifecycle log entries, and complete the multi-user scoping audit so Phase 2 can flip on without rework. This slice exists because the prior slices each shipped *some* §10 mitigations as part of their core work; this one ensures every row has a fixture, every audit log entry renders in the UI, and `user_id` is consistent everywhere.

## Spec Reference

**Canonical spec:** [`docs/superpowers/specs/manual-escalation.md`](../docs/superpowers/specs/manual-escalation.md)
**Sections this slice realizes:** §10 remaining (any rows that prior slices didn't cover with regression tests), §10.10 (audit timeline view in dashboard), §10.9 (multi-user readiness audit), §11 end-to-end tests.
**Related upstream specs:** `spec_v3.md §13.1` (Budget Rules — verifies the new behavior holds), `spec_v3.md §16.1` (Schemas — confirms `user_id` discipline across new tables).

This slice is bound to the canonical spec above. Read it before starting work. Cite the relevant `§` in the PR description.

## Spec Excerpts

### §10.10 — Audit & observability

Every state transition writes to `invocation_log` with `task_type='escalation_lifecycle'` and a small JSON payload:
- `escalation_offered` — modes shown, estimate, remaining
- `escalation_resolved` — chosen mode, resolved_by
- `escalation_submitted` — branch, iteration
- `escalation_validated` / `escalation_failed`
- `extension_granted` / `extension_voided`
- `tool_gap_detected` / `tool_request_filed`
- `iteration_limit_reached`

Dashboard renders these as a timeline per `escalation_request_id`.

### §10.9 — Multi-user readiness

| Failure | Mitigation |
|---|---|
| Phase 2 multi-user enabled, escalations from one user trigger another's Discord | Every table has `user_id`; Discord routing uses each user's configured channel. Tested via integration fixture even in Phase 1. |
| Cross-user budget mixing | Budget is per-user from day one; no global pool. Existing `donna_models.yaml` budget keys would need to move into a per-user config table — call out as a follow-up but enforce schema today. |

### §11 — End-to-end tests required

- [ ] **chat mode E2E:** trigger an over-budget chat_escalation; receive Discord prompt; submit answer via dashboard; task completes with answer as result.
- [ ] **claude_code mode E2E:** trigger over-budget skill_draft; receive Discord ping; build branch in worktree; mark as built via dashboard; skill enters sandbox state.
- [ ] **api_extended E2E:** approve extension; task runs; daily_remaining reflects extension; invocation_log carries escalation_request_id.
- [ ] **tool gap E2E:** add a capability that requires a missing tool; capability_tool_check fires; ping arrives in real time; user files request.

### §10 (residual) — failure-mode regression coverage

Each prior slice landed mitigations for §10 rows it owned:
- Slice 17 → §10.1 (Discord channel failures)
- Slice 18 → §10.6 (budget extension failures)
- Slice 19 → §10.7 row 1 (toggle race / optimistic lock)
- Slice 20 → §10.2 (prompt delivery), §10.3 row 1 (chat empty answer)
- Slice 21 → §10.3 rows 2–5 (claude_code submission), §10.4 rows 1–2 (validation, iteration cap)
- Slice 22 → §10.5 (tool-build-specific)
- Slice 23 → §10.7 rows 2–4 (config reload, missing reference_module, dashboard auth bound)

This slice audits each row, ensures a regression test exists, and fills any gaps. Specifically targeted: §10.4 rows 3–4 (tool build pre-validation lint, shadow regression), §10.8 (privacy / secret-scanner integration), §10.9 (multi-user fixtures).

## Relevant Docs

- `CLAUDE.md`
- Canonical spec, especially §10 (full), §11
- All prior slices (17–23) — each lists which §10 rows it covered
- `tests/` — review existing fixture patterns in `tests/skills/`, `tests/cost/`, `tests/integrations/`
- `docs/domain/observability.md` — Grafana dashboards may need new escalation panels

## What to Build

Cross-slice hardening — no new product features, but the audit, drift,
and regression work the prior slices each touched but never finished.

1. **§10.10 audit-timeline endpoint + UI poll.** A dedicated
   ``GET /admin/escalations/{correlation_id}/timeline`` that merges
   ``escalation_lifecycle`` (slice 17) and ``tool_gap_lifecycle``
   (slice 22) rows under the same ``escalation_request_id`` and
   carries a ``next_after_id`` cursor for append-only polling. The
   detail page consumes it on its 30 s tick so new audit events show
   up without re-fetching the entire detail blob. Resolves the
   slice-22 gap where ``tool_request_fulfillment`` rows hid their
   lint outcome.
2. **§10.9 multi-user isolation fixtures + a real bug fix.** A
   parametrised ``two_user_ids`` fixture in ``tests/conftest.py``
   plus ``tests/integration/test_multi_user_isolation.py`` runs
   every escalation read path under two distinct users. Caught and
   fixed a slice-21 cross-tenant gap in
   :func:`EscalationRepository.find_open_for_originating_entity`
   (now requires ``user_id``).
3. **ORM ↔ Alembic schema-drift guard.** Added every Alembic-only
   column to ``src/donna/tasks/db_models.py`` (slice-21 columns,
   slice-22 ``tool_request`` table, pre-existing
   ``invocation_log`` drift) and shipped
   ``tests/unit/test_orm_alembic_consistency.py`` to fail fast on
   any future drift between ``Base.metadata.create_all`` and
   ``alembic upgrade head``. This unblocked the existing chat-mode
   E2E that had been silently broken on main.
4. **§11 E2E coverage.** ``test_chat_mode_e2e.py`` already existed
   (now green again). Added ``test_api_extended_e2e.py`` (gate fires
   → grant → resolution → daily_remaining bumped → audit chain
   linked) and ``test_tool_gap_e2e.py`` (capability_tool_check →
   surfacer → tool_request → file_request → fulfillment audit
   chain on the unified per-row timeline).
5. **§10 residual regression coverage** for the rows the audit
   flagged: §10.1 row 5 (BudgetEscalationView owner-mismatch),
   §10.6 row 5 (hard monthly ceiling refuses grant), §10.8 row 1
   (deterministic-summary privacy guard), §10.10 row 6
   (``extension_granted`` audit row).
6. **§10.5 row 1** — closed the slice-22 deferral with
   :class:`donna.cost.requires_rebuild_nag.RequiresRebuildNagger`,
   an orchestrator-tick scanner that nags hourly until a
   ``requires_rebuild=True`` tool actually appears in
   ``ToolRegistry.list_tool_names()`` after the user restarts.
   Per-row cooldown via ``tool_request.last_pinged_at``; failed
   posts deliberately leave the column NULL so the next tick
   retries.

## Implementation Notes

- **Timeline merge:** see ``_TIMELINE_TASK_TYPES`` in
  ``src/donna/api/routes/admin_escalations.py``. Both endpoints use
  the same ``_fetch_timeline`` helper so the embedded detail
  timeline and the new dedicated endpoint stay in lock-step.
- **Multi-user fixture:** ``two_user_ids`` parametrises both
  orderings (``("nick","alex")`` and ``("alex","nick")``) so a
  spec-violating ``ORDER BY`` tie-break can't pass by accident.
- **Schema-drift test:** the test fixture rebuilds via
  ``alembic upgrade head`` per test (cheap; in-memory SQLite) and
  diffs the column-set produced by ``Base.metadata.create_all``.
  Server defaults must match — that's why the ORM grew explicit
  ``server_default="0"`` markers on slice-22 status / priority
  columns.
- **Nagger contract:** the ``RequiresRebuildNagPoster`` Protocol
  mirrors slice-22's ``ToolGapPingPoster`` so the bot wiring layer
  in ``cli_wiring.py`` (followup) can construct both with the same
  closure pattern (bot, owner_discord_id, channel name).
- **No worktree-style claude_code E2E.** The slice-21 poller test
  battery already covers every transition; running a full
  gate→worktree→poller flow needs real disk I/O. Logged as a
  followup so the slice that introduces an integration harness
  picks it up.

## Test Plan

- ``pytest tests/unit/test_orm_alembic_consistency.py`` — drift
  guard. Eight assertions (one per manually-managed table + two
  presence checks).
- ``pytest tests/integration/test_admin_escalations.py`` —
  expanded with seven additional test methods covering the new
  ``/timeline`` endpoint and the merged detail-blob timeline.
- ``pytest tests/integration/test_multi_user_isolation.py`` —
  ten parametrised assertions covering escalation, budget,
  audit, and delivery isolation.
- ``pytest tests/integration/test_chat_mode_e2e.py`` — restored
  green; covered by the schema-drift test going forward.
- ``pytest tests/integration/test_api_extended_e2e.py`` — two
  scenarios (happy path + crash-recovery void).
- ``pytest tests/integration/test_tool_gap_e2e.py`` — two
  scenarios (high → fulfillment chain, speculative → silent).
- ``pytest tests/integration/test_section_10_residual_gaps.py`` —
  four scenarios pinning the §10 rows the audit flagged.
- ``pytest tests/cost/test_requires_rebuild_nag.py`` — seven
  scenarios pinning grace, cooldown, registry hit, async
  provider, and post-failure semantics.

## Open Questions

- Does the existing observability stack (Grafana / Loki) need new panels for escalation lifecycle? Likely yes — confirm.
- E2E test infrastructure: do we have an existing harness for "trigger over-budget → Discord mock → fixture submit → assert task result"? If not, this slice builds it.

## Not in Scope

- New behaviors. This slice only audits, tests, and surfaces what slices 17–23 built.
- Phase 2 multi-user activation itself. Phase 2 is gated on a separate slice that flips `auth.yaml`; this slice ensures the data model survives that flip.
- Browser tool / specific tool builds.

## Session Context

Load only: `CLAUDE.md`, this brief, canonical spec, all prior slices (17–23), `tests/conftest.py` and a sampling of existing fixture-style tests, `docs/domain/observability.md`.

## Brainstorm Gaps (resolve before implementation)

> Run the superpowers brainstorm skill against this slice.

- [ ] Audit each §10 row: cite the test file + test name that proves the mitigation. List gaps explicitly.
- [ ] Define the audit-timeline view component contract — feeds from a single API endpoint that returns time-ordered `invocation_log` rows for one `escalation_request_id`.
- [ ] Multi-user fixture strategy: a parametrized `user_id` fixture in `conftest.py` that runs every escalation test under two distinct users, asserting no cross-talk?
- [ ] Grafana panels: which metrics matter? Suggested: open escalations by mode, time-to-resolution histogram, iteration distribution, validation pass rate, daily extension grant rate, tool gaps per day.
- [ ] Privacy regression: a fixture that posts a prompt containing a vault key reference and asserts the rendered Discord message contains *names not values*.
- [ ] Crash-recovery regression for §10.6 row 4 (orchestrator boot scan voiding orphaned extensions) — needs a test harness that simulates crash mid-grant.
- [ ] Should §10.9 row 2 (`donna_models.yaml` per-user migration) ship in this slice or be flagged as a follow-up? Spec leaves it open.

## Spec Drift Protocol

If implementation diverges from the canonical spec at `docs/superpowers/specs/manual-escalation.md`, the **same PR that introduces the divergence** must update the affected `§` of that spec (and any cross-referenced `spec_v3.md` section) so the doc matches reality.

Per `CLAUDE.md`: *"When a PR changes behavior, schema, routing, config contract, or external integration that the spec describes, update the affected `§` in the same PR."*

Drift checklist for this slice:

- [x] Did any §10 mitigation turn out impossible-as-written? Update the row. *§10.5 row 1 (`requires_rebuild=True` Discord nag) updated in `manual-escalation.md` to reference the new `donna.cost.requires_rebuild_nag.RequiresRebuildNagger`. §10.9 row 1 mitigation updated to cite the slice-24 `find_open_for_originating_entity` fix + parametrised fixture.*
- [x] Did the audit timeline differ from §10.10? Update §10.10. *§10.10 now describes the dedicated `GET /admin/escalations/{correlation_id}/timeline` endpoint and the merge of `escalation_lifecycle` + `tool_gap_lifecycle` rows.*
- [x] Did multi-user assumptions in §10.9 hold up? Update §10.9. *Both rows updated. Row 1 cites the parametrised `tests/integration/test_multi_user_isolation.py`; row 2 cites the slice-24 budget-isolation test.*
- [x] Did the E2E test list in §11 need additions or removals? Update §11. *§11 checklist boxes ticked: chat / api_extended / tool gap. claude_code mode E2E explicitly footnoted as deferred (worktree harness needed) and logged in `followups.md#S24`. Functional + regression-test checklists ticked off where the integrated suite now covers them; the two remaining open items (Discord-5xx Twilio integration, dashboard-down attachment fallback) are footnoted with what's covered today.*
- [x] Did the §15 decisions hold across all slices? Add a closing entry summarizing the as-built state and date. *Five slice-24 decision-log entries appended to `manual-escalation.md` §15 (audit-timeline merge, find_open user_id, ORM/Alembic guard, requires_rebuild nag, decision-log close).*
- [x] Did any prior-slice spec drift that wasn't reconciled in its own PR show up here? Reconcile now. *Caught and reconciled: (a) ORM ↔ Alembic drift on `escalation_request` (slice 21), `tool_request` (slice 22), `invocation_log` (LLM-gateway / context-budget) — fixed in `db_models.py` with regression guard. (b) `find_open_for_originating_entity` cross-tenant query (slice 21) — `user_id` now required. (c) `test_chat_mode_e2e.py` was broken on main due to the ORM drift — fixed by the schema-sync, kept green by the consistency guard.*
