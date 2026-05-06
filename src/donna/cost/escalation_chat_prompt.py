"""Chat-mode prompt rendering + summarization for over-budget escalations.

Realizes ``docs/superpowers/specs/manual-escalation.md`` §5.2 / §6.1 /
§10.2 row 3. When the over-budget gate decides to offer a chat-mode
manual handoff, this module:

1. Renders ``prompts/escalation/chat_question.md`` with the caller's
   original prompt + escalation metadata to produce the canonical
   prompt body.
2. Generates a 1-3 sentence summary via local Ollama (no API spend),
   falling back to a deterministic templated string if Ollama is down,
   times out, or returns a malformed shape.
3. Writes the rendered body to
   ``${DONNA_WORKSPACE_PATH}/<workspace_subdir>/<correlation_id>.md``
   so the user has a backup read-only view if the dashboard is down.
4. Persists ``prompt_body``, ``summary``, ``prompt_path``, and
   ``mode='chat'`` onto the ``escalation_request`` row before the
   delivery callback runs (the delivery callback uses the file path to
   attach the .md to the Discord message).

Any failure short of a programming error is non-fatal — the escalation
delivery still happens, just with degraded fidelity (deterministic
summary, no attachment if the file write itself failed).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiosqlite
import jinja2
import jsonschema
import structlog

from donna.config import PromptDeliveryConfig

if TYPE_CHECKING:
    from donna.cost.escalation_repository import EscalationRequestRow
    from donna.models.router import ModelRouter

logger = structlog.get_logger()

ESCALATION_SUMMARY_TASK_TYPE = "escalation_summary"
"""Task type alias used for the local-Ollama summarizer call."""

CHAT_QUESTION_TEMPLATE_PATH = "prompts/escalation/chat_question.md"
"""Relative path under ``project_root`` to the Jinja prompt template."""

SUMMARY_SCHEMA_RELATIVE_PATH = "schemas/escalation_summary_output.json"
"""Relative path (under ``project_root``) to the JSON schema enforcing the
local-LLM summarizer's output shape. A schema-violating response triggers
the deterministic fallback per spec §10.2 row 3."""


class ChatPromptBuilder:
    """Renders the chat-mode prompt and summary, persists to the row.

    Single entry point: :meth:`build_and_persist`. Construction is
    cheap; one instance is shared across the orchestrator process and
    held by :class:`donna.cost.escalation_gate.EscalationGate`.

    Attributes:
        router: Used only for the summary call (chat-mode itself never
            spends API budget — the *user* answers the question).
        project_root: Filesystem root used to resolve the Jinja
            template path.
        workspace_root: Resolved at construction time from
            ``DONNA_WORKSPACE_PATH`` (env). When unset, falls back to
            ``project_root / "var" / "workspace"`` so tests / dev boots
            still produce a real path.
        config: ``PromptDeliveryConfig`` controlling truncation,
            attachment toggle, and the workspace subdirectory.
    """

    def __init__(
        self,
        *,
        router: ModelRouter,
        project_root: Path,
        config: PromptDeliveryConfig,
        workspace_root: Path | None = None,
    ) -> None:
        self._router = router
        self._project_root = project_root
        self._config = config
        self._workspace_root = workspace_root or self._resolve_workspace_root(
            project_root
        )
        self._template: jinja2.Template | None = None
        self._summary_schema: dict[str, Any] | None = None

    @staticmethod
    def _resolve_workspace_root(project_root: Path) -> Path:
        env_path = os.environ.get("DONNA_WORKSPACE_PATH")
        if env_path:
            return Path(env_path)
        return project_root / "var" / "workspace"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def build_and_persist(
        self,
        *,
        conn: aiosqlite.Connection,
        row: EscalationRequestRow,
        original_prompt: str,
    ) -> tuple[str, str, str | None]:
        """Render, summarize, write to disk, and persist columns.

        Returns:
            Tuple of (prompt_body, summary, prompt_path). ``prompt_path``
            is None when the disk write failed — the caller should treat
            that as "no attachment available" rather than aborting the
            escalation.
        """
        prompt_body = self._render_prompt_body(row=row, original_prompt=original_prompt)
        summary = await self._generate_summary(
            row=row, original_prompt=original_prompt
        )
        prompt_path = await self._write_workspace_file(
            row=row, prompt_body=prompt_body
        )

        await conn.execute(
            """
            UPDATE escalation_request
               SET prompt_body = ?,
                   summary     = ?,
                   prompt_path = COALESCE(?, prompt_path),
                   mode        = COALESCE(mode, 'chat')
             WHERE id = ?
            """,
            (prompt_body, summary, prompt_path, row.id),
        )
        await conn.commit()

        logger.info(
            "escalation_chat_prompt_persisted",
            correlation_id=row.correlation_id,
            escalation_request_id=row.id,
            prompt_chars=len(prompt_body),
            summary_chars=len(summary),
            prompt_path=prompt_path,
        )
        return prompt_body, summary, prompt_path

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_template(self) -> jinja2.Template:
        if self._template is not None:
            return self._template
        path = self._project_root / CHAT_QUESTION_TEMPLATE_PATH
        # autoescape stays off — the rendered output is markdown, not HTML.
        env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(path.parent)),
            autoescape=False,
            undefined=jinja2.StrictUndefined,
            keep_trailing_newline=True,
        )
        self._template = env.get_template(path.name)
        return self._template

    def _render_prompt_body(
        self, *, row: EscalationRequestRow, original_prompt: str
    ) -> str:
        template = self._load_template()
        return template.render(
            correlation_id=row.correlation_id,
            task_type=row.task_type,
            task_id=row.task_id,
            estimate_usd=row.estimate_usd,
            daily_remaining_usd=row.daily_remaining_usd,
            iteration=row.iteration,
            original_prompt=original_prompt,
        )

    async def _generate_summary(
        self, *, row: EscalationRequestRow, original_prompt: str
    ) -> str:
        """Ollama summary with a deterministic fallback (§10.2 row 3)."""
        max_chars = max(0, self._config.discord_summary_max_chars)
        try:
            template = self._router.get_prompt_template(
                ESCALATION_SUMMARY_TASK_TYPE
            )
        except Exception:
            logger.warning(
                "escalation_summary_template_unavailable",
                correlation_id=row.correlation_id,
            )
            return self._deterministic_summary(row, max_chars)

        rendered_prompt = self._render_summary_prompt(template, original_prompt)
        try:
            result, _meta = await self._router.complete(
                prompt=rendered_prompt,
                task_type=ESCALATION_SUMMARY_TASK_TYPE,
                user_id=row.user_id,
            )
            self._validate_summary_payload(result)
            title = str(result.get("title") or "").strip()
            summary = str(result.get("summary") or "").strip()
            stitched = f"{title} — {summary}" if title else summary
        except Exception:
            logger.warning(
                "escalation_summary_fallback",
                correlation_id=row.correlation_id,
                exc_info=True,
            )
            return self._deterministic_summary(row, max_chars)

        # Single-line + truncate so we always fit Discord's 2000-char body.
        flattened = " ".join(stitched.split())
        return self._truncate(flattened, max_chars)

    def _deterministic_summary(
        self, row: EscalationRequestRow, max_chars: int
    ) -> str:
        text = (
            f"{row.task_type} request — estimate "
            f"${row.estimate_usd:.2f}. Click for full prompt."
        )
        return self._truncate(text, max_chars)

    @staticmethod
    def _truncate(text: str, max_chars: int) -> str:
        if max_chars <= 0 or len(text) <= max_chars:
            return text
        # Keep an ellipsis so truncation is visible to the human reader.
        keep = max(1, max_chars - 1)
        return text[:keep] + "…"

    @staticmethod
    def _render_summary_prompt(template: str, original_prompt: str) -> str:
        # The summarizer template uses {{ original_prompt }}; render with
        # a transient Jinja env to keep the substitution safe regardless
        # of brace-laden user content.
        env = jinja2.Environment(
            autoescape=False,
            undefined=jinja2.StrictUndefined,
        )
        return env.from_string(template).render(original_prompt=original_prompt)

    async def _write_workspace_file(
        self, *, row: EscalationRequestRow, prompt_body: str
    ) -> str | None:
        """Write the prompt to disk; return path or None on failure."""
        target_dir = self._workspace_root / self._config.workspace_subdir
        target = target_dir / f"{row.correlation_id}.md"
        try:
            await asyncio.to_thread(_write_file_sync, target, prompt_body)
        except Exception:
            logger.exception(
                "escalation_workspace_write_failed",
                correlation_id=row.correlation_id,
                target=str(target),
            )
            return None
        return str(target)

    def _load_summary_schema(self) -> dict[str, Any]:
        """Load + cache the summarizer JSON schema.

        Schema lives under the project root rather than alongside this
        module so it stays colocated with the other request/response
        schemas the orchestrator already manages.
        """
        if self._summary_schema is not None:
            return self._summary_schema
        path = self._project_root / SUMMARY_SCHEMA_RELATIVE_PATH
        with open(path) as f:
            self._summary_schema = json.load(f)
        return self._summary_schema

    def _validate_summary_payload(self, payload: Any) -> None:
        """Validate a summarizer response against the JSON schema.

        Raises ``jsonschema.ValidationError`` (or ``OSError`` /
        ``json.JSONDecodeError`` if the schema itself is unreadable) so
        :meth:`_generate_summary`'s broad except clause surfaces the
        deterministic-fallback path in either case.
        """
        schema = self._load_summary_schema()
        jsonschema.validate(payload, schema)


def _write_file_sync(target: Path, body: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")


__all__ = [
    "CHAT_QUESTION_TEMPLATE_PATH",
    "ESCALATION_SUMMARY_TASK_TYPE",
    "ChatPromptBuilder",
]
