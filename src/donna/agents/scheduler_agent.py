"""Scheduler agent — Phase 3.

Wraps the existing Scheduler service to operate as a sub-agent within
the agent framework. High autonomy for priority 1–3 tasks; auto-schedules
based on calendar availability.

See docs/agents.md — Agent Hierarchy.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from donna.agents.base import AgentContext, AgentResult
from donna.tasks.database import TaskRow

logger = structlog.get_logger()

_TIMEOUT_SECONDS = 120  # 2 minutes


class SchedulerAgent:
    """Scheduler agent that finds slots and schedules tasks.

    Wraps ``donna.scheduling.scheduler.Scheduler`` to fit the Agent protocol.
    """

    @property
    def name(self) -> str:
        return "scheduler"

    @property
    def allowed_tools(self) -> list[str]:
        return ["calendar_read", "calendar_write", "task_db_read", "task_db_write"]

    @property
    def timeout_seconds(self) -> int:
        return _TIMEOUT_SECONDS

    async def execute(self, task: TaskRow, context: AgentContext) -> AgentResult:
        """Find a slot and schedule the task.

        Uses the tool registry for calendar reads if available, otherwise
        falls back to direct scheduler invocation for compatibility.
        """
        start = time.monotonic()

        try:
            # Read existing calendar events via tool registry.
            if context.tool_registry.is_allowed("parse_task", "calendar_read"):
                events_data = await context.tool_registry.execute(
                    "calendar_read",
                    {"lookahead_days": 14},
                )
                events = events_data.get("events", [])
            else:
                events = []

            # Find next available slot.
            from donna.config import load_calendar_config
            from donna.scheduling.scheduler import Scheduler

            config_dir = context.project_root / "config"
            cal_config = load_calendar_config(config_dir)
            scheduler = Scheduler(cal_config)

            slot = scheduler.find_next_slot(task, events)

            elapsed = int((time.monotonic() - start) * 1000)

            logger.info(
                "scheduler_agent_slot_found",
                task_id=task.id,
                slot_start=slot.start.isoformat(),
                slot_end=slot.end.isoformat(),
                duration_ms=elapsed,
            )

            return AgentResult(
                status="complete",
                output={
                    "task_id": task.id,
                    "slot_start": slot.start.isoformat(),
                    "slot_end": slot.end.isoformat(),
                    "action": "schedule",
                },
                duration_ms=elapsed,
            )

        except Exception as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            logger.error(
                "scheduler_agent_failed",
                task_id=task.id,
                error=str(exc),
            )
            return AgentResult(
                status="failed",
                output={},
                duration_ms=elapsed,
                error=str(exc),
            )
