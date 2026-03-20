"""Input parsing pipeline — natural language to structured task.

Takes raw text input, renders the prompt template, calls the model
router, validates the response, logs the invocation, and returns
a typed TaskParseResult. See docs/task-system.md.
"""

from __future__ import annotations

import dataclasses
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from donna.logging.invocation_logger import InvocationLogger, InvocationMetadata
from donna.models.router import ModelRouter
from donna.models.types import CompletionMetadata
from donna.models.validation import validate_output
from donna.tasks.dedup import Deduplicator, DuplicateDetectedError  # noqa: F401 — re-exported

logger = structlog.get_logger()

TASK_TYPE = "parse_task"


@dataclasses.dataclass(frozen=True)
class TaskParseResult:
    """Structured output from natural language task parsing."""

    title: str
    description: str | None
    domain: str
    priority: int
    deadline: str | None
    deadline_type: str
    estimated_duration: int
    recurrence: str | None
    tags: list[str]
    prep_work_flag: bool
    agent_eligible: bool
    confidence: float


def _render_template(template: str, user_input: str) -> str:
    """Fill template variables with current context."""
    now = datetime.now(timezone.utc)
    return (
        template
        .replace("{{ current_date }}", now.strftime("%Y-%m-%d"))
        .replace("{{ current_time }}", now.strftime("%H:%M %Z"))
        .replace("{{ user_input }}", user_input)
    )


def _input_hash(text: str) -> str:
    """SHA-256 hash of input for dedup and invocation logging."""
    return hashlib.sha256(text.encode()).hexdigest()


def _to_parse_result(data: dict[str, Any]) -> TaskParseResult:
    """Convert a validated dict to a TaskParseResult dataclass."""
    return TaskParseResult(
        title=data["title"],
        description=data.get("description"),
        domain=data["domain"],
        priority=data["priority"],
        deadline=data.get("deadline"),
        deadline_type=data["deadline_type"],
        estimated_duration=data["estimated_duration"],
        recurrence=data.get("recurrence"),
        tags=data.get("tags", []),
        prep_work_flag=data.get("prep_work_flag", False),
        agent_eligible=data.get("agent_eligible", False),
        confidence=data["confidence"],
    )


class InputParser:
    """Parses natural language input into structured tasks.

    Orchestrates: template rendering → model call → validation → logging.
    """

    def __init__(
        self,
        router: ModelRouter,
        invocation_logger: InvocationLogger,
        project_root: Path,
        deduplicator: Deduplicator | None = None,
    ) -> None:
        self._router = router
        self._invocation_logger = invocation_logger
        self._project_root = project_root
        self._deduplicator = deduplicator

    async def parse(
        self,
        raw_text: str,
        user_id: str,
        channel: str = "discord",
    ) -> TaskParseResult:
        """Parse raw text into a structured TaskParseResult.

        Args:
            raw_text: Natural language task input from the user.
            user_id: ID of the user who submitted the input.
            channel: Input channel (discord, sms, etc.).

        Returns:
            Validated TaskParseResult.

        Raises:
            ValidationError: If the LLM response fails schema validation.
            RoutingError: If the task type cannot be routed.
        """
        # 1. Render prompt template
        template = self._router.get_prompt_template(TASK_TYPE)
        prompt = _render_template(template, raw_text)

        # 2. Call the model
        response, metadata = await self._router.complete(prompt, task_type=TASK_TYPE)

        # 3. Validate against schema
        schema = self._router.get_output_schema(TASK_TYPE)
        validated = validate_output(response, schema)

        # 4. Log the invocation
        await self._invocation_logger.log(
            InvocationMetadata(
                task_type=TASK_TYPE,
                model_alias=self._router._models_config.routing[TASK_TYPE].model,
                model_actual=metadata.model_actual,
                input_hash=_input_hash(raw_text),
                latency_ms=metadata.latency_ms,
                tokens_in=metadata.tokens_in,
                tokens_out=metadata.tokens_out,
                cost_usd=metadata.cost_usd,
                user_id=user_id,
                output=validated,
            )
        )

        # 5. Convert to result
        result = _to_parse_result(validated)

        # 6. Deduplication check — raises DuplicateDetectedError if duplicate found
        if self._deduplicator is not None:
            await self._deduplicator.check(
                new_title=result.title,
                new_description=result.description,
                new_domain=result.domain,
                user_id=user_id,
            )

        logger.info(
            "task_parsed",
            title=result.title,
            domain=result.domain,
            priority=result.priority,
            confidence=result.confidence,
            channel=channel,
            user_id=user_id,
        )

        return result
