"""Conversation engine — core chat handler for Donna.

Single entry point for all chat interactions. Classifies intent,
assembles context, calls the local LLM, and manages sessions.
See docs/superpowers/specs/2026-04-12-chat-interface-design.md.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import structlog

from donna.chat.config import ChatConfig
from donna.chat.context import (
    build_intent_context,
    build_session_context,
    render_chat_prompt,
)
from donna.chat.types import ChatIntent, ChatResponse

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from donna.models.router import ModelRouter
    from donna.tasks.database import Database

logger = structlog.get_logger()


class ConversationEngine:
    """Handles all chat interactions regardless of frontend.

    Usage:
        engine = ConversationEngine(db, router, config, project_root)
        response = await engine.handle_message(None, "nick", "Hi", "discord")
    """

    def __init__(
        self,
        db: Database,
        router: ModelRouter,
        config: ChatConfig,
        project_root: Path,
    ) -> None:
        self._db = db
        self._router = router
        self._config = config
        self._project_root = project_root

    async def handle_message(
        self,
        session_id: str | None,
        user_id: str,
        text: str,
        channel: str,
    ) -> ChatResponse:
        """Process a chat message and return a response.

        If session_id is None, resumes the active session or creates one.
        """
        log = logger.bind(user_id=user_id, channel=channel)

        # Resolve or create session
        session = None
        if session_id:
            session = await self._db.get_chat_session(session_id)
        if session is None:
            session = await self._db.get_active_chat_session(user_id, channel)
        if session is None:
            session = await self._db.create_chat_session(
                user_id=user_id,
                channel=channel,
                ttl_minutes=self._config.sessions.ttl_minutes,
            )
            log.info("chat_session_created", session_id=session.id)

        # Refresh TTL
        new_expires = datetime.utcnow() + timedelta(
            minutes=self._config.sessions.ttl_minutes
        )
        await self._db.update_chat_session(
            session.id, expires_at=new_expires.isoformat()
        )

        # Store user message
        await self._db.add_chat_message(
            session_id=session.id, role="user", content=text
        )

        # Classify intent
        intent_result = await self._classify_intent(text, user_id)
        intent = ChatIntent(intent_result.get("intent", "freeform"))

        # Check for escalation at classification stage
        if intent_result.get("needs_escalation"):
            cost_estimate = self._estimate_escalation_cost()
            return ChatResponse(
                text=f"I'd need to use Claude for this — {intent_result.get('escalation_reason', 'complex reasoning required')}. "
                     f"Estimated cost: ~${cost_estimate:.2f}. Go ahead?",
                needs_escalation=True,
                escalation_reason=intent_result.get("escalation_reason"),
                estimated_cost=cost_estimate,
                session_pinned_task_id=getattr(session, "pinned_task_id", None),
            )

        # Load context
        history = await self._db.list_chat_messages(session.id)
        pinned_task = None
        if getattr(session, "pinned_task_id", None):
            task_row = await self._db.get_task(session.pinned_task_id)
            if task_row:
                pinned_task = {
                    "title": task_row.title,
                    "description": task_row.description,
                    "status": task_row.status,
                    "priority": task_row.priority,
                    "notes": task_row.notes,
                }

        session_ctx = build_session_context(history, pinned_task)

        # Build intent-specific context
        intent_ctx = await self._load_intent_context(intent, user_id)

        # Load and render prompt
        system_template = self._load_system_prompt()
        prompt = render_chat_prompt(
            template=system_template,
            user_input=text,
            user_name="Nick",
            session_context=session_ctx,
            intent_context=intent_ctx,
        )

        # Call LLM for response
        response_data, metadata = await self._router.complete(
            prompt=prompt,
            task_type="chat_respond",
            user_id=user_id,
        )

        response_text = response_data.get("response_text", "")
        needs_escalation = response_data.get("needs_escalation", False)

        if needs_escalation:
            cost_estimate = self._estimate_escalation_cost()
            result = ChatResponse(
                text=f"I'd need to use Claude for this — {response_data.get('escalation_reason', 'complex reasoning required')}. "
                     f"Estimated cost: ~${cost_estimate:.2f}. Go ahead?",
                needs_escalation=True,
                escalation_reason=response_data.get("escalation_reason"),
                estimated_cost=cost_estimate,
                session_pinned_task_id=getattr(session, "pinned_task_id", None),
            )
        else:
            result = ChatResponse(
                text=response_text,
                suggested_actions=response_data.get("suggested_actions", []),
                pin_suggestion=response_data.get("pin_suggestion"),
                session_pinned_task_id=getattr(session, "pinned_task_id", None),
            )

        # Store assistant message
        await self._db.add_chat_message(
            session_id=session.id,
            role="assistant",
            content=result.text,
            intent=intent.value,
            tokens_used=metadata.tokens_out if hasattr(metadata, "tokens_out") else None,
        )

        log.info(
            "chat_response_sent",
            session_id=session.id,
            intent=intent.value,
            escalation=result.needs_escalation,
        )

        return result

    async def handle_escalation(
        self, session_id: str, user_id: str
    ) -> ChatResponse:
        """Handle an approved escalation — send context to Claude."""
        session = await self._db.get_chat_session(session_id)
        if session is None:
            return ChatResponse(text="Session not found.")

        history = await self._db.list_chat_messages(session_id)
        session_ctx = build_session_context(history, pinned_task=None)

        system_template = self._load_system_prompt()
        # Use the last user message as the input
        last_user_msg = ""
        for msg in reversed(history):
            if msg.role == "user":
                last_user_msg = msg.content
                break

        prompt = render_chat_prompt(
            template=system_template,
            user_input=last_user_msg,
            user_name="Nick",
            session_context=session_ctx,
        )

        response_data, metadata = await self._router.complete(
            prompt=prompt,
            task_type="chat_escalation",
            user_id=user_id,
        )

        response_text = response_data.get("response_text", "")
        result = ChatResponse(
            text=response_text,
            suggested_actions=response_data.get("suggested_actions", []),
            session_pinned_task_id=getattr(session, "pinned_task_id", None),
        )

        await self._db.add_chat_message(
            session_id=session_id,
            role="assistant",
            content=result.text,
            intent="escalation",
            tokens_used=metadata.tokens_out if hasattr(metadata, "tokens_out") else None,
        )

        return result

    async def pin_session(self, session_id: str, task_id: str) -> None:
        """Pin a session to a task."""
        await self._db.update_chat_session(session_id, pinned_task_id=task_id)

    async def unpin_session(self, session_id: str) -> None:
        """Unpin a session from its task."""
        await self._db.update_chat_session(session_id, pinned_task_id=None)

    async def close_session(self, session_id: str) -> str | None:
        """Close a session and generate a summary."""
        if self._config.sessions.summary_on_close:
            summary = await self._summarize_session(session_id)
            await self._db.update_chat_session(
                session_id, status="closed", summary=summary
            )
            return summary
        await self._db.update_chat_session(session_id, status="closed")
        return None

    async def _classify_intent(
        self, text: str, user_id: str
    ) -> dict[str, Any]:
        """Classify user message intent via local LLM."""
        template_path = self._project_root / "prompts" / "chat" / "classify_intent.md"
        template = ""
        if template_path.exists():
            template = template_path.read_text()
        else:
            template = "Classify this message intent: {{ user_input }}"

        prompt = render_chat_prompt(template=template, user_input=text)
        result, _ = await self._router.complete(
            prompt=prompt,
            task_type="classify_chat_intent",
            user_id=user_id,
        )
        return result

    async def _load_intent_context(
        self, intent: ChatIntent, user_id: str
    ) -> str:
        """Load intent-specific context from the database."""
        if intent in (ChatIntent.FREEFORM, ChatIntent.ESCALATION_REQUEST):
            return ""

        tasks = []
        if intent in (
            ChatIntent.TASK_QUERY,
            ChatIntent.TASK_ACTION,
            ChatIntent.PLANNING,
        ):
            task_rows = await self._db.list_tasks(user_id=user_id)
            tasks = [
                {
                    "title": t.title,
                    "status": t.status,
                    "priority": t.priority,
                    "domain": t.domain,
                }
                for t in task_rows
                if t.status not in ("done", "cancelled")
            ]

        schedule_summary = None
        open_task_count = None
        if intent == ChatIntent.PLANNING:
            open_task_count = len(tasks)
            scheduled = [t for t in tasks if t["status"] == "scheduled"]
            schedule_summary = f"{len(scheduled)} tasks scheduled"

        return build_intent_context(
            intent,
            tasks=tasks,
            schedule_summary=schedule_summary,
            open_task_count=open_task_count,
        )

    def _load_system_prompt(self) -> str:
        """Load the system prompt template based on persona config."""
        if self._config.persona.mode == "neutral":
            path = self._project_root / "prompts" / "chat" / "chat_system_neutral.md"
        else:
            path = self._project_root / "prompts" / "chat" / "chat_system.md"
        if path.exists():
            return path.read_text()
        return "You are a helpful assistant. {{ user_input }}"

    async def _summarize_session(self, session_id: str) -> str:
        """Generate a summary of the session via local LLM."""
        messages = await self._db.list_chat_messages(session_id)
        if not messages:
            return "Empty session."

        history_text = "\n".join(
            f"{'User' if m.role == 'user' else 'Assistant'}: {m.content}"
            for m in messages
        )

        template_path = self._project_root / "prompts" / "chat" / "chat_summarize.md"
        template = ""
        if template_path.exists():
            template = template_path.read_text()
        else:
            template = "Summarize this conversation:\n{{ conversation_history }}"

        prompt = render_chat_prompt(
            template=template,
            user_input="",
            conversation_history=history_text,
        )

        result, _ = await self._router.complete(
            prompt=prompt,
            task_type="chat_summarize",
            user_id="system",
        )
        return result.get("summary", "Session ended.")

    def _estimate_escalation_cost(self) -> float:
        """Rough cost estimate for a Claude escalation call."""
        # ~4k tokens context + ~1k response, at Claude Sonnet pricing
        # $3/MTok input + $15/MTok output (approximate)
        return round(4 * 0.003 + 1 * 0.015, 3)
