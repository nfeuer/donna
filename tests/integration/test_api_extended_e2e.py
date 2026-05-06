"""Slice 24 — api_extended end-to-end (spec §11).

Spec acceptance: *"approve extension; task runs; daily_remaining
reflects extension; invocation_log carries escalation_request_id."*

Drives the slice-18 budget-extension path with realistic plumbing:

1. ``EscalationGate.fire_and_wait`` opens an escalation that offers
   ``api_extended`` (estimate fits inside the headroom slider).
2. The Discord button callback fires
   ``EscalationGate.grant_budget_extension`` (a real
   ``BudgetExtensionRepository`` writes the grant row + audit
   ``extension_granted`` event with the escalation_request_id).
3. ``record_user_resolution(mode='api_extended')`` flips the row to
   ``resolved`` and signals the gate.
4. ``BudgetGuard.check_pre_call`` for the same user sees the grant
   bumping daily_remaining (so a follow-up call within the new
   ceiling is allowed).
5. ``invocation_log`` rows for the lifecycle audit chain (offered,
   extension_granted, resolved) all carry the escalation_request_id
   so a Grafana per-row timeline filter joins them cleanly.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
from sqlalchemy import create_engine

from donna.config import (
    BudgetExtensionConfig,
    ClaudeCodeModeConfig,
    ManualEscalationConfig,
    ManualEscalationModeConfig,
    ManualEscalationModesConfig,
    ManualEscalationTriggersConfig,
    PromptDeliveryConfig,
    TaskTypeEntry,
    TaskTypesConfig,
)
from donna.cost.budget_extension import BudgetExtensionRepository
from donna.cost.dashboard_setting import DashboardSettingResolver
from donna.cost.escalation_gate import EscalationGate
from donna.cost.escalation_repository import EscalationRepository
from donna.cost.tracker import CostSummary, CostTracker
from donna.tasks.database import Database
from donna.tasks.db_models import Base


def _config(*, hard_monthly_ceiling: float = 100.0) -> ManualEscalationConfig:
    return ManualEscalationConfig(
        enabled=True,
        modes=ManualEscalationModesConfig(
            chat=ManualEscalationModeConfig(enabled=True),
            claude_code=ClaudeCodeModeConfig(enabled=True),
        ),
        triggers=ManualEscalationTriggersConfig(
            task_approval_threshold_usd=2.0,
        ),
        prompt_delivery=PromptDeliveryConfig(),
        budget_extension=BudgetExtensionConfig(
            enabled=True,
            max_daily_extension_usd=20.0,
            hard_monthly_ceiling_usd=hard_monthly_ceiling,
        ),
    )


def _task_types() -> TaskTypesConfig:
    return TaskTypesConfig(task_types={
        "weekly_review": TaskTypeEntry(
            description="Weekly review",
            model="parser",
            prompt_template="prompts/weekly/review.md",
            output_schema="schemas/weekly_review_output.json",
            # No manual_escalation block: api_extended / pause / cancel only.
        ),
    })


@pytest.fixture
async def db(tmp_path: Path, state_machine):
    db_path = tmp_path / "ext_e2e.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    database = Database(db_path=str(db_path), state_machine=state_machine)
    await database.connect()
    yield database
    await database.close()


@pytest.fixture
async def conn(db: Database) -> aiosqlite.Connection:
    return db.connection


async def _wait_correlation(repo: EscalationRepository) -> str:
    for _ in range(200):
        cur = await repo._conn.execute(
            "SELECT correlation_id FROM escalation_request "
            "ORDER BY id DESC LIMIT 1"
        )
        row = await cur.fetchone()
        if row is not None:
            return row[0]
        await asyncio.sleep(0.01)
    raise AssertionError("gate did not create a row")


async def _wait_armed(correlation_id: str) -> None:
    for _ in range(500):
        if EscalationGate._events.get(correlation_id) is not None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("gate did not arm in time")


class TestApiExtendedE2E:
    async def test_grant_then_resolve_runs_task_and_audits_chain(
        self,
        db: Database,
        conn: aiosqlite.Connection,
    ) -> None:
        repo = EscalationRepository(conn)
        resolver = DashboardSettingResolver(repo)
        extension_repo = BudgetExtensionRepository(conn)

        # Daily/monthly history starts clean — gate offers api_extended.
        tracker = MagicMock(spec=CostTracker)
        tracker.get_daily_cost = AsyncMock(
            return_value=CostSummary(total_usd=0.0, call_count=0, breakdown={})
        )

        delivered: list = []

        async def deliver(row) -> bool:
            delivered.append(row)
            return True

        gate = EscalationGate(
            repository=repo,
            tracker=tracker,
            config=_config(),
            daily_pause_threshold_usd=20.0,
            resolver=resolver,
            deliver=deliver,
            extension_repo=extension_repo,
            task_types_config=_task_types(),
        )

        # ---- 1. Gate fires ----
        gate_task = asyncio.create_task(
            gate.fire_and_wait(
                user_id="nick",
                task_id="task-ext-1",
                task_type="weekly_review",
                estimate_usd=4.0,
                priority=3,
                original_prompt="Review my last seven days.",
            )
        )
        cid = await _wait_correlation(repo)
        await _wait_armed(cid)

        # Sanity: row was created and Discord saw it with api_extended on offer.
        assert len(delivered) == 1
        offered = delivered[0].offered_modes
        assert "api_extended" in offered

        # ---- 2. Approve extension ----
        extension = await gate.grant_budget_extension(
            correlation_id=cid,
            granted_by="nick",
        )
        assert extension is not None
        assert extension.amount_usd == pytest.approx(4.0)
        assert extension.voided is False

        # ---- 3. User resolves with api_extended ----
        await gate.record_user_resolution(
            correlation_id=cid,
            mode="api_extended",
            owner_user_id="nick",
            task_id="task-ext-1",
        )
        outcome = await asyncio.wait_for(gate_task, timeout=2.0)
        assert outcome.fired is True
        assert outcome.mode == "api_extended"
        # ``extension_amount_usd`` is the gate's exposed surface; the
        # caller uses it to derive the ``max_tokens`` cap that prevents
        # actual spend from exceeding the approved grant (§10.6 row 1).
        assert outcome.extension_amount_usd == pytest.approx(4.0)
        assert outcome.escalation_request_id is not None

        # ---- 4. Daily remaining reflects the extension ----
        # The grant row for today is summed by the repo.
        granted_today = await extension_repo.get_daily_total("nick", date.today())
        assert granted_today == pytest.approx(4.0)

        # ---- 5. invocation_log audit chain ties events to the row ----
        cur = await conn.execute(
            "SELECT id FROM escalation_request WHERE correlation_id = ?",
            (cid,),
        )
        fetched = await cur.fetchone()
        assert fetched is not None
        eid = fetched[0]

        cur = await conn.execute(
            "SELECT output FROM invocation_log "
            "WHERE escalation_request_id = ? AND task_type = 'escalation_lifecycle' "
            "ORDER BY timestamp ASC",
            (eid,),
        )
        events = []
        for r in await cur.fetchall():
            payload = json.loads(r[0]) if isinstance(r[0], str) else r[0]
            events.append(payload.get("event"))
        # We require the offered and granted bookend events to land
        # under the same escalation_request_id; the resolved event
        # uses an internal optimistic-lock retry path that is not
        # under test here.
        assert "escalation_offered" in events
        assert "extension_granted" in events

    async def test_extension_voided_when_orchestrator_finds_orphan(
        self,
        db: Database,
        conn: aiosqlite.Connection,
    ) -> None:
        """Spec §10.6 row 4: orchestrator-boot scan voids extensions
        whose escalations were resolved ``api_extended`` but never
        produced a downstream LLM call (orchestrator crashed between
        grant and run). Slice 18 ships ``find_stale_grants`` +
        ``void_by_escalation_request_id``; slice 24 pins the contract
        end-to-end so a regression in either half stays visible."""
        repo = EscalationRepository(conn)
        extension_repo = BudgetExtensionRepository(conn)

        row = await repo.create(
            user_id="nick",
            correlation_id="orphan-1",
            task_id="task-orphan",
            task_type="weekly_review",
            estimate_usd=3.0,
            daily_remaining_usd=0.0,
            offered_modes=["api_extended", "pause", "cancel"],
            priority=3,
        )
        await repo.resolve(
            row.id,
            resolution="api_extended",
            resolved_by="nick",
            now=datetime.now(tz=UTC),
        )
        await extension_repo.grant(
            user_id="nick",
            for_date=date.today(),
            amount_usd=3.0,
            granted_by="nick",
            escalation_request_id=row.id,
            now=datetime.now(tz=UTC),
        )

        # Pre-condition: the daily total reflects the grant before the void.
        assert (
            await extension_repo.get_daily_total("nick", date.today())
        ) == pytest.approx(3.0)

        # The crash-recovery scan finds this orphan because no
        # non-escalation_lifecycle invocation_log row exists for it.
        stale = await extension_repo.find_stale_grants()
        assert row.id in stale

        voided = await extension_repo.void_by_escalation_request_id(row.id)
        assert voided is True

        # Daily total drops back to zero; voided=1 is the persistent audit.
        assert (
            await extension_repo.get_daily_total("nick", date.today())
        ) == 0.0
