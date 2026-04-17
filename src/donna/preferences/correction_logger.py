"""Correction logger for Donna preference learning.

Whenever a user overrides a task field — priority, domain, scheduled time,
etc. — the change is recorded in the `correction_log` table. This data
accumulates for future rule extraction (Phase 3 batch job).

Calendar sync time changes are already logged implicitly by
``CalendarSync._log_correction()``; this module provides the same
capability as a standalone reusable function so all other code paths
(Discord commands, SMS commands, future UI) can call a single entry point.

Schema (from Alembic migration 6c29a416f050_initial_schema.py):
    id            TEXT  PRIMARY KEY
    timestamp     TEXT  NOT NULL
    user_id       TEXT  NOT NULL
    task_type     TEXT  NOT NULL   -- parse_task, classify_priority, calendar_sync, …
    task_id       TEXT
    input_text    TEXT
    field_corrected TEXT
    original_value  TEXT
    corrected_value TEXT
    rule_extracted  INTEGER  DEFAULT 0

See docs/preferences.md and slices/slice_08_email_corrections.md.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from donna.skills.correction_cluster import CorrectionClusterDetector
    from donna.tasks.database import Database

logger = structlog.get_logger()


async def log_correction(
    db: Database,
    user_id: str,
    task_id: str,
    task_type: str,
    field: str,
    original: str,
    corrected: str,
    input_text: str = "",
    cluster_detector: CorrectionClusterDetector | None = None,
) -> None:
    """Record a user correction to a task field in the correction_log table.

    When ``cluster_detector`` is provided (typically wired at orchestrator
    startup), fire a synchronous ``scan_for_capability(task_type)`` after the
    INSERT commits. This is the F-7 fast path — users waiting for a
    correction cluster to flag a skill see the transition within seconds
    instead of waiting for the nightly cron invocation of
    :meth:`CorrectionClusterDetector.scan_once`.

    Args:
        db: Active database connection.
        user_id: The user who made the correction (e.g. "nick").
        task_id: The task UUID being corrected.
        task_type: The operation context (e.g. "classify_priority", "parse_task",
                   "calendar_sync", "discord_command").
        field: The field that was corrected (e.g. "priority", "domain",
               "scheduled_start").
        original: The value before the correction.
        corrected: The value after the correction.
        input_text: The raw user input that triggered the correction (e.g.
                    the Discord message text). Empty string if not applicable.
        cluster_detector: Optional detector used to fire a synchronous
                          per-capability scan after the row is committed.
                          Failures from the scan are logged but never raised
                          — the correction is persisted regardless.
    """
    conn = db.connection
    await conn.execute(
        """
        INSERT INTO correction_log
            (id, timestamp, user_id, task_type, task_id, input_text,
             field_corrected, original_value, corrected_value)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            datetime.now(tz=UTC).isoformat(),
            user_id,
            task_type,
            task_id,
            input_text,
            field,
            original,
            corrected,
        ),
    )
    await conn.commit()

    logger.info(
        "correction_logged",
        user_id=user_id,
        task_id=task_id,
        task_type=task_type,
        field=field,
        original=original,
        corrected=corrected,
    )

    if cluster_detector is not None:
        try:
            await cluster_detector.scan_for_capability(task_type)
        except Exception:
            logger.exception(
                "correction_cluster_scan_failed",
                task_id=task_id,
                task_type=task_type,
            )
