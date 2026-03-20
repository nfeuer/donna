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

import uuid
from collections.abc import Callable, Awaitable
from datetime import datetime
from typing import Any

import discord
import structlog

from donna.orchestrator.input_parser import InputParser
from donna.tasks.database import Database
from donna.tasks.db_models import DeadlineType, InputChannel, TaskDomain

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
        overdue_reply_handler: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self._input_parser = input_parser
        self._database = database
        self._tasks_channel_id = tasks_channel_id
        self._debug_channel_id = debug_channel_id
        self._digest_channel_id = digest_channel_id
        self._overdue_reply_handler = overdue_reply_handler
        # Maps Discord thread ID → task ID for overdue nudge reply routing.
        self.overdue_threads: dict[int, str] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_ready(self) -> None:
        """Log bot online status and announce in #donna-debug if configured."""
        logger.info("discord_bot_ready", user=str(self.user))
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

            await message.channel.send(
                f"Got it. '{task.title}' — {task.domain}, priority {task.priority}."
                " Scheduled: pending."
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
