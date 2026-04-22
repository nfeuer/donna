"""Async credential and service validators.

Each validator receives the env vars collected so far and returns a
``ValidatorResult``.  Validators are referenced by name from step
definitions in ``phases.py``.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ValidatorResult:
    """Outcome of a single validation check."""

    success: bool
    message: str
    details: dict[str, str] = field(default_factory=dict)


async def _http_get(
    url: str,
    headers: dict[str, str] | None = None,
    auth: tuple[str, str] | None = None,
    timeout_s: float = 15,
) -> tuple[int, str]:
    """Lightweight async HTTP GET.  Returns (status_code, body_text)."""
    import aiohttp

    connector = aiohttp.TCPConnector(ssl=True)
    auth_obj = aiohttp.BasicAuth(auth[0], auth[1]) if auth else None
    async with aiohttp.ClientSession(connector=connector) as session, session.get(
        url,
        headers=headers or {},
        auth=auth_obj,
        timeout=aiohttp.ClientTimeout(total=timeout_s),
    ) as resp:
        body = await resp.text()
        return resp.status, body


async def _http_post(
    url: str,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout_s: float = 15,
) -> tuple[int, str]:
    """Lightweight async HTTP POST.  Returns (status_code, body_text)."""
    import aiohttp

    connector = aiohttp.TCPConnector(ssl=True)
    async with aiohttp.ClientSession(connector=connector) as session, session.post(
        url,
        headers=headers or {},
        json=json_body,
        timeout=aiohttp.ClientTimeout(total=timeout_s),
    ) as resp:
        body = await resp.text()
        return resp.status, body


# ---------------------------------------------------------------------------
# Individual validators
# ---------------------------------------------------------------------------


async def validate_anthropic(env: dict[str, str]) -> ValidatorResult:
    """Validate Anthropic API key with a minimal messages request."""
    key = env.get("ANTHROPIC_API_KEY", "")
    if not key:
        return ValidatorResult(False, "ANTHROPIC_API_KEY is empty")
    try:
        status, body = await _http_post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json_body={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        if status == 200:
            return ValidatorResult(True, "API key is valid")
        if status == 401:
            return ValidatorResult(False, "Invalid API key (401 Unauthorized)")
        if status == 403:
            return ValidatorResult(False, "API key forbidden — check billing status")
        if status == 429:
            return ValidatorResult(True, "API key accepted (rate limited, but valid)")
        return ValidatorResult(False, f"Unexpected status {status}: {body[:200]}")
    except Exception as exc:
        return ValidatorResult(False, f"Connection error: {exc}")


async def validate_discord_token(env: dict[str, str]) -> ValidatorResult:
    """Validate Discord bot token by fetching the bot user."""
    token = env.get("DISCORD_BOT_TOKEN", "")
    if not token:
        return ValidatorResult(False, "DISCORD_BOT_TOKEN is empty")
    try:
        status, body = await _http_get(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": f"Bot {token}"},
        )
        if status == 200:
            data = json.loads(body)
            name = data.get("username", "unknown")
            return ValidatorResult(True, f"Bot user: {name}", {"bot_name": name})
        if status == 401:
            return ValidatorResult(False, "Invalid bot token (401 Unauthorized)")
        return ValidatorResult(False, f"Discord API returned {status}: {body[:200]}")
    except Exception as exc:
        return ValidatorResult(False, f"Connection error: {exc}")


async def validate_discord_guild(env: dict[str, str]) -> ValidatorResult:
    """Check the bot can access the specified guild."""
    token = env.get("DISCORD_BOT_TOKEN", "")
    guild_id = env.get("DISCORD_GUILD_ID", "")
    if not guild_id:
        return ValidatorResult(False, "DISCORD_GUILD_ID is empty")
    if not guild_id.isdigit():
        return ValidatorResult(False, f"Guild ID must be numeric, got: {guild_id!r}")
    try:
        status, body = await _http_get(
            f"https://discord.com/api/v10/guilds/{guild_id}",
            headers={"Authorization": f"Bot {token}"},
        )
        if status == 200:
            data = json.loads(body)
            name = data.get("name", "unknown")
            return ValidatorResult(True, f"Guild: {name}", {"guild_name": name})
        if status == 403:
            return ValidatorResult(
                False, "Bot is not a member of this guild — invite it first"
            )
        return ValidatorResult(False, f"Discord API returned {status}: {body[:200]}")
    except Exception as exc:
        return ValidatorResult(False, f"Connection error: {exc}")


async def validate_discord_channels(env: dict[str, str]) -> ValidatorResult:
    """Validate that the bot can see configured Discord channels."""
    token = env.get("DISCORD_BOT_TOKEN", "")
    tasks_id = env.get("DISCORD_TASKS_CHANNEL_ID", "")
    if not tasks_id:
        return ValidatorResult(False, "DISCORD_TASKS_CHANNEL_ID is required")
    if not tasks_id.isdigit():
        return ValidatorResult(False, f"Tasks channel ID must be numeric, got: {tasks_id!r}")

    channel_vars = [
        ("DISCORD_TASKS_CHANNEL_ID", True),
        ("DISCORD_DIGEST_CHANNEL_ID", False),
        ("DISCORD_AGENTS_CHANNEL_ID", False),
        ("DISCORD_DEBUG_CHANNEL_ID", False),
    ]

    errors: list[str] = []
    for var_name, _required in channel_vars:
        channel_id = env.get(var_name, "")
        if not channel_id:
            continue
        if not channel_id.isdigit():
            errors.append(f"{var_name} must be numeric")
            continue
        try:
            status, _body = await _http_get(
                f"https://discord.com/api/v10/channels/{channel_id}",
                headers={"Authorization": f"Bot {token}"},
            )
            if status == 403:
                errors.append(f"{var_name}: bot lacks View Channel permission")
            elif status == 404:
                errors.append(f"{var_name}: channel not found")
            elif status != 200:
                errors.append(f"{var_name}: unexpected status {status}")
        except Exception as exc:
            errors.append(f"{var_name}: connection error — {exc}")

    if errors:
        return ValidatorResult(False, "; ".join(errors))
    return ValidatorResult(True, "All channels accessible")


async def validate_paths(env: dict[str, str]) -> ValidatorResult:
    """Validate storage paths are non-empty strings. Actual creation is in infra."""
    for var in [
        "DONNA_DATA_PATH",
        "DONNA_DB_PATH",
        "DONNA_WORKSPACE_PATH",
        "DONNA_BACKUP_PATH",
        "DONNA_LOG_PATH",
    ]:
        val = env.get(var, "")
        if not val:
            return ValidatorResult(False, f"{var} cannot be empty")
        if not val.startswith("/"):
            return ValidatorResult(False, f"{var} must be an absolute path, got: {val!r}")
    return ValidatorResult(True, "Paths look valid")


async def validate_budget(env: dict[str, str]) -> ValidatorResult:
    """Validate budget values are positive numbers."""
    for var in ["DONNA_MONTHLY_BUDGET_USD", "DONNA_DAILY_PAUSE_THRESHOLD_USD"]:
        val = env.get(var, "")
        if not val:
            return ValidatorResult(False, f"{var} is empty")
        try:
            num = float(val)
            if num <= 0:
                return ValidatorResult(False, f"{var} must be positive, got: {val}")
        except ValueError:
            return ValidatorResult(False, f"{var} must be a number, got: {val!r}")
    return ValidatorResult(True, "Budget limits set")


async def validate_grafana_password(env: dict[str, str]) -> ValidatorResult:
    """Warn if Grafana password is the default."""
    pw = env.get("GRAFANA_ADMIN_PASSWORD", "")
    if not pw:
        return ValidatorResult(False, "GRAFANA_ADMIN_PASSWORD is empty")
    if pw == "changeme":
        return ValidatorResult(
            True,
            "Using default password 'changeme' — change before exposing to network",
        )
    if len(pw) < 8:
        return ValidatorResult(True, "Password is short — consider using 8+ characters")
    return ValidatorResult(True, "Password set")


async def validate_twilio(env: dict[str, str]) -> ValidatorResult:
    """Validate Twilio credentials against the API."""
    sid = env.get("TWILIO_ACCOUNT_SID", "")
    token = env.get("TWILIO_AUTH_TOKEN", "")
    phone = env.get("TWILIO_PHONE_NUMBER", "")
    user_phone = env.get("DONNA_USER_PHONE", "")

    if not sid or not token:
        return ValidatorResult(False, "Account SID and Auth Token are required")

    e164 = re.compile(r"^\+[1-9]\d{1,14}$")
    if phone and not e164.match(phone):
        return ValidatorResult(False, f"TWILIO_PHONE_NUMBER not E.164 format: {phone!r}")
    if user_phone and not e164.match(user_phone):
        return ValidatorResult(False, f"DONNA_USER_PHONE not E.164 format: {user_phone!r}")

    try:
        status, body = await _http_get(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json",
            auth=(sid, token),
        )
        if status == 200:
            return ValidatorResult(True, "Twilio credentials valid")
        if status == 401:
            return ValidatorResult(False, "Invalid Twilio credentials (401)")
        return ValidatorResult(False, f"Twilio API returned {status}: {body[:200]}")
    except Exception as exc:
        return ValidatorResult(False, f"Connection error: {exc}")


async def validate_google_creds_file(env: dict[str, str]) -> ValidatorResult:
    """Check Google credentials file exists and is valid JSON."""
    path_str = env.get("GOOGLE_CREDENTIALS_PATH", "")
    if not path_str:
        return ValidatorResult(False, "GOOGLE_CREDENTIALS_PATH is empty")

    path = Path(path_str)
    if not path.is_file():
        return ValidatorResult(False, f"File not found: {path}")

    try:
        data = json.loads(path.read_text())
        if "client_id" not in data.get("installed", data.get("web", {})):
            return ValidatorResult(
                False, "JSON file missing 'client_id' — is this an OAuth credentials file?"
            )
        return ValidatorResult(True, f"Credentials file found at {path}")
    except json.JSONDecodeError:
        return ValidatorResult(False, f"File is not valid JSON: {path}")


async def validate_calendar_ids(env: dict[str, str]) -> ValidatorResult:
    """Basic format check on calendar IDs (actual API check needs OAuth flow)."""
    personal = env.get("GOOGLE_CALENDAR_PERSONAL_ID", "")
    if not personal:
        return ValidatorResult(False, "Personal calendar ID is required")
    return ValidatorResult(True, "Calendar IDs set (full validation requires OAuth flow)")


async def validate_supabase(env: dict[str, str]) -> ValidatorResult:
    """Test Supabase connectivity with the anon key."""
    url = env.get("SUPABASE_URL", "")
    anon_key = env.get("SUPABASE_ANON_KEY", "")

    if not url or not anon_key:
        return ValidatorResult(False, "URL and anon key are required")

    if not url.startswith("https://"):
        return ValidatorResult(False, f"SUPABASE_URL must start with https://, got: {url!r}")

    try:
        status, body = await _http_get(
            f"{url.rstrip('/')}/rest/v1/",
            headers={
                "apikey": anon_key,
                "Authorization": f"Bearer {anon_key}",
            },
        )
        if status == 200:
            return ValidatorResult(True, "Supabase connection successful")
        if status == 401:
            return ValidatorResult(False, "Invalid anon key (401)")
        return ValidatorResult(False, f"Supabase returned {status}: {body[:200]}")
    except Exception as exc:
        return ValidatorResult(False, f"Connection error: {exc}")


async def validate_nvidia_gpu(env: dict[str, str]) -> ValidatorResult:
    """Check nvidia-smi can see the specified GPU."""
    gpu_id = env.get("DONNA_OLLAMA_GPU_ID", "")
    if not gpu_id:
        return ValidatorResult(False, "DONNA_OLLAMA_GPU_ID is empty")

    if not shutil.which("nvidia-smi"):
        return ValidatorResult(False, "nvidia-smi not found — NVIDIA drivers not installed?")

    try:
        proc = await asyncio.create_subprocess_exec(
            "nvidia-smi",
            "--query-gpu=index,name,memory.total",
            "--format=csv,noheader",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return ValidatorResult(False, f"nvidia-smi failed: {stderr.decode()[:200]}")

        lines = stdout.decode().strip().splitlines()
        gpu_indices = [line.split(",")[0].strip() for line in lines]
        if gpu_id not in gpu_indices:
            available = ", ".join(gpu_indices)
            return ValidatorResult(
                False, f"GPU {gpu_id} not found. Available: {available}"
            )

        gpu_line = next(line for line in lines if line.split(",")[0].strip() == gpu_id)
        return ValidatorResult(True, f"GPU {gpu_id}: {gpu_line.strip()}")
    except Exception as exc:
        return ValidatorResult(False, f"Error checking GPU: {exc}")


async def validate_docker(env: dict[str, str]) -> ValidatorResult:
    """Check Docker is installed and running."""
    if not shutil.which("docker"):
        return ValidatorResult(False, "Docker not found in PATH")
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "info",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            return ValidatorResult(False, f"Docker not running: {stderr.decode()[:200]}")
        return ValidatorResult(True, "Docker is running")
    except Exception as exc:
        return ValidatorResult(False, f"Error checking Docker: {exc}")


# ---------------------------------------------------------------------------
# Validator registry (name → function)
# ---------------------------------------------------------------------------

VALIDATORS: dict[str, type[object] | object] = {
    "validate_anthropic": validate_anthropic,
    "validate_discord_token": validate_discord_token,
    "validate_discord_guild": validate_discord_guild,
    "validate_discord_channels": validate_discord_channels,
    "validate_paths": validate_paths,
    "validate_budget": validate_budget,
    "validate_grafana_password": validate_grafana_password,
    "validate_twilio": validate_twilio,
    "validate_google_creds_file": validate_google_creds_file,
    "validate_calendar_ids": validate_calendar_ids,
    "validate_supabase": validate_supabase,
    "validate_nvidia_gpu": validate_nvidia_gpu,
    "validate_docker": validate_docker,
}
