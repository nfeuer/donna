"""Interactive setup wizard orchestrator.

Walks through each configuration step, validates credentials,
persists state across restarts, and sets up infrastructure.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import questionary

from donna.setup import output
from donna.setup.phases import PHASES, SetupStep, StepPrompt, steps_for_phase
from donna.setup.state import (
    empty_state,
    is_step_done,
    load_state,
    mark_completed,
    mark_skipped,
    save_state,
)
from donna.setup.validators import VALIDATORS, ValidatorResult


def _read_env_file(env_path: Path) -> dict[str, str]:
    """Read a .env file into a dict, ignoring comments and blank lines."""
    env: dict[str, str] = {}
    if not env_path.is_file():
        return env
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def _write_env_file(env: dict[str, str], env_path: Path, example_path: Path) -> None:
    """Write env vars back to .env, preserving structure from .env.example."""
    # Read the example to preserve comments and ordering
    if example_path.is_file():
        lines: list[str] = []
        written_keys: set[str] = set()
        for line in example_path.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in env:
                    lines.append(f"{key}={env[key]}")
                    written_keys.add(key)
                else:
                    lines.append(line)
            else:
                lines.append(line)
        # Append any keys not in the example
        extra = {k: v for k, v in env.items() if k not in written_keys}
        if extra:
            lines.append("")
            lines.append("# === Additional settings ===")
            for k, v in extra.items():
                lines.append(f"{k}={v}")
        env_path.write_text("\n".join(lines) + "\n")
    else:
        # No example — just dump key=value
        content = "\n".join(f"{k}={v}" for k, v in env.items())
        env_path.write_text(content + "\n")


def _backup_env_file(env_path: Path) -> Path | None:
    """Create a timestamped backup of the existing .env file."""
    if not env_path.is_file():
        return None
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    backup_path = env_path.with_suffix(f".backup.{ts}")
    shutil.copy2(env_path, backup_path)
    return backup_path


async def _prompt_for_value(prompt_def: StepPrompt, current_value: str) -> str | None:
    """Prompt user for a single value. Returns None if user wants to leave it empty."""
    suffix = ""
    if prompt_def.help_hint:
        suffix = f" {prompt_def.help_hint}"

    default = current_value or prompt_def.default or ""

    if prompt_def.secret:
        # Show masked current value if set
        if current_value:
            masked = output.mask_secret(current_value)
            output.info(f"Current value: {masked}")

        value = await questionary.password(
            f"  {prompt_def.label}{suffix}:",
        ).ask_async()

        # If user hits enter on a secret prompt and there's a current value, keep it
        if not value and current_value:
            return current_value
    else:
        value = await questionary.text(
            f"  {prompt_def.label}{suffix}:",
            default=default,
        ).ask_async()

    # None means user cancelled (Ctrl+C in questionary)
    if value is None:
        return None

    return value.strip()


async def _run_validator(
    validator_name: str, env: dict[str, str]
) -> ValidatorResult:
    """Look up and run a validator by name."""
    validator_fn = VALIDATORS.get(validator_name)
    if validator_fn is None:
        return ValidatorResult(False, f"Unknown validator: {validator_name}")
    # All validators are async callables
    result: ValidatorResult = await validator_fn(env)  # type: ignore[operator]
    return result


async def _run_step(
    step: SetupStep,
    step_num: int,
    total_steps: int,
    env: dict[str, str],
    state: dict,
    state_path: Path,
    env_path: Path,
    example_path: Path,
) -> bool:
    """Run a single setup step. Returns True if completed/skipped, False if user aborted."""

    output.step_header(step_num, total_steps, step.name)

    # Check if already done
    if is_step_done(state, step.id):
        if step.id in state.get("completed_steps", []):
            # Show existing values (masked for secrets)
            for p in step.prompts:
                val = env.get(p.env_var, "")
                if val:
                    display = output.mask_secret(val) if p.secret else val
                    output.done(p.label, display)
            return True
        else:
            output.skipped(step.name, "previously skipped")
            return True

    # Optional steps — ask if user wants to configure
    if not step.required:
        configure = await questionary.confirm(
            f"  Configure {step.name}?",
            default=False,
        ).ask_async()

        if configure is None:  # Ctrl+C
            return False

        if not configure:
            mark_skipped(state, step.id)
            save_state(state, state_path)
            output.skipped(step.name, "skipped")
            return True

    # Show help text
    if step.help_text:
        for line in step.help_text.splitlines():
            output.info(line)
        print()

    # Retry loop for this step
    while True:
        # Collect values
        step_env: dict[str, str] = {}
        aborted = False

        for prompt_def in step.prompts:
            if not prompt_def.required:
                # Optional sub-prompts — ask if user wants to set them
                current = env.get(prompt_def.env_var, "")
                if not current:
                    hint = prompt_def.help_hint or "(optional)"
                    skip = await questionary.confirm(
                        f"  Set {prompt_def.label}? {hint}",
                        default=False,
                    ).ask_async()
                    if skip is None:
                        aborted = True
                        break
                    if not skip:
                        continue

            value = await _prompt_for_value(prompt_def, env.get(prompt_def.env_var, ""))
            if value is None:  # Ctrl+C
                aborted = True
                break
            if value:
                step_env[prompt_def.env_var] = value

        if aborted:
            return False

        # Merge into env for validation
        merged_env = {**env, **step_env}

        # Validate
        print()
        output.info("Validating...")
        result = await _run_validator(step.validator, merged_env)

        if result.success:
            output.passed(step.name, result.message)

            # Persist to .env and state
            env.update(step_env)
            _write_env_file(env, env_path, example_path)
            mark_completed(state, step.id)
            save_state(state, state_path)
            return True
        else:
            output.failed(step.name, result.message)
            print()

            retry = await questionary.confirm(
                "  Retry this step?",
                default=True,
            ).ask_async()

            if retry is None or not retry:
                # User doesn't want to retry — skip if optional, abort if required
                if not step.required:
                    mark_skipped(state, step.id)
                    save_state(state, state_path)
                    output.skipped(step.name, "skipped after failure")
                    return True
                else:
                    output.error(f"  {step.name} is required and could not be validated.")
                    again = await questionary.confirm(
                        "  Try again anyway?",
                        default=True,
                    ).ask_async()
                    if again is None or not again:
                        return False
                    # Loop back to retry


async def run_wizard(
    project_root: Path,
    phase: int | None = None,
    reconfigure: str | None = None,
    dry_run: bool = False,
) -> bool:
    """Run the interactive setup wizard. Returns True on success."""
    from donna.setup import infra

    docker_dir = project_root / "docker"
    env_path = docker_dir / ".env"
    example_path = docker_dir / ".env.example"
    state_path = docker_dir / ".setup-state.json"

    # Preflight: warn if running inside Docker
    if await infra.check_running_in_docker():
        output.warn("Setup", "Running inside Docker — this should run on the host machine")
        proceed = await questionary.confirm(
            "  Continue anyway?", default=False
        ).ask_async()
        if not proceed:
            return False

    # Load or create state
    state = load_state(state_path)
    existing_env = _read_env_file(env_path)

    if state is not None and reconfigure is None:
        # Resuming a previous session
        output.heading(f"Donna Setup — Resuming (Phase {state['phase']})")

        completed = state.get("completed_steps", [])
        skipped_list = state.get("skipped_steps", [])
        if completed:
            output.info(f"Completed: {', '.join(completed)}")
        if skipped_list:
            output.info(f"Skipped:   {', '.join(skipped_list)}")

        resume = await questionary.confirm(
            "\n  Continue from where you left off?",
            default=True,
        ).ask_async()

        if resume is None:
            return False

        if not resume:
            # Start fresh
            state = None

        if state is not None and phase is None:
            phase = state["phase"]

    # Phase selection (if not resuming or specified via flag)
    if phase is None:
        phase_choices = [
            questionary.Choice(
                title=f"Phase {p}: {name}",
                value=p,
            )
            for p, name in PHASES.items()
        ]
        phase = await questionary.select(
            "Which deployment phase?",
            choices=phase_choices,
        ).ask_async()

        if phase is None:
            return False

    if state is None:
        state = empty_state(phase)
        save_state(state, state_path)
    else:
        state["phase"] = phase
        save_state(state, state_path)

    steps = steps_for_phase(phase)
    total = len(steps)

    output.heading(f"Donna Setup — Phase {phase}: {PHASES[phase]}")
    output.info(f"{total} configuration steps")

    # Handle --reconfigure: jump to specific step
    if reconfigure:
        target_steps = [s for s in steps if s.id == reconfigure]
        if not target_steps:
            output.error(f"Unknown step: {reconfigure!r}")
            available = ", ".join(s.id for s in steps)
            output.info(f"Available: {available}")
            return False

        # Remove from completed/skipped so it re-runs
        step = target_steps[0]
        if step.id in state.get("completed_steps", []):
            state["completed_steps"].remove(step.id)
        if step.id in state.get("skipped_steps", []):
            state["skipped_steps"].remove(step.id)
        save_state(state, state_path)

        step_idx = steps.index(step) + 1
        success = await _run_step(
            step, step_idx, total, existing_env, state, state_path, env_path, example_path
        )
        return success

    # Backup existing .env before modifications
    if env_path.is_file():
        backup = _backup_env_file(env_path)
        if backup:
            output.info(f"Backed up existing .env to {backup.name}")

    # If .env doesn't exist, copy from example
    if not env_path.is_file() and example_path.is_file():
        shutil.copy2(example_path, env_path)
        output.info("Created .env from .env.example")

    # Main step loop
    passed_count = 0
    failed_count = 0
    skipped_count = 0

    for idx, step in enumerate(steps, 1):
        try:
            success = await _run_step(
                step, idx, total, existing_env, state, state_path, env_path, example_path
            )
        except KeyboardInterrupt:
            print()
            output.warn("Setup paused", "Run 'donna setup' to resume")
            save_state(state, state_path)
            return False

        if success:
            if step.id in state.get("skipped_steps", []):
                skipped_count += 1
            else:
                passed_count += 1
        else:
            # User aborted
            output.warn("Setup paused", "Run 'donna setup' to resume")
            save_state(state, state_path)
            return False

    # Infrastructure setup
    output.heading("Infrastructure Setup")

    if not dry_run:
        await infra.create_directories(existing_env)
        await infra.ensure_docker_network()
        await infra.run_alembic_migrations(project_root)
        infra.print_cron_instructions(project_root)
    else:
        output.info("Dry run — skipping infrastructure changes")

    # Final summary
    output.heading("Setup Complete")
    output.summary_line(
        total=total,
        passed_count=passed_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
    )

    print()
    output.info("Next steps:")
    output.info("  1. Review docker/.env for correctness")
    output.info("  2. Start services: docker compose -f docker/donna-core.yml up --build")
    output.info("  3. Check health: donna health")
    print()

    return True
