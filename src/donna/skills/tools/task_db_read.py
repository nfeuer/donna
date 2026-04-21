"""task_db_read — thin read-only wrapper around Database.get_task / list_tasks.

Registered into DEFAULT_TOOL_REGISTRY via donna.skills.tools.register_default_tools.
Only registered when a Database handle is available at boot.

Read-only by construction: never imports or references create/update/transition.
"""
from __future__ import annotations

import dataclasses
from typing import Any

import structlog

from donna.tasks.db_models import TaskDomain, TaskStatus

logger = structlog.get_logger()

_PROJECTED_FIELDS = (
    "id",
    "title",
    "description",
    "domain",
    "priority",
    "status",
    "deadline",
    "scheduled_start",
    "capability_name",
    "inputs",
)


class TaskDbReadError(Exception):
    """Raised when a task_db_read invocation fails."""


async def task_db_read(
    *,
    client: Any,
    task_id: str | None = None,
    user_id: str | None = None,
    status: str | None = None,
    domain: str | None = None,
) -> dict[str, Any]:
    """Fetch one task (by ``task_id``) or a filtered list.

    When ``task_id`` is provided, returns ``{ok, task}``; otherwise returns
    ``{ok, tasks}`` filtered by any of ``user_id`` / ``status`` / ``domain``.
    """
    if task_id is not None:
        if not task_id.strip():
            raise TaskDbReadError("task_id must be non-empty")
        if user_id is not None or status is not None or domain is not None:
            raise TaskDbReadError(
                "task_id is mutually exclusive with user_id/status/domain filters"
            )
        try:
            row = await client.get_task(task_id)
        except Exception as exc:
            logger.warning("task_db_read_get_failed", task_id=task_id, error=str(exc))
            raise TaskDbReadError(f"get_task: {exc}") from exc
        if row is None:
            raise TaskDbReadError(f"task not found: {task_id}")
        return {"ok": True, "task": _project(row)}

    status_enum = _coerce_status(status) if status is not None else None
    domain_enum = _coerce_domain(domain) if domain is not None else None
    try:
        rows = await client.list_tasks(
            user_id=user_id, status=status_enum, domain=domain_enum,
        )
    except Exception as exc:
        logger.warning(
            "task_db_read_list_failed",
            user_id=user_id, status=status, domain=domain, error=str(exc),
        )
        raise TaskDbReadError(f"list_tasks: {exc}") from exc
    return {"ok": True, "tasks": [_project(r) for r in rows]}


def _coerce_status(value: str) -> TaskStatus:
    try:
        return TaskStatus(value)
    except ValueError as exc:
        raise TaskDbReadError(f"unknown status: {value!r}") from exc


def _coerce_domain(value: str) -> TaskDomain:
    try:
        return TaskDomain(value)
    except ValueError as exc:
        raise TaskDbReadError(f"unknown domain: {value!r}") from exc


def _project(row: Any) -> dict[str, Any]:
    """Project a TaskRow to a JSON-safe subset. Only read-friendly fields."""
    if dataclasses.is_dataclass(row) and not isinstance(row, type):
        as_dict: dict[str, Any] = dataclasses.asdict(row)
    else:
        as_dict = dict(row)
    return {k: as_dict.get(k) for k in _PROJECTED_FIELDS}
