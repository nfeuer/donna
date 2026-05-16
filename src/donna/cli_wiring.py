"""Orchestrator startup wiring — extracted from cli._run_orchestrator (F-W2-E).

This module splits the formerly-330-line `_run_orchestrator` into a
`StartupContext` + three typed helpers:

  * `build_startup_context(args)` — config loading, DB open, router,
    input_parser, bot construction, notification service, user/port.
  * `wire_skill_system(ctx)` — registers default tools, seeds capabilities,
    constructs the SkillSystemBundle, nightly cron, and manual-draft poller.
  * `wire_automation_subsystem(ctx, skill_h)` — AutomationRepository,
    AutomationDispatcher, AutomationScheduler.
  * `wire_discord(ctx, skill_h, automation_h)` — registers slash commands,
    logs proactive-prompt config, and schedules `bot.start()` onto the
    shared asyncio task list.

Each helper returns a typed `@dataclass` handle so the next stage can
read the concrete objects it needs (`skill_h.bundle.lifecycle_manager`,
`automation_h.scheduler`, etc.) without reaching into a grab-bag dict.

Behaviour must match the pre-refactor `_run_orchestrator` exactly — all
log keys, task-creation order, and guarded try/except blocks are
preserved verbatim.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import zoneinfo
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path
from typing import Any

import structlog

from donna.automations.dispatcher import AutomationDispatcher
from donna.automations.repository import AutomationRepository
from donna.automations.scheduler import AutomationScheduler
from donna.config import (
    ManualEscalationConfig,
    ModelsConfig,
    PromptDeliveryConfig,
    SkillSystemConfig,
    TaskTypesConfig,
    load_manual_escalation_config,
    load_models_config,
    load_skill_system_config,
    load_state_machine_config,
    load_task_types_config,
)
from donna.cost.budget import BudgetGuard
from donna.cost.budget_extension import BudgetExtensionRepository
from donna.cost.claude_code_poller import ClaudeCodePoller
from donna.cost.claude_code_spec import ClaudeCodeSpecBuilder, expand_workspace_path
from donna.cost.dashboard_setting import DashboardSettingResolver
from donna.cost.escalation_audit import EVENT_EXTENSION_VOIDED, write_escalation_event
from donna.cost.escalation_gate import EscalationGate
from donna.cost.escalation_repository import EscalationRepository
from donna.cost.manual_validation_router import ManualValidationRouter
from donna.cost.tracker import CostTracker
from donna.integrations.git_repo import GitRepo
from donna.logging.invocation_logger import InvocationLogger
from donna.models.router import ModelRouter
from donna.notifications.escalation_delivery_loop import EscalationDeliveryLoop
from donna.notifications.service import NotificationService
from donna.orchestrator.input_parser import InputParser
from donna.skills.startup_wiring import SkillSystemBundle
from donna.tasks.database import Database
from donna.tasks.state_machine import StateMachine

logger = structlog.get_logger()


def _try_build_gmail_client(config_dir: Path) -> Any | None:
    """Attempt to construct a GmailClient from config/email.yaml.

    Returns None on any failure (missing file, creds file missing, construction
    raises). Non-fatal — the capability-availability guard surfaces the
    missing-tool state at automation-approval time via an actionable DM.
    """
    email_yaml = config_dir / "email.yaml"
    if not email_yaml.exists():
        return None
    try:
        from donna.config import load_email_config
        from donna.integrations.gmail import GmailClient

        email_cfg = load_email_config(config_dir)
        token_path = Path(email_cfg.credentials.token_path)
        secrets_path = Path(email_cfg.credentials.client_secrets_path)
        if not token_path.exists() or not secrets_path.exists():
            logger.warning(
                "gmail_client_unavailable",
                reason="credential_file_missing",
                token_exists=token_path.exists(),
                secrets_exists=secrets_path.exists(),
            )
            return None
        return GmailClient(config=email_cfg)
    except Exception as exc:
        logger.warning("gmail_client_unavailable", reason=str(exc))
        return None


def _try_build_vault_client(config_dir: Path) -> Any | None:
    """Attempt to construct a :class:`VaultClient` from ``config/memory.yaml``.

    Non-fatal: returns None if the config file is missing or the vault
    root's parent directory cannot be accessed. Missing vault config
    degrades the skill system to the pre-slice-12 baseline (no vault
    tools registered); it does not prevent boot.
    """
    memory_yaml = config_dir / "memory.yaml"
    if not memory_yaml.exists():
        return None
    try:
        from donna.config import load_memory_config
        from donna.integrations.vault import VaultClient

        memory_cfg = load_memory_config(config_dir)
        vault_root = Path(memory_cfg.vault.root)
        if not vault_root.parent.exists():
            logger.warning(
                "vault_client_unavailable",
                reason="vault_root_parent_missing",
                vault_root=str(vault_root),
            )
            return None
        return VaultClient(config=memory_cfg)
    except Exception as exc:
        logger.warning("vault_client_unavailable", reason=str(exc))
        return None


async def _try_build_memory_store(
    config_dir: Path,
    db: Any,
    user_id: str,
    invocation_logger: InvocationLogger,
    vault_client: Any | None,
) -> tuple[Any | None, tuple[Any, Any | None] | None]:
    """Construct the slice-13 memory pipeline (non-fatal).

    Returns ``(memory_store, (ingest_queue, vault_source))`` on success.
    Returns ``(None, None)`` when:

    - ``config/memory.yaml`` is missing, or
    - sqlite-vec failed to load on the shared DB connection
      (``db.vec_available == False``), or
    - any provider / store / source constructor raises.

    The orchestrator always keeps booting; the `memory_search` tool
    and vault watcher simply don't register.
    """
    memory_yaml = config_dir / "memory.yaml"
    if not memory_yaml.exists():
        return None, None
    if not getattr(db, "vec_available", False):
        logger.warning(
            "memory_store_unavailable",
            reason="sqlite_vec_not_loaded",
        )
        return None, None
    try:
        from donna.config import load_memory_config
        from donna.memory.chunking import MarkdownHeadingChunker
        from donna.memory.embeddings import build_embedding_provider
        from donna.memory.queue import MemoryIngestQueue
        from donna.memory.sources_vault import VaultSource
        from donna.memory.store import MemoryStore

        cfg = load_memory_config(config_dir)
        provider = build_embedding_provider(
            cfg.embedding,
            invocation_logger=invocation_logger,
            user_id=user_id,
        )
        chunker = MarkdownHeadingChunker(
            max_tokens=cfg.embedding.max_tokens,
            overlap_tokens=cfg.embedding.chunk_overlap,
        )
        store = MemoryStore(db.connection, provider, chunker, cfg.retrieval)
        queue = MemoryIngestQueue(store)
        source: Any | None = None
        if cfg.sources.vault.enabled and vault_client is not None:
            source = VaultSource(
                client=vault_client,
                store=store,
                queue=queue,
                cfg=cfg.sources.vault,
                vault_cfg=cfg.vault,
                user_id=user_id,
            )
        return store, (queue, source)
    except Exception as exc:
        logger.warning("memory_store_unavailable", reason=str(exc))
        return None, None


def _build_episodic_sources(
    config_dir: Path,
    memory_store: Any | None,
    db: Any,
    user_id: str,
) -> dict[str, Any]:
    """Construct slice-14 episodic sources and wire observers.

    Returns a dict ``{"chat", "task", "correction"}`` keyed on source
    name (values are the source instances, or absent when disabled).
    Non-fatal: a failure on any one source logs + skips, the others
    still wire.
    """
    built: dict[str, Any] = {}
    if memory_store is None:
        return built
    memory_yaml = config_dir / "memory.yaml"
    if not memory_yaml.exists():
        return built
    try:
        from donna.config import load_memory_config

        cfg = load_memory_config(config_dir)
    except Exception as exc:
        logger.warning("episodic_sources_unavailable", reason=str(exc))
        return built

    # chat + task run through the Database constructor observer
    # (Option A). correction uses the module-level registry (Option B).
    if cfg.sources.chat.enabled:
        try:
            from donna.memory.sources_chat import ChatSource

            built["chat"] = ChatSource(
                store=memory_store,
                cfg=cfg.sources.chat,
                user_id_default=user_id,
            )
        except Exception as exc:
            logger.warning(
                "episodic_source_unavailable",
                source="chat",
                reason=str(exc),
            )
    if cfg.sources.task.enabled:
        try:
            from donna.memory.sources_task import TaskSource

            built["task"] = TaskSource(store=memory_store, cfg=cfg.sources.task)
        except Exception as exc:
            logger.warning(
                "episodic_source_unavailable",
                source="task",
                reason=str(exc),
            )
    if cfg.sources.correction.enabled:
        try:
            from donna.memory.observers import register_observer
            from donna.memory.sources_correction import CorrectionSource

            corr = CorrectionSource(store=memory_store, cfg=cfg.sources.correction)
            register_observer("correction", corr.observe)
            built["correction"] = corr
        except Exception as exc:
            logger.warning(
                "episodic_source_unavailable",
                source="correction",
                reason=str(exc),
            )

    if built.get("chat") is not None or built.get("task") is not None:
        combined = _CombinedDbObserver(
            chat=built.get("chat"), task=built.get("task"),
        )
        setter = getattr(db, "set_memory_observer", None)
        if callable(setter):
            setter(combined)
    return built


class _CombinedDbObserver:
    """Dispatches DB-side events to the right source.

    The DB accepts a single observer handle (see
    :meth:`donna.tasks.database.Database.set_memory_observer`). This
    wrapper forwards ``observe_message`` to :class:`ChatSource` and
    ``observe_task`` to :class:`TaskSource` when each is present.
    """

    def __init__(self, *, chat: Any | None, task: Any | None) -> None:
        self._chat = chat
        self._task = task

    async def observe_message(self, event: dict[str, Any]) -> None:
        if self._chat is not None:
            await self._chat.observe_message(event)

    async def observe_session_closed(self, event: dict[str, Any]) -> None:
        if self._chat is not None:
            await self._chat.observe_session_closed(event)

    async def observe_task(self, event: dict[str, Any]) -> None:
        if self._task is not None:
            await self._task.observe_task(event)


def _start_memory_tasks(
    ctx: Any, handles: tuple[Any, Any | None] | None,
) -> None:
    """Spawn the ingest worker + vault watcher + backfill as bg tasks.

    Appends to ``ctx.tasks`` so the orchestrator's main ``asyncio.wait``
    supervises them alongside every other long-running subsystem.
    """
    if handles is None:
        return
    queue, source = handles
    ctx.tasks.append(asyncio.create_task(queue.run_forever()))
    if source is not None:
        ctx.tasks.append(asyncio.create_task(source.watch()))
        # Backfill is one-shot — run it in the background rather than
        # adding to ctx.tasks, where FIRST_COMPLETED would shut down
        # the orchestrator as soon as it finishes.
        backfill = asyncio.create_task(  # noqa: RUF006, F841
            source.backfill(ctx.user_id),
        )


async def _try_build_vault_writer(
    config_dir: Path, vault_client: Any | None
) -> Any | None:
    """Construct a :class:`VaultWriter`, then ``ensure_ready()``.

    Non-fatal: returns None when the client is absent, memory config
    cannot be loaded, or ``ensure_ready`` raises (e.g. vault root on a
    read-only mount).
    """
    if vault_client is None:
        return None
    memory_yaml = config_dir / "memory.yaml"
    if not memory_yaml.exists():
        return None
    try:
        from donna.config import load_memory_config
        from donna.integrations.git_repo import GitRepo
        from donna.integrations.vault import VaultWriter

        memory_cfg = load_memory_config(config_dir)
        git = GitRepo(
            root=Path(memory_cfg.vault.root),
            author_name=memory_cfg.vault.git_author_name,
            author_email=memory_cfg.vault.git_author_email,
        )
        writer = VaultWriter(config=memory_cfg, git=git, client=vault_client)
        await writer.ensure_ready()
        return writer
    except Exception as exc:
        logger.warning("vault_writer_unavailable", reason=str(exc))
        return None


def _try_build_template_renderer(project_root: Path) -> Any | None:
    """Slice 15: build the file-based vault :class:`VaultTemplateRenderer`.

    Non-fatal: returns ``None`` when ``prompts/vault/`` is missing or the
    constructor raises. The meeting-note skill is short-circuited when
    this returns ``None``.
    """
    templates_dir = project_root / "prompts" / "vault"
    if not templates_dir.is_dir():
        logger.info(
            "template_renderer_unavailable",
            reason="prompts_vault_missing",
            path=str(templates_dir),
        )
        return None
    try:
        from donna.memory.templates import VaultTemplateRenderer

        return VaultTemplateRenderer(templates_dir=templates_dir)
    except Exception as exc:
        logger.warning("template_renderer_unavailable", reason=str(exc))
        return None


def _try_build_meeting_note_skill(
    config_dir: Path,
    *,
    renderer: Any | None,
    memory_store: Any | None,
    vault_client: Any | None,
    vault_writer: Any | None,
    router: ModelRouter,
    invocation_logger: InvocationLogger,
    user_id: str,
) -> tuple[Any | None, Any | None]:
    """Slice 15: build the :class:`MeetingNoteSkill` + its config.

    Returns ``(skill, config)`` or ``(None, None)`` when any prerequisite
    is absent or the skill block is disabled. Non-fatal; the poller is
    simply not started when this returns ``None``.
    """
    if (
        renderer is None
        or memory_store is None
        or vault_client is None
        or vault_writer is None
    ):
        return None, None
    memory_yaml = config_dir / "memory.yaml"
    if not memory_yaml.exists():
        return None, None
    try:
        from donna.capabilities.meeting_note_skill import MeetingNoteSkill
        from donna.config import load_memory_config
        from donna.memory.writer import MemoryInformedWriter

        cfg = load_memory_config(config_dir)
        skill_cfg = cfg.skills.meeting_note
        if not skill_cfg.enabled:
            logger.info("meeting_note_skill_disabled_by_config")
            return None, None
        writer = MemoryInformedWriter(
            renderer=renderer,
            vault_client=vault_client,
            vault_writer=vault_writer,
            router=router,
            logger=invocation_logger,
            safety_allowlist=cfg.safety.path_allowlist,
        )
        skill = MeetingNoteSkill(
            writer=writer,
            memory_store=memory_store,
            vault_client=vault_client,
            config=skill_cfg,
            user_id=user_id,
        )
        return skill, skill_cfg
    except Exception as exc:
        logger.warning("meeting_note_skill_unavailable", reason=str(exc))
        return None, None


def _start_meeting_end_poller(
    ctx: Any, *, skill: Any | None, config: Any | None
) -> None:
    """Spawn the meeting-end poller as a supervised bg task.

    No-op when ``skill`` or ``config`` is ``None``. Appends the task to
    ``ctx.tasks`` so the orchestrator's ``asyncio.wait`` supervises it.
    """
    if skill is None or config is None:
        return
    try:
        from donna.capabilities.meeting_end_poller import MeetingEndPoller

        poller = MeetingEndPoller(
            connection=ctx.db.connection,
            skill=skill,
            config=config,
            user_id=ctx.user_id,
        )
        ctx.tasks.append(asyncio.create_task(poller.run_forever()))
    except Exception as exc:
        logger.warning("meeting_end_poller_unavailable", reason=str(exc))


# ---------------------------------------------------------------------------
# Slice 16 — cadence-driven template-write skills.
# ---------------------------------------------------------------------------


def _try_build_memory_informed_writer(
    config_dir: Path,
    *,
    renderer: Any | None,
    vault_client: Any | None,
    vault_writer: Any | None,
    router: ModelRouter,
    invocation_logger: InvocationLogger,
) -> Any | None:
    """Build a single shared :class:`MemoryInformedWriter` used by every
    slice-16 skill. Returns ``None`` when any prerequisite is missing."""
    if renderer is None or vault_client is None or vault_writer is None:
        return None
    memory_yaml = config_dir / "memory.yaml"
    if not memory_yaml.exists():
        return None
    try:
        from donna.config import load_memory_config
        from donna.memory.writer import MemoryInformedWriter

        cfg = load_memory_config(config_dir)
        return MemoryInformedWriter(
            renderer=renderer,
            vault_client=vault_client,
            vault_writer=vault_writer,
            router=router,
            logger=invocation_logger,
            safety_allowlist=cfg.safety.path_allowlist,
        )
    except Exception as exc:
        logger.warning("memory_informed_writer_unavailable", reason=str(exc))
        return None


def _try_build_daily_reflection_skill(
    config_dir: Path,
    *,
    writer: Any | None,
    memory_store: Any | None,
    db_connection: Any | None,
    user_id: str,
) -> tuple[Any | None, Any | None]:
    if writer is None or memory_store is None or db_connection is None:
        return None, None
    try:
        from donna.capabilities.daily_reflection_skill import (
            DailyReflectionSkill,
        )
        from donna.config import load_memory_config

        cfg = load_memory_config(config_dir)
        skill_cfg = cfg.skills.daily_reflection
        if not skill_cfg.enabled:
            logger.info("daily_reflection_skill_disabled_by_config")
            return None, None
        return (
            DailyReflectionSkill(
                writer=writer,
                memory_store=memory_store,
                connection=db_connection,
                config=skill_cfg,
                user_id=user_id,
            ),
            skill_cfg,
        )
    except Exception as exc:
        logger.warning("daily_reflection_skill_unavailable", reason=str(exc))
        return None, None


def _try_build_commitment_log_skill(
    config_dir: Path,
    *,
    writer: Any | None,
    memory_store: Any | None,
    db_connection: Any | None,
    user_id: str,
) -> tuple[Any | None, Any | None]:
    if writer is None or memory_store is None or db_connection is None:
        return None, None
    try:
        from donna.capabilities.commitment_log_skill import CommitmentLogSkill
        from donna.config import load_memory_config

        cfg = load_memory_config(config_dir)
        skill_cfg = cfg.skills.commitment_log
        if not skill_cfg.enabled:
            logger.info("commitment_log_skill_disabled_by_config")
            return None, None
        return (
            CommitmentLogSkill(
                writer=writer,
                memory_store=memory_store,
                connection=db_connection,
                config=skill_cfg,
                user_id=user_id,
            ),
            skill_cfg,
        )
    except Exception as exc:
        logger.warning("commitment_log_skill_unavailable", reason=str(exc))
        return None, None


def _try_build_weekly_review_skill(
    config_dir: Path,
    *,
    writer: Any | None,
    memory_store: Any | None,
    vault_client: Any | None,
    db_connection: Any | None,
    user_id: str,
) -> tuple[Any | None, Any | None]:
    if (
        writer is None
        or memory_store is None
        or vault_client is None
        or db_connection is None
    ):
        return None, None
    try:
        from donna.capabilities.weekly_review_skill import WeeklyReviewSkill
        from donna.config import load_memory_config

        cfg = load_memory_config(config_dir)
        skill_cfg = cfg.skills.weekly_review
        if not skill_cfg.enabled:
            logger.info("weekly_review_skill_disabled_by_config")
            return None, None
        return (
            WeeklyReviewSkill(
                writer=writer,
                memory_store=memory_store,
                vault_client=vault_client,
                connection=db_connection,
                config=skill_cfg,
                user_id=user_id,
            ),
            skill_cfg,
        )
    except Exception as exc:
        logger.warning("weekly_review_skill_unavailable", reason=str(exc))
        return None, None


def _try_build_person_profile_skill(
    config_dir: Path,
    *,
    writer: Any | None,
    memory_store: Any | None,
    vault_client: Any | None,
    db_connection: Any | None,
    user_id: str,
) -> tuple[Any | None, Any | None]:
    if (
        writer is None
        or memory_store is None
        or vault_client is None
        or db_connection is None
    ):
        return None, None
    try:
        from donna.capabilities.person_mention_counter import (
            PersonMentionCounter,
        )
        from donna.capabilities.person_profile_skill import PersonProfileSkill
        from donna.config import load_memory_config

        cfg = load_memory_config(config_dir)
        skill_cfg = cfg.skills.person_profile
        if not skill_cfg.enabled:
            logger.info("person_profile_skill_disabled_by_config")
            return None, None
        return (
            PersonProfileSkill(
                writer=writer,
                memory_store=memory_store,
                vault_client=vault_client,
                mention_counter=PersonMentionCounter(db_connection),
                config=skill_cfg,
                user_id=user_id,
            ),
            skill_cfg,
        )
    except Exception as exc:
        logger.warning("person_profile_skill_unavailable", reason=str(exc))
        return None, None


def _build_notification_tasks(
    ctx: StartupContext,
    *,
    calendar_client: Any | None = None,
    gmail_client: Any | None = None,
    scheduler: Any | None = None,
) -> Any | None:
    """Construct a NotificationTasks bundle for run_server().

    Returns None when the notification service is unavailable (no Discord bot).
    Components that can't be constructed are set to None — run_server() checks
    each before starting its background loop.
    """
    if ctx.notification_service is None:
        logger.info("notification_tasks_skipped_no_notification_service")
        return None

    from donna.server import NotificationTasks

    # --- Morning Digest ---
    morning_digest = None
    try:
        from donna.config import load_calendar_config, load_email_config
        from donna.notifications.digest import MorningDigest

        cal_cfg = load_calendar_config(ctx.config_dir)
        personal = cal_cfg.calendars.get("personal")
        calendar_id = personal.calendar_id if personal else "primary"

        user_email = ""
        try:
            email_cfg = load_email_config(ctx.config_dir)
            user_email = getattr(email_cfg, "user_email", "")
        except Exception:
            pass

        morning_digest = MorningDigest(
            db=ctx.db,
            service=ctx.notification_service,
            router=ctx.router,
            calendar_client=calendar_client,
            calendar_id=calendar_id,
            user_id=ctx.user_id,
            project_root=ctx.project_root,
            gmail=gmail_client,
            user_email=user_email,
            tool_request_repo=ctx.tool_request_repository,
            tz=ctx.tz,
        )
        logger.info("morning_digest_constructed")
    except Exception as exc:
        logger.warning("morning_digest_unavailable", reason=str(exc))

    # --- Reminder Scheduler ---
    reminder_scheduler = None
    try:
        from donna.notifications.reminders import ReminderScheduler

        reminder_scheduler = ReminderScheduler(
            db=ctx.db,
            service=ctx.notification_service,
            user_id=ctx.user_id,
            router=ctx.router,
            tz=ctx.tz,
        )
        logger.info("reminder_scheduler_constructed")
    except Exception as exc:
        logger.warning("reminder_scheduler_unavailable", reason=str(exc))

    # --- Reply Handler (used by OverdueDetector) ---
    reply_handler = None
    try:
        from donna.config import load_reply_actions_config, load_reply_intents_config
        from donna.replies.handler import ReplyHandler

        intents_cfg = load_reply_intents_config(ctx.config_dir)
        actions_cfg = load_reply_actions_config(ctx.config_dir)
        reply_context: dict[str, Any] = {
            "scheduler": scheduler,
            "calendar_client": calendar_client,
            "calendar_id": None,
            "user_id": ctx.user_id,
        }
        try:
            from donna.config import load_calendar_config as _load_cal_rh

            _rh_cal_cfg = _load_cal_rh(ctx.config_dir)
            _rh_personal = _rh_cal_cfg.calendars.get("personal")
            reply_context["calendar_id"] = (
                _rh_personal.calendar_id if _rh_personal else "primary"
            )
        except Exception:
            pass

        reply_handler = ReplyHandler(
            conn=ctx.db.connection,
            intents_config=intents_cfg,
            actions_config=actions_cfg,
            router=ctx.router,
            db=ctx.db,
            context=reply_context,
        )
        logger.info("reply_handler_constructed")
    except Exception as exc:
        logger.warning("reply_handler_unavailable", reason=str(exc))

    # --- Overdue Detector ---
    overdue_detector = None
    if ctx.bot is not None and scheduler is not None:
        try:
            from donna.config import load_calendar_config
            from donna.notifications.overdue import OverdueDetector

            cal_cfg = load_calendar_config(ctx.config_dir)
            personal = cal_cfg.calendars.get("personal")
            calendar_id = personal.calendar_id if personal else "primary"
            overdue_tz = zoneinfo.ZoneInfo(cal_cfg.timezone)

            overdue_detector = OverdueDetector(
                db=ctx.db,
                service=ctx.notification_service,
                bot=ctx.bot,
                scheduler=scheduler,
                calendar_id=calendar_id,
                user_id=ctx.user_id,
                router=ctx.router,
                reply_handler=reply_handler,
                calendar_client=calendar_client,
                tz=overdue_tz,
            )
            logger.info("overdue_detector_constructed")
        except Exception as exc:
            logger.warning("overdue_detector_unavailable", reason=str(exc))

    # --- Weekly Planner ---
    weekly_planner = None
    if scheduler is not None:
        try:
            from donna.config import load_calendar_config
            from donna.scheduling.priority_engine import PriorityEngine
            from donna.scheduling.priority_recalculator import PriorityRecalculator
            from donna.scheduling.weekly_planner import WeeklyPlanner

            cal_cfg = load_calendar_config(ctx.config_dir)
            personal = cal_cfg.calendars.get("personal")
            calendar_id = personal.calendar_id if personal else "primary"

            priority_engine = PriorityEngine(cal_cfg.priority)
            recalculator = PriorityRecalculator(
                db=ctx.db,
                engine=priority_engine,
                service=ctx.notification_service,
                user_id=ctx.user_id,
            )

            if calendar_client is None:
                raise RuntimeError("calendar_client required for WeeklyPlanner")
            weekly_planner = WeeklyPlanner(
                db=ctx.db,
                scheduler=scheduler,
                recalculator=recalculator,
                service=ctx.notification_service,
                calendar_client=calendar_client,
                calendar_id=calendar_id,
                user_id=ctx.user_id,
            )
            logger.info("weekly_planner_constructed")
        except Exception as exc:
            logger.warning("weekly_planner_unavailable", reason=str(exc))

    # --- End-of-Day Digest ---
    eod_digest = None
    try:
        from donna.config import load_email_config as _load_eod_email
        from donna.notifications.eod_digest import EodDigest

        eod_email_cfg = _load_eod_email(ctx.config_dir)
        eod_digest = EodDigest(
            db=ctx.db,
            service=ctx.notification_service,
            gmail=gmail_client,
            user_id=ctx.user_id,
            user_email=getattr(eod_email_cfg, "user_email", ""),
            email_config=eod_email_cfg,
            tz=ctx.tz,
        )
        logger.info("eod_digest_constructed")
    except Exception as exc:
        logger.warning("eod_digest_unavailable", reason=str(exc))

    # --- Weekly Digest ---
    weekly_digest = None
    try:
        from donna.notifications.weekly_digest import WeeklyDigest

        weekly_digest = WeeklyDigest(
            db=ctx.db,
            service=ctx.notification_service,
            router=ctx.router,
            user_id=ctx.user_id,
        )
        logger.info("weekly_digest_constructed")
    except Exception as exc:
        logger.warning("weekly_digest_unavailable", reason=str(exc))

    if morning_digest is None or reminder_scheduler is None or overdue_detector is None:
        logger.warning(
            "notification_tasks_partial",
            digest=morning_digest is not None,
            reminders=reminder_scheduler is not None,
            overdue=overdue_detector is not None,
            weekly_planner_discarded=weekly_planner is not None,
        )
        return None

    return NotificationTasks(
        reminder_scheduler=reminder_scheduler,
        overdue_detector=overdue_detector,
        morning_digest=morning_digest,
        weekly_planner=weekly_planner,
        eod_digest=eod_digest,
        weekly_digest=weekly_digest,
    )


def _start_daily_reflection_cron(
    ctx: Any, *, skill: Any | None, config: Any | None
) -> None:
    if skill is None or config is None:
        return
    try:
        from datetime import date as _date

        from donna.skills.crons.scheduler import AsyncCronScheduler

        async def _fire() -> None:
            await skill.run_for_day(_date.today())

        scheduler = AsyncCronScheduler(
            hour_utc=config.hour_utc,
            minute_utc=config.minute_utc,
            task=_fire,
            tz=ctx.tz,
        )
        ctx.tasks.append(asyncio.create_task(scheduler.run_forever()))
    except Exception as exc:
        logger.warning("daily_reflection_cron_unavailable", reason=str(exc))


def _start_commitment_log_cron(
    ctx: Any, *, skill: Any | None, config: Any | None
) -> None:
    if skill is None or config is None:
        return
    try:
        from datetime import date as _date

        from donna.skills.crons.scheduler import AsyncCronScheduler

        async def _fire() -> None:
            await skill.run_for_day(_date.today())

        scheduler = AsyncCronScheduler(
            hour_utc=config.hour_utc,
            minute_utc=config.minute_utc,
            task=_fire,
            tz=ctx.tz,
        )
        ctx.tasks.append(asyncio.create_task(scheduler.run_forever()))
    except Exception as exc:
        logger.warning("commitment_log_cron_unavailable", reason=str(exc))


def _start_weekly_review_cron(
    ctx: Any, *, skill: Any | None, config: Any | None
) -> None:
    if skill is None or config is None:
        return
    try:
        from datetime import date as _date

        from donna.skills.crons.scheduler import AsyncCronScheduler

        async def _fire() -> None:
            await skill.run_for_week(_date.today())

        scheduler = AsyncCronScheduler(
            hour_utc=config.hour_utc,
            minute_utc=config.minute_utc,
            day_of_week=config.day_of_week,
            task=_fire,
            tz=ctx.tz,
        )
        ctx.tasks.append(asyncio.create_task(scheduler.run_forever()))
    except Exception as exc:
        logger.warning("weekly_review_cron_unavailable", reason=str(exc))


def _start_person_profile_cron(
    ctx: Any, *, skill: Any | None, config: Any | None
) -> None:
    if skill is None or config is None:
        return
    try:
        from donna.skills.crons.scheduler import AsyncCronScheduler

        async def _fire() -> None:
            names = await skill.list_names_to_refresh()
            for name, reason in names:
                try:
                    await skill.run_for_person(name, reason)
                except Exception as exc:
                    logger.warning(
                        "person_profile_run_failed",
                        name=name,
                        reason=reason,
                        error=str(exc),
                    )

        scheduler = AsyncCronScheduler(
            hour_utc=config.hour_utc,
            minute_utc=config.minute_utc,
            day_of_week=config.day_of_week,
            task=_fire,
            tz=ctx.tz,
        )
        ctx.tasks.append(asyncio.create_task(scheduler.run_forever()))
    except Exception as exc:
        logger.warning("person_profile_cron_unavailable", reason=str(exc))


async def _try_build_calendar_client(config_dir: Path) -> Any | None:
    """Attempt to construct and authenticate a GoogleCalendarClient.

    Non-fatal: returns None if config, credentials, or auth fail.
    """
    calendar_yaml = config_dir / "calendar.yaml"
    if not calendar_yaml.exists():
        return None
    try:
        from donna.config import load_calendar_config
        from donna.integrations.calendar import GoogleCalendarClient

        cal_cfg = load_calendar_config(config_dir)
        token_path = Path(cal_cfg.credentials.token_path)
        secrets_path = Path(cal_cfg.credentials.client_secrets_path)
        if not token_path.exists() or not secrets_path.exists():
            logger.warning(
                "calendar_client_unavailable",
                reason="credential_file_missing",
                token_exists=token_path.exists(),
                secrets_exists=secrets_path.exists(),
            )
            return None
        client = GoogleCalendarClient(config=cal_cfg)
        await client.authenticate()
        logger.info("calendar_client_authenticated")
        return client
    except Exception as exc:
        logger.warning("calendar_client_unavailable", reason=str(exc))
        return None


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class StartupContext:
    """Shared resources needed by every wire_* helper.

    Built once in `build_startup_context`, then passed to each wire_*
    helper. Mutable `tasks` list is appended to by helpers that spawn
    long-running asyncio tasks; `_run_orchestrator` awaits the final
    list.
    """

    args: argparse.Namespace
    config_dir: Path
    project_root: Path
    log: Any
    # Config
    models_config: ModelsConfig
    task_types_config: TaskTypesConfig
    skill_config: SkillSystemConfig
    # Live handles
    db: Database
    state_machine: StateMachine
    router: ModelRouter
    invocation_logger: InvocationLogger
    input_parser: InputParser
    # Runtime knobs
    port: int
    user_id: str
    # Discord env inputs (pre-parsed for wire_discord)
    discord_token: str | None
    tasks_channel_id_str: str | None
    debug_channel_id_str: str | None
    agents_channel_id_str: str | None
    guild_id_str: str | None
    # Bot + NotificationService are constructed here so skill/automation
    # wiring (which runs before bot.start()) can see a live notifier.
    bot: Any | None
    notification_service: NotificationService | None
    # Slice 17/18: over-budget escalation infrastructure. Optional so tests
    # that hand-construct StartupContext don't have to know about them.
    manual_escalation_config: ManualEscalationConfig | None = None
    escalation_repository: EscalationRepository | None = None
    dashboard_setting_resolver: DashboardSettingResolver | None = None
    budget_extension_repo: BudgetExtensionRepository | None = None
    escalation_gate: EscalationGate | None = None
    escalation_delivery_loop: EscalationDeliveryLoop | None = None
    # Slice 21: claude_code mode infrastructure. None when the host
    # repo mount or spec builder couldn't be resolved at boot —
    # claude_code button is not offered in that case.
    claude_code_poller: ClaudeCodePoller | None = None
    owner_discord_id: int | None = None
    # Slice 22 — tool gap surfacing. None when no manual_escalation_config
    # was loaded (boot fail-soft).
    tool_request_repository: Any | None = None
    tool_gap_surfacer: Any | None = None
    # Slice 24 — hourly nag for ``requires_rebuild=True`` tools that
    # haven't been redeployed (spec §10.5 row 1). None when no
    # tool_gap_surfacer was wired.
    requires_rebuild_nagger: Any | None = None
    # TaskEventBus — wired into Database so task mutations emit events that
    # AutoScheduler and other subscribers can react to.
    event_bus: Any | None = None
    # Timezone for all cron/prompt scheduling — loaded from calendar.yaml.
    tz: zoneinfo.ZoneInfo | None = None
    # Calendar client for event lifecycle (create/update/delete).
    # None when credentials are missing or authentication fails.
    calendar_client: Any | None = None
    calendar_id: str = "primary"
    # Shared asyncio task list — every helper that spawns a background
    # loop appends to this. `_run_orchestrator` awaits it.
    tasks: list[asyncio.Task[Any]] = field(default_factory=list)


@dataclass
class SkillSystemHandle:
    """Return value of `wire_skill_system`.

    `bundle` is None when `skill_config.enabled` is false; downstream
    helpers (automation + discord) still wire correctly in that case.
    `subsystem_router` + `budget_guard` are always present because the
    automation subsystem needs them even without the skill system.

    ``subsystem_router`` is a ModelRouter instance shared by BOTH the
    skill system and the automation subsystem (F-W3-J). It is distinct
    from ``ctx.model_router`` / ``ctx.router`` which handles the primary
    orchestrator request path; the subsystem router exists so the skill
    + automation pipelines can be swapped, rate-limited, or budget-
    capped independently of the interactive path.
    """

    subsystem_router: ModelRouter
    budget_guard: BudgetGuard | None
    cost_tracker: CostTracker | None
    bundle: SkillSystemBundle | None
    notifier: Callable[[str], Any]


@dataclass
class AutomationHandle:
    """Return value of `wire_automation_subsystem`."""

    repository: AutomationRepository | None
    dispatcher: AutomationDispatcher | None
    scheduler: AutomationScheduler | None


@dataclass
class DiscordHandle:
    """Return value of `wire_discord`.

    `bot` is the DonnaBot instance (or None if the token/channel env
    vars aren't present). Callers that need the notification service
    should reach through ``StartupContext.notification_service``; this
    handle used to duplicate that field but no consumer read it off the
    handle (F-W3-I).
    """

    bot: Any | None
    intent_dispatcher: Any | None = None  # Wave 3 Task 8 will wire this.


# ---------------------------------------------------------------------------
# build_startup_context
# ---------------------------------------------------------------------------


async def build_startup_context(args: argparse.Namespace) -> StartupContext:
    """Open the DB, load config, construct router/input_parser/bot/notifier.

    Extracted from the top of the pre-refactor `_run_orchestrator`. The
    Discord bot + NotificationService are constructed here (but not
    started) so that skill-system + automation wiring — which run before
    `bot.start()` — can receive a live NotificationService.
    """
    log = structlog.get_logger()
    log.info(
        "donna_starting", config_dir=args.config_dir, log_level=args.log_level,
    )

    config_dir = Path(args.config_dir)
    _source_root = Path(__file__).resolve().parents[2]
    if (_source_root / "prompts").is_dir():
        project_root = _source_root
    else:
        project_root = Path(os.environ.get("DONNA_PROJECT_ROOT", "/app"))

    # Load configuration
    models_config = load_models_config(config_dir)
    task_types_config = load_task_types_config(config_dir)
    state_machine_config = load_state_machine_config(config_dir)
    skill_config = load_skill_system_config(config_dir)
    manual_escalation_config = load_manual_escalation_config(config_dir)

    # Load timezone from calendar.yaml for all cron/prompt scheduling.
    user_tz: zoneinfo.ZoneInfo | None = None
    try:
        from donna.config import load_calendar_config as _load_cal
        _cal = _load_cal(config_dir)
        user_tz = zoneinfo.ZoneInfo(_cal.timezone)
    except Exception:
        log.warning("calendar_tz_load_failed_defaulting_utc")

    # Slice 23 — fail boot if any task type declares
    # ``manual_escalation.mode='claude_code'`` without a target_paths or
    # reference_module (spec §10.7 row 3). Catches drift from config
    # edits that would otherwise produce un-actionable specs.
    from donna.config import validate_manual_escalation_config

    validate_manual_escalation_config(task_types=task_types_config)

    # Initialise state machine and database
    state_machine = StateMachine(state_machine_config)
    db_path = os.environ.get("DONNA_DB_PATH", "donna_tasks.db")

    # Wire Supabase write-through sync when credentials are available.
    supabase_sync = None
    if os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
        try:
            from donna.integrations.supabase_sync import SupabaseSync

            supabase_sync = SupabaseSync()
            log.info("supabase_sync_constructed", configured=supabase_sync.configured)
        except Exception:
            log.warning("supabase_sync_unavailable", exc_info=True)

    db = Database(db_path, state_machine, supabase_sync=supabase_sync)
    await db.connect()
    await db.run_migrations()

    # Wire the TaskEventBus so task mutations emit events that
    # AutoScheduler and other subscribers can react to.
    from donna.tasks.events import TaskEventBus

    event_bus = TaskEventBus()
    db.set_event_bus(event_bus)

    from donna.preferences.correction_subscriber import CorrectionSubscriber

    correction_subscriber = CorrectionSubscriber(db)
    event_bus.subscribe("task_updated", correction_subscriber.on_task_updated)

    # Initialise model layer and input parser
    invocation_logger = InvocationLogger(db.connection)
    router = ModelRouter(
        models_config, task_types_config, project_root,
        invocation_logger=invocation_logger,
    )
    input_parser = InputParser(router, invocation_logger, project_root, tz=user_tz)

    # Slice 17/18 — over-budget escalation infrastructure. Built before
    # the bot so the gate's delivery callback can capture a bot reference
    # once that's constructed below; the gate itself is wired only when
    # the bot is available.
    escalation_repository = EscalationRepository(db.connection)
    dashboard_setting_resolver = DashboardSettingResolver(escalation_repository)
    budget_extension_repo = BudgetExtensionRepository(db.connection)

    # Slice 22 — tool gap surfacing. Repository is always wired (table
    # is non-optional). Surfacer is built without a ping_poster here;
    # the bot-aware poster is bolted on later once the bot exists
    # (see _wire_tool_gap_ping_poster below).
    from donna.cost.tool_gap_surfacer import ToolGapSurfacer
    from donna.cost.tool_request_repository import ToolRequestRepository

    tool_request_repository = ToolRequestRepository(db.connection)
    tool_gap_surfacer = ToolGapSurfacer(
        repository=tool_request_repository,
        conn=db.connection,
        ping_poster=None,
    )

    # Crash-recovery scan (§10.6 row 4): void extensions granted before a
    # previous crash that never ran their associated API call.
    await _run_crash_recovery(
        extension_repo=budget_extension_repo,
        escalation_repo=escalation_repository,
        conn=db.connection,
        user_id=os.environ.get("DONNA_USER_ID", "nick"),
        log=log,
    )

    # OWNER_DISCORD_ID — parsed up front. The env-var requirement is
    # enforced below only once a Discord bot is actually being wired
    # (with no bot, manual escalation has no surface to land on, so
    # missing the env var is benign).
    owner_discord_id_str = os.environ.get("DONNA_OWNER_DISCORD_ID")
    owner_discord_id: int | None
    if owner_discord_id_str is not None and owner_discord_id_str.strip():
        owner_discord_id = int(owner_discord_id_str.strip())
    else:
        owner_discord_id = None

    port: int = args.port or int(os.environ.get("DONNA_PORT", "8100"))

    # Pre-parse Discord env so wire_discord can consume them; we need
    # them here too because bot + NotificationService are constructed
    # up-front so skill/automation wiring can see the live notifier.
    discord_token = os.environ.get("DISCORD_BOT_TOKEN")
    tasks_channel_id_str = os.environ.get("DISCORD_TASKS_CHANNEL_ID")
    debug_channel_id_str = os.environ.get("DISCORD_DEBUG_CHANNEL_ID")
    agents_channel_id_str = os.environ.get("DISCORD_AGENTS_CHANNEL_ID")
    guild_id_str = os.environ.get("DISCORD_GUILD_ID")
    user_id = os.environ.get("DONNA_USER_ID", "nick")

    bot: Any | None = None
    notification_service: NotificationService | None = None
    if discord_token and tasks_channel_id_str:
        from donna.integrations.discord_bot import DonnaBot

        digest_channel_id_str = os.environ.get("DISCORD_DIGEST_CHANNEL_ID")
        bot = DonnaBot(
            input_parser=input_parser,
            database=db,
            tasks_channel_id=int(tasks_channel_id_str),
            debug_channel_id=int(debug_channel_id_str) if debug_channel_id_str else None,
            digest_channel_id=int(digest_channel_id_str) if digest_channel_id_str else None,
            agents_channel_id=int(agents_channel_id_str) if agents_channel_id_str else None,
            guild_id=int(guild_id_str) if guild_id_str else None,
            event_bus=event_bus,
        )

        # Wave 1 (F-6 Step 6a): construct NotificationService with the live bot.
        from donna.config import load_calendar_config

        twilio_sms_instance = None
        try:
            from donna.config import load_sms_config
            from donna.integrations.twilio_sms import TwilioSMS

            sms_cfg = load_sms_config(config_dir)
            twilio_sms_instance = TwilioSMS(sms_cfg)
            log.info("twilio_sms_constructed")
        except Exception:
            log.warning("twilio_sms_unavailable")

        try:
            calendar_config = load_calendar_config(config_dir)
            notification_service = NotificationService(
                bot=bot,
                calendar_config=calendar_config,
                user_id=user_id,
                sms=twilio_sms_instance,
                gmail=None,
            )
            log.info("notification_service_wired")
        except Exception:
            log.exception("notification_service_init_failed")

        # Wire TwilioVoice for Tier 4 phone escalation.
        twilio_voice_instance = None
        try:
            from donna.integrations.twilio_voice import TwilioVoice

            if twilio_sms_instance is not None:
                twilio_voice_instance = TwilioVoice(max_per_day=1)
                log.info("twilio_voice_constructed")
        except Exception:
            log.warning("twilio_voice_unavailable", exc_info=True)

        # Wire EscalationManager for tiered notification escalation.
        _escalation_manager = None
        if notification_service is not None and twilio_sms_instance is not None:
            try:
                from donna.notifications.escalation import EscalationManager

                gmail_client = _try_build_gmail_client(config_dir)
                user_email = os.environ.get("DONNA_EMAIL_FROM", "")

                _prefs_path = config_dir / "preferences.yaml"
                _tier4_voice_enabled = True
                if _prefs_path.exists():
                    import yaml as _yaml
                    with open(_prefs_path) as _f:
                        _prefs = _yaml.safe_load(_f) or {}
                    _tier4_voice_enabled = _prefs.get("escalation", {}).get(
                        "tier4_voice_enabled", True
                    )

                _escalation_manager = EscalationManager(
                    db=db,
                    service=notification_service,
                    sms=twilio_sms_instance,
                    sms_config=sms_cfg,
                    user_id=user_id,
                    user_phone=os.environ.get("DONNA_USER_PHONE", ""),
                    gmail=gmail_client,
                    user_email=user_email,
                    voice=twilio_voice_instance,
                    tier4_enabled=_tier4_voice_enabled and twilio_voice_instance is not None,
                )
                log.info(
                    "escalation_manager_wired",
                    tier4_enabled=_tier4_voice_enabled and twilio_voice_instance is not None,
                )
            except Exception:
                log.warning("escalation_manager_unavailable", exc_info=True)

    # Boot-time health diagnostics — runs all checks and sends warnings
    # to the Discord debug channel if available.
    from donna.resilience.health_check import SelfDiagnostic

    async def _debug_notify(message: str) -> None:
        if bot is not None and debug_channel_id_str:
            try:
                channel = bot.get_channel(int(debug_channel_id_str))
                if channel:
                    await channel.send(message[:2000])
            except Exception:
                log.warning("debug_notify_failed", exc_info=True)

    logs_db_path = Path(
        os.environ.get("DONNA_LOGS_DB_PATH", str(Path(db_path).parent / "donna_logs.db"))
    )
    diagnostics = SelfDiagnostic(
        tasks_db_path=Path(db_path),
        logs_db_path=logs_db_path,
        donna_mount=Path(os.environ.get("DONNA_DATA_PATH", "/donna")),
        last_supabase_sync_path=(
            Path(os.environ.get("DONNA_DB_DIR", str(Path(db_path).parent)))
            / ".supabase_last_sync"
        ),
        notify=_debug_notify,
    )
    try:
        boot_warnings = await diagnostics.run()
        if boot_warnings:
            log.warning("boot_diagnostics_issues", count=len(boot_warnings))
    except Exception:
        log.exception("boot_diagnostics_failed")

    # Slice 17 — assemble the gate + delivery loop only when the bot is
    # available. Without a bot the four-button view has nowhere to land;
    # callers can still detect over-budget tasks via BudgetGuard.
    escalation_gate: EscalationGate | None = None
    escalation_delivery_loop: EscalationDeliveryLoop | None = None
    if (
        bot is not None
        and manual_escalation_config.enabled
        and owner_discord_id is None
    ):
        # In production an operator should set DONNA_OWNER_DISCORD_ID so
        # buttons can be resolved. Log loudly and continue with the gate
        # disabled — failing to boot here would cripple every other
        # subsystem for an issue scoped to over-budget escalations only.
        log.warning(
            "escalation_gate_disabled_no_owner",
            reason="DONNA_OWNER_DISCORD_ID is unset; "
            "manual escalation buttons cannot be authorised",
        )
    claude_code_poller: ClaudeCodePoller | None = None
    if bot is not None and owner_discord_id is not None:
        cost_tracker_for_gate = CostTracker(db.connection)
        deliver = _make_escalation_delivery_callback(
            bot=bot,
            owner_discord_id=owner_discord_id,
            gate_holder=lambda: escalation_gate,  # late binding
            prompt_delivery=manual_escalation_config.prompt_delivery,
        )
        # Slice 20 — chat-mode prompt builder. Renders chat_question.md,
        # generates the local-Ollama summary, persists prompt_body /
        # summary / prompt_path on the escalation_request row, and
        # writes the workspace .md the delivery callback attaches.
        from donna.cost.escalation_chat_prompt import ChatPromptBuilder

        chat_prompt_builder = ChatPromptBuilder(
            router=router,
            project_root=project_root,
            config=manual_escalation_config.prompt_delivery,
        )

        # Slice 21 — resolve the host repo + spec builder if claude_code
        # mode is enabled. Fails soft: missing env / missing repo just
        # disables claude_code button rendering (chat / api_extended /
        # pause / cancel still work).
        claude_code_cfg = manual_escalation_config.modes.claude_code
        host_repo: GitRepo | None = None
        spec_builder: ClaudeCodeSpecBuilder | None = None
        if claude_code_cfg.enabled:
            host_repo_path_env = claude_code_cfg.host_repo_path_env
            host_repo_path_str = os.environ.get(host_repo_path_env)
            if host_repo_path_str:
                host_repo_path = Path(host_repo_path_str).expanduser()
                if (host_repo_path / ".git").exists():
                    host_repo = GitRepo(root=host_repo_path)
                    workspace_path = expand_workspace_path(
                        os.environ.get("DONNA_WORKSPACE_PATH", str(project_root))
                    )
                    worktree_root = expand_workspace_path(
                        claude_code_cfg.worktree_root
                    )
                    spec_builder = ClaudeCodeSpecBuilder(
                        prompt_dir=project_root / "prompts" / "escalation",
                        workspace_path=workspace_path,
                        host_repo_path=host_repo_path,
                        worktree_root=worktree_root,
                        dashboard_base_url=f"http://localhost:{port}",
                        iteration_limit=manual_escalation_config.triggers.manual_iteration_limit,
                    )
                    log.info(
                        "claude_code_mode_wired",
                        host_repo_path=str(host_repo_path),
                        worktree_root=str(worktree_root),
                    )
                else:
                    log.warning(
                        "claude_code_mode_disabled_invalid_host_repo",
                        host_repo_path=str(host_repo_path),
                        reason="path is not a git repo",
                    )
            else:
                log.info(
                    "claude_code_mode_disabled_no_host_repo_env",
                    env_var=host_repo_path_env,
                )

        escalation_gate = EscalationGate(
            repository=escalation_repository,
            tracker=cost_tracker_for_gate,
            config=manual_escalation_config,
            daily_pause_threshold_usd=models_config.cost.daily_pause_threshold_usd,
            resolver=dashboard_setting_resolver,
            deliver=deliver,
            extension_repo=budget_extension_repo,
            task_types_config=task_types_config,
            chat_prompt_builder=chat_prompt_builder,
            spec_builder=spec_builder,
            host_repo=host_repo,
        )
        escalation_delivery_loop = EscalationDeliveryLoop(
            db=db,
            repository=escalation_repository,
            timeout_minutes=manual_escalation_config.triggers.escalation_timeout_minutes,
            deliver=deliver,
        )
        # The router is constructed before the bot, so we late-bind the
        # gate here so estimate-bearing calls can reach it.
        router.set_escalation_gate(escalation_gate)
        log.info("escalation_gate_wired")

    ctx_obj = StartupContext(
        args=args,
        config_dir=config_dir,
        project_root=project_root,
        log=log,
        models_config=models_config,
        task_types_config=task_types_config,
        skill_config=skill_config,
        manual_escalation_config=manual_escalation_config,
        db=db,
        state_machine=state_machine,
        router=router,
        invocation_logger=invocation_logger,
        input_parser=input_parser,
        escalation_repository=escalation_repository,
        dashboard_setting_resolver=dashboard_setting_resolver,
        budget_extension_repo=budget_extension_repo,
        escalation_gate=escalation_gate,
        escalation_delivery_loop=escalation_delivery_loop,
        claude_code_poller=claude_code_poller,
        owner_discord_id=owner_discord_id,
        tool_request_repository=tool_request_repository,
        tool_gap_surfacer=tool_gap_surfacer,
        requires_rebuild_nagger=None,  # late-bound below once the bot is up
        event_bus=event_bus,
        port=port,
        user_id=user_id,
        discord_token=discord_token,
        tasks_channel_id_str=tasks_channel_id_str,
        debug_channel_id_str=debug_channel_id_str,
        agents_channel_id_str=agents_channel_id_str,
        guild_id_str=guild_id_str,
        bot=bot,
        notification_service=notification_service,
        tz=user_tz,
    )
    if escalation_delivery_loop is not None:
        ctx_obj.tasks.append(
            asyncio.create_task(
                escalation_delivery_loop.run(),
                name="escalation_delivery_loop",
            )
        )

    # Slice 20 — chat-mode submission ingestion. Always start when
    # manual escalation is enabled in YAML, regardless of whether a
    # Discord bot is wired: the dashboard submit endpoint is the
    # canonical surface and works even without Discord.
    if manual_escalation_config.enabled:
        from donna.skills.chat_escalation_ingestion_poller import (
            ChatEscalationIngestionPoller,
        )

        chat_ingestion_poller = ChatEscalationIngestionPoller(db=db)
        ctx_obj.tasks.append(
            asyncio.create_task(
                chat_ingestion_poller.run(),
                name="chat_escalation_ingestion_poller",
            )
        )
        log.info("chat_escalation_ingestion_poller_started")

    # Supabase keep-alive — periodic HEAD ping to prevent idle disconnect.
    if supabase_sync is not None and supabase_sync.configured:
        ctx_obj.tasks.append(
            asyncio.create_task(
                supabase_sync.keep_alive(),
                name="supabase_keepalive",
            )
        )
        log.info("supabase_keepalive_started")

    # Email parser poller — monitors a Gmail alias for forwarded task emails.
    email_monitor_alias = os.environ.get("DONNA_EMAIL_MONITOR_ALIAS", "").strip()
    if email_monitor_alias:
        gmail_for_poller = _try_build_gmail_client(config_dir)
        if gmail_for_poller is not None:
            from donna.integrations.email_parser import poll_and_create_tasks

            async def _email_poll_loop() -> None:
                while True:
                    try:
                        created = await poll_and_create_tasks(
                            gmail=gmail_for_poller,
                            input_parser=input_parser,
                            db=db,
                            user_id=user_id,
                            monitor_alias=email_monitor_alias,
                        )
                        if created > 0:
                            log.info("email_poll_created_tasks", count=created)
                    except Exception:
                        log.exception("email_poll_error")
                    await asyncio.sleep(300)

            ctx_obj.tasks.append(
                asyncio.create_task(
                    _email_poll_loop(),
                    name="email_poll_loop",
                )
            )
            log.info("email_poll_loop_started", monitor_alias=email_monitor_alias)
        else:
            log.info("email_poll_skipped_no_gmail", monitor_alias=email_monitor_alias)

    return ctx_obj


async def _run_crash_recovery(
    *,
    extension_repo: BudgetExtensionRepository,
    escalation_repo: EscalationRepository,
    conn: Any,
    user_id: str,
    log: Any,
) -> None:
    """Void budget extensions that were granted but never consumed.

    On orchestrator boot, find every ``daily_budget_extension`` row for an
    ``api_extended`` resolution that has no corresponding real invocation_log
    row (i.e. the API call never ran because the orchestrator crashed between
    grant and execution). Void those extensions so the phantom headroom does
    not persist across restarts.

    Realizes docs/superpowers/specs/manual-escalation.md §10.6 row 4.
    """
    try:
        stale_ids = await extension_repo.find_stale_grants()
    except Exception:
        log.exception("crash_recovery_find_stale_failed")
        return

    for esc_id in stale_ids:
        try:
            voided = await extension_repo.void_by_escalation_request_id(esc_id)
            if voided:
                row = await escalation_repo.get(esc_id)
                if row:
                    await write_escalation_event(
                        conn,
                        event=EVENT_EXTENSION_VOIDED,
                        escalation_request_id=esc_id,
                        correlation_id=row.correlation_id,
                        user_id=user_id,
                        task_id=row.task_id,
                        payload={"reason": "crash_recovery"},
                    )
                log.info(
                    "extension_voided_crash_recovery",
                    escalation_request_id=esc_id,
                )
        except Exception:
            log.exception(
                "crash_recovery_void_failed", escalation_request_id=esc_id
            )


async def wire_claude_code_poller(
    ctx: StartupContext,
    skill_h: SkillSystemHandle,
) -> ClaudeCodePoller | None:
    """Slice 21 — construct and start the claude_code ingestion poller.

    Runs after :func:`wire_skill_system` so the lifecycle manager and
    validation executor are available. Idempotent: returns existing
    poller if already wired (e.g. across reruns in tests).

    Realizes docs/superpowers/specs/manual-escalation.md §5.3 ingestion
    paragraph (Donna ingestion / poller).
    """
    log = ctx.log
    if ctx.escalation_gate is None:
        log.info("claude_code_poller_skip_no_gate")
        return None
    if ctx.manual_escalation_config is None or ctx.escalation_repository is None:
        log.info("claude_code_poller_skip_no_config")
        return None
    bundle = skill_h.bundle
    if bundle is None:
        log.info("claude_code_poller_skip_no_skill_bundle")
        return None
    # The gate's host_repo / spec_builder are the ground truth for
    # whether claude_code mode is enabled (see build_startup_context).
    host_repo = ctx.escalation_gate._host_repo
    if host_repo is None:
        log.info("claude_code_poller_skip_no_host_repo")
        return None

    cc_cfg = ctx.manual_escalation_config.modes.claude_code
    triggers = ctx.manual_escalation_config.triggers

    def _executor_factory() -> Any:
        from donna.skills.validation_executor import ValidationExecutor
        return ValidationExecutor(
            model_router=skill_h.subsystem_router,
            config=ctx.skill_config,
        )

    # Slice 22 — pass the tool_request repo + lint config so the router
    # can validate tool_request_fulfillment branches via _validate_tool.
    from donna.cost.tool_lint import ToolLintConfig as _SLC22ToolLintConfig

    _tool_gap_cfg = (
        ctx.manual_escalation_config.tool_gap
        if ctx.manual_escalation_config is not None
        else None
    )
    _tool_lint_config = _SLC22ToolLintConfig(
        detect_secrets_enabled=(
            _tool_gap_cfg.lint.detect_secrets_enabled
            if _tool_gap_cfg is not None
            else False
        ),
        requires_rebuild_default=(
            _tool_gap_cfg.lint.requires_rebuild_default
            if _tool_gap_cfg is not None
            else False
        ),
        default_timeout_seconds=(
            _tool_gap_cfg.lint.default_timeout_seconds
            if _tool_gap_cfg is not None
            else 5
        ),
    )
    router = ManualValidationRouter(
        conn=ctx.db.connection,
        host_repo=host_repo,
        executor_factory=_executor_factory,
        lifecycle=bundle.lifecycle_manager,
        fixture_pass_rate=ctx.skill_config.auto_draft_fixture_pass_rate,
        tool_request_repo=ctx.tool_request_repository,
        tool_lint_config=_tool_lint_config,
        host_repo_path=host_repo.root,
    )

    feedback = _make_claude_code_feedback_callback(
        bot=ctx.bot,
    ) if ctx.bot is not None else None

    poller = ClaudeCodePoller(
        repository=ctx.escalation_repository,
        host_repo=host_repo,
        validation_router=router,
        base_ref=cc_cfg.base_ref,
        feedback=feedback,
        manual_iteration_limit=triggers.manual_iteration_limit,
        feedback_max_failing_cases=cc_cfg.feedback_max_failing_cases,
        dashboard_base_url=f"http://localhost:{ctx.port}",
        tick_seconds=cc_cfg.poll_tick_seconds,
    )
    ctx.claude_code_poller = poller
    ctx.tasks.append(
        asyncio.create_task(poller.run(), name="claude_code_poller")
    )
    log.info(
        "claude_code_poller_wired",
        tick_seconds=cc_cfg.poll_tick_seconds,
        base_ref=cc_cfg.base_ref,
    )
    return poller


def _make_claude_code_feedback_callback(
    *, bot: Any
) -> Callable[[Any, str], Awaitable[None]]:
    """Build a feedback callback bound to the tasks-channel.

    Sent as plain text (no view) — these are status pings, not
    decision points. Spec §10.4 row 1 (failures back to Discord).
    """

    async def feedback(row: Any, message: str) -> None:
        try:
            await bot.send_message("tasks", message)
        except Exception:
            logger.exception(
                "claude_code_feedback_send_failed",
                correlation_id=getattr(row, "correlation_id", None),
            )

    return feedback


def _make_escalation_delivery_callback(
    *,
    bot: Any,
    owner_discord_id: int,
    gate_holder: Callable[[], EscalationGate | None],
    prompt_delivery: PromptDeliveryConfig | None = None,
) -> Callable[[Any], Awaitable[bool]]:
    """Build the Discord delivery callback consumed by the gate + loop.

    Returns True if the four-button message landed in #donna-tasks.
    Late-binds the gate via ``gate_holder`` because the gate, the
    callback, and the bot are all needed to talk to one another.

    Slice 20: when the row carries a chat-mode summary + prompt path,
    the message body switches to the summary and the rendered prompt
    is attached as ``<correlation_id>.md``. Attachment failures are
    best-effort per spec §10.2 row 2 — the message still posts.
    """
    from donna.integrations.discord_views import BudgetEscalationView

    host_base_url = os.environ.get("DONNA_HOST_BASE_URL", "").rstrip("/")

    async def deliver(row: Any) -> bool:
        gate = gate_holder()
        if gate is None:
            return False
        view = BudgetEscalationView(
            correlation_id=row.correlation_id,
            offered_modes=list(row.offered_modes),
            owner_discord_id=owner_discord_id,
            gate=gate,
            task_id=row.task_id,
            estimate_usd=row.estimate_usd,
        )
        # Spec §10.6 row 5 — when api_extended is filtered specifically
        # because the hard monthly ceiling is reached, surface "Monthly
        # cap. Pause / Cancel only." in the Discord summary so the user
        # sees the *why*, not just a thinner button row.
        extension_reason: str | None = None
        if "api_extended" not in row.offered_modes:
            try:
                extension_reason = await gate.extension_filter_reason(
                    user_id=row.user_id, estimate_usd=row.estimate_usd
                )
            except Exception:
                logger.exception(
                    "extension_filter_reason_raised",
                    correlation_id=row.correlation_id,
                )
        text = _build_escalation_message_body(
            row=row,
            host_base_url=host_base_url,
            extension_reason=extension_reason,
        )
        attachment = _build_attachment(
            row=row, prompt_delivery=prompt_delivery
        )
        try:
            sent = await bot.send_message_with_view(
                "tasks", text, view, file=attachment
            )
        except TypeError:
            # Older DonnaBot stub without the ``file`` kwarg — fall back
            # to the no-attachment path so the notification still lands.
            try:
                sent = await bot.send_message_with_view("tasks", text, view)
            except Exception:
                logger.exception(
                    "escalation_delivery_send_raised",
                    correlation_id=row.correlation_id,
                )
                return False
        except Exception:
            logger.exception(
                "escalation_delivery_send_raised",
                correlation_id=row.correlation_id,
            )
            if attachment is not None:
                logger.warning(
                    "attachment_upload_failed",
                    correlation_id=row.correlation_id,
                )
                try:
                    sent = await bot.send_message_with_view(
                        "tasks", text, view
                    )
                except Exception:
                    return False
                return sent is not None
            return False
        return sent is not None

    return deliver


def _build_escalation_message_body(
    *,
    row: Any,
    host_base_url: str,
    extension_reason: str | None = None,
) -> str:
    """Compose the inline text for the escalation Discord notification.

    ``extension_reason`` (spec §10.6 row 5) is set when ``api_extended``
    is missing from ``offered_modes``: ``"over_ceiling"`` triggers the
    "Monthly cap. Pause / Cancel only." line; other values are not
    surfaced to the user (they describe normal toggle / headroom states
    that the button-row already implies).
    """
    summary = getattr(row, "summary", None)
    parts: list[str] = []
    if summary:
        parts.append(f"**Escalation** — {row.task_type}\n{summary}")
    else:
        # Slice 21: enumerate exactly the buttons being rendered so the
        # message matches what the user sees on the BudgetEscalationView.
        # Order mirrors the view: extension → manual modes → pause/cancel.
        button_labels: list[str] = []
        if "api_extended" in row.offered_modes:
            button_labels.append(f"approve a ${row.estimate_usd:.2f} extension")
        if "claude_code" in row.offered_modes:
            button_labels.append("hand off to Claude Code")
        if "chat" in row.offered_modes:
            button_labels.append("answer in chat")
        if "manual" in row.offered_modes and not (
            "claude_code" in row.offered_modes or "chat" in row.offered_modes
        ):
            button_labels.append("hand off manually")
        button_labels.extend(["pause", "cancel"])
        if len(button_labels) == 1:
            choice_line = button_labels[0]
        else:
            choice_line = (
                ", ".join(button_labels[:-1]) + ", or " + button_labels[-1]
            )
        body = (
            f"**Over-budget decision** — {row.task_type}\n"
            f"Estimate: ${row.estimate_usd:.2f}  |  "
            f"Daily remaining: ${row.daily_remaining_usd:.2f}\n"
        )
        if extension_reason == "over_ceiling":
            body += "Monthly cap. Pause / Cancel only."
        else:
            body += f"Choose: {choice_line}."
        parts.append(body)
    parts.append(
        f"Estimate: ${row.estimate_usd:.2f}  |  "
        f"Daily remaining: ${row.daily_remaining_usd:.2f}  |  "
        f"ID: `{row.correlation_id}`"
    )
    if host_base_url:
        parts.append(
            f"Dashboard: {host_base_url}/admin/escalations/{row.correlation_id}"
        )
    return "\n".join(parts)


def _build_attachment(
    *,
    row: Any,
    prompt_delivery: PromptDeliveryConfig | None,
) -> Any | None:
    """Construct a ``discord.File`` for the workspace .md, or ``None``.

    Best-effort: returns ``None`` when the feature flag is off, the row
    has no prompt path, the file is missing, or the file is too large to
    upload. Logs ``attachment_upload_failed`` so the operational team
    can spot the case where the prompt body never reached Discord.
    """
    if prompt_delivery is None or not prompt_delivery.attach_full_prompt_to_discord:
        return None
    prompt_path = getattr(row, "prompt_path", None)
    if not prompt_path:
        return None
    try:
        from pathlib import Path as _Path

        import discord as _discord

        path = _Path(prompt_path)
        if not path.exists():
            logger.warning(
                "attachment_upload_failed",
                correlation_id=row.correlation_id,
                reason="missing_file",
                prompt_path=prompt_path,
            )
            return None
        max_bytes = prompt_delivery.attachment_size_limit_mb * 1024 * 1024
        if path.stat().st_size > max_bytes:
            logger.warning(
                "attachment_upload_failed",
                correlation_id=row.correlation_id,
                reason="oversize",
                prompt_path=prompt_path,
            )
            return None
        return _discord.File(str(path), filename=f"{row.correlation_id}.md")
    except Exception:
        logger.exception(
            "attachment_upload_failed",
            correlation_id=row.correlation_id,
            prompt_path=prompt_path,
        )
        return None


# ---------------------------------------------------------------------------
# wire_skill_system
# ---------------------------------------------------------------------------


async def wire_skill_system(
    ctx: StartupContext,
    *,
    gmail_client: Any | None = None,
    calendar_client: Any | None = None,
    vault_client: Any | None = None,
    vault_writer: Any | None = None,
    memory_store: Any | None = None,
) -> SkillSystemHandle:
    """Register default tools, seed capabilities, assemble skill bundle.

    Always returns a handle. When `skill_config.enabled` is false, the
    bundle is None but `subsystem_router` + `budget_guard=None` are still
    populated so the automation subsystem can wire.

    Integration clients are threaded through to ``register_default_tools``
    so the dependent skill tools register when clients are available at
    boot. Each defaults to None (backward-compat: tests + degraded-mode
    boot that don't supply a client still work correctly); the
    ``task_db`` handle reuses ``ctx.db`` and the ``cost_tracker`` is
    constructed here regardless.
    """
    log = ctx.log
    from donna.skills import tools as _skill_tools_module
    from donna.skills.crons import (
        AsyncCronScheduler,
        NightlyDeps,
        run_nightly_tasks,
    )
    from donna.skills.startup_wiring import assemble_skill_system

    # Shared ModelRouter used by both the skill system and the automation
    # subsystem (F-W3-J). Distinct from ctx.router which serves the
    # interactive request path. Pre-defined here so the automation
    # subsystem wires even when skill_system.enabled=false. The
    # automation dispatcher tolerates a None budget_guard (see
    # AutomationDispatcher._run_one).
    subsystem_router = ModelRouter(
        ctx.models_config, ctx.task_types_config, ctx.project_root,
        invocation_logger=ctx.invocation_logger,
    )

    # CostTracker is constructed here (rather than later, inside the
    # skill-enabled branch) so it can be injected into register_default_tools
    # as the backing client for the cost_summary skill tool. The same
    # instance is reused downstream by BudgetGuard.
    cost_tracker_early = CostTracker(ctx.db.connection)

    # Wave 2 Task 16: register default tools (web_fetch, etc.) on the module-level
    # registry so SkillExecutor instances without an explicit registry can dispatch.
    # Must happen before assemble_skill_system, because the bundle will construct
    # SkillExecutor instances that look up the default registry.
    _skill_tools_module.DEFAULT_TOOL_REGISTRY.clear()
    _skill_tools_module.register_default_tools(
        _skill_tools_module.DEFAULT_TOOL_REGISTRY,
        gmail_client=gmail_client,
        calendar_client=calendar_client,
        task_db=ctx.db,
        cost_tracker=cost_tracker_early,
        vault_client=vault_client,
        vault_writer=vault_writer,
        memory_store=memory_store,
    )
    log.info(
        "default_tools_registered",
        tools=_skill_tools_module.DEFAULT_TOOL_REGISTRY.list_tool_names(),
    )

    notification_service = ctx.notification_service

    async def _skill_system_notifier(message: str) -> None:
        if notification_service is None:
            log.info(
                "skill_system_notification_no_service",
                message=message,
            )
            return
        from donna.notifications.service import (
            CHANNEL_TASKS,
            NOTIF_AUTOMATION_FAILURE,
        )

        await notification_service.dispatch(
            notification_type=NOTIF_AUTOMATION_FAILURE,
            content=message,
            channel=CHANNEL_TASKS,
            priority=4,
        )

    skill_budget_guard: BudgetGuard | None = None
    cost_tracker: CostTracker | None = None
    bundle: SkillSystemBundle | None = None

    if ctx.skill_config.enabled:
        # Wave 2 Task 16: sync capability rows from config/capabilities.yaml on
        # every startup. Redundant for rows already seeded by Alembic, but
        # lets Nick add capabilities via YAML edit + restart without a new
        # migration. Idempotent (UPSERT).
        from donna.skills.seed_capabilities import SeedCapabilityLoader

        cap_yaml = ctx.config_dir / "capabilities.yaml"
        if cap_yaml.exists():
            try:
                loader = SeedCapabilityLoader(connection=ctx.db.connection)
                count = await loader.load_and_upsert(cap_yaml)
                log.info("capabilities_loader_ran", upserted=count)
            except Exception:
                log.exception("capabilities_loader_failed")

        # Wave 1 followup: verify every capability's declared tools are in
        # the registry. Fail-loud rather than silently falling back to the
        # ad_hoc path at runtime.
        from donna.capabilities.capability_tool_check import (
            CapabilityToolRegistryCheck,
        )

        check = CapabilityToolRegistryCheck(
            registry=_skill_tools_module.DEFAULT_TOOL_REGISTRY,
            connection=ctx.db.connection,
            surfacer=ctx.tool_gap_surfacer,
            boot_owner_user_id=getattr(ctx, "user_id", "boot") or "boot",
        )
        await check.validate_all()

        cost_tracker = cost_tracker_early

        skill_budget_guard = BudgetGuard(
            tracker=cost_tracker,
            models_config=ctx.models_config,
            notifier=lambda channel, message: _skill_system_notifier(message),
            extension_repo=ctx.budget_extension_repo,
        )

        bundle = assemble_skill_system(
            connection=ctx.db.connection,
            model_router=subsystem_router,
            budget_guard=skill_budget_guard,
            notifier=_skill_system_notifier,
            config=ctx.skill_config,
            validation_executor_factory=None,  # default real ValidationExecutor
            tool_gap_surfacer=ctx.tool_gap_surfacer,
            tool_registry=_skill_tools_module.DEFAULT_TOOL_REGISTRY,
        )

        if bundle is not None:
            async def _nightly_job() -> None:
                deps = NightlyDeps(
                    detector=bundle.detector,
                    auto_drafter=bundle.auto_drafter,
                    degradation=bundle.degradation,
                    evolution_scheduler=bundle.evolution_scheduler,
                    correction_cluster=bundle.correction_cluster,
                    cost_tracker=cost_tracker,
                    daily_budget_limit_usd=ctx.models_config.cost.daily_pause_threshold_usd,
                    config=ctx.skill_config,
                )
                report = await run_nightly_tasks(deps)
                log.info(
                    "nightly_skill_tasks_done",
                    new_candidates=len(report.new_candidates),
                    drafted=len(report.drafted),
                    evolved=len(report.evolved),
                    degraded=len(report.degraded),
                    correction_flagged=len(report.correction_flagged),
                    errors=len(report.errors),
                )

            scheduler = AsyncCronScheduler(
                hour_utc=ctx.skill_config.nightly_run_hour_utc,
                task=_nightly_job,
                tz=ctx.tz,
            )
            ctx.tasks.append(asyncio.create_task(scheduler.run_forever()))
            log.info(
                "skill_system_started",
                nightly_run_hour_utc=ctx.skill_config.nightly_run_hour_utc,
            )

            # Wave 2 F-W1-D: poll skill_candidate_report.manual_draft_at for
            # manual draft triggers from the API process.
            from donna.skills.manual_draft_poller import ManualDraftPoller

            manual_draft_poller = ManualDraftPoller(
                connection=ctx.db.connection,
                auto_drafter=bundle.auto_drafter,
                candidate_repo=bundle.candidate_repo,
            )

            async def _manual_draft_loop() -> None:
                while True:
                    try:
                        await manual_draft_poller.run_once()
                    except Exception:
                        log.exception("manual_draft_poller_tick_failed")
                    await asyncio.sleep(
                        ctx.skill_config.automation_poll_interval_seconds,
                    )

            ctx.tasks.append(asyncio.create_task(_manual_draft_loop()))
            log.info("manual_draft_poller_started")
    else:
        log.info("skill_system_disabled_in_config")

    return SkillSystemHandle(
        subsystem_router=subsystem_router,
        budget_guard=skill_budget_guard,
        cost_tracker=cost_tracker,
        bundle=bundle,
        notifier=_skill_system_notifier,
    )


# ---------------------------------------------------------------------------
# wire_automation_subsystem
# ---------------------------------------------------------------------------


class _ReclamperSchedulerAdapter:
    """Thin wrapper exposing ``compute_next_run(cron)`` for CadenceReclamper.

    CadenceReclamper calls ``scheduler.compute_next_run(cron_str)`` to
    recompute ``next_run_at`` when active_cadence_cron changes. The real
    AutomationScheduler has no such method, so we adapt the raw
    CronScheduleCalculator here. Mirrors the harness's
    _SchedulerComputeNextRun.
    """

    def __init__(self) -> None:
        from donna.automations.cron import CronScheduleCalculator

        self._cron = CronScheduleCalculator()

    async def compute_next_run(self, cron: str) -> Any:
        from datetime import datetime

        return self._cron.next_run(
            expression=cron, after=datetime.now(UTC),
        )


async def wire_automation_subsystem(
    ctx: StartupContext, skill_h: SkillSystemHandle,
) -> AutomationHandle:
    """Construct AutomationRepository + Dispatcher + Scheduler.

    Runs regardless of `skill_config.enabled` (F-W1-H). Failure is
    logged but not raised — the pre-refactor code had the entire block
    wrapped in a try/except and this preserves that behaviour.
    """
    log = ctx.log
    from donna.automations.alert import AlertEvaluator
    from donna.automations.cadence_policy import CadencePolicy
    from donna.automations.cadence_reclamper import CadenceReclamper
    from donna.automations.cron import CronScheduleCalculator

    try:
        automation_repo = AutomationRepository(ctx.db.connection)
        # Slice 22 — runtime tool-availability check for the dispatcher.
        runtime_tool_check = None
        if ctx.tool_gap_surfacer is not None:
            try:
                from donna.capabilities.runtime_tool_check import RuntimeToolCheck
                from donna.capabilities.tool_requirements import (
                    SkillToolRequirementsLookup,
                )
                from donna.skills import tools as _slc22_skill_tools

                runtime_tool_check = RuntimeToolCheck(
                    registry=_slc22_skill_tools.DEFAULT_TOOL_REGISTRY,
                    lookup=SkillToolRequirementsLookup(ctx.db.connection),
                )
            except Exception:
                log.exception("runtime_tool_check_wire_failed")
        def _automation_skill_executor_factory() -> Any:
            from donna.skills.executor import SkillExecutor
            return SkillExecutor(
                model_router=skill_h.subsystem_router,
                config=ctx.skill_config,
                tool_gap_surfacer=ctx.tool_gap_surfacer,
            )

        automation_dispatcher = AutomationDispatcher(
            connection=ctx.db.connection,
            repository=automation_repo,
            model_router=skill_h.subsystem_router,
            skill_executor_factory=_automation_skill_executor_factory,
            budget_guard=skill_h.budget_guard,
            alert_evaluator=AlertEvaluator(),
            cron=CronScheduleCalculator(),
            notifier=ctx.notification_service,
            config=ctx.skill_config,
            runtime_tool_check=runtime_tool_check,
            tool_gap_surfacer=ctx.tool_gap_surfacer,
        )
        gpu_home_model: str | None = None
        try:
            from donna.llm.types import load_gateway_config
            gw = load_gateway_config(ctx.config_dir)
            gpu_home_model = gw.gpu.home_model
        except Exception:
            log.debug("gpu_home_model_not_available")

        automation_scheduler = AutomationScheduler(
            repository=automation_repo,
            dispatcher=automation_dispatcher,
            poll_interval_seconds=ctx.skill_config.automation_poll_interval_seconds,
            gpu_home_model=gpu_home_model,
        )
        ctx.tasks.append(asyncio.create_task(automation_scheduler.run_forever()))
        log.info(
            "automation_scheduler_started",
            poll_interval_seconds=ctx.skill_config.automation_poll_interval_seconds,
        )

        # Wave 3 Bug-fix: register CadenceReclamper on the skill lifecycle hook
        # so promoting a skill (sandbox → shadow_primary → trusted) recomputes
        # active_cadence_cron for every automation bound to that capability.
        # The harness at tests/e2e/harness.py registers the reclamper for E2E
        # tests; production wiring must do the same or cadence-uplift is inert.
        bundle = skill_h.bundle
        if bundle is not None:
            cadence_path = ctx.config_dir / "automations.yaml"
            if cadence_path.exists():
                try:
                    policy = CadencePolicy.load(cadence_path)
                    reclamper = CadenceReclamper(
                        repo=automation_repo,
                        policy=policy,
                        scheduler=_ReclamperSchedulerAdapter(),
                    )
                    # reclamp_for_capability returns int; the hook signature
                    # expects Awaitable[None], so adapt via a wrapper that
                    # discards the return value.
                    _reclamp_fn: Callable[[str, str], Awaitable[int]] = (
                        reclamper.reclamp_for_capability
                    )

                    async def _reclamp_adapter(
                        capability_name: str, new_state: str,
                    ) -> None:
                        await _reclamp_fn(capability_name, new_state)

                    bundle.lifecycle_manager.after_state_change.register(
                        _reclamp_adapter,
                    )
                    log.info("cadence_reclamper_registered")
                except Exception:
                    log.exception("cadence_reclamper_wiring_failed")

        return AutomationHandle(
            repository=automation_repo,
            dispatcher=automation_dispatcher,
            scheduler=automation_scheduler,
        )
    except Exception:
        log.exception("automation_scheduler_wiring_failed")
        return AutomationHandle(
            repository=None, dispatcher=None, scheduler=None,
        )


# ---------------------------------------------------------------------------
# wire_discord
# ---------------------------------------------------------------------------


async def wire_discord(
    ctx: StartupContext,
    skill_h: SkillSystemHandle,
    automation_h: AutomationHandle,
) -> DiscordHandle:
    """Register slash commands, proactive-prompt config, start bot task.

    If `DISCORD_BOT_TOKEN` / `DISCORD_TASKS_CHANNEL_ID` were not present
    in the environment (checked in `build_startup_context`), no bot was
    constructed — this helper logs `discord_bot_disabled` and returns a
    handle with `bot=None`.

    Wave 3 Task 13: constructs a DiscordIntentDispatcher from the
    ChallengerAgent + ClaudeNoveltyJudge + PendingDraftRegistry +
    CadencePolicy + lifecycle adapter + candidate-report writer, and
    wires it onto the running `DonnaBot` so `on_message` routes new
    utterances through the Wave 3 intent pipeline.
    """
    log = ctx.log

    if ctx.bot is None:
        log.warning(
            "discord_bot_disabled",
            reason="DISCORD_BOT_TOKEN or DISCORD_TASKS_CHANNEL_ID not set",
        )
        return DiscordHandle(bot=None, intent_dispatcher=None)

    # Wave 3: construct the intent dispatcher. Failure here is logged
    # but non-fatal — the bot falls back to the legacy InputParser path.
    intent_dispatcher = await _build_intent_dispatcher(
        ctx, skill_h, automation_h, log,
    )
    if intent_dispatcher is not None:
        # Attach to the already-constructed DonnaBot so `on_message`
        # routes via the Wave 3 path. automation_repo is needed for the
        # AutomationConfirmationView approval coordinator.
        ctx.bot._intent_dispatcher = intent_dispatcher
        ctx.bot._automation_repo = automation_h.repository
        # Wave 4: wire capability-availability guard into AutomationCreationPath.
        from donna.capabilities.tool_requirements import SkillToolRequirementsLookup
        from donna.skills import tools as _skill_tools_module

        ctx.bot._automation_tool_registry = _skill_tools_module.DEFAULT_TOOL_REGISTRY
        _cap_lookup = SkillToolRequirementsLookup(ctx.db.connection)
        ctx.bot._automation_capability_lookup = _cap_lookup.list_required_tools

        # F-W4-K: wire optional-input defaulting lookup into AutomationCreationPath.
        from donna.capabilities.repo_input_schema_lookup import (
            CapabilityInputSchemaDBLookup,
        )

        _input_schema_lookup = CapabilityInputSchemaDBLookup(ctx.db.connection)
        ctx.bot._automation_input_schema_lookup = _input_schema_lookup.lookup
        # Pull the Discord automation default min-interval from config so
        # AutomationCreationPath doesn't hardcode the 300-second floor.
        from donna.automations.cadence_policy import (
            load_discord_automation_default_min_interval_seconds,
        )

        cadence_path = ctx.config_dir / "automations.yaml"
        if cadence_path.exists():
            try:
                ctx.bot._automation_default_min_interval_seconds = (
                    load_discord_automation_default_min_interval_seconds(
                        cadence_path
                    )
                )
            except Exception:
                log.exception("automation_default_min_interval_load_failed")
        # Wire default_alert_conditions lookup so AutomationCreationPath
        # falls back to capability-level defaults when the LLM returns null.
        from donna.capabilities.default_alerts_lookup import (
            CapabilityDefaultAlertsLookup,
        )

        _caps_yaml = ctx.config_dir / "capabilities.yaml"
        if _caps_yaml.exists():
            _default_alerts = CapabilityDefaultAlertsLookup(_caps_yaml)

            async def _async_default_alerts(name: str) -> dict[str, Any] | None:
                return _default_alerts.get(name)

            ctx.bot._automation_default_alerts_lookup = _async_default_alerts

        log.info("discord_intent_dispatcher_wired")

    # Slice 22 — wire the tool-gap ping poster onto the surfacer once
    # we have a bot + escalation_gate + owner_discord_id. The poster is
    # a small closure that builds a ToolGapPingView and posts it to the
    # configured channel. Without these dependencies the surfacer
    # records rows + audits but doesn't ping (boot fail-soft).
    if (
        ctx.bot is not None
        and ctx.escalation_gate is not None
        and ctx.owner_discord_id is not None
        and ctx.tool_gap_surfacer is not None
        and ctx.tool_request_repository is not None
        and ctx.manual_escalation_config is not None
    ):
        try:
            from donna.integrations.discord_views import ToolGapPingView

            _bot = ctx.bot
            _gate = ctx.escalation_gate
            _repo = ctx.tool_request_repository
            _owner = ctx.owner_discord_id
            _channel = ctx.manual_escalation_config.tool_gap.realtime_channel
            _snooze = ctx.manual_escalation_config.tool_gap.snooze_seconds

            async def _post_tool_gap_ping(row: Any) -> bool:
                blocking = (
                    f"capability `{row.blocking_capability_id}`"
                    if row.blocking_capability_id
                    else "skill draft"
                )
                text = (
                    f":rotating_light: **Tool gap (high blocking):** "
                    f"`{row.tool_name}` is required by {blocking}.\n"
                    f"_{row.rationale}_\n"
                    f"Detected at: `{row.detection_point}`"
                )
                view = ToolGapPingView(
                    tool_request_id=row.id,
                    tool_name=row.tool_name,
                    owner_discord_id=_owner,
                    gate=_gate,
                    tool_request_repo=_repo,
                    snooze_seconds=_snooze,
                )
                msg = await _bot.send_message_with_view(_channel, text, view)
                return msg is not None

            ctx.tool_gap_surfacer.set_ping_poster(_post_tool_gap_ping)
            ctx.bot._tool_gap_surfacer = ctx.tool_gap_surfacer
            log.info("tool_gap_ping_poster_wired", channel=_channel)
        except Exception:
            log.exception("tool_gap_ping_poster_wire_failed")

    # Slice 24 — wire the requires_rebuild=True hourly nag (spec §10.5
    # row 1). Mirrors the slice-22 poster pattern: closure over the bot
    # + owner + channel, plus a ticker task on the same task list as
    # the other escalation loops. The provider returns the live
    # ToolRegistry tool-name set so the nagger stops once the user
    # rebuilds + restarts.
    if (
        ctx.bot is not None
        and ctx.owner_discord_id is not None
        and ctx.tool_request_repository is not None
        and ctx.manual_escalation_config is not None
    ):
        try:
            from donna.cost.requires_rebuild_nag import RequiresRebuildNagger
            from donna.skills.tools import DEFAULT_TOOL_REGISTRY

            _nag_bot = ctx.bot
            _nag_owner = ctx.owner_discord_id
            _nag_channel = (
                ctx.manual_escalation_config.tool_gap.realtime_channel
            )

            async def _post_rebuild_nag(row: Any) -> bool:
                text = (
                    f":wrench: **Rebuild reminder:** "
                    f"`{row.tool_name}` is built (branch "
                    f"`{row.resolved_branch}`) but the orchestrator "
                    f"hasn't been restarted with the new image yet. "
                    f"Run `docker compose build && docker compose up -d` "
                    f"once you've merged."
                )
                _ = _nag_owner  # owner-DM scoping is the channel itself
                msg = await _nag_bot.send_message(_nag_channel, text)
                return msg is not None

            nagger = RequiresRebuildNagger(
                repository=ctx.tool_request_repository,
                registered_tools_provider=DEFAULT_TOOL_REGISTRY.list_tool_names,
                ping_poster=_post_rebuild_nag,
            )
            ctx.requires_rebuild_nagger = nagger

            async def _nag_loop() -> None:
                # Tick every minute, same cadence as the other
                # escalation loops. The nagger's per-row cooldown
                # (1 h default) makes the tick rate cheap.
                import asyncio as _asyncio

                while True:
                    try:
                        await nagger.tick_once()
                    except Exception:
                        log.exception("requires_rebuild_nag_tick_failed")
                    await _asyncio.sleep(60)

            ctx.tasks.append(
                asyncio.create_task(
                    _nag_loop(), name="requires_rebuild_nag_loop"
                )
            )
            log.info(
                "requires_rebuild_nagger_wired", channel=_nag_channel
            )
        except Exception:
            log.exception("requires_rebuild_nagger_wire_failed")

    bot = ctx.bot
    # Load Discord config and register slash commands if enabled.
    try:
        from donna.config import load_discord_config
        from donna.integrations.discord_commands import register_commands
        from donna.integrations.discord_submit_command import register_submit_command

        discord_config = load_discord_config(ctx.config_dir)
        if discord_config.commands.enabled:
            register_commands(
                bot, ctx.db, ctx.user_id,
                calendar_client=ctx.calendar_client,
                calendar_id=ctx.calendar_id,
            )
            # Slice 20 — register `/donna_submit` for the chat-mode
            # fallback path. Skipped silently when the bot is unavailable
            # or when manual escalation hasn't been wired (single-user
            # boot without a Discord integration leaves the config None).
            manual_cfg = ctx.manual_escalation_config
            if bot is not None and manual_cfg is not None:
                register_submit_command(
                    bot=bot,
                    conn=ctx.db.connection,
                    config=manual_cfg.prompt_delivery,
                    iteration_limit=manual_cfg.triggers.manual_iteration_limit,
                    owner_discord_id=ctx.owner_discord_id,
                )
            log.info("discord_slash_commands_registered")

        # Wire agent activity feed if agents channel is configured.
        if ctx.agents_channel_id_str:
            from donna.integrations.discord_agent_feed import AgentActivityFeed

            agent_feed = AgentActivityFeed(bot)  # noqa: F841 — side-effect ctor
            log.info("discord_agent_feed_enabled")

        # Start proactive prompt background tasks.
        prompts_cfg = discord_config.proactive_prompts

        # NotificationService is needed for proactive prompts — lazy import.
        try:
            from donna.notifications.proactive_prompts import (  # noqa: F401
                AfternoonInactivityCheck,
                EveningCheckin,
                PostMeetingCapture,
                StaleTaskDetector,
            )

            # Proactive prompts need NotificationService. If it's not yet
            # wired (e.g., no calendar config), skip gracefully.
            # For now, log that proactive prompts are configured but will
            # be started once the full notification stack is wired in server.py.
            log.info(
                "discord_proactive_prompts_configured",
                evening_checkin=prompts_cfg.evening_checkin.enabled,
                stale_detection=prompts_cfg.stale_detection.enabled,
                post_meeting=prompts_cfg.post_meeting_capture.enabled,
                afternoon_inactivity=prompts_cfg.afternoon_inactivity.enabled,
            )
        except Exception:
            log.exception("discord_proactive_prompts_load_failed")

    except Exception:
        log.exception("discord_config_load_failed")

    ctx.tasks.append(asyncio.create_task(bot.start(ctx.discord_token)))
    log.info("discord_bot_enabled", tasks_channel_id=ctx.tasks_channel_id_str)

    return DiscordHandle(
        bot=bot,
        intent_dispatcher=intent_dispatcher,
    )


# ---------------------------------------------------------------------------
# Wave 3 intent-dispatcher construction
# ---------------------------------------------------------------------------


class _SkillLifecycleStateAdapter:
    """Expose `async current_state(capability_name) -> str` over the skill table.

    DiscordIntentDispatcher uses this to decide the cadence clamp via
    CadencePolicy. When no skill row exists for a capability (e.g., the
    capability is claude-native-only), returns ``"claude_native"`` so
    CadencePolicy applies the most conservative clamp.
    """

    def __init__(self, connection: Any) -> None:
        self._conn = connection

    async def current_state(self, capability_name: str) -> str:
        try:
            cursor = await self._conn.execute(
                "SELECT state FROM skill WHERE capability_name = ? "
                "ORDER BY updated_at DESC LIMIT 1",
                (capability_name,),
            )
            row = await cursor.fetchone()
        except Exception:
            return "claude_native"
        if row is None:
            return "claude_native"
        return str(row[0])


class _TasksDbAdapter:
    """Adapt `Database.create_task` to the dispatcher's `insert_task` surface.

    DiscordIntentDispatcher passes (user_id, title, inputs, deadline,
    capability_name); we persist via `Database.create_task` which now
    stores ``capability_name`` + ``inputs_json`` as first-class columns
    (Wave 3 migration e7f8a9b0c1d2).
    """

    def __init__(self, database: Any) -> None:
        self._db = database

    async def insert_task(
        self,
        *,
        user_id: str,
        title: str,
        inputs: dict[str, Any] | None = None,
        deadline: Any | None = None,
        capability_name: str | None = None,
    ) -> str:
        from donna.tasks.db_models import InputChannel

        row = await self._db.create_task(
            user_id=user_id,
            title=title,
            deadline=deadline,
            created_via=InputChannel.DISCORD,
            capability_name=capability_name,
            inputs=inputs,
        )
        return str(row.id)


async def _build_intent_dispatcher(
    ctx: StartupContext,
    skill_h: SkillSystemHandle,
    automation_h: AutomationHandle,
    log: Any,
) -> Any | None:
    """Construct a DiscordIntentDispatcher from live handles.

    Returns ``None`` on any construction failure so the bot falls back
    to the legacy InputParser flow instead of crashing at startup.
    """
    try:
        from donna.agents.challenger_agent import ChallengerAgent
        from donna.agents.claude_novelty_judge import ClaudeNoveltyJudge
        from donna.automations.cadence_policy import CadencePolicy
        from donna.capabilities.matcher import CapabilityMatcher
        from donna.capabilities.registry import CapabilityRegistry
        from donna.integrations.discord_pending_drafts import (
            PendingDraftRegistry,
        )
        from donna.orchestrator.discord_intent_dispatcher import (
            DiscordIntentDispatcher,
        )

        registry = CapabilityRegistry(ctx.db.connection, ctx.skill_config)
        matcher = CapabilityMatcher(registry, config=ctx.skill_config)

        challenger = ChallengerAgent(
            matcher=matcher, model_router=skill_h.subsystem_router,
        )
        novelty = ClaudeNoveltyJudge(
            model_router=skill_h.subsystem_router, matcher=matcher,
        )
        pending = PendingDraftRegistry()

        cadence_path = ctx.config_dir / "automations.yaml"
        policy = (
            CadencePolicy.load(cadence_path)
            if cadence_path.exists()
            else None
        )

        lifecycle_adapter = _SkillLifecycleStateAdapter(ctx.db.connection)
        tasks_adapter = _TasksDbAdapter(ctx.db)

        candidate_writer = (
            skill_h.bundle.candidate_repo if skill_h.bundle is not None else None
        )

        dispatcher = DiscordIntentDispatcher(
            challenger=challenger,
            novelty_judge=novelty,
            pending_drafts=pending,
            tasks_db=tasks_adapter,
            cadence_policy=policy,
            lifecycle_lookup=lifecycle_adapter,
            candidate_report_writer=candidate_writer,
        )
        log.info(
            "discord_intent_dispatcher_built",
            cadence_policy=policy is not None,
            candidate_writer=candidate_writer is not None,
        )
        return dispatcher
    except Exception:
        log.exception("discord_intent_dispatcher_build_failed")
        return None
