"""Conversation engine — core chat handler for Donna.

Single entry point for all chat interactions. Classifies intent,
assembles context, calls the local LLM, and manages sessions.
See docs/superpowers/specs/archive/2026-04-12-chat-interface-design.md.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from donna.chat.actions import ActionRegistry
from donna.chat.config import ChatConfig
from donna.chat.context import (
    build_intent_context,
    build_session_context,
    render_chat_prompt,
)
from donna.chat.types import ActionContext, ActionResult, ChatIntent, ChatResponse

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
        action_registry: ActionRegistry | None = None,
    ) -> None:
        self._db = db
        self._router = router
        self._config = config
        self._project_root = project_root
        self._action_registry = action_registry

    async def handle_message(
        self,
        session_id: str | None,
        user_id: str,
        text: str,
        channel: str,
        dashboard_context: dict[str, Any] | None = None,
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
        new_expires = datetime.now(UTC) + timedelta(
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

        # ── Action pipeline ──────────────────────────────
        if (
            self._action_registry is not None
            and self._config.actions.enabled
        ):
            action_result = await self._try_action_pipeline(
                intent_result=intent_result,
                text=text,
                user_id=user_id,
                session=session,
                dashboard_context=dashboard_context,
            )
            if action_result is not None:
                return action_result

        # Check for escalation at classification stage
        if intent_result.get("needs_escalation"):
            cost_estimate = self._estimate_escalation_cost()
            return ChatResponse(
                text=(
                    f"I'd need to use Claude for this — "
                    f"{intent_result.get('escalation_reason', 'complex reasoning required')}. "
                    f"Estimated cost: ~${cost_estimate:.2f}. Go ahead?"
                ),
                session_id=session.id,
                needs_escalation=True,
                escalation_reason=intent_result.get("escalation_reason"),
                estimated_cost=cost_estimate,
                session_pinned_task_id=getattr(session, "pinned_task_id", None),
            )

        # Load context
        history = await self._db.list_chat_messages(session.id)
        pinned_task = None
        if getattr(session, "pinned_task_id", None):
            assert session.pinned_task_id is not None
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
        system_prompt = self._load_system_prompt()
        rendered_system = render_chat_prompt(
            template=system_prompt,
            user_input=text,
            user_name="Nick",
            session_context=session_ctx,
            intent_context=intent_ctx,
        )
        respond_template = self._load_respond_template()
        prompt = (
            respond_template
            .replace("{{ system_prompt }}", rendered_system)
            .replace("{{ conversation_history }}", session_ctx)
            .replace("{{ user_input }}", text)
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
                text=(
                    f"I'd need to use Claude for this — "
                    f"{response_data.get('escalation_reason', 'complex reasoning required')}. "
                    f"Estimated cost: ~${cost_estimate:.2f}. Go ahead?"
                ),
                session_id=session.id,
                needs_escalation=True,
                escalation_reason=response_data.get("escalation_reason"),
                estimated_cost=cost_estimate,
                session_pinned_task_id=getattr(session, "pinned_task_id", None),
            )
        else:
            result = ChatResponse(
                text=response_text,
                session_id=session.id,
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

        logger.info(
            "chat_escalation_sent",
            session_id=session_id,
            user_id=user_id,
            tokens_out=getattr(metadata, "tokens_out", None),
            cost_usd=getattr(metadata, "cost_usd", None),
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

    async def _try_action_pipeline(
        self,
        intent_result: dict[str, Any],
        text: str,
        user_id: str,
        session: Any,
        dashboard_context: dict[str, Any] | None,
    ) -> ChatResponse | None:
        """Attempt to match and execute an action from the registry."""
        assert self._action_registry is not None

        domain = intent_result.get("domain")
        action_hint = intent_result.get("action_hint")

        if not domain and not action_hint:
            return None

        action = self._action_registry.match(
            domain=domain, action_hint=action_hint,
        )
        if action is None:
            return None

        log = logger.bind(action=action.name, domain=action.domain)

        params = await self._extract_action_params(action, text, session, dashboard_context)

        required = action.parameters.get("required", [])
        missing = [r for r in required if r not in params or params[r] is None]
        if missing:
            return ChatResponse(
                text=f"I need a bit more info to do that. Missing: {', '.join(missing)}",
                session_id=session.id,
                session_pinned_task_id=getattr(session, "pinned_task_id", None),
            )

        if action.safety == "confirm":
            pending = self._action_registry.format_pending_action(action.name, params)
            await self._db.update_chat_session(session.id, pending_action=pending)
            desc = action.description
            param_summary = ", ".join(f"{k}={v}" for k, v in params.items() if v)
            return ChatResponse(
                text=f"I'll {desc.lower()} ({param_summary}). Go ahead?",
                session_id=session.id,
                needs_escalation=False,
                session_pinned_task_id=getattr(session, "pinned_task_id", None),
            )

        ctx = ActionContext(
            db=self._db,
            user_id=user_id,
            session_id=session.id,
            config=self._config,
            dashboard_context=dashboard_context,
        )
        result = await self._action_registry.execute(action.name, params, ctx)

        log.info("action_executed", success=result.success, summary=result.summary)

        response_text = await self._summarize_action_result(action, params, result, session)

        await self._db.add_chat_message(
            session_id=session.id,
            role="assistant",
            content=response_text,
            intent=action.domain,
            action_name=action.name,
            action_result=json.dumps(result.data) if result.data else None,
        )

        return ChatResponse(
            text=response_text,
            session_id=session.id,
            suggested_actions=result.data.get("suggested_actions", []) if result.data else [],
            session_pinned_task_id=getattr(session, "pinned_task_id", None),
        )

    async def _extract_action_params(
        self,
        action: Any,
        text: str,
        session: Any,
        dashboard_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Extract action parameters from user text via LLM."""
        template_path = self._project_root / "prompts" / "chat" / "extract_action_params.md"
        if not template_path.exists():
            return {}
        template = template_path.read_text()

        ctx_str = ""
        if dashboard_context:
            page = dashboard_context.get("page", "unknown")
            selected = dashboard_context.get("selected_item")
            if selected:
                ctx_str = (
                    f"User is viewing the {page.title()} page and has selected "
                    f"{selected.get('type', 'item')} '{selected.get('label', '')}' "
                    f"(id: {selected.get('id', '')})."
                )
            else:
                ctx_str = f"User is viewing the {page.title()} page."

        example_output = json.dumps(
            {p: f"<{p}>" for p in action.parameters.get("properties", {}).keys()},
            indent=2,
        )

        history = await self._db.list_chat_messages(session.id, limit=10)
        history_text = "\n".join(
            f"{'User' if m.role == 'user' else 'Assistant'}: {m.content}"
            for m in history[-6:]
        )

        prompt = render_chat_prompt(
            template=template,
            user_input=text,
            action_name=action.name,
            action_description=action.description,
            parameter_schema=json.dumps(action.parameters, indent=2),
            dashboard_context=ctx_str,
            conversation_history=history_text,
            example_output=example_output,
        )

        result, _ = await self._router.complete(
            prompt=prompt,
            task_type="classify_chat_intent",
            user_id="system",
        )
        return result

    async def _summarize_action_result(
        self,
        action: Any,
        params: dict[str, Any],
        result: ActionResult,
        session: Any,
    ) -> str:
        """Summarize an action result into user-friendly text."""
        if result.summary and not result.data:
            return result.summary

        template_path = self._project_root / "prompts" / "chat" / "summarize_action_result.md"
        if not template_path.exists():
            return result.summary or result.error or "Action completed."

        template = template_path.read_text()

        prompt = render_chat_prompt(
            template=template,
            user_input="",
            action_name=action.name,
            action_description=action.description,
            params_json=json.dumps(params, indent=2),
            success=str(result.success),
            result_data=json.dumps(result.data, indent=2, default=str) if result.data else (result.error or "No data"),
        )

        resp, _ = await self._router.complete(
            prompt=prompt,
            task_type="chat_respond",
            user_id="system",
        )
        return resp.get("response_text", result.summary or "Done.")

    async def handle_confirm(
        self, session_id: str, user_id: str, confirmed: bool,
    ) -> ChatResponse:
        """Confirm or reject a pending action."""
        session = await self._db.get_chat_session(session_id)
        if session is None:
            return ChatResponse(text="Session not found.", session_id=session_id)

        pending_raw = getattr(session, "pending_action", None)
        if not pending_raw:
            return ChatResponse(
                text="Nothing pending to confirm.",
                session_id=session_id,
            )

        if not confirmed:
            await self._db.update_chat_session(session_id, pending_action=None)
            return ChatResponse(text="Cancelled.", session_id=session_id)

        action_name, params = ActionRegistry.parse_pending_action(pending_raw)
        await self._db.update_chat_session(session_id, pending_action=None)

        assert self._action_registry is not None
        action = self._action_registry.get(action_name)
        if action is None:
            return ChatResponse(text=f"Unknown action: {action_name}", session_id=session_id)

        ctx = ActionContext(
            db=self._db, user_id=user_id, session_id=session_id,
            config=self._config, dashboard_context=None,
        )
        result = await self._action_registry.execute(action_name, params, ctx)
        response_text = await self._summarize_action_result(action, params, result, session)

        await self._db.add_chat_message(
            session_id=session_id,
            role="assistant",
            content=response_text,
            intent=action.domain,
            action_name=action_name,
            action_result=json.dumps(result.data) if result.data else None,
        )

        return ChatResponse(text=response_text, session_id=session_id)

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

    def _load_respond_template(self) -> str:
        """Load the chat respond template."""
        path = self._project_root / "prompts" / "chat" / "chat_respond.md"
        if path.exists():
            return path.read_text()
        return (
            "Respond to the user's message.\n\n"
            "{{ system_prompt }}\n\n"
            "## User Message\n\n{{ user_input }}"
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
        return str(result.get("summary", "Session ended."))

    def _estimate_escalation_cost(self) -> float:
        """Rough cost estimate for a Claude escalation call."""
        # ~4k tokens context + ~1k response, at Claude Sonnet pricing
        # $3/MTok input + $15/MTok output (approximate)
        return round(4 * 0.003 + 1 * 0.015, 3)
