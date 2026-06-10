"""AutomationCreationPath — renders confirmation card, handles approve/cancel/edit."""
from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from donna.automations.creation_flow import AutomationCreationPath
from donna.automations.cron import CronScheduleCalculator
from donna.orchestrator.discord_intent_dispatcher import DraftAutomation


class _FakeRepo:
    def __init__(self):
        self.created = []

    async def create(self, **kwargs):
        self.created.append(kwargs)
        return "auto-1"


@pytest.mark.asyncio
async def test_approve_creates_automation_row() -> None:
    repo = _FakeRepo()
    flow = AutomationCreationPath(repository=repo)
    draft = DraftAutomation(
        user_id="u1",
        capability_name="product_watch",
        inputs={"url": "https://x.com/shirt"},
        schedule_cron="0 12 * * *",
        schedule_human="daily at noon",
        alert_conditions={
            "expression": "triggers_alert == true",
            "channels": ["discord_dm"],
        },
        target_cadence_cron="0 12 * * *",
        active_cadence_cron="0 12 * * *",
    )
    automation_id = await flow.approve(draft, name="watch shirt")
    assert automation_id == "auto-1"
    assert len(repo.created) == 1
    row = repo.created[0]
    assert row["capability_name"] == "product_watch"
    assert row["created_via"] == "discord"
    assert row["schedule"] == "0 12 * * *"


@pytest.mark.asyncio
async def test_approve_twice_is_idempotent() -> None:
    class IdempotentRepo:
        def __init__(self):
            self.calls = 0

        async def create(self, **kwargs):
            self.calls += 1
            if self.calls > 1:
                from donna.automations.repository import AlreadyExistsError

                raise AlreadyExistsError("duplicate")
            return "auto-1"

    repo = IdempotentRepo()
    flow = AutomationCreationPath(repository=repo)
    draft = DraftAutomation(
        user_id="u1",
        capability_name="product_watch",
        inputs={},
        schedule_cron="0 12 * * *",
        schedule_human="daily",
        alert_conditions=None,
        target_cadence_cron="0 12 * * *",
        active_cadence_cron="0 12 * * *",
    )
    id1 = await flow.approve(draft, name="watch")
    id2 = await flow.approve(draft, name="watch")
    assert id1 == "auto-1"
    assert id2 is None  # second attempt returns None


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
    assert row["alert_conditions"] == {
        "field": "triggers_alert", "op": "==", "value": True,
    }


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


@pytest.mark.asyncio
async def test_approve_defaults_alert_channels_to_discord_dm() -> None:
    repo = _FakeRepo()
    flow = AutomationCreationPath(repository=repo)
    draft = DraftAutomation(
        user_id="u1",
        capability_name="product_watch",
        inputs={},
        schedule_cron="0 12 * * *",
        schedule_human="daily",
        alert_conditions=None,
        target_cadence_cron="0 12 * * *",
        active_cadence_cron="0 12 * * *",
    )
    await flow.approve(draft, name="watch")
    row = repo.created[0]
    assert row["alert_channels"] == ["discord_dm"]


@pytest.mark.asyncio
async def test_approve_queues_skill_candidate_when_flagged() -> None:
    repo = _FakeRepo()
    candidate_reports: list[dict] = []

    async def _fake_writer(
        capability_name, task_pattern_hash,
        expected_savings_usd, volume_30d, variance_score,
        reasoning=None,
    ):
        candidate_reports.append({
            "capability_name": capability_name,
            "reasoning": reasoning,
        })
        return "cand-1"

    flow = AutomationCreationPath(
        repository=repo,
        skill_candidate_writer=_fake_writer,
    )
    draft = DraftAutomation(
        user_id="u1",
        capability_name=None,
        inputs={"time": "3pm", "message": "call dentist"},
        schedule_cron="0 15 * * *",
        schedule_human="daily at 3pm",
        alert_conditions=None,
        target_cadence_cron="0 15 * * *",
        active_cadence_cron="0 15 * * *",
        skill_candidate=True,
        skill_candidate_reasoning="Reminders are a common pattern worth a dedicated skill",
    )
    await flow.approve(draft, name="dentist reminder")
    assert len(candidate_reports) == 1
    assert candidate_reports[0]["capability_name"] == "claude_native"
    assert "Reminders" in candidate_reports[0]["reasoning"]


@pytest.mark.asyncio
async def test_approve_does_not_queue_candidate_when_not_flagged() -> None:
    repo = _FakeRepo()
    candidate_reports: list[dict] = []

    async def _fake_writer(
        capability_name, task_pattern_hash,
        expected_savings_usd, volume_30d, variance_score,
        reasoning=None,
    ):
        candidate_reports.append({})
        return "cand-1"

    flow = AutomationCreationPath(
        repository=repo,
        skill_candidate_writer=_fake_writer,
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
        skill_candidate=False,
    )
    await flow.approve(draft, name="watch shirt")
    assert len(candidate_reports) == 0


@pytest.mark.asyncio
async def test_creation_uses_injected_tz_cron() -> None:
    """next_run_at must reflect the injected timezone, not bare UTC."""
    repo = _FakeRepo()
    flow = AutomationCreationPath(
        repository=repo,
        cron=CronScheduleCalculator(tz=ZoneInfo("America/New_York")),
    )
    draft = DraftAutomation(
        user_id="u1",
        capability_name="product_watch",
        inputs={"url": "https://x.com/shirt"},
        schedule_cron="0 9 * * *",
        schedule_human="daily at 9am",
        alert_conditions=None,
        target_cadence_cron="0 9 * * *",
        active_cadence_cron="0 9 * * *",
    )
    await flow.approve(draft, name="morning watch")
    row = repo.created[0]
    next_run_at = row["next_run_at"]
    assert next_run_at is not None
    # 9 AM Eastern = 13:00 or 14:00 UTC depending on DST; never 09:00 UTC
    assert next_run_at.hour in (13, 14), (
        f"expected 13 or 14 UTC (9 AM Eastern), got {next_run_at.hour:02d}:00 UTC"
    )
