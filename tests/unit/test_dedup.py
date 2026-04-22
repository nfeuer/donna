"""Tests for the two-pass deduplication engine."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from donna.config import ModelConfig, ModelsConfig, RoutingEntry, TaskTypeEntry, TaskTypesConfig
from donna.models.router import ModelRouter
from donna.models.types import CompletionMetadata
from donna.tasks.database import TaskRow
from donna.tasks.dedup import (
    _HIGH_THRESHOLD,
    _MID_THRESHOLD,
    Deduplicator,
    DuplicateDetectedError,
    _find_best_fuzzy_match,
    _render_dedup_template,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task_row(**overrides: object) -> TaskRow:
    defaults = dict(
        id="task-001",
        user_id="nick",
        title="Get oil change",
        description=None,
        domain="personal",
        priority=2,
        status="backlog",
        estimated_duration=None,
        deadline=None,
        deadline_type="none",
        scheduled_start=None,
        actual_start=None,
        completed_at=None,
        recurrence=None,
        dependencies=None,
        parent_task=None,
        prep_work_flag=False,
        prep_work_instructions=None,
        agent_eligible=False,
        assigned_agent=None,
        agent_status=None,
        tags=None,
        notes=None,
        reschedule_count=0,
        created_at="2026-03-01T10:00:00",
        created_via="discord",
        estimated_cost=None,
        calendar_event_id=None,
        donna_managed=False,
        nudge_count=0,
        quality_score=None,
    )
    defaults.update(overrides)
    return TaskRow(**defaults)


def _make_metadata() -> CompletionMetadata:
    return CompletionMetadata(
        latency_ms=200,
        tokens_in=100,
        tokens_out=60,
        cost_usd=0.0012,
        model_actual="anthropic/claude-sonnet-4-20250514",
    )


def _make_router() -> ModelRouter:
    models_config = ModelsConfig(
        models={
            "parser": ModelConfig(provider="anthropic", model="claude-sonnet-4-20250514"),
            "reasoner": ModelConfig(provider="anthropic", model="claude-sonnet-4-20250514"),
        },
        routing={
            "dedup_check": RoutingEntry(model="parser", fallback="reasoner", confidence_threshold=0.7),
        },
    )
    task_types_config = TaskTypesConfig(
        task_types={
            "dedup_check": TaskTypeEntry(
                description="Dedup check",
                model="parser",
                prompt_template="prompts/dedup_check.md",
                output_schema="schemas/dedup_output.json",
            ),
        }
    )
    return ModelRouter(models_config, task_types_config, PROJECT_ROOT)


def _dedup_response(verdict: str = "same") -> dict:
    return {
        "verdict": verdict,
        "confidence": 0.9,
        "reasoning": f"These tasks are {verdict}.",
        "suggested_action": "merge" if verdict == "same" else ("link" if verdict == "related" else "none"),
    }


# ---------------------------------------------------------------------------
# Unit tests for fuzzy scoring bucketing
# Directly verify how the Deduplicator routes based on score thresholds.
# We patch fuzz.token_sort_ratio to control the score precisely.
# ---------------------------------------------------------------------------


class TestFuzzyScoreBucketing:
    """Test that the Deduplicator correctly routes to the right path based on score."""

    def _make_deduplicator(self, tasks: list[TaskRow]) -> tuple[Deduplicator, AsyncMock, AsyncMock]:
        db = MagicMock()
        db.list_tasks = AsyncMock(return_value=tasks)
        router = _make_router()
        router.complete = AsyncMock(return_value=(_dedup_response("same"), _make_metadata()))
        inv_logger = AsyncMock()
        inv_logger.log = AsyncMock(return_value="inv-001")
        dedup = Deduplicator(db=db, router=router, invocation_logger=inv_logger, project_root=PROJECT_ROOT)
        return dedup, router, inv_logger

    async def test_score_above_85_raises_without_llm_call(self) -> None:
        """Score ≥85 → DuplicateDetectedError raised, LLM not called."""
        task = _make_task_row(title="Get oil change")
        dedup, router, _ = self._make_deduplicator([task])

        with (
            patch("donna.tasks.dedup.fuzz.token_sort_ratio", return_value=90),
            pytest.raises(DuplicateDetectedError) as exc_info,
        ):
            await dedup.check("Oil change needed", None, "personal", "nick")

        assert exc_info.value.verdict == "same"
        assert exc_info.value.fuzzy_score == 90
        assert exc_info.value.existing_task.id == "task-001"
        router.complete.assert_not_called()

    async def test_score_exactly_85_raises_without_llm_call(self) -> None:
        """Score exactly at 85 → high threshold, no LLM."""
        task = _make_task_row(title="Get oil change")
        dedup, router, _ = self._make_deduplicator([task])

        with (
            patch("donna.tasks.dedup.fuzz.token_sort_ratio", return_value=85),
            pytest.raises(DuplicateDetectedError),
        ):
            await dedup.check("Oil change needed", None, "personal", "nick")

        router.complete.assert_not_called()

    async def test_score_below_70_no_error_no_llm(self) -> None:
        """Score <70 → no error, no LLM call."""
        task = _make_task_row(title="Get oil change")
        dedup, router, _ = self._make_deduplicator([task])

        with patch("donna.tasks.dedup.fuzz.token_sort_ratio", return_value=50):
            await dedup.check("Buy groceries", None, "personal", "nick")

        router.complete.assert_not_called()

    async def test_score_exactly_70_calls_llm(self) -> None:
        """Score exactly 70 → mid range → LLM called."""
        task = _make_task_row(title="Get oil change")
        dedup, router, _inv_logger = self._make_deduplicator([task])

        with (
            patch("donna.tasks.dedup.fuzz.token_sort_ratio", return_value=70),
            pytest.raises(DuplicateDetectedError),
        ):
            await dedup.check("Oil change for car", None, "personal", "nick")

        router.complete.assert_called_once()

    async def test_mid_range_llm_different_no_error(self) -> None:
        """Mid-range score, LLM returns 'different' → no error raised."""
        task = _make_task_row(title="Oil change for lawn mower")
        dedup, router, _ = self._make_deduplicator([task])
        router.complete = AsyncMock(return_value=(_dedup_response("different"), _make_metadata()))

        with patch("donna.tasks.dedup.fuzz.token_sort_ratio", return_value=75):
            await dedup.check("Oil change for car", None, "personal", "nick")

        router.complete.assert_called_once()

    async def test_mid_range_llm_related_raises(self) -> None:
        """Mid-range score, LLM returns 'related' → DuplicateDetectedError with verdict='related'."""
        task = _make_task_row(title="Oil change for lawn mower")
        dedup, router, _ = self._make_deduplicator([task])
        router.complete = AsyncMock(return_value=(_dedup_response("related"), _make_metadata()))

        with (
            patch("donna.tasks.dedup.fuzz.token_sort_ratio", return_value=75),
            pytest.raises(DuplicateDetectedError) as exc_info,
        ):
            await dedup.check("Oil change for car", None, "personal", "nick")

        assert exc_info.value.verdict == "related"

    async def test_mid_range_llm_same_raises(self) -> None:
        """Mid-range score, LLM returns 'same' → DuplicateDetectedError."""
        task = _make_task_row(title="Oil change for lawn mower")
        dedup, router, inv_logger = self._make_deduplicator([task])
        router.complete = AsyncMock(return_value=(_dedup_response("same"), _make_metadata()))

        with (
            patch("donna.tasks.dedup.fuzz.token_sort_ratio", return_value=78),
            pytest.raises(DuplicateDetectedError) as exc_info,
        ):
            await dedup.check("Oil change for car", None, "personal", "nick")

        assert exc_info.value.verdict == "same"
        inv_logger.log.assert_called_once()

    async def test_no_active_tasks_no_check(self) -> None:
        """No active tasks → dedup skipped."""
        db = MagicMock()
        db.list_tasks = AsyncMock(return_value=[])
        router = _make_router()
        router.complete = AsyncMock()
        inv_logger = AsyncMock()
        dedup = Deduplicator(db=db, router=router, invocation_logger=inv_logger, project_root=PROJECT_ROOT)

        await dedup.check("Get oil change", None, "personal", "nick")
        router.complete.assert_not_called()

    async def test_dedup_logs_invocation_on_llm_path(self) -> None:
        """LLM path logs invocation with correct metadata."""
        task = _make_task_row(title="Oil change for lawn mower")
        dedup, router, inv_logger = self._make_deduplicator([task])
        router.complete = AsyncMock(return_value=(_dedup_response("different"), _make_metadata()))

        with patch("donna.tasks.dedup.fuzz.token_sort_ratio", return_value=75):
            await dedup.check("Oil change for car", None, "personal", "nick")

        inv_logger.log.assert_called_once()
        log_call = inv_logger.log.call_args[0][0]
        assert log_call.task_type == "dedup_check"
        assert log_call.user_id == "nick"
        assert log_call.cost_usd == 0.0012


# ---------------------------------------------------------------------------
# Acceptance criteria: verify actual fuzzy scores for the specified pairs
# ---------------------------------------------------------------------------


class TestAcceptanceCriteria:
    """Verify the acceptance criteria string pairs produce expected bucketing.

    Uses the actual scorer (no mocking) to confirm real-world behavior.
    """

    def test_identical_task_scores_100(self) -> None:
        """Identical title → 100% score."""
        task = _make_task_row(title="Get oil change")
        _, score = _find_best_fuzzy_match("Get oil change", [task])
        assert score == 100.0

    def test_same_words_different_order_scores_high(self) -> None:
        """Same words, reordered → token_sort_ratio should be 100%."""
        task = _make_task_row(title="change oil get")
        _, score = _find_best_fuzzy_match("get oil change", [task])
        assert score == 100.0

    def test_buy_groceries_vs_oil_change_scores_low(self) -> None:
        """Clearly different tasks → score well below 70."""
        task = _make_task_row(title="Buy groceries")
        _, score = _find_best_fuzzy_match("Get oil change", [task])
        assert score < 70

    def test_picks_best_of_multiple_candidates(self) -> None:
        """Returns the task with the highest score."""
        tasks = [
            _make_task_row(id="a", title="Buy cat food"),
            _make_task_row(id="b", title="Oil change needed"),  # closest to query
            _make_task_row(id="c", title="Schedule dentist"),
        ]
        # "Oil change needed" and "Get oil change" share 2/3 tokens
        best, _ = _find_best_fuzzy_match("Oil change needed", tasks)
        assert best.id == "b"


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------


class TestRenderDedupTemplate:
    def test_fills_all_variables(self) -> None:
        template = (
            "{{ task_a_title }} | {{ task_a_description }} | {{ task_a_created_at }} | "
            "{{ task_a_domain }} | {{ task_b_title }} | {{ task_b_description }} | "
            "{{ task_b_domain }} | {{ fuzzy_score }}"
        )
        task_a = _make_task_row(
            title="Get oil change",
            description="Car needs oil change",
            domain="personal",
            created_at="2026-03-01T10:00:00",
        )
        result = _render_dedup_template(
            template,
            task_a=task_a,
            new_title="Oil change needed",
            new_description="Need oil change",
            new_domain="personal",
            fuzzy_score=90.0,
        )
        assert "Get oil change" in result
        assert "Car needs oil change" in result
        assert "Oil change needed" in result
        assert "90" in result

    def test_handles_none_description(self) -> None:
        template = "{{ task_a_description }} | {{ task_b_description }}"
        task_a = _make_task_row(description=None)
        result = _render_dedup_template(
            template,
            task_a=task_a,
            new_title="title",
            new_description=None,
            new_domain="personal",
            fuzzy_score=75.0,
        )
        # None → empty string
        assert "None" not in result


# ---------------------------------------------------------------------------
# Threshold constants
# ---------------------------------------------------------------------------


class TestThresholds:
    def test_high_threshold_is_85(self) -> None:
        assert _HIGH_THRESHOLD == 85

    def test_mid_threshold_is_70(self) -> None:
        assert _MID_THRESHOLD == 70
