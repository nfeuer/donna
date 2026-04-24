"""Donna CLI entry point.

Usage:
    donna run          Start the orchestrator
    donna eval         Run evaluation harness (Phase 3+)
    donna health       Check system health
    donna backup       Trigger a manual backup
    donna setup        Interactive setup wizard
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
from datetime import UTC
from typing import Any, cast


def _build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argparse parser with all subcommands.

    Extracted from main() so unit tests can exercise argument parsing
    without invoking any dispatch side effects.
    """
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

    # donna setup
    setup_parser = subparsers.add_parser("setup", help="Interactive setup wizard")
    setup_parser.add_argument(
        "--phase",
        type=int,
        default=None,
        choices=[1, 2, 3, 4],
        help="Target deployment phase (1-4). Prompted if omitted.",
    )
    setup_parser.add_argument(
        "--reconfigure",
        type=str,
        default=None,
        metavar="STEP_ID",
        help="Re-run a specific step (e.g. discord_channels)",
    )
    setup_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be configured without writing anything",
    )

    # Wave 1: test-notification subcommand
    tn_parser = subparsers.add_parser(
        "test-notification",
        help="Send a test notification via the live NotificationService",
    )
    tn_parser.add_argument("--config-dir", default="config")
    tn_parser.add_argument(
        "--type",
        required=True,
        help="Notification type (digest, automation_alert, etc.)",
    )
    tn_parser.add_argument(
        "--channel",
        default="tasks",
        help="Channel name (tasks, digest, debug)",
    )
    tn_parser.add_argument(
        "--content",
        required=True,
        help="Message content",
    )
    tn_parser.add_argument(
        "--priority",
        type=int,
        default=3,
        help="Notification priority (1-5)",
    )

    # donna memory backfill (slice 14)
    memory_parser = subparsers.add_parser(
        "memory",
        help="Memory index maintenance commands",
    )
    memory_sub = memory_parser.add_subparsers(dest="memory_command")
    backfill_parser = memory_sub.add_parser(
        "backfill",
        help="Re-ingest every enabled source into the memory index",
    )
    backfill_parser.add_argument("--config-dir", default="config")
    backfill_parser.add_argument(
        "--source",
        default="all",
        choices=["vault", "chat", "task", "correction", "all"],
        help="Which source to backfill (default: all)",
    )
    backfill_parser.add_argument(
        "--user-id",
        default=None,
        help="User ID to backfill (default: $DONNA_USER_ID or 'nick')",
    )

    return parser


def main() -> None:
    """Main CLI entry point."""
    parser = _build_parser()
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
    elif args.command == "setup":
        asyncio.run(_setup(args))
    elif args.command == "test-notification":
        asyncio.run(_test_notification(args))
    elif args.command == "memory":
        if args.memory_command == "backfill":
            sys.exit(asyncio.run(_run_memory_backfill(args)))
        else:
            parser.parse_args([args.command, "--help"])
            sys.exit(1)


async def _run_orchestrator(args: argparse.Namespace) -> None:
    """Start the Donna orchestrator.

    Thin glue after F-W2-E: hands off to `cli_wiring.build_startup_context`
    and the three `wire_*` helpers, then awaits the shared asyncio task
    list until one subsystem completes (normally an indefinite-block case).
    """
    from donna.cli_wiring import (
        build_startup_context,
        wire_automation_subsystem,
        wire_discord,
        wire_skill_system,
    )
    from donna.logging.setup import setup_logging
    from donna.server import run_server

    setup_logging(log_level=args.log_level, json_output=not args.dev)

    ctx = await build_startup_context(args)
    log = ctx.log

    # Server task is launched first so clients can probe /healthz even if
    # downstream wiring is still in flight. Matches the pre-refactor order.
    ctx.tasks.append(asyncio.create_task(run_server(port=ctx.port)))

    # Wave 5 F-W4-I: attempt to build a GmailClient at boot so Gmail skill
    # tools register for capabilities like email_triage. Non-fatal on failure.
    # Wave 1 followup: also attempt a GoogleCalendarClient for calendar_read.
    from donna.cli_wiring import (
        _build_episodic_sources,
        _start_commitment_log_cron,
        _start_daily_reflection_cron,
        _start_meeting_end_poller,
        _start_memory_tasks,
        _start_person_profile_cron,
        _start_weekly_review_cron,
        _try_build_calendar_client,
        _try_build_commitment_log_skill,
        _try_build_daily_reflection_skill,
        _try_build_gmail_client,
        _try_build_meeting_note_skill,
        _try_build_memory_informed_writer,
        _try_build_memory_store,
        _try_build_person_profile_skill,
        _try_build_template_renderer,
        _try_build_vault_client,
        _try_build_vault_writer,
        _try_build_weekly_review_skill,
    )

    gmail_client = _try_build_gmail_client(ctx.config_dir)
    calendar_client = _try_build_calendar_client(ctx.config_dir)
    vault_client = _try_build_vault_client(ctx.config_dir)
    vault_writer = await _try_build_vault_writer(ctx.config_dir, vault_client)
    memory_store, memory_handles = await _try_build_memory_store(
        ctx.config_dir,
        ctx.db,
        ctx.user_id,
        ctx.invocation_logger,
        vault_client,
    )
    _start_memory_tasks(ctx, memory_handles)
    _build_episodic_sources(ctx.config_dir, memory_store, ctx.db, ctx.user_id)

    # Slice 15 — template-driven vault writes.
    template_renderer = _try_build_template_renderer(ctx.project_root)
    meeting_skill, meeting_cfg = _try_build_meeting_note_skill(
        ctx.config_dir,
        renderer=template_renderer,
        memory_store=memory_store,
        vault_client=vault_client,
        vault_writer=vault_writer,
        router=ctx.router,
        invocation_logger=ctx.invocation_logger,
        user_id=ctx.user_id,
    )
    _start_meeting_end_poller(ctx, skill=meeting_skill, config=meeting_cfg)

    # Slice 16 — cadence-driven template writes (shared writer).
    autowrite_writer = _try_build_memory_informed_writer(
        ctx.config_dir,
        renderer=template_renderer,
        vault_client=vault_client,
        vault_writer=vault_writer,
        router=ctx.router,
        invocation_logger=ctx.invocation_logger,
    )
    reflection_skill, reflection_cfg = _try_build_daily_reflection_skill(
        ctx.config_dir,
        writer=autowrite_writer,
        memory_store=memory_store,
        db_connection=ctx.db.connection,
        user_id=ctx.user_id,
    )
    _start_daily_reflection_cron(
        ctx, skill=reflection_skill, config=reflection_cfg
    )
    commitment_skill, commitment_cfg = _try_build_commitment_log_skill(
        ctx.config_dir,
        writer=autowrite_writer,
        memory_store=memory_store,
        db_connection=ctx.db.connection,
        user_id=ctx.user_id,
    )
    _start_commitment_log_cron(
        ctx, skill=commitment_skill, config=commitment_cfg
    )
    weekly_skill, weekly_cfg = _try_build_weekly_review_skill(
        ctx.config_dir,
        writer=autowrite_writer,
        memory_store=memory_store,
        vault_client=vault_client,
        db_connection=ctx.db.connection,
        user_id=ctx.user_id,
    )
    _start_weekly_review_cron(ctx, skill=weekly_skill, config=weekly_cfg)
    profile_skill, profile_cfg = _try_build_person_profile_skill(
        ctx.config_dir,
        writer=autowrite_writer,
        memory_store=memory_store,
        vault_client=vault_client,
        db_connection=ctx.db.connection,
        user_id=ctx.user_id,
    )
    _start_person_profile_cron(ctx, skill=profile_skill, config=profile_cfg)
    skill_h = await wire_skill_system(
        ctx,
        gmail_client=gmail_client,
        calendar_client=calendar_client,
        vault_client=vault_client,
        vault_writer=vault_writer,
        memory_store=memory_store,
    )
    automation_h = await wire_automation_subsystem(ctx, skill_h)
    _discord_h = await wire_discord(ctx, skill_h, automation_h)

    try:
        done, pending = await asyncio.wait(
            ctx.tasks, return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            # Swallow cancellation + unexpected errors during cleanup;
            # surfaced via task.exception() on the `done` set below.
            with contextlib.suppress(BaseException):
                await task
        # Surface any exception from the completed task
        for task in done:
            if task.exception() is not None:
                log.error("orchestrator_task_failed", exc_info=task.exception())
    finally:
        await ctx.db.close()


def _parse_model_arg(model_str: str) -> tuple[str, str]:
    """Parse a 'provider/model' string into (provider_name, model_id).

    Examples:
        "ollama/qwen2.5:32b-instruct-q6_K" → ("ollama", "qwen2.5:32b-instruct-q6_K")
        "anthropic/claude-sonnet-4-20250514" → ("anthropic", "claude-sonnet-4-20250514")

    Raises ValueError if the format is invalid.
    """
    provider, _, model_id = model_str.partition("/")
    if not model_id:
        raise ValueError(
            f"Model must be in 'provider/model' format, got: {model_str!r}"
        )
    return provider, model_id


async def _run_eval(args: argparse.Namespace) -> None:
    """Run the offline evaluation harness."""
    import json
    from pathlib import Path

    from donna.config import load_models_config, load_task_types_config
    from donna.logging.setup import setup_logging
    from donna.models.providers.anthropic import AnthropicProvider
    from donna.models.router import ModelRouter
    from donna.models.validation import validate_output

    setup_logging(log_level="INFO", json_output=False)

    import structlog
    logger = structlog.get_logger()
    logger.info("eval_starting", task_type=args.task_type, model=args.model, tier=args.tier)

    project_root = Path(__file__).resolve().parents[2]
    config_dir = project_root / "config"
    fixtures_dir = Path(args.fixtures_dir)

    models_config = load_models_config(config_dir)
    task_types_config = load_task_types_config(config_dir)
    router = ModelRouter(models_config, task_types_config, project_root)

    template = router.get_prompt_template(args.task_type)
    schema = router.get_output_schema(args.task_type)

    # Parse --model arg to instantiate the target provider directly.
    # This bypasses routing so eval can test any provider/model combo.
    provider_name, model_id = _parse_model_arg(args.model)

    provider: Any
    if provider_name == "ollama":
        from donna.models.providers.ollama import OllamaProvider

        provider = OllamaProvider(
            base_url=models_config.ollama.base_url,
            timeout_s=models_config.ollama.timeout_s,
        )
    elif provider_name == "anthropic":
        provider = AnthropicProvider()
    else:
        print(f"Unknown provider: {provider_name!r}")
        sys.exit(1)

    task_fixtures_dir = fixtures_dir / args.task_type
    if not task_fixtures_dir.exists():
        print(f"No fixtures found for task type {args.task_type!r} at {task_fixtures_dir}")
        sys.exit(1)

    fixture_files = sorted(task_fixtures_dir.glob("tier*.json"))
    if args.tier is not None:
        fixture_files = [f for f in fixture_files if f.name.startswith(f"tier{args.tier}")]

    if not fixture_files:
        print(f"No fixture files found for task type {args.task_type!r} (tier={args.tier})")
        sys.exit(1)

    overall_pass = True
    for fixture_path in fixture_files:
        with open(fixture_path) as fh:
            fixture = json.load(fh)

        tier = fixture["tier"]
        name = fixture["name"]
        pass_gate: float = fixture["pass_gate"]
        cases: list[dict[str, Any]] = fixture["cases"]

        passed = 0
        print(f"\nTier {tier} — {name}  ({len(cases)} cases, gate: {pass_gate:.0%})")
        print("-" * 60)

        for case in cases:
            case_id = case["id"]
            expected: dict[str, Any] = case["expected"]
            prompt = _render_eval_prompt(template, case["input"])

            try:
                result, _ = await provider.complete(prompt, model_id)
                validate_output(result, schema)
                mismatches = _compare_fields(expected, result)
                if not mismatches:
                    passed += 1
                    print(f"  PASS  {case_id}")
                else:
                    print(f"  FAIL  {case_id}  — {'; '.join(mismatches)}")
            except Exception as exc:
                print(f"  ERROR {case_id}  — {exc}")

        pass_rate = passed / len(cases)
        tier_pass = pass_rate >= pass_gate
        status = "PASS" if tier_pass else "FAIL"
        print(f"\n  {status}  {passed}/{len(cases)}  ({pass_rate:.0%} vs gate {pass_gate:.0%})")
        if not tier_pass:
            overall_pass = False

    print("\n" + "=" * 60)
    print(f"Overall: {'PASS' if overall_pass else 'FAIL'}")
    if not overall_pass:
        sys.exit(1)


def _render_eval_prompt(template: str, case_input: str | dict[str, Any]) -> str:
    """Render a prompt template with fixture case input."""
    from datetime import datetime

    now = datetime.now(UTC)
    result = template
    result = result.replace("{{ current_date }}", now.strftime("%Y-%m-%d"))
    result = result.replace("{{ current_time }}", now.strftime("%H:%M %Z"))

    if isinstance(case_input, str):
        result = result.replace("{{ user_input }}", case_input)
    else:
        for key, value in case_input.items():
            result = result.replace(f"{{{{ {key} }}}}", str(value) if value is not None else "")

    return result


def _compare_fields(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Return mismatch descriptions for fields declared in expected."""
    mismatches: list[str] = []
    for key, exp_val in expected.items():
        act_val = actual.get(key)
        if exp_val is None:
            if act_val is not None:
                mismatches.append(f"{key}: expected null, got {act_val!r}")
        elif isinstance(exp_val, str):
            if str(act_val).lower().strip() != exp_val.lower().strip():
                mismatches.append(f"{key}: expected {exp_val!r}, got {act_val!r}")
        else:
            if act_val != exp_val:
                mismatches.append(f"{key}: expected {exp_val!r}, got {act_val!r}")
    return mismatches


async def _health_check() -> None:
    """Run a self-diagnostic health check."""
    from pathlib import Path

    from donna.logging.setup import setup_logging
    from donna.resilience.health_check import SelfDiagnostic

    setup_logging(log_level="WARNING", json_output=False)

    tasks_db = Path(os.environ.get("DONNA_DB_PATH", "donna_tasks.db"))
    logs_db = Path(os.environ.get("DONNA_LOGS_DB_PATH", "donna_logs.db"))

    diagnostic = SelfDiagnostic(tasks_db_path=tasks_db, logs_db_path=logs_db)
    warnings = await diagnostic.run()

    if warnings:
        print(f"{len(warnings)} issue(s) found:")
        for w in warnings:
            print(f"  {w}")
        sys.exit(1)
    else:
        print("All checks passed.")


async def _backup() -> None:
    """Trigger a manual SQLite backup."""
    from pathlib import Path

    from donna.logging.setup import setup_logging
    from donna.resilience.backup import BackupManager

    setup_logging(log_level="INFO", json_output=False)

    db_path = Path(os.environ.get("DONNA_DB_PATH", "donna_tasks.db"))
    db_dir = db_path.parent if db_path.parent != Path(".") else Path.cwd()
    backup_dir = Path(os.environ.get("DONNA_BACKUP_DIR", "/donna/backups"))

    manager = BackupManager(db_dir=db_dir, backup_dir=backup_dir)
    paths = await manager.backup_all(label="manual")
    manager.rotate_backups()

    if paths:
        print(f"Backed up {len(paths)} database(s):")
        for p in paths:
            print(f"  {p}")
    else:
        print("No databases found to back up.")


async def _setup(args: argparse.Namespace) -> None:
    """Run the interactive setup wizard."""
    from pathlib import Path

    from donna.setup.wizard import run_wizard

    project_root = Path(__file__).resolve().parents[2]

    try:
        success = await run_wizard(
            project_root=project_root,
            phase=args.phase,
            reconfigure=args.reconfigure,
            dry_run=args.dry_run,
        )
    except KeyboardInterrupt:
        print("\nSetup interrupted. Run 'donna setup' to resume.")
        sys.exit(1)

    if not success:
        sys.exit(1)


async def _test_notification(args: argparse.Namespace) -> None:
    """Dev-only: construct bot + NotificationService, dispatch one message, exit."""
    from pathlib import Path

    from donna.config import load_calendar_config
    from donna.integrations.discord_bot import DonnaBot
    from donna.notifications.service import NotificationService

    token = os.environ["DISCORD_BOT_TOKEN"]
    tasks_channel_id = int(os.environ["DISCORD_TASKS_CHANNEL_ID"])
    debug_channel_id_env = os.environ.get("DISCORD_DEBUG_CHANNEL_ID")
    agents_channel_id_env = os.environ.get("DISCORD_AGENTS_CHANNEL_ID")
    guild_id_env = os.environ.get("DISCORD_GUILD_ID")
    user_id = os.environ.get("DONNA_USER_ID", "nick")
    config_dir = Path(args.config_dir)

    # Dev-only path: notification test doesn't route messages through the
    # parser/DB, so we pass None via cast to satisfy DonnaBot's signature.
    bot = DonnaBot(
        input_parser=cast(Any, None),
        database=cast(Any, None),
        tasks_channel_id=tasks_channel_id,
        debug_channel_id=int(debug_channel_id_env) if debug_channel_id_env else None,
        agents_channel_id=int(agents_channel_id_env) if agents_channel_id_env else None,
        guild_id=int(guild_id_env) if guild_id_env else None,
    )
    bot_task = asyncio.create_task(bot.start(token))
    if hasattr(bot, "ready_event"):
        await bot.ready_event.wait()
    else:
        # Fallback: wait up to 30s for discord.py's own wait_until_ready semantics.
        await asyncio.wait_for(bot.wait_until_ready(), timeout=30)

    notification_service = NotificationService(
        bot=bot,
        calendar_config=load_calendar_config(config_dir),
        user_id=user_id,
    )
    sent = await notification_service.dispatch(
        notification_type=args.type,
        content=args.content,
        channel=args.channel,
        priority=args.priority,
    )
    print(f"dispatched={sent}")
    await bot.close()
    await bot_task


async def _run_memory_backfill(args: argparse.Namespace) -> int:
    """Run the slice-14 ``donna memory backfill`` subcommand.

    Builds a minimal Database + MemoryStore + sources, then invokes
    each selected source's ``backfill(user_id)``. Idempotent: the
    second run leaves row counts unchanged (enforced by
    ``UNIQUE(user_id, source_type, source_id)`` at the store level).

    Returns exit code 0 when every requested source succeeded, 1 if
    any raised. Remaining sources still run so partial progress
    lands.
    """
    from pathlib import Path

    import structlog

    from donna.cli_wiring import (
        _build_episodic_sources,
        _try_build_memory_store,
        _try_build_vault_client,
    )
    from donna.config import load_state_machine_config
    from donna.logging.invocation_logger import InvocationLogger
    from donna.logging.setup import setup_logging
    from donna.tasks.database import Database
    from donna.tasks.state_machine import StateMachine

    setup_logging(log_level="INFO", json_output=False)
    log = structlog.get_logger()

    config_dir = Path(args.config_dir)
    user_id = args.user_id or os.environ.get("DONNA_USER_ID", "nick")
    db_path = os.environ.get("DONNA_DB_PATH", "donna_tasks.db")

    state_machine = StateMachine(load_state_machine_config(config_dir))
    db = Database(db_path, state_machine)
    await db.connect()
    try:
        await db.run_migrations()
        invocation_logger = InvocationLogger(db.connection)
        vault_client = _try_build_vault_client(config_dir)
        memory_store, memory_handles = await _try_build_memory_store(
            config_dir, db, user_id, invocation_logger, vault_client,
        )
        if memory_store is None:
            log.error("memory_backfill_unavailable", reason="memory_store_missing")
            return 1
        episodic = _build_episodic_sources(config_dir, memory_store, db, user_id)

        requested = args.source
        wanted = (
            ["vault", "chat", "task", "correction"]
            if requested == "all"
            else [requested]
        )
        summary: dict[str, int | str] = {}
        had_error = False
        for name in wanted:
            try:
                if name == "vault":
                    _q, vault_source = memory_handles or (None, None)
                    if vault_source is None:
                        log.info("memory_backfill_skipped", source=name)
                        summary[name] = "skipped"
                        continue
                    count = await vault_source.backfill(user_id)
                else:
                    src = episodic.get(name)
                    if src is None:
                        log.info("memory_backfill_skipped", source=name)
                        summary[name] = "skipped"
                        continue
                    count = await src.backfill(user_id)
                summary[name] = count
                log.info("memory_backfill_source_done", source=name, count=count)
            except Exception as exc:
                had_error = True
                summary[name] = f"error: {exc}"
                log.exception("memory_backfill_source_failed", source=name)
        log.info("memory_backfill_done", summary=summary)
        print("backfill summary:")
        for name, result in summary.items():
            print(f"  {name}: {result}")
        return 1 if had_error else 0
    finally:
        await db.close()


if __name__ == "__main__":
    main()
