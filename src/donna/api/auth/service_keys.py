"""Per-caller service keys for the internal LLM gateway.

Keys are argon2-hashed at rest. Validation requires both:
  1. Source IP in DONNA_INTERNAL_CIDRS (internal-only routing)
  2. X-Forwarded-Host absent (not proxied via Caddy)
"""

from __future__ import annotations

import ipaddress
import secrets
from dataclasses import dataclass
from typing import Any

import aiosqlite
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_ph = PasswordHasher()


@dataclass(frozen=True)
class ServiceCaller:
    caller_id: str
    monthly_budget_usd: float


def _ip_in_any(
    ip: str, cidrs: list[ipaddress.IPv4Network | ipaddress.IPv6Network]
) -> bool:
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(parsed in cidr for cidr in cidrs)


async def seed_or_rotate(
    conn: aiosqlite.Connection,
    *,
    caller_id: str,
    monthly_budget_usd: float,
) -> str:
    """Create or rotate a caller key. Returns the raw key (ONE TIME ONLY).

    If caller_id already exists, its key_hash and monthly_budget_usd are
    replaced; `enabled` is forced to 1 and `revoked_at` cleared.
    """
    raw = secrets.token_urlsafe(32)
    key_hash = _ph.hash(raw)
    await conn.execute(
        """INSERT INTO llm_gateway_callers
               (caller_id, key_hash, monthly_budget_usd, enabled)
           VALUES (?, ?, ?, 1)
           ON CONFLICT(caller_id) DO UPDATE SET
               key_hash=excluded.key_hash,
               monthly_budget_usd=excluded.monthly_budget_usd,
               enabled=1,
               revoked_at=NULL,
               revoke_reason=NULL""",
        (caller_id, key_hash, monthly_budget_usd),
    )
    await conn.commit()
    return raw


async def validate(
    conn: aiosqlite.Connection,
    *,
    presented_key: str,
    source_ip: str,
    internal_cidrs: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
    forwarded_host: str | None,
) -> dict[str, Any] | None:
    """Return caller row if key is valid, source is internal, and not proxied."""
    if not _ip_in_any(source_ip, internal_cidrs):
        return None
    if forwarded_host:
        return None
    cursor = await conn.execute(
        """SELECT id, caller_id, key_hash, monthly_budget_usd
           FROM llm_gateway_callers
           WHERE enabled=1 AND revoked_at IS NULL"""
    )
    rows = await cursor.fetchall()
    await cursor.close()
    for row in rows:
        row_dict = dict(row)
        try:
            if _ph.verify(row_dict["key_hash"], presented_key):
                return row_dict
        except VerifyMismatchError:
            continue
    return None
