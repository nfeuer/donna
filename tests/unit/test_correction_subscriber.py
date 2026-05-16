"""Unit tests for CorrectionSubscriber."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from donna.preferences.correction_subscriber import CorrectionSubscriber

pytestmark = pytest.mark.asyncio


def _make_task(user_id: str = "nick", task_id: str = "task-1") -> MagicMock:
    task = MagicMock()
    task.user_id = user_id
    task.id = task_id
    return task


class TestCorrectionSubscriber:
    async def test_logs_correction_for_learnable_field(self) -> None:
        """A priority change with source logs a correction."""
        mock_db = MagicMock()
        sub = CorrectionSubscriber(mock_db)
        task = _make_task()
        previous = _make_task()

        with patch(
            "donna.preferences.correction_subscriber.log_correction",
            new_callable=AsyncMock,
        ) as mock_log:
            await sub.on_task_updated(
                task,
                previous=previous,
                changed_fields={"priority": (3, 5)},
                source="api",
            )

        mock_log.assert_called_once_with(
            db=mock_db,
            user_id="nick",
            task_id="task-1",
            task_type="api",
            field="priority",
            original="3",
            corrected="5",
            input_text="",
        )

    async def test_skips_non_learnable_field(self) -> None:
        """A status change is not logged as a correction."""
        mock_db = MagicMock()
        sub = CorrectionSubscriber(mock_db)
        task = _make_task()
        previous = _make_task()

        with patch(
            "donna.preferences.correction_subscriber.log_correction",
            new_callable=AsyncMock,
        ) as mock_log:
            await sub.on_task_updated(
                task,
                previous=previous,
                changed_fields={"status": ("backlog", "done")},
                source="api",
            )

        mock_log.assert_not_called()

    async def test_skips_when_source_is_none(self) -> None:
        """System-initiated updates (source=None) are ignored."""
        mock_db = MagicMock()
        sub = CorrectionSubscriber(mock_db)
        task = _make_task()
        previous = _make_task()

        with patch(
            "donna.preferences.correction_subscriber.log_correction",
            new_callable=AsyncMock,
        ) as mock_log:
            await sub.on_task_updated(
                task,
                previous=previous,
                changed_fields={"priority": (2, 4)},
                source=None,
            )

        mock_log.assert_not_called()

    async def test_logs_multiple_fields_separately(self) -> None:
        """Multi-field edit produces one correction per changed field."""
        mock_db = MagicMock()
        sub = CorrectionSubscriber(mock_db)
        task = _make_task()
        previous = _make_task()

        with patch(
            "donna.preferences.correction_subscriber.log_correction",
            new_callable=AsyncMock,
        ) as mock_log:
            await sub.on_task_updated(
                task,
                previous=previous,
                changed_fields={
                    "priority": (2, 4),
                    "domain": ("personal", "work"),
                    "status": ("backlog", "done"),  # not learnable
                },
                source="discord_modal",
            )

        assert mock_log.call_count == 2
        fields_logged = {c.kwargs["field"] for c in mock_log.call_args_list}
        assert fields_logged == {"priority", "domain"}

    async def test_none_original_becomes_empty_string(self) -> None:
        """None values are stringified to empty string."""
        mock_db = MagicMock()
        sub = CorrectionSubscriber(mock_db)
        task = _make_task()
        previous = _make_task()

        with patch(
            "donna.preferences.correction_subscriber.log_correction",
            new_callable=AsyncMock,
        ) as mock_log:
            await sub.on_task_updated(
                task,
                previous=previous,
                changed_fields={"deadline": (None, "2026-06-01")},
                source="api",
            )

        mock_log.assert_called_once()
        assert mock_log.call_args.kwargs["original"] == ""
        assert mock_log.call_args.kwargs["corrected"] == "2026-06-01"
