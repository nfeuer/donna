"""Structured logging setup for Donna.

Configures structlog with JSON output and contextvars for async context
propagation. All services import and call setup_logging() at startup.
See docs/observability.md.
"""

from __future__ import annotations

import contextvars
import logging
import sys

import structlog

# Context variables for async correlation
correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)
user_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "user_id", default="system"
)
channel_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "channel", default=""
)
task_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "task_id", default=""
)


def add_context_vars(
    logger: structlog.types.WrappedLogger,
    method_name: str,
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    """Inject context variables into every log entry."""
    ctx_vars = {
        "correlation_id": correlation_id_var.get(""),
        "user_id": user_id_var.get("system"),
        "channel": channel_var.get(""),
        "task_id": task_id_var.get(""),
    }
    # Only include non-empty values
    for key, value in ctx_vars.items():
        if value:
            event_dict[key] = value
    return event_dict


def setup_logging(
    log_level: str = "INFO",
    json_output: bool = True,
) -> None:
    """Configure structured logging for all Donna services.

    Args:
        log_level: Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_output: If True, output JSON. If False, output human-readable (dev mode).
    """
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        add_context_vars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    renderer: structlog.types.Processor
    if json_output:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
