"""Unit tests for the correction logger."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

from donna.preferences.correction_logger import log_correction


class TestLogCorrection:
    async def test_log_correction_inserts_row(self) -> None:
        """log_correction executes an INSERT into correction_log."""
        mock_conn = AsyncMock()
        mock_db = MagicMock()
        mock_db.connection = mock_conn

        await log_correction(
            db=mock_db,
            user_id="nick",
            task_id="task-abc",
            task_type="classify_priority",
            field="priority",
            original="2",
            corrected="4",
            input_text="change priority to 4",
        )

        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args
        sql = call_args[0][0]
        params = call_args[0][1]

        assert "INSERT INTO correction_log" in sql
        # SQL order: id, timestamp, user_id, task_type, task_id, input_text,
        #            field_corrected, original_value, corrected_value
        assert params[2] == "nick"          # user_id
        assert params[3] == "classify_priority"  # task_type
        assert params[4] == "task-abc"      # task_id
        assert params[5] == "change priority to 4"  # input_text
        assert params[6] == "priority"      # field_corrected
        assert params[7] == "2"             # original_value
        assert params[8] == "4"             # corrected_value

    async def test_log_correction_captures_all_fields(self) -> None:
        """All fields are stored in the correct positions."""
        mock_conn = AsyncMock()
        mock_db = MagicMock()
        mock_db.connection = mock_conn

        await log_correction(
            db=mock_db,
            user_id="nick",
            task_id="task-xyz",
            task_type="discord_command",
            field="domain",
            original="PERSONAL",
            corrected="WORK",
            input_text="move to work domain",
        )

        call_args = mock_conn.execute.call_args
        params = call_args[0][1]

        # id (UUID), timestamp, user_id, task_type, task_id, input_text,
        # field_corrected, original_value, corrected_value
        assert len(params) == 9
        # SQL order: id, timestamp, user_id, task_type, task_id, input_text,
        #            field_corrected, original_value, corrected_value
        assert uuid.UUID(params[0])  # Should not raise
        assert params[6] == "domain"
        assert params[7] == "PERSONAL"
        assert params[8] == "WORK"

    async def test_log_correction_commits_transaction(self) -> None:
        """log_correction commits after inserting."""
        mock_conn = AsyncMock()
        mock_db = MagicMock()
        mock_db.connection = mock_conn

        await log_correction(
            db=mock_db,
            user_id="nick",
            task_id="t1",
            task_type="calendar_sync",
            field="scheduled_start",
            original="2026-03-20T10:00:00",
            corrected="2026-03-21T10:00:00",
        )

        mock_conn.commit.assert_called_once()

    async def test_log_correction_default_empty_input_text(self) -> None:
        """input_text defaults to empty string."""
        mock_conn = AsyncMock()
        mock_db = MagicMock()
        mock_db.connection = mock_conn

        await log_correction(
            db=mock_db,
            user_id="nick",
            task_id="t2",
            task_type="parse_task",
            field="priority",
            original="3",
            corrected="5",
            # input_text not provided — defaults to ""
        )

        call_args = mock_conn.execute.call_args
        params = call_args[0][1]
        assert params[5] == ""  # input_text is empty (index 5 in SQL order)
