"""FastAPI backend for the Donna Flutter app — Phase 4.

Wraps the orchestrator's internal API for external consumption.
See docs/architecture.md (App Architecture) for the full design.

Start with:
    uvicorn donna.api:app --host 0.0.0.0 --port 8200
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
import yaml
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from donna.api.routes import (
    admin_agents as admin_agents_routes,
)
from donna.api.routes import (
    admin_config,
    admin_dashboard,
    admin_health,
    admin_invocations,
    admin_logs,
    admin_preferences,
    admin_shadow,
    admin_tasks,
    agents,
    automations as automations_routes,
    capabilities as capabilities_routes,
    chat as chat_routes,
    health,
    llm,
    schedule,
    skill_candidates as skill_candidates_routes,
    skill_drafts as skill_drafts_routes,
    skill_runs as skill_runs_routes,
    skills as skills_routes,
    tasks,
)
from donna.chat.config import get_chat_config
from donna.chat.engine import ConversationEngine
from donna.config import load_state_machine_config
from donna.llm.alerter import GatewayAlerter
from donna.llm.queue import LLMQueueWorker
from donna.llm.rate_limiter import RateLimiter
from donna.llm.types import load_gateway_config
from donna.logging.invocation_logger import InvocationLogger
from donna.logging.setup import setup_logging
from donna.models.providers.ollama import OllamaProvider
from donna.tasks.database import Database
from donna.tasks.state_machine import StateMachine

logger = structlog.get_logger()


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every HTTP request with method, path, status, and duration."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Skip logging for health check endpoints
        if request.url.path in ("/health", "/admin/health"):
            return await call_next(request)

        start = time.monotonic()
        response = await call_next(request)
        duration_ms = round((time.monotonic() - start) * 1000, 1)

        component = "admin" if request.url.path.startswith("/admin") else "api"
        event_type = f"{component}.request"

        logger.info(
            event_type,
            event_type=event_type,
            component=component,
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open DB connection on startup; close on shutdown."""
    setup_logging(
        service_name="api",
        dev_mode=os.environ.get("DONNA_DEV_MODE", "").lower() == "true",
    )

    config_dir = Path(os.environ.get("DONNA_CONFIG_DIR", "config"))
    db_path = Path(os.environ.get("DONNA_DB_PATH", "/donna/db/donna_tasks.db"))

    sm_config = load_state_machine_config(config_dir)
    state_machine = StateMachine(sm_config)
    db = Database(db_path, state_machine)
    await db.connect()

    app.state.db = db
    app.state.config_dir = config_dir

    # Load models config for LLM gateway
    models_path = config_dir / "donna_models.yaml"
    models_config: dict = {}
    if models_path.exists():
        with open(models_path) as f:
            models_config = yaml.safe_load(f) or {}
    app.state.models_config = models_config

    # Load gateway config
    gw_config = load_gateway_config(config_dir)
    app.state.llm_gateway_config = gw_config

    # Initialise OllamaProvider
    ollama_cfg = models_config.get("ollama", {})
    ollama_url = os.environ.get(
        "DONNA_OLLAMA_URL",
        ollama_cfg.get("base_url", "http://donna-ollama:11434"),
    )
    ollama = OllamaProvider(
        base_url=ollama_url,
        timeout_s=int(ollama_cfg.get("timeout_s", 120)),
    )
    app.state.ollama = ollama

    # Rate limiter
    rate_limiter = RateLimiter(
        default_rpm=gw_config.default_rpm,
        default_rph=gw_config.default_rph,
        caller_limits=gw_config.caller_limits,
    )
    app.state.rate_limiter = rate_limiter

    # Gateway alerter (log-only for now; replace notifier when Discord bot is available)
    async def _debug_log_notifier(channel: str, message: str) -> None:
        logger.info("gateway_alert", channel=channel, message=message,
                     event_type="llm_gateway.alert")

    alerter = GatewayAlerter(
        notifier=_debug_log_notifier,
        debounce_minutes=gw_config.debounce_minutes,
    )
    app.state.gateway_alerter = alerter

    # LLM Queue Worker
    inv_logger = InvocationLogger(db.connection)
    queue_worker = LLMQueueWorker(
        config=gw_config,
        ollama=ollama,
        inv_logger=inv_logger,
        alerter=alerter,
        rate_limiter=rate_limiter,
    )
    app.state.llm_queue = queue_worker

    # Start worker as background task
    worker_task = asyncio.create_task(queue_worker.run())

    # Chat engine
    chat_config = get_chat_config(config_dir)
    chat_engine = None
    models_yaml = config_dir / "donna_models.yaml"
    task_types_yaml = config_dir / "task_types.yaml"
    if models_yaml.exists() and task_types_yaml.exists():
        try:
            from donna.config import load_models_config, load_task_types_config
            from donna.models.router import ModelRouter

            m_cfg = load_models_config(config_dir)
            t_cfg = load_task_types_config(config_dir)
            project_root = Path(os.environ.get("DONNA_PROJECT_ROOT", "."))
            chat_router = ModelRouter(m_cfg, t_cfg, project_root)
            chat_engine = ConversationEngine(
                db=db, router=chat_router, config=chat_config,
                project_root=project_root,
            )
        except Exception:
            logger.warning("chat_engine_init_failed", exc_info=True)

    app.state.chat_engine = chat_engine
    app.state.chat_config = chat_config

    # Skill system (Phase 3 + 4) — wire everything if enabled.
    app.state.skill_system_bundle = None
    app.state.skill_cron_scheduler = None
    app.state.skill_cron_task = None
    try:
        from donna.config import (
            load_models_config,
            load_skill_system_config,
            load_task_types_config,
        )
        from donna.cost.budget import BudgetGuard
        from donna.cost.tracker import CostTracker
        from donna.models.router import ModelRouter
        from donna.skills.crons import (
            AsyncCronScheduler,
            NightlyDeps,
            run_nightly_tasks,
        )
        from donna.skills.startup_wiring import assemble_skill_system

        skill_config = load_skill_system_config(config_dir)
        app.state.skill_system_config = skill_config

        if skill_config.enabled:
            m_cfg = load_models_config(config_dir)
            t_cfg = load_task_types_config(config_dir)
            project_root = Path(os.environ.get("DONNA_PROJECT_ROOT", "."))
            skill_router = ModelRouter(m_cfg, t_cfg, project_root)
            cost_tracker = CostTracker(db.connection)
            skill_budget_guard = BudgetGuard(
                tracker=cost_tracker,
                models_config=m_cfg,
                notifier=_debug_log_notifier,
            )

            async def _skill_notifier(message: str) -> None:
                logger.info(
                    "skill_system_notification",
                    message=message,
                    event_type="skill_system.alert",
                )

            bundle = assemble_skill_system(
                connection=db.connection,
                model_router=skill_router,
                budget_guard=skill_budget_guard,
                notifier=_skill_notifier,
                config=skill_config,
                validation_executor_factory=None,  # defaults to real ValidationExecutor
            )
            app.state.skill_system_bundle = bundle
            if bundle is not None:
                # Expose references used by the admin dashboard routes.
                app.state.skill_lifecycle_manager = bundle.lifecycle_manager
                app.state.auto_drafter = bundle.auto_drafter

                async def _nightly_job() -> None:
                    deps = NightlyDeps(
                        detector=bundle.detector,
                        auto_drafter=bundle.auto_drafter,
                        degradation=bundle.degradation,
                        evolution_scheduler=bundle.evolution_scheduler,
                        correction_cluster=bundle.correction_cluster,
                        cost_tracker=cost_tracker,
                        daily_budget_limit_usd=m_cfg.cost.daily_pause_threshold_usd,
                        config=skill_config,
                    )
                    report = await run_nightly_tasks(deps)
                    logger.info(
                        "nightly_skill_tasks_done",
                        new_candidates=len(report.new_candidates),
                        drafted=len(report.drafted),
                        evolved=len(report.evolved),
                        degraded=len(report.degraded),
                        correction_flagged=len(report.correction_flagged),
                        errors=len(report.errors),
                    )

                scheduler = AsyncCronScheduler(
                    hour_utc=skill_config.nightly_run_hour_utc,
                    task=_nightly_job,
                )
                cron_task = asyncio.create_task(scheduler.run_forever())
                app.state.skill_cron_scheduler = scheduler
                app.state.skill_cron_task = cron_task
                logger.info(
                    "skill_system_started",
                    nightly_run_hour_utc=skill_config.nightly_run_hour_utc,
                )

                # Automation subsystem — scheduler + dispatcher
                app.state.automation_scheduler = None
                app.state.automation_scheduler_task = None
                app.state.automation_dispatcher = None

                try:
                    from donna.automations.alert import AlertEvaluator
                    from donna.automations.cron import CronScheduleCalculator
                    from donna.automations.dispatcher import AutomationDispatcher
                    from donna.automations.repository import AutomationRepository
                    from donna.automations.scheduler import AutomationScheduler

                    automation_repo = AutomationRepository(db.connection)
                    dispatcher = AutomationDispatcher(
                        connection=db.connection,
                        repository=automation_repo,
                        model_router=skill_router,
                        skill_executor_factory=lambda: None,
                        budget_guard=skill_budget_guard,
                        alert_evaluator=AlertEvaluator(),
                        cron=CronScheduleCalculator(),
                        notifier=getattr(app.state, "notification_service", None),
                        config=skill_config,
                    )
                    app.state.automation_dispatcher = dispatcher
                    app.state.automation_repository = automation_repo

                    auto_scheduler = AutomationScheduler(
                        repository=automation_repo,
                        dispatcher=dispatcher,
                        poll_interval_seconds=skill_config.automation_poll_interval_seconds,
                    )
                    automation_task = asyncio.create_task(auto_scheduler.run_forever())
                    app.state.automation_scheduler = auto_scheduler
                    app.state.automation_scheduler_task = automation_task
                    logger.info(
                        "automation_scheduler_started",
                        poll_interval_seconds=skill_config.automation_poll_interval_seconds,
                    )
                except Exception:
                    logger.warning("automation_scheduler_wiring_failed", exc_info=True)
        else:
            logger.info("skill_system_disabled_in_config")
    except Exception:
        logger.warning("skill_system_wiring_failed", exc_info=True)

    logger.info("donna_api_started", db_path=str(db_path), port=8200)
    yield

    await queue_worker.stop()
    worker_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await worker_task

    # Stop the skill-system cron scheduler.
    scheduler = getattr(app.state, "skill_cron_scheduler", None)
    cron_task = getattr(app.state, "skill_cron_task", None)
    if scheduler is not None:
        scheduler.stop()
    if cron_task is not None:
        cron_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cron_task

    automation_scheduler = getattr(app.state, "automation_scheduler", None)
    automation_task = getattr(app.state, "automation_scheduler_task", None)
    if automation_scheduler is not None:
        automation_scheduler.stop()
    if automation_task is not None:
        automation_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await automation_task

    await ollama.close()
    await db.close()
    logger.info("donna_api_stopped")


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    app = FastAPI(
        title="Donna API",
        description="REST API for Donna Flutter Web + Android app (Phase 4).",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    cors_origins = os.environ.get("DONNA_CORS_ORIGINS", "*").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in cors_origins],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestLoggingMiddleware)

    app.include_router(health.router)
    app.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
    app.include_router(schedule.router, prefix="/schedule", tags=["schedule"])
    app.include_router(agents.router, prefix="/agents", tags=["agents"])

    # Admin routes for the Management GUI (no auth required)
    app.include_router(admin_dashboard.router, prefix="/admin", tags=["admin"])
    app.include_router(admin_logs.router, prefix="/admin", tags=["admin"])
    app.include_router(admin_invocations.router, prefix="/admin", tags=["admin"])
    app.include_router(admin_tasks.router, prefix="/admin", tags=["admin"])
    app.include_router(admin_config.router, prefix="/admin", tags=["admin"])
    app.include_router(admin_agents_routes.router, prefix="/admin", tags=["admin"])
    app.include_router(admin_shadow.router, prefix="/admin", tags=["admin"])
    app.include_router(admin_preferences.router, prefix="/admin", tags=["admin"])
    app.include_router(admin_health.router, prefix="/admin", tags=["admin"])
    app.include_router(capabilities_routes.router, prefix="/admin", tags=["capabilities"])
    app.include_router(skills_routes.router, prefix="/admin", tags=["skills"])
    app.include_router(skill_runs_routes.router, prefix="/admin", tags=["skill-runs"])
    app.include_router(skill_candidates_routes.router, prefix="/admin", tags=["skill-candidates"])
    app.include_router(skill_drafts_routes.router, prefix="/admin", tags=["skill-drafts"])
    app.include_router(automations_routes.router, prefix="/admin", tags=["automations"])

    # LLM gateway for homelab services
    app.include_router(llm.router, prefix="/llm", tags=["llm"])

    # Chat interface
    app.include_router(chat_routes.router, prefix="/chat", tags=["chat"])

    return app


app = create_app()
