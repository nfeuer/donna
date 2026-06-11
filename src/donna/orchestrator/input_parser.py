"""Input parsing pipeline — natural language to structured task.

Takes raw text input, renders the prompt template, calls the model
router, validates the response, logs the invocation, and returns
a typed TaskParseResult. See docs/task-system.md.
"""

from __future__ import annotations

import dataclasses
import zoneinfo
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from donna.logging.invocation_logger import InvocationLogger
from donna.models.router import ModelRouter
from donna.models.validation import validate_output
from donna.orchestrator.task_context import build_personal_context
from donna.tasks.dedup import Deduplicator, DuplicateDetectedError  # noqa: F401 — re-exported

if TYPE_CHECKING:
    from donna.preferences.rule_applier import PreferenceApplier

logger = structlog.get_logger()

TASK_TYPE = "parse_task"
CLOUD_TASK_TYPE = "parse_task_cloud"


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


_DEFAULT_TZ = zoneinfo.ZoneInfo("America/New_York")


def _render_template(
    template: str,
    user_input: str,
    tz: zoneinfo.ZoneInfo | None = None,
    personal_context: str = "",
) -> str:
    """Fill template variables with current context."""
    now = datetime.now(UTC).astimezone(tz or _DEFAULT_TZ)
    return (
        template
        .replace("{{ current_date }}", now.strftime("%Y-%m-%d"))
        .replace("{{ current_time }}", now.strftime("%I:%M %p %Z"))
        .replace("{{ personal_context }}", personal_context.strip() or "(none)")
        .replace("{{ user_input }}", user_input)
    )


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
        preference_applier: PreferenceApplier | None = None,
        tz: zoneinfo.ZoneInfo | None = None,
        memory_store: Any | None = None,
    ) -> None:
        self._router = router
        self._invocation_logger = invocation_logger
        self._project_root = project_root
        self._deduplicator = deduplicator
        self._preference_applier = preference_applier
        self._tz = tz
        self._memory_store = memory_store

    def set_memory_store(self, memory_store: Any) -> None:
        """Late-bind the memory store (built after the parser at boot)."""
        self._memory_store = memory_store

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
        # 1. Build personal context, then render the prompt template
        personal_context = await build_personal_context(
            raw_text,
            user_id,
            preference_applier=self._preference_applier,
            memory_store=self._memory_store,
        )
        template = self._router.get_prompt_template(TASK_TYPE)
        prompt = _render_template(
            template, raw_text, tz=self._tz, personal_context=personal_context,
        )

        # 2. Call the model (invocation logged automatically by ModelRouter)
        response, _metadata = await self._router.complete(
            prompt, task_type=TASK_TYPE, user_id=user_id,
        )

        # 3. Validate against schema
        schema = self._router.get_output_schema(TASK_TYPE)
        validated = validate_output(response, schema)

        # 3b. Confidence-gated escalation: re-parse on the cloud model when the
        # local model is unsure. The cloud route reuses this prompt + schema.
        threshold = self._router.confidence_threshold_for(TASK_TYPE)
        if threshold is not None and validated["confidence"] < threshold:
            logger.info(
                "parse_confidence_escalation",
                local_confidence=validated["confidence"],
                threshold=threshold,
                user_id=user_id,
            )
            cloud_response, _cloud_meta = await self._router.complete(
                prompt, task_type=CLOUD_TASK_TYPE, user_id=user_id,
            )
            # _cloud_meta is intentionally discarded — router.complete() logs
            # the invocation itself.
            validated = validate_output(cloud_response, schema)
            logger.info(
                "parse_confidence_escalation_resolved",
                cloud_confidence=validated["confidence"],
                user_id=user_id,
            )

        # 5. Convert to result
        result = _to_parse_result(validated)

        # 5b. Apply learned preferences (post-parse, pre-database)
        if self._preference_applier is not None:
            result = await self._preference_applier.apply_for_user(result, user_id)

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
