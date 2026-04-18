"""Challenger agent — probes newly created tasks for quality and context.

Unlike the PM Agent which checks for missing *fields*, the Challenger
evaluates whether the task description is rich enough to execute well.
It asks follow-up questions about success criteria, hidden dependencies,
and scope boundaries.

Runs on the local LLM (via ``challenge_task`` task type) to keep costs
at zero. Falls through silently if the task is already well-specified.

See docs/agents.md for the agent hierarchy.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import jinja2
import structlog

from donna.agents.base import AgentContext, AgentResult
from donna.capabilities.matcher import CapabilityMatcher, MatchConfidence
from donna.capabilities.models import CapabilityRow
from donna.cost.budget import BudgetPausedError
from donna.models.router import ContextOverflowError, RoutingError
from donna.tasks.database import TaskRow

logger = structlog.get_logger()

_TASK_TYPE = "challenge_task"
_TIMEOUT_SECONDS = 120  # 2 minutes


@dataclass(slots=True)
class ChallengerMatchResult:
    """Result of ChallengerAgent.match_and_extract."""
    status: str  # ready | needs_input | escalate_to_claude | ambiguous
    intent_kind: str = "task"  # task | automation | question | chat
    capability: CapabilityRow | None = None
    extracted_inputs: dict[str, Any] = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)
    clarifying_question: str | None = None
    match_score: float = 0.0
    # Wave 3 extensions
    schedule: dict[str, Any] | None = None  # {cron, human_readable} when intent_kind=automation
    deadline: datetime | None = None  # when intent_kind=task
    alert_conditions: dict[str, Any] | None = None  # {expression, channels}
    confidence: float = 0.0  # LLM self-assessed confidence 0..1
    low_quality_signals: list[str] = field(default_factory=list)


class ChallengerAgent:
    """Probes task quality and asks follow-up questions when context is thin."""

    def __init__(
        self,
        *,
        matcher: CapabilityMatcher | None = None,
        input_extractor: Any | None = None,
        model_router: Any | None = None,
        capability_snapshot_ttl_s: float = 60.0,
    ) -> None:
        self._matcher = matcher
        self._input_extractor = input_extractor
        self._router = model_router
        self._env = jinja2.Environment(
            loader=jinja2.FileSystemLoader("prompts"),
            autoescape=False,
        )
        # F-W3-K: 60s TTL cache for capability snapshots. Every free-text
        # Discord message renders the registry into the challenger parse
        # prompt; without caching each message triggered a SQLite
        # round-trip. The registry changes rarely (migrations + nightly
        # auto-drafts), so 60s of staleness is fine.
        self._cap_snapshot_cache: list[CapabilityRow] | None = None
        self._cap_snapshot_cached_at: float = 0.0
        self._cap_snapshot_ttl_s: float = capability_snapshot_ttl_s

    @property
    def name(self) -> str:
        return "challenger"

    @property
    def allowed_tools(self) -> list[str]:
        return ["task_db_read"]

    @property
    def timeout_seconds(self) -> int:
        return _TIMEOUT_SECONDS

    async def match_and_extract(
        self,
        user_message: str,
        user_id: str,
    ) -> ChallengerMatchResult:
        """Match a user message against the capability registry and extract inputs.

        Prefers the unified LLM parse path when ``model_router`` is configured.
        Falls back to the legacy matcher + input_extractor pipeline otherwise.
        """
        if self._router is None:
            return await self._legacy_match_and_extract(user_message, user_id)

        caps = await self._snapshot_capabilities()
        template = self._env.get_template("challenger_parse.md")
        prompt = template.render(
            capabilities=caps,
            user_message=user_message,
            # Emit strict ISO-8601 with `Z` suffix (not `+00:00`) so prompt
            # fixtures and schema examples match the rendered value exactly.
            current_date_iso=datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        )

        # Narrow transport-style failures that are safe to fall back on.
        # Anything else (template bugs, schema mismatches, float() parse
        # failures inside _build_result_from_parse, etc.) must surface so
        # dev regressions are visible instead of silently masked.
        try:
            result_json, _meta = await self._router.complete(
                prompt,
                task_type="challenge_task",
                user_id=user_id,
            )
        except (ContextOverflowError, BudgetPausedError):
            # Propagate: caller/state machine decides how to handle these.
            raise
        except (asyncio.TimeoutError, json.JSONDecodeError, RoutingError) as exc:
            logger.exception(
                "challenger_parse_llm_failed",
                user_id=user_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            # Fall back to legacy path on transport-ish errors only.
            return await self._legacy_match_and_extract(user_message, user_id)
        except OSError as exc:
            # Network / aiohttp / httpx transport errors all inherit OSError.
            logger.exception(
                "challenger_parse_llm_transport_error",
                user_id=user_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return await self._legacy_match_and_extract(user_message, user_id)

        # F-W3-H: validate the LLM output against schemas/challenger_parse.json.
        # Mirrors ClaudeNoveltyJudge (Wave 3 Task 6). On validation failure
        # we log and degrade — _build_result_from_parse already tolerates
        # missing optional fields, so we fall through rather than crash.
        try:
            from donna.models.validation import validate_output

            schema = self._router.get_output_schema("challenge_task")
            if schema:
                result_json = validate_output(result_json, schema)
        except Exception as exc:
            logger.warning(
                "challenger_parse_schema_validation_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            # Fall through — don't abort the parse on validation mismatch.

        return self._build_result_from_parse(result_json, caps)

    async def _legacy_match_and_extract(
        self,
        user_message: str,
        user_id: str,
    ) -> ChallengerMatchResult:
        """Legacy matcher + input extractor path (retained for backward compat)."""
        if self._matcher is None:
            return ChallengerMatchResult(status="escalate_to_claude", match_score=0.0)

        match = await self._matcher.match(user_message)

        if match.confidence == MatchConfidence.LOW:
            return ChallengerMatchResult(
                status="escalate_to_claude",
                capability=None,
                match_score=match.best_score,
            )

        cap = match.best_match
        assert cap is not None

        if self._input_extractor is None:
            return ChallengerMatchResult(
                status="ready",
                capability=cap,
                match_score=match.best_score,
            )

        extracted = await self._input_extractor.extract(
            user_message=user_message,
            schema=cap.input_schema,
            user_id=user_id,
        )

        required = cap.input_schema.get("required", [])
        missing = [f for f in required if f not in extracted or extracted[f] in (None, "")]

        if missing:
            question = self._build_clarifying_question_for_fields(cap, missing)
            status = "needs_input" if match.confidence == MatchConfidence.HIGH else "ambiguous"
            return ChallengerMatchResult(
                status=status,
                capability=cap,
                extracted_inputs=extracted,
                missing_fields=missing,
                clarifying_question=question,
                match_score=match.best_score,
            )

        return ChallengerMatchResult(
            status="ready",
            capability=cap,
            extracted_inputs=extracted,
            missing_fields=[],
            match_score=match.best_score,
        )

    def _build_clarifying_question_for_fields(
        self, cap: CapabilityRow, missing: list[str]
    ) -> str:
        """Phase 1: simple templated question for missing fields."""
        props = cap.input_schema.get("properties", {})
        field_descriptions = []
        for f in missing:
            desc = props.get(f, {}).get("description", f)
            field_descriptions.append(f"- {f}: {desc}")

        return (
            f"I need a bit more to act on this as a {cap.name}:\n"
            + "\n".join(field_descriptions)
        )

    async def _snapshot_capabilities(self) -> list[CapabilityRow]:
        """Return the active capability set for inclusion in the parse prompt.

        Cached for ``_cap_snapshot_ttl_s`` seconds (default 60) so free-text
        Discord traffic doesn't hit SQLite on every message (F-W3-K).

        Returns an empty list when no matcher is configured or when the
        matcher does not expose a ``list_all`` method. Failures are
        logged and produce an empty snapshot; they are NOT cached (so
        a transient DB error doesn't poison the next 60s of parses).
        """
        now = time.monotonic()
        if (
            self._cap_snapshot_cache is not None
            and now - self._cap_snapshot_cached_at < self._cap_snapshot_ttl_s
        ):
            return self._cap_snapshot_cache
        if self._matcher is None or not hasattr(self._matcher, "list_all"):
            return []
        try:
            rows = await self._matcher.list_all()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("challenger_capabilities_snapshot_failed", error=str(exc))
            return []
        cached = list(rows)
        self._cap_snapshot_cache = cached
        self._cap_snapshot_cached_at = now
        return cached

    def _build_result_from_parse(
        self,
        parse: dict,
        caps: list[CapabilityRow],
    ) -> ChallengerMatchResult:
        """Convert the LLM's JSON parse into a ChallengerMatchResult.

        This method is synchronous — it resolves the capability by name from
        the in-memory ``caps`` snapshot captured before the LLM call, so it
        never awaits. If the snapshot is empty (e.g., caller passed a matcher
        without ``list_all``), ``capability`` remains None and downstream
        consumers must handle name-based lookups themselves.
        """
        name = parse.get("capability_name")
        cap: CapabilityRow | None = None
        if name:
            for row in caps:
                if getattr(row, "name", None) == name:
                    cap = row
                    break

        missing = list(parse.get("missing_fields") or [])
        confidence = float(parse.get("confidence", 0.0))
        match_score = float(parse.get("match_score", 0.0))
        intent_kind = parse.get("intent_kind", "task")

        # Hallucination guard: the LLM returned a capability name but it
        # doesn't exist in the snapshot we rendered into the prompt.
        # Without this guard the status ladder (`not name`) treats a
        # hallucinated name as a valid match and returns status=ready
        # with capability=None — downstream consumers then try to act
        # on a ghost capability. Force escalation instead.
        hallucinated = name is not None and cap is None
        if hallucinated:
            logger.warning(
                "challenger_parse_hallucinated_capability",
                llm_capability_name=name,
                available_capabilities=[getattr(c, "name", None) for c in caps],
            )
            cap = None

        if hallucinated:
            status = "escalate_to_claude"
        elif intent_kind in ("chat", "question"):
            status = "ready"
        elif not name or match_score < 0.4:
            status = "escalate_to_claude"
        elif missing:
            status = "needs_input"
        elif confidence < 0.7:
            status = "ambiguous"
        else:
            status = "ready"

        deadline: datetime | None = None
        raw_deadline = parse.get("deadline")
        if raw_deadline:
            # PRESERVE tz-awareness. datetime.fromisoformat in 3.11+ parses 'Z'
            # directly; older versions require an explicit fallback.
            try:
                deadline = datetime.fromisoformat(raw_deadline)
            except ValueError:
                if raw_deadline.endswith("Z"):
                    deadline = datetime.fromisoformat(raw_deadline[:-1]).replace(
                        tzinfo=timezone.utc
                    )
                else:
                    deadline = None

        return ChallengerMatchResult(
            status=status,
            intent_kind=intent_kind,
            capability=cap,
            extracted_inputs=parse.get("extracted_inputs") or {},
            missing_fields=missing,
            clarifying_question=parse.get("clarifying_question"),
            match_score=match_score,
            schedule=parse.get("schedule"),
            deadline=deadline,
            alert_conditions=parse.get("alert_conditions"),
            confidence=confidence,
            low_quality_signals=list(parse.get("low_quality_signals") or []),
        )

    async def execute(self, task: TaskRow, context: AgentContext) -> AgentResult:
        """Evaluate task quality and return follow-up questions if needed."""
        start = time.monotonic()

        prompt = self._build_challenge_prompt(task)

        try:
            result, metadata = await context.router.complete(
                prompt, task_type=_TASK_TYPE, user_id=context.user_id
            )
        except ContextOverflowError:
            raise
        except Exception as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            logger.error("challenger_agent_llm_failed", task_id=task.id, error=str(exc))
            # On failure, let the task proceed — don't block on challenger errors.
            return AgentResult(
                status="complete",
                output={"challenger_skipped": True, "reason": str(exc)},
                duration_ms=elapsed,
            )

        elapsed = int((time.monotonic() - start) * 1000)

        needs_clarification = result.get("needs_clarification", False)
        questions = result.get("questions", [])

        if needs_clarification and questions:
            logger.info(
                "challenger_agent_needs_input",
                task_id=task.id,
                question_count=len(questions),
                reasoning=result.get("reasoning", ""),
            )
            return AgentResult(
                status="needs_input",
                output=result,
                duration_ms=elapsed,
                questions=questions,
            )

        logger.info(
            "challenger_agent_approved",
            task_id=task.id,
            duration_ms=elapsed,
        )

        return AgentResult(
            status="complete",
            output={**result, "task_id": task.id},
            duration_ms=elapsed,
        )

    def _build_challenge_prompt(self, task: TaskRow) -> str:
        """Build a prompt for task quality evaluation."""
        return f"""You are Donna's task quality reviewer. A new task has been created.
Evaluate if it has enough context to execute well.

Task:
- Title: {task.title}
- Description: {task.description or 'None provided'}
- Domain: {task.domain}
- Priority: {task.priority}
- Deadline: {task.deadline or 'None'}
- Estimated duration: {task.estimated_duration or 'Unknown'}
- Tags: {task.tags or '[]'}

Generate 1-3 follow-up questions ONLY if the task is vague or missing
critical context. Questions should probe:
- What "done" looks like (success criteria)
- Hidden dependencies or blockers
- Scope boundaries (what's NOT included)

If the task is clear and actionable as-is, return no questions.

Respond with JSON:
{{
  "needs_clarification": true or false,
  "questions": ["What does done look like for this?"],
  "reasoning": "Brief explanation of why questions are needed or why task is clear"
}}"""
