# Slice 17: Escalation Core

> **Goal:** Land the foundational data model and Discord plumbing for the over-budget decision tree. Cost router emits `escalation_offered`; user sees a four-button Discord message; `pause` and `cancel` resolve correctly. `api_extended` and manual modes are stubbed (buttons not rendered) — they ship in slices 18–21.

## Spec Reference

**Canonical spec:** [`docs/superpowers/specs/manual-escalation.md`](../docs/superpowers/specs/manual-escalation.md)
**Sections this slice realizes:** §4 (decision tree), §6.1 (YAML defaults — kill switch + triggers), §8 (`escalation_request`, `dashboard_setting` schemas — `daily_budget_extension` lands with slice 18 but the table is created here for FK targets), §10.1 (Discord channel failures), §10.10 (audit logging for `escalation_offered`, `escalation_resolved`, `escalation_timed_out`).
**Related upstream specs:** `spec_v3.md §13.1` (Budget Rules — pause-only terminal extended here), `spec_v3.md §16.1` (Database Strategy — new tables added).

This slice is bound to the canonical spec above. Read it before starting work. Cite the relevant `§` in the PR description.

## Spec Excerpts

### §4 — Decision tree (this slice ships only Pause + Cancel)

```
Task: <task_description>
Estimate: $<estimate>  |  Daily remaining: $<remaining>  |  Type: <task_type>

[Approve $X extension]   [Manual handoff]   [Pause]   [Cancel]
```

| Button | Effect (this slice) |
|---|---|
| **Approve $X extension** | NOT RENDERED in slice 17. Land in slice 18. |
| **Manual handoff** | NOT RENDERED in slice 17. Land in slices 20/21. |
| **Pause** | Task moves to `paused` state. Will be reconsidered tomorrow when budget refreshes. |
| **Cancel** | Task is closed without action. |

Decision flow rules:
- Only buttons whose modes are *enabled* render. Disabled "Manual handoff" never shows; user must Approve, Pause, or Cancel.
- If **all** modes are disabled by config/dashboard, only `Pause` and `Cancel` show.
- Buttons time out after `escalation_timeout_minutes` (default 60). On timeout: task moves to `paused`, log entry `escalation_timed_out`, next escalation tier (SMS via slice 7) fires if priority ≥ 4.

### §6.1 — Bootstrap YAML (`config/manual_escalation.yaml` — new file lands here)

```yaml
enabled: true                          # global kill switch

modes:
  chat:
    enabled: true
  claude_code:
    enabled: true

triggers:
  task_approval_threshold_usd: 5.0     # moved from DonnaConfig
  escalation_timeout_minutes: 60
  manual_iteration_limit: 3
```

(`budget_extension` and `prompt_delivery` blocks land with their respective slices.)

### §8 — Schemas added in this slice

```sql
CREATE TABLE escalation_request (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  correlation_id TEXT UNIQUE NOT NULL,
  task_id INTEGER,
  task_type TEXT NOT NULL,
  estimate_usd REAL NOT NULL,
  daily_remaining_usd REAL NOT NULL,
  offered_modes JSON NOT NULL,
  resolution TEXT,
  resolved_by TEXT,
  resolved_at TIMESTAMP,
  prompt_path TEXT,
  branch_name TEXT,
  iteration INTEGER DEFAULT 1,
  status TEXT DEFAULT 'open',
  submitted_at TIMESTAMP,
  validated_at TIMESTAMP,
  parent_escalation_id INTEGER,
  FOREIGN KEY (parent_escalation_id) REFERENCES escalation_request(id)
);

CREATE TABLE dashboard_setting (
  key TEXT PRIMARY KEY,
  value JSON NOT NULL,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_by TEXT NOT NULL
);

ALTER TABLE invocation_log ADD COLUMN escalation_request_id INTEGER
  REFERENCES escalation_request(id);
```

### §10.1 — Discord channel failures (mitigations to wire in this slice)

| Failure | Mitigation |
|---|---|
| Discord API down / network partition during escalation | Escalation request created in DB regardless. Cron retries delivery every 60s for up to `escalation_timeout_minutes`. SMS tier-2 fallback (slice 7) for priority ≥ 3. |
| User on mobile, didn't see ping | `escalation_timeout_minutes` default 60. Tasks below daily-pause-threshold survive timeout into `paused` state. |
| Stale button click (escalation already resolved) | Buttons carry the `correlation_id`. Click handler checks `status='open'` before mutating. |
| Replay attack (old message clicked) | Same correlation_id check + buttons disabled (component re-render) on resolution. |
| Wrong-account approval | Discord interaction `user.id` must match the configured `OWNER_DISCORD_ID`. Reject + log otherwise. |

## Relevant Docs

- `CLAUDE.md` (always)
- `docs/superpowers/specs/manual-escalation.md` (canonical)
- `spec_v3.md §13.1`, `§16.1`, `§4.3` (invocation_log)
- `slices/slice_07_sms_escalation.md` — tier-2 fallback this slice integrates with
- `src/donna/integrations/discord_views.py` — `AgentActionView` is the pattern for the four-button view (canonical spec §14)
- `src/donna/cost/budget.py`, `tracker.py` — where the gating check goes

## What to Build

> *Resolve the brainstorm gaps below before filling in this section.*

## Implementation Notes

> *Resolve the brainstorm gaps below before filling in this section.*

## Test Plan

> *Resolve the brainstorm gaps below before filling in this section.*

## Open Questions

- See spec §12 — open questions 1, 2, 5 are most relevant to this slice.

## Not in Scope

- `api_extended` button + grant logic (slice 18).
- Dashboard rendering of escalations (slice 19).
- `chat` and `claude_code` modes (slices 20, 21).
- Tool gap surfacing (slice 22).
- Dashboard runtime overrides UI (slice 23).

## Session Context

Load only: `CLAUDE.md`, this slice brief, the canonical spec (`docs/superpowers/specs/manual-escalation.md`), `spec_v3.md §13.1 + §16.1`, `slices/slice_07_sms_escalation.md`, the existing `discord_views.py`, `cost/budget.py`, `cost/tracker.py`.

## Brainstorm Gaps (resolve before implementation)

> Run the superpowers brainstorm skill against this slice to fill in `What to Build`, `Implementation Notes`, and `Test Plan` above before writing any code.

- [ ] Confirm `OWNER_DISCORD_ID` source (env var vs vault vs auth.yaml — spec §12 Q1).
- [ ] Pick the correlation ID format (ULID vs UUIDv7) and document.
- [ ] Decide where the cost-router gating check lives — `budget_guard` extension or a new `EscalationGate` module.
- [ ] Confirm cron / scheduler used for the 60s Discord retry loop — reuse `AsyncCronScheduler` or a one-shot asyncio task per request?
- [ ] Decide whether `dashboard_setting` resolution layer ships here (read-only fallback to YAML) or is deferred to slice 23. Recommendation: ship the read path here so later slices can flip toggles via SQL during testing.
- [ ] Resolve the `escalation_timed_out` → SMS tier-2 wiring with slice 7's existing tier definitions — does priority ≥ 4 still match current SMS rate-limit policy?
- [ ] Determine `paused` task semantics: is this an existing task state, or do we add it? Cross-check `config/task_states.yaml`.
- [ ] Decide: do we land an Alembic migration per table or a single revision for all three? (Spec §8 says "one revision per table" but this slice ships three tables.)

## Spec Drift Protocol

If implementation diverges from the canonical spec at `docs/superpowers/specs/manual-escalation.md`, the **same PR that introduces the divergence** must update the affected `§` of that spec (and any cross-referenced `spec_v3.md` section) so the doc matches reality.

Per `CLAUDE.md`: *"When a PR changes behavior, schema, routing, config contract, or external integration that the spec describes, update the affected `§` in the same PR. If the drift is intentional but the spec update is out of scope, call it out explicitly in the PR description so it can be reconciled later rather than silently drifting."*

Drift checklist for this slice:

- [ ] Did the schema change differ from §8? Update §8.
- [ ] Did the config keys differ from §6.1? Update §6.1.
- [ ] Did the failure mitigations differ from §10.1? Update §10.1.
- [ ] Did acceptance criteria need adjustment? Update §11.
- [ ] Did decisions in §15 turn out wrong? Add a §15 entry with the updated decision and date.
- [ ] Did the upstream `spec_v3.md §13.1` / §16.1` stubs need updating to reflect what actually shipped? Update them.
