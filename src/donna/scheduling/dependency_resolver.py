"""Dependency chain scheduling — Phase 2.

Provides topological sort and scheduling helpers for tasks with dependencies.
Ensures that blocking tasks are always scheduled before the tasks they block.

See docs/scheduling.md.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from typing import Any

import structlog

from donna.tasks.database import TaskRow

logger = structlog.get_logger()

# Statuses that count as "resolved" for dependency purposes.
_RESOLVED_STATUSES = {"done", "cancelled", "scheduled"}


class CyclicDependencyError(Exception):
    """Raised when the dependency graph contains a cycle."""

    def __init__(self, cycle: list[str]) -> None:
        self.cycle = cycle
        super().__init__(f"Cyclic dependency detected: {' → '.join(cycle)}")


def topological_sort(tasks: list[TaskRow]) -> list[TaskRow]:
    """Return tasks sorted so that all dependencies come before dependents.

    Uses Kahn's algorithm on the dependency graph derived from task.dependencies.

    Args:
        tasks: List of tasks (may include tasks with no dependencies).

    Returns:
        Tasks in topological order (blockers first).

    Raises:
        CyclicDependencyError: If the dependency graph contains a cycle.
    """
    task_map = {t.id: t for t in tasks}
    task_ids = set(task_map)

    # Build adjacency list: edge A → B means A must come before B.
    in_degree: dict[str, int] = {t.id: 0 for t in tasks}
    dependents: dict[str, list[str]] = defaultdict(list)  # blocker → [tasks that depend on it]

    for task in tasks:
        deps = _parse_deps(task.dependencies)
        for dep_id in deps:
            if dep_id not in task_ids:
                continue  # ignore references to tasks outside the input set
            dependents[dep_id].append(task.id)
            in_degree[task.id] += 1

    # Kahn's BFS.
    queue: deque[str] = deque(t_id for t_id, deg in in_degree.items() if deg == 0)
    result: list[TaskRow] = []

    while queue:
        t_id = queue.popleft()
        result.append(task_map[t_id])
        for dep in dependents[t_id]:
            in_degree[dep] -= 1
            if in_degree[dep] == 0:
                queue.append(dep)

    if len(result) != len(tasks):
        # Remaining tasks with in_degree > 0 are part of the cycle.
        cycle_ids = [t_id for t_id, deg in in_degree.items() if deg > 0]
        raise CyclicDependencyError(cycle_ids)

    return result


def tasks_ready_to_schedule(all_tasks: list[TaskRow]) -> list[TaskRow]:
    """Return tasks whose dependencies are all resolved (done/cancelled/scheduled).

    A task with no dependencies is always ready. A task whose dependencies
    include any non-resolved task is not ready.
    """
    status_map = {t.id: t.status for t in all_tasks}
    ready: list[TaskRow] = []

    for task in all_tasks:
        if task.status in _RESOLVED_STATUSES:
            continue  # already resolved — not a candidate
        deps = _parse_deps(task.dependencies)
        if all(status_map.get(dep_id, "done") in _RESOLVED_STATUSES for dep_id in deps):
            ready.append(task)

    return ready


def _parse_deps(dependencies: str | list[Any] | None) -> list[str]:
    """Parse the dependencies field into a list of task ID strings."""
    if not dependencies:
        return []
    if isinstance(dependencies, list):
        return [str(d) for d in dependencies]
    try:
        parsed = json.loads(dependencies)
        if isinstance(parsed, list):
            return [str(d) for d in parsed]
    except (json.JSONDecodeError, TypeError):
        pass
    return []
