# Slice 18: Budget Extension (`api_extended` mode)

> **Goal:** Land the `api_extended` mode end-to-end. Slice 17 stood up the four-button view with Pause + Cancel; this slice renders the `[Approve $X extension]` button, persists grants, raises today's effective cap, and routes the deferred task back into `complete()`. Hard ceilings, idempotent grants, and crash-recovery scan are required for this slice to be considered done.

## Spec Reference

**Canonical spec:** [`docs/superpowers/specs/manual-escalation.md`](../docs/superpowers/specs/manual-escalation.md)
**Sections this slice realizes:** §5.1 (`api_extended` mode), §6.1 (`budget_extension` YAML block), §8 (`daily_budget_extension` schema), §10.6 (budget-extension-specific failures), §10.10 (audit logging for `extension_granted` / `extension_voided`).
**Related upstream specs:** `spec_v3.md §13.1` (Budget Rules — replaces "pause-only" with extension path), `spec_v3.md §13.2` (Cost Tracking — extensions roll up into existing aggregates).

This slice is bound to the canonical spec above. Read it before starting work. Cite the relevant `§` in the PR description.

## Spec Excerpts

### §5.1 — `api_extended` mode

- Recipient: existing `complete()` gateway.
- Side effect: `daily_budget_extension` entry inserted, today's effective cap = base cap + sum(extensions).
- Hard ceiling: cumulative extensions per day cannot exceed `max_daily_extension_usd` (default $10). Extensions over ceiling surface a "Pause / Cancel" only choice.
- Audit: `escalation_request.resolution = 'api_extended'`; the resulting API call's `invocation_log` row carries the `escalation_request_id` foreign key.

### §6.1 — `budget_extension` YAML block (added to `config/manual_escalation.yaml`)

```yaml
budget_extension:
  enabled: true
  max_daily_extension_usd: 10.0
  hard_monthly_ceiling_usd: 150.0      # absolute cap, dashboard cannot exceed
```

### §8 — Schema added in this slice

```sql
CREATE TABLE daily_budget_extension (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  date DATE NOT NULL,
  amount_usd REAL NOT NULL,
  granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  granted_by TEXT NOT NULL,               -- discord user id
  escalation_request_id INTEGER,
  FOREIGN KEY (escalation_request_id) REFERENCES escalation_request(id)
);
```

### §10.6 — Budget-extension-specific failures (mitigations to wire in this slice)

| Failure | Mitigation |
|---|---|
| User approves extension; estimate was wrong; actual cost overshoots | API call's `complete()` enforces a hard token limit derived from `extension_amount × token_rate`. Truncated output triggers re-estimate + re-escalation. |
| Multiple extensions in one day stack to absurd amounts | `max_daily_extension_usd` enforced at button render time (button disabled if remaining headroom < estimate). |
| Approver clicks but interaction fails (Discord 5xx) | Idempotency: granting an extension is keyed on `(escalation_request_id, granted_by)`. Retry-safe. |
| Extension granted, task never runs (orchestrator crash) | On orchestrator boot, scan `escalation_request WHERE resolution='api_extended' AND task_status NOT IN ('completed','failed')`; resume or rollback. Rolled-back extensions get `voided=true`, never charged. |
| Hard monthly ceiling reached | All extension buttons disabled. Discord message reads "Monthly cap. Pause / Cancel only." |

## Relevant Docs

- `CLAUDE.md`
- Canonical spec, especially §5.1, §6.1, §8, §10.6
- `slices/slice_17_escalation_core.md` — depends on its data model + view scaffolding
- `spec_v3.md §13.1, §13.2, §4.3`
- `src/donna/cost/budget.py`, `tracker.py` — extension awareness goes here
- `src/donna/llm/complete.py` (or equivalent gateway) — token-limit enforcement (§10.6 row 1)

## What to Build

> *Resolve the brainstorm gaps below before filling in this section.*

## Implementation Notes

> *Resolve the brainstorm gaps below before filling in this section.*

## Test Plan

> *Resolve the brainstorm gaps below before filling in this section.*

## Open Questions

- Spec §12 Q5 — re-escalation parent chains. If a task's first extension overshoots and re-escalates, does the new escalation reuse the existing extension or grant a fresh one?

## Not in Scope

- Dashboard UI for setting `max_daily_extension_usd` slider (slice 23).
- The dashboard escalation-detail view (slice 19).
- Manual modes (slices 20, 21).

## Session Context

Load only: `CLAUDE.md`, this slice brief, the canonical spec, slice 17's outputs, `spec_v3.md §13.1 / §13.2`, `cost/budget.py`, `cost/tracker.py`, the LLM gateway entry point.

## Brainstorm Gaps (resolve before implementation)

> Run the superpowers brainstorm skill against this slice.

- [ ] Confirm token-limit enforcement strategy in `complete()` — is there an existing `max_tokens` clamp or do we add one keyed on `extension_amount`?
- [ ] Decide token_rate constants source — `config/donna_models.yaml` or hardcoded per provider?
- [ ] Crash-recovery scan: where does it run — orchestrator boot hook, dedicated scheduled task, or a startup probe?
- [ ] How is "today's effective cap" computed and cached? (Live SUM on every `complete()` call, or a memoized daily envelope object that gets bumped on grant?)
- [ ] Concurrency: two simultaneous escalations both trying to grant when only one fits under `max_daily_extension_usd` — atomic SQL transaction, or app-level lock?
- [ ] What's the rollback path when an extension is voided mid-flight? Does a partially-charged invocation_log entry get a `voided=true` flag too?
- [ ] How does this integrate with the existing `budget_guard.check`? Wrap or replace?

## Spec Drift Protocol

If implementation diverges from the canonical spec at `docs/superpowers/specs/manual-escalation.md`, the **same PR that introduces the divergence** must update the affected `§` of that spec (and any cross-referenced `spec_v3.md` section) so the doc matches reality.

Per `CLAUDE.md`: *"When a PR changes behavior, schema, routing, config contract, or external integration that the spec describes, update the affected `§` in the same PR. If the drift is intentional but the spec update is out of scope, call it out explicitly in the PR description so it can be reconciled later rather than silently drifting."*

Drift checklist for this slice:

- [ ] Did the schema differ from §8? Update §8.
- [ ] Did the YAML keys differ from §6.1? Update §6.1.
- [ ] Did the failure mitigations differ from §10.6? Update §10.6.
- [ ] Did acceptance criteria need adjustment? Update §11.
- [ ] Did decisions in §15 turn out wrong? Add a §15 entry with the updated decision and date.
- [ ] Did the upstream `spec_v3.md §13.1` / §13.2` stubs need updating? Update them.
