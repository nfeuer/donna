"""ClaudeNoveltyJudge — Claude call for no-capability-match escalations.

Returns execution-ready extraction + a reuse judgment. Called by
DiscordIntentDispatcher when ChallengerAgent emits status=escalate_to_claude.

See docs/agents.md and slices/wave-3/ for context.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import jinja2
import structlog

logger = structlog.get_logger()


@dataclass(slots=True)
class NoveltyVerdict:
    """Structured output from the ClaudeNoveltyJudge call."""

    intent_kind: str
    trigger_type: str | None
    extracted_inputs: dict[str, Any]
    schedule: dict[str, Any] | None
    deadline: datetime | None
    alert_conditions: dict[str, Any] | None
    polling_interval_suggestion: str | None
    skill_candidate: bool
    skill_candidate_reasoning: str
    clarifying_question: str | None


class ClaudeNoveltyJudge:
    """Calls Claude to judge no-match messages and emit structured intent."""

    _TASK_TYPE = "claude_novelty"

    def __init__(self, *, model_router: Any, database: Any) -> None:
        self._router = model_router
        self._db = database
        self._env = jinja2.Environment(
            loader=jinja2.FileSystemLoader("prompts"),
            autoescape=False,
        )

    async def evaluate(self, user_message: str, user_id: str) -> NoveltyVerdict:
        """Evaluate a no-match user message and return a NoveltyVerdict.

        The injected ``database`` handle is expected to expose a
        ``list_capabilities`` coroutine returning an iterable of capability
        rows (with ``name`` and ``description`` attributes). This wrapper is
        provided by the caller (e.g., DiscordIntentDispatcher) and typically
        delegates to :meth:`CapabilityRegistry.list_all` under the hood.
        """
        caps: list[Any] = []
        if hasattr(self._db, "list_capabilities"):
            caps = list(await self._db.list_capabilities())

        template = self._env.get_template("claude_novelty.md")
        prompt = template.render(
            capabilities=caps,
            user_message=user_message,
            current_date_iso=datetime.now(timezone.utc).isoformat(),
        )
        parsed, _meta = await self._router.complete(
            prompt,
            task_type=self._TASK_TYPE,
            user_id=user_id,
        )

        deadline: datetime | None = None
        raw_deadline = parsed.get("deadline")
        if raw_deadline:
            # Preserve tz-awareness. datetime.fromisoformat in 3.11+ parses
            # 'Z' directly; older runtimes need an explicit fallback.
            try:
                deadline = datetime.fromisoformat(raw_deadline)
            except ValueError:
                if raw_deadline.endswith("Z"):
                    deadline = datetime.fromisoformat(raw_deadline[:-1]).replace(
                        tzinfo=timezone.utc
                    )
                else:
                    deadline = None

        return NoveltyVerdict(
            intent_kind=parsed["intent_kind"],
            trigger_type=parsed.get("trigger_type"),
            extracted_inputs=parsed.get("extracted_inputs") or {},
            schedule=parsed.get("schedule"),
            deadline=deadline,
            alert_conditions=parsed.get("alert_conditions"),
            polling_interval_suggestion=parsed.get("polling_interval_suggestion"),
            skill_candidate=bool(parsed["skill_candidate"]),
            skill_candidate_reasoning=parsed["skill_candidate_reasoning"],
            clarifying_question=parsed.get("clarifying_question"),
        )
