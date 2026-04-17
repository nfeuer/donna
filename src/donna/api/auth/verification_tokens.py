"""Magic-link verification tokens.

Tokens are 32 random bytes (urlsafe base64). Only the sha256 is stored.
Tokens are bound to the requesting IP and single-use.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any

import aiosqlite


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def create(
    conn: aiosqlite.Connection,
    *,
    ip: str,
    email: str,
    expiry_minutes: int = 15,
    trust_duration: str = "30d",
) -> str:
    """Generate, store, and return a raw opaque token."""
    raw = secrets.token_urlsafe(32)
    expires_at = (datetime.utcnow() + timedelta(minutes=expiry_minutes)).isoformat()
    await conn.execute(
        """INSERT INTO verification_tokens
               (token_hash, ip_address, email, expires_at, trust_duration)
           VALUES (?, ?, ?, ?, ?)""",
        (_hash(raw), ip, email, expires_at, trust_duration),
    )
    await conn.commit()
    return raw


async def validate(
    conn: aiosqlite.Connection,
    *,
    token: str,
    ip: str,
) -> dict[str, Any] | None:
    """Return the token row dict if valid, else None.

    Valid means: exists, not used, not expired, and IP matches issuance IP.
    """
    now_iso = datetime.utcnow().isoformat()
    cursor = await conn.execute(
        """SELECT * FROM verification_tokens
           WHERE token_hash=? AND used=0 AND expires_at > ?""",
        (_hash(token), now_iso),
    )
    row = await cursor.fetchone()
    await cursor.close()
    if row is None:
        return None
    record = dict(row)
    if record["ip_address"] != ip:
        return None
    return record


async def mark_used(conn: aiosqlite.Connection, *, token: str) -> None:
    await conn.execute(
        "UPDATE verification_tokens SET used=1 WHERE token_hash=?",
        (_hash(token),),
    )
    await conn.commit()
