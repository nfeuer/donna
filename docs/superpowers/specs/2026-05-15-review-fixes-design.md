# Code Review Fixes — 2026-05-15

Fixes for 6 issues identified in the code review of branch `fix/discord-done-intent-and-thread-routing` (commits `5f85d64..eaac432`).

## 1. Migration: BigInteger for Discord snowflake IDs

**Problem:** `alembic/versions/b1c2d3e4f5a6_add_overdue_thread_map.py` declares `discord_thread_id` as `sa.Integer()` (32-bit). Discord snowflake IDs are 64-bit unsigned. SQLite handles this via dynamic typing, but the schema is wrong for Postgres portability (Supabase sync) and correctness.

**Fix:** New Alembic migration that runs `ALTER TABLE overdue_thread_map RENAME TO _old; CREATE TABLE overdue_thread_map (... sa.BigInteger() ...); INSERT INTO ... SELECT FROM _old; DROP TABLE _old`. SQLite doesn't support `ALTER COLUMN`, so the table must be recreated. Existing data (5+ rows of real snowflake IDs) is preserved.

## 2. Discord: guard untracked thread replies

**Problem:** `discord_bot.py:442-444` — any message in an untracked thread under `#donna-tasks` unconditionally calls `_handle_done_intent`, which marks the user's most recent active task as done. Typing "hello" or "what's the status?" in an old overdue thread would accidentally complete a task.

**Fix:** Gate the call with `_detect_done_intent(raw_text)`. When the message doesn't match a done intent, reply with a brief message: "I see your reply but I'm not sure what you'd like me to do. Try 'done' to mark a task complete." This preserves the thread-reply UX while preventing accidental completions.

## 3. Skill YAML: `claude_with_triage` fallback dead end

**Problem:** `skills/product_watch/skill.yaml` — `claude_with_triage` (line 58) has no `on_failure: continue`. If triage succeeds but Claude fails (e.g. invalid JSON), the skill run dies. `claude_fallback`'s condition checks `triage_for_claude.success`, which is True, so it's skipped — dead end with no recovery.

**Fix:**
- Add `on_failure: continue` to `claude_with_triage`
- Change `claude_fallback` condition to: `not (state.try_local_extract.success or state.try_vision_extract.success or state.claude_with_triage.success)` — triggers when triage succeeded but Claude extraction failed, falling through to the direct-URL fallback

## 4. Ollama: loud failure for unsupported params

**Problem:** `OllamaProvider.complete()` accepts `tools` and `messages` for Protocol conformity but silently ignores them. If tool_use is ever routed to Ollama (config mistake), tools and conversation history are silently dropped.

**Fix:** Add guards at the top of `complete()`:
- `if tools: raise NotImplementedError("Ollama does not support tool_use")`
- `if messages: raise NotImplementedError("Ollama does not support multi-turn messages")`

This follows the project's "safety first" principle (CLAUDE.md §Key Design Principles).

## 5. Dispatcher: atomic success reset via advance_schedule

**Problem:** `dispatcher.py:286-294` — on success, `advance_schedule()` and `update_fields()` are two separate transactions (two commits). If the process crashes between them, `status` remains "paused" from a prior failure cycle.

**Fix:** Add optional `status_override: str | None = None` and `failure_count_override: int | None = None` params to `AutomationRepository.advance_schedule()`. When provided, the single UPDATE statement includes `status = ?` and `failure_count = ?` clauses. The dispatcher's success path passes `status_override="active", failure_count_override=0` and removes the separate `update_fields` call. All other callers are unaffected — they don't pass the new params.

## 6. Tests: executor tool_use loop

**Problem:** `_complete_with_tool_loop` in `executor.py:650-720` is the most complex new code path and has zero test coverage.

**Fix:** New test file `tests/unit/skills/test_executor_tool_loop.py` with cases:
1. **No tools passthrough** — verifies direct router call when `tool_definitions` is None
2. **Single-round tool_use** — mock router returns `_tool_use` on first call, text on second; verify tool dispatch and message threading
3. **Multi-round tool_use** — two rounds of tool calls before text response
4. **Max rounds exceeded** — mock router always returns `_tool_use`; verify `RuntimeError` raised
5. **Tool dispatch error** — mock `_tool_registry.dispatch` raises; verify `is_error: True` tool_result sent back

Tests use mocked router and tool_registry — no real LLM calls.

## Files Changed

| File | Change |
|------|--------|
| `alembic/versions/<new>_bigint_overdue_thread.py` | New migration: recreate table with BigInteger |
| `src/donna/integrations/discord_bot.py` | Guard untracked thread replies with done intent check |
| `skills/product_watch/skill.yaml` | Add `on_failure: continue` to `claude_with_triage`, fix `claude_fallback` condition |
| `src/donna/models/providers/ollama.py` | Raise NotImplementedError for tools/messages |
| `src/donna/automations/repository.py` | Add status_override/failure_count_override to advance_schedule |
| `src/donna/automations/dispatcher.py` | Use merged advance_schedule call on success path |
| `tests/unit/skills/test_executor_tool_loop.py` | New: 5 test cases for tool_use loop |
