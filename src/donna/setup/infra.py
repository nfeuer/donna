"""Infrastructure setup — directories, Docker network, migrations, cron."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from donna.setup import output


async def create_directories(env: dict[str, str]) -> list[str]:
    """Create storage directories. Returns list of actions taken."""
    actions: list[str] = []
    path_vars = [
        "DONNA_DATA_PATH",
        "DONNA_DB_PATH",
        "DONNA_WORKSPACE_PATH",
        "DONNA_BACKUP_PATH",
        "DONNA_LOG_PATH",
    ]
    for var in path_vars:
        path_str = env.get(var, "")
        if not path_str:
            continue
        path = Path(path_str)
        if path.is_dir():
            output.skipped(var, f"{path} already exists")
        else:
            try:
                path.mkdir(parents=True, exist_ok=True)
                output.passed(var, f"created {path}")
                actions.append(f"Created {path}")
            except PermissionError:
                output.failed(var, f"permission denied creating {path}")
                actions.append(f"FAILED to create {path} — permission denied")
    return actions


async def ensure_docker_network(network_name: str = "homelab") -> bool:
    """Create Docker network if it doesn't exist. Returns True if ready."""
    if not shutil.which("docker"):
        output.failed("Docker network", "docker not found in PATH")
        return False

    # Check if network already exists
    proc = await asyncio.create_subprocess_exec(
        "docker", "network", "inspect", network_name,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    if proc.returncode == 0:
        output.skipped(f"Docker network '{network_name}'", "already exists")
        return True

    # Create the network
    proc = await asyncio.create_subprocess_exec(
        "docker", "network", "create", network_name,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode == 0:
        output.passed(f"Docker network '{network_name}'", "created")
        return True

    output.failed(f"Docker network '{network_name}'", stderr.decode().strip()[:200])
    return False


async def run_alembic_migrations(project_root: Path) -> bool:
    """Run ``alembic upgrade head`` if not already current."""
    alembic_ini = project_root / "alembic.ini"
    if not alembic_ini.is_file():
        output.failed("Alembic migrations", f"alembic.ini not found at {alembic_ini}")
        return False

    if not shutil.which("alembic"):
        output.failed("Alembic migrations", "alembic not found in PATH")
        return False

    # Check current migration status
    proc = await asyncio.create_subprocess_exec(
        "alembic", "current",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(project_root),
    )
    stdout, _ = await proc.communicate()
    current_output = stdout.decode().strip()

    if "(head)" in current_output:
        output.skipped("Alembic migrations", "already at head")
        return True

    # Run migrations
    proc = await asyncio.create_subprocess_exec(
        "alembic", "upgrade", "head",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(project_root),
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode == 0:
        output.passed("Alembic migrations", "upgraded to head")
        return True

    output.failed("Alembic migrations", stderr.decode().strip()[:300])
    return False


def print_cron_instructions(project_root: Path) -> None:
    """Print crontab entries the user should add manually."""
    scripts = [
        (
            "Supabase keepalive",
            f"0 8 */3 * * {project_root}/scripts/supabase_keepalive.sh "
            f">> /var/log/donna-keepalive.log 2>&1",
        ),
        (
            "Watchdog",
            f"*/5 * * * * {project_root}/scripts/watchdog.sh "
            f">> /var/log/donna-watchdog.log 2>&1",
        ),
    ]

    # Check which scripts actually exist
    existing = [(name, cmd) for name, cmd in scripts
                if (project_root / "scripts" / cmd.split("/scripts/")[1].split()[0]).is_file()]

    if not existing:
        return

    output.subheading("Cron Jobs (add manually)")
    output.info("Run 'crontab -e' and add these lines:")
    print()
    for name, cmd in existing:
        print(f"  # {name}")
        print(f"  {cmd}")
    print()


async def check_running_in_docker() -> bool:
    """Return True if we appear to be running inside a Docker container."""
    return (
        os.path.isfile("/.dockerenv")
        or os.environ.get("DOCKER_CONTAINER") == "true"
    )
