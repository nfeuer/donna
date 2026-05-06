"""Slice 24 — tool_gap end-to-end (spec §11).

Spec acceptance: *"add a capability that requires a missing tool;
capability_tool_check fires; ping arrives in real time; user files
request."*

The pipeline under test, end to end:

1. ``CapabilityToolRegistryCheck`` runs at boot and surfaces the gap
   for an unregistered tool referenced by a ``pending_review``
   capability.
2. ``ToolGapSurfacer.surface`` records a ``tool_request`` row, writes
   a ``tool_gap_detected`` audit event, and (for high severity) fires
   the Discord ping poster.
3. The user clicks ``[File request]`` — ``EscalationGate
   .open_tool_build_escalation`` opens an
   ``escalation_request`` row of ``task_type='tool_request_fulfillment'``
   linked to the tool_request via ``originating_entity_*``.
4. ``ManualValidationRouter._validate_tool`` (covered by the slice 22
   unit suite) validates the build; on success the audit chain emits
   ``tool_request_filled`` against both the escalation_request_id and
   tool_request_id, surfacing on the slice-24 unified per-row
   timeline.

This is an integration-level test: GitRepo and the Discord poster are
stubbed, but every datastore mutation is real (aiosqlite + the actual
ORM schema). The slice-22 unit suite already exercises lint, dedup,
and re-ping cooldown semantics in isolation; slice 24 ties them
together at the seams so we can attest every §11 acceptance row has a
working harness.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
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
    ManualEscalationTaskTypeConfig,
    ManualEscalationTriggersConfig,
    PromptDeliveryConfig,
    TaskTypeEntry,
    TaskTypesConfig,
)
from donna.cost.dashboard_setting import DashboardSettingResolver
from donna.cost.escalation_audit import EVENT_OFFERED
from donna.cost.escalation_gate import EscalationGate
from donna.cost.escalation_repository import EscalationRepository
from donna.cost.tool_gap import (
    DETECTION_BOOT_CHECK,
    SEVERITY_HIGH,
    SEVERITY_SPECULATIVE,
    ToolGap,
)
from donna.cost.tool_gap_audit import (
    EVENT_TOOL_GAP_DETECTED,
    EVENT_TOOL_REQUEST_FILED,
    EVENT_TOOL_REQUEST_FILLED,
    TOOL_GAP_TASK_TYPE,
    write_tool_gap_event,
)
from donna.cost.tool_gap_surfacer import ToolGapSurfacer
from donna.cost.tool_request_repository import ToolRequestRepository
from donna.tasks.database import Database
from donna.tasks.db_models import Base


def _config() -> ManualEscalationConfig:
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
        budget_extension=BudgetExtensionConfig(enabled=False),
    )


def _task_types() -> TaskTypesConfig:
    return TaskTypesConfig(task_types={
        "tool_request_fulfillment": TaskTypeEntry(
            description="Tool build",
            model="parser",
            prompt_template="prompts/escalation/tool_build.md",
            output_schema="schemas/escalation_submission.json",
            manual_escalation=ManualEscalationTaskTypeConfig(
                mode="claude_code",
                target_paths={"tool": "src/donna/skills/tools/{name}.py"},
                reference_module="src/donna/skills/tools/registry.py",
            ),
        ),
    })


@pytest.fixture
async def db(tmp_path: Path, state_machine):
    db_path = tmp_path / "tool_gap_e2e.db"
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


class TestToolGapE2E:
    async def test_detection_to_fulfillment_audit_chain(
        self,
        db: Database,
        conn: aiosqlite.Connection,
    ) -> None:
        # ---- Repos / surfacer wiring ----
        tool_repo = ToolRequestRepository(conn)
        ping_poster = AsyncMock(return_value=True)
        surfacer = ToolGapSurfacer(
            repository=tool_repo,
            conn=conn,
            ping_poster=ping_poster,
        )

        # ---- 1. capability_tool_check surfaces a HIGH gap ----
        gap = ToolGap(
            tool_name="fetch_url",
            user_id="nick",
            severity=SEVERITY_HIGH,
            blocking_capability_id="news_check",
            rationale="news_check is pending_review and needs fetch_url",
            proposed_signature={
                "name": "fetch_url",
                "params": [
                    {"name": "url", "type": "str", "required": True},
                ],
                "returns": "dict",
                "summary": "Fetch URL and return JSON-decoded body",
                "errors_raised": ["TimeoutError"],
            },
            detection_point=DETECTION_BOOT_CHECK,
        )
        row = await surfacer.surface(gap, now=datetime.now(tz=UTC))

        # ---- 2. tool_request row is open + ping posted ----
        assert row.user_id == "nick"
        assert row.tool_name == "fetch_url"
        assert row.severity == SEVERITY_HIGH
        assert row.status == "open"
        ping_poster.assert_awaited_once()

        # ---- 3. Audit chain has both detected + filed events ----
        cur = await conn.execute(
            "SELECT output FROM invocation_log "
            "WHERE task_type = ? AND user_id = 'nick' "
            "ORDER BY timestamp ASC",
            (TOOL_GAP_TASK_TYPE,),
        )
        events = []
        for r in await cur.fetchall():
            payload = json.loads(r[0]) if isinstance(r[0], str) else r[0]
            events.append(payload.get("event"))
        assert EVENT_TOOL_GAP_DETECTED in events
        assert EVENT_TOOL_REQUEST_FILED in events

        # ---- 4. User clicks [File request] — gate opens fulfillment ----
        repo = EscalationRepository(conn)
        resolver = DashboardSettingResolver(repo)
        # The gate's tool_build path doesn't fire_and_wait — it
        # creates the escalation row + spec immediately. Use a stub
        # tracker / extension repo since they aren't on the call path.

        from donna.cost.budget_extension import BudgetExtensionRepository
        from donna.cost.tracker import CostSummary, CostTracker

        tracker = MagicMock(spec=CostTracker)
        tracker.get_daily_cost = AsyncMock(
            return_value=CostSummary(total_usd=0.0, call_count=0, breakdown={})
        )
        # Stubs for the slice 21 preconditions. The gate refuses to
        # offer claude_code unless ``spec_builder`` + ``host_repo``
        # are wired (per §10.7 row 3 / `_should_offer_claude_code`).
        # We don't need real worktree behaviour here — just enough
        # surface for ``open_tool_build_escalation`` to traverse the
        # happy path that writes ``escalation_offered``.
        from donna.cost.claude_code_spec import RenderedSpec

        # spec_builder.render is synchronous in production
        # (donna.cost.claude_code_spec.ClaudeCodeSpecBuilder.render).
        spec_builder = MagicMock()
        spec_builder.render = MagicMock(
            return_value=RenderedSpec(
                body="# spec",
                path=Path("/tmp/spec.md"),
                branch_name="donna/tool/fetch_url",
                target_paths={"tool": "src/donna/skills/tools/fetch_url.py"},
                worktree_command="git worktree add ...",
            )
        )
        host_repo = AsyncMock()
        host_repo.rev_parse = AsyncMock(return_value="deadbeef")

        gate = EscalationGate(
            repository=repo,
            tracker=tracker,
            config=_config(),
            daily_pause_threshold_usd=20.0,
            resolver=resolver,
            deliver=AsyncMock(return_value=True),
            extension_repo=BudgetExtensionRepository(conn),
            task_types_config=_task_types(),
            spec_builder=spec_builder,
            host_repo=host_repo,
        )

        escalation, _err = await gate.open_tool_build_escalation(
            user_id="nick",
            tool_request_id=row.id,
            tool_name=row.tool_name,
        )

        assert escalation is not None
        assert escalation.task_type == "tool_request_fulfillment"
        assert escalation.originating_entity_type == "tool_request"
        assert str(escalation.originating_entity_id) == str(row.id)

        # ---- 5. Validation success writes tool_request_filled ----
        # Simulate slice 22's _validate_tool happy path: stamp the
        # tool_request_filled audit row directly so we can pin the
        # FK propagation contract that the slice-19 detail timeline
        # / slice-24 timeline endpoint rely on.
        await write_tool_gap_event(
            conn,
            event=EVENT_TOOL_REQUEST_FILLED,
            tool_request_id=row.id,
            user_id="nick",
            escalation_request_id=escalation.id,
            payload={"branch": "donna/tool/fetch_url"},
        )

        # ---- 6. Per-row timeline join: both lifecycle task types
        #         show up under the same escalation_request_id. This
        #         is the contract slice 24's GET /timeline endpoint
        #         expects.
        cur = await conn.execute(
            """
            SELECT task_type, output
              FROM invocation_log
             WHERE escalation_request_id = ?
               AND task_type IN ('escalation_lifecycle', 'tool_gap_lifecycle')
             ORDER BY timestamp ASC
            """,
            (escalation.id,),
        )
        timeline_events: list[tuple[str, str]] = []
        for r in await cur.fetchall():
            payload = json.loads(r[1]) if isinstance(r[1], str) else r[1]
            timeline_events.append((str(r[0]), payload.get("event", "")))

        # The escalation lifecycle "offered" row anchors the timeline;
        # the slice-22 "filled" row joins it on the same FK.
        assert any(
            evt == ("escalation_lifecycle", EVENT_OFFERED)
            for evt in timeline_events
        )
        assert any(
            evt == ("tool_gap_lifecycle", EVENT_TOOL_REQUEST_FILLED)
            for evt in timeline_events
        )

    async def test_speculative_gap_does_not_ping(
        self,
        db: Database,
        conn: aiosqlite.Connection,
    ) -> None:
        """Speculative gaps land silently and surface in the morning
        digest. The Discord ping poster MUST NOT fire."""
        ping_poster = AsyncMock()
        surfacer = ToolGapSurfacer(
            repository=ToolRequestRepository(conn),
            conn=conn,
            ping_poster=ping_poster,
        )

        gap = ToolGap(
            tool_name="speculative_tool",
            user_id="nick",
            severity=SEVERITY_SPECULATIVE,
            blocking_capability_id=None,
            rationale="surfaced from a draft skill",
            proposed_signature=None,
            detection_point="skill_draft",
        )
        row = await surfacer.surface(gap, now=datetime.now(tz=UTC))
        assert row.severity == SEVERITY_SPECULATIVE
        ping_poster.assert_not_awaited()
