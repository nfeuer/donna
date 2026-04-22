"""Intent dispatcher applies cadence policy to drafts."""
from __future__ import annotations

import pytest

from donna.agents.challenger_agent import ChallengerMatchResult
from donna.automations.cadence_policy import CadencePolicy
from donna.capabilities.models import CapabilityRow
from donna.orchestrator.discord_intent_dispatcher import DiscordIntentDispatcher


class _FakeChallenger:
    def __init__(self, result): self._r = result
    async def match_and_extract(self, msg, uid): return self._r


class _FakeLifecycle:
    def __init__(self, state): self._state = state
    async def current_state(self, cap_name): return self._state


class _FakePendingDrafts:
    def set(self, d): pass
    def get_by_thread(self, tid): return None
    def discard(self, tid): pass


class _FakeTasksDb:
    async def insert_task(self, **kwargs): return "t1"


@pytest.mark.asyncio
async def test_draft_uses_policy_clamp_for_sandbox_capability(tmp_path) -> None:
    cfg = tmp_path / "automations.yaml"
    cfg.write_text(
        "cadence_policy:\n"
        "  sandbox: {min_interval_seconds: 43200}\n"
        "  trusted: {min_interval_seconds: 900}\n"
    )
    policy = CadencePolicy.load(cfg)

    cap = CapabilityRow(
        id="c1", name="product_watch", description="",
        input_schema={}, trigger_type="on_schedule",
        default_output_shape={}, status="active",
        embedding=None, created_at=None,
        created_by="system", notes=None,
    )
    result = ChallengerMatchResult(
        status="ready", intent_kind="automation", capability=cap,
        schedule={"cron": "*/15 * * * *", "human_readable": "every 15 min"},
        extracted_inputs={"url": "x"}, confidence=0.9,
    )

    dispatcher = DiscordIntentDispatcher(
        challenger=_FakeChallenger(result),
        novelty_judge=None,
        pending_drafts=_FakePendingDrafts(),
        tasks_db=_FakeTasksDb(),
        cadence_policy=policy,
        lifecycle_lookup=_FakeLifecycle("sandbox"),
    )

    from dataclasses import dataclass
    @dataclass
    class M:
        content: str
        author_id: str = "u1"
        thread_id: int | None = None

    out = await dispatcher.dispatch(M(content="watch x every 15 min"))
    assert out.kind == "automation_confirmation_needed"
    assert out.draft_automation.target_cadence_cron == "*/15 * * * *"
    assert out.draft_automation.active_cadence_cron == "0 */12 * * *"


@pytest.mark.asyncio
async def test_draft_no_clamp_for_trusted_capability(tmp_path) -> None:
    cfg = tmp_path / "automations.yaml"
    cfg.write_text(
        "cadence_policy:\n"
        "  sandbox: {min_interval_seconds: 43200}\n"
        "  trusted: {min_interval_seconds: 900}\n"
    )
    policy = CadencePolicy.load(cfg)

    cap = CapabilityRow(
        id="c1", name="product_watch", description="",
        input_schema={}, trigger_type="on_schedule",
        default_output_shape={}, status="active",
        embedding=None, created_at=None,
        created_by="system", notes=None,
    )
    result = ChallengerMatchResult(
        status="ready", intent_kind="automation", capability=cap,
        schedule={"cron": "*/15 * * * *", "human_readable": "every 15 min"},
        extracted_inputs={"url": "x"}, confidence=0.9,
    )
    dispatcher = DiscordIntentDispatcher(
        challenger=_FakeChallenger(result),
        novelty_judge=None,
        pending_drafts=_FakePendingDrafts(),
        tasks_db=_FakeTasksDb(),
        cadence_policy=policy,
        lifecycle_lookup=_FakeLifecycle("trusted"),
    )
    from dataclasses import dataclass
    @dataclass
    class M:
        content: str
        author_id: str = "u1"
        thread_id: int | None = None

    out = await dispatcher.dispatch(M(content="watch x every 15 min"))
    assert out.draft_automation.active_cadence_cron == "*/15 * * * *"  # no clamp
