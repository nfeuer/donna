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
    ModelsConfig,
    SkillSystemConfig,
    TaskTypesConfig,
    load_models_config,
    load_skill_system_config,
    load_state_machine_config,
    load_task_types_config,
)
from donna.cost.budget import BudgetGuard
from donna.cost.tracker import CostTracker
from donna.logging.invocation_logger import InvocationLogger
from donna.models.router import ModelRouter
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
        ctx.tasks.append(asyncio.create_task(source.backfill(ctx.user_id)))


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


def _try_build_calendar_client(config_dir: Path) -> Any | None:
    """Attempt to construct a GoogleCalendarClient from config/calendar.yaml.

    Non-fatal: returns None if config or credentials are absent.
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
        return GoogleCalendarClient(config=cal_cfg)
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
    project_root = Path(__file__).resolve().parents[2]

    # Load configuration
    models_config = load_models_config(config_dir)
    task_types_config = load_task_types_config(config_dir)
    state_machine_config = load_state_machine_config(config_dir)
    skill_config = load_skill_system_config(config_dir)

    # Initialise state machine and database
    state_machine = StateMachine(state_machine_config)
    db_path = os.environ.get("DONNA_DB_PATH", "donna_tasks.db")
    db = Database(db_path, state_machine)
    await db.connect()
    await db.run_migrations()

    # Initialise model layer and input parser
    router = ModelRouter(models_config, task_types_config, project_root)
    invocation_logger = InvocationLogger(db.connection)
    input_parser = InputParser(router, invocation_logger, project_root)

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

        bot = DonnaBot(
            input_parser=input_parser,
            database=db,
            tasks_channel_id=int(tasks_channel_id_str),
            debug_channel_id=int(debug_channel_id_str) if debug_channel_id_str else None,
            agents_channel_id=int(agents_channel_id_str) if agents_channel_id_str else None,
            guild_id=int(guild_id_str) if guild_id_str else None,
        )

        # Wave 1 (F-6 Step 6a): construct NotificationService with the live bot.
        # Tasks 14 and 15 will wire this into the skill-system bundle and the
        # AutomationDispatcher. SMS/Gmail wiring is Wave 2+.
        from donna.config import load_calendar_config

        try:
            calendar_config = load_calendar_config(config_dir)
            notification_service = NotificationService(
                bot=bot,
                calendar_config=calendar_config,
                user_id=user_id,
                sms=None,
                gmail=None,
            )
            log.info("notification_service_wired")
        except Exception:
            log.exception("notification_service_init_failed")

    return StartupContext(
        args=args,
        config_dir=config_dir,
        project_root=project_root,
        log=log,
        models_config=models_config,
        task_types_config=task_types_config,
        skill_config=skill_config,
        db=db,
        state_machine=state_machine,
        router=router,
        invocation_logger=invocation_logger,
        input_parser=input_parser,
        port=port,
        user_id=user_id,
        discord_token=discord_token,
        tasks_channel_id_str=tasks_channel_id_str,
        debug_channel_id_str=debug_channel_id_str,
        agents_channel_id_str=agents_channel_id_str,
        guild_id_str=guild_id_str,
        bot=bot,
        notification_service=notification_service,
    )


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
        )
        await check.validate_all()

        cost_tracker = cost_tracker_early

        skill_budget_guard = BudgetGuard(
            tracker=cost_tracker,
            models_config=ctx.models_config,
            notifier=lambda channel, message: _skill_system_notifier(message),
        )

        bundle = assemble_skill_system(
            connection=ctx.db.connection,
            model_router=subsystem_router,
            budget_guard=skill_budget_guard,
            notifier=_skill_system_notifier,
            config=ctx.skill_config,
            validation_executor_factory=None,  # default real ValidationExecutor
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
        automation_dispatcher = AutomationDispatcher(
            connection=ctx.db.connection,
            repository=automation_repo,
            model_router=skill_h.subsystem_router,
            skill_executor_factory=lambda: None,  # OOS-W1-2
            budget_guard=skill_h.budget_guard,
            alert_evaluator=AlertEvaluator(),
            cron=CronScheduleCalculator(),
            notifier=ctx.notification_service,
            config=ctx.skill_config,
        )
        automation_scheduler = AutomationScheduler(
            repository=automation_repo,
            dispatcher=automation_dispatcher,
            poll_interval_seconds=ctx.skill_config.automation_poll_interval_seconds,
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
        log.info("discord_intent_dispatcher_wired")

    bot = ctx.bot
    # Load Discord config and register slash commands if enabled.
    try:
        from donna.config import load_discord_config
        from donna.integrations.discord_commands import register_commands

        discord_config = load_discord_config(ctx.config_dir)
        if discord_config.commands.enabled:
            register_commands(bot, ctx.db, ctx.user_id)
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
