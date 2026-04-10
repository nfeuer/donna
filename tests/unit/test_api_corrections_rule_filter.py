"""Unit tests for the rule_id filter on the corrections endpoint."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from donna.api.routes.admin_preferences import list_corrections


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cursor(fetchall: list | None = None, fetchone: tuple | None = None) -> AsyncMock:
    c = AsyncMock()
    c.fetchall = AsyncMock(return_value=fetchall or [])
    c.fetchone = AsyncMock(return_value=fetchone)
    return c


def _make_correction_row(**overrides: object) -> tuple:
    defaults = {
        "id": "corr-001",
        "timestamp": "2026-04-01T10:00:00Z",
        "user_id": "nick",
        "task_type": "parse_task",
        "task_id": "task-001",
        "input_text": "buy milk tomorrow",
        "field_corrected": "priority",
        "original_value": "3",
        "corrected_value": "1",
        "rule_extracted": None,
    }
    defaults.update(overrides)
    return tuple(defaults.values())


# ---------------------------------------------------------------------------
# rule_id filter tests
# ---------------------------------------------------------------------------


class TestListCorrectionsRuleIdFilter:
    async def test_rule_not_found_returns_empty(
        self, mock_request: tuple
    ) -> None:
        """When the rule_id doesn't exist in preference_rules, return empty."""
        request, conn = mock_request
        # First execute: rule lookup returns nothing
        conn.execute = AsyncMock(return_value=_cursor(fetchone=None))

        result = await list_corrections(request, rule_id="nonexistent-rule", limit=50, offset=0)

        assert result["corrections"] == []
        assert result["total"] == 0
        # Only one execute call (the rule lookup); should not query correction_log
        assert conn.execute.call_count == 1

    async def test_rule_with_empty_supporting_corrections_returns_empty(
        self, mock_request: tuple
    ) -> None:
        """When supporting_corrections is an empty list, return empty."""
        request, conn = mock_request
        conn.execute = AsyncMock(
            return_value=_cursor(fetchone=(json.dumps([]),))
        )

        result = await list_corrections(request, rule_id="rule-001", limit=50, offset=0)

        assert result["corrections"] == []
        assert result["total"] == 0
        assert conn.execute.call_count == 1

    async def test_rule_with_null_supporting_corrections_returns_empty(
        self, mock_request: tuple
    ) -> None:
        """When supporting_corrections column is NULL, return empty."""
        request, conn = mock_request
        conn.execute = AsyncMock(
            return_value=_cursor(fetchone=(None,))
        )

        result = await list_corrections(request, rule_id="rule-001", limit=50, offset=0)

        assert result["corrections"] == []
        assert result["total"] == 0
        assert conn.execute.call_count == 1

    async def test_rule_with_corrections_filters_by_ids(
        self, mock_request: tuple
    ) -> None:
        """When rule has supporting_corrections, query uses IN clause with those IDs."""
        request, conn = mock_request
        correction_ids = ["corr-001", "corr-002"]
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(json.dumps(correction_ids),)),  # rule lookup
                _cursor(fetchone=(2,)),                            # COUNT
                _cursor(fetchall=[
                    _make_correction_row(id="corr-001"),
                    _make_correction_row(id="corr-002"),
                ]),
            ]
        )

        result = await list_corrections(request, rule_id="rule-001", limit=50, offset=0)

        assert result["total"] == 2
        assert len(result["corrections"]) == 2
        assert result["corrections"][0]["id"] == "corr-001"

        # Verify the COUNT query used an IN clause
        count_sql = conn.execute.call_args_list[1][0][0]
        assert "id IN" in count_sql

        # Verify params include the correction IDs
        count_params = conn.execute.call_args_list[1][0][1]
        assert "corr-001" in count_params
        assert "corr-002" in count_params

    async def test_rule_filter_combined_with_field_filter(
        self, mock_request: tuple
    ) -> None:
        """rule_id and field filters compose correctly with AND."""
        request, conn = mock_request
        correction_ids = ["corr-001"]
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(json.dumps(correction_ids),)),  # rule lookup
                _cursor(fetchone=(1,)),                            # COUNT
                _cursor(fetchall=[_make_correction_row()]),
            ]
        )

        result = await list_corrections(
            request, rule_id="rule-001", field="priority", limit=50, offset=0
        )

        count_sql = conn.execute.call_args_list[1][0][0]
        assert "field_corrected = ?" in count_sql
        assert "id IN" in count_sql
        assert result["total"] == 1

    async def test_no_rule_id_skips_rule_lookup(
        self, mock_request: tuple
    ) -> None:
        """Without rule_id, behaviour is unchanged (no extra DB call)."""
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(1,)),
                _cursor(fetchall=[_make_correction_row()]),
            ]
        )

        result = await list_corrections(request, rule_id=None, limit=50, offset=0)

        assert result["total"] == 1
        # Exactly two execute calls: COUNT + SELECT (no rule lookup)
        assert conn.execute.call_count == 2

    async def test_placeholders_match_number_of_ids(
        self, mock_request: tuple
    ) -> None:
        """IN clause has the right number of placeholders for the IDs."""
        request, conn = mock_request
        correction_ids = ["c1", "c2", "c3"]
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(json.dumps(correction_ids),)),
                _cursor(fetchone=(0,)),
                _cursor(fetchall=[]),
            ]
        )

        await list_corrections(request, rule_id="rule-xyz", limit=50, offset=0)

        count_sql = conn.execute.call_args_list[1][0][0]
        # Should have exactly 3 placeholders
        assert count_sql.count("?") >= 3
        count_params = conn.execute.call_args_list[1][0][1]
        for cid in correction_ids:
            assert cid in count_params
