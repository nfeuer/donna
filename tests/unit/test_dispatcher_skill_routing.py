from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from donna.agents.challenger_agent import ChallengerMatchResult
from donna.capabilities.models import CapabilityRow
from donna.orchestrator.dispatcher import AgentDispatcher


def _cap(name: str) -> CapabilityRow:
    return CapabilityRow(
        id="c1", name=name, description="x", input_schema={"type": "object"},
        trigger_type="on_message", default_output_shape=None, status="active",
        embedding=None, created_at=datetime.now(UTC), created_by="seed", notes=None,
    )


def _make_dispatcher(
    skill_routing_enabled: bool = True,
    challenger_match_status: str = "ready",
    challenger_capability_name: str = "parse_task",
    skill_exists: bool = True,
) -> tuple:
    """Create a dispatcher with mocked agents and skill infrastructure."""
    # Mock the challenger with both old execute() and new match_and_extract()
    challenger = AsyncMock()
    challenger.name = "challenger"
    challenger.timeout_seconds = 120
    challenger.execute.return_value = MagicMock(status="complete", output={}, questions=[])

    cap = _cap(challenger_capability_name) if challenger_match_status == "ready" else None
    challenger.match_and_extract.return_value = ChallengerMatchResult(
        status=challenger_match_status,
        capability=cap,
        extracted_inputs={"raw_text": "test"} if challenger_match_status == "ready" else {},
        match_score=0.9 if challenger_match_status == "ready" else 0.2,
    )

    # Mock PM agent
    pm = AsyncMock()
    pm.name = "pm"
    pm.timeout_seconds = 120
    pm.execute.return_value = MagicMock(
        status="complete",
        output={"recommended_agent": "scheduler"},
        questions=[],
    )

    # Mock scheduler agent
    scheduler = AsyncMock()
    scheduler.name = "scheduler"
    scheduler.timeout_seconds = 120
    scheduler.execute.return_value = MagicMock(
        status="complete",
        output={"task_id": "t1"},
        duration_ms=100,
        error=None,
    )

    agents = {"pm": pm, "challenger": challenger, "scheduler": scheduler}

    # Mock skill infrastructure
    skill_database = AsyncMock()
    skill_executor = AsyncMock()

    if skill_exists:
        skill_row = MagicMock(
            id="s1", capability_name="parse_task",
            current_version_id="v1", state="sandbox",
        )
        version_row = MagicMock(id="v1", version_number=1)
        skill_database.get_by_capability.return_value = skill_row
        skill_database.get_version.return_value = version_row
        skill_executor.execute.return_value = MagicMock(
            status="succeeded", final_output={"title": "x"}, total_latency_ms=50,
        )
    else:
        skill_database.get_by_capability.return_value = None

    # Mock tool_registry and router
    tool_registry = MagicMock()
    router = MagicMock()

    dispatcher = AgentDispatcher(
        agents=agents,
        tool_registry=tool_registry,
        router=router,
        db=MagicMock(),
        skill_executor=skill_executor,
        skill_database=skill_database,
        skill_routing_enabled=skill_routing_enabled,
    )

    return dispatcher, skill_executor, skill_database, pm, scheduler


async def test_dispatcher_runs_skill_shadow_alongside_legacy():
    """Skill shadow runs AND legacy flow runs. Legacy result is returned."""
    dispatcher, skill_executor, skill_database, pm, scheduler = _make_dispatcher()

    task = MagicMock()
    task.id = "t1"
    task.title = "draft the review"

    result = await dispatcher.dispatch(task, user_id="nick")

    # Legacy flow still ran.
    assert result.status == "complete"
    pm.execute.assert_awaited_once()
    scheduler.execute.assert_awaited_once()

    # Skill shadow also ran.
    skill_database.get_by_capability.assert_awaited_once_with("parse_task")
    skill_executor.execute.assert_awaited_once()


async def test_dispatcher_skips_skill_when_no_match():
    dispatcher, skill_executor, _, _pm, _scheduler = _make_dispatcher(
        challenger_match_status="escalate_to_claude"
    )

    task = MagicMock()
    task.id = "t1"
    task.title = "novel request"

    result = await dispatcher.dispatch(task, user_id="nick")

    # Legacy ran.
    assert result.status == "complete"
    # Skill was NOT executed.
    skill_executor.execute.assert_not_called()


async def test_dispatcher_skips_skill_when_flag_disabled():
    dispatcher, skill_executor, skill_database, _, _ = _make_dispatcher(
        skill_routing_enabled=False
    )

    task = MagicMock()
    task.id = "t1"
    task.title = "anything"

    result = await dispatcher.dispatch(task, user_id="nick")

    assert result.status == "complete"
    skill_executor.execute.assert_not_called()
    skill_database.get_by_capability.assert_not_called()


async def test_dispatcher_skill_shadow_failure_does_not_break_legacy():
    """If the skill shadow path throws, the legacy flow still completes."""
    dispatcher, skill_executor, _, pm, scheduler = _make_dispatcher()
    skill_executor.execute.side_effect = RuntimeError("skill exploded")

    task = MagicMock()
    task.id = "t1"
    task.title = "test"

    result = await dispatcher.dispatch(task, user_id="nick")

    # Legacy flow ran successfully despite skill error.
    assert result.status == "complete"
    pm.execute.assert_awaited_once()
    scheduler.execute.assert_awaited_once()


async def test_dispatcher_skill_shadow_skips_when_no_skill_exists():
    dispatcher, skill_executor, _, _, _ = _make_dispatcher(skill_exists=False)

    task = MagicMock()
    task.id = "t1"
    task.title = "test"

    await dispatcher.dispatch(task, user_id="nick")

    # Skill executor should NOT be called when no skill row exists.
    skill_executor.execute.assert_not_called()
