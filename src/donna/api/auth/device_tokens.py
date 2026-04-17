"""Device tokens: long-lived auth for mobile apps and desktop browsers.

Tokens are hashed with argon2id at rest. The raw token is returned only
from `issue()` and never retrievable afterwards. Validation uses
constant-time comparison via argon2's verify().

Sliding window: every successful validate() bumps expires_at by
`sliding_window_days`, capped at `absolute_max_days` from created_at.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any

import aiosqlite
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_ph = PasswordHasher()


def _hash(raw: str) -> str:
    return _ph.hash(raw)


def _verify(hashed: str, raw: str) -> bool:
    try:
        return _ph.verify(hashed, raw)
    except VerifyMismatchError:
        return False


def _lookup(raw: str) -> str:
    """Fast indexable lookup digest for a raw token.

    sha256 is used only for equality lookup; the authoritative credential
    check remains argon2id. Raw tokens are 256-bit random so preimage
    search is infeasible — this doesn't weaken the at-rest protection.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def issue(
    conn: aiosqlite.Connection,
    *,
    user_id: str,
    label: str | None,
    user_agent: str | None,
    ip: str,
    sliding_window_days: int,
    absolute_max_days: int,
) -> str:
    """Create a new device token row. Returns the raw token (ONE TIME ONLY)."""
    raw = secrets.token_urlsafe(32)
    now = datetime.utcnow()
    expires_at = (now + timedelta(days=sliding_window_days)).isoformat()
    await conn.execute(
        """INSERT INTO device_tokens
               (token_hash, token_lookup, user_id, label, user_agent,
                last_seen, last_seen_ip, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            _hash(raw),
            _lookup(raw),
            user_id,
            label,
            user_agent,
            now.isoformat(),
            ip,
            expires_at,
        ),
    )
    await conn.commit()
    return raw


async def validate(
    conn: aiosqlite.Connection,
    *,
    token: str,
    ip: str,
    sliding_window_days: int,
    absolute_max_days: int,
) -> dict[str, Any] | None:
    """Return the device row if the token is valid. Refresh sliding window."""
    now = datetime.utcnow()
    cursor = await conn.execute(
        """SELECT id, token_hash, user_id, created_at, expires_at, revoked_at
           FROM device_tokens
           WHERE token_lookup=? AND revoked_at IS NULL AND expires_at > ?""",
        (_lookup(token), now.isoformat()),
    )
    row = await cursor.fetchone()
    await cursor.close()
    if row is None:
        return None

    row_dict = dict(row)
    if not _verify(row_dict["token_hash"], token):
        return None

    new_expires = now + timedelta(days=sliding_window_days)
    created = datetime.fromisoformat(row_dict["created_at"])
    absolute_cap = created + timedelta(days=absolute_max_days)
    if new_expires > absolute_cap:
        new_expires = absolute_cap
    await conn.execute(
        """UPDATE device_tokens
           SET last_seen=?, last_seen_ip=?, expires_at=?
           WHERE id=?""",
        (now.isoformat(), ip, new_expires.isoformat(), row_dict["id"]),
    )
    await conn.commit()
    return row_dict


async def revoke(
    conn: aiosqlite.Connection, *, device_id: int, revoked_by: str
) -> None:
    await conn.execute(
        "UPDATE device_tokens SET revoked_at=?, revoked_by=? WHERE id=?",
        (datetime.utcnow().isoformat(), revoked_by, device_id),
    )
    await conn.commit()


async def list_for_user(
    conn: aiosqlite.Connection, *, user_id: str
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """SELECT id, label, user_agent, created_at, last_seen, last_seen_ip,
                  expires_at, revoked_at
           FROM device_tokens
           WHERE user_id=?
           ORDER BY created_at DESC""",
        (user_id,),
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return [dict(r) for r in rows]
