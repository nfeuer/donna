"""Task prep work agent — Phase 2.

Background loop that polls for tasks with prep_work_flag=True whose
scheduled_start is within a configurable lead-time window. For each, calls
the prep_research LLM agent, stores the summary in task notes, and sets
agent_status=COMPLETE.

Runs every 15 minutes. Uses write-before-call idempotency: sets
agent_status=IN_PROGRESS before making the LLM call to prevent double
execution across restarts.

See docs/agents.md and prompts/prep_research.md.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog

from donna.models.router import ModelRouter
from donna.models.validation import validate_output
from donna.notifications.service import NotificationService
from donna.tasks.database import Database, TaskRow

logger = structlog.get_logger()

TASK_TYPE = "prep_research"
POLL_INTERVAL_SECONDS = 15 * 60  # 15 minutes


class PrepAgent:
    """Executes prep research for upcoming flagged tasks.

    Usage:
        agent = PrepAgent(db, router, service, user_id, project_root)
        asyncio.create_task(agent.run())
    """

    def __init__(
        self,
        db: Database,
        router: ModelRouter,
        service: NotificationService,
        user_id: str,
        project_root: Path,
        lead_hours: float = 2.0,
    ) -> None:
        self._db = db
        self._router = router
        self._service = service
        self._user_id = user_id
        self._project_root = project_root
        self._lead_hours = lead_hours

    async def run(self) -> None:
        """Poll every 15 minutes for tasks that need prep work."""
        logger.info("prep_agent_started", lead_hours=self._lead_hours, user_id=self._user_id)

        while True:
            try:
                await self._check_and_execute(datetime.now(tz=UTC))
            except Exception:
                logger.exception("prep_agent_poll_failed", user_id=self._user_id)

            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    async def _check_and_execute(self, now: datetime) -> None:
        """Find tasks in the prep window and execute prep for each."""
        window_end = now + timedelta(hours=self._lead_hours)

        conn = self._db.connection
        cursor = await conn.execute(
            """
            SELECT id FROM tasks
            WHERE user_id = ?
              AND prep_work_flag = 1
              AND prep_work_instructions IS NOT NULL
              AND agent_status IS NULL
              AND status = 'scheduled'
              AND scheduled_start IS NOT NULL
              AND scheduled_start >= ?
              AND scheduled_start <= ?
            """,
            (self._user_id, now.isoformat(), window_end.isoformat()),
        )
        rows = await cursor.fetchall()

        for (task_id,) in rows:
            task = await self._db.get_task(task_id)
            if task is None:
                continue
            await self._execute_prep(task)

    async def _execute_prep(self, task: TaskRow) -> None:
        """Run prep research for a single task."""
        # Write-before-call: mark IN_PROGRESS before LLM call for idempotency.
        await self._db.update_task(task.id, agent_status="in_progress")
        logger.info("prep_agent_executing", task_id=task.id, title=task.title)

        try:
            prompt = self._render_prompt(task)
            schema = self._router.get_output_schema(TASK_TYPE)
            raw, _ = await self._router.complete(prompt, task_type=TASK_TYPE, user_id=self._user_id)
            validated = validate_output(raw, schema)

            summary = self._format_summary(validated)

            # Append to task notes.
            existing_notes: list[str] = []
            if task.notes:
                try:
                    existing_notes = json.loads(task.notes)
                except (json.JSONDecodeError, TypeError):
                    existing_notes = []
            existing_notes.append(f"[prep_research]: {summary}")

            await self._db.update_task(
                task.id,
                notes=existing_notes,
                agent_status="complete",
            )

            logger.info("prep_agent_complete", task_id=task.id)

            # Notify via Discord.
            try:
                await self._service.dispatch(
                    notification_type="prep_ready",
                    content=f"Prep research ready for **{task.title}**: {summary}",
                    channel="tasks",
                    priority=3,
                )
            except Exception:
                logger.exception("prep_agent_notify_failed", task_id=task.id)

        except Exception:
            logger.exception("prep_agent_execution_failed", task_id=task.id)
            await self._db.update_task(task.id, agent_status="failed")

    def _render_prompt(self, task: TaskRow) -> str:
        """Render the prep_research prompt template."""
        template_path = self._project_root / "prompts" / "prep_research.md"
        template = template_path.read_text()

        replacements = {
            "{{ task_title }}": task.title,
            "{{ task_description }}": task.description or "",
            "{{ domain }}": task.domain,
            "{{ scheduled_start }}": task.scheduled_start or "not set",
            "{{ estimated_duration }}": str(task.estimated_duration or "unknown"),
            "{{ prep_work_instructions }}": task.prep_work_instructions or "",
        }
        result = template
        for key, value in replacements.items():
            result = result.replace(key, value)
        return result

    def _format_summary(self, validated: dict[str, Any]) -> str:
        """Build a concise summary string from the prep output."""
        summary = validated.get("summary", "")
        action_items = validated.get("action_items", [])
        if action_items:
            items_text = "; ".join(action_items[:3])
            return f"{summary} Actions: {items_text}"
        return summary
