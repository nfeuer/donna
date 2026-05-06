"""Integration: BudgetEscalationView button handlers route through the gate."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from donna.cost.escalation_repository import EscalationRepository
from donna.integrations.discord_views import BudgetEscalationView, _ModeButton

OWNER_ID = 999_999

_SCHEMA = """
CREATE TABLE escalation_request (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL UNIQUE,
    task_id TEXT,
    task_type TEXT NOT NULL,
    estimate_usd REAL NOT NULL,
    daily_remaining_usd REAL NOT NULL,
    offered_modes TEXT NOT NULL,
    resolution TEXT,
    resolved_by TEXT,
    resolved_at TEXT,
    prompt_path TEXT,
    prompt_body TEXT,
    summary TEXT,
    mode TEXT,
    result TEXT,
    validation_result TEXT,
    branch_name TEXT,
    iteration INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    submitted_at TEXT,
    validated_at TEXT,
    priority INTEGER NOT NULL DEFAULT 2,
    delivery_status TEXT,
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    last_delivery_attempt_at TEXT,
    parent_escalation_id INTEGER REFERENCES escalation_request(id),
    -- slice 21 additions
    human_review INTEGER NOT NULL DEFAULT 0,
    target_paths TEXT,
    originating_entity_type TEXT,
    originating_entity_id TEXT,
    base_sha TEXT,
    merged_at TEXT
);
"""


def _make_interaction(user_id: int) -> MagicMock:
    inter = MagicMock()
    inter.user.id = user_id
    inter.response.send_message = AsyncMock()
    inter.response.edit_message = AsyncMock()
    return inter


def _find_button(view: BudgetEscalationView, mode: str) -> _ModeButton:
    for child in view.children:
        if isinstance(child, _ModeButton) and child._mode == mode:
            return child
    raise AssertionError(f"button {mode} not found")


@pytest.fixture
async def repo(tmp_path: Path):
    conn = await aiosqlite.connect(str(tmp_path / "view.db"))
    await conn.executescript(_SCHEMA)
    await conn.commit()
    yield EscalationRepository(conn)
    await conn.close()


@pytest.fixture
async def open_row(repo: EscalationRepository):
    return await repo.create(
        user_id="nick",
        correlation_id="vbtn-1",
        task_id="t1",
        task_type="x",
        estimate_usd=10.0,
        daily_remaining_usd=0.0,
        offered_modes=["pause", "cancel"],
        priority=2,
    )


def _make_gate(repo: EscalationRepository) -> MagicMock:
    gate = MagicMock()

    async def _record(*, correlation_id, mode, owner_user_id, task_id):
        return await repo.resolve(
            (await repo.get_by_correlation(correlation_id)).id,  # type: ignore[union-attr]
            resolution=mode,
            resolved_by="user",
        )

    gate.record_user_resolution = AsyncMock(side_effect=_record)
    return gate


class TestRendering:
    def test_only_pause_and_cancel_render(self) -> None:
        view = BudgetEscalationView(
            correlation_id="x",
            offered_modes=["pause", "cancel"],
            owner_discord_id=OWNER_ID,
            gate=MagicMock(),
        )
        modes = {c._mode for c in view.children if isinstance(c, _ModeButton)}
        assert modes == {"pause", "cancel"}


class TestOwnerCheck:
    async def test_wrong_user_rejected(
        self, repo: EscalationRepository, open_row
    ) -> None:
        gate = _make_gate(repo)
        view = BudgetEscalationView(
            correlation_id="vbtn-1",
            offered_modes=["pause", "cancel"],
            owner_discord_id=OWNER_ID,
            gate=gate,
            task_id="t1",
        )
        button = _find_button(view, "pause")
        inter = _make_interaction(user_id=12345)  # not owner
        await button.callback(inter)
        gate.record_user_resolution.assert_not_called()
        # Row is still open.
        latest = await repo.get(open_row.id)
        assert latest is not None
        assert latest.status == "open"


class TestButtonResolution:
    async def test_pause_resolves_row(
        self, repo: EscalationRepository, open_row
    ) -> None:
        gate = _make_gate(repo)
        view = BudgetEscalationView(
            correlation_id="vbtn-1",
            offered_modes=["pause", "cancel"],
            owner_discord_id=OWNER_ID,
            gate=gate,
            task_id="t1",
        )
        button = _find_button(view, "pause")
        inter = _make_interaction(user_id=OWNER_ID)
        await button.callback(inter)
        gate.record_user_resolution.assert_awaited_once()
        latest = await repo.get(open_row.id)
        assert latest is not None
        assert latest.resolution == "pause"

    async def test_stale_click_returns_already_resolved(
        self, repo: EscalationRepository, open_row
    ) -> None:
        # Pre-resolve the row out from under the view so the button click
        # races with another resolver and loses.
        await repo.resolve(open_row.id, resolution="cancel", resolved_by="user")

        gate = _make_gate(repo)
        view = BudgetEscalationView(
            correlation_id="vbtn-1",
            offered_modes=["pause", "cancel"],
            owner_discord_id=OWNER_ID,
            gate=gate,
            task_id="t1",
        )
        button = _find_button(view, "pause")
        inter = _make_interaction(user_id=OWNER_ID)
        await button.callback(inter)
        # Resolution should still be 'cancel' (the earlier one).
        latest = await repo.get(open_row.id)
        assert latest is not None
        assert latest.resolution == "cancel"
        # Ephemeral 'already resolved' message sent.
        inter.response.send_message.assert_awaited_once()
        args, kwargs = inter.response.send_message.call_args
        assert kwargs.get("ephemeral") is True
        assert "already resolved" in args[0].lower()
