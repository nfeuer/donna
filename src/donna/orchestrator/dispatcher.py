"""Agent dispatcher — routes tasks through the agent hierarchy.

Coordinates the PM Agent assessment → execution agent flow described
in docs/agents.md. Enforces per-agent timeouts and logs all agent
activity via structured logging.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

from donna.agents.base import Agent, AgentContext, AgentResult
from donna.agents.tool_registry import ToolRegistry
from donna.models.router import ModelRouter
from donna.tasks.database import Database, TaskRow

logger = structlog.get_logger()


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
    ) -> None:
        self._agents = agents
        self._tool_registry = tool_registry
        self._router = router
        self._db = db
        self._project_root = project_root

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

        # Step 1: PM assessment
        pm = self._agents.get("pm")
        if pm is None:
            logger.warning("pm_agent_not_available")
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

        return exec_result

    async def _run_with_timeout(
        self, agent: Agent, task: TaskRow, context: AgentContext
    ) -> AgentResult:
        """Execute an agent with its configured timeout."""
        try:
            return await asyncio.wait_for(
                agent.execute(task, context),
                timeout=agent.timeout_seconds,
            )
        except asyncio.TimeoutError:
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
