# Session 5b — LLM Gateway Internal-Lane Routing (needs a review checkpoint)

> **Why this is staged, not done overnight:** this is the one change that can break *every* LLM call. It modifies the 625-line `ModelRouter.complete()` hot path (`router.py:559-1184`), which is tangled through `resilient_call`, per-provider circuit breakers, fallback-provider dispatch, and truncation re-dispatch. It should be implemented and deployed with a human watching, not on an unattended overnight run. The owner decided "wire it through" — this is that work, scoped to be bounded and safe.

**Goal:** Realize choke point (B) from spec §26 — route Donna's *local Ollama* inference through the gateway queue so GPU work is arbitrated and preemptible, while keeping cost/accounting in `ModelRouter` (choke point A).

**Bounded blast radius:** wire the queue into the **chat `ModelRouter` in the API process only** (it co-locates the `LLMQueueWorker` and is low-volume/interactive). Leave the **orchestrator** routers unchanged (direct provider calls, as today) — the orchestrator is a separate process from the worker, so sharing the arbiter is cross-process and stays deferred. This means a bug in the routing can only affect chat, never the critical parse/reminder/digest path.

## Tasks

### Task 1 — Repair queue preemption (`llm/queue.py`)
`_current_aio_task` (`queue.py:62`) is never assigned, so `preempt_external` (`:419-424`) is a permanent no-op.
- In `process_one` (`:212`), wrap the execution: `exec_task = asyncio.create_task(self._execute(item)); self._current_aio_task = exec_task; result, meta = await exec_task`.
- In the `except asyncio.CancelledError` branch (`:247`), if the worker itself is being cancelled (shutdown, not preemption), cancel `exec_task` too so it isn't orphaned — distinguish shutdown-cancel from preempt-cancel (preempt sets `item.interrupted`).
- Test: enqueue an internal item while an external one is "running" (a slow fake `_ollama.complete`); assert the external item is cancelled + re-enqueued and the internal runs first. Add a shutdown test asserting no orphaned `_execute` task survives worker cancellation.

### Task 2 — Give `ModelRouter` an optional GPU gateway (`models/router.py`)
- Add `gpu_gateway: LLMQueueWorker | None = None` to `ModelRouter.__init__` / `build_model_router`. Default None → today's behavior exactly.
- Inside `complete()`, at the Ollama dispatch point (inside the `resilient_call`/breaker wrapper — find where `provider.complete(...)` runs for `provider.name == "ollama"`), when `self._gpu_gateway is not None` AND the resolved provider is Ollama, replace the direct `await provider.complete(...)` with:
  `future = await self._gpu_gateway.enqueue_internal(prompt=..., model=resolved_model, max_tokens=..., json_mode=..., task_type=task_type, priority=<map from config/llm_gateway.yaml priority_map>, task_id=..., user_id=...)` then `result, meta = await future`.
  Keep everything else (budget pre-check, breaker, logging, parsing, truncation) unchanged — only the raw inference dispatch moves into the queue. The queue's `_execute` calls its own `self._ollama.complete()` with the same args, returning the same `(result, meta)` shape.
- **Preserve breaker semantics:** the Ollama breaker must still open on queue-path failures — ensure the `resilient_call` wrapper still sees the exception (the future should propagate `_execute`'s exception).
- Cloud path unchanged (never routed through the queue).
- Tests: with a stub gateway, assert an Ollama `complete()` call goes through `enqueue_internal` and returns the future's result; assert a cloud call does NOT; assert a queue-path failure still opens the Ollama breaker and triggers fallback; assert `gpu_gateway=None` is byte-for-byte today's path.

### Task 3 — Wire the chat router only (`api/__init__.py`)
- The worker already lives at `app.state.llm_queue` (`api/__init__.py:207`). Pass it as `gpu_gateway=` to the chat `build_model_router(...)` (`:245`). Do NOT wire it into any orchestrator router.
- Test: a chat completion in the API process routes through the queue (integration test with the real worker + a fake Ollama).

### Task 4 — Minimal shadow consumer (optional, low priority)
- Pass an `on_shadow_complete` at the production builders that persists primary-vs-shadow output divergence as a `shadow_comparison` structured log event (shadow spend already lands in `invocation_log`; only the output comparison is missing). Leave the pluggable callback for the fuller harness. Shadow is gated by `shadow.enabled` (currently off), so this is inert until enabled.

## Deploy & verify (with review)
Rebuild `donna-api`, deploy, then live-test: (a) a chat message still completes; (b) an external `POST /llm/completions` still works; (c) send a chat + an external call concurrently and confirm from Loki (`llm_gateway.*` events) that the internal (chat) lane was served first / preempted the external one. Watch `docker logs donna-api` for `llm_gateway.failed`. Revert Task 3's one-line wiring if anything regresses — the mechanism (Tasks 1–2) can stay since it's inert without a gateway handle.

## Not in scope (explicitly deferred)
Orchestrator cross-process arbitration (its local calls reaching the worker over an internal HTTP route, or co-locating the worker). This is the piece that makes §26(B) fully true for the primary GPU user; it needs its own design (auth on the internal route, no double cost-logging). Track in `followups.md`.
