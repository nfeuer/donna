"""Donna CLI entry point.

Usage:
    donna run          Start the orchestrator
    donna eval         Run evaluation harness (Phase 3+)
    donna health       Check system health
    donna backup       Trigger a manual backup
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="donna",
        description="Donna AI Personal Assistant",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # donna run
    run_parser = subparsers.add_parser("run", help="Start the orchestrator")
    run_parser.add_argument(
        "--config-dir",
        default="config",
        help="Path to configuration directory",
    )
    run_parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    )
    run_parser.add_argument(
        "--dev",
        action="store_true",
        help="Enable development mode (human-readable logs)",
    )
    run_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to listen on (default: DONNA_PORT env var or 8100)",
    )

    # donna eval
    eval_parser = subparsers.add_parser("eval", help="Run evaluation harness")
    eval_parser.add_argument(
        "--task-type",
        required=True,
        help="Task type to evaluate (e.g., task_parse, classify_priority)",
    )
    eval_parser.add_argument(
        "--model",
        required=True,
        help="Model to evaluate (e.g., ollama/llama3.1:8b-q4)",
    )
    eval_parser.add_argument(
        "--fixtures-dir",
        default="fixtures",
        help="Path to test fixtures directory",
    )
    eval_parser.add_argument(
        "--tier",
        type=int,
        default=None,
        help="Run specific tier only (1-4). Default: run all tiers with pass gates.",
    )

    # donna health
    subparsers.add_parser("health", help="Check system health")

    # donna backup
    subparsers.add_parser("backup", help="Trigger a manual backup")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "run":
        asyncio.run(_run_orchestrator(args))
    elif args.command == "eval":
        asyncio.run(_run_eval(args))
    elif args.command == "health":
        asyncio.run(_health_check())
    elif args.command == "backup":
        asyncio.run(_backup())


async def _run_orchestrator(args: argparse.Namespace) -> None:
    """Start the Donna orchestrator."""
    from donna.logging.setup import setup_logging
    from donna.server import run_server

    setup_logging(log_level=args.log_level, json_output=not args.dev)

    import structlog
    logger = structlog.get_logger()
    logger.info("donna_starting", config_dir=args.config_dir, log_level=args.log_level)

    port: int = args.port or int(os.environ.get("DONNA_PORT", "8100"))
    await run_server(port=port)


async def _run_eval(args: argparse.Namespace) -> None:
    """Run the offline evaluation harness."""
    from donna.logging.setup import setup_logging

    setup_logging(log_level="INFO", json_output=False)

    import structlog
    logger = structlog.get_logger()
    logger.info(
        "eval_starting",
        task_type=args.task_type,
        model=args.model,
        tier=args.tier,
    )

    # TODO: Load fixtures, run evaluation, save model session
    logger.info("evaluation_harness_not_yet_implemented")


async def _health_check() -> None:
    """Run a health check."""
    # TODO: Check SQLite, Discord, calendar, API
    print("Health check not yet implemented.")


async def _backup() -> None:
    """Trigger a manual backup."""
    # TODO: Run SQLite .backup API
    print("Manual backup not yet implemented.")


if __name__ == "__main__":
    main()
