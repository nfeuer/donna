"""FastAPI backend for the Donna Flutter app — Phase 4.

Wraps the orchestrator's internal API for external consumption.
See docs/architecture.md (App Architecture) for the full design.

Start with:
    uvicorn donna.api:app --host 0.0.0.0 --port 8200
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from donna.api.routes import (
    admin_config,
    admin_dashboard,
    admin_invocations,
    admin_logs,
    admin_tasks,
    agents,
    health,
    schedule,
    tasks,
)
from donna.config import load_state_machine_config
from donna.logging.setup import setup_logging
from donna.tasks.database import Database
from donna.tasks.state_machine import StateMachine

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open DB connection on startup; close on shutdown."""
    setup_logging(dev_mode=os.environ.get("DONNA_DEV_MODE", "").lower() == "true")

    config_dir = Path(os.environ.get("DONNA_CONFIG_DIR", "config"))
    db_path = Path(os.environ.get("DONNA_DB_PATH", "/donna/db/donna_tasks.db"))

    sm_config = load_state_machine_config(config_dir)
    state_machine = StateMachine(sm_config)
    db = Database(db_path, state_machine)
    await db.connect()

    app.state.db = db
    app.state.config_dir = config_dir

    logger.info("donna_api_started", db_path=str(db_path), port=8200)
    yield

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

    return app


app = create_app()
