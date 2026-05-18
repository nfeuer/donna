"""Conversation engine — core chat handler for Donna.

Single entry point for all chat interactions. Classifies intent,
assembles context, calls the local LLM, and manages sessions.
See docs/superpowers/specs/archive/2026-04-12-chat-interface-design.md.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
import uuid6

from donna.chat.actions import ActionRegistry
from donna.chat.config import ChatConfig
from donna.chat.context import (
    build_intent_context,
    build_session_context,
    render_chat_prompt,
)
from donna.chat.tools import ToolContext, ToolRegistry, truncate_result
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
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self._db = db
        self._router = router
        self._config = config
        self._project_root = project_root
        self._action_registry = action_registry
        self._tool_registry = tool_registry

    async def handle_message(
        self,
        session_id: str | None,
        user_id: str,
        text: str,
        channel: str,
        dashboard_context: dict[str, Any] | None = None,
        force_new: bool = False,
    ) -> ChatResponse:
        """Process a chat message and return a response.

        If session_id is None, resumes the active session or creates one.
        When force_new is True, always creates a new session instead of
        looking up an existing active session.
        """
        log = logger.bind(user_id=user_id, channel=channel)

        # Resolve or create session
        session = None
        if session_id:
            session = await self._db.get_chat_session(session_id)
        if session is None and not force_new:
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

        # Route to tool loop when tool_registry is available
        if self._tool_registry is not None:
            return await self._run_tool_loop(
                session=session,
                user_id=user_id,
                text=text,
                dashboard_context=dashboard_context,
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
            {p: f"<{p}>" for p in action.parameters.get("properties", {})},
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
            result_data=(
                json.dumps(result.data, indent=2, default=str)
                if result.data
                else (result.error or "No data")
            ),
        )

        resp, _ = await self._router.complete(
            prompt=prompt,
            task_type="chat_respond",
            user_id="system",
        )
        return str(resp.get("response_text", result.summary or "Done."))

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

    # ── Tool-use agent loop ────────────────────────────

    _TOOL_LOOP_MAX_CALLS = 10
    _TOOL_LOOP_TIMEOUT_S = 300  # 5 minutes wall-clock

    async def _run_tool_loop(
        self,
        session: Any,
        user_id: str,
        text: str,
        dashboard_context: dict[str, Any] | None,
    ) -> ChatResponse:
        """Run the tool-use agent loop instead of classify-dispatch-respond.

        The loop calls the LLM repeatedly. Each iteration, the LLM either
        returns a final text response or a tool_call. Read tools are executed
        inline and results fed back. Write tools pause the loop for
        confirmation. The loop terminates on text response, write-tool
        confirmation, max tool calls, wall-clock timeout, or consecutive
        malformed responses.
        """
        assert self._tool_registry is not None

        trace_id = str(uuid6.uuid7())
        log = logger.bind(trace_id=trace_id, session_id=session.id)

        # Build page context hint
        page_context = self._build_page_context(dashboard_context)

        # Load conversation history
        history = await self._db.list_chat_messages(session.id)
        history_text = "\n".join(
            f"{'User' if m.role == 'user' else 'Assistant'}: {m.content}"
            for m in history
        )

        # Load and render system prompt
        template_path = self._project_root / "prompts" / "chat" / "tool_agent_system.md"
        if template_path.exists():
            template = template_path.read_text()
        else:
            template = (
                "You are a helpful assistant with tool access.\n"
                "{{ tool_schemas }}\n{{ conversation_history }}"
            )

        tool_schemas = self._tool_registry.schemas_for_prompt()
        system_prompt = render_chat_prompt(
            template=template,
            user_input=text,
            user_name="Nick",
            conversation_history=history_text,
            page_context=page_context,
            tool_schemas=tool_schemas,
        )

        # Loop state
        loop_context: list[str] = []
        invocation_ids: list[str] = []
        tool_call_count = 0
        consecutive_malformed = 0
        start_time = time.monotonic()

        while True:
            # Wall-clock timeout check
            elapsed = time.monotonic() - start_time
            if elapsed > self._TOOL_LOOP_TIMEOUT_S:
                log.warning("chat.tool_loop_timeout", elapsed_s=elapsed)
                return self._tool_loop_fallback(
                    session, trace_id, invocation_ids,
                    "I ran out of time processing your request. Could you try again?",
                )

            # Max tool calls check
            if tool_call_count >= self._TOOL_LOOP_MAX_CALLS:
                log.warning(
                    "chat.tool_loop_max_calls",
                    tool_call_count=tool_call_count,
                )
                return self._tool_loop_fallback(
                    session, trace_id, invocation_ids,
                    "I've used all available tool calls for this request. "
                    "Here's what I found so far — could you narrow your question?",
                )

            # Build full prompt with accumulated tool results
            full_prompt = system_prompt
            if loop_context:
                full_prompt += "\n\n## Tool Results\n" + "\n".join(loop_context)

            # Call LLM
            response_data, metadata = await self._router.complete(
                prompt=full_prompt,
                task_type="chat_respond",
                user_id=user_id,
            )

            inv_id = getattr(metadata, "invocation_id", None)
            if inv_id:
                invocation_ids.append(str(inv_id))

            log.info(
                "chat.tool_loop_turn",
                turn=tool_call_count,
                tokens_in=getattr(metadata, "tokens_in", None),
                tokens_out=getattr(metadata, "tokens_out", None),
                cost_usd=getattr(metadata, "cost_usd", None),
            )

            # Parse response
            parsed = self._parse_tool_response(response_data)

            if parsed is None:
                consecutive_malformed += 1
                if consecutive_malformed >= 2:
                    log.warning("chat.tool_loop_malformed_limit")
                    return self._tool_loop_fallback(
                        session, trace_id, invocation_ids,
                        "I'm having trouble processing this request. "
                        "Could you rephrase your question?",
                    )
                # Append a retry hint to loop context
                loop_context.append(
                    "[System: Your previous response was malformed. "
                    "Respond with valid JSON: "
                    '{"type": "text", "response_text": "..."} or '
                    '{"type": "tool_call", "tool": "...", "params": {...}}]'
                )
                continue

            # Valid response resets malformed counter
            consecutive_malformed = 0

            resp_type = parsed.get("type")

            # ── Text response ──
            if resp_type == "text":
                response_text = parsed.get("response_text", "")
                await self._db.add_chat_message(
                    session_id=session.id,
                    role="assistant",
                    content=response_text,
                    trace_id=trace_id,
                    invocation_ids=json.dumps(invocation_ids),
                )
                self._log_loop_complete(
                    log, trace_id, tool_call_count, invocation_ids, start_time,
                )
                return ChatResponse(
                    text=response_text,
                    session_id=session.id,
                    trace_id=trace_id,
                    session_pinned_task_id=getattr(session, "pinned_task_id", None),
                )

            # ── Tool call ──
            if resp_type == "tool_call":
                tool_name = parsed.get("tool", "")
                params = parsed.get("params", {})

                # Validate tool exists
                tool_def = self._tool_registry.get(tool_name)
                if tool_def is None:
                    loop_context.append(
                        f"[System: Unknown tool '{tool_name}'. "
                        f"Available tools: "
                        f"{', '.join(t.name for t in self._tool_registry.list_tools())}]"
                    )
                    continue

                # Validate params
                validation_error = self._tool_registry.validate_params(tool_name, params)
                if validation_error:
                    loop_context.append(
                        f"[System: Parameter validation failed for {tool_name}: "
                        f"{validation_error}]"
                    )
                    continue

                # Check read vs write
                if not self._tool_registry.is_read_tool(tool_name):
                    # Write tool — pause loop, store pending action, return confirmation
                    pending = json.dumps({
                        "tool": tool_name,
                        "params": params,
                        "trace_id": trace_id,
                    })
                    await self._db.update_chat_session(
                        session.id, pending_action=pending,
                    )
                    desc = tool_def.description
                    param_summary = ", ".join(
                        f"{k}={v}" for k, v in params.items() if v
                    )
                    confirmation_text = (
                        f"I'd like to {desc.lower()}"
                        f"{' (' + param_summary + ')' if param_summary else ''}. "
                        f"Go ahead?"
                    )
                    await self._db.add_chat_message(
                        session_id=session.id,
                        role="assistant",
                        content=confirmation_text,
                        trace_id=trace_id,
                        invocation_ids=json.dumps(invocation_ids),
                    )
                    self._log_loop_complete(
                        log, trace_id, tool_call_count, invocation_ids,
                        start_time, paused_for_confirmation=True,
                    )
                    return ChatResponse(
                        text=confirmation_text,
                        session_id=session.id,
                        trace_id=trace_id,
                        session_pinned_task_id=getattr(session, "pinned_task_id", None),
                    )

                # Execute read tool
                tool_call_count += 1
                ctx = ToolContext(
                    db=self._db,
                    user_id=user_id,
                    session_id=session.id,
                )
                result = await self._tool_registry.execute(tool_name, params, ctx)
                result_json, was_truncated = truncate_result(result)

                loop_context.append(
                    f"### Tool: {tool_name}({json.dumps(params)})\n"
                    f"Result:\n{result_json}"
                )

                log.info(
                    "chat.tool_executed",
                    tool=tool_name,
                    result_count=result.total_count,
                    truncated=was_truncated,
                )
                continue

            # Unknown type — treat as malformed
            consecutive_malformed += 1
            if consecutive_malformed >= 2:
                log.warning("chat.tool_loop_malformed_limit")
                return self._tool_loop_fallback(
                    session, trace_id, invocation_ids,
                    "I'm having trouble processing this request. "
                    "Could you rephrase your question?",
                )
            loop_context.append(
                f"[System: Unrecognized response type '{resp_type}'. "
                f"Use 'text' or 'tool_call'.]"
            )

    def _parse_tool_response(self, response_data: Any) -> dict[str, Any] | None:
        """Parse an LLM response into a recognized structure.

        Handles:
        - dict with "type" key → return as-is
        - dict with "response_text" but no "type" → wrap as text
        - JSON string → parse and recurse
        - Anything else → None (malformed)
        """
        if isinstance(response_data, dict):
            if "type" in response_data:
                return response_data
            if "response_text" in response_data:
                return {"type": "text", "response_text": response_data["response_text"]}
            return None

        if isinstance(response_data, str):
            try:
                parsed = json.loads(response_data)
                if isinstance(parsed, dict):
                    return self._parse_tool_response(parsed)
            except (json.JSONDecodeError, ValueError):
                pass
            return None

        return None

    def _build_page_context(self, dashboard_context: dict[str, Any] | None) -> str:
        """Render a page context hint from dashboard_context."""
        if not dashboard_context:
            return ""
        page = dashboard_context.get("page", "unknown")
        selected = dashboard_context.get("selected_item")
        if selected:
            return (
                f"User is on the {page.title()} page, viewing "
                f"{selected.get('type', 'item')} '{selected.get('label', '')}' "
                f"(id: {selected.get('id', '')})."
            )
        return f"User is on the {page.title()} page."

    def _tool_loop_fallback(
        self,
        session: Any,
        trace_id: str,
        invocation_ids: list[str],
        text: str,
    ) -> ChatResponse:
        """Return a fallback ChatResponse when the tool loop terminates abnormally."""
        return ChatResponse(
            text=text,
            session_id=session.id,
            trace_id=trace_id,
            session_pinned_task_id=getattr(session, "pinned_task_id", None),
        )

    def _log_loop_complete(
        self,
        log: Any,
        trace_id: str,
        tool_call_count: int,
        invocation_ids: list[str],
        start_time: float,
        paused_for_confirmation: bool = False,
    ) -> None:
        """Emit the summary structured log event for a completed tool loop."""
        elapsed = time.monotonic() - start_time
        log.info(
            "chat.tool_loop_complete",
            trace_id=trace_id,
            tool_calls=tool_call_count,
            invocations=len(invocation_ids),
            elapsed_s=round(elapsed, 3),
            paused_for_confirmation=paused_for_confirmation,
        )

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
