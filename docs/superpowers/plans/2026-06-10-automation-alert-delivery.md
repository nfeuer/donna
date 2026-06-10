# Automation Alert Delivery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make scheduled automation alerts actually reach the user by evaluating crons in the user's timezone, exempting user-configured/system notifications from blackout/quiet hours, and recording `alert_sent` truthfully.

**Architecture:** Three independent fixes plus wiring. (1) `CronScheduleCalculator` gains an optional timezone and evaluates cron fields in it. (2) `NotificationService` consults a config-driven exempt-list before applying blackout/quiet gates. (3) `AutomationDispatcher` propagates each channel's real delivery result into `alert_sent` and surfaces deferred deliveries. A startup recompute realigns existing `next_run_at` values after the timezone change.

**Tech Stack:** Python 3.12 / asyncio, `croniter`, `pydantic` (config models), `aiosqlite`, `pytest` + `unittest.mock`, `structlog`.

**Spec:** `docs/superpowers/specs/2026-06-10-automation-alert-delivery-design.md`

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/donna/automations/cron.py` | Cron next-run arithmetic | Add tz; evaluate in tz |
| `config/notifications.yaml` | Per-type window policy | Create |
| `src/donna/config.py` | Config models + loaders | Add `NotificationPolicyConfig` + loader |
| `src/donna/notifications/service.py` | Outbound dispatch + window gating | Consult policy in `dispatch`/`dispatch_dm` |
| `src/donna/automations/dispatcher.py` | One automation run end-to-end | Truthful `alert_sent`; debug on deferral |
| `src/donna/automations/creation_flow.py` | NL automation creation | Use injected tz-aware cron |
| `src/donna/automations/reschedule.py` | One-shot next-run realign | Create |
| `src/donna/cli_wiring.py` | Dependency wiring | Pass tz to crons, policy to service, run recompute |
| `config/calendar.yaml` | Window docs | Update blackout comment |
| `spec_v3.md`, `docs/superpowers/specs/followups.md` | Canonical docs | Update |

Tests: `tests/unit/test_automation_cron.py`, `tests/unit/test_config_*`, `tests/unit/test_notification_service.py`, `tests/unit/test_automation_alert.py` (dispatcher portion) / `tests/unit/test_automation_dispatcher.py`, `tests/unit/test_automation_reschedule.py` (new).

Run the whole suite with: `uv run pytest <path> -v`

---

### Task 1: Evaluate cron in a configured timezone

**Files:**
- Modify: `src/donna/automations/cron.py`
- Test: `tests/unit/test_automation_cron.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_automation_cron.py`:

```python
from zoneinfo import ZoneInfo


def test_next_run_interprets_cron_in_configured_tz_summer():
    # During EDT (UTC-4), "9 AM Eastern" is 13:00 UTC.
    calc = CronScheduleCalculator(tz=ZoneInfo("America/New_York"))
    ref = datetime(2026, 6, 10, 6, 0, tzinfo=UTC)  # 02:00 EDT
    nxt = calc.next_run(expression="0 9 * * *", after=ref)
    assert nxt == datetime(2026, 6, 10, 13, 0, tzinfo=UTC)


def test_next_run_interprets_cron_in_configured_tz_winter():
    # During EST (UTC-5), "9 AM Eastern" is 14:00 UTC (DST correctness).
    calc = CronScheduleCalculator(tz=ZoneInfo("America/New_York"))
    ref = datetime(2026, 1, 10, 6, 0, tzinfo=UTC)  # 01:00 EST
    nxt = calc.next_run(expression="0 9 * * *", after=ref)
    assert nxt == datetime(2026, 1, 10, 14, 0, tzinfo=UTC)


def test_next_run_defaults_to_utc_when_no_tz():
    # No tz => legacy UTC interpretation (backward compatible).
    calc = CronScheduleCalculator()
    ref = datetime(2026, 6, 10, 6, 0, tzinfo=UTC)
    nxt = calc.next_run(expression="0 9 * * *", after=ref)
    assert nxt == datetime(2026, 6, 10, 9, 0, tzinfo=UTC)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_automation_cron.py -v -k "configured_tz or defaults_to_utc"`
Expected: FAIL — `CronScheduleCalculator()` takes no `tz` argument (`TypeError`).

- [ ] **Step 3: Implement tz-aware evaluation**

Replace the entire body of `src/donna/automations/cron.py` with:

```python
"""CronScheduleCalculator — thin wrapper over croniter for next-run arithmetic."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from croniter import CroniterBadCronError, croniter


class InvalidCronExpressionError(ValueError):
    """Raised when the cron expression cannot be parsed."""


class CronScheduleCalculator:
    def __init__(self, tz: ZoneInfo | None = None) -> None:
        """Args:
        tz: Zone in which cron fields are interpreted. When None, fields are
            interpreted in UTC (legacy behavior).
        """
        self._tz = tz

    def next_run(self, *, expression: str, after: datetime) -> datetime:
        """Compute the next execution time strictly AFTER *after*.

        Cron fields are interpreted in the configured timezone (or UTC when
        none was set). The returned datetime is timezone-aware UTC. DST is
        honored because croniter advances over a tz-aware base time.
        """
        zone = self._tz or UTC
        if after.tzinfo is None:
            after = after.replace(tzinfo=UTC)
        local_after = after.astimezone(zone)
        try:
            it = croniter(expression, local_after)
        except (CroniterBadCronError, ValueError, KeyError) as exc:
            raise InvalidCronExpressionError(
                f"invalid cron expression {expression!r}: {exc}"
            ) from exc
        nxt = it.get_next(datetime)
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=zone)
        return nxt.astimezone(UTC)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_automation_cron.py -v`
Expected: PASS — including the pre-existing UTC tests (they construct `CronScheduleCalculator()` with no tz).

- [ ] **Step 5: Commit**

```bash
git add src/donna/automations/cron.py tests/unit/test_automation_cron.py
git commit -m "feat(automations): evaluate cron in configured timezone"
```

---

### Task 2: Notification-policy config + loader

**Files:**
- Create: `config/notifications.yaml`
- Modify: `src/donna/config.py`
- Test: `tests/unit/test_config_notification_policy.py` (new)

- [ ] **Step 1: Create the config file**

Create `config/notifications.yaml`:

```yaml
# Per-type blackout / quiet-hours policy.
#
# Types NOT listed below respect BOTH windows (the safe default):
#   - blackout (12 AM–6 AM): absolute window, all priorities queued
#   - quiet hours (10 PM–12 AM): priority < 5 queued
# Listed types are EXEMPT and deliver regardless of the named window.
#
# Rationale: proactive nudges (overdue, post_meeting, evening_checkin,
# stale_task, afternoon_inactivity, digests) use many evolving type strings,
# so they are gated by the default rather than enumerated. Only deliberately
# exempt types are listed.
notification_policy:
  blackout_exempt:
    - reminder            # user-set for a specific time
    - automation_alert    # user-configured deliberately
    - automation_failure  # ops signal
    - debug               # ops signal
  quiet_exempt:
    - reminder
    - automation_alert
    - automation_failure
    - debug
```

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_config_notification_policy.py`:

```python
from __future__ import annotations

from pathlib import Path

from donna.config import (
    NotificationPolicyConfig,
    load_notification_policy_config,
)


def test_load_notification_policy(tmp_path: Path) -> None:
    (tmp_path / "notifications.yaml").write_text(
        "notification_policy:\n"
        "  blackout_exempt: [reminder, debug]\n"
        "  quiet_exempt: [debug]\n"
    )
    cfg = load_notification_policy_config(tmp_path)
    assert isinstance(cfg, NotificationPolicyConfig)
    assert cfg.blackout_exempt == ["reminder", "debug"]
    assert cfg.quiet_exempt == ["debug"]


def test_load_notification_policy_missing_section_defaults_empty(tmp_path: Path) -> None:
    (tmp_path / "notifications.yaml").write_text("{}\n")
    cfg = load_notification_policy_config(tmp_path)
    assert cfg.blackout_exempt == []
    assert cfg.quiet_exempt == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_config_notification_policy.py -v`
Expected: FAIL — `ImportError: cannot import name 'NotificationPolicyConfig'`.

- [ ] **Step 4: Implement the config model + loader**

In `src/donna/config.py`, add near the other notification/calendar config models (after `load_calendar_config`):

```python
class NotificationPolicyConfig(BaseModel):
    """Per-type blackout/quiet-hours exemptions (from notifications.yaml)."""

    blackout_exempt: list[str] = Field(default_factory=list)
    quiet_exempt: list[str] = Field(default_factory=list)


def load_notification_policy_config(config_dir: Path) -> NotificationPolicyConfig:
    """Load the per-type notification window policy."""
    data = load_yaml(config_dir / "notifications.yaml")
    section = (data or {}).get("notification_policy") or {}
    return NotificationPolicyConfig(**section)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_config_notification_policy.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add config/notifications.yaml src/donna/config.py tests/unit/test_config_notification_policy.py
git commit -m "feat(config): per-type notification window policy"
```

---

### Task 3: NotificationService consults the policy

**Files:**
- Modify: `src/donna/notifications/service.py`
- Test: `tests/unit/test_notification_service.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_notification_service.py`. First extend the bot helper to expose `send_dm` (place near `_make_bot`):

```python
from donna.config import NotificationPolicyConfig


def _make_bot_with_dm() -> MagicMock:
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=None)
    bot.send_embed = AsyncMock(return_value=None)
    bot.send_to_thread = AsyncMock()
    bot.send_dm = AsyncMock(return_value=None)
    return bot


def _make_service_with_policy(policy: NotificationPolicyConfig) -> tuple[NotificationService, MagicMock]:
    tw = _make_time_windows()
    cfg = _make_calendar_config(tw)
    bot = _make_bot_with_dm()
    service = NotificationService(
        bot=bot, calendar_config=cfg, user_id="u1",
        notification_policy=policy,
    )
    return service, bot
```

Then the tests:

```python
import pytest

from donna.notifications.service import NOTIF_AUTOMATION_ALERT


@pytest.mark.asyncio
async def test_blackout_exempt_type_sends_during_blackout() -> None:
    policy = NotificationPolicyConfig(
        blackout_exempt=[NOTIF_AUTOMATION_ALERT], quiet_exempt=[]
    )
    service, bot = _make_service_with_policy(policy)
    with patch("donna.notifications.service.datetime") as mock_dt:
        mock_dt.now.return_value = _utc(3)  # 3 AM — blackout
        sent = await service.dispatch_dm(
            "123", NOTIF_AUTOMATION_ALERT, "deal!", priority=3
        )
    assert sent is True
    bot.send_dm.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_exempt_type_queues_during_blackout() -> None:
    policy = NotificationPolicyConfig(
        blackout_exempt=[NOTIF_AUTOMATION_ALERT], quiet_exempt=[]
    )
    service, bot = _make_service_with_policy(policy)
    with patch("donna.notifications.service.datetime") as mock_dt:
        mock_dt.now.return_value = _utc(3)  # 3 AM — blackout
        sent = await service.dispatch_dm("123", "overdue", "nudge", priority=3)
    assert sent is False
    bot.send_dm.assert_not_awaited()


@pytest.mark.asyncio
async def test_quiet_exempt_type_sends_during_quiet_hours() -> None:
    policy = NotificationPolicyConfig(
        blackout_exempt=[], quiet_exempt=[NOTIF_AUTOMATION_ALERT]
    )
    service, bot = _make_service_with_policy(policy)
    with patch("donna.notifications.service.datetime") as mock_dt:
        mock_dt.now.return_value = _utc(21)  # 9 PM — quiet hours
        sent = await service.dispatch_dm(
            "123", NOTIF_AUTOMATION_ALERT, "deal!", priority=3
        )
    assert sent is True
    bot.send_dm.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_policy_keeps_legacy_gating() -> None:
    # Backward compat: with no policy, every type respects blackout.
    tw = _make_time_windows()
    cfg = _make_calendar_config(tw)
    bot = _make_bot_with_dm()
    service = NotificationService(bot=bot, calendar_config=cfg, user_id="u1")
    with patch("donna.notifications.service.datetime") as mock_dt:
        mock_dt.now.return_value = _utc(3)
        sent = await service.dispatch_dm(
            "123", NOTIF_AUTOMATION_ALERT, "deal!", priority=3
        )
    assert sent is False
    bot.send_dm.assert_not_awaited()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_notification_service.py -v -k "exempt or legacy_gating"`
Expected: FAIL — `NotificationService.__init__` has no `notification_policy` parameter (`TypeError`).

- [ ] **Step 3: Add the policy parameter and helpers**

In `src/donna/notifications/service.py`, import the config model at the top (with the other `donna.config` import):

```python
from donna.config import CalendarConfig, NotificationPolicyConfig
```

Extend `__init__` signature and store the exempt sets. Change the signature to add the new keyword-only-friendly parameter (append it after `digest_max_chars`):

```python
    def __init__(
        self,
        bot: BotProtocol,
        calendar_config: CalendarConfig,
        user_id: str,
        sms: TwilioSMS | None = None,
        gmail: GmailClient | None = None,
        digest_max_chars: int = DIGEST_MAX_CHARS_DEFAULT,
        notification_policy: NotificationPolicyConfig | None = None,
    ) -> None:
```

At the end of `__init__` (after `self._alerting = False`), add:

```python
        # Per-type window exemptions. Empty sets => every type respects both
        # windows (legacy behavior when no policy is supplied).
        self._blackout_exempt: set[str] = (
            set(notification_policy.blackout_exempt) if notification_policy else set()
        )
        self._quiet_exempt: set[str] = (
            set(notification_policy.quiet_exempt) if notification_policy else set()
        )
```

Add two helpers (place them next to `_is_blackout` / `_is_quiet`):

```python
    def _respects_blackout(self, notification_type: str) -> bool:
        return notification_type not in self._blackout_exempt

    def _respects_quiet(self, notification_type: str) -> bool:
        return notification_type not in self._quiet_exempt
```

- [ ] **Step 4: Apply the policy in `dispatch`**

In `dispatch`, change the two gates:

```python
        # Hard block: blackout applies to all priorities (unless type-exempt).
        if self._is_blackout(now) and self._respects_blackout(notification_type):
            log.info("notification_queued_blackout")
            self._enqueue(notification_type, content, channel, priority, embed, thread_id)
            return False

        # Soft block: quiet hours apply to priority < 5 (unless type-exempt).
        if (
            self._is_quiet(now)
            and priority < 5
            and self._respects_quiet(notification_type)
        ):
            log.info("notification_queued_quiet_hours")
            self._enqueue(notification_type, content, channel, priority, embed, thread_id)
            return False
```

- [ ] **Step 5: Apply the policy in `dispatch_dm`**

In `dispatch_dm`, change the two gates:

```python
        if self._is_blackout(now) and self._respects_blackout(notification_type):
            log.info("dm_queued_blackout")
            self._enqueue_dm(discord_id, notification_type, content, priority)
            return False

        if (
            self._is_quiet(now)
            and priority < 5
            and self._respects_quiet(notification_type)
        ):
            log.info("dm_queued_quiet_hours")
            self._enqueue_dm(discord_id, notification_type, content, priority)
            return False
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_notification_service.py -v`
Expected: PASS (new tests + all pre-existing ones — they pass no policy, so gating is unchanged).

- [ ] **Step 7: Commit**

```bash
git add src/donna/notifications/service.py tests/unit/test_notification_service.py
git commit -m "feat(notifications): per-type blackout/quiet exemptions"
```

---

### Task 4: Truthful `alert_sent` in the dispatcher

**Files:**
- Modify: `src/donna/automations/dispatcher.py`
- Test: `tests/unit/test_automation_dispatcher.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_automation_dispatcher.py`. These exercise the alert-dispatch block by driving `dispatch()` with a mock notifier. Match the existing fixtures in that file for building an `AutomationDispatcher`; the assertions that matter:

```python
@pytest.mark.asyncio
async def test_alert_sent_false_when_dm_deferred(dispatcher_with_mocks):
    # dispatch_dm returns False (queued/blocked) -> alert_sent must be False
    # and a debug notification must be emitted.
    d, mocks = dispatcher_with_mocks
    mocks.notifier.dispatch_dm = AsyncMock(return_value=False)
    mocks.notifier.dispatch = AsyncMock(return_value=True)  # debug channel
    report = await d.dispatch(mocks.automation)  # alert_conditions match output
    assert report.alert_sent is False
    # A debug notification about non-delivery was sent:
    assert any(
        call.kwargs.get("channel") == CHANNEL_DEBUG
        for call in mocks.notifier.dispatch.await_args_list
    )


@pytest.mark.asyncio
async def test_alert_sent_true_when_dm_delivered(dispatcher_with_mocks):
    d, mocks = dispatcher_with_mocks
    mocks.notifier.dispatch_dm = AsyncMock(return_value=True)
    report = await d.dispatch(mocks.automation)
    assert report.alert_sent is True
```

> If `tests/unit/test_automation_dispatcher.py` has no shared `dispatcher_with_mocks` fixture, add one that constructs an `AutomationDispatcher` with: a `claude_native` path (no skill row), a `model_router` whose `complete` returns an output dict that satisfies the automation's `alert_conditions` (e.g. `{"triggers_alert": True}` with conditions `{"field": "triggers_alert", "op": "==", "value": True}`), a real `AlertEvaluator`, a `cron` returning any datetime, and a `MagicMock` notifier. Reuse the construction already present in that file's other tests rather than inventing a new shape.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_automation_dispatcher.py -v -k "alert_sent_false_when_dm_deferred or alert_sent_true_when_dm_delivered"`
Expected: FAIL — current code sets `alert_sent = True` regardless of the DM result, so `test_alert_sent_false_when_dm_deferred` fails.

- [ ] **Step 3: Make `_dispatch_alert_to_channel` return a delivery bool**

In `src/donna/automations/dispatcher.py`, replace `_dispatch_alert_to_channel` with a version that returns `bool` (True only if the channel actually delivered):

```python
    async def _dispatch_alert_to_channel(
        self,
        channel: str,
        automation: AutomationRow,
        content: str,
    ) -> bool:
        if channel == "discord_dm":
            return await self._notifier.dispatch_dm(
                discord_id=automation.user_id,
                notification_type=NOTIF_AUTOMATION_ALERT,
                content=content,
                priority=3,
            )
        elif channel == "sms":
            phone = os.environ.get("DONNA_USER_PHONE", "")
            if phone:
                return await self._notifier.dispatch_sms(
                    body=content, to=phone, priority=3,
                )
            logger.warning(
                "automation_alert_sms_no_phone",
                automation_id=automation.id,
            )
            return False
        elif channel == "email":
            email = os.environ.get("DONNA_USER_EMAIL", "")
            if email:
                return await self._notifier.dispatch_email(
                    to=email,
                    subject=f"Donna Alert: {automation.name}",
                    body=content,
                    priority=3,
                )
            logger.warning(
                "automation_alert_email_no_address",
                automation_id=automation.id,
            )
            return False
        elif channel in ("discord_channel", "discord"):
            return await self._notifier.dispatch(
                notification_type=NOTIF_AUTOMATION_ALERT,
                content=content,
                channel=CHANNEL_TASKS,
                priority=3,
            )
        else:
            logger.warning(
                "automation_alert_unknown_channel",
                automation_id=automation.id,
                channel=channel,
            )
            return False
```

- [ ] **Step 4: Aggregate the real results into `alert_sent`**

In `dispatch`, replace the alert-dispatch block (currently lines ~260-274) with:

```python
            if fires:
                alert_content = self._render_alert_content(automation, output)
                try:
                    if self._notifier is not None:
                        channels = automation.alert_channels or ["discord_dm"]
                        delivered = False
                        deferred: list[str] = []
                        for ch in channels:
                            ok = await self._dispatch_alert_to_channel(
                                ch, automation, alert_content,
                            )
                            if ok:
                                delivered = True
                            else:
                                deferred.append(ch)
                        alert_sent = delivered
                        if deferred:
                            await self._notify_debug(
                                f"Automation '{automation.name}' alert NOT "
                                f"delivered on channel(s): {', '.join(deferred)} "
                                f"(queued, blocked, or misconfigured)."
                            )
                except Exception:
                    logger.exception(
                        "automation_alert_dispatch_failed",
                        automation_id=automation.id,
                    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_automation_dispatcher.py tests/unit/test_automation_alert.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/donna/automations/dispatcher.py tests/unit/test_automation_dispatcher.py
git commit -m "fix(automations): record alert_sent from real delivery result"
```

---

### Task 5: CreationFlow uses an injected tz-aware cron

**Files:**
- Modify: `src/donna/automations/creation_flow.py`
- Test: `tests/unit/test_automation_creation_flow.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_automation_creation_flow.py` (adapt the existing flow-construction fixture in that file — pass the new `cron=` kwarg):

```python
from zoneinfo import ZoneInfo

from donna.automations.cron import CronScheduleCalculator


@pytest.mark.asyncio
async def test_creation_uses_injected_tz_cron(make_creation_flow, sample_draft):
    # sample_draft.schedule_cron == "0 9 * * *"
    flow = make_creation_flow(cron=CronScheduleCalculator(tz=ZoneInfo("America/New_York")))
    await flow.create(sample_draft)
    created = await flow._repo.get_last_created()  # or inspect the repo mock call
    # 9 AM Eastern is 13:00 or 14:00 UTC — never 09:00 UTC (the old UTC bug).
    assert created.next_run_at.hour in (13, 14)
```

> If the existing fixture builds `CreationFlow` directly, just add `cron=...` to that call. The key assertion: `next_run_at` is no longer computed as 09:00 UTC.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_automation_creation_flow.py -v -k injected_tz_cron`
Expected: FAIL — `CreationFlow.__init__` has no `cron` parameter, or `next_run_at` is 09:00 UTC.

- [ ] **Step 3: Inject the cron into CreationFlow**

In `src/donna/automations/creation_flow.py`, add a `cron` parameter to `__init__` (append after the existing parameters), defaulting to a UTC calculator for backward compatibility:

```python
        cron: CronScheduleCalculator | None = None,
```

Store it (next to the other `self._...` assignments in `__init__`):

```python
        self._cron = cron or CronScheduleCalculator()
```

Ensure the import is present at the top of the file (it already imports from `donna.automations.cron`; confirm `CronScheduleCalculator` is imported). Then replace the inline construction:

```python
                next_run_at = self._cron.next_run(
                    expression=schedule_expr, after=datetime.now(UTC),
                )
```

(was `CronScheduleCalculator().next_run(...)`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_automation_creation_flow.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/donna/automations/creation_flow.py tests/unit/test_automation_creation_flow.py
git commit -m "feat(automations): inject tz-aware cron into CreationFlow"
```

---

### Task 6: One-shot `next_run_at` realign

**Files:**
- Create: `src/donna/automations/reschedule.py`
- Test: `tests/unit/test_automation_reschedule.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_automation_reschedule.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.automations.reschedule import recompute_next_runs


def _auto(id_: str, trigger_type: str, schedule: str | None):
    a = MagicMock()
    a.id = id_
    a.trigger_type = trigger_type
    a.schedule = schedule
    return a


@pytest.mark.asyncio
async def test_recompute_only_scheduled_automations():
    repo = MagicMock()
    repo.list_all = AsyncMock(return_value=[
        _auto("a", "on_schedule", "0 9 * * *"),
        _auto("b", "manual", None),
        _auto("c", "on_schedule", None),  # no schedule -> skip
    ])
    repo.update_fields = AsyncMock()
    cron = MagicMock()
    cron.next_run.return_value = datetime(2026, 6, 10, 13, 0, tzinfo=UTC)
    now = datetime(2026, 6, 10, 7, 0, tzinfo=UTC)

    count = await recompute_next_runs(repo, cron, now)

    assert count == 1
    repo.update_fields.assert_awaited_once_with(
        "a", next_run_at=datetime(2026, 6, 10, 13, 0, tzinfo=UTC)
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_automation_reschedule.py -v`
Expected: FAIL — module `donna.automations.reschedule` does not exist.

- [ ] **Step 3: Implement the realign routine**

Create `src/donna/automations/reschedule.py`:

```python
"""One-shot realignment of automation next_run_at after a cron-tz change.

Idempotent: recomputes next_run_at for every active on_schedule automation
using the supplied (tz-aware) cron calculator. Safe to run on every startup.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog

logger = structlog.get_logger()


async def recompute_next_runs(repo: Any, cron: Any, now: datetime) -> int:
    """Recompute next_run_at for all active on_schedule automations.

    Args:
        repo: AutomationRepository (needs list_all + update_fields).
        cron: CronScheduleCalculator configured with the user timezone.
        now: Reference time for the next-run computation (UTC).

    Returns:
        Number of automations whose schedule was recomputed.
    """
    automations = await repo.list_all(status="active", limit=1000)
    count = 0
    for automation in automations:
        if automation.trigger_type != "on_schedule" or not automation.schedule:
            continue
        try:
            next_run_at = cron.next_run(expression=automation.schedule, after=now)
        except Exception:
            logger.warning(
                "automation_reschedule_invalid_cron",
                automation_id=automation.id,
                schedule=automation.schedule,
            )
            continue
        await repo.update_fields(automation.id, next_run_at=next_run_at)
        count += 1
    if count:
        logger.info("automation_next_runs_recomputed", count=count)
    return count
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_automation_reschedule.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/donna/automations/reschedule.py tests/unit/test_automation_reschedule.py
git commit -m "feat(automations): idempotent next_run_at realign routine"
```

---

### Task 7: Wire timezone + policy + startup realign

**Files:**
- Modify: `src/donna/cli_wiring.py`

> No new unit test — this is composition. Verified by the full suite plus a smoke run. Make the edits, then run the whole `tests/unit` suite.

- [ ] **Step 1: Build a single tz-aware cron and the policy**

In `src/donna/cli_wiring.py`, where the calendar config is loaded for the `NotificationService` (around line 1350, `calendar_config = load_calendar_config(config_dir)`), also load the policy and pass it:

```python
            calendar_config = load_calendar_config(config_dir)
            notification_policy = load_notification_policy_config(config_dir)
            notification_service = NotificationService(
                bot=bot,
                calendar_config=calendar_config,
                user_id=user_id,
                sms=twilio_sms_instance,
                gmail=None,
                notification_policy=notification_policy,
            )
```

Add `load_notification_policy_config` to the existing `from donna.config import (...)` block at the top of the file.

- [ ] **Step 2: Pass tz to every `CronScheduleCalculator()` in wiring**

There are two construction sites in `cli_wiring.py` (around lines 2280 and 2338). Replace each bare `CronScheduleCalculator()` with a tz-aware one built from the calendar timezone. Add near the top of the function that wires automations:

```python
        from zoneinfo import ZoneInfo
        from donna.automations.cron import CronScheduleCalculator

        _automation_tz = ZoneInfo(load_calendar_config(config_dir).timezone)
        automation_cron = CronScheduleCalculator(tz=_automation_tz)
```

- Line ~2280 `self._cron = CronScheduleCalculator()` → `self._cron = CronScheduleCalculator(tz=_automation_tz)` (or pass `automation_cron` in if that scope has it).
- Line ~2338 `cron=CronScheduleCalculator()` → `cron=automation_cron`.
- Where `CreationFlow(...)` is constructed (search `CreationFlow(`), add `cron=automation_cron`.

If `_automation_tz` is not in scope at a given site, construct it locally there with the same two lines (it's cheap and idempotent).

- [ ] **Step 3: Run the realign on startup**

After the automation repository and `automation_cron` are available during startup wiring, call the realign once. Find where automation wiring completes (near the `AutomationDispatcher(...)` construction, ~line 2331) and add:

```python
        from datetime import UTC, datetime
        from donna.automations.reschedule import recompute_next_runs

        try:
            await recompute_next_runs(
                automation_repository, automation_cron, datetime.now(UTC)
            )
        except Exception:
            log.exception("automation_reschedule_failed")
```

Use whatever the repository variable is named in that scope (search for `AutomationRepository(` to find it).

- [ ] **Step 4: Run the full unit suite**

Run: `uv run pytest tests/unit -q`
Expected: PASS (no regressions).

- [ ] **Step 5: Commit**

```bash
git add src/donna/cli_wiring.py
git commit -m "chore(wiring): tz-aware automation cron, notification policy, startup realign"
```

---

### Task 8: Documentation

**Files:**
- Modify: `config/calendar.yaml`
- Modify: `spec_v3.md`
- Modify: `docs/superpowers/specs/followups.md`

- [ ] **Step 1: Update the calendar.yaml blackout comment**

In `config/calendar.yaml`, replace the comment block above `time_windows` (currently "Blackout (12am–6am) is absolute — no exceptions, not even priority 5.") with:

```yaml
# Blackout (12am–6am) and quiet hours (10pm–12am) are gated PER notification
# type. By default a type respects both windows. Types listed in
# config/notifications.yaml (reminder, automation_alert, automation_failure,
# debug) are EXEMPT and deliver regardless. SMS always stays night-silent.
# All hours are local time (interpreted via timezone above).
```

- [ ] **Step 2: Update spec_v3.md**

In `spec_v3.md`, in §6.9 (automation dispatch) and the notifications section, add a note that (a) automation cron expressions are evaluated in the user's `calendar.yaml` timezone, and (b) blackout/quiet enforcement is per-type via `config/notifications.yaml`, replacing the previous "blackout is absolute" statement. Cite this design doc.

- [ ] **Step 3: Log the deferred follow-up**

Append to `docs/superpowers/specs/followups.md`:

```markdown
## Durable notification queue (deferred 2026-06-10)

The blackout/quiet queue is in-memory (`NotificationService._queue`), flushed
at 6 AM by the reminder loop. Types that respect blackout (overdue, digests,
proactive nudges) can still be lost on a restart between queueing and flush.
Automation alerts no longer use this path (they are blackout-exempt), so this
is low-urgency. Follow-up: back the queue with the DB so restarts don't drop
queued proactive notifications. Ref:
docs/superpowers/specs/2026-06-10-automation-alert-delivery-design.md.
```

- [ ] **Step 4: Commit**

```bash
git add config/calendar.yaml spec_v3.md docs/superpowers/specs/followups.md
git commit -m "docs(automations): document tz-local crons and per-type window policy"
```

---

## Final Verification

- [ ] **Run the full unit suite:** `uv run pytest tests/unit -q` → all pass.
- [ ] **Type + lint gates (CI gates — run before any PR):** `uv run mypy src/donna/automations src/donna/notifications src/donna/config.py` and `uv run ruff check src tests`.
- [ ] **Manual sanity (optional, against a scratch DB):** construct `CronScheduleCalculator(tz=ZoneInfo("America/New_York")).next_run(expression="0 9 * * *", after=datetime.now(UTC))` and confirm the result is 13:00/14:00 UTC, not 09:00.
- [ ] **Confirm the COS automation realigns:** after deploy + startup, `next_run_at` for `019e0507-39cf-720b-9b98-163834d0f6b9` should be `...T13:00:00+00:00` (9 AM EDT), not `...T09:00:00+00:00`.
```
