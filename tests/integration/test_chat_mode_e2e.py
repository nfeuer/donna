"""Slice 20 end-to-end test for the chat-mode pipeline.

Drives every component in the chat-mode happy path with realistic
plumbing rather than per-component mocks:

1. ``EscalationGate.fire_and_wait`` decides the task is over budget.
2. ``ChatPromptBuilder.build_and_persist`` renders + summarises +
   writes the workspace ``.md`` and persists ``prompt_body`` /
   ``summary`` / ``prompt_path`` / ``mode='chat'``.
3. The delivery callback (mocked Discord bot) ships the summary +
   attachment to ``#donna-tasks``.
4. The user clicks "Manual handoff" — ``record_user_resolution``
   transitions the row to ``resolved`` with ``mode='chat'``.
5. The user submits an answer through the shared
   ``apply_submission`` service (the same path the dashboard endpoint
   and ``/donna_submit`` use).
6. ``ChatEscalationIngestionPoller.tick_once`` picks up the
   ``submitted`` row, appends the answer to the originating task's
   notes, transitions the task to ``done``, and marks the escalation
   ``validated``.

Realises the §11 acceptance criterion: *"trigger an over-budget
chat_escalation; receive Discord prompt; submit answer via dashboard;
task completes with answer as result."*
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
from sqlalchemy import create_engine

from donna.config import (
    ManualEscalationConfig,
    ManualEscalationModeConfig,
    ManualEscalationModesConfig,
    ManualEscalationTaskTypeConfig,
    ManualEscalationTriggersConfig,
    PromptDeliveryConfig,
    TaskTypeEntry,
    TaskTypesConfig,
)
from donna.cost.budget_extension import BudgetExtensionRepository
from donna.cost.dashboard_setting import DashboardSettingResolver
from donna.cost.escalation_chat_prompt import ChatPromptBuilder
from donna.cost.escalation_gate import EscalationGate
from donna.cost.escalation_repository import EscalationRepository
from donna.cost.escalation_submit_service import apply_submission
from donna.cost.tracker import CostSummary, CostTracker
from donna.skills.chat_escalation_ingestion_poller import (
    ChatEscalationIngestionPoller,
)
from donna.tasks.database import Database
from donna.tasks.db_models import Base, TaskStatus

_REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------


def _manual_escalation_config() -> ManualEscalationConfig:
    return ManualEscalationConfig(
        enabled=True,
        modes=ManualEscalationModesConfig(
            chat=ManualEscalationModeConfig(enabled=True),
            claude_code=ManualEscalationModeConfig(enabled=True),
        ),
        triggers=ManualEscalationTriggersConfig(task_approval_threshold_usd=5.0),
        prompt_delivery=PromptDeliveryConfig(),
    )


def _task_types_config() -> TaskTypesConfig:
    return TaskTypesConfig(task_types={
        "chat_escalation": TaskTypeEntry(
            description="Chat escalation",
            model="parser",
            prompt_template="prompts/chat/chat_respond.md",
            output_schema="schemas/chat_respond_output.json",
            manual_escalation=ManualEscalationTaskTypeConfig(mode="chat"),
        ),
    })


def _stub_extension_repo() -> MagicMock:
    stub = MagicMock(spec=BudgetExtensionRepository)
    stub.get_daily_total = AsyncMock(return_value=0.0)
    stub.get_monthly_total = AsyncMock(return_value=0.0)
    return stub


def _stub_router(*, summary_payload: dict[str, str]) -> MagicMock:
    """A router whose ``complete()`` returns a valid summarizer payload
    and whose ``get_prompt_template`` returns the rendered summarizer
    template content (so the real builder code path runs)."""
    router = MagicMock()
    router.complete = AsyncMock(return_value=(summary_payload, MagicMock()))
    router.get_prompt_template = MagicMock(
        return_value="Summarize this prompt: {{ original_prompt }}"
    )
    return router


@pytest.fixture
async def db(tmp_path: Path, state_machine):
    db_path = tmp_path / "chat_e2e.db"
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


async def _seed_originating_task(conn: aiosqlite.Connection, *, task_id: str) -> None:
    await conn.execute(
        """
        INSERT INTO tasks (
            id, user_id, title, description, domain, priority, status,
            deadline_type, created_at, created_via, prep_work_flag,
            agent_eligible, reschedule_count, donna_managed, nudge_count
        )
        VALUES (?, 'nick', 'Answer me this', NULL, 'personal', 2,
                'in_progress', 'none', '2026-05-06T00:00:00', 'discord',
                0, 0, 0, 0, 0)
        """,
        (task_id,),
    )
    await conn.commit()


async def _await_correlation_id(repo: EscalationRepository) -> str:
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


async def _await_gate_armed(correlation_id: str) -> None:
    """Yield until the gate has finished delivery and registered its
    resolution event. Without this wait the test races the gate's
    setup and ``record_user_resolution`` may signal an event that
    doesn't exist yet, leaving the gate's ``event.wait()`` to hang
    forever."""
    for _ in range(500):
        if EscalationGate._events.get(correlation_id) is not None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("gate did not arm its resolution event in time")


# ---------------------------------------------------------------------
# E2E
# ---------------------------------------------------------------------


class TestChatModeE2E:
    async def test_full_chat_pipeline_lands_answer_on_originating_task(
        self,
        db: Database,
        tmp_path: Path,
    ) -> None:
        # ---- Setup ----
        task_id = "01900000-0000-7000-0000-000000000001"
        await _seed_originating_task(db.connection, task_id=task_id)

        repo = EscalationRepository(db.connection)
        resolver = DashboardSettingResolver(repo)
        tracker = MagicMock(spec=CostTracker)
        tracker.get_daily_cost = AsyncMock(
            return_value=CostSummary(total_usd=0.0, call_count=0, breakdown={})
        )

        config = _manual_escalation_config()
        builder = ChatPromptBuilder(
            router=_stub_router(
                summary_payload={
                    "title": "Quick question",
                    "summary": "Donna parked a high-value chat call; answer in claude.ai.",
                }
            ),
            project_root=_REPO_ROOT,
            config=config.prompt_delivery,
            workspace_root=tmp_path / "workspace",
        )

        # Captures the last row the delivery callback saw so the test
        # can assert that summary + prompt_path made it through.
        delivered_rows: list = []

        async def _deliver(row) -> bool:
            delivered_rows.append(row)
            return True

        gate = EscalationGate(
            repository=repo,
            tracker=tracker,
            config=config,
            daily_pause_threshold_usd=20.0,
            resolver=resolver,
            deliver=_deliver,
            extension_repo=_stub_extension_repo(),
            task_types_config=_task_types_config(),
            chat_prompt_builder=builder,
        )

        # ---- 1. Gate fires + 2. Prompt builder runs + 3. Delivery ----
        gate_task = asyncio.create_task(
            gate.fire_and_wait(
                user_id="nick",
                task_id=task_id,
                task_type="chat_escalation",
                estimate_usd=8.0,
                priority=3,
                original_prompt="What's the right way to debounce a webhook?",
            )
        )
        cid = await _await_correlation_id(repo)
        await _await_gate_armed(cid)

        # Delivery should have happened with the freshly-persisted summary
        # and prompt_path.
        assert len(delivered_rows) == 1
        delivered = delivered_rows[0]
        assert delivered.summary is not None
        assert "Quick question" in delivered.summary
        assert delivered.prompt_path is not None
        # Workspace file landed on disk too.
        assert Path(delivered.prompt_path).read_text(encoding="utf-8")

        # ---- 4. User clicks Manual handoff (chat) ----
        await gate.record_user_resolution(
            correlation_id=cid,
            mode="chat",
            owner_user_id="nick",
            task_id=task_id,
        )
        outcome = await asyncio.wait_for(gate_task, timeout=2.0)
        assert outcome.fired is True
        assert outcome.mode == "chat"
        assert outcome.resolved_by == "user"

        # ---- 5. User submits via shared service ----
        answer = (
            "Use a per-key Redis lock with a 30-second TTL and reject "
            "duplicate webhook IDs by hashing the body."
        )
        result = await apply_submission(
            conn=db.connection,
            correlation_id=cid,
            payload={"mode": "chat", "answer": answer},
        )
        assert result.status == "submitted"
        assert result.mode == "chat"

        # ---- 6. Ingestion poller closes the loop ----
        poller = ChatEscalationIngestionPoller(db=db)
        processed = await poller.tick_once()
        assert processed == 1

        # Originating task: notes contain the annotated answer + status DONE.
        refreshed = await db.get_task(task_id)
        assert refreshed is not None
        assert refreshed.status == TaskStatus.DONE.value
        notes = json.loads(refreshed.notes) if refreshed.notes else []
        assert any(answer in n for n in notes)
        assert any(n.startswith(f"[escalation:{cid}]") for n in notes)

        # Escalation row: validated with the chat channel marker.
        cur = await db.connection.execute(
            "SELECT status, validation_result FROM escalation_request "
            "WHERE correlation_id = ?",
            (cid,),
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == "validated"
        validation = json.loads(row[1])
        assert validation["channel"] == "chat"
        assert validation["correlation_id"] == cid

        # Audit timeline contains every milestone exactly once.
        cur = await db.connection.execute(
            """
            SELECT json_extract(output, '$.event'), COUNT(*)
              FROM invocation_log
             WHERE task_type = 'escalation_lifecycle'
               AND escalation_request_id = (
                   SELECT id FROM escalation_request WHERE correlation_id = ?
               )
             GROUP BY 1
             ORDER BY 1
            """,
            (cid,),
        )
        events = {ev: count for ev, count in await cur.fetchall()}
        assert events.get("escalation_offered") == 1
        assert events.get("escalation_resolved") == 1
        assert events.get("escalation_submitted") == 1
        assert events.get("escalation_validated") == 1

    async def test_summarizer_schema_violation_falls_back_to_deterministic(
        self,
        db: Database,
        tmp_path: Path,
    ) -> None:
        """A summarizer response missing required fields trips
        ``jsonschema.validate`` which routes us to the deterministic
        fallback (§10.2 row 3). The escalation still gets delivered."""
        task_id = "01900000-0000-7000-0000-000000000002"
        await _seed_originating_task(db.connection, task_id=task_id)

        repo = EscalationRepository(db.connection)
        resolver = DashboardSettingResolver(repo)
        tracker = MagicMock(spec=CostTracker)
        tracker.get_daily_cost = AsyncMock(
            return_value=CostSummary(total_usd=0.0, call_count=0, breakdown={})
        )
        config = _manual_escalation_config()
        # Returns a payload with the required ``title`` missing.
        builder = ChatPromptBuilder(
            router=_stub_router(summary_payload={"summary": "oops no title"}),
            project_root=_REPO_ROOT,
            config=config.prompt_delivery,
            workspace_root=tmp_path / "workspace",
        )

        async def _deliver(_row) -> bool:
            return True

        gate = EscalationGate(
            repository=repo,
            tracker=tracker,
            config=config,
            daily_pause_threshold_usd=20.0,
            resolver=resolver,
            deliver=_deliver,
            extension_repo=_stub_extension_repo(),
            task_types_config=_task_types_config(),
            chat_prompt_builder=builder,
        )

        gate_task = asyncio.create_task(
            gate.fire_and_wait(
                user_id="nick",
                task_id=task_id,
                task_type="chat_escalation",
                estimate_usd=8.0,
                original_prompt="anything",
            )
        )
        cid = await _await_correlation_id(repo)
        await _await_gate_armed(cid)
        await gate.record_user_resolution(
            correlation_id=cid,
            mode="pause",
            owner_user_id="nick",
            task_id=task_id,
        )
        outcome = await asyncio.wait_for(gate_task, timeout=2.0)
        assert outcome.fired is True

        cur = await db.connection.execute(
            "SELECT summary FROM escalation_request WHERE correlation_id = ?",
            (cid,),
        )
        row = await cur.fetchone()
        assert row is not None
        # Deterministic fallback shape:
        # "<task_type> request — estimate $X.XX. Click for full prompt."
        assert "Click for full prompt." in row[0]
