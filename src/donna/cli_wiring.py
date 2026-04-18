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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

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
    discord_token: Optional[str]
    tasks_channel_id_str: Optional[str]
    debug_channel_id_str: Optional[str]
    agents_channel_id_str: Optional[str]
    guild_id_str: Optional[str]
    # Bot + NotificationService are constructed here so skill/automation
    # wiring (which runs before bot.start()) can see a live notifier.
    bot: Optional[Any]
    notification_service: Optional[NotificationService]
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
    budget_guard: Optional[BudgetGuard]
    cost_tracker: Optional[CostTracker]
    bundle: Optional[SkillSystemBundle]
    notifier: Callable[[str], Any]


@dataclass
class AutomationHandle:
    """Return value of `wire_automation_subsystem`."""

    repository: Optional[AutomationRepository]
    dispatcher: Optional[AutomationDispatcher]
    scheduler: Optional[AutomationScheduler]


@dataclass
class DiscordHandle:
    """Return value of `wire_discord`.

    `bot` is the DonnaBot instance (or None if the token/channel env
    vars aren't present). Callers that need the notification service
    should reach through ``StartupContext.notification_service``; this
    handle used to duplicate that field but no consumer read it off the
    handle (F-W3-I).
    """

    bot: Optional[Any]
    intent_dispatcher: Optional[Any] = None  # Wave 3 Task 8 will wire this.


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

    bot: Optional[Any] = None
    notification_service: Optional[NotificationService] = None
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


async def wire_skill_system(ctx: StartupContext) -> SkillSystemHandle:
    """Register default tools, seed capabilities, assemble skill bundle.

    Always returns a handle. When `skill_config.enabled` is false, the
    bundle is None but `subsystem_router` + `budget_guard=None` are still
    populated so the automation subsystem can wire.
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

    # Wave 2 Task 16: register default tools (web_fetch, etc.) on the module-level
    # registry so SkillExecutor instances without an explicit registry can dispatch.
    # Must happen before assemble_skill_system, because the bundle will construct
    # SkillExecutor instances that look up the default registry.
    _skill_tools_module.register_default_tools(
        _skill_tools_module.DEFAULT_TOOL_REGISTRY,
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

    skill_budget_guard: Optional[BudgetGuard] = None
    cost_tracker: Optional[CostTracker] = None
    bundle: Optional[SkillSystemBundle] = None

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

        cost_tracker = CostTracker(ctx.db.connection)

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
        from datetime import datetime, timezone

        return self._cron.next_run(
            expression=cron, after=datetime.now(timezone.utc),
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
                    bundle.lifecycle_manager.after_state_change.register(
                        reclamper.reclamp_for_capability,
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
        except Exception:  # noqa: BLE001 — defensive; fall back to claude_native
            return "claude_native"
        if row is None:
            return "claude_native"
        return row[0]


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
        return row.id


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
