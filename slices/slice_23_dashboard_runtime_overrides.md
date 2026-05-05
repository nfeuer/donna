# Slice 23: Dashboard Runtime Overrides

> **Goal:** Surface every YAML default from `config/manual_escalation.yaml` and the per-task-type `manual_escalation` block as dashboard controls. Slice 17 created the `dashboard_setting` table and a read-only resolution layer; this slice adds the *write* side: UI cards for toggles, optimistic locking on writes, and per-task-type override grid. After this slice, the user can flip any escalation behavior from the dashboard without restarting the orchestrator.

## Spec Reference

**Canonical spec:** [`docs/superpowers/specs/manual-escalation.md`](../docs/superpowers/specs/manual-escalation.md)
**Sections this slice realizes:** §6.2 (per-task-type config), §6.3(a) (toggle control panel — full), §10.7 (routing & toggle failures — all rows).
**Related upstream specs:** `docs/domain/management-gui.md` (dashboard conventions); no `spec_v3.md` cross-ref.

This slice is bound to the canonical spec above. Read it before starting work. Cite the relevant `§` in the PR description.

## Spec Excerpts

### §6.2 — Per-task-type config (`config/task_types.yaml` extension)

```yaml
task_types:
  skill_draft:
    manual_escalation:
      mode: claude_code
      target_paths:
        skill: "src/donna/skills/{name}.py"
        test:  "tests/skills/test_{name}.py"
      reference_module: "src/donna/skills/schema_inference.py"
  chat_escalation:
    manual_escalation:
      mode: chat
  evolution:
    manual_escalation:
      mode: claude_code
      target_paths:
        skill: "src/donna/skills/{name}.py"
      reference_module: "src/donna/skills/{name}.py"
```

Task types without a `manual_escalation` block are **never** offered manual mode — only `Approve / Pause / Cancel`.

### §6.3(a) — Toggle control panel

`dashboard_setting` resolution order: `dashboard_setting` → YAML default. This slice adds the write path.

Dashboard surfaces (admin section):
- Master kill switch: **Manual escalation**: On / Off
- Per-mode toggles: **Chat**, **Claude Code**
- Budget extension: **Allow extensions**: On / Off
- Slider: **Max daily extension** (capped at `hard_monthly_ceiling_usd / days_left_in_month`)
- Per-task-type override grid: each task type with a manual mode shows a row with `Auto / Force-API / Force-Manual / Disabled`.

`hard_monthly_ceiling_usd` is **not** dashboard-mutable — only YAML. Prevents a compromised dashboard session from authorizing unlimited spend.

### §10.7 — Routing & toggle failures

| Failure | Mitigation |
|---|---|
| Dashboard toggle race (two browser tabs flip same setting) | `dashboard_setting` writes use `updated_at` optimistic lock; second write returns 409 with current value. |
| Config reload during in-flight escalation | Resolution semantics: an open escalation uses the offered_modes snapshotted in its row, NOT live config. Disabling claude_code mid-flight does not retroactively cancel an open claude_code escalation. |
| Task type has `manual_escalation: claude_code` but no reference_module configured | Validation at config load: any task type declaring claude_code mode MUST have target_paths + reference_module. Hard fail at boot. |
| Dashboard authentication compromised | `hard_monthly_ceiling_usd` is YAML-only (§6.3). Worst case attacker can run today's daily extension cap — bounded blast. |

## Relevant Docs

- `CLAUDE.md`
- Canonical spec, especially §6.2, §6.3(a), §10.7
- `docs/domain/management-gui.md` — convention anchors (cards on Dashboard, page-per-subsystem, dark theme, 30s refresh)
- `docs/superpowers/plans/2026-04-21-wave-4-skill-system-ui.md` — most recent dashboard slice for pattern reference
- Slices 17–22 — all already gated on the resolution layer; this slice flips the canonical surface from YAML to dashboard
- `donna-ui/src/api/preferences.ts` — fetcher pattern
- `donna-ui/src/primitives/Switch.tsx`, `Slider`, etc.

## What to Build

> *Resolve the brainstorm gaps below before filling in this section.*

## Implementation Notes

> *Resolve the brainstorm gaps below before filling in this section.*

## Test Plan

> *Resolve the brainstorm gaps below before filling in this section.*

## Open Questions

- Do existing dashboard cards have an established optimistic-locking pattern this slice should reuse, or is this the first?
- Where does the per-task-type override grid live — its own page or a section of the toggle card?

## Not in Scope

- New behaviors gated by these toggles — those are owned by slices 17–22. This slice only adds UI/API for runtime override.
- Multi-user-aware toggles (Phase 2).

## Session Context

Load only: `CLAUDE.md`, this brief, canonical spec, `docs/domain/management-gui.md`, the most recent dashboard slice plan, slice 17's `dashboard_setting` schema, the existing `donna-ui/src/api/preferences.ts` and any matching backend route.

## Brainstorm Gaps (resolve before implementation)

> Run the superpowers brainstorm skill against this slice.

- [ ] Optimistic lock contract for the API: client sends `updated_at` it last read; server compares and 409s on mismatch. What does the UI do on 409 — silent refetch + retry, or surface a "Setting changed elsewhere — reload?" toast?
- [ ] Define the per-task-type override grid's data source — it needs both YAML defaults and the override list joined, with empty rows for task types missing a `manual_escalation` block.
- [ ] Should the slider for `Max daily extension` show today's "remaining headroom under monthly ceiling" live, or only at page load?
- [ ] Validation at boot for §10.7 row 3 (`claude_code` mode requires `target_paths` + `reference_module`) — does the existing config loader have a place for this, or do we add a `validate_manual_escalation_config()` step?
- [ ] What happens when a toggle is flipped while an escalation is open? Spec §10.7 row 2 says no retroactive effect — confirm the implementation snapshots `offered_modes` at creation time (slice 17 should already do this — verify).
- [ ] Audit: should toggle changes write to `invocation_log` (with `task_type='dashboard_setting_change'`), or a dedicated audit table? `dashboard_setting.updated_by` already covers most needs.

## Spec Drift Protocol

If implementation diverges from the canonical spec at `docs/superpowers/specs/manual-escalation.md`, the **same PR that introduces the divergence** must update the affected `§` of that spec (and any cross-referenced `spec_v3.md` section) so the doc matches reality.

Per `CLAUDE.md`: *"When a PR changes behavior, schema, routing, config contract, or external integration that the spec describes, update the affected `§` in the same PR."*

Drift checklist for this slice:

- [ ] Did the dashboard surface differ from §6.3(a)? Update §6.3(a).
- [ ] Did the per-task-type config differ from §6.2? Update §6.2.
- [ ] Did the failure mitigations differ from §10.7? Update §10.7.
- [ ] Did acceptance criteria need adjustment? Update §11.
- [ ] Did `docs/domain/management-gui.md` need a new subsection for the escalation toggle card? Add it.
