# Wiring Remediation — Design Document (Session 3)

**Created:** 2026-07-02. **Status:** design only — no code. Gates Sessions 4–6 of the master plan.
**Method:** three parallel intent-reconstruction investigations (LLM gateway/shadow; proactive loops/heartbeats/supervision; state machine/calendar/config), each asked to recover *original design intent* from `spec_v3.md` + design docs before recommending any change.

**Owner's north star for this work:** *"The design is better than what is actually working. Before cutting any code out, consider the potential of the design. Try to understand the intent. A lot of the wiring issues is probably because it was working off of large design documentation and only wrote the features instead of stringing them along. Remove obsolete pathways only if the intent is carried through, or the design direction was wrong."*

**Headline conclusion:** The investigations confirm the hypothesis almost universally. Of the nine items below, **seven are pure wiring** (the implementation is complete and correct in isolation; a handful of assignment statements and one log call are missing) and **two require a genuine design decision** (LLM gateway internal lane; shadow-mode consumer). **Nothing should be deleted** except one dead-end placeholder block whose intent is carried by the real fix. This is a stringing-together job, exactly as suspected.

---

## Disposition table

| # | Item | Root cause | Disposition | Session |
|---|---|---|---|---|
| 1 | Four proactive prompts unwired | complete-but-unwired | **WIRE** (construct + set 4 `NotificationTasks` fields; delete 1 placeholder block) | 4 |
| 2 | Health heartbeat writers absent | complete-but-unwired | **WIRE** (callback into `ReminderScheduler` + `ModelRouter`) | 4 |
| 3 | ~15 bg loops unsupervised | missing supervision seam | **WIRE** (done-callback → existing `cli.py` supervision chain) | 4 |
| 4 | Reminder `_parse_dt` silent skip | missing log/alert | **WIRE** (split empty vs bad-format; alert on bad-format) | 4 |
| 5 | LLM gateway internal lane inert + preemption bug | unfinished build (not misdesign) | **DECIDE → wire (recommended) or simplify**; fix §26 wording either way | 5 |
| 6 | Shadow mode `on_shadow_complete` unwired | deliberately staged | **KEEP + wire a minimal consumer**; do not cut | 5 |
| 7 | State-machine bypass (`update_task(status=)`) | code routed around incomplete design | **EXTEND design** (`*→done` wildcard) then force all writers through the machine | 6 |
| 8 | CalendarSync fail-open mass-unschedule | real bug (fail-closed intent) | **FIX** (abort change-detection on read error + alert) | 6 |
| 9a | `log_capability_gap` handler missing | config written for a never-created glue fn | **WIRE** (add module-level wrapper) | 6 |
| 9b | GPU model-tag mismatch (q6_K vs q4_K_M) | config drift | **DECIDE which tag** (see §9b — needs owner call), then make them match | 6 |

---

## 1–4. The "just wiring" cluster (Session 4) — low risk, high value

All four are additive. The classes/functions exist and are correct; the seams that activate them were never connected.

**1. Four proactive prompts** (`notifications/proactive_prompts.py` — `EveningCheckin`, `AfternoonInactivityCheck`, `StaleTaskDetector`, `PostMeetingCapture`). Intended (per `docs/domain/notifications.md:203-210`, `spec §11/§18.1`) to run as permanent background tasks like `ReminderScheduler`, configured from `config/discord.yaml`. The `NotificationTasks` dataclass already has the four fields (`server.py:81-84`) and `run_server` already has the start-guards (`server.py:325-351`). **The only gap:** `_build_notification_tasks` (`cli_wiring.py:886`) never constructs them or sets the four fields, so the guards are always false. **Fix:** construct the four from `discord.yaml` inside `_build_notification_tasks` and pass them to `NotificationTasks(...)`. **Delete** the dead-end import-and-log block in `wire_discord` (`cli_wiring.py:2776-2795`) — its only purpose was to be a placeholder; its intent is carried by the real construction. *This is the single highest value-per-line fix in the repo — it turns the idle assistant into one that reaches out daily, which is the product's whole thesis.*

**2. Health heartbeat writers.** `_check_scheduler` reads `scheduler_last_heartbeat`, `_check_api_freshness` reads `last_api_ts`; nothing writes either (so 2 of 4 health checks always report "healthy, no heartbeat wired"). **Fix:** thread a callback — add `on_heartbeat` to `ReminderScheduler.__init__` (call each 60s iteration) and `on_api_call` to `ModelRouter.__init__` (call after a successful provider response, ~`router.py:1088`); both closures write `datetime.now(UTC)` into the app-state cell. Because the router is built in `build_startup_context` before `run_server` creates the app, pass a shared mutable cell (a small dataclass/1-element list) that both sides reference. **Design note:** prefer a tiny `HealthHeartbeats` dataclass over a bare list for type-clarity.

**3. Background-task supervision.** `run_server` creates ~15 loops as bare `asyncio.create_task` in `bg_tasks` with no done-callback; a crashed loop dies silently (the per-loop `except` guards don't cover a loop that escapes its own try). The supervision pattern already exists for top-level tasks in `cli.py:380-393` (`asyncio.wait(FIRST_COMPLETED)` → `orchestrator_task_failed`). **Fix:** add a `add_done_callback` to each `bg_tasks` entry that, on unexpected exit, logs `event_type="fallback_activated"` and calls `stop_event.set()` — which unblocks `run_server`, completing its task and threading into the existing `cli.py` supervision chain. Also dispatch `dispatch_fallback_alert` when the notification service is reachable.

**4. Reminder `_parse_dt` silent skip** (`reminders.py:104-105`). A permanently-unparseable `scheduled_start` is skipped every tick forever with no signal. **Fix:** split the empty case (legitimate skip) from the non-empty-but-unparseable case (data corruption) and, for the latter, log `reminder_scheduled_start_unparseable` + `dispatch_fallback_alert` (mirroring the existing LLM-failure alert at `reminders.py:188-193`).

> **Risk assessment for the cluster:** low. All four are additive wiring inside two files (`cli_wiring.py`, `server.py`) plus small constructor params on `ReminderScheduler`/`ModelRouter`. Each is independently testable. Sequence them in one session because 1–3 all touch `server.py`/`cli_wiring.py`.

---

## 5. LLM gateway internal lane + preemption (Session 5) — the first real decision

**Intent recovered** (`docs/superpowers/specs/archive/2026-04-11-llm-gateway-queue-design.md`): the RTX 3090 runs one model at a time and Ollama has no priority notion. The gateway was designed to serialize the single GPU so external homelab callers (e.g. immich-tagger) can't starve Donna's interactive work, with **preemption** during active hours. Crucially, the design doc says only **local Ollama** calls were ever meant to flow through the queue — **cloud/Claude always bypasses** (it doesn't use the GPU). So spec §26's "*all* outbound calls flow through the LLM Gateway as a single cost-aware choke point" was an over-statement from day one: it conflates two different choke points.

**Reality:**
- **Cost/accounting choke point = `ModelRouter.complete()`** — already real and complete (budget pre-check `router.py:698`, escalation gate, `_log_invocation`). This is what makes §13 budget enforceable, *not* the gateway.
- **GPU-arbitration choke point = the queue worker** — external lane fully wired and working (in the API process); internal lane (`enqueue_internal`) has zero callers; `ModelRouter` has no queue handle; **preemption is a ~5-line bug** (`_current_aio_task` is checked/cancelled but never assigned because `process_one` awaits `_execute` inline instead of wrapping it in a cancellable task).

**Recommended disposition — WIRE IT (option a done right), do not delete.** The GPU-contention problem is real on one 3090 and its intent is carried nowhere else, so deleting the internal lane would discard exactly the capability you want strung through.
1. **Fix spec §26** to describe *two* choke points and state that only local Ollama calls are GPU-arbitrated (cloud bypasses). Same-PR spec sync per CLAUDE.md.
2. **Repair preemption** (do regardless, low risk): wrap `self._execute(item)` in `asyncio.create_task`, assign to `self._current_aio_task`, await it.
3. **Wire the internal lane through `ModelRouter`** — accounting stays in the router; when the resolved provider is Ollama, dispatch the *inference step* via `enqueue_internal(priority=…)`. **In-process first** (cheap): share the existing `app.state.llm_queue` worker with the chat router at `api/__init__.py:245`. **Cross-process (orchestrator) deferred explicitly**: the orchestrator is a separate process and the primary GPU user; sharing the arbiter needs either an internal-priority HTTP route or co-location — park in `followups.md` given light solo load, but it is the piece that makes §26 fully true.

**Alternative (only if you consciously choose it):** make `ModelRouter` the sole acknowledged choke point, delete the internal `PriorityQueue`/`enqueue_internal`/preemption/active-hours logic, and rewrite §26 to call the gateway an external-only rate-limited proxy. **Not recommended** — GPU contention between the two processes is genuinely possible. Choose this only if the orchestrator will *never* share the Ollama arbiter.

---

## 6. Shadow mode (Session 5) — keep, do not cut

**Intent:** shadow-eval runs the primary route plus a shadow route and compares. **Reality:** shadow *does* fire in production — sampling + per-route shadow alias + `is_shadow=1` invocation logging + budget/breaker coverage are all live (confirmed by `followups.md` ML-FABLE-P2). The **only** inert piece is the `on_shadow_complete` callback: it's a constructor param invoked at `router.py:1236` but no production builder passes one. It is the designed seam for a primary-vs-shadow **output comparison** harness, which `followups.md:103` schedules as a future slice.

**Disposition: KEEP + wire a minimal default consumer.** Shadow *spend* already lands in `invocation_log`; the missing capture is the *output divergence*, which the callback carries (`result, metadata`). Pass an `on_shadow_complete` at the production builders that persists the primary-vs-shadow divergence (a small `shadow_comparison` structured log event or table). Leave the pluggable callback for the fuller fixture-driven harness later. **Do not delete** — it is staged, not dead.

---

## 7. State-machine bypass (Session 6) — extend the design, don't route around it

**Intent** (`spec §5.2`): transitions defined in `task_states.yaml`, invalid ones rejected; `transition_task_state()` is the sanctioned path (lock + validate + emit + side-effects like `set_completed_at`, `update_velocity_metrics`). The config already bans `backlog→done` ("must go through scheduled→in_progress→done") **and** already has a `*→cancelled` wildcard for "user intent overrides lifecycle position."

**Root cause:** code routed *around* an incomplete design. The `backlog→done` ban predates the Discord UX; real users tap "Done" on tasks that were never scheduled (trivial, or handled offline). The smoking gun is `chat_escalation_ingestion_poller.py:136-144`, whose comment shows the developer understood the rule, hit a legitimate case it blocked, and hard-set status instead of extending the config. The missing piece is a `*→done` wildcard — the pattern `*→cancelled` already established.

**Disposition: EXTEND then enforce.** Add `*→done` (trigger `user_marks_complete`, with `set_completed_at` + `update_velocity_metrics` + `delete_calendar_event_if_exists` side effects) to `config/task_states.yaml`. Then: remove `"status"` from `_UPDATABLE_COLUMNS` (`database.py:88`); route the three Discord callers (`discord_commands.py:153`, `discord_views.py:177,427`), the API `PATCH /tasks/{id}`, and the chat-escalation poller through `transition_task_state()`; the API PATCH must reject unknown status strings with 422. Result: every status write goes through lock+validate+emit, and the side-effects that currently don't fire (completed_at, velocity) start firing. This *carries the intent through* rather than papering over it.

---

## 8. CalendarSync fail-open (Session 6) — a straight bug

**Intent** is unambiguous and documented on the Scheduler: `CalendarReadError` exists precisely so placement **fails closed** rather than "booking blind against an empty event list — which would silently double-book" (`scheduler.py:59-73`). The change-detector should obey the same rule: if you can't read the calendar, you cannot distinguish "user deleted the event" from "temporarily unreadable."

**Reality:** `calendar_sync.py:82-91` swallows a `list_events` failure, leaving `live_events` incomplete; the change-detector (`:97-150`) then treats every Donna-managed event missing from `live_events` as user-deleted and mass-unschedules those tasks (→ BACKLOG, clears `calendar_event_id`, `scheduled_start`), silently, with no fallback alert.

**Disposition: FIX (fail-closed).** Track fetch errors per calendar; if any calendar read failed, log `event="fallback_activated"`, dispatch a fallback alert, and **`return` before the change-detection loop** so the cycle is skipped and retried next poll. Add a `fallback_alert_fn` param to `CalendarSync.__init__` (matching the pattern used across the codebase) and wire it in `cli_wiring.py`.

---

## 9a. `log_capability_gap` (Session 6) — add the missing glue

`config/reply_actions.yaml:55` points at `donna.replies.actions.gap_actions.log_capability_gap`, but the module only defines `class CapabilityGapTracker` (with `async def log_gap`). The reply handler resolves the path via `getattr` (`handler.py:210`) → `AttributeError`, caught as a generic failure. **Disposition: WIRE** — add a module-level `async def log_capability_gap(db, context, params) -> str` wrapper that instantiates `CapabilityGapTracker(db.connection)` and delegates to `log_gap(...)`. No config change. (The class is complete; only the glue was missing — config written ahead of the function.)

## 9b. GPU model-tag mismatch (Session 6) — **needs an owner decision, do not blind-fix**

`config/llm_gateway.yaml:45` `home_model: qwen2.5:32b-instruct-q6_K` vs `config/donna_models.yaml:22` `local_parser.model: qwen2.5:32b-instruct-q4_K_M`. Because the queue compares tags by string equality, every `local_parser` call (14+ task types) looks like non-home work and would trigger a GPU model swap out-and-back on each call.

**Why this one is not a blind fix:** the design spec names `q6_K` (`docs/superpowers/specs/2026-05-13-gpu-aware-extraction-pipeline-design.md`), and `api/routes/llm.py:54-55` hardcodes `q6_K` — so the *design intent* is q6_K. **But the live system actually runs `q4_K_M`** (runtime audit: all local inference over the last 7 days used `qwen2.5:32b-instruct-q4_K_M`). So someone deliberately moved `local_parser` to the lighter quantization — plausibly for VRAM headroom (q6_K of a 32B model is ~27 GB; q4_K_M ~20 GB; the 3090 has 24 GB, so **q6_K may not fit alongside the MiniLM embedder / KV cache**).

**Disposition: DECIDE, then make the two configs agree.**
- If you want the design-intent quality (`q6_K`): confirm it fits in 24 GB with your embedder resident, `ollama pull` it, set `local_parser.model` → `q6_K`. 
- If VRAM is the real constraint (likely, given the live choice): keep `q4_K_M` as `local_parser` and change `home_model` → `q4_K_M`, and update the design spec + `api/routes/llm.py` fallback to match.
- Either way the fix is one line in one file — but which file depends on your VRAM reality. **Recommend:** confirm actual VRAM usage (`nvidia-smi` while a q6_K parse runs) before choosing. This is the one item in the whole set where the "design" might have been correctly overridden by deployment reality — exactly the case where I should not cut toward the doc.

---

## What this design pass changes about Sessions 4–6

- Session 4 (items 1–4) is safe to write as a bite-sized TDD plan immediately — no open decisions.
- Session 5 (items 5–6) needs your nod on **wire-through vs simplify** for the gateway (recommended: wire, in-process now, cross-process deferred) before I write its task plan.
- Session 6 (items 7–9) is ready except **9b needs your VRAM/quantization call**.
