"""Event-driven correction logging subscriber.

Subscribes to ``task_updated`` events on the :class:`TaskEventBus` and
logs user-initiated field changes to the ``correction_log`` table via
:func:`log_correction`. System-initiated updates (``source=None``) are
ignored.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from donna.preferences.correction_logger import log_correction

if TYPE_CHECKING:
    from donna.tasks.database import Database, TaskRow

logger = structlog.get_logger()

LEARNABLE_FIELDS: frozenset[str] = frozenset({
    "priority",
    "domain",
    "title",
    "description",
    "scheduled_start",
    "deadline",
    "estimated_duration",
    "tags",
})


class CorrectionSubscriber:
    """Logs user-initiated task field changes as preference corrections."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def on_task_updated(
        self,
        task: TaskRow,
        *,
        previous: TaskRow | None,
        changed_fields: dict[str, tuple[Any, Any]],
        source: str | None,
        **_: Any,
    ) -> None:
        if source is None:
            return

        for field, (original, corrected) in changed_fields.items():
            if field not in LEARNABLE_FIELDS:
                continue
            try:
                await log_correction(
                    db=self._db,
                    user_id=task.user_id,
                    task_id=task.id,
                    task_type=source,
                    field=field,
                    original=str(original) if original is not None else "",
                    corrected=str(corrected) if corrected is not None else "",
                    input_text="",
                )
            except Exception:
                logger.exception(
                    "correction_subscriber_log_failed",
                    task_id=task.id,
                    field=field,
                    source=source,
                )
