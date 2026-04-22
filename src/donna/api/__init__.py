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
    admin_access,
    admin_config,
    admin_dashboard,
    admin_health,
    admin_invocations,
    admin_logs,
    admin_preferences,
    admin_shadow,
    admin_tasks,
    agents,
    health,
    llm,
    schedule,
    tasks,
)
from donna.api.routes import (
    admin_agents as admin_agents_routes,
)
from donna.api.routes import (
    automations as automations_routes,
)
from donna.api.routes import (
    capabilities as capabilities_routes,
)
from donna.api.routes import (
    chat as chat_routes,
)
from donna.api.routes import (
    skill_candidates as skill_candidates_routes,
)
from donna.api.routes import (
    skill_drafts as skill_drafts_routes,
)
from donna.api.routes import (
    skill_runs as skill_runs_routes,
)
from donna.api.routes import (
    skills as skills_routes,
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
        log_level=os.environ.get("DONNA_LOG_LEVEL", "INFO"),
        json_output=os.environ.get("DONNA_DEV_MODE", "").lower() != "true",
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

    from donna.api.auth.config import load as load_auth_config
    from donna.api.auth.dependencies import AuthContext
    from donna.api.auth.email_allowlist import sync as sync_allowlist
    from donna.api.auth.email_allowlist import sync_loop
    from donna.api.auth.immich import ImmichClient

    auth_cfg = load_auth_config(config_dir / "auth.yaml")
    admin_api_key = os.environ.get(auth_cfg.immich.admin_api_key_env, "").strip()
    if not admin_api_key:
        raise RuntimeError(
            f"{auth_cfg.immich.admin_api_key_env} must be set before startup"
        )

    immich_client = ImmichClient(
        internal_url=auth_cfg.immich.internal_url,
        cache_ttl_s=auth_cfg.immich.user_cache_ttl_seconds,
    )
    app.state.auth_config = auth_cfg
    app.state.auth_context = AuthContext(
        conn=db.connection,
        auth_config=auth_cfg,
        immich_client=immich_client,
    )

    try:
        await sync_allowlist(
            db.connection,
            internal_url=auth_cfg.immich.internal_url,
            admin_api_key=admin_api_key,
        )
    except Exception as exc:
        logger.error("auth_allowlist_initial_sync_failed", error=str(exc))

    sync_task = asyncio.create_task(
        sync_loop(
            db.connection,
            internal_url=auth_cfg.immich.internal_url,
            admin_api_key=admin_api_key,
            interval_seconds=auth_cfg.immich.allowlist_sync_interval_seconds,
        )
    )
    app.state.auth_sync_task = sync_task

    # Skill-system background work (nightly cron, auto-drafter, lifecycle
    # manager) lives in the orchestrator (donna-orchestrator) process. See
    # Wave 1 spec §6.4 / F-6 Tasks 14-15. The API only loads the config so
    # admin routes can surface `enabled` status; it no longer starts any
    # skill-system background tasks.
    #
    # Automation subsystem wiring (dispatcher + scheduler) lives in the
    # orchestrator (Wave 1 F-6 Task 15). /admin/automations/{id}/run-now is
    # now a DB-only operation that flips ``next_run_at`` to now; the
    # orchestrator's AutomationScheduler polls the DB and picks it up on its
    # next tick (~15 s).
    try:
        from donna.config import load_skill_system_config

        app.state.skill_system_config = load_skill_system_config(config_dir)
    except Exception:
        logger.warning("skill_system_config_load_failed", exc_info=True)
        app.state.skill_system_config = None

    logger.info("donna_api_started", db_path=str(db_path), port=8200)
    yield

    sync_task_state = getattr(app.state, "auth_sync_task", None)
    if sync_task_state:
        sync_task_state.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sync_task_state
    await queue_worker.stop()
    worker_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await worker_task

    # Skill-system and automation background tasks are owned by the orchestrator
    # process (Wave 1 F-6 Tasks 14-15) — no shutdown cleanup required here.

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

    cors_origins_raw = os.environ.get("DONNA_CORS_ORIGINS", "").strip()
    if cors_origins_raw:
        if "*" in cors_origins_raw.split(","):
            raise RuntimeError(
                "DONNA_CORS_ORIGINS='*' is forbidden when auth cookies are in use. "
                "Set a concrete allowlist or unset the variable for same-origin "
                "deployments behind Caddy."
            )
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[o.strip() for o in cors_origins_raw.split(",")],
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["authorization", "content-type", "x-immich-token"],
        )
    app.add_middleware(RequestLoggingMiddleware)

    app.include_router(health.router)

    from donna.api.routes import auth_flow
    app.include_router(auth_flow.router, prefix="/auth", tags=["auth"])

    app.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
    app.include_router(schedule.router, prefix="/schedule", tags=["schedule"])
    app.include_router(agents.router, prefix="/agents", tags=["agents"])

    app.include_router(admin_dashboard.router, prefix="/admin", tags=["admin"])
    app.include_router(admin_logs.router, prefix="/admin", tags=["admin"])
    app.include_router(admin_invocations.router, prefix="/admin", tags=["admin"])
    app.include_router(admin_tasks.router, prefix="/admin", tags=["admin"])
    app.include_router(admin_config.router, prefix="/admin", tags=["admin"])
    app.include_router(admin_agents_routes.router, prefix="/admin", tags=["admin"])
    app.include_router(admin_shadow.router, prefix="/admin", tags=["admin"])
    app.include_router(admin_preferences.router, prefix="/admin", tags=["admin"])
    app.include_router(admin_health.router, prefix="/admin", tags=["admin"])
    app.include_router(admin_access.router, prefix="/admin", tags=["admin"])
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
