"""Two-pass task deduplication.

Pass 1: rapidfuzz token-sort ratio against active tasks.
Pass 2: LLM semantic comparison via dedup_check task type.

Thresholds (from slice_06_dedup_cost.md):
  ≥85  → auto-flag as duplicate, skip LLM
  70–84 → proceed to LLM semantic check
  <70  → clearly different, no action
"""

from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path

import structlog
from rapidfuzz import fuzz

from donna.logging.invocation_logger import InvocationLogger, InvocationMetadata
from donna.models.router import ModelRouter
from donna.models.validation import validate_output
from donna.tasks.database import Database, TaskRow
from donna.tasks.db_models import TaskStatus

logger = structlog.get_logger()

TASK_TYPE = "dedup_check"

# Fuzzy score thresholds
_HIGH_THRESHOLD = 85
_MID_THRESHOLD = 70

# Active statuses — only compare against non-terminal tasks
_ACTIVE_STATUSES = (
    TaskStatus.BACKLOG,
    TaskStatus.SCHEDULED,
    TaskStatus.IN_PROGRESS,
    TaskStatus.BLOCKED,
    TaskStatus.WAITING_INPUT,
)


class DuplicateDetectedError(Exception):
    """Raised when a new task is a duplicate of an existing one.

    Callers (e.g. DonnaBot) should catch this, prompt the user for a
    merge/keep/update decision, and proceed accordingly.
    """

    def __init__(
        self,
        existing_task: TaskRow,
        new_title: str,
        verdict: str,
        fuzzy_score: float,
        reasoning: str,
    ) -> None:
        self.existing_task = existing_task
        self.new_title = new_title
        self.verdict = verdict
        self.fuzzy_score = fuzzy_score
        self.reasoning = reasoning
        super().__init__(
            f"Duplicate detected: '{new_title}' matches '{existing_task.title}' "
            f"(score={fuzzy_score:.0f}, verdict={verdict})"
        )


def _render_dedup_template(
    template: str,
    task_a: TaskRow,
    new_title: str,
    new_description: str | None,
    new_domain: str,
    fuzzy_score: float,
) -> str:
    """Substitute template variables into the dedup_check prompt."""
    return (
        template
        .replace("{{ task_a_title }}", task_a.title)
        .replace("{{ task_a_description }}", task_a.description or "")
        .replace("{{ task_a_created_at }}", task_a.created_at or "")
        .replace("{{ task_a_domain }}", task_a.domain)
        .replace("{{ task_b_title }}", new_title)
        .replace("{{ task_b_description }}", new_description or "")
        .replace("{{ task_b_domain }}", new_domain)
        .replace("{{ fuzzy_score }}", f"{fuzzy_score:.0f}")
    )


def _input_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class Deduplicator:
    """Two-pass task deduplication engine.

    Pass 1 — fuzzy: compare new title against active tasks using
    rapidfuzz.fuzz.token_sort_ratio.

    Pass 2 — LLM: for mid-range scores (70–84), send both tasks to the
    LLM via the dedup_check task type for semantic comparison.

    Raises DuplicateDetectedError when verdict is same or related.
    """

    def __init__(
        self,
        db: Database,
        router: ModelRouter,
        invocation_logger: InvocationLogger,
        project_root: Path,
    ) -> None:
        self._db = db
        self._router = router
        self._invocation_logger = invocation_logger
        self._project_root = project_root

    async def check(
        self,
        new_title: str,
        new_description: str | None,
        new_domain: str,
        user_id: str,
    ) -> None:
        """Check if new_title duplicates any active task for user_id.

        Raises DuplicateDetectedError if a duplicate is found.
        Returns normally if no duplicate detected.
        """
        active_tasks = await self._get_active_tasks(user_id)
        if not active_tasks:
            return

        best_task, best_score = _find_best_fuzzy_match(new_title, active_tasks)

        logger.debug(
            "dedup_fuzzy_pass",
            new_title=new_title,
            best_candidate=best_task.title,
            fuzzy_score=best_score,
            user_id=user_id,
        )

        if best_score < _MID_THRESHOLD:
            logger.info(
                "dedup_check_result",
                verdict="different",
                method="fuzzy",
                fuzzy_score=best_score,
                user_id=user_id,
            )
            return

        if best_score >= _HIGH_THRESHOLD:
            logger.info(
                "dedup_check_result",
                verdict="same",
                method="fuzzy_high",
                fuzzy_score=best_score,
                candidate_task_id=best_task.id,
                user_id=user_id,
            )
            raise DuplicateDetectedError(
                existing_task=best_task,
                new_title=new_title,
                verdict="same",
                fuzzy_score=best_score,
                reasoning=f"Fuzzy token-sort score {best_score:.0f} ≥ {_HIGH_THRESHOLD}",
            )

        # Mid-range: call LLM for semantic comparison
        verdict, reasoning = await self._llm_check(
            best_task, new_title, new_description, new_domain, best_score, user_id
        )

        logger.info(
            "dedup_check_result",
            verdict=verdict,
            method="llm",
            fuzzy_score=best_score,
            candidate_task_id=best_task.id,
            user_id=user_id,
        )

        if verdict in ("same", "related"):
            raise DuplicateDetectedError(
                existing_task=best_task,
                new_title=new_title,
                verdict=verdict,
                fuzzy_score=best_score,
                reasoning=reasoning,
            )

    async def _get_active_tasks(self, user_id: str) -> list[TaskRow]:
        """Fetch all non-terminal tasks for the user."""
        tasks: list[TaskRow] = []
        for status in _ACTIVE_STATUSES:
            batch = await self._db.list_tasks(user_id=user_id, status=status)
            tasks.extend(batch)
        return tasks

    async def _llm_check(
        self,
        existing_task: TaskRow,
        new_title: str,
        new_description: str | None,
        new_domain: str,
        fuzzy_score: float,
        user_id: str,
    ) -> tuple[str, str]:
        """Call the LLM to determine if tasks are same/related/different.

        Returns (verdict, reasoning).
        """
        template = self._router.get_prompt_template(TASK_TYPE)
        prompt = _render_dedup_template(
            template,
            task_a=existing_task,
            new_title=new_title,
            new_description=new_description,
            new_domain=new_domain,
            fuzzy_score=fuzzy_score,
        )

        response, metadata = await self._router.complete(prompt, task_type=TASK_TYPE)
        schema = self._router.get_output_schema(TASK_TYPE)
        validated = validate_output(response, schema)

        await self._invocation_logger.log(
            InvocationMetadata(
                task_type=TASK_TYPE,
                model_alias=self._router._models_config.routing[TASK_TYPE].model,
                model_actual=metadata.model_actual,
                input_hash=_input_hash(f"{existing_task.title}|{new_title}"),
                latency_ms=metadata.latency_ms,
                tokens_in=metadata.tokens_in,
                tokens_out=metadata.tokens_out,
                cost_usd=metadata.cost_usd,
                estimated_tokens_in=metadata.estimated_tokens_in,
                overflow_escalated=metadata.overflow_escalated,
                user_id=user_id,
                output=validated,
            )
        )

        return validated["verdict"], validated["reasoning"]


def _find_best_fuzzy_match(
    new_title: str, candidates: list[TaskRow]
) -> tuple[TaskRow, float]:
    """Return the candidate with the highest token-sort ratio score."""
    best_task = candidates[0]
    best_score = float(fuzz.token_sort_ratio(new_title, candidates[0].title))
    for task in candidates[1:]:
        score = float(fuzz.token_sort_ratio(new_title, task.title))
        if score > best_score:
            best_score = score
            best_task = task
    return best_task, best_score
