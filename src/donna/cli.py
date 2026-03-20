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
    from pathlib import Path

    from donna.config import (
        load_models_config,
        load_state_machine_config,
        load_task_types_config,
    )
    from donna.logging.invocation_logger import InvocationLogger
    from donna.logging.setup import setup_logging
    from donna.models.router import ModelRouter
    from donna.orchestrator.input_parser import InputParser
    from donna.server import run_server
    from donna.tasks.database import Database
    from donna.tasks.state_machine import StateMachine

    setup_logging(log_level=args.log_level, json_output=not args.dev)

    import structlog
    log = structlog.get_logger()
    log.info("donna_starting", config_dir=args.config_dir, log_level=args.log_level)

    config_dir = Path(args.config_dir)
    project_root = Path(__file__).resolve().parents[2]

    # Load configuration
    models_config = load_models_config(config_dir)
    task_types_config = load_task_types_config(config_dir)
    state_machine_config = load_state_machine_config(config_dir)

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

    tasks: list[asyncio.Task[None]] = [
        asyncio.create_task(run_server(port=port))
    ]

    # Wire up Discord bot if credentials are present
    discord_token = os.environ.get("DISCORD_BOT_TOKEN")
    tasks_channel_id_str = os.environ.get("DISCORD_TASKS_CHANNEL_ID")
    debug_channel_id_str = os.environ.get("DISCORD_DEBUG_CHANNEL_ID")

    if discord_token and tasks_channel_id_str:
        from donna.integrations.discord_bot import DonnaBot

        bot = DonnaBot(
            input_parser=input_parser,
            database=db,
            tasks_channel_id=int(tasks_channel_id_str),
            debug_channel_id=int(debug_channel_id_str) if debug_channel_id_str else None,
        )
        tasks.append(asyncio.create_task(bot.start(discord_token)))
        log.info("discord_bot_enabled", tasks_channel_id=tasks_channel_id_str)
    else:
        log.warning(
            "discord_bot_disabled",
            reason="DISCORD_BOT_TOKEN or DISCORD_TASKS_CHANNEL_ID not set",
        )

    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        # Surface any exception from the completed task
        for task in done:
            if task.exception() is not None:
                log.error("orchestrator_task_failed", exc_info=task.exception())
    finally:
        await db.close()


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
