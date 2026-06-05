# Fallback Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every fallback/degraded code path in Donna visible via `#donna-debug` Discord notifications with rate limiting, and prevent future silent fallbacks.

**Architecture:** Add `dispatch_fallback_alert()` to `NotificationService` with in-memory dedup. Retrofit all 16 audited silent-fallback sites. Fix the morning digest prompt so the local model returns the correct JSON schema. Add a CLAUDE.md convention and CI lint to prevent regression.

**Tech Stack:** Python 3.12, structlog, discord.py, pytest

---

### Task 1: Add `dispatch_fallback_alert()` to NotificationService

**Files:**
- Modify: `src/donna/notifications/service.py:52-78` (class init + new method)
- Test: `tests/unit/test_notification_service.py`

- [ ] **Step 1: Write failing tests for dispatch_fallback_alert**

Add to `tests/unit/test_notification_service.py`:

```python
import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

# (existing imports and helpers already in the file)


class TestFallbackAlert:
    async def test_dispatches_to_debug_channel(self) -> None:
        service, bot = _make_service()
        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = _utc(10)
            sent = await service.dispatch_fallback_alert(
                component="morning_digest",
                error="LLM returned wrong keys",
                fallback="degraded plain-text digest",
            )
        assert sent is True
        bot.send_message.assert_called_once()
        call_args = bot.send_message.call_args
        assert call_args[0][0] == "debug"
        msg = call_args[0][1]
        assert "morning_digest" in msg
        assert "LLM returned wrong keys" in msg
        assert "degraded plain-text digest" in msg

    async def test_includes_context_in_message(self) -> None:
        service, bot = _make_service()
        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = _utc(10)
            await service.dispatch_fallback_alert(
                component="reminder",
                error="LLM timeout",
                fallback="template string",
                context={"task_id": "t1", "model": "qwen2.5"},
            )
        msg = bot.send_message.call_args[0][1]
        assert "task_id: t1" in msg
        assert "model: qwen2.5" in msg

    async def test_dedup_within_cooldown(self) -> None:
        service, bot = _make_service()
        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = _utc(10)
            first = await service.dispatch_fallback_alert(
                component="digest",
                error="same error",
                fallback="fallback",
                cooldown_seconds=3600,
            )
            second = await service.dispatch_fallback_alert(
                component="digest",
                error="same error",
                fallback="fallback",
                cooldown_seconds=3600,
            )
        assert first is True
        assert second is False
        assert bot.send_message.call_count == 1

    async def test_different_component_not_deduped(self) -> None:
        service, bot = _make_service()
        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = _utc(10)
            await service.dispatch_fallback_alert(
                component="digest", error="err", fallback="fb"
            )
            await service.dispatch_fallback_alert(
                component="reminder", error="err", fallback="fb"
            )
        assert bot.send_message.call_count == 2

    async def test_cooldown_expires(self) -> None:
        service, bot = _make_service()
        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = _utc(10)
            await service.dispatch_fallback_alert(
                component="digest",
                error="err",
                fallback="fb",
                cooldown_seconds=60,
            )
            # Advance 2 minutes past cooldown
            mock_dt.now.return_value = _utc(10, 3)
            sent = await service.dispatch_fallback_alert(
                component="digest",
                error="err",
                fallback="fb",
                cooldown_seconds=60,
            )
        assert sent is True
        assert bot.send_message.call_count == 2

    async def test_recursion_guard_on_send_failure(self) -> None:
        service, bot = _make_service()
        bot.send_message = AsyncMock(side_effect=RuntimeError("Discord down"))
        with patch("donna.notifications.service.datetime") as mock_dt:
            mock_dt.now.return_value = _utc(10)
            sent = await service.dispatch_fallback_alert(
                component="digest", error="err", fallback="fb"
            )
        assert sent is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_notification_service.py::TestFallbackAlert -v`
Expected: FAIL — `dispatch_fallback_alert` does not exist yet.

- [ ] **Step 3: Implement dispatch_fallback_alert**

In `src/donna/notifications/service.py`, add to `__init__` (after line 77):

```python
        self._fallback_alert_history: dict[tuple[str, str], datetime] = {}
        self._alerting = False
```

Then add the method after `flush_queue()` (after line 263):

```python
    async def dispatch_fallback_alert(
        self,
        component: str,
        error: str,
        fallback: str,
        context: dict[str, Any] | None = None,
        cooldown_seconds: int = 3600,
    ) -> bool:
        """Dispatch a fallback-activated alert to #donna-debug with rate limiting.

        Call this from any code path where an exception or unexpected result
        causes the system to use a fallback behavior instead of the primary
        path. Every fallback must be observable — see CLAUDE.md Conventions.

        Args:
            component: Subsystem identifier (e.g. "morning_digest").
            error: What went wrong — exception message or unexpected state.
            fallback: What the system did instead of the primary path.
            context: Optional structured data (task_id, model, keys, etc.).
            cooldown_seconds: Dedup window — same (component, error_prefix)
                within this window logs but skips Discord. Default 1 hour.

        Returns:
            True if dispatched to Discord, False if deduped or failed.
        """
        now = datetime.now(tz=UTC)
        dedup_key = (component, error[:50])

        logger.warning(
            "fallback_activated",
            event_type="fallback_activated",
            component=component,
            error=error,
            fallback=fallback,
            context=context or {},
        )

        last_sent = self._fallback_alert_history.get(dedup_key)
        if last_sent is not None:
            elapsed = (now - last_sent).total_seconds()
            if elapsed < cooldown_seconds:
                logger.info(
                    "fallback_alert_deduped",
                    component=component,
                    seconds_since_last=int(elapsed),
                    cooldown_seconds=cooldown_seconds,
                )
                return False

        if self._alerting:
            logger.error(
                "fallback_alert_recursion_blocked",
                component=component,
            )
            return False

        self._alerting = True
        try:
            lines = [
                f"⚠️ Fallback activated: {component}",
                f"Error: {error}",
                f"Fallback: {fallback}",
            ]
            if context:
                for k, v in context.items():
                    lines.append(f"{k}: {v}")
            message = "\n".join(lines)

            await self._bot.send_message(CHANNEL_DEBUG, message)
            self._fallback_alert_history[dedup_key] = now
            return True
        except Exception:
            logger.error(
                "fallback_alert_dispatch_failed",
                component=component,
                error=error,
            )
            return False
        finally:
            self._alerting = False
```

Also add `Any` to the typing imports at the top of the file if not already present. The file already imports `from typing import TYPE_CHECKING, Any` so this is covered.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_notification_service.py::TestFallbackAlert -v`
Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/donna/notifications/service.py tests/unit/test_notification_service.py
git commit -m "feat: add dispatch_fallback_alert() to NotificationService with rate limiting

Centralized method for making all fallback/degraded code paths
observable via #donna-debug. Includes in-memory dedup by
(component, error_prefix) with configurable cooldown window
and recursion guard to prevent alert-about-alert loops.

Spec: docs/superpowers/specs/2026-05-19-fallback-observability-design.md §1"
```

---

### Task 2: Fix morning digest prompt and add fallback key extraction

**Files:**
- Modify: `prompts/morning_digest.md:52-54`
- Modify: `src/donna/notifications/digest.py:125-161`
- Test: `tests/unit/test_digest.py`

- [ ] **Step 1: Write failing tests for digest fallback key extraction**

Add to `tests/unit/test_digest.py`. First check the existing test helpers — the file likely has a `_make_digest()` or similar factory. Add tests:

```python
class TestDigestFallbackAlert:
    """Verify that the digest alerts on schema mismatch and degraded mode."""

    async def test_schema_mismatch_salvages_description_key(self) -> None:
        """When LLM returns {description: ...} instead of {digest_text: ...},
        salvage the text and dispatch a fallback alert."""
        router = AsyncMock()
        router.complete = AsyncMock(return_value=(
            {"description": "Hello from Donna", "title": "Digest", "color": 123},
            MagicMock(),
        ))
        service = AsyncMock()
        service.dispatch = AsyncMock(return_value=True)
        service.dispatch_fallback_alert = AsyncMock(return_value=True)

        digest = MorningDigest(
            db=AsyncMock(),
            service=service,
            router=router,
            calendar_client=None,
            calendar_id="",
            user_id="u1",
            project_root=Path(__file__).resolve().parents[2],
        )
        digest._assemble_data = AsyncMock(return_value={
            "current_date": "2026-05-19",
            "day_of_week": "Tuesday",
            "calendar_events": "No events today.",
            "tasks_due_today": "None.",
            "carryover_tasks": "None.",
            "overdue_tasks": "None.",
            "prep_work_results": "No prep work completed.",
            "agent_activity": "No agent activity since last digest.",
            "system_status": "All systems normal.",
            "yesterday_cost": "0.0059",
            "mtd_cost": "0.9292",
            "monthly_budget": "100.00",
            "tool_gaps": "None.",
        })

        await digest._fire(datetime(2026, 5, 19, 6, 30, tzinfo=UTC))

        # Should have used the description value as digest text
        service.dispatch.assert_called_once()
        call_kwargs = service.dispatch.call_args[1]
        assert call_kwargs["content"] == "Hello from Donna"
        assert call_kwargs["embed"] is not None

        # Should have alerted about the schema mismatch
        service.dispatch_fallback_alert.assert_called_once()
        alert_kwargs = service.dispatch_fallback_alert.call_args[1]
        assert alert_kwargs["component"] == "morning_digest"
        assert "wrong keys" in alert_kwargs["error"].lower() or "schema" in alert_kwargs["error"].lower()

    async def test_degraded_mode_dispatches_fallback_alert(self) -> None:
        """When LLM returns None, degraded mode should alert."""
        router = AsyncMock()
        router.complete = AsyncMock(side_effect=RuntimeError("LLM down"))
        service = AsyncMock()
        service.dispatch = AsyncMock(return_value=True)
        service.dispatch_fallback_alert = AsyncMock(return_value=True)

        digest = MorningDigest(
            db=AsyncMock(),
            service=service,
            router=router,
            calendar_client=None,
            calendar_id="",
            user_id="u1",
            project_root=Path(__file__).resolve().parents[2],
        )
        digest._assemble_data = AsyncMock(return_value={
            "current_date": "2026-05-19",
            "day_of_week": "Tuesday",
            "calendar_events": "No events today.",
            "tasks_due_today": "None.",
            "carryover_tasks": "None.",
            "overdue_tasks": "None.",
            "prep_work_results": "No prep work completed.",
            "agent_activity": "No agent activity since last digest.",
            "system_status": "All systems normal.",
            "yesterday_cost": "0.0059",
            "mtd_cost": "0.9292",
            "monthly_budget": "100.00",
            "tool_gaps": "None.",
        })

        await digest._fire(datetime(2026, 5, 19, 6, 30, tzinfo=UTC))

        # Should have dispatched degraded text (no embed)
        service.dispatch.assert_called_once()
        call_kwargs = service.dispatch.call_args[1]
        assert call_kwargs.get("embed") is None

        # Should have alerted about degraded mode
        service.dispatch_fallback_alert.assert_called()
        alert_kwargs = service.dispatch_fallback_alert.call_args[1]
        assert alert_kwargs["component"] == "morning_digest"
        assert "degraded" in alert_kwargs["fallback"].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_digest.py::TestDigestFallbackAlert -v`
Expected: FAIL — `dispatch_fallback_alert` is never called by the current code.

- [ ] **Step 3: Update the morning digest prompt template**

In `prompts/morning_digest.md`, replace the existing Output section (lines 52-54):

```markdown
## Output Format

Return a JSON object with exactly these fields:

    {"digest_text": "<the full digest message, under 2000 chars>", "task_count": <integer>, "overdue_count": <integer>}

Do not use any other keys. The digest_text field contains the complete message suitable for Discord embed or email.
```

- [ ] **Step 4: Add fallback key extraction and alerts to digest.py _fire()**

Replace lines 125-161 in `src/donna/notifications/digest.py`:

```python
        # Attempt LLM-generated digest.
        digest_text: str | None = None
        llm_error: str | None = None
        try:
            rendered_prompt = _render_template(template_text, data)
            result, _ = await self._router.complete(rendered_prompt, task_type="generate_digest")
            digest_text = result.get("digest_text") if isinstance(result, dict) else None

            if digest_text is None and isinstance(result, dict):
                digest_text = result.get("description")
                if digest_text:
                    llm_error = f"LLM returned wrong keys: {sorted(result.keys())}"
                    logger.warning(
                        "morning_digest_schema_mismatch",
                        keys=sorted(result.keys()),
                    )
                    await self._service.dispatch_fallback_alert(
                        component="morning_digest",
                        error=llm_error,
                        fallback="used 'description' field as digest_text",
                        context={"expected_key": "digest_text", "actual_keys": str(sorted(result.keys()))},
                    )
                else:
                    llm_error = f"LLM returned dict without usable text key: {sorted(result.keys())}"
            elif not isinstance(result, dict):
                llm_error = f"LLM returned non-dict: {type(result).__name__}"
        except ContextOverflowError:
            raise
        except Exception as exc:
            llm_error = f"{type(exc).__name__}: {exc}"
            logger.exception("morning_digest_llm_failed")

        if digest_text:
            embed = discord.Embed(
                title=f"Good morning — {data['day_of_week']}, {data['current_date']}",
                description=digest_text,
                colour=EMBED_COLOUR,
            )
            await self._service.dispatch(
                notification_type=NOTIF_DIGEST,
                content=digest_text,
                channel=CHANNEL_DIGEST,
                priority=5,
                embed=embed,
            )
            logger.info("morning_digest_sent_llm")
            email_body = digest_text
        else:
            await self._service.dispatch_fallback_alert(
                component="morning_digest",
                error=llm_error or "digest_text was None after LLM call",
                fallback="degraded plain-text digest",
            )
            fallback_text = self._render_degraded(data)
            await self._service.dispatch(
                notification_type=NOTIF_DIGEST,
                content=fallback_text,
                channel=CHANNEL_DIGEST,
                priority=5,
            )
            logger.info("morning_digest_sent_degraded")
            email_body = fallback_text
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_digest.py -v`
Expected: All tests PASS, including both new and existing tests.

- [ ] **Step 6: Commit**

```bash
git add prompts/morning_digest.md src/donna/notifications/digest.py tests/unit/test_digest.py
git commit -m "fix: morning digest schema mismatch detection and fallback alerting

Adds JSON output format to prompt so local Qwen model produces correct
keys. Salvages 'description' key when model returns wrong schema.
Dispatches fallback alert to #donna-debug on schema mismatch and on
entry to degraded mode.

Fixes: silent fallback discovered 2026-05-19 where Qwen returned
{title, description, color} instead of {digest_text, task_count,
overdue_count}.

Spec: §2 of 2026-05-19-fallback-observability-design.md"
```

---

### Task 3: Retrofit digest.py data assembly fallback sites (5 sites)

**Files:**
- Modify: `src/donna/notifications/digest.py:113-121, 183-192, 233-256, 260-278`

These are the `_assemble_data()` fallback sites: self-diagnostic, calendar, cost queries, config read, and tool gaps.

- [ ] **Step 1: Add fallback alerts to _assemble_data and self-diagnostic**

In `src/donna/notifications/digest.py`, replace the self-diagnostic block (lines 113-121):

```python
        if self._self_diagnostic is not None:
            try:
                issues = await self._self_diagnostic.run()
                if issues:
                    data["system_status"] = "\n".join(
                        [":warning: System warnings:"] + [f"  • {w}" for w in issues]
                    )
            except Exception as exc:
                logger.exception("morning_digest_self_diagnostic_failed")
                await self._service.dispatch_fallback_alert(
                    component="morning_digest",
                    error=f"Self-diagnostic crashed: {type(exc).__name__}: {exc}",
                    fallback="system_status shows 'All systems normal' (unverified)",
                )
```

- [ ] **Step 2: Add fallback alert to calendar query**

Replace the calendar block in `_assemble_data` (lines 182-192):

```python
        if self._calendar_client is not None:
            try:
                events = await self._calendar_client.list_events(
                    self._calendar_id, today_start, today_end
                )
                calendar_events_list = [
                    f"- {ev.summary} ({ev.start.strftime('%H:%M')}–{ev.end.strftime('%H:%M')})"
                    for ev in events
                ]
            except Exception as exc:
                logger.exception("morning_digest_calendar_failed")
                await self._service.dispatch_fallback_alert(
                    component="morning_digest",
                    error=f"Calendar API failed: {type(exc).__name__}: {exc}",
                    fallback="calendar_events shows 'No events today' (may be masking API failure)",
                    context={"calendar_id": self._calendar_id},
                )
```

- [ ] **Step 3: Add fallback alert to cost queries and replace contextlib.suppress**

Replace the cost query block (lines 232-256):

```python
        yesterday_cost = 0.0
        mtd_cost = 0.0
        try:
            conn = self._db.connection
            row = await (await conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM invocation_log WHERE timestamp >= ?",
                (yesterday_start.isoformat(),),
            )).fetchone()
            if row:
                yesterday_cost = float(row[0])

            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            row2 = await (await conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM invocation_log WHERE timestamp >= ?",
                (month_start.isoformat(),),
            )).fetchone()
            if row2:
                mtd_cost = float(row2[0])
        except Exception as exc:
            logger.exception("morning_digest_cost_query_failed")
            await self._service.dispatch_fallback_alert(
                component="morning_digest",
                error=f"Cost query failed: {type(exc).__name__}: {exc}",
                fallback="costs showing as $0.00",
            )

        monthly_budget = 100.0
        try:
            monthly_budget = self._router._models_config.cost.monthly_budget_usd
        except Exception as exc:
            logger.warning(
                "morning_digest_config_read_failed",
                error=str(exc),
                event_type="fallback_activated",
            )
            await self._service.dispatch_fallback_alert(
                component="morning_digest",
                error=f"Config read failed: {type(exc).__name__}: {exc}",
                fallback="monthly_budget defaulting to $100.00",
            )
```

Remove the `import contextlib` from the top of the file if it's no longer used after this change — check first.

- [ ] **Step 4: Add fallback alert to tool gaps query**

Replace the tool gaps block (lines 260-278):

```python
        tool_gap_lines: list[str] = []
        if self._tool_request_repo is not None:
            try:
                rows = await self._tool_request_repo.list_open_speculative(
                    exclude_snoozed=True, now=now,
                )
                for row in rows:
                    blocking = (
                        f"capability `{row.blocking_capability_id}`"
                        if row.blocking_capability_id
                        else "skill draft"
                    )
                    tool_gap_lines.append(
                        f"- `{row.tool_name}` (priority {row.priority}, from "
                        f"{blocking}, first seen "
                        f"{row.first_seen_at.strftime('%Y-%m-%d')})"
                    )
            except Exception as exc:
                logger.exception("morning_digest_tool_gaps_query_failed")
                await self._service.dispatch_fallback_alert(
                    component="morning_digest",
                    error=f"Tool gaps query failed: {type(exc).__name__}: {exc}",
                    fallback="tool_gaps showing as 'None.'",
                )
```

- [ ] **Step 5: Run all digest tests**

Run: `pytest tests/unit/test_digest.py -v`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add src/donna/notifications/digest.py
git commit -m "fix: add fallback alerts to all digest data assembly sites

Self-diagnostic, calendar API, cost queries, config read, and tool
gaps now dispatch fallback alerts when they fail. Replaces
contextlib.suppress with explicit try/except + alert.

Spec: §3 sites 2-6 of 2026-05-19-fallback-observability-design.md"
```

---

### Task 4: Retrofit weekly_digest.py and eod_digest.py

**Files:**
- Modify: `src/donna/notifications/weekly_digest.py:83-118`
- Modify: `src/donna/notifications/eod_digest.py:356-373`

- [ ] **Step 1: Add fallback alert to weekly_digest.py**

Replace lines 83-118 in `src/donna/notifications/weekly_digest.py`. The exception path alerts immediately; the `else` branch only alerts when no exception was raised but `digest_text` is still None (wrong schema case):

```python
        # Try LLM-generated digest.
        digest_text: str | None = None
        llm_failed = False
        try:
            prompt = self._build_prompt(stats)
            result, _ = await self._router.complete(
                prompt, task_type="generate_weekly_digest", user_id=self._user_id
            )
            digest_text = result.get("digest_text") if isinstance(result, dict) else None
        except ContextOverflowError:
            raise
        except Exception as exc:
            llm_failed = True
            logger.exception("weekly_digest_llm_failed")
            await self._service.dispatch_fallback_alert(
                component="weekly_digest",
                error=f"LLM failed: {type(exc).__name__}: {exc}",
                fallback="plain-text stats table",
            )

        if digest_text:
            embed = discord.Embed(
                title="Weekly Efficiency Report",
                description=digest_text,
                colour=EMBED_COLOUR,
            )
            await self._service.dispatch(
                notification_type=NOTIF_DIGEST,
                content=digest_text,
                channel=CHANNEL_DIGEST,
                priority=5,
                embed=embed,
            )
            logger.info("weekly_digest_sent_llm")
        else:
            if not llm_failed:
                await self._service.dispatch_fallback_alert(
                    component="weekly_digest",
                    error="digest_text was None (wrong schema or empty response)",
                    fallback="plain-text stats table",
                )
            fallback = self._render_fallback(stats)
            await self._service.dispatch(
                notification_type=NOTIF_DIGEST,
                content=fallback,
                channel=CHANNEL_DIGEST,
                priority=5,
            )
            logger.info("weekly_digest_sent_fallback")
```

- [ ] **Step 2: Add fallback alerts to eod_digest.py**

Replace lines 356-373 in `src/donna/notifications/eod_digest.py`:

```python
        today_cost = 0.0
        try:
            conn = self._db.connection
            row = await (await conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM invocation_log WHERE timestamp >= ?",
                (today_start.isoformat(),),
            )).fetchone()
            if row:
                today_cost = float(row[0])
        except Exception as exc:
            logger.exception("eod_digest_cost_query_failed")
            await self._service.dispatch_fallback_alert(
                component="eod_digest",
                error=f"Cost query failed: {type(exc).__name__}: {exc}",
                fallback="today_cost showing as $0.00",
            )

        skill_system = {}
        try:
            skill_system = await self._assemble_skill_system_data(now)
        except Exception as exc:
            logger.exception("eod_digest_skill_system_query_failed")
            await self._service.dispatch_fallback_alert(
                component="eod_digest",
                error=f"Skill system query failed: {type(exc).__name__}: {exc}",
                fallback="skill system section omitted from digest",
            )
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/unit/test_digest.py tests/unit/test_eod_digest_skill_section.py -v`
Expected: All PASS.

- [ ] **Step 4: Commit**

```bash
git add src/donna/notifications/weekly_digest.py src/donna/notifications/eod_digest.py
git commit -m "fix: add fallback alerts to weekly and EOD digests

Weekly digest LLM failure and wrong-schema paths now alert.
EOD digest cost query and skill system query failures now alert.

Spec: §3 sites 7-8 of 2026-05-19-fallback-observability-design.md"
```

---

### Task 5: Retrofit reminders.py and migrate overdue.py

**Files:**
- Modify: `src/donna/notifications/reminders.py:148-189`
- Modify: `src/donna/notifications/overdue.py:87-97, 120-122, 244-247, 269-272`

- [ ] **Step 1: Add fallback alert to reminders.py**

Replace lines 184-189 in `src/donna/notifications/reminders.py`:

```python
        except ContextOverflowError:
            raise
        except Exception as exc:
            logger.exception("reminder_llm_failed", task_id=task.id)
            await self._service.dispatch_fallback_alert(
                component="reminder",
                error=f"LLM failed: {type(exc).__name__}: {exc}",
                fallback="template string reminder",
                context={"task_id": task.id, "task_title": task.title},
            )

        return fallback, False
```

- [ ] **Step 2: Migrate overdue.py from _alert_debug to dispatch_fallback_alert**

Remove the `_alert_debug` method (lines 87-97) and replace all 3 call sites:

**Site 1** (line 120-122, overdue check failed):
```python
            except Exception as exc:
                logger.exception("overdue_check_failed")
                await self._service.dispatch_fallback_alert(
                    component="overdue_detector",
                    error=f"Overdue check failed: {type(exc).__name__}: {exc}",
                    fallback="skipped this check cycle",
                )
```

**Site 2** (line 244-247, nudge LLM failed):
```python
        except Exception as exc:
            logger.exception("nudge_llm_failed", task_id=getattr(task, "id", None))
            await self._service.dispatch_fallback_alert(
                component="overdue_nudge",
                error=f"LLM nudge failed for '{getattr(task, 'title', '?')}': {type(exc).__name__}: {exc}",
                fallback="template string nudge",
                context={"task_id": getattr(task, "id", None)},
            )
```

**Site 3** (line 269-272, reply handler failed):
```python
            except Exception as exc:
                logger.exception("reply_handler_failed", task_id=task_id)
                await self._service.dispatch_fallback_alert(
                    component="overdue_reply",
                    error=f"Reply handler crashed for task '{task.title}': {type(exc).__name__}: {exc}",
                    fallback="reply ignored",
                    context={"task_id": task_id},
                )
                return None
```

Then delete the `_alert_debug` method (lines 87-97) and remove the `CHANNEL_DEBUG` import if it's no longer used in this file.

- [ ] **Step 3: Run overdue and reminder tests**

Run: `pytest tests/unit/ -k "overdue or reminder" -v`
Expected: All PASS.

- [ ] **Step 4: Commit**

```bash
git add src/donna/notifications/reminders.py src/donna/notifications/overdue.py
git commit -m "fix: add fallback alert to reminders, migrate overdue to dispatch_fallback_alert

Reminders LLM failures now alert via centralized method.
Overdue detector migrated from local _alert_debug() to
dispatch_fallback_alert() for consistency and rate limiting.

Spec: §3 sites 9-10 of 2026-05-19-fallback-observability-design.md"
```

---

### Task 6: Add fallback_alert_fn callback to ModelRouter

**Files:**
- Modify: `src/donna/models/router.py:117-175, 345-416, 491-505`
- Modify: `src/donna/cli_wiring.py:1241-1245, 1336-1343`

- [ ] **Step 1: Add fallback_alert_fn parameter to ModelRouter.__init__**

In `src/donna/models/router.py`, add to `__init__` parameters (after `payload_writer` on line 129):

```python
        fallback_alert_fn: Callable[..., Awaitable[bool]] | None = None,
```

And store it (after line 138):

```python
        self._fallback_alert_fn = fallback_alert_fn
```

Also add a late-bind setter (after `set_escalation_gate` around line 184):

```python
    def set_fallback_alert_fn(
        self, fn: Callable[..., Awaitable[bool]] | None
    ) -> None:
        """Late-bind the fallback alert callback.

        The notification service is constructed after the router in the
        boot sequence, so this is wired once the service exists.
        """
        self._fallback_alert_fn = fn
```

Add the `Callable` and `Awaitable` imports — check if they're already imported from `collections.abc`.

- [ ] **Step 2: Add fallback alert calls to context overflow and recovery**

In the context overflow fallback section (around line 375, after the `logger.warning("context_overflow_escalation", ...)`), add:

```python
                if self._fallback_alert_fn is not None:
                    try:
                        await self._fallback_alert_fn(
                            component="model_router",
                            error=f"Context overflow: {estimated_in} tokens > {budget} budget for {alias!r}",
                            fallback=f"escalated to {fallback_alias!r}",
                            context={"task_type": task_type, "from_alias": alias, "to_alias": fallback_alias},
                        )
                    except Exception:
                        logger.warning("fallback_alert_fn_failed", task_type=task_type)
```

In the recovery detection section (around line 501, after `logger.info("ollama_recovered", ...)`), add:

```python
                if self._fallback_alert_fn is not None:
                    try:
                        await self._fallback_alert_fn(
                            component="model_router",
                            error="Ollama recovered — no longer falling back to cloud",
                            fallback="resuming local model routing",
                            context={"task_type": task_type},
                        )
                    except Exception:
                        logger.warning("fallback_alert_fn_failed_recovery", task_type=task_type)
```

- [ ] **Step 3: Wire the callback in cli_wiring.py**

After the `notification_service` is constructed (around line 1343), add:

```python
            router.set_fallback_alert_fn(notification_service.dispatch_fallback_alert)
```

- [ ] **Step 4: Run model router tests**

Run: `pytest tests/unit/ -k "router" -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add src/donna/models/router.py src/donna/cli_wiring.py
git commit -m "feat: add fallback_alert_fn callback to ModelRouter

Ollama→Claude context overflow fallback and Ollama recovery now
dispatch fallback alerts. Uses a callback to avoid coupling the
model layer to NotificationService directly. Late-bound via
set_fallback_alert_fn() since the service is constructed after
the router in the boot sequence.

Spec: §3 sites 11-12 of 2026-05-19-fallback-observability-design.md"
```

---

### Task 7: Retrofit auto_scheduler.py and fix service.py DM bug

**Files:**
- Modify: `src/donna/scheduling/auto_scheduler.py:58-83`
- Modify: `src/donna/notifications/service.py:198-203`

- [ ] **Step 1: Add fallback alert to auto_scheduler.py**

Replace lines 78-83 in `src/donna/scheduling/auto_scheduler.py`:

```python
        except NoSlotFoundError:
            logger.warning("auto_scheduler_no_slot", task_id=task.id)
            return
        except Exception as exc:
            logger.exception("auto_scheduler_failed", task_id=task.id)
            if self._notification_service is not None:
                await self._notification_service.dispatch_fallback_alert(
                    component="auto_scheduler",
                    error=f"Scheduling failed: {type(exc).__name__}: {exc}",
                    fallback="task left in backlog",
                    context={"task_id": task.id},
                )
            return
```

Also add the fallback alert for the calendar-unavailable fallback path (line 69-77). This is the case where `self._calendar_client is None` but a task still needs scheduling:

```python
            else:
                slot = self._scheduler.find_next_slot(task, [])
                await self._db.transition_task_state(task.id, TaskStatus.SCHEDULED)
                await self._db.update_task(
                    task.id,
                    scheduled_start=slot.start,
                    donna_managed=True,
                )
                logger.info("auto_scheduler_fallback_mode", task_id=task.id)
```

This is an "expected" fallback per the spec non-goals (calendar was never configured), so do **not** add an alert here. Only the exception path gets an alert.

- [ ] **Step 2: Fix the DM send failure bug in service.py**

Replace lines 198-203 in `src/donna/notifications/service.py`:

```python
        try:
            await self._bot.send_dm(discord_id, content)
            log.info("dm_sent")
        except Exception:
            log.exception("dm_send_failed", event_type="fallback_activated")
            return False
        return True
```

The return value is now correct: `True` only on success, `False` on failure. The `event_type="fallback_activated"` makes it grepable in Loki. No `dispatch_fallback_alert` here to avoid recursion risk.

- [ ] **Step 3: Run tests**

Run: `pytest tests/unit/test_notification_service.py tests/unit/test_notification_dm.py -v`
Expected: All PASS. Check if any existing test expects `return True` on DM failure — if so, update it.

- [ ] **Step 4: Commit**

```bash
git add src/donna/scheduling/auto_scheduler.py src/donna/notifications/service.py
git commit -m "fix: auto_scheduler fallback alert + fix DM send returning True on failure

Auto-scheduler exception path now alerts via dispatch_fallback_alert.
Fixed bug where dispatch_dm() returned True even when send_dm threw,
which made callers think the DM was delivered.

Spec: §3 sites 13-14 of 2026-05-19-fallback-observability-design.md"
```

---

### Task 8: Replace contextlib.suppress in discord_views.py and discord_bot.py

**Files:**
- Modify: `src/donna/integrations/discord_views.py:1122, 1152, 1218, 1223`
- Modify: `src/donna/integrations/discord_bot.py:882`

- [ ] **Step 1: Replace all 4 contextlib.suppress in discord_views.py**

Each `contextlib.suppress(Exception)` wrapping `interaction.message.edit(view=view)` gets the same replacement. For line 1122-1123:

```python
            try:
                await interaction.message.edit(view=view)  # type: ignore[union-attr]
            except Exception:
                logger.warning(
                    "discord_view_edit_failed",
                    event_type="fallback_activated",
                    component="tool_gap_view",
                )
```

Apply the same pattern to lines 1152-1153, 1218-1219, and 1223-1224. Use the same `component="tool_gap_view"` for all since they're in the same view family.

- [ ] **Step 2: Replace contextlib.suppress in discord_bot.py**

Replace line 882-883 in `src/donna/integrations/discord_bot.py`:

```python
                try:
                    existing_notes = _json.loads(existing_task.notes)
                except Exception:
                    logger.warning(
                        "dedup_notes_parse_failed",
                        event_type="fallback_activated",
                        component="dedup_reply",
                        task_id=existing_task.id,
                    )
```

- [ ] **Step 3: Remove contextlib import if unused**

Check if `contextlib` is still used elsewhere in each file. If not, remove the import. Run:

```bash
grep -n "contextlib" src/donna/integrations/discord_views.py
grep -n "contextlib" src/donna/integrations/discord_bot.py
```

Remove the import line if no other uses remain.

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/ -k "discord or bot or view" -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add src/donna/integrations/discord_views.py src/donna/integrations/discord_bot.py
git commit -m "fix: replace contextlib.suppress with logged try/except in Discord UI code

Four view edit suppressions and one JSON parse suppression now log
at WARNING with event_type=fallback_activated for Loki visibility.

Spec: §3 sites 15-16 of 2026-05-19-fallback-observability-design.md"
```

---

### Task 9: Add CLAUDE.md convention and CI lint

**Files:**
- Modify: `CLAUDE.md:56-62`
- Create: `tests/lint/test_no_contextlib_suppress.py`

- [ ] **Step 1: Add convention to CLAUDE.md**

In `CLAUDE.md`, append to the Conventions section (after the line about schema changes, line 62):

```markdown
- Every `try/except` that falls back to a default or degraded behavior must call `dispatch_fallback_alert()` (or log with `event_type="fallback_activated"` if `NotificationService` is unavailable). Never use `contextlib.suppress(Exception)`.
```

- [ ] **Step 2: Write the CI lint test**

Create `tests/lint/test_no_contextlib_suppress.py`:

```python
"""Lint: ban contextlib.suppress(Exception) in application code.

Blanket exception suppression hides failures that should be observable.
Use explicit try/except with logging and dispatch_fallback_alert() instead.
See CLAUDE.md Conventions and 2026-05-19-fallback-observability-design.md §4.
"""

from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[2] / "src" / "donna"
BANNED_PATTERN = "contextlib.suppress(Exception)"


def test_no_contextlib_suppress_in_src() -> None:
    violations: list[str] = []
    for py_file in SRC_DIR.rglob("*.py"):
        text = py_file.read_text()
        for i, line in enumerate(text.splitlines(), start=1):
            if BANNED_PATTERN in line:
                rel = py_file.relative_to(SRC_DIR.parent.parent)
                violations.append(f"{rel}:{i}: {line.strip()}")

    assert not violations, (
        f"Found {len(violations)} contextlib.suppress(Exception) usage(s).\n"
        "Replace with explicit try/except + logger.warning(..., event_type='fallback_activated').\n"
        "See CLAUDE.md Conventions.\n\n"
        + "\n".join(violations)
    )
```

- [ ] **Step 3: Run the lint test**

Run: `pytest tests/lint/test_no_contextlib_suppress.py -v`
Expected: PASS (all `contextlib.suppress(Exception)` were removed in Task 3 and Task 8).

If it fails, it means we missed a `contextlib.suppress(Exception)` somewhere — fix it before proceeding.

- [ ] **Step 4: Run full test suite**

Run: `pytest -x -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md tests/lint/test_no_contextlib_suppress.py
git commit -m "chore: add fallback observability convention to CLAUDE.md + CI lint

Codifies the rule that every fallback/degraded path must be
observable. Adds a lint test that bans contextlib.suppress(Exception)
in src/donna/.

Spec: §4 of 2026-05-19-fallback-observability-design.md"
```
