# Slice 25: Re-Escalation Chains & Token-Cap Recovery

> **Goal:** Close the long-deferred behavioural gap left after slices 17–24:
> when a `complete()` call hits the extension-derived token cap (pre-call
> input-only or post-call truncation), transparently re-estimate the spend,
> re-fire the escalation gate with `parent_escalation_id` linking back to
> the prior row, walk the resulting parent chain to enforce a configurable
> depth cap, and emit dedicated `escalation_lifecycle` audit rows so the
> dashboard timeline (slice 19/24) shows the entire chain. Also wires the
> conversation engine through the gate (S20-FU2).

## Spec Reference

**Canonical spec:** [`docs/superpowers/specs/manual-escalation.md`](../docs/superpowers/specs/manual-escalation.md)

**Sections this slice realises:**

- §4 (over-budget decision tree — extends with re-fire branch)
- §5.1 `api_extended` mode — token-cap recovery loop (was only enforcement)
- §10.6 row 1 — *re-estimate + re-escalation on overspend* (was deferred per `followups.md#S24`)
- §10.10 — three new audit events: `re_escalation_offered`,
  `re_escalation_chain_capped`, `re_escalation_token_limited`
- §11 functional — two new acceptance criteria for the recovery loop
- §12 Q5 — `max_re_escalation_depth` resolution (was the open question per
  `followups.md#S24` and `#S21`)
- §15 — new decision-log entries for the chain heuristic and depth cap

**Related upstream specs:**
`spec_v3.md §13.1` (Budget Rules — re-estimation never crosses
`hard_monthly_ceiling_usd`), `spec_v3.md §16.1` (Schemas — confirms the
existing `parent_escalation_id` FK and the new index).

## Spec Excerpts (current state, what changes below)

### §10.6 row 1 (current — pinned by slice 18, audit residue per `followups.md#S24`)

> User approves extension; then estimate was wrong; actual cost overshoots →
> *API call's `complete()` enforces a hard token limit derived from
> `extension_amount × token_rate`. Truncated output triggers a re-estimate
> + re-escalation rather than silent overspend.*

**Today:** `ModelRouter.complete()` raises `TokenLimitReachedError`
(slice 18). No caller catches it; tasks fail. The "re-estimate + re-escalation"
half is unimplemented (logged in `followups.md#S18` parent-chain wiring,
`#S24` §10.6 row 1 audit residue).

**Slice 25 closes this** by introducing
`donna.cost.re_escalation_coordinator.ReEscalationCoordinator` and
invoking it from `ModelRouter.complete()` so the re-fire is centralised
and every existing caller benefits without surface changes.

### §12 Q5 (current)

> Re-escalation parent chains — current spec stores `parent_escalation_id`.
> Do we need a depth limit beyond `manual_iteration_limit`? *Open. Slice 24
> audited the path … Adding `max_re_escalation_depth` is a new behaviour
> and explicitly out of slice-24 scope … Logged in `followups.md#S24` for
> the next behavioural slice.*

**Slice 25 resolves Q5** with `manual_escalation.triggers.max_re_escalation_depth`
(default 5), enforced in `EscalationGate.fire_and_wait` before row creation.

## Relevant Docs

- `CLAUDE.md`
- Canonical spec, especially §4, §5.1, §10.6, §10.10, §12 Q5, §15
- Slices 17 (`escalation_request` schema), 18 (`api_extended` enforcement
  + `TokenLimitReachedError`), 19 (audit timeline), 21 (parent-chain
  use-case for iteration limits), 24 (drift guard, multi-user fixture)
- `docs/superpowers/specs/followups.md` — entries S18, S21, S24
  (`§10.6 row 1`, `§12 Q5`)
- `src/donna/models/router.py` — both `TokenLimitReachedError` raise
  sites (lines ~414 pre-call and ~477 post-call)
- `src/donna/cost/escalation_gate.py` — `fire_and_wait` is the integration
  point
- `src/donna/cost/escalation_repository.py` — `create()` adds the
  `parent_escalation_id` kwarg
- `src/donna/cost/escalation_audit.py` — new event constants land here
- `src/donna/chat/engine.py` — S20-FU2 wiring lives here

## What to Build

This is a **single-theme behavioural slice** that closes three coupled
followups with one feature.

### 1. `max_re_escalation_depth` config (§12 Q5)

- New field on `ManualEscalationTriggersConfig`:
  `max_re_escalation_depth: int = 5`. Surfaced in
  `config/manual_escalation.yaml` next to `manual_iteration_limit` with
  a comment naming this slice and the spec section.
- Dashboard runtime override at key
  `manual_escalation.triggers.max_re_escalation_depth` via the slice-23
  pipeline (`DashboardSettingResolver`). One-liner addition to the
  resolver's known-keys list and the React settings card.
- Validated as a positive integer ≥ 1; YAML-load failures log
  `manual_escalation_invalid_re_escalation_depth` and fall back to the
  default rather than crashing boot (consistent with the slice-23 reload
  semantics).

### 2. Repository — chain-walk + parent FK (§4, §10.10)

- `EscalationRepository.create()` grows a `parent_escalation_id: int | None
  = None` keyword. Persisted into the existing column. Backwards-compatible
  default keeps every prior caller working.
- New `EscalationRepository.find_chain_depth(escalation_request_id: int)
  -> int` — walks `parent_escalation_id` upward, returning the depth of
  the *root* row (0 = no parent, 1 = one ancestor, …). Implemented as a
  recursive CTE in SQLite; falls back to a single SELECT when the column
  is NULL on the seed row.
- New index `ix_escalation_request_parent_id` on
  `escalation_request(parent_escalation_id)` — added via Alembic
  revision `f0a1b2c3d4e5_re_escalation_parent_index.py`. Mirrored on
  the SQLAlchemy model so the slice-24 ORM/Alembic drift guard stays
  green (no new column; just an index — verify via the same comparison
  helper).

### 3. Gate — depth check + parent kwarg (§4, §10.6, §10.10)

- `EscalationGate.fire_and_wait()` accepts `parent_escalation_id: int |
  None = None` (kwarg, default `None`). When set:
  1. Resolves `triggers.max_re_escalation_depth` through the
     `DashboardSettingResolver`.
  2. Walks the chain via `repo.find_chain_depth(parent_escalation_id) +
     1`. If the result exceeds the cap, **does not create a row**:
     writes a single `escalation_lifecycle` audit event with payload
     `{event: "re_escalation_chain_capped", parent_id, depth, cap}` and
     returns a `GateOutcome(fired=True, mode="cancel",
     resolved_by="system", escalation_request_id=parent_escalation_id,
     correlation_id=<parent's correlation_id>)`. Caller sees a
     `cancel` resolution and falls through normal cancel handling
     (task transitions to `cancelled`, no API spend).
  3. Otherwise creates the row with `parent_escalation_id` set; emits
     a `re_escalation_offered` audit event before the standard
     `escalation_offered` row so the dashboard timeline shows the
     re-fire as a distinct chain link.
- The dedup guard at the top of `fire_and_wait` is **bypassed** for
  re-fires: a re-escalation by definition relates to an in-flight
  originating entity, so the `find_open_for_originating_entity` check
  would otherwise short-circuit every chain past the first link. Bypass
  is gated explicitly on `parent_escalation_id is not None`.

### 4. `ReEscalationCoordinator` (§10.6 row 1)

New module `src/donna/cost/re_escalation_coordinator.py`:

```python
class ReEscalationCoordinator:
    """Centralises the catch-and-re-fire loop for token-cap recovery."""

    def __init__(self, *, gate: EscalationGate, models_config: ModelsConfig,
                 max_in_flight_attempts: int = 3) -> None: ...

    async def recover(
        self,
        *,
        token_error: TokenLimitReachedError,
        user_id: str,
        task_id: str | None,
        task_type: str,
        priority: int,
        originating_entity: tuple[str, str] | None,
        target_paths: dict[str, str] | None,
        base_sha: str | None,
        original_prompt: str,
        previous_estimate_usd: float,
        previous_extension_usd: float | None,
        consumed_tokens: int | None,
        model_alias: str,
    ) -> GateOutcome: ...
```

- Computes the new estimate: `max(previous_estimate × 2,
  observed_input_cost + (extension_amount × 2))`. Hard-clamped to the
  YAML `hard_monthly_ceiling_usd` minus the user's month-to-date
  extension consumption — never asks the user to approve a number the
  ceiling would refuse anyway.
- Walks the parent chain via `EscalationRepository.find_chain_depth`
  before invoking the gate, so a chain-capped recovery fails fast with
  a `re_escalation_chain_capped` audit row and re-raises the original
  `TokenLimitReachedError` (so the caller's existing failure path is
  untouched).
- A coordinator-level `max_in_flight_attempts` (default 3) bounds the
  while-loop locally; the spec-level `max_re_escalation_depth` bounds
  the persisted chain. Two layers because the loop's local cap is a
  defensive guard against a misconfigured DB allowing an unbounded
  chain — keeps memory + audit volume bounded even if the persisted
  cap is mis-set.

### 5. Router wiring (§4, §10.6 row 1)

- `ModelRouter` constructor grows an optional
  `re_escalation_coordinator: ReEscalationCoordinator | None = None`.
- Inside `complete()`, both `TokenLimitReachedError` raise sites become
  `try` blocks that call `coordinator.recover(...)` if configured.
  - On `mode='api_extended'` from the recovery: re-call `complete()`
    recursively with the new estimate and `parent_escalation_id` set.
    Recursion depth bounded by the coordinator (so the same router
    method body handles every link).
  - On any other mode: raise `EscalationDecisionError` exactly as
    today's gate path does.
  - On `mode='cancel'` from the chain-cap branch: re-raise the original
    `TokenLimitReachedError` so the caller still sees a token-budget
    failure (the user wasn't asked anything; the system gave up).
- `cli_wiring.build_startup_context` constructs the coordinator after
  the gate is wired and calls `router.set_re_escalation_coordinator(...)`
  before the orchestrator starts. Boot logs
  `re_escalation_coordinator_wired` once; if `escalation_gate is None`
  the coordinator is **not** built and the router behaves exactly as
  it does today.

### 6. Audit events (§10.10)

Three new constants in `donna.cost.escalation_audit`:

- `EVENT_RE_ESCALATION_OFFERED = "re_escalation_offered"` — written
  *before* the standard `escalation_offered` row when a re-fire creates
  a child. Payload: `{parent_id, parent_correlation_id, depth,
  previous_estimate_usd, new_estimate_usd, consumed_tokens}`.
- `EVENT_RE_ESCALATION_CAP_REACHED = "re_escalation_chain_capped"` —
  written when the depth cap rejects the re-fire. Payload:
  `{parent_id, depth, cap}`. Row is keyed off the parent's
  `escalation_request_id` (not a new row) so the parent's timeline
  carries the failure marker.
- `EVENT_RE_ESCALATION_TOKEN_LIMITED = "re_escalation_token_limited"` —
  written when the coordinator finishes its in-flight loop without a
  recovery (all chain links resolved to a non-`api_extended` mode).
  Payload: `{root_correlation_id, depth, last_outcome_mode}`.

The slice-19/24 dashboard timeline picks these up automatically because
the timeline endpoint already aggregates by `escalation_request_id` and
renders any `escalation_lifecycle` row.

### 7. ConversationEngine — S20-FU2 (chat-mode wiring)

- `ConversationEngine.handle_escalation` now passes `estimate_usd=
  self._estimate_escalation_cost()` and catches
  `EscalationDecisionError(mode='chat')` — the user's escalation prompt
  is then deflected into the chat-mode handoff (Discord summary +
  dashboard submit) rather than dying with an unhandled exception.
- Returns a `ChatResponse` whose `text` says "I've sent this to your
  Discord — answer there or in the dashboard" so the user knows where
  the round-trip lives. The session's pinned task ID stays on the
  response so the chat surface is unambiguous.
- The estimate calculator (`_estimate_escalation_cost`) becomes a real
  pre-flight estimate — `tokens_in_estimate * input_cost_per_token +
  expected_tokens_out * output_cost_per_token` — read off the
  `chat_escalation` task type's resolved model alias. Today it returns
  a hardcoded value; slice 25 grounds it in the actual model rates.

## Implementation Notes

- **No new schema column.** `parent_escalation_id` already exists per
  slice-17 migration `c7d8e9f0a1b2_escalation_core.py` and is in the
  ORM at `src/donna/tasks/db_models.py:757`. Slice 25 only persists
  into it (every prior slice silently left it `NULL`).
- **Index, not column.** The recursive-CTE chain walk would scan the
  table for every re-fire without `ix_escalation_request_parent_id`.
  Adding the index is a micro-migration but it's still a schema change,
  so it goes through Alembic, the slice-24 drift guard, and the
  `Base.metadata.create_all` test path.
- **Recursion in `complete()` is shallow and bounded.** The
  coordinator's `max_in_flight_attempts` plus the gate's persisted
  `max_re_escalation_depth` mean the recursion can't blow the stack
  even under a config mistake. We *could* re-write the router's
  recovery loop iteratively, but the recursive shape exactly matches
  the request graph and a 5-deep stack is unmeasurable.
- **Cancellation semantics on chain-cap.** The chain-cap path returns
  `mode='cancel'` because the user *did not* see a button — the system
  gave up. The downstream caller (auto_drafter / evolution / chat
  engine) already handles `cancel` as terminal-without-spend, so no
  caller-side work is needed.
- **Re-estimate heuristic is configurable but defaulted.** The
  multiplier sits in
  `triggers.re_escalation_estimate_multiplier: float = 2.0`. Documented
  as a heuristic — an over-estimate is fine because the gate clamps to
  daily / monthly headroom anyway. Tested as part of the coordinator
  unit suite (boundary at clamp ceiling).
- **Idempotency on the chain head.** If `complete()` is re-entered
  after a coordinator hop, the new gate row's `correlation_id` is
  fresh (uuid7) — the parent's `correlation_id` is unaffected. The
  invocation_log row for the *actual* completed API call carries the
  *child*'s `escalation_request_id` (stamped from the recursive
  `complete()` call's gate outcome) so cost reporting attributes spend
  to the link that actually ran.
- **Conversation engine estimate.** Reads
  `_models_config.routing['chat_escalation'].model` → looks up
  `models[alias].input_cost_per_token_usd` /
  `output_cost_per_token_usd`. For unknown aliases (test fixtures with
  empty config) the engine logs `chat_escalation_estimate_unavailable`
  and skips the gate — preserves today's "always succeed" path for the
  test surface that doesn't wire a router with cost rates.

## Test Plan

All tests sit under existing folders so nothing about CI structure
changes.

- `tests/unit/test_re_escalation_coordinator.py` — eleven scenarios:
  recover with extension granted (happy path), recover with chain cap
  reached (fast-fail), recover with user picking pause (raises
  `EscalationDecisionError`), recover when gate not enabled (returns
  early), recover with consumed-tokens used in re-estimate, recover
  when ceiling clamps the multiplier, in-flight `max_in_flight_attempts`
  bound, audit-event payloads, child-row carries `parent_escalation_id`,
  parent-chain depth=0 path, parent-chain depth=N walk through several
  ancestors.
- `tests/unit/test_escalation_gate_chain_cap.py` — six scenarios:
  cap honoured at exact depth, cap honoured one over, cap not enforced
  with `parent_escalation_id=None`, dedup-bypass on re-fire, audit row
  written on cap, runtime override changes the cap mid-flight (slice-23
  pipeline test).
- `tests/unit/test_escalation_repository_chain.py` — five scenarios:
  `find_chain_depth` returns 0 for root, 1 for direct child, N for
  longer chains, handles a NULL `parent_escalation_id`, ignores rows
  for other users (multi-user safety — uses the slice-24 `two_user_ids`
  fixture).
- `tests/integration/test_re_escalation_recovery.py` — three scenarios:
  pre-call token cap recovers via coordinator, post-call token cap
  recovers, chain reaches cap and the original `TokenLimitReachedError`
  surfaces to the caller. Each scenario asserts the
  `escalation_lifecycle` audit chain that the dashboard timeline reads.
- `tests/integration/test_chat_engine_escalation.py` — extended with
  three new tests: `test_handle_escalation_passes_estimate_usd` (S20-FU2
  primary fix), `test_handle_escalation_chat_mode_branches_to_handoff`,
  `test_handle_escalation_estimate_falls_back_when_unconfigured`.
- `tests/unit/test_orm_alembic_consistency.py` — extended with one
  assertion that `escalation_request` carries the new
  `ix_escalation_request_parent_id` index in both the ORM and the
  Alembic-installed schema.
- `tests/integration/test_admin_escalations.py` — extended with one
  test that GETs `/timeline` after a re-escalation and asserts the
  three new audit events appear in `next_after_id` order.

## Open Questions

- **Recovery recursion vs iteration in `complete()`** — recursion keeps
  the call stack identical to the request graph and is at most
  `max_in_flight_attempts` deep. Iteration would be a defensive rewrite
  but adds complexity without changing observable behaviour. Going
  with recursion; flag for review.
- **Should `re_escalation_estimate_multiplier` be per-task-type?** —
  No, default 2.0 across the board. A future slice can promote it to
  a per-task-type knob if any task type is observed to systematically
  under- or over-estimate. Logged in followups.md if surfaced.
- **What if the user is asleep / unreachable mid-chain?** — Same answer
  as the rest of the gate: `escalation_timeout_minutes` (existing) auto-
  resolves to pause. The chain just terminates one link earlier.

## Not in Scope

- The worktree-style claude_code E2E harness (still deferred per
  `followups.md#S24`).
- The §10.4 row 4 dependent-skill regression (still deferred per
  `followups.md#S24`).
- The §11 Twilio-mock E2E for Discord-5xx retry (still deferred per
  `followups.md#S24`).
- Per-user `donna_models.yaml` budget migration (the §10.9 row 2
  follow-up — separate slice).
- Phase 2 multi-user activation (still gated on a separate slice that
  flips `auth.yaml`).
- Per-task-type multiplier override (see Open Questions).
- Programmatic re-estimation from a fresh model call ("ask Claude
  whether it really needs more tokens"). The heuristic multiplier is
  enough; calling Claude to size the next Claude call would be a recursive
  budgeting hole.

## Session Context

Load only: `CLAUDE.md`, this brief, the canonical spec sections cited
above, slices 17, 18, 21, 24 briefs, `tests/conftest.py`,
`src/donna/cost/escalation_gate.py`,
`src/donna/cost/escalation_repository.py`,
`src/donna/cost/escalation_audit.py`,
`src/donna/models/router.py`, `src/donna/chat/engine.py`,
`config/manual_escalation.yaml`,
`docs/superpowers/specs/followups.md` entries S18/S21/S24.

## Brainstorm Gaps (resolved before implementation)

- [x] **Where does the re-estimate heuristic live?** — Coordinator-side,
  configurable via `triggers.re_escalation_estimate_multiplier`,
  defaulted to 2.0, hard-clamped to monthly ceiling minus consumed
  extensions for the user.
- [x] **Two layers of caps (in-flight loop vs persisted chain)?** — Yes.
  In-flight is a defensive guard against a config mistake; persisted
  is the user-visible truth surfaced in the dashboard.
- [x] **Dedup-bypass safety on re-fire** — Bypass is gated on
  `parent_escalation_id is not None`. Any non-re-fire path keeps the
  slice-21 dedup contract untouched.
- [x] **Audit row keying for chain-cap** — Cap event is written against
  the *parent's* `escalation_request_id` so the timeline naturally
  shows the failure on the link that the user did see, rather than
  spawning an orphan row.
- [x] **Recursion depth in `complete()`** — Bounded by
  `max_in_flight_attempts`, default 3. A 3-deep recursion is well
  within Python defaults.
- [x] **Conversation-engine pre-flight estimate** — Read from the
  resolved model alias's cost rates via the router. Falls back to
  "skip the gate" with a structured warning when the alias is
  unconfigured (test surface).
- [x] **Multi-user safety** — Every new repository method is
  `user_id`-scoped (the chain-walk respects the original row's
  `user_id`); the slice-24 `two_user_ids` fixture parametrises the
  recovery integration test so cross-tenant chain pollution can't
  silently slip through.

## Spec Drift Protocol

Per `CLAUDE.md`: any divergence between this slice and the canonical
spec must be reconciled in the same PR.

Drift checklist for this slice:

- [ ] Did `§10.6 row 1` need a re-write? Yes — *was* "raises
      `TokenLimitReachedError`; re-estimate + re-escalation is open".
      Now: "raises through `ReEscalationCoordinator` which re-fires
      the gate with `parent_escalation_id` set, walks the chain to
      enforce `max_re_escalation_depth`, and emits `re_escalation_*`
      audit events." Update §10.6 row 1.
- [ ] Did `§12 Q5` need closing? Yes — moved from open to *resolved
      (slice 25)*. Update §12 Q5 to cite this slice.
- [ ] Did `§10.10` need new audit-event names? Yes — three new event
      strings are listed there.
- [ ] Did `§4` need a new branch in the decision tree? Yes — the
      "extension granted but token cap fired" path now reads "→
      coordinator re-fires the gate; if chain-cap → cancel; else
      proceed with the new extension".
- [ ] Did `§15` need new key-decision entries? Yes — three: depth-cap
      default, recovery heuristic multiplier, recursion-vs-iteration
      choice in `complete()`.
- [ ] Did the §11 Functional checklist need new boxes? Yes — two:
      token-cap recovery happy path, chain-cap surfaces correctly.
- [ ] Did `followups.md#S18` (token-cap re-escalation) /
      `#S18` (parent-chain not wired) / `#S21` (depth limit) /
      `#S24 §10.6 row 1` / `#S24 §12 Q5` need closing? Yes — flip to
      resolved-in-slice-25 with citations to the new code locations.
