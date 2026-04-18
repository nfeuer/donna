"""AutomationCreationPath — renders confirmation card, handles approve/cancel/edit."""
from __future__ import annotations

import pytest

from donna.automations.creation_flow import AutomationCreationPath
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
