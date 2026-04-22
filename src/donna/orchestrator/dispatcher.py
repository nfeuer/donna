"""Agent dispatcher — routes tasks through the agent hierarchy.

Coordinates the PM Agent assessment → execution agent flow described
in docs/agents.md. Enforces per-agent timeouts and logs all agent
activity via structured logging.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable

import structlog

from donna.agents.base import Agent, AgentContext, AgentResult
from donna.agents.tool_registry import ToolRegistry
from donna.models.router import ModelRouter
from donna.tasks.database import Database, TaskRow

logger = structlog.get_logger()


@runtime_checkable
class AgentActivityListener(Protocol):
    """Protocol for receiving agent lifecycle events."""

    async def on_agent_start(
        self, task_id: str, agent_name: str, task_title: str
    ) -> None: ...

    async def on_agent_complete(
        self, task_id: str, agent_name: str, result: AgentResult, cost_usd: float
    ) -> None: ...

    async def on_agent_failure(
        self, task_id: str, agent_name: str, error: str
    ) -> None: ...


class AgentDispatcher:
    """Routes tasks through PM assessment, then to execution agents.

    Usage:
        dispatcher = AgentDispatcher(agents, tool_registry, router, db)
        result = await dispatcher.dispatch(task, user_id="nick")
    """

    def __init__(
        self,
        agents: dict[str, Agent],
        tool_registry: ToolRegistry,
        router: ModelRouter,
        db: Database,
        project_root: Any = None,
        activity_listener: AgentActivityListener | None = None,
        # Phase 1 skill system (all optional)
        skill_executor: Any | None = None,
        skill_database: Any | None = None,
        skill_routing_enabled: bool = False,
    ) -> None:
        self._agents = agents
        self._tool_registry = tool_registry
        self._router = router
        self._db = db
        self._project_root = project_root
        self._activity_listener = activity_listener
        self._skill_executor = skill_executor
        self._skill_database = skill_database
        self._skill_routing_enabled = skill_routing_enabled

    async def dispatch(
        self,
        task: TaskRow,
        user_id: str,
    ) -> AgentResult:
        """Route a task through the PM agent, then to the execution agent.

        Flow:
        1. PM Agent assesses task completeness
        2. If needs_input → return questions to user (task → waiting_input)
        3. If complete → dispatch to recommended execution agent
        4. Return execution result

        Args:
            task: The task to process.
            user_id: The user who owns the task.

        Returns:
            AgentResult from the final agent in the chain.
        """
        from pathlib import Path

        context = AgentContext(
            router=self._router,
            db=self._db,
            user_id=user_id,
            project_root=self._project_root or Path.cwd(),
            tool_registry=self._tool_registry,
        )

        # Phase 1 skill shadow: run the skill path for logging only.
        await self._try_skill_shadow(task, user_id)

        # Notify listener of agent start.
        if self._activity_listener is not None:
            try:
                await self._activity_listener.on_agent_start(
                    task_id=task.id, agent_name="pm", task_title=task.title
                )
            except Exception:
                logger.exception("activity_listener_start_failed")

        # Step 1: PM assessment
        pm = self._agents.get("pm")
        if pm is None:
            logger.warning("pm_agent_not_available")
            if self._activity_listener is not None:
                try:
                    await self._activity_listener.on_agent_failure(
                        task_id=task.id, agent_name="pm", error="PM agent not configured"
                    )
                except Exception:
                    logger.exception("activity_listener_failure_failed")
            return AgentResult(
                status="failed",
                output={},
                error="PM agent not configured",
            )

        logger.info("dispatcher_pm_assessment", task_id=task.id)
        pm_result = await self._run_with_timeout(pm, task, context)

        if pm_result.status == "needs_input":
            logger.info(
                "dispatcher_needs_input",
                task_id=task.id,
                questions=pm_result.questions,
            )
            return pm_result

        if pm_result.status == "failed":
            logger.error("dispatcher_pm_failed", task_id=task.id, error=pm_result.error)
            return pm_result

        # Step 1.5: Challenger assessment (if enabled)
        challenger = self._agents.get("challenger")
        if challenger is not None:
            logger.info("dispatcher_challenger_assessment", task_id=task.id)
            challenger_result = await self._run_with_timeout(challenger, task, context)

            if challenger_result.status == "needs_input":
                logger.info(
                    "dispatcher_challenger_needs_input",
                    task_id=task.id,
                    questions=challenger_result.questions,
                )
                return challenger_result

        # Step 2: Dispatch to execution agent
        recommended = pm_result.output.get("recommended_agent", "scheduler")
        agent = self._agents.get(recommended)

        if agent is None:
            logger.warning(
                "dispatcher_agent_not_available",
                recommended=recommended,
                task_id=task.id,
            )
            # Fall back to scheduler if recommended agent isn't available
            agent = self._agents.get("scheduler")
            if agent is None:
                return AgentResult(
                    status="failed",
                    output=pm_result.output,
                    error=f"Neither {recommended!r} nor scheduler agent available",
                )

        logger.info(
            "dispatcher_executing",
            task_id=task.id,
            agent=agent.name,
        )

        exec_result = await self._run_with_timeout(agent, task, context)

        logger.info(
            "dispatcher_complete",
            task_id=task.id,
            agent=agent.name,
            status=exec_result.status,
            duration_ms=exec_result.duration_ms,
        )

        # Notify listener of completion or failure.
        if self._activity_listener is not None:
            try:
                if exec_result.status == "failed":
                    await self._activity_listener.on_agent_failure(
                        task_id=task.id,
                        agent_name=agent.name,
                        error=exec_result.error or "Unknown error",
                    )
                else:
                    await self._activity_listener.on_agent_complete(
                        task_id=task.id,
                        agent_name=agent.name,
                        result=exec_result,
                        cost_usd=0.0,
                    )
            except Exception:
                logger.exception("activity_listener_complete_failed")

        return exec_result

    async def _try_skill_shadow(self, task: TaskRow, user_id: str) -> None:
        """Run the skill shadow path for logging. Failures are caught and logged.

        Phase 1: this runs in the background alongside the legacy flow.
        The skill result is logged but NOT returned to the caller.
        """
        if not self._skill_routing_enabled:
            return

        challenger = self._agents.get("challenger")
        if challenger is None or not hasattr(challenger, "match_and_extract"):
            return

        if self._skill_executor is None or self._skill_database is None:
            return

        try:
            # Use task title as the match query (raw text is not available on TaskRow)
            query = task.title or ""
            match = await challenger.match_and_extract(
                user_message=query,
                user_id=user_id,
            )

            if match.status != "ready":
                logger.info(
                    "dispatcher_skill_shadow_no_match",
                    task_id=task.id,
                    match_status=match.status,
                    match_score=match.match_score,
                )
                return

            # Look up the skill and version
            skill_row = await self._skill_database.get_by_capability(match.capability.name)
            if skill_row is None or skill_row.current_version_id is None:
                logger.info(
                    "dispatcher_skill_shadow_no_skill",
                    task_id=task.id,
                    capability=match.capability.name,
                )
                return

            version_row = await self._skill_database.get_version(skill_row.current_version_id)
            if version_row is None:
                return

            result = await self._skill_executor.execute(
                skill=skill_row,
                version=version_row,
                inputs=match.extracted_inputs,
                user_id=user_id,
            )

            logger.info(
                "dispatcher_skill_shadow_complete",
                task_id=task.id,
                capability=match.capability.name,
                skill_status=result.status,
                skill_latency_ms=result.total_latency_ms,
            )
        except Exception:
            logger.exception("dispatcher_skill_shadow_failed", task_id=task.id)

    async def _run_with_timeout(
        self, agent: Agent, task: TaskRow, context: AgentContext
    ) -> AgentResult:
        """Execute an agent with its configured timeout."""
        try:
            return await asyncio.wait_for(
                agent.execute(task, context),
                timeout=agent.timeout_seconds,
            )
        except TimeoutError:
            logger.error(
                "agent_timeout",
                agent=agent.name,
                task_id=task.id,
                timeout_seconds=agent.timeout_seconds,
            )
            return AgentResult(
                status="failed",
                output={},
                error=f"Agent {agent.name!r} timed out after {agent.timeout_seconds}s",
            )
