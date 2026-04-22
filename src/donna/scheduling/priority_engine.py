"""Daily priority recalculation engine — Phase 2.

Pure Python — no LLM call. Applies two escalation rules:

1. Deadline proximity: hard-deadline tasks approaching their deadline get
   their priority floored up toward 5.
2. Workload pressure: tasks scheduled on a crowded day that have been
   rescheduled multiple times get a +1 bump.

See docs/scheduling.md and config/calendar.yaml priority block.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from donna.config import PriorityConfig
from donna.tasks.database import TaskRow

logger = structlog.get_logger()


class PriorityEngine:
    """Recalculates task priorities based on deadline proximity and workload.

    Usage:
        engine = PriorityEngine(config)
        changes = engine.recalculate(tasks, now)
        # changes: [(task_id, old_priority, new_priority)]
    """

    def __init__(self, config: PriorityConfig) -> None:
        self._config = config

    def recalculate(
        self,
        tasks: list[TaskRow],
        now: datetime | None = None,
    ) -> list[tuple[str, int, int]]:
        """Return priority changes for tasks that need escalation.

        Args:
            tasks: All non-done/non-cancelled tasks for the user.
            now: Override current time (for testing).

        Returns:
            List of (task_id, old_priority, new_priority) for changed tasks.
        """
        now = now or datetime.now(tz=UTC)
        active = [
            t for t in tasks
            if t.status not in ("done", "cancelled")
        ]

        changes: list[tuple[str, int, int]] = []

        for task in active:
            old = task.priority
            new = self._compute_new_priority(task, active, now)
            if new != old:
                changes.append((task.id, old, new))
                logger.info(
                    "priority_escalated",
                    task_id=task.id,
                    old_priority=old,
                    new_priority=new,
                    title=task.title,
                )

        return changes

    def _compute_new_priority(
        self,
        task: TaskRow,
        all_tasks: list[TaskRow],
        now: datetime,
    ) -> int:
        """Return the new priority for a single task."""
        priority = task.priority

        # Rule 1: deadline proximity.
        deadline_priority = self._deadline_escalation(task, now)
        priority = max(priority, deadline_priority)

        # Rule 2: workload pressure.
        pressure_priority = self._workload_pressure(task, all_tasks)
        priority = max(priority, pressure_priority)

        return min(priority, 5)

    def _deadline_escalation(self, task: TaskRow, now: datetime) -> int:
        """Return minimum priority based on deadline proximity."""
        if task.deadline_type != "hard" or not task.deadline:
            return task.priority

        try:
            deadline_dt = _parse_dt(task.deadline)
        except (ValueError, TypeError):
            return task.priority

        if deadline_dt is None:
            return task.priority

        days_remaining = (deadline_dt - now).total_seconds() / 86400

        if days_remaining <= self._config.deadline_critical_days:
            return max(task.priority, 5)
        elif days_remaining <= self._config.deadline_warning_days:
            return max(task.priority, 4)

        return task.priority

    def _workload_pressure(self, task: TaskRow, all_tasks: list[TaskRow]) -> int:
        """Return bumped priority if the task's scheduled day is overloaded."""
        if not task.scheduled_start:
            return task.priority

        # Only escalate tasks that have been rescheduled enough times.
        if task.reschedule_count <= self._config.escalation_after_reschedules:
            return task.priority

        scheduled_date = task.scheduled_start[:10]  # YYYY-MM-DD prefix

        # Count tasks scheduled on the same day.
        same_day = sum(
            1 for t in all_tasks
            if t.scheduled_start and t.scheduled_start[:10] == scheduled_date
        )

        if same_day > self._config.workload_threshold_per_day:
            return min(task.priority + 1, 5)

        return task.priority


def _parse_dt(value: str) -> datetime | None:
    """Parse an ISO datetime string into a UTC-aware datetime."""
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt
