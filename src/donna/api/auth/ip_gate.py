"""Async IP gate — per-source-IP allowlist with email verification.

Port of immich-manager/shared/auth/ip_gate.py for aiosqlite. Same
schema, same return contracts.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import aiosqlite
import structlog

logger = structlog.get_logger()

_DURATION_DELTAS: dict[str, timedelta] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
}


async def insert_pending_ip(
    conn: aiosqlite.Connection, ip_address: str, source: str = "web"
) -> None:
    """Insert an IP as pending. Silently ignores duplicates."""
    await conn.execute(
        "INSERT OR IGNORE INTO trusted_ips (ip_address, status, source) "
        "VALUES (?, 'pending', ?)",
        (ip_address, source),
    )
    await conn.commit()


async def trust_ip(
    conn: aiosqlite.Connection,
    ip_address: str,
    *,
    access_level: str,
    trust_duration: str,
    verified_by: str,
) -> None:
    """Mark an IP as trusted with the given duration and access level."""
    now = datetime.utcnow()
    delta = _DURATION_DELTAS.get(trust_duration)
    expires_at = (now + delta).isoformat() if delta else None
    await conn.execute(
        """UPDATE trusted_ips
           SET status='trusted',
               access_level=?,
               trust_duration=?,
               trusted_at=?,
               expires_at=?,
               verified_by=?,
               last_seen=?,
               revoked_at=NULL, revoked_by=NULL, revoke_reason=NULL
           WHERE ip_address=?""",
        (
            access_level,
            trust_duration,
            now.isoformat(),
            expires_at,
            verified_by,
            now.isoformat(),
            ip_address,
        ),
    )
    await conn.commit()


async def revoke_ip(
    conn: aiosqlite.Connection,
    ip_address: str,
    *,
    revoked_by: str,
    reason: str | None = None,
) -> None:
    now = datetime.utcnow().isoformat()
    await conn.execute(
        """UPDATE trusted_ips
           SET status='revoked', revoked_at=?, revoked_by=?, revoke_reason=?
           WHERE ip_address=?""",
        (now, revoked_by, reason, ip_address),
    )
    await conn.commit()


async def update_last_seen(conn: aiosqlite.Connection, ip_address: str) -> None:
    await conn.execute(
        "UPDATE trusted_ips SET last_seen=? WHERE ip_address=?",
        (datetime.utcnow().isoformat(), ip_address),
    )
    await conn.commit()


async def get_trusted_ip(
    conn: aiosqlite.Connection, ip_address: str
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        "SELECT * FROM trusted_ips WHERE ip_address=?", (ip_address,)
    )
    row = await cursor.fetchone()
    await cursor.close()
    return dict(row) if row else None


async def record_ip_connection(
    conn: aiosqlite.Connection,
    ip_address: str,
    *,
    service: str,
    action: str,
    user_id: str | None = None,
) -> None:
    await conn.execute(
        "INSERT INTO ip_connections (ip_address, service, action, user_id) "
        "VALUES (?, ?, ?, ?)",
        (ip_address, service, action, user_id),
    )
    await conn.commit()


async def check_ip_access(
    conn: aiosqlite.Connection,
    ip_address: str,
    *,
    service: str = "donna",
) -> dict[str, Any]:
    """Core check. Returns {action, reason, ip_record}.

    action ∈ {"allow", "challenge", "block"}
    """
    row = await get_trusted_ip(conn, ip_address)
    if row is None:
        return {"action": "challenge", "reason": "unknown_ip", "ip_record": None}

    status = row["status"]
    if status == "revoked":
        return {"action": "block", "reason": "revoked", "ip_record": row}
    if status == "pending":
        return {"action": "challenge", "reason": "pending_verification", "ip_record": row}

    if status == "trusted":
        if row["expires_at"]:
            try:
                expires = datetime.fromisoformat(row["expires_at"])
            except ValueError:
                logger.warning("ip_gate_bad_expires_at", ip=ip_address)
                return {"action": "challenge", "reason": "bad_expires_at", "ip_record": row}
            if datetime.utcnow() > expires:
                return {"action": "challenge", "reason": "expired", "ip_record": row}

        if service == "admin" and row["access_level"] != "admin":
            return {
                "action": "block",
                "reason": "insufficient_access_level",
                "ip_record": row,
            }

        await update_last_seen(conn, ip_address)
        return {"action": "allow", "reason": "trusted", "ip_record": row}

    return {"action": "challenge", "reason": "unknown_status", "ip_record": row}
