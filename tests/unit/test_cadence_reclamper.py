"""CadenceReclamper — recomputes active cadence when skill state changes."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from donna.automations.cadence_policy import CadencePolicy
from donna.automations.cadence_reclamper import CadenceReclamper


class _FakeRepo:
    def __init__(self, rows):
        self.rows = rows
        self.updates: list[dict] = []

    async def list_by_capability(self, cap):
        return self.rows

    async def update_active_cadence(self, automation_id, active_cadence_cron, next_run_at):
        self.updates.append(
            {"id": automation_id, "active": active_cadence_cron, "next": next_run_at}
        )


@pytest.mark.asyncio
async def test_reclamp_clamps_to_policy_floor() -> None:
    policy = CadencePolicy(
        intervals={"sandbox": 43200, "trusted": 900},
        paused_states=set(),
    )
    rows = [
        type(
            "R",
            (),
            {
                "id": "a1",
                "schedule": "*/15 * * * *",
                "active_cadence_cron": "0 */12 * * *",
                "capability_name": "product_watch",
                "target_cadence_cron": "*/15 * * * *",
            },
        )(),
    ]
    repo = _FakeRepo(rows)
    scheduler = AsyncMock()
    scheduler.compute_next_run = AsyncMock(return_value=None)
    reclamper = CadenceReclamper(repo=repo, policy=policy, scheduler=scheduler)

    await reclamper.reclamp_for_capability("product_watch", new_state="trusted")

    assert len(repo.updates) == 1
    # target 15min, trusted floor 15min -> active upgrades to user target
    assert repo.updates[0]["active"] == "*/15 * * * *"


@pytest.mark.asyncio
async def test_reclamp_pauses_on_flagged_for_review() -> None:
    policy = CadencePolicy(
        intervals={"sandbox": 43200},
        paused_states={"flagged_for_review"},
    )
    rows = [
        type(
            "R",
            (),
            {
                "id": "a1",
                "schedule": "0 12 * * *",
                "active_cadence_cron": "0 12 * * *",
                "capability_name": "product_watch",
                "target_cadence_cron": "0 12 * * *",
            },
        )(),
    ]
    repo = _FakeRepo(rows)
    scheduler = AsyncMock()
    reclamper = CadenceReclamper(repo=repo, policy=policy, scheduler=scheduler)

    await reclamper.reclamp_for_capability("product_watch", new_state="flagged_for_review")

    assert repo.updates[0]["active"] is None  # NULL = paused


@pytest.mark.asyncio
async def test_reclamp_skips_when_no_change() -> None:
    policy = CadencePolicy(intervals={"trusted": 900}, paused_states=set())
    rows = [
        type(
            "R",
            (),
            {
                "id": "a1",
                "schedule": "*/15 * * * *",
                "active_cadence_cron": "*/15 * * * *",
                "capability_name": "product_watch",
                "target_cadence_cron": "*/15 * * * *",
            },
        )(),
    ]
    repo = _FakeRepo(rows)
    scheduler = AsyncMock()
    reclamper = CadenceReclamper(repo=repo, policy=policy, scheduler=scheduler)

    await reclamper.reclamp_for_capability("product_watch", new_state="trusted")

    assert repo.updates == []  # no-op
