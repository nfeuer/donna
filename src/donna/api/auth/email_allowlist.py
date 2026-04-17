"""Email allowlist: only people with Immich accounts can request access."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime

import aiohttp
import aiosqlite
import structlog

logger = structlog.get_logger()

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def normalize_email(raw: str) -> str:
    return raw.strip().lower()


def is_valid_email(raw: str) -> bool:
    normalized = normalize_email(raw)
    if len(normalized) > 254:
        return False
    return bool(_EMAIL_RE.match(normalized))


class EmptyAllowlistError(RuntimeError):
    """Immich returned zero users — refuse to wipe the allowlist."""


async def sync(
    conn: aiosqlite.Connection,
    *,
    internal_url: str,
    admin_api_key: str,
) -> int:
    """Replace `allowed_emails` with the current Immich user list.

    Returns the number of users synced. Raises on network error.
    Raises `EmptyAllowlistError` if Immich returns zero users, because
    applying an empty response would lock everyone out until the next
    successful sync.
    """
    headers = {"x-api-key": admin_api_key}
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{internal_url.rstrip('/')}/api/admin/users",
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            resp.raise_for_status()
            users = await resp.json()

    if not users:
        raise EmptyAllowlistError(
            "Immich returned zero users; refusing to wipe allowed_emails"
        )

    now_iso = datetime.utcnow().isoformat()
    await conn.execute("BEGIN")
    try:
        await conn.execute("DELETE FROM allowed_emails")
        for u in users:
            await conn.execute(
                """INSERT OR REPLACE INTO allowed_emails
                       (email, immich_user_id, name, is_admin, synced_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    normalize_email(u["email"]),
                    u["id"],
                    u.get("name"),
                    1 if u.get("isAdmin") else 0,
                    now_iso,
                ),
            )
    except Exception:
        await conn.rollback()
        raise
    await conn.commit()
    logger.info("email_allowlist_synced", count=len(users))
    return len(users)


async def is_allowed(conn: aiosqlite.Connection, email: str) -> bool:
    cursor = await conn.execute(
        "SELECT 1 FROM allowed_emails WHERE email=?",
        (normalize_email(email),),
    )
    row = await cursor.fetchone()
    await cursor.close()
    return row is not None


async def sync_loop(
    conn: aiosqlite.Connection,
    *,
    internal_url: str,
    admin_api_key: str,
    interval_seconds: int,
) -> None:
    """Background task: sync every interval. Tolerates transient errors."""
    while True:
        try:
            await sync(conn, internal_url=internal_url, admin_api_key=admin_api_key)
        except Exception as exc:
            logger.error("email_allowlist_sync_failed", error=str(exc))
        await asyncio.sleep(interval_seconds)
