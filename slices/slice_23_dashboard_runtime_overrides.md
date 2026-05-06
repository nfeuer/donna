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

1. **Catalog module** at `src/donna/cost/dashboard_settings_catalog.py` —
   single source of truth for the keys the dashboard exposes, their
   types, legacy aliases, and the slider-cap helpers.
2. **Optimistic-lock writer** —
   `EscalationRepository.set_dashboard_setting_with_lock(key, value, *,
   expected_updated_at, updated_by)` returns `(ok, current_value,
   updated_at, updated_by)` so a 409 carries enough state for the UI to
   self-correct without a re-fetch round trip.
3. **Admin routes** at `src/donna/api/routes/admin_escalation_settings.py` —
   `GET /admin/escalation-settings`,
   `PUT /admin/escalation-settings/{key:path}`,
   `PUT /admin/escalation-settings/task-types/{task_type}`.
4. **Boot validation** — `validate_manual_escalation_config(task_types)` in
   `src/donna/config.py`, called from `cli_wiring.build_startup_context` and
   the API `lifespan`. Hard-fails when a `claude_code`-mode task type lacks
   `target_paths` or `reference_module` (§10.7 row 3).
5. **Gate plumbing** — `EscalationGate` consumes the per-task-type
   override (`auto / force_api / force_manual / disabled`) and the new
   `max_daily_extension_usd` slider through the existing
   `DashboardSettingResolver`. Existing chat / claude_code / budget
   reads are migrated to canonical key names, with legacy aliases
   honoured for backward compatibility.
6. **Frontend page** at `donna-ui/src/pages/EscalationSettings/` — toggles,
   slider, and override grid bound to the API client at
   `donna-ui/src/api/escalationSettings.ts`. Sidebar nav entry.

## Implementation Notes

- **Resolution order** — `dashboard_setting → YAML default` (spec §6.3).
  The catalog records each setting's YAML default function so the GET
  response can show the override value AND the default side by side.
- **Canonical key namespace** — every key is the dot-path of the YAML
  structure under `manual_escalation.*`. Slice 17/18/21 had drifted to
  two short names; the resolver now consults the canonical key first
  and falls back to documented legacy aliases. New writes always go to
  canonical keys (see `S23` in `docs/superpowers/specs/followups.md`).
- **Audit** — successful writes also append an `escalation_lifecycle`
  row to `invocation_log` with `event='dashboard_setting_changed'`. The
  slice 19 timeline view picks these up automatically.
- **Slider cap** — `hard_monthly_ceiling_usd / days_left_in_month`
  recomputed server-side on each PUT (so a stale GET cannot smuggle a
  larger value through). The GET response includes the cap and the
  basis (`hard_monthly_ceiling_usd`, `days_left_in_month`) so the UI
  can render the slider's max + a help string in one trip.
- **Per-task-type override application** — applied **after**
  the per-mode preconditions are evaluated. `force_manual` cannot
  invent a chat or claude_code button when the underlying gate is not
  wired; `force_api` cannot conjure budget headroom that does not
  exist. `disabled` short-circuits to Pause / Cancel only.
- **Route ordering** — the per-task-type PUT is declared **before**
  the catch-all `{key:path}` PUT so FastAPI's path converter does not
  swallow `task-types/...` URLs.

## Test Plan

- `tests/unit/test_dashboard_settings_writes.py` — catalog drift guards,
  type coercion, slider-cap math, optimistic-lock happy-path / stale-
  token / no-row cases, and resolver fallback to legacy aliases.
- `tests/unit/test_manual_escalation_validation.py` — §10.7 row 3
  boot-time check covers no-block, chat-only, claude_code-with-full-
  contract, missing-target_paths, missing-reference_module, and
  multi-offender enumeration.
- `tests/unit/test_escalation_gate_overrides.py` — `auto / force_api /
  force_manual / disabled` each produce the right `offered_modes` set,
  and the slider's stored value lowers headroom past the YAML default.
- `tests/integration/test_admin_escalation_settings.py` — end-to-end
  through `httpx.ASGITransport` covering GET shape, override grid
  filtering, audit row written, optimistic-lock 409, value-type 422,
  slider over-cap 422, and the path-routes-block check that prevents
  the catch-all PUT from masquerading as the task-type endpoint.
- UI build (`npx vite build`) and typecheck (`npx tsc --noEmit`) must
  pass — these are the bar per `donna-ui/CLAUDE.md`.

## Open Questions

- ~~Do existing dashboard cards have an established optimistic-locking pattern this slice should reuse, or is this the first?~~
  **Resolved (slice 23):** First subsystem with optimistic locks. Pattern
  lives at `EscalationRepository.set_dashboard_setting_with_lock` —
  reuseable for any future dashboard-mutable subsystem with the same
  `key TEXT PK, value JSON, updated_at, updated_by` schema.
- ~~Where does the per-task-type override grid live — its own page or a section of the toggle card?~~
  **Resolved (slice 23):** dedicated `/escalation-settings` page with a
  separate sidebar entry. The escalations workspace is per-row;
  settings are per-subsystem. See `S23` in the followups log.

## Not in Scope

- New behaviors gated by these toggles — those are owned by slices 17–22. This slice only adds UI/API for runtime override.
- Multi-user-aware toggles (Phase 2).

## Session Context

Load only: `CLAUDE.md`, this brief, canonical spec, `docs/domain/management-gui.md`, the most recent dashboard slice plan, slice 17's `dashboard_setting` schema, the existing `donna-ui/src/api/preferences.ts` and any matching backend route.

## Brainstorm Gaps (resolve before implementation)

> Run the superpowers brainstorm skill against this slice.

- [x] Optimistic lock — UI 409 behaviour: **visible toast + replace-in-place
      with the live state**. Silent retry could race with another tab; toast
      keeps the user in control. See `S23` in `docs/superpowers/specs/followups.md`.
- [x] Per-task-type override grid data source: catalog joins
      `task_types_config` (only entries with a `manual_escalation` block) with
      `dashboard_setting` rows under the
      `manual_escalation.task_types.<task_type>.override` key prefix. Default
      is `auto` when no override exists.
- [x] Slider headroom: **page-load only**. Cap (`hard_monthly_ceiling_usd /
      days_left_in_month`) is computed at GET and re-validated at PUT, so
      stale UI cannot smuggle an over-ceiling value. See `S23` slider entry
      in followups.
- [x] Boot validation: new `validate_manual_escalation_config(task_types)`
      in `src/donna/config.py`, raising `ManualEscalationConfigError`. Wired
      into both `cli_wiring.build_startup_context` and the API lifespan
      startup so either process catches drift on boot.
- [x] In-flight escalations: confirmed — the gate snapshots `offered_modes`
      onto the row at fire time (`EscalationGate.fire_and_wait` → `repo.create`).
      Slice 23 toggles do not retroactively rewrite open rows. Spec §10.7 row 2
      already covered this; no new code needed.
- [x] Audit: toggle changes write an `escalation_lifecycle` row to
      `invocation_log` (`event='dashboard_setting_changed'`) so the slice 19
      timeline view surfaces them alongside actual escalations. Reusing
      `invocation_log` avoids a parallel audit table.

## Spec Drift Protocol

If implementation diverges from the canonical spec at `docs/superpowers/specs/manual-escalation.md`, the **same PR that introduces the divergence** must update the affected `§` of that spec (and any cross-referenced `spec_v3.md` section) so the doc matches reality.

Per `CLAUDE.md`: *"When a PR changes behavior, schema, routing, config contract, or external integration that the spec describes, update the affected `§` in the same PR."*

Drift checklist for this slice:

- [x] §6.3(a) updated with the canonical key namespace, the API
      contract, the slider-cap rule, the audit log entry, and the
      page route + sidebar.
- [x] §6.2 updated with a sentence pointing at §6.3(a) for the
      runtime override.
- [x] §10.7 row 1 updated with the `set_dashboard_setting_with_lock`
      contract; row 3 updated with the
      `validate_manual_escalation_config` function name + behaviour.
- [x] §11 acceptance criteria already covered "Dashboard toggles
      override YAML and take effect on next escalation (no restart)";
      no adjustment needed.
- [x] `docs/domain/management-gui.md` Manual Escalation Surfaces §
      replaced the "planned" toggle-card description with the shipped
      page + API + audit + ceiling notes.
