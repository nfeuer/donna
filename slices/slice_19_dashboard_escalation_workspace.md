# Slice 19: Dashboard Escalation Workspace

> **Goal:** Stand up the canonical surface for viewing and submitting escalations: `/admin/escalations` list view + `/admin/escalations/<correlation_id>` detail view + submit endpoint. This slice ships BEFORE the chat and claude_code modes (slices 20, 21) — they require this surface to exist. Submit endpoint is mode-agnostic for slice 19; mode-specific UIs (textarea vs "Mark as built" modal) attach in their respective slices.

## Spec Reference

**Canonical spec:** [`docs/superpowers/specs/manual-escalation.md`](../docs/superpowers/specs/manual-escalation.md)
**Sections this slice realizes:** §6.3(b) (escalation workspace), §5.2 / §5.3 dashboard surfaces (the *generic* parts — mode-specific UIs land in slices 20/21), §11 functional ACs related to dashboard rendering and submission, §10.7 row 1 (toggle race / optimistic lock infrastructure used by the submit endpoint too).
**Related upstream specs:** `spec_v3.md` is silent on the dashboard escalation page; this is net-new surface area. The existing `docs/domain/management-gui.md` patterns apply (admin section, no auth, dark theme, 30s refresh).

This slice is bound to the canonical spec above. Read it before starting work. Cite the relevant `§` in the PR description.

## Spec Excerpts

### §6.3(b) — Escalation workspace

New dashboard area at `/admin/escalations`:

- **List view**: all escalation_requests with status filter (`open | submitted | validated | failed | cancelled`), sort by age, expandable rows showing the summary inline.
- **Detail view** at `/admin/escalations/<correlation_id>`:
  - Full prompt rendered with one-click copy
  - Mode-specific submission UI:
    - `chat`: textarea + Submit button (slice 20 attaches behavior)
    - `claude_code`: copy worktree command, copy spec, "Mark as built" modal (slice 21 attaches behavior)
  - Status timeline (each `invocation_log` lifecycle row)
  - Validation result panel (post-submission)
  - Re-submit affordance if validation failed (within iteration cap)
- **Auth**: same auth as the rest of admin dashboard. Multi-user-ready via `user_id` filter on the list view.

### §5.2 / §5.3 — Dashboard surfaces this slice ships generically

- Full prompt rendered (markdown → HTML, syntax-highlighted code blocks).
- One-click **Copy prompt** button (works for both modes).
- Estimate, daily remaining, task_type, age display.
- Status timeline component (consumes `invocation_log` rows).
- POST endpoint `/admin/escalations/<correlation_id>/submit` accepting a JSON payload with `mode`-discriminated body (validated against `schemas/escalation_submission.json`).

### §11 — Functional ACs realized by this slice

- [ ] Dashboard `/admin/escalations/<id>` page renders prompt, accepts answers (chat) or branch confirmations (claude_code) — chat textarea + claude_code "Mark as built" modal land in slices 20/21, but the page scaffolding + endpoint ship here.

## Relevant Docs

- `CLAUDE.md`
- Canonical spec, especially §6.3(b), §5.2, §5.3
- `docs/domain/management-gui.md` — convention anchors (no auth, dark theme, 30s refresh, card-on-Dashboard pattern)
- `docs/superpowers/plans/2026-04-21-wave-4-skill-system-ui.md` — most recent dashboard slice; the patterns it sets (PageHeader + filters + table + drawer) should be reused
- `donna-ui/src/pages/Shadow/index.tsx` — closest existing analogue for the list+detail pattern
- `donna-ui/src/api/` — fetcher module pattern
- `src/donna/api/routes/admin_dashboard.py` — backend surface
- Slice 17's `escalation_request` schema is the data source

## What to Build

1. **Schema additions** — Alembic revision
   `d8e9f0a1b2c3_escalation_workspace_columns.py` adding to
   `escalation_request`:
   - `prompt_body TEXT` — full prompt rendered by the dashboard
   - `summary TEXT` — short summary used by the Discord notification
     (slice 20) and the list view's inline cell
   - `mode TEXT` — chosen manual mode (`chat` | `claude_code`); kept
     alongside `resolution` so list-view filters and submit validation
     don't have to special-case `resolution` values
   - `result TEXT` — JSON-stringified submission payload (post-submit)
   - `validation_result JSON` — post-validation panel content
   The SQLAlchemy `EscalationRequest` model gains the same five fields.

2. **JSON schema** — `schemas/escalation_submission.json`. A
   discriminated `oneOf` on `mode`:
   - `{ "mode": "chat", "answer": string ≥ 50 }`
   - `{ "mode": "claude_code", "branch": string, "sha"?: string }`

3. **Backend endpoints** in `src/donna/api/routes/admin_escalations.py`
   (registered at `/admin` prefix, `admin_router()` auth):
   - `GET /admin/escalations` — list view; filters by `status`/`user_id`,
     paginates, returns `status_counts` for the filter chips. Open rows
     sort to the top by age, then everything else by `created_at` desc.
   - `GET /admin/escalations/{correlation_id}` — detail; returns the
     full row (including `prompt_body`, `result`, `validation_result`)
     plus the `escalation_lifecycle` audit trail joined from
     `invocation_log`.
   - `POST /admin/escalations/{correlation_id}/submit` — mode-agnostic
     submit. Validates payload against `schemas/escalation_submission.json`,
     enforces the row's `mode` matches the payload's, and uses an
     optimistic `WHERE status IN ('resolved','failed')` lock to avoid
     racing submissions. Re-submit after `failed` increments
     `iteration`. Writes an `escalation_submitted` audit row.

4. **Frontend** in `donna-ui/`:
   - `src/api/escalations.ts` — typed fetcher (list, detail, submit).
   - `src/pages/Escalations/index.tsx` — list page with status filter,
     30s auto-refresh, status-count strip, click-through to detail.
   - `src/pages/Escalations/EscalationsTable.tsx` — TanStack table
     showing task type, status pill, mode pill, estimate, daily-left,
     iteration, summary, age.
   - `src/pages/Escalations/EscalationDetail.tsx` — two-pane layout:
     left = prompt + Copy button + submission placeholder + validation
     panel; right = metadata grid + lifecycle timeline. Slices 20/21
     replace the submission placeholder with their mode-specific UI.
   - Routes added to `src/App.tsx`; `Escalations` entry added to
     `src/layout/Sidebar.tsx` with the `AlertOctagon` lucide icon.

## Implementation Notes

- **Auth (open question resolved):** `admin_router()` gives the same
  admin auth as the rest of the dashboard. `docs/domain/management-gui.md`'s
  "no auth" line refers to the `/admin/*` API in single-user homelab
  deployments where the `_admin_dep` resolves trivially; the spec's
  "same auth as the rest of admin dashboard" wording is honoured by
  using the same factory.
- **Submit endpoint URL (open question resolved):** kept under
  `/admin/escalations/<id>/submit` per spec §5.2. The
  `donna-ui` SPA route is `/escalations[/<correlation_id>]` because that
  matches the existing UI convention (no `/admin` prefix); see slice 19
  follow-up entry in `docs/superpowers/specs/followups.md`.
- **Status timeline (gap resolved):** read directly from
  `invocation_log` rows where `task_type = 'escalation_lifecycle' AND
  escalation_request_id = <id>` ordered by timestamp. Each row's
  `output` JSON carries the event name and payload (already the format
  written by `donna.cost.escalation_audit.write_escalation_event`).
  No derived `escalation_event` table is needed.
- **Markdown renderer (gap deferred):** the prompt is rendered as
  preformatted text in slice 19. Adding `react-markdown` + syntax
  highlighting is deferred to slice 20 (chat mode) where prompt
  authoring decisions are made.
- **Optimistic lock (gap resolved):** the submit endpoint's UPDATE
  matches `WHERE status IN ('resolved','failed')`. A second concurrent
  POST sees `rowcount = 0` and gets a 409 `concurrent_submission`. The
  detail GET is read-only; if the row advances mid-view, the next
  refresh shows the new state.
- **Re-submit affordance (gap resolved):** same `correlation_id`,
  `iteration` increments. `parent_escalation_id` chains are reserved
  for the case where a `tool_request` spawns from a failed iteration —
  not yet wired here. The submit endpoint increments `iteration` only
  when the prior status was `failed`.
- **Mobile responsiveness (gap):** desktop-first like the rest of the
  admin dashboard; the detail layout collapses to a single column at
  ≤1024px so it's readable on a phone, but no mobile-specific tooling.
- **Validation panel placeholder (gap resolved):** when
  `status ∈ {open, resolved}` the panel says "Validation runs after
  submission"; when `validation_result` is null but the status is
  `submitted` it says "No validation result recorded yet".

## Test Plan

- **Backend integration tests** in
  `tests/integration/test_admin_escalations.py` (real aiosqlite, FastAPI
  with the admin dep stubbed):
  - List endpoint: empty state, status counts, status filter, invalid
    status returns 400.
  - Detail endpoint: returns prompt_body + timeline events ordered by
    timestamp; 404 when correlation_id missing.
  - Submit endpoint: chat happy path (answer ≥ 50 chars), claude_code
    happy path (branch + sha), short answer rejected with
    `schema_validation_failed`, mode mismatch returns 409, submit
    against an `open` row returns 409, re-submit after `failed`
    increments iteration.
- **Frontend:** TypeScript strict-build via `npx tsc --noEmit` and
  `npx vite build` in `donna-ui/` confirm the page + API typings
  compile.
- **Migration round-trip:** existing
  `tests/integration/test_slice_17_migration.py` exercises the full
  Alembic head with the new revision included
  (`d8e9f0a1b2c3_escalation_workspace_columns`).
- **Out of scope here** (lands in slices 20/21): chat textarea
  submission flow, "Mark as built" modal, validation result population.

## Open Questions

- Dashboard auth: spec says "same auth as rest of admin dashboard". `docs/domain/management-gui.md` says "no auth". Confirm which is canonical for this homelab single-user phase.
- Should the submit endpoint live under `/admin/...` or `/api/escalations/...`? The convention split in `admin_dashboard.py` may already answer this.

## Not in Scope

- Mode-specific submission UI behavior (slices 20, 21 attach textarea logic and Mark-as-built modal logic).
- Toggle control panel (slice 23).
- Validation result rendering content — slice 19 ships the *panel*; what populates it is slices 20/21.
- Budget extension UI (slice 18 + slice 23 split).

## Session Context

Load only: `CLAUDE.md`, this brief, the canonical spec, `docs/domain/management-gui.md`, `docs/superpowers/plans/2026-04-21-wave-4-skill-system-ui.md` (latest dashboard pattern reference), slice 17's outputs, the existing `donna-ui/src/pages/Shadow/index.tsx` page as the pattern to mirror.

## Brainstorm Gaps (resolve before implementation)

> Run the superpowers brainstorm skill against this slice.

- [ ] Confirm dashboard auth model (per open question above).
- [ ] Define `schemas/escalation_submission.json` shape — discriminated union by mode? Single envelope with optional fields?
- [ ] Decide how the status timeline reads `invocation_log` rows efficiently — direct DB read vs a derived `escalation_event` table.
- [ ] Pick the markdown renderer (existing in donna-ui or add `react-markdown` + `rehype-highlight`).
- [ ] Page-level optimistic lock semantics: if user A is viewing detail page while user B (or background process) advances state, how does the page reconcile?
- [ ] Re-submit affordance: same correlation_id with iteration++, or a new `escalation_request` row with `parent_escalation_id`? (Spec §8 has `parent_escalation_id` for this.)
- [ ] Mobile responsiveness scope — full responsive, or admin-desktop-only matching existing dashboard?
- [ ] Define what "Validation result panel" displays when status='open' (placeholder vs hidden).

## Spec Drift Protocol

If implementation diverges from the canonical spec at `docs/superpowers/specs/manual-escalation.md`, the **same PR that introduces the divergence** must update the affected `§` of that spec (and any cross-referenced `spec_v3.md` section) so the doc matches reality.

Per `CLAUDE.md`: *"When a PR changes behavior, schema, routing, config contract, or external integration that the spec describes, update the affected `§` in the same PR."*

Drift checklist for this slice:

- [ ] Did the dashboard surface differ from §6.3(b)? Update §6.3(b).
- [ ] Did the submit endpoint contract differ from §5.2/§5.3? Update those.
- [ ] Did the schema for the submission payload differ from §9 (`schemas/escalation_submission.json`)? Update §9.
- [ ] Did acceptance criteria need adjustment? Update §11.
- [ ] Did `docs/domain/management-gui.md` need a new subsection? Add it.
