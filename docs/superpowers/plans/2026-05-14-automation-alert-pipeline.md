# Automation Alert Pipeline Improvements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make automation alerts reliable by giving the LLM output field context, extracting notification preferences, applying default alert conditions, and routing alerts to the right channels.

**Architecture:** Config-driven defaults in capabilities.yaml (no DB migration). LLM extracts `notification_channels` alongside existing fields. CreationPath merges LLM alerts with capability defaults. Dispatcher routes to the right NotificationService method per channel.

**Tech Stack:** Python, Jinja2 templates, JSON Schema, YAML config, pytest

---

### Task 1: Add `default_alert_conditions` to capabilities.yaml

**Files:**
- Modify: `config/capabilities.yaml`

- [ ] **Step 1: Add `default_alert_conditions` to product_watch**

```yaml
  - name: product_watch
    # ... existing fields unchanged ...
    default_alert_conditions:
      field: triggers_alert
      op: "=="
      value: true
```

Add this after the existing `default_output_shape` block (after line 34 in capabilities.yaml).

- [ ] **Step 2: Add `default_alert_conditions` to news_check**

```yaml
  - name: news_check
    # ... existing fields unchanged ...
    default_alert_conditions:
      field: triggers_alert
      op: "=="
      value: true
```

Add after the `default_output_shape` block for news_check (after line 67).

- [ ] **Step 3: Add `default_alert_conditions` to email_triage**

```yaml
  - name: email_triage
    # ... existing fields unchanged ...
    default_alert_conditions:
      field: triggers_alert
      op: "=="
      value: true
```

Add after the `default_output_shape` block for email_triage (after line 98).

- [ ] **Step 4: Commit**

```bash
git add config/capabilities.yaml
git commit -m "feat(config): add default_alert_conditions to on-schedule capabilities

Each on-schedule capability now declares the alert DSL expression that
fires when the LLM doesn't provide one. All three use triggers_alert==true
since each skill computes that field internally.

Ref: spec_v3.md §4.6"
```

---

### Task 2: Render output fields in challenger prompt

**Files:**
- Modify: `prompts/challenger_parse.md`

The CapabilityRow already carries `default_output_shape` (a JSON schema dict with `properties`). We render its property names and types so the LLM knows what fields exist to write alert conditions against.

- [ ] **Step 1: Add output fields rendering to the capability loop**

In `prompts/challenger_parse.md`, the capability loop currently (lines 7-10) renders name, description, and input schema. Add output field rendering:

```jinja2
{% for cap in capabilities %}
- **{{ cap.name }}**: {{ cap.description }}
  Input schema: {{ (cap.input_schema.get('properties', {}) if cap.input_schema else {}) | tojson }}
{% if cap.default_output_shape and cap.default_output_shape.get('properties') %}
  Output fields: {% for fname, fschema in cap.default_output_shape['properties'].items() %}{{ fname }} ({{ fschema.get('type', 'any') }}){% if not loop.last %}, {% endif %}{% endfor %}

{% endif %}
{% endfor %}
```

This renders e.g.: `Output fields: ok (boolean), price_usd (['number', 'null']), in_stock (boolean), size_available (boolean), triggers_alert (boolean), title (string)`

- [ ] **Step 2: Update alert_conditions instructions to reference output fields**

Replace the current alert_conditions bullet (lines 25-31) with:

```markdown
- `alert_conditions`: alert DSL describing when the automation should notify on skill output.
  Use the **Output fields** listed above for each capability to decide what to alert on.
  For monitoring capabilities (product_watch, news_check, email_triage), the skill computes
  a `triggers_alert` boolean — use `{"field": "triggers_alert", "op": "==", "value": true}`
  as the default when the user wants alerts but doesn't specify a condition.
  - Terminal: `{"field": "<dotted.path>", "op": "<op>", "value": <any>}` where
    `op` is one of `==`, `!=`, `<`, `<=`, `>`, `>=`, `contains`, `exists`.
  - Composite: `{"all_of": [<node>, <node>, ...]}` or `{"any_of": [<node>, ...]}` — nodes may
    themselves be terminal or composite.
  - Leave `null` only when intent_kind is `task`, `question`, or `chat`.
    For `automation` intents, ALWAYS set alert_conditions — at minimum use
    `{"field": "triggers_alert", "op": "==", "value": true}`.
  - Do NOT emit `{expression, channels}` — that shape is ignored by the alert evaluator.
```

- [ ] **Step 3: Commit**

```bash
git add prompts/challenger_parse.md
git commit -m "feat(prompt): render output fields and strengthen alert_conditions guidance

The challenger prompt now shows each capability's output field names and
types, so the LLM can write informed alert conditions. The instructions
now tell the LLM to always set alert_conditions for automation intents.

Ref: spec_v3.md §4.2, §4.6"
```

---

### Task 3: Add `notification_channels` to challenger parse schema and prompt

**Files:**
- Modify: `schemas/challenger_parse.json`
- Modify: `prompts/challenger_parse.md`
- Modify: `src/donna/agents/challenger_agent.py` (ChallengerMatchResult + _build_result_from_parse)
- Modify: `src/donna/agents/claude_novelty_judge.py` (NoveltyVerdict)
- Modify: `src/donna/orchestrator/discord_intent_dispatcher.py` (DraftAutomation + both builders)
- Test: `tests/unit/test_challenger_parse_prompt_schema_parity.py` (if it validates field parity)

- [ ] **Step 1: Add `notification_channels` to JSON schema**

In `schemas/challenger_parse.json`, add after the `low_quality_signals` property:

```json
"notification_channels": {
  "type": ["array", "null"],
  "items": {"enum": ["discord_dm", "sms", "email", "discord_channel"]},
  "description": "User's preferred alert delivery channels extracted from phrases like 'text me', 'DM me', 'email me'. Null = default (discord_dm)."
}
```

- [ ] **Step 2: Add `notification_channels` instructions to prompt**

In `prompts/challenger_parse.md`, add a new bullet after the `alert_conditions` bullet:

```markdown
- `notification_channels`: array of preferred delivery channels the user wants for alerts.
  Extract from phrases like "text me" → `["sms"]`, "DM me" → `["discord_dm"]`,
  "send me an email" → `["email"]`, "post in the channel" → `["discord_channel"]`.
  Multiple channels are allowed (e.g. "DM me and text me" → `["discord_dm", "sms"]`).
  Null when the user doesn't specify a preference (system default: discord_dm).
```

- [ ] **Step 3: Add `notification_channels` to ChallengerMatchResult**

In `src/donna/agents/challenger_agent.py`, add to the `ChallengerMatchResult` dataclass (after `low_quality_signals`):

```python
notification_channels: list[str] | None = None
```

- [ ] **Step 4: Populate notification_channels in _build_result_from_parse**

In `src/donna/agents/challenger_agent.py`, in the `_build_result_from_parse` method, add to the `return ChallengerMatchResult(...)` call (after `low_quality_signals`):

```python
notification_channels=parse.get("notification_channels"),
```

- [ ] **Step 5: Add `notification_channels` to NoveltyVerdict**

In `src/donna/agents/claude_novelty_judge.py`, add to `NoveltyVerdict` dataclass (after `clarifying_question`):

```python
notification_channels: list[str] | None = None
```

And in the `return NoveltyVerdict(...)` call, add:

```python
notification_channels=parsed.get("notification_channels"),
```

- [ ] **Step 6: Add `notification_channels` to DraftAutomation**

In `src/donna/orchestrator/discord_intent_dispatcher.py`, add to `DraftAutomation` dataclass (after `skill_candidate_reasoning`):

```python
notification_channels: list[str] | None = None
```

- [ ] **Step 7: Populate notification_channels in _build_automation_draft**

In `src/donna/orchestrator/discord_intent_dispatcher.py`, in `_build_automation_draft` (line 264), add to the `DraftAutomation(...)` call:

```python
notification_channels=result.notification_channels,
```

- [ ] **Step 8: Populate notification_channels in _build_automation_draft_from_verdict**

In `src/donna/orchestrator/discord_intent_dispatcher.py`, in `_build_automation_draft_from_verdict` (line 285), add to the `DraftAutomation(...)` call:

```python
notification_channels=verdict.notification_channels,
```

- [ ] **Step 9: Run tests**

```bash
pytest tests/unit/test_challenger_parse_prompt_schema_parity.py tests/unit/test_challenger_match_and_extract.py tests/unit/test_discord_intent_dispatcher.py -v
```

Fix any failures.

- [ ] **Step 10: Commit**

```bash
git add schemas/challenger_parse.json prompts/challenger_parse.md \
  src/donna/agents/challenger_agent.py src/donna/agents/claude_novelty_judge.py \
  src/donna/orchestrator/discord_intent_dispatcher.py
git commit -m "feat(challenger): extract notification_channels from user messages

New field in challenger parse schema lets the LLM extract delivery
preferences ('text me' → sms, 'DM me' → discord_dm). Threaded through
ChallengerMatchResult, NoveltyVerdict, and DraftAutomation.

Ref: spec_v3.md §4.2, §5.3"
```

---

### Task 4: Create default_alert_conditions lookup

**Files:**
- Create: `src/donna/capabilities/default_alerts_lookup.py`
- Test: `tests/unit/test_default_alerts_lookup.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_default_alerts_lookup.py
import pytest
from pathlib import Path
import yaml

from donna.capabilities.default_alerts_lookup import CapabilityDefaultAlertsLookup


@pytest.fixture
def yaml_path(tmp_path: Path) -> Path:
    p = tmp_path / "capabilities.yaml"
    p.write_text(yaml.dump({
        "capabilities": [
            {
                "name": "product_watch",
                "description": "watch",
                "trigger_type": "on_schedule",
                "default_alert_conditions": {
                    "field": "triggers_alert",
                    "op": "==",
                    "value": True,
                },
            },
            {
                "name": "no_alerts_cap",
                "description": "none",
                "trigger_type": "ad_hoc",
            },
        ]
    }))
    return p


def test_lookup_returns_conditions(yaml_path: Path) -> None:
    lookup = CapabilityDefaultAlertsLookup(yaml_path)
    result = lookup.get("product_watch")
    assert result == {"field": "triggers_alert", "op": "==", "value": True}


def test_lookup_returns_none_for_missing(yaml_path: Path) -> None:
    lookup = CapabilityDefaultAlertsLookup(yaml_path)
    assert lookup.get("no_alerts_cap") is None


def test_lookup_returns_none_for_unknown(yaml_path: Path) -> None:
    lookup = CapabilityDefaultAlertsLookup(yaml_path)
    assert lookup.get("nonexistent") is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/test_default_alerts_lookup.py -v
```

Expected: ImportError — module doesn't exist yet.

- [ ] **Step 3: Write the implementation**

```python
# src/donna/capabilities/default_alerts_lookup.py
"""Load default_alert_conditions from capabilities.yaml by capability name."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class CapabilityDefaultAlertsLookup:
    def __init__(self, yaml_path: Path) -> None:
        with open(yaml_path) as f:
            data = yaml.safe_load(f) or {}
        self._defaults: dict[str, dict[str, Any]] = {}
        for entry in data.get("capabilities", []):
            name = entry.get("name")
            conditions = entry.get("default_alert_conditions")
            if name and conditions:
                self._defaults[name] = conditions

    def get(self, capability_name: str) -> dict[str, Any] | None:
        return self._defaults.get(capability_name)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/unit/test_default_alerts_lookup.py -v
```

Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/donna/capabilities/default_alerts_lookup.py tests/unit/test_default_alerts_lookup.py
git commit -m "feat(capabilities): YAML-based default_alert_conditions lookup

Loads default_alert_conditions from capabilities.yaml at init time.
No DB migration needed — these are config-only defaults.

Ref: spec_v3.md §4.6"
```

---

### Task 5: Fix AutomationCreationPath to merge defaults and honor notification_channels

**Files:**
- Modify: `src/donna/automations/creation_flow.py`
- Modify: `tests/unit/test_automation_creation_flow.py`

- [ ] **Step 1: Write the failing test — alert defaults merge**

Add to `tests/unit/test_automation_creation_flow.py`:

```python
@pytest.mark.asyncio
async def test_approve_merges_default_alert_conditions_when_draft_has_none() -> None:
    repo = _FakeRepo()

    async def _default_alerts(name: str) -> dict | None:
        if name == "product_watch":
            return {"field": "triggers_alert", "op": "==", "value": True}
        return None

    flow = AutomationCreationPath(
        repository=repo,
        capability_default_alerts_lookup=_default_alerts,
    )
    draft = DraftAutomation(
        user_id="u1",
        capability_name="product_watch",
        inputs={"url": "https://x.com/shirt"},
        schedule_cron="0 12 * * *",
        schedule_human="daily at noon",
        alert_conditions=None,
        target_cadence_cron="0 12 * * *",
        active_cadence_cron="0 12 * * *",
    )
    await flow.approve(draft, name="watch shirt")
    row = repo.created[0]
    assert row["alert_conditions"] == {"field": "triggers_alert", "op": "==", "value": True}
```

- [ ] **Step 2: Write the failing test — notification_channels passthrough**

Add to `tests/unit/test_automation_creation_flow.py`:

```python
@pytest.mark.asyncio
async def test_approve_populates_alert_channels_from_notification_channels() -> None:
    repo = _FakeRepo()
    flow = AutomationCreationPath(repository=repo)
    draft = DraftAutomation(
        user_id="u1",
        capability_name="product_watch",
        inputs={"url": "https://x.com/shirt"},
        schedule_cron="0 12 * * *",
        schedule_human="daily at noon",
        alert_conditions={"field": "triggers_alert", "op": "==", "value": True},
        target_cadence_cron="0 12 * * *",
        active_cadence_cron="0 12 * * *",
        notification_channels=["sms", "discord_dm"],
    )
    await flow.approve(draft, name="watch shirt")
    row = repo.created[0]
    assert row["alert_channels"] == ["sms", "discord_dm"]
```

- [ ] **Step 3: Write the failing test — LLM-provided alert_conditions are preserved**

```python
@pytest.mark.asyncio
async def test_approve_preserves_llm_alert_conditions_over_defaults() -> None:
    repo = _FakeRepo()

    async def _default_alerts(name: str) -> dict | None:
        return {"field": "triggers_alert", "op": "==", "value": True}

    flow = AutomationCreationPath(
        repository=repo,
        capability_default_alerts_lookup=_default_alerts,
    )
    llm_conditions = {"field": "price_usd", "op": "<=", "value": 500}
    draft = DraftAutomation(
        user_id="u1",
        capability_name="product_watch",
        inputs={"url": "https://x.com/shirt"},
        schedule_cron="0 12 * * *",
        schedule_human="daily at noon",
        alert_conditions=llm_conditions,
        target_cadence_cron="0 12 * * *",
        active_cadence_cron="0 12 * * *",
    )
    await flow.approve(draft, name="watch shirt")
    row = repo.created[0]
    assert row["alert_conditions"] == llm_conditions
```

- [ ] **Step 4: Run tests to verify they fail**

```bash
pytest tests/unit/test_automation_creation_flow.py -v
```

Expected: 3 new tests FAIL (missing parameter, missing field).

- [ ] **Step 5: Implement the changes in creation_flow.py**

In `src/donna/automations/creation_flow.py`:

Add type alias after existing ones (line 36):

```python
CapabilityDefaultAlertsLookup = Callable[[str], Awaitable[dict[str, Any] | None]]
```

Add parameter to `__init__`:

```python
capability_default_alerts_lookup: CapabilityDefaultAlertsLookup | None = None,
```

Store it:

```python
self._capability_default_alerts_lookup = capability_default_alerts_lookup
```

In `approve()`, replace line 109 (`alert_conditions=draft.alert_conditions or {},`) with:

```python
alert_conditions = draft.alert_conditions
if not alert_conditions and self._capability_default_alerts_lookup is not None and draft.capability_name:
    try:
        defaults = await self._capability_default_alerts_lookup(draft.capability_name)
        if defaults:
            alert_conditions = defaults
    except Exception:
        logger.exception("capability_default_alerts_lookup_failed")
if not alert_conditions:
    alert_conditions = {}
```

Replace line 110 (`alert_channels=["discord_dm"],`) with:

```python
alert_channels = getattr(draft, "notification_channels", None) or ["discord_dm"]
```

Then use `alert_conditions` and `alert_channels` in the `create()` call.

- [ ] **Step 6: Run tests to verify they pass**

```bash
pytest tests/unit/test_automation_creation_flow.py -v
```

Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/donna/automations/creation_flow.py tests/unit/test_automation_creation_flow.py
git commit -m "feat(creation): merge default alert_conditions, honor notification_channels

AutomationCreationPath.approve() now falls back to capability-level
default_alert_conditions when the LLM returns null. alert_channels
are populated from the draft's notification_channels instead of
hardcoding discord_dm.

Ref: spec_v3.md §4.6, §5.3"
```

---

### Task 6: Fix AutomationDispatcher to route alerts by channel

**Files:**
- Modify: `src/donna/automations/dispatcher.py`
- Modify: `tests/unit/test_automation_dispatcher.py`

- [ ] **Step 1: Write the failing test — DM dispatch**

Add to `tests/unit/test_automation_dispatcher.py`:

```python
@pytest.mark.asyncio
async def test_alert_dispatches_to_discord_dm(db):
    auto_id, auto = await _seed_automation(
        db,
        alert_conditions={"field": "triggers_alert", "op": "==", "value": True},
    )
    # Update alert_channels to discord_dm
    await db.execute(
        "UPDATE automation SET alert_channels = ? WHERE id = ?",
        ('["discord_dm"]', auto_id),
    )
    await db.commit()
    auto = await AutomationRepository(db).get(auto_id)

    notifier = AsyncMock()
    notifier.dispatch_dm = AsyncMock(return_value=True)
    dispatcher = _make_dispatcher(db, notifier=notifier)

    output = {"triggers_alert": True, "price_usd": 450, "title": "Test"}
    await dispatcher.dispatch_one(auto, output=output)

    notifier.dispatch_dm.assert_called_once()
    call_kwargs = notifier.dispatch_dm.call_args
    assert call_kwargs.kwargs.get("notification_type") == "automation_alert" or \
           call_kwargs[1].get("notification_type") == "automation_alert"
```

- [ ] **Step 2: Write the failing test — SMS dispatch**

```python
@pytest.mark.asyncio
async def test_alert_dispatches_to_sms(db):
    auto_id, auto = await _seed_automation(
        db,
        alert_conditions={"field": "triggers_alert", "op": "==", "value": True},
    )
    await db.execute(
        "UPDATE automation SET alert_channels = ? WHERE id = ?",
        ('["sms"]', auto_id),
    )
    await db.commit()
    auto = await AutomationRepository(db).get(auto_id)

    notifier = AsyncMock()
    notifier.dispatch_sms = AsyncMock(return_value=True)
    dispatcher = _make_dispatcher(db, notifier=notifier)

    output = {"triggers_alert": True, "price_usd": 450, "title": "Test"}
    await dispatcher.dispatch_one(auto, output=output)

    notifier.dispatch_sms.assert_called_once()
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
pytest tests/unit/test_automation_dispatcher.py -v -k "test_alert_dispatches"
```

Expected: FAIL — dispatcher still calls `dispatch(channel=CHANNEL_TASKS)`.

- [ ] **Step 4: Implement multi-channel alert dispatch**

In `src/donna/automations/dispatcher.py`, add imports at the top (alongside existing NOTIF_ imports):

```python
import os
```

Replace the alert dispatch block (lines 248-258) with:

```python
alert_content = self._render_alert_content(automation, output)
try:
    if self._notifier is not None:
        channels = automation.alert_channels or ["discord_dm"]
        for ch in channels:
            await self._dispatch_alert_to_channel(
                ch, automation, alert_content,
            )
        alert_sent = True
except Exception:
    logger.exception(
        "automation_alert_dispatch_failed",
        automation_id=automation.id,
    )
```

Add a new method `_dispatch_alert_to_channel`:

```python
async def _dispatch_alert_to_channel(
    self,
    channel: str,
    automation: AutomationRow,
    content: str,
) -> None:
    if channel == "discord_dm":
        await self._notifier.dispatch_dm(
            discord_id=automation.user_id,
            notification_type=NOTIF_AUTOMATION_ALERT,
            content=content,
            priority=3,
        )
    elif channel == "sms":
        phone = os.environ.get("DONNA_USER_PHONE", "")
        if phone:
            await self._notifier.dispatch_sms(
                body=content, to=phone, priority=3,
            )
        else:
            logger.warning(
                "automation_alert_sms_no_phone",
                automation_id=automation.id,
            )
    elif channel == "email":
        email = os.environ.get("DONNA_USER_EMAIL", "")
        if email:
            await self._notifier.dispatch_email(
                to=email,
                subject=f"Donna Alert: {automation.name}",
                body=content,
                priority=3,
            )
        else:
            logger.warning(
                "automation_alert_email_no_address",
                automation_id=automation.id,
            )
    elif channel == "discord_channel":
        await self._notifier.dispatch(
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
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/unit/test_automation_dispatcher.py -v
```

Expected: All tests PASS (existing tests should still pass since `alert_channels=["discord"]` in old seed data won't match any known channel — falls to `else` branch and logs warning — but alert_sent is still True since it didn't raise. Check and fix if needed).

- [ ] **Step 6: Commit**

```bash
git add src/donna/automations/dispatcher.py tests/unit/test_automation_dispatcher.py
git commit -m "feat(dispatcher): route automation alerts by alert_channels

Dispatcher now reads alert_channels from the automation row and calls
the appropriate NotificationService method for each channel: dispatch_dm,
dispatch_sms, dispatch_email, or dispatch (channel post).

Ref: spec_v3.md §5.3"
```

---

### Task 7: Wire default_alerts_lookup into cli_wiring.py

**Files:**
- Modify: `src/donna/cli_wiring.py`
- Modify: `src/donna/integrations/discord_bot.py`

- [ ] **Step 1: Add default alerts lookup wiring in cli_wiring.py**

In `src/donna/cli_wiring.py`, after the input schema lookup wiring (around line 2392), add:

```python
# Wire default_alert_conditions lookup into AutomationCreationPath.
from donna.capabilities.default_alerts_lookup import CapabilityDefaultAlertsLookup

_caps_yaml = ctx.config_dir / "capabilities.yaml"
if _caps_yaml.exists():
    _default_alerts_lookup = CapabilityDefaultAlertsLookup(_caps_yaml)
    ctx.bot._automation_default_alerts_lookup = _default_alerts_lookup.get
```

Note: `CapabilityDefaultAlertsLookup.get` is synchronous. The creation_flow expects an async callable. We need to wrap it:

```python
async def _async_default_alerts(name: str) -> dict | None:
    return _default_alerts_lookup.get(name)
ctx.bot._automation_default_alerts_lookup = _async_default_alerts
```

- [ ] **Step 2: Pass the lookup to AutomationCreationPath in discord_bot.py**

In `src/donna/integrations/discord_bot.py`, in `_approve_automation_draft` (line 724), add to the `AutomationCreationPath(...)` constructor:

```python
capability_default_alerts_lookup=getattr(self, "_automation_default_alerts_lookup", None),
```

- [ ] **Step 3: Run the full test suite**

```bash
pytest tests/unit/ -v --tb=short 2>&1 | tail -40
```

Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/donna/cli_wiring.py src/donna/integrations/discord_bot.py
git commit -m "feat(wiring): connect default_alerts_lookup to creation path

cli_wiring loads CapabilityDefaultAlertsLookup from capabilities.yaml
and passes it through the bot to AutomationCreationPath. Single-user
system reads DONNA_USER_PHONE from env for SMS routing.

Ref: spec_v3.md §4.6, §5.3"
```

---

### Task 8: Run full test suite and verify

**Files:** None (verification only)

- [ ] **Step 1: Run all tests**

```bash
pytest tests/ -v --tb=short 2>&1 | tail -60
```

- [ ] **Step 2: Verify challenger prompt renders output fields**

Quick manual check — render the prompt template and verify output fields appear:

```bash
python -c "
from jinja2 import Environment, FileSystemLoader
from types import SimpleNamespace

env = Environment(loader=FileSystemLoader('prompts'))
t = env.get_template('challenger_parse.md')

caps = [SimpleNamespace(
    name='product_watch',
    description='Monitor a product',
    input_schema={'properties': {'url': {'type': 'string'}}},
    default_output_shape={'properties': {
        'ok': {'type': 'boolean'},
        'triggers_alert': {'type': 'boolean'},
        'price_usd': {'type': ['number', 'null']},
    }},
)]

print(t.render(capabilities=caps, user_message='test', current_date_iso='2026-05-14'))
"
```

Expected: Output shows `Output fields: ok (boolean), triggers_alert (boolean), price_usd (['number', 'null'])`

- [ ] **Step 3: Verify alert_conditions flow end-to-end concept**

```bash
python -c "
from donna.capabilities.default_alerts_lookup import CapabilityDefaultAlertsLookup
from pathlib import Path
lookup = CapabilityDefaultAlertsLookup(Path('config/capabilities.yaml'))
print('product_watch defaults:', lookup.get('product_watch'))
print('generate_digest defaults:', lookup.get('generate_digest'))
"
```

Expected: product_watch returns `{"field": "triggers_alert", "op": "==", "value": True}`, generate_digest returns `None`.
