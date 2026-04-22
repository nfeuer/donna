"""Discord bot integration — primary input channel for Donna.

Listens for messages in #donna-tasks, pipes them through the input parser,
stores parsed tasks in the database, and confirms back in the same channel.

On failure (circuit breaker open / API down), stores raw text with a
_parse_error tag for later re-processing and sends a degraded confirmation.

Also supports outbound messaging (reminders, overdue nudges, morning digest)
and routes user replies in overdue threads to the OverdueDetector.

See docs/notifications.md and slices/slice_03_discord.md.
"""

from __future__ import annotations

import contextlib
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

import discord
import structlog
from discord import app_commands

from donna.integrations.discord_pending_drafts import PendingDraft
from donna.orchestrator.input_parser import DuplicateDetectedError, InputParser
from donna.preferences.correction_logger import log_correction
from donna.tasks.database import Database, TaskRow
from donna.tasks.db_models import DeadlineType, InputChannel, TaskDomain

if TYPE_CHECKING:
    from donna.orchestrator.dispatcher import AgentDispatcher

logger = structlog.get_logger()

LOW_CONFIDENCE_THRESHOLD = 0.7


class DonnaBot(discord.Client):
    """Discord bot that captures tasks from #donna-tasks.

    On each message in the configured tasks channel:
      - Parses natural language via InputParser
      - Creates a task record in the database (confidence ≥ 0.7)
      - Replies with confirmation, or asks for clarification (confidence < 0.7)
      - Falls back to raw-text storage when the API is unavailable

    Outbound support (Slice 5):
      - send_message / send_embed / send_to_thread for notifications
      - Tracks overdue_threads (thread_id → task_id) for reply routing

    Channel IDs are supplied at construction time from environment variables.
    """

    def __init__(
        self,
        input_parser: InputParser,
        database: Database,
        tasks_channel_id: int,
        debug_channel_id: int | None = None,
        digest_channel_id: int | None = None,
        agents_channel_id: int | None = None,
        guild_id: int | None = None,
        overdue_reply_handler: Callable[[str, str], Awaitable[None]] | None = None,
        dispatcher: AgentDispatcher | None = None,
        chat_channel_id: int | None = None,
        chat_engine: Any | None = None,
        intent_dispatcher: Any | None = None,
        automation_repo: Any | None = None,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self._input_parser = input_parser
        self._database = database
        self._tasks_channel_id = tasks_channel_id
        self._debug_channel_id = debug_channel_id
        self._digest_channel_id = digest_channel_id
        self._agents_channel_id = agents_channel_id
        self._guild_id = guild_id
        self._overdue_reply_handler = overdue_reply_handler
        self._dispatcher = dispatcher
        self._chat_channel_id = chat_channel_id
        self._chat_engine = chat_engine
        # Wave 3: DiscordIntentDispatcher-driven routing.
        self._intent_dispatcher = intent_dispatcher
        self._automation_repo = automation_repo
        # Command tree for slash commands (may fail if Client not fully initialized).
        try:
            self.tree = app_commands.CommandTree(self)
        except AttributeError:
            self.tree = None  # type: ignore[assignment]
        # Maps Discord thread ID → task ID for overdue nudge reply routing.
        self.overdue_threads: dict[int, str] = {}
        # Maps Discord thread ID → task ID for challenger follow-up routing.
        self._challenger_threads: dict[int, str] = {}
        # Maps user_id → (new_parse_result_title, new_description, new_domain, existing_task)
        # for pending dedup decisions awaiting user reply.
        self._dedup_pending: dict[str, tuple[str, str | None, str, TaskRow]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_ready(self) -> None:
        """Log bot online status, sync commands, and announce in #donna-debug."""
        logger.info("discord_bot_ready", user=str(self.user))

        # Sync slash commands to guild for instant registration.
        if self._guild_id is not None and self.tree is not None:
            try:
                guild = discord.Object(id=self._guild_id)
                synced = await self.tree.sync(guild=guild)
                logger.info("command_tree_synced", count=len(synced), guild_id=self._guild_id)
            except Exception:
                logger.exception("command_tree_sync_failed")

        if self._debug_channel_id is not None:
            channel = self.get_channel(self._debug_channel_id)
            if channel is not None and hasattr(channel, "send"):
                await channel.send("Donna is online.")  # type: ignore[union-attr]

    # ------------------------------------------------------------------
    # Outbound messaging (Slice 5)
    # ------------------------------------------------------------------

    def _resolve_channel(self, channel_name: str) -> discord.abc.Messageable | None:
        """Map a channel name string to a Discord channel object."""
        mapping: dict[str, int | None] = {
            "tasks": self._tasks_channel_id,
            "debug": self._debug_channel_id,
            "digest": self._digest_channel_id,
            "agents": self._agents_channel_id,
            "chat": self._chat_channel_id,
        }
        channel_id = mapping.get(channel_name)
        if channel_id is None:
            return None
        ch = self.get_channel(channel_id)
        if ch is None or not hasattr(ch, "send"):
            return None
        return ch  # type: ignore[return-value]

    async def send_message(self, channel_name: str, text: str) -> discord.Message | None:
        """Send a plain-text message to a named channel.

        Returns the sent Message, or None if the channel is unavailable.
        """
        channel = self._resolve_channel(channel_name)
        if channel is None:
            logger.warning("send_message_channel_unavailable", channel_name=channel_name)
            return None
        msg: discord.Message = await channel.send(text)  # type: ignore[union-attr]
        return msg

    async def send_embed(self, channel_name: str, embed: discord.Embed) -> discord.Message | None:
        """Send a Discord embed to a named channel.

        Returns the sent Message, or None if the channel is unavailable.
        """
        channel = self._resolve_channel(channel_name)
        if channel is None:
            logger.warning("send_embed_channel_unavailable", channel_name=channel_name)
            return None
        msg: discord.Message = await channel.send(embed=embed)  # type: ignore[union-attr]
        return msg

    async def send_message_with_view(
        self,
        channel_name: str,
        text: str,
        view: discord.ui.View,
        embed: discord.Embed | None = None,
    ) -> discord.Message | None:
        """Send a message with an interactive View to a named channel."""
        channel = self._resolve_channel(channel_name)
        if channel is None:
            logger.warning("send_message_with_view_channel_unavailable", channel_name=channel_name)
            return None
        kwargs: dict[str, Any] = {"content": text, "view": view}
        if embed is not None:
            kwargs["embed"] = embed
        msg: discord.Message = await channel.send(**kwargs)  # type: ignore[union-attr]
        return msg

    async def send_to_thread(self, thread_id: int, text: str) -> None:
        """Send a message to an existing Discord thread by ID."""
        thread = self.get_channel(thread_id)
        if thread is None or not hasattr(thread, "send"):
            logger.warning("send_to_thread_unavailable", thread_id=thread_id)
            return
        await thread.send(text)  # type: ignore[union-attr]

    async def create_overdue_thread(
        self,
        task_id: str,
        task_title: str,
        nudge_text: str,
    ) -> int | None:
        """Send an overdue nudge as a new thread in #donna-tasks.

        Returns the thread ID so the reply handler can be registered,
        or None if the channel is unavailable.
        """
        channel = self._resolve_channel("tasks")
        if channel is None:
            logger.warning("create_overdue_thread_channel_unavailable")
            return None

        # discord.TextChannel supports create_thread; narrowing via hasattr.
        if not hasattr(channel, "send"):
            return None

        msg: discord.Message = await channel.send(nudge_text)  # type: ignore[union-attr]

        if hasattr(msg, "create_thread"):
            thread = await msg.create_thread(name=f"Overdue: {task_title[:80]}")
            self.overdue_threads[thread.id] = task_id
            logger.info(
                "overdue_thread_created",
                task_id=task_id,
                thread_id=thread.id,
            )
            return thread.id

        # Fallback: channel doesn't support threading; track the message channel.
        self.overdue_threads[msg.channel.id] = task_id
        return msg.channel.id

    # ------------------------------------------------------------------
    # Inbound message handling
    # ------------------------------------------------------------------

    async def on_message(self, message: discord.Message) -> None:
        """Handle incoming messages in #donna-tasks.

        Pipeline:
          1. Filter: ignore bots and wrong channels.
          2. Bind correlation_id and user context to structlog.
          3. Parse raw text via InputParser.
          4. Low confidence → ask for clarification, skip task creation.
          5. High confidence → create task in DB, send confirmation.
          6. Any exception → store raw text with _parse_error tag, send
             degraded confirmation.
        """
        # Ignore bots (including self)
        if message.author.bot:
            return

        # Route overdue-thread replies before the tasks-channel filter.
        if message.channel.id in self.overdue_threads and self._overdue_reply_handler is not None:
            task_id = self.overdue_threads[message.channel.id]
            reply = message.content.strip().lower()
            logger.info(
                "overdue_reply_received",
                task_id=task_id,
                reply=reply[:50],
            )
            try:
                await self._overdue_reply_handler(task_id, reply)
            except Exception:
                logger.exception("overdue_reply_handler_failed", task_id=task_id)
            return

        # Route challenger-thread replies: append answers to task and re-dispatch.
        if message.channel.id in self._challenger_threads:
            task_id = self._challenger_threads[message.channel.id]
            reply = message.content.strip()
            await self._handle_challenger_reply(message, task_id, reply)
            return

        # Route chat channel messages to conversation engine.
        if (
            self._chat_channel_id is not None
            and message.channel.id == self._chat_channel_id
            and self._chat_engine is not None
        ):
            await self._handle_chat_message(message)
            return

        # Ignore messages outside the designated tasks channel
        if message.channel.id != self._tasks_channel_id:
            return

        correlation_id = str(uuid.uuid4())
        user_id = str(message.author.id)
        raw_text = message.content.strip()

        log = logger.bind(
            correlation_id=correlation_id,
            user_id=user_id,
            channel="discord",
        )

        # Stateful pre-checks that must short-circuit before the Wave 3
        # intent dispatcher branch. These handle in-flight conversations
        # (merge/keep/update replies, field-update commands) and must not
        # be re-routed through Claude's novelty judge.

        # Route pending dedup decisions: user is replying to a duplicate prompt.
        if user_id in self._dedup_pending:
            await self._handle_dedup_reply(message, user_id, log)
            return

        # Detect and handle field-update commands ("change priority to 3", "move to work domain").
        field_update = _detect_field_update(raw_text)
        if field_update is not None:
            await self._handle_field_update(message, user_id, raw_text, field_update, log)
            return

        # Wave 3: if the intent dispatcher is wired, route the message through
        # it instead of the legacy InputParser flow. The stateful pre-checks
        # above always take precedence.
        if self._intent_dispatcher is not None:
            await self._handle_tasks_channel_via_dispatcher(message)
            return

        log.info("discord_message_received", raw_text=raw_text[:200])

        try:
            result = await self._input_parser.parse(
                raw_text, user_id=user_id, channel="discord"
            )

            if result.confidence < LOW_CONFIDENCE_THRESHOLD:
                log.info("low_confidence_parse", confidence=result.confidence)
                await message.channel.send(
                    "I'm not sure I understood that. Could you give me a bit more detail?"
                )
                return

            # Map domain string → enum (graceful fallback to PERSONAL)
            try:
                domain = TaskDomain(result.domain)
            except ValueError:
                log.warning("unknown_domain_value", domain=result.domain)
                domain = TaskDomain.PERSONAL

            # Map deadline_type string → enum
            try:
                deadline_type = DeadlineType(result.deadline_type)
            except ValueError:
                deadline_type = DeadlineType.NONE

            # Parse ISO deadline string → datetime if present
            deadline: datetime | None = None
            if result.deadline:
                try:
                    deadline = datetime.fromisoformat(result.deadline)
                except ValueError:
                    log.warning("unparseable_deadline", deadline=result.deadline)

            task = await self._database.create_task(
                user_id=user_id,
                title=result.title,
                description=result.description,
                domain=domain,
                priority=result.priority,
                deadline=deadline,
                deadline_type=deadline_type,
                estimated_duration=result.estimated_duration,
                tags=result.tags if result.tags else None,
                prep_work_flag=result.prep_work_flag,
                agent_eligible=result.agent_eligible,
                created_via=InputChannel.DISCORD,
            )

            log.info("task_created_via_discord", task_id=task.id, title=task.title)

            from donna.integrations.discord_views import TaskConfirmationView

            confirmation_view = TaskConfirmationView(
                task_id=task.id, db=self._database
            )
            await message.channel.send(
                f"Got it. '{task.title}' — {task.domain}, priority {task.priority}."
                " Scheduled: pending.",
                view=confirmation_view,
            )

            # Run challenger agent to probe task quality (if dispatcher is wired).
            if self._dispatcher is not None:
                await self._run_challenger(message, task, user_id, log)

        except DuplicateDetectedError as dup:
            # Dedup triggered: store pending context and prompt user.
            self._dedup_pending[user_id] = (
                dup.new_title,
                None,
                "",
                dup.existing_task,
            )
            log.info(
                "dedup_duplicate_detected",
                new_title=dup.new_title,
                existing_task_id=dup.existing_task.id,
                verdict=dup.verdict,
                fuzzy_score=dup.fuzzy_score,
            )
            created_at = dup.existing_task.created_at[:10] if dup.existing_task.created_at else "unknown date"
            await message.channel.send(
                f"This looks like a duplicate of **'{dup.existing_task.title}'** "
                f"(created {created_at}). "
                f"Reply with **merge**, **keep both**, or **update existing**."
            )

        except Exception:
            log.exception("discord_message_capture_failed", raw_text=raw_text[:200])

            # Degraded mode: persist raw text so nothing is lost
            try:
                await self._database.create_task(
                    user_id=user_id,
                    title=raw_text[:500],
                    tags=["_parse_error"],
                    created_via=InputChannel.DISCORD,
                )
                log.info("degraded_raw_task_stored")
            except Exception:
                log.exception("degraded_mode_storage_failed")

            await message.channel.send(
                "Captured your message. I'll parse it properly when my brain"
                " comes back online."
            )

    # ------------------------------------------------------------------
    # Wave 3: DiscordIntentDispatcher routing
    # ------------------------------------------------------------------

    async def _handle_tasks_channel_via_dispatcher(
        self, message: discord.Message
    ) -> None:
        """Route a tasks-channel message through the DiscordIntentDispatcher.

        Branches on DispatchResult.kind:
          - task_created → confirmation reply
          - clarification_posted → thread + question
          - automation_confirmation_needed → post the AutomationConfirmationView
          - chat / no_action → return
        """
        log = logger.bind(
            user_id=str(message.author.id),
            channel="discord",
        )

        # Build a minimal duck-typed message for the dispatcher.
        thread_id: int | None = None
        thread_obj = getattr(message, "thread", None)
        if thread_obj is not None and hasattr(thread_obj, "id"):
            try:
                thread_id = int(thread_obj.id)
            except (TypeError, ValueError):
                thread_id = None

        class _Msg:
            content = message.content
            author_id = str(message.author.id)

        _Msg.thread_id = thread_id  # type: ignore[attr-defined]

        try:
            result = await self._intent_dispatcher.dispatch(_Msg())
        except Exception:
            log.exception("intent_dispatch_failed")
            await message.channel.send(
                "Something went wrong routing that message. Try again in a moment."
            )
            return

        kind = getattr(result, "kind", "no_action")

        if kind == "task_created":
            task_id = getattr(result, "task_id", None) or "?"
            await message.channel.send(f"Task captured (`{task_id}`).")
            return

        if kind == "clarification_posted":
            question = (
                getattr(result, "clarifying_question", None) or "Need more info."
            )
            try:
                thread = await message.create_thread(name="Clarification")
                await thread.send(question)
            except Exception:
                log.exception("clarification_thread_create_failed")
                await message.channel.send(question)
            return

        if kind == "automation_confirmation_needed":
            draft = getattr(result, "draft_automation", None)
            if draft is None:
                log.warning("automation_confirmation_missing_draft")
                return
            await self._send_automation_confirmation(message, draft, log)
            return

        if kind == "chat":
            return

        if kind == "no_action":
            log.info(
                "on_message_no_action",
                content_preview=(message.content or "")[:60],
            )
            return

        log.warning("on_message_unknown_dispatch_kind", kind=kind)

    async def _send_automation_confirmation(
        self,
        message: discord.Message,
        draft: Any,
        log: Any,
    ) -> None:
        """Post the AutomationConfirmationView and await user decision."""
        from donna.integrations.discord_views import AutomationConfirmationView

        name = _suggest_automation_name(draft)
        view = AutomationConfirmationView(draft=draft, name=name)
        try:
            await message.channel.send(embed=view.build_embed(), view=view)
        except Exception:
            log.exception("automation_confirmation_send_failed")
            return

        # Wait for the user to click Approve/Edit/Cancel (or view to time out).
        # discord.py runs views on its own task scheduler — awaiting here only
        # blocks this on_message coroutine, not the bot's event loop.
        try:
            await view.wait()
        except Exception:
            log.exception("automation_confirmation_wait_failed")
            return

        decision = getattr(view, "result", None)
        if decision == "approve":
            await self._approve_automation_draft(message, view, log)
        elif decision == "edit":
            await self._store_automation_edit_draft(message, view, log)
        elif decision == "cancel":
            # View already replaced itself with a "Cancelled" message.
            return
        else:
            log.info("automation_confirmation_timeout")

    async def _store_automation_edit_draft(
        self,
        message: discord.Message,
        view: Any,
        log: Any,
    ) -> None:
        """Persist a PendingDraft so the user's next message resumes the edit.

        Wave 3 F-W3-F MVP: the AutomationConfirmationView "Edit" button sets
        ``view.result = "edit"`` and the view already sent a follow-up prompt.
        To make that prompt actionable we seed a PendingDraft keyed on the
        DM-fallback key (``dm:{user_id}``) so the next message routed through
        DiscordIntentDispatcher.dispatch hits the ``_resume`` path with the
        draft's prior context and re-parses into a fresh DraftAutomation.
        """
        log.info(
            "automation_edit_requested",
            user_id=str(message.author.id),
        )
        if self._intent_dispatcher is None:
            return
        draft = view.draft
        draft_snapshot = {
            "user_id": getattr(draft, "user_id", None),
            "capability_name": getattr(draft, "capability_name", None),
            "inputs": getattr(draft, "inputs", None) or {},
            "schedule_cron": getattr(draft, "schedule_cron", None),
            "alert_conditions": getattr(draft, "alert_conditions", None),
            "target_cadence_cron": getattr(draft, "target_cadence_cron", None),
            "active_cadence_cron": getattr(draft, "active_cadence_cron", None),
        }
        pending = PendingDraft(
            user_id=str(message.author.id),
            thread_id=f"dm:{message.author.id}",
            draft_kind="automation",
            partial={
                "extracted_inputs": draft_snapshot["inputs"],
                "edit_snapshot": draft_snapshot,
            },
            capability_name=draft_snapshot["capability_name"],
        )
        try:
            self._intent_dispatcher._drafts.set(pending)
        except Exception:
            log.exception(
                "automation_edit_pending_draft_store_failed",
                user_id=str(message.author.id),
            )

    async def _approve_automation_draft(
        self,
        message: discord.Message,
        view: Any,
        log: Any,
    ) -> None:
        """Persist an approved DraftAutomation via AutomationCreationPath."""
        if self._automation_repo is None:
            log.warning("automation_approve_no_repo")
            await message.channel.send(
                "Automation flow isn't fully wired yet — nothing saved."
            )
            return

        from donna.automations.creation_flow import AutomationCreationPath, MissingToolError

        default_min_interval = getattr(
            self, "_automation_default_min_interval_seconds", 300
        )
        creation = AutomationCreationPath(
            repository=self._automation_repo,
            default_min_interval_seconds=default_min_interval,
            tool_registry=getattr(self, "_automation_tool_registry", None),
            capability_tool_lookup=getattr(self, "_automation_capability_lookup", None),
            capability_input_schema_lookup=getattr(self, "_automation_input_schema_lookup", None),
        )
        try:
            automation_id = await creation.approve(view.draft, name=view.name)
        except MissingToolError as exc:
            if len(exc.missing) == 1:
                tools_str = f"`{exc.missing[0]}`"
                verb = "is"
            else:
                tools_str = ", ".join(f"`{t}`" for t in exc.missing[:-1]) + f" and `{exc.missing[-1]}`"
                verb = "are"
            msg = (
                f"I can't run `{exc.capability}` until "
                f"{tools_str} {verb} connected — "
                f"set that up first and try again."
            )
            await message.channel.send(msg)
            return
        except Exception:
            log.exception("automation_creation_failed", name=view.name)
            await message.channel.send(
                f"Couldn't create automation `{view.name}`."
            )
            return

        if automation_id:
            await message.channel.send(
                f"Automation created (`{automation_id}`)."
            )
        else:
            await message.channel.send(
                f"Automation `{view.name}` already exists."
            )

    async def _handle_dedup_reply(
        self,
        message: discord.Message,
        user_id: str,
        log: Any,
    ) -> None:
        """Handle a user's merge/keep/update reply to a duplicate prompt."""
        new_title, new_description, new_domain, existing_task = self._dedup_pending.pop(user_id)
        reply = message.content.strip().lower()

        log.info("dedup_user_reply", reply=reply[:50], existing_task_id=existing_task.id)

        if "merge" in reply:
            # Combine new title info into existing task notes.
            import json as _json
            existing_notes: list[str] = []
            if existing_task.notes:
                with contextlib.suppress(Exception):
                    existing_notes = _json.loads(existing_task.notes)
            merged_notes = [*existing_notes, f"[merged from: {new_title}]"]
            await self._database.update_task(existing_task.id, notes=merged_notes)
            log.info("dedup_merged", existing_task_id=existing_task.id, new_title=new_title)
            await message.channel.send(
                f"Merged. '{new_title}' folded into '{existing_task.title}'."
            )

        elif "keep" in reply:
            # Create the new task linked to the existing one.
            await self._database.create_task(
                user_id=user_id,
                title=new_title,
                description=new_description,
                parent_task=existing_task.id,
                created_via=InputChannel.DISCORD,
            )
            log.info("dedup_kept_both", existing_task_id=existing_task.id, new_title=new_title)
            await message.channel.send(
                f"Kept both. '{new_title}' created and linked to '{existing_task.title}'."
            )

        elif "update" in reply:
            # Update the existing task title/description with the new input.
            updates: dict[str, Any] = {"title": new_title}
            if new_description:
                updates["description"] = new_description
            await self._database.update_task(existing_task.id, **updates)
            log.info("dedup_updated_existing", existing_task_id=existing_task.id)
            await message.channel.send(
                f"Updated '{existing_task.title}' with the new details."
            )

        else:
            # Unrecognised reply: re-queue and re-prompt.
            self._dedup_pending[user_id] = (new_title, new_description, new_domain, existing_task)
            await message.channel.send(
                "I didn't catch that. Reply with **merge**, **keep both**, or **update existing**."
            )

    async def _handle_field_update(
        self,
        message: discord.Message,
        user_id: str,
        raw_text: str,
        field_update: tuple[str, str],
        log: Any,
    ) -> None:
        """Apply a field-update command and log the correction.

        Finds the user's most recent non-done, non-cancelled task and applies
        the detected field change (priority or domain). Logs the before/after
        values to correction_log for preference learning.

        Args:
            message: The incoming Discord message.
            user_id: The Discord user ID string.
            raw_text: Original message text.
            field_update: (field_name, new_value) pair from _detect_field_update.
            log: Bound structlog logger.
        """
        field, new_value = field_update

        # Find the most recent active task for this user.
        all_tasks = await self._database.list_tasks(user_id=user_id)
        active_tasks = [
            t for t in all_tasks
            if t.status not in ("done", "cancelled")
        ]
        if not active_tasks:
            await message.channel.send(
                "No active tasks to update. Create a task first."
            )
            return

        # Most recent first (list_tasks returns newest last; take the last).
        task = active_tasks[-1]

        # Capture original value before update.
        original_value = str(getattr(task, field, "") or "")

        # Apply the update.
        try:
            if field == "priority":
                await self._database.update_task(task.id, priority=int(new_value))
            elif field == "domain":
                domain_enum = TaskDomain(new_value.upper())
                await self._database.update_task(task.id, domain=domain_enum)
            else:
                log.warning("field_update_unsupported_field", field=field)
                return
        except (ValueError, Exception):
            log.exception("field_update_apply_failed", field=field, new_value=new_value)
            await message.channel.send(
                f"Couldn't update {field} to '{new_value}'. Check the value and try again."
            )
            return

        # Log the correction.
        try:
            await log_correction(
                db=self._database,
                user_id=user_id,
                task_id=task.id,
                task_type="discord_command",
                field=field,
                original=original_value,
                corrected=new_value,
                input_text=raw_text,
            )
        except Exception:
            log.exception("correction_log_failed", field=field)

        log.info(
            "field_update_applied",
            task_id=task.id,
            field=field,
            original=original_value,
            corrected=new_value,
        )
        await message.channel.send(
            f"Updated '{task.title}': {field} changed from {original_value} to {new_value}."
        )

    # ------------------------------------------------------------------
    # Challenger agent integration
    # ------------------------------------------------------------------

    async def _run_challenger(
        self,
        message: discord.Message,
        task: TaskRow,
        user_id: str,
        log: Any,
    ) -> None:
        """Dispatch task through the challenger agent; open a thread if questions arise."""
        if self._dispatcher is None:
            return

        try:
            result = await self._dispatcher.dispatch(task, user_id=user_id)
        except Exception:
            log.exception("challenger_dispatch_failed", task_id=task.id)
            return

        if result.status == "needs_input" and result.questions:
            try:
                thread = await message.create_thread(
                    name=f"Details: {task.title[:80]}",
                )
                self._challenger_threads[thread.id] = task.id
                for q in result.questions:
                    await thread.send(q)
                log.info(
                    "challenger_thread_created",
                    task_id=task.id,
                    thread_id=thread.id,
                    question_count=len(result.questions),
                )
            except Exception:
                log.exception("challenger_thread_create_failed", task_id=task.id)

    async def _handle_challenger_reply(
        self,
        message: discord.Message,
        task_id: str,
        reply: str,
    ) -> None:
        """Append user's challenger reply to task notes and re-dispatch."""
        log = logger.bind(task_id=task_id, channel="discord")

        task = await self._database.get_task(task_id)
        if task is None:
            log.warning("challenger_reply_task_not_found")
            return

        # Append the reply to the task's notes.
        import json as _json

        existing_notes: list[str] = _json.loads(task.notes) if task.notes else []
        existing_notes.append(f"Challenger follow-up: {reply}")
        await self._database.update_task(task_id, notes=existing_notes)

        # Append to description for richer context.
        new_desc = (task.description or "") + f"\n\nAdditional context: {reply}"
        await self._database.update_task(task_id, description=new_desc.strip())

        log.info("challenger_reply_applied", reply=reply[:200])
        await message.channel.send("Got it, added to the task.")

        # Clean up thread tracking — one round of follow-up is enough.
        self._challenger_threads.pop(message.channel.id, None)

    async def _handle_chat_message(self, message: discord.Message) -> None:
        """Route a #donna-chat message through the ConversationEngine."""
        user_id = str(message.author.id)
        text = message.content.strip()
        log = logger.bind(user_id=user_id, channel="discord_chat")

        try:
            resp = await self._chat_engine.handle_message(
                session_id=None,
                user_id=user_id,
                text=text,
                channel="discord",
            )

            if resp.needs_escalation:
                from donna.integrations.discord_views import EscalationApprovalView

                view = EscalationApprovalView(
                    session_id=resp.session_pinned_task_id or "unknown",
                    chat_engine=self._chat_engine,
                    user_id=user_id,
                )
                await message.channel.send(resp.text, view=view)
            else:
                await message.channel.send(resp.text)

        except Exception:
            log.exception("chat_message_failed")
            await message.channel.send(
                "Something went wrong. Try again in a moment."
            )


# ------------------------------------------------------------------
# Automation name suggestion
# ------------------------------------------------------------------


def _suggest_automation_name(draft: Any) -> str:
    """Simple deterministic name: capability + first meaningful input token."""
    cap = getattr(draft, "capability_name", None) or "claude_native"
    inputs = getattr(draft, "inputs", None) or {}
    first_input = next(iter(inputs.values()), None) if inputs else None
    if first_input:
        token = str(first_input).split("/")[-1][:20]
        return f"{cap}_{token}"
    return cap


# ------------------------------------------------------------------
# Field-update command detection
# ------------------------------------------------------------------

# Regex patterns for detecting field-update commands.
_PRIORITY_RE = re.compile(
    r"(?:change|set|update)\s+priority\s+to\s+([1-5])"
    r"|priority\s+([1-5])",
    re.IGNORECASE,
)
_DOMAIN_RE = re.compile(
    r"(?:change|set|move\s+to|update)\s+(?:domain\s+to\s+|to\s+)?(?:the\s+)?"
    r"(personal|work|family)\s*(?:domain)?",
    re.IGNORECASE,
)


def _detect_field_update(text: str) -> tuple[str, str] | None:
    """Detect a field-update command in a Discord message.

    Recognised patterns:
      - "change priority to 3" / "set priority to 4" / "priority 2"
      - "move to work domain" / "change domain to personal" / "set domain to family"

    Args:
        text: Raw message content.

    Returns:
        (field_name, new_value) tuple if detected, else None.
    """
    m = _PRIORITY_RE.search(text)
    if m:
        new_val = m.group(1) or m.group(2)
        return ("priority", new_val)

    m = _DOMAIN_RE.search(text)
    if m:
        return ("domain", m.group(1).upper())

    return None
