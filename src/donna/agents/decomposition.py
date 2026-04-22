"""Task decomposition service — Phase 2.

Takes a task and calls the LLM with the `task_decompose` prompt to break it
into subtasks. Persists each subtask as a real Task row with parent_task set.
Integer dependency indices from the LLM output are resolved to real UUIDs via
a two-pass insert.

See docs/agents.md and prompts/task_decompose.md.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from donna.models.router import ModelRouter
from donna.models.validation import validate_output
from donna.tasks.database import Database, TaskRow
from donna.tasks.db_models import InputChannel, TaskDomain, TaskStatus

logger = structlog.get_logger()

TASK_TYPE = "task_decompose"


@dataclasses.dataclass(frozen=True)
class DecomposeResult:
    """Summary of a successful decomposition operation."""

    parent_task_id: str
    subtask_ids: list[str]
    total_estimated_hours: float
    missing_information: list[dict[str, Any]]
    deadline_feasible: bool | None


class DecompositionService:
    """Breaks a complex task into subtasks via LLM.

    Usage:
        svc = DecompositionService(db, router, user_id, project_root)
        result = await svc.decompose(task_id)
    """

    def __init__(
        self,
        db: Database,
        router: ModelRouter,
        user_id: str,
        project_root: Path,
    ) -> None:
        self._db = db
        self._router = router
        self._user_id = user_id
        self._project_root = project_root

    async def decompose(self, task_id: str, user_context: str = "") -> DecomposeResult:
        """Decompose a task into subtasks.

        Args:
            task_id: UUID of the parent task to decompose.
            user_context: Optional extra context from the user.

        Returns:
            DecomposeResult with the list of created subtask IDs.

        Raises:
            ValueError: If the task is not found.
        """
        task = await self._db.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")

        prompt = self._render_prompt(task, user_context)
        schema = self._router.get_output_schema(TASK_TYPE)
        raw, _ = await self._router.complete(prompt, task_type=TASK_TYPE, user_id=self._user_id)
        validated = validate_output(raw, schema)

        subtask_ids = await self._persist_subtasks(task, validated.get("subtasks", []))

        result = DecomposeResult(
            parent_task_id=task_id,
            subtask_ids=subtask_ids,
            total_estimated_hours=validated.get("total_estimated_hours", 0.0),
            missing_information=validated.get("missing_information", []),
            deadline_feasible=validated.get("suggested_deadline_feasible"),
        )

        logger.info(
            "task_decomposed",
            parent_task_id=task_id,
            subtask_count=len(subtask_ids),
            total_hours=result.total_estimated_hours,
            user_id=self._user_id,
        )

        return result

    def _render_prompt(self, task: TaskRow, user_context: str) -> str:
        """Render the task_decompose prompt template."""
        template_path = self._project_root / "prompts" / "task_decompose.md"
        template = template_path.read_text()

        now = datetime.now(tz=UTC)
        replacements = {
            "{{ current_date }}": now.strftime("%Y-%m-%d"),
            "{{ task_title }}": task.title,
            "{{ task_description }}": task.description or "",
            "{{ domain }}": task.domain,
            "{{ deadline }}": task.deadline or "none",
            "{{ estimated_duration }}": str(task.estimated_duration or "unknown"),
            "{{ tags }}": json.dumps(json.loads(task.tags) if task.tags else []),
            "{{ user_context }}": user_context,
        }
        result = template
        for key, value in replacements.items():
            result = result.replace(key, value)
        return result

    async def _persist_subtasks(
        self,
        parent: TaskRow,
        subtasks_data: list[dict[str, Any]],
    ) -> list[str]:
        """Insert subtasks and resolve integer dependency indices to UUIDs.

        Two-pass: first insert all subtasks (collecting UUIDs), then update
        each task's dependencies JSON to replace indices with real UUIDs.
        """
        if not subtasks_data:
            return []

        # Sort by priority_order so index 0 = first task.
        ordered = sorted(subtasks_data, key=lambda s: s.get("priority_order", 0))

        # Pass 1: insert all subtasks without dependencies.
        created: list[TaskRow] = []
        for subtask_data in ordered:
            try:
                domain_str = parent.domain
                domain = TaskDomain(domain_str) if domain_str in TaskDomain._value2member_map_ else TaskDomain.PERSONAL
            except (ValueError, AttributeError):
                domain = TaskDomain.PERSONAL

            row = await self._db.create_task(
                user_id=self._user_id,
                title=subtask_data["title"],
                description=subtask_data.get("description"),
                domain=domain,
                priority=parent.priority,
                status=TaskStatus.BACKLOG,
                estimated_duration=subtask_data.get("estimated_duration"),
                created_via=InputChannel.DISCORD,
                parent_task=parent.id,
                agent_eligible=subtask_data.get("agent_eligible", False),
            )
            created.append(row)

        # Pass 2: update dependencies using real UUIDs.
        for i, subtask_data in enumerate(ordered):
            dep_indices: list[int] = subtask_data.get("dependencies", [])
            if dep_indices:
                dep_uuids = [
                    created[idx].id
                    for idx in dep_indices
                    if 0 <= idx < len(created)
                ]
                if dep_uuids:
                    await self._db.update_task(created[i].id, dependencies=dep_uuids)

        return [row.id for row in created]
