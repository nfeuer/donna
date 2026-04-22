"""Unit tests for the admin preferences endpoints."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from donna.api.routes.admin_preferences import (
    RuleToggleBody,
    _parse_json_field,
    list_corrections,
    list_preference_rules,
    preference_stats,
    toggle_preference_rule,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cursor(fetchall: list | None = None, fetchone: tuple | None = None) -> AsyncMock:
    c = AsyncMock()
    c.fetchall = AsyncMock(return_value=fetchall or [])
    c.fetchone = AsyncMock(return_value=fetchone)
    return c


def _make_rule_row(**overrides: object) -> tuple:
    """Build a learned_preferences row (11 columns)."""
    defaults = {
        "id": "rule-001",
        "user_id": "nick",
        "rule_type": "field_default",
        "rule_text": "When domain is 'work', default priority to 2",
        "confidence": 0.85,
        "condition": json.dumps({"domain": "work"}),
        "action": json.dumps({"set_priority": 2}),
        "supporting_corrections": json.dumps(["corr-1", "corr-2"]),
        "enabled": 1,
        "created_at": "2026-04-01T10:00:00Z",
        "disabled_at": None,
    }
    defaults.update(overrides)
    return tuple(defaults.values())


def _make_correction_row(**overrides: object) -> tuple:
    """Build a correction_log row (10 columns)."""
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
# _parse_json_field
# ---------------------------------------------------------------------------


class TestParseJsonField:
    def test_parses_json_string(self) -> None:
        assert _parse_json_field('{"key": "val"}') == {"key": "val"}

    def test_returns_none_for_none(self) -> None:
        assert _parse_json_field(None) is None

    def test_returns_dict_as_is(self) -> None:
        d = {"key": "val"}
        assert _parse_json_field(d) is d

    def test_returns_invalid_json_as_string(self) -> None:
        assert _parse_json_field("not-json") == "not-json"


# ---------------------------------------------------------------------------
# list_preference_rules
# ---------------------------------------------------------------------------


class TestListPreferenceRules:
    async def test_empty_result(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[_cursor(fetchone=(0,)), _cursor()]
        )
        result = await list_preference_rules(request)
        assert result["total"] == 0
        assert result["rules"] == []

    async def test_returns_rules_with_parsed_json(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(1,)),
                _cursor(fetchall=[_make_rule_row()]),
            ]
        )
        result = await list_preference_rules(request)
        rule = result["rules"][0]
        assert rule["id"] == "rule-001"
        assert rule["condition"] == {"domain": "work"}
        assert rule["action"] == {"set_priority": 2}
        assert rule["supporting_corrections"] == ["corr-1", "corr-2"]
        assert rule["enabled"] is True

    async def test_filter_by_enabled(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[_cursor(fetchone=(0,)), _cursor()]
        )
        await list_preference_rules(request, enabled=True)
        sql = conn.execute.call_args_list[0][0][0]
        assert "enabled = ?" in sql

    async def test_filter_by_rule_type(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[_cursor(fetchone=(0,)), _cursor()]
        )
        await list_preference_rules(request, rule_type="field_default")
        sql = conn.execute.call_args_list[0][0][0]
        assert "rule_type = ?" in sql

    async def test_non_list_supporting_corrections(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(1,)),
                _cursor(fetchall=[_make_rule_row(supporting_corrections=json.dumps("not-a-list"))]),
            ]
        )
        result = await list_preference_rules(request)
        assert result["rules"][0]["supporting_corrections"] == []


# ---------------------------------------------------------------------------
# toggle_preference_rule
# ---------------------------------------------------------------------------


class TestTogglePreferenceRule:
    async def test_enable_rule(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=("rule-001",)),  # exists check
                AsyncMock(),  # UPDATE
                _cursor(fetchone=_make_rule_row(enabled=1, disabled_at=None)),  # re-fetch
            ]
        )
        result = await toggle_preference_rule(
            request, rule_id="rule-001", body=RuleToggleBody(enabled=True)
        )
        assert result["enabled"] is True
        assert result["disabled_at"] is None

    async def test_disable_rule(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=("rule-001",)),
                AsyncMock(),
                _cursor(fetchone=_make_rule_row(enabled=0, disabled_at="2026-04-06T10:00:00Z")),
            ]
        )
        result = await toggle_preference_rule(
            request, rule_id="rule-001", body=RuleToggleBody(enabled=False)
        )
        assert result["enabled"] is False
        assert result["disabled_at"] is not None

    async def test_not_found_raises_404(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(return_value=_cursor(fetchone=None))
        with pytest.raises(HTTPException) as exc_info:
            await toggle_preference_rule(
                request, rule_id="nonexistent", body=RuleToggleBody(enabled=True)
            )
        assert exc_info.value.status_code == 404

    async def test_commit_called(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=("rule-001",)),
                AsyncMock(),
                _cursor(fetchone=_make_rule_row()),
            ]
        )
        await toggle_preference_rule(
            request, rule_id="rule-001", body=RuleToggleBody(enabled=True)
        )
        conn.commit.assert_called_once()


# ---------------------------------------------------------------------------
# list_corrections
# ---------------------------------------------------------------------------


class TestListCorrections:
    async def test_empty_result(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[_cursor(fetchone=(0,)), _cursor()]
        )
        result = await list_corrections(request)
        assert result["total"] == 0
        assert result["corrections"] == []

    async def test_returns_corrections(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(1,)),
                _cursor(fetchall=[_make_correction_row()]),
            ]
        )
        result = await list_corrections(request)
        corr = result["corrections"][0]
        assert corr["id"] == "corr-001"
        assert corr["field_corrected"] == "priority"

    async def test_filter_by_field(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[_cursor(fetchone=(0,)), _cursor()]
        )
        await list_corrections(request, field="priority")
        sql = conn.execute.call_args_list[0][0][0]
        assert "field_corrected = ?" in sql

    async def test_filter_by_task_type(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[_cursor(fetchone=(0,)), _cursor()]
        )
        await list_corrections(request, task_type="parse_task")
        sql = conn.execute.call_args_list[0][0][0]
        assert "task_type = ?" in sql


# ---------------------------------------------------------------------------
# preference_stats
# ---------------------------------------------------------------------------


class TestPreferenceStats:
    async def test_empty_db(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(0,)),    # total rules
                _cursor(fetchone=(0,)),    # active rules
                _cursor(fetchone=(None,)), # avg confidence
                _cursor(fetchone=(0,)),    # total corrections
                _cursor(),                 # top fields
            ]
        )
        result = await preference_stats(request)
        assert result["total_rules"] == 0
        assert result["active_rules"] == 0
        assert result["disabled_rules"] == 0
        assert result["avg_confidence"] is None
        assert result["total_corrections"] == 0
        assert result["top_fields"] == []

    async def test_with_data(self, mock_request: tuple) -> None:
        request, conn = mock_request
        conn.execute = AsyncMock(
            side_effect=[
                _cursor(fetchone=(10,)),   # total rules
                _cursor(fetchone=(8,)),    # active rules
                _cursor(fetchone=(0.82,)), # avg confidence
                _cursor(fetchone=(50,)),   # total corrections
                _cursor(fetchall=[("priority", 20), ("domain", 15)]),
            ]
        )
        result = await preference_stats(request)
        assert result["total_rules"] == 10
        assert result["active_rules"] == 8
        assert result["disabled_rules"] == 2
        assert result["avg_confidence"] == 0.82
        assert result["total_corrections"] == 50
        assert len(result["top_fields"]) == 2
        assert result["top_fields"][0]["field"] == "priority"
