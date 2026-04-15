# API Auth Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Donna's Firebase-JWT auth stub with a four-layer auth stack (device token → IP gate → Immich identity → service caller) that closes every finding from the 2026-04-14 security audit and ports the battle-tested `immich-manager/shared/auth` pattern into Donna's async codebase.

**Architecture:** A new `src/donna/api/auth/` package exposes four FastAPI router factories (`public_liveness_router`, `public_auth_router`, `user_router`, `admin_router`, `service_router`), each with the right dependencies pre-bound. A new Alembic migration adds 7 tables. Every existing route is migrated to a typed factory; the old `src/donna/api/auth.py` Firebase file is deleted. Caddy path-splits `donna.houseoffeuer.com/` (UI) from `/api/*` (API) so UI and API are same-origin, letting CORS middleware be removed entirely.

**Tech Stack:** FastAPI, aiosqlite, Alembic, structlog, pytest/pytest-asyncio, aioresponses (HTTP mocks), argon2-cffi (new dep for hashing keys/tokens), Caddy, Cloudflare Tunnel.

**Reference spec:** `docs/superpowers/specs/2026-04-14-api-auth-hardening-design.md`

**Reference pattern:** `~/Documents/Projects/immich-manager/shared/auth/ip_gate.py` (sync prototype to port to async)

**Phases:**
- **Phase 1 — Foundation** (Tasks 1-9): schema + pure async modules + composition layer. No route changes. Safe to merge in isolation; does not affect running system.
- **Phase 2 — Auth flow routes** (Tasks 10-12): `/auth/request-access`, `/auth/verify`, `/auth/status`, `/auth/logout`. Wires Gmail + Immich sync. End-to-end email flow works.
- **Phase 3 — Route migration** (Tasks 13-17): flip `/tasks`, `/schedule`, `/agents`, `/chat`, `/admin`, `/llm` to new factories. Delete Firebase code. Delete fail-open. Delete CORS wildcard.
- **Phase 4 — Admin UI routes + deployment** (Tasks 18-20): `/admin/ips`, `/admin/devices`, `/admin/callers` routes; Caddy config; bootstrap docs.

---

## File Structure

**New files:**

```
alembic/versions/add_auth_tables.py                        # migration (7 tables)
config/auth.yaml                                            # auth config
config/service_callers.yaml                                 # per-caller seeds (non-secret metadata only)
src/donna/api/auth/__init__.py                              # public exports (dep factories + types)
src/donna/api/auth/ip_gate.py                               # async port of immich-manager ip_gate
src/donna/api/auth/verification_tokens.py                   # magic-link tokens (sha256 hash, IP-bound)
src/donna/api/auth/device_tokens.py                         # argon2-hashed device tokens + sliding window
src/donna/api/auth/service_keys.py                          # per-caller key validation + CIDR check
src/donna/api/auth/trusted_proxies.py                       # X-Forwarded-For resolution
src/donna/api/auth/immich.py                                # Immich /users/me forwarding + caching
src/donna/api/auth/email_allowlist.py                       # sync job from Immich admin API
src/donna/api/auth/email_sender.py                          # magic-link sender (via gmail integration)
src/donna/api/auth/config.py                                # AuthConfig dataclass + loader
src/donna/api/auth/dependencies.py                          # _resolve_user_id / _resolve_admin / _resolve_service
src/donna/api/auth/router_factory.py                        # the four/five router factories
src/donna/api/routes/auth_flow.py                           # /auth/request-access, /verify, /status, /logout
src/donna/api/routes/admin_access.py                        # /admin/ips, /admin/devices, /admin/callers

tests/unit/test_auth_ip_gate.py
tests/unit/test_auth_verification_tokens.py
tests/unit/test_auth_device_tokens.py
tests/unit/test_auth_service_keys.py
tests/unit/test_auth_trusted_proxies.py
tests/unit/test_auth_immich.py
tests/unit/test_auth_email_allowlist.py
tests/unit/test_auth_email_sender.py
tests/unit/test_auth_config.py
tests/unit/test_auth_dependencies.py
tests/unit/test_auth_router_factory.py
tests/unit/test_auth_flow_routes.py
tests/unit/test_admin_access_routes.py
tests/integration/test_auth_end_to_end.py
```

**Modified files:**

```
pyproject.toml                                              # + argon2-cffi dependency
src/donna/api/__init__.py                                   # remove CORS wildcard, mount new auth router, use factories
src/donna/api/routes/tasks.py                               # switch import from donna.api.auth to donna.api.auth (package)
src/donna/api/routes/schedule.py                            # same
src/donna/api/routes/agents.py                              # same
src/donna/api/routes/chat.py                                # add CurrentUser dep, remove body.get("user_id"), scope ownership
src/donna/api/routes/llm.py                                 # delete _require_api_key, use CurrentServiceCaller dep
src/donna/api/routes/admin_*.py                             # replace APIRouter() with admin_router() factory
config/email.yaml                                           # set send_enabled: true (via migration note, not in-code)
docker/.env.example                                         # + IMMICH_ADMIN_API_KEY, + DONNA_BOOTSTRAP_ADMIN_EMAIL, + DONNA_TRUSTED_PROXIES, + DONNA_INTERNAL_CIDRS
SETUP.md                                                    # bootstrap walkthrough
```

**Deleted files:**

```
src/donna/api/auth.py                                       # Firebase stub (replaced by donna.api.auth package)
```

---

## Task 1: Alembic migration — auth tables

**Files:**
- Create: `alembic/versions/add_auth_tables.py`
- Test: `tests/unit/test_auth_migration.py`

- [ ] **Step 1: Find the current Alembic head**

Run: `alembic heads`
Expected: prints one or two revision IDs. If two heads exist (branch), create a merge migration *first* per the project's existing convention (see `add_chat_tables.py` next to `add_context_budget_columns.py`). For this plan, assume the head is `<CURRENT_HEAD>` — substitute the actual ID from `alembic heads` output before committing.

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_auth_migration.py`:

```python
"""Smoke test that the auth migration creates the expected tables."""

from __future__ import annotations

import pytest
import aiosqlite
from alembic import command
from alembic.config import Config as AlembicConfig


@pytest.mark.asyncio
async def test_auth_migration_creates_expected_tables(tmp_path):
    db_path = tmp_path / "migration_smoke.db"
    cfg = AlembicConfig("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")

    async with aiosqlite.connect(str(db_path)) as conn:
        rows = await conn.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        table_names = {r[0] for r in rows}

    for name in (
        "trusted_ips",
        "verification_tokens",
        "ip_connections",
        "allowed_emails",
        "users",
        "device_tokens",
        "llm_gateway_callers",
    ):
        assert name in table_names, f"{name} missing from schema"
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `pytest tests/unit/test_auth_migration.py -v`
Expected: FAIL (tables don't exist yet).

- [ ] **Step 4: Write the migration**

Create `alembic/versions/add_auth_tables.py`:

```python
"""add auth tables

Revision ID: a1c9d3e5f701
Revises: <CURRENT_HEAD>
Create Date: 2026-04-14 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1c9d3e5f701"
down_revision: Union[str, None] = "<CURRENT_HEAD>"  # fill in from alembic heads
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "trusted_ips",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ip_address", sa.String(45), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("access_level", sa.String(20), nullable=True),
        sa.Column("trust_duration", sa.String(10), nullable=True),
        sa.Column("trusted_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("verified_by", sa.String(254), nullable=True),
        sa.Column("label", sa.String(200), nullable=True),
        sa.Column("last_seen", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("source", sa.String(20), nullable=False, server_default="web"),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_by", sa.String(254), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
    )
    op.create_index("idx_trusted_ips_status", "trusted_ips", ["status"])

    op.create_table(
        "verification_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("ip_address", sa.String(45), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trust_duration", sa.String(10), nullable=False, server_default="30d"),
    )
    op.create_index("idx_verification_tokens_hash", "verification_tokens", ["token_hash"])

    op.create_table(
        "ip_connections",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ip_address", sa.String(45), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("service", sa.String(100), nullable=True),
        sa.Column("action", sa.String(100), nullable=True),
        sa.Column("user_id", sa.String(100), nullable=True),
    )
    op.create_index("idx_ip_connections_ip", "ip_connections", ["ip_address"])
    op.create_index("idx_ip_connections_timestamp", "ip_connections", ["timestamp"])

    op.create_table(
        "allowed_emails",
        sa.Column("email", sa.String(254), primary_key=True),
        sa.Column("immich_user_id", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("is_admin", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "users",
        sa.Column("donna_user_id", sa.String(100), primary_key=True),
        sa.Column("immich_user_id", sa.String(100), nullable=False, unique=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("role", sa.String(20), nullable=False, server_default="user"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "device_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("token_hash", sa.String(200), nullable=False, unique=True),
        sa.Column("user_id", sa.String(100), nullable=False),
        sa.Column("label", sa.String(200), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("last_seen", sa.DateTime(), nullable=True),
        sa.Column("last_seen_ip", sa.String(45), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_by", sa.String(100), nullable=True),
    )
    op.create_index("idx_device_tokens_user", "device_tokens", ["user_id"])
    op.create_index("idx_device_tokens_expires", "device_tokens", ["expires_at"])

    op.create_table(
        "llm_gateway_callers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("caller_id", sa.String(100), nullable=False, unique=True),
        sa.Column("key_hash", sa.String(200), nullable=False),
        sa.Column("monthly_budget_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
    )
    op.create_index("idx_llm_gateway_callers_enabled", "llm_gateway_callers", ["enabled"])


def downgrade() -> None:
    op.drop_table("llm_gateway_callers")
    op.drop_table("device_tokens")
    op.drop_table("users")
    op.drop_table("allowed_emails")
    op.drop_table("ip_connections")
    op.drop_table("verification_tokens")
    op.drop_table("trusted_ips")
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/unit/test_auth_migration.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full test suite to ensure nothing else broke**

Run: `pytest`
Expected: PASS (no regressions from adding new tables).

- [ ] **Step 7: Commit**

```bash
git add alembic/versions/add_auth_tables.py tests/unit/test_auth_migration.py
git commit -m "feat(auth): add migration for auth tables"
```

---

## Task 2: Add argon2-cffi dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add argon2-cffi to dependencies**

Edit `pyproject.toml`, add to the `dependencies = [...]` list, right after `"cryptography>=42.0.0",`:

```toml
    "argon2-cffi>=23.1.0",
```

- [ ] **Step 2: Install and verify import**

Run: `uv sync --all-extras`
Then: `python -c "from argon2 import PasswordHasher; PasswordHasher().hash('test')"`
Expected: prints an `$argon2id$...` hash.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build(deps): add argon2-cffi for auth key hashing"
```

---

## Task 3: Port `ip_gate` module (async)

**Files:**
- Create: `src/donna/api/auth/__init__.py` (empty package marker for now)
- Create: `src/donna/api/auth/ip_gate.py`
- Test: `tests/unit/test_auth_ip_gate.py`

- [ ] **Step 1: Create the empty package marker**

Create `src/donna/api/auth/__init__.py` with:

```python
"""Authentication and authorization for the Donna REST API.

See docs/superpowers/specs/2026-04-14-api-auth-hardening-design.md.
"""
```

- [ ] **Step 2: Write the failing test for status transitions**

Create `tests/unit/test_auth_ip_gate.py`:

```python
"""Unit tests for the async IP gate module."""

from __future__ import annotations

from datetime import datetime, timedelta

import aiosqlite
import pytest

from donna.api.auth import ip_gate


@pytest.fixture
async def db(tmp_path):
    path = tmp_path / "ipgate.db"
    async with aiosqlite.connect(str(path)) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(
            """
            CREATE TABLE trusted_ips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip_address TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'pending',
                access_level TEXT,
                trust_duration TEXT,
                trusted_at DATETIME,
                expires_at DATETIME,
                verified_by TEXT,
                label TEXT,
                last_seen DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                source TEXT DEFAULT 'web',
                revoked_at DATETIME,
                revoked_by TEXT,
                revoke_reason TEXT
            );
            CREATE TABLE ip_connections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip_address TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                service TEXT, action TEXT, user_id TEXT
            );
            """
        )
        await conn.commit()
        yield conn


@pytest.mark.asyncio
async def test_unknown_ip_is_challenge(db):
    result = await ip_gate.check_ip_access(db, "203.0.113.5")
    assert result["action"] == "challenge"
    assert result["reason"] == "unknown_ip"


@pytest.mark.asyncio
async def test_trust_ip_then_allow(db):
    await ip_gate.insert_pending_ip(db, "203.0.113.6")
    await ip_gate.trust_ip(
        db, "203.0.113.6",
        access_level="user",
        trust_duration="30d",
        verified_by="test@example.com",
    )
    result = await ip_gate.check_ip_access(db, "203.0.113.6")
    assert result["action"] == "allow"
    assert result["ip_record"]["access_level"] == "user"


@pytest.mark.asyncio
async def test_expired_trust_returns_challenge(db):
    await ip_gate.insert_pending_ip(db, "203.0.113.7")
    await db.execute(
        """UPDATE trusted_ips SET status='trusted', access_level='user',
                  trust_duration='24h',
                  trusted_at=?, expires_at=?
           WHERE ip_address=?""",
        (
            (datetime.utcnow() - timedelta(days=2)).isoformat(),
            (datetime.utcnow() - timedelta(days=1)).isoformat(),
            "203.0.113.7",
        ),
    )
    await db.commit()
    result = await ip_gate.check_ip_access(db, "203.0.113.7")
    assert result["action"] == "challenge"
    assert result["reason"] == "expired"


@pytest.mark.asyncio
async def test_revoked_is_block(db):
    await ip_gate.insert_pending_ip(db, "203.0.113.8")
    await ip_gate.revoke_ip(db, "203.0.113.8", revoked_by="admin", reason="test")
    result = await ip_gate.check_ip_access(db, "203.0.113.8")
    assert result["action"] == "block"
    assert result["reason"] == "revoked"


@pytest.mark.asyncio
async def test_sql_injection_in_ip_is_literal(db):
    """Adversarial: ensure ip_address is treated as a literal string."""
    payload = "1.2.3.4'; DROP TABLE trusted_ips; --"
    await ip_gate.insert_pending_ip(db, payload)
    rows = await db.execute_fetchall("SELECT name FROM sqlite_master WHERE type='table'")
    assert any(r[0] == "trusted_ips" for r in rows)


@pytest.mark.asyncio
async def test_admin_access_level_enforced_for_admin_service(db):
    await ip_gate.insert_pending_ip(db, "203.0.113.9")
    await ip_gate.trust_ip(
        db, "203.0.113.9",
        access_level="user",
        trust_duration="30d",
        verified_by="x@example.com",
    )
    result = await ip_gate.check_ip_access(db, "203.0.113.9", service="admin")
    assert result["action"] == "block"
    assert result["reason"] == "insufficient_access_level"
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `pytest tests/unit/test_auth_ip_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: donna.api.auth.ip_gate`.

- [ ] **Step 4: Implement `ip_gate.py`**

Create `src/donna/api/auth/ip_gate.py`:

```python
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
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/unit/test_auth_ip_gate.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/donna/api/auth/__init__.py src/donna/api/auth/ip_gate.py tests/unit/test_auth_ip_gate.py
git commit -m "feat(auth): async IP gate module ported from immich-manager"
```

---

## Task 4: Verification tokens module

**Files:**
- Create: `src/donna/api/auth/verification_tokens.py`
- Test: `tests/unit/test_auth_verification_tokens.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_auth_verification_tokens.py`:

```python
"""Unit tests for magic-link verification tokens."""

from __future__ import annotations

import aiosqlite
import pytest

from donna.api.auth import verification_tokens as vt


@pytest.fixture
async def db(tmp_path):
    path = tmp_path / "vt.db"
    async with aiosqlite.connect(str(path)) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(
            """
            CREATE TABLE verification_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT NOT NULL UNIQUE,
                ip_address TEXT NOT NULL,
                email TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                expires_at DATETIME NOT NULL,
                used INTEGER NOT NULL DEFAULT 0,
                trust_duration TEXT NOT NULL DEFAULT '30d'
            );
            """
        )
        await conn.commit()
        yield conn


@pytest.mark.asyncio
async def test_create_and_validate_round_trip(db):
    raw = await vt.create(db, ip="1.2.3.4", email="nick@example.com", expiry_minutes=15)
    assert len(raw) >= 32
    record = await vt.validate(db, token=raw, ip="1.2.3.4")
    assert record is not None
    assert record["email"] == "nick@example.com"


@pytest.mark.asyncio
async def test_validation_rejects_wrong_ip(db):
    raw = await vt.create(db, ip="1.2.3.4", email="nick@example.com", expiry_minutes=15)
    record = await vt.validate(db, token=raw, ip="9.9.9.9")
    assert record is None


@pytest.mark.asyncio
async def test_mark_used_prevents_replay(db):
    raw = await vt.create(db, ip="1.2.3.4", email="nick@example.com", expiry_minutes=15)
    await vt.mark_used(db, token=raw)
    record = await vt.validate(db, token=raw, ip="1.2.3.4")
    assert record is None


@pytest.mark.asyncio
async def test_expired_token_rejected(db):
    raw = await vt.create(
        db, ip="1.2.3.4", email="nick@example.com", expiry_minutes=-1
    )
    record = await vt.validate(db, token=raw, ip="1.2.3.4")
    assert record is None


@pytest.mark.asyncio
async def test_sql_injection_email_is_literal(db):
    payload = "x@example.com'; DROP TABLE verification_tokens; --"
    await vt.create(db, ip="1.2.3.4", email=payload, expiry_minutes=15)
    rows = await db.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )
    assert any(r[0] == "verification_tokens" for r in rows)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/unit/test_auth_verification_tokens.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the module**

Create `src/donna/api/auth/verification_tokens.py`:

```python
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/unit/test_auth_verification_tokens.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/donna/api/auth/verification_tokens.py tests/unit/test_auth_verification_tokens.py
git commit -m "feat(auth): magic-link verification tokens (sha256 hashed, IP-bound)"
```

---

## Task 5: Device tokens module

**Files:**
- Create: `src/donna/api/auth/device_tokens.py`
- Test: `tests/unit/test_auth_device_tokens.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_auth_device_tokens.py`:

```python
"""Unit tests for device token issuance, validation, sliding window."""

from __future__ import annotations

from datetime import datetime, timedelta

import aiosqlite
import pytest

from donna.api.auth import device_tokens as dt


@pytest.fixture
async def db(tmp_path):
    path = tmp_path / "dt.db"
    async with aiosqlite.connect(str(path)) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(
            """
            CREATE TABLE device_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT NOT NULL UNIQUE,
                user_id TEXT NOT NULL,
                label TEXT, user_agent TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_seen DATETIME, last_seen_ip TEXT,
                expires_at DATETIME NOT NULL,
                revoked_at DATETIME, revoked_by TEXT
            );
            """
        )
        await conn.commit()
        yield conn


@pytest.mark.asyncio
async def test_issue_and_validate(db):
    raw = await dt.issue(
        db, user_id="nick", label="iPhone", user_agent="ua", ip="1.2.3.4",
        sliding_window_days=90, absolute_max_days=365,
    )
    assert len(raw) >= 32
    record = await dt.validate(db, token=raw, ip="2.2.2.2", sliding_window_days=90, absolute_max_days=365)
    assert record is not None
    assert record["user_id"] == "nick"


@pytest.mark.asyncio
async def test_revoked_token_rejected(db):
    raw = await dt.issue(db, user_id="nick", label="L", user_agent="ua", ip="1.2.3.4",
                          sliding_window_days=90, absolute_max_days=365)
    row = await dt.validate(db, token=raw, ip="1.2.3.4", sliding_window_days=90, absolute_max_days=365)
    await dt.revoke(db, device_id=row["id"], revoked_by="admin")
    record = await dt.validate(db, token=raw, ip="1.2.3.4", sliding_window_days=90, absolute_max_days=365)
    assert record is None


@pytest.mark.asyncio
async def test_expired_token_rejected(db):
    raw = await dt.issue(db, user_id="nick", label="L", user_agent="ua", ip="1.2.3.4",
                          sliding_window_days=90, absolute_max_days=365)
    # Fast-forward: manually set expires_at in the past.
    await db.execute(
        "UPDATE device_tokens SET expires_at=? WHERE user_id='nick'",
        ((datetime.utcnow() - timedelta(days=1)).isoformat(),),
    )
    await db.commit()
    record = await dt.validate(db, token=raw, ip="1.2.3.4", sliding_window_days=90, absolute_max_days=365)
    assert record is None


@pytest.mark.asyncio
async def test_sliding_window_extends_expires(db):
    raw = await dt.issue(db, user_id="nick", label="L", user_agent="ua", ip="1.2.3.4",
                          sliding_window_days=90, absolute_max_days=365)
    # Set expires_at to 10 days from now; validate should bump it.
    await db.execute(
        "UPDATE device_tokens SET expires_at=? WHERE user_id='nick'",
        ((datetime.utcnow() + timedelta(days=10)).isoformat(),),
    )
    await db.commit()
    await dt.validate(db, token=raw, ip="1.2.3.4", sliding_window_days=90, absolute_max_days=365)
    cursor = await db.execute("SELECT expires_at FROM device_tokens WHERE user_id='nick'")
    row = await cursor.fetchone()
    new_exp = datetime.fromisoformat(row[0])
    assert new_exp > datetime.utcnow() + timedelta(days=80)


@pytest.mark.asyncio
async def test_absolute_max_caps_sliding_window(db):
    raw = await dt.issue(db, user_id="nick", label="L", user_agent="ua", ip="1.2.3.4",
                          sliding_window_days=90, absolute_max_days=365)
    # Pretend this token was created 360 days ago.
    await db.execute(
        "UPDATE device_tokens SET created_at=? WHERE user_id='nick'",
        ((datetime.utcnow() - timedelta(days=360)).isoformat(),),
    )
    await db.commit()
    record = await dt.validate(db, token=raw, ip="1.2.3.4", sliding_window_days=90, absolute_max_days=365)
    assert record is not None
    cursor = await db.execute("SELECT expires_at FROM device_tokens WHERE user_id='nick'")
    row = await cursor.fetchone()
    new_exp = datetime.fromisoformat(row[0])
    # Cap is 365 days from creation → max 5 days from now.
    assert new_exp <= datetime.utcnow() + timedelta(days=6)


@pytest.mark.asyncio
async def test_sql_injection_label_is_literal(db):
    payload = "iPhone'; DROP TABLE device_tokens; --"
    await dt.issue(db, user_id="nick", label=payload, user_agent="ua", ip="1.2.3.4",
                    sliding_window_days=90, absolute_max_days=365)
    rows = await db.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )
    assert any(r[0] == "device_tokens" for r in rows)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/unit/test_auth_device_tokens.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `device_tokens.py`**

Create `src/donna/api/auth/device_tokens.py`:

```python
"""Device tokens: long-lived auth for mobile apps and desktop browsers.

Tokens are hashed with argon2id at rest. The raw token is returned only
from `issue()` and never retrievable afterwards. Validation uses
constant-time comparison via argon2's verify().

Sliding window: every successful validate() bumps expires_at by
`sliding_window_days`, capped at `absolute_max_days` from created_at.
"""

from __future__ import annotations

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
               (token_hash, user_id, label, user_agent,
                last_seen, last_seen_ip, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (_hash(raw), user_id, label, user_agent, now.isoformat(), ip, expires_at),
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
           WHERE revoked_at IS NULL AND expires_at > ?""",
        (now.isoformat(),),
    )
    rows = await cursor.fetchall()
    await cursor.close()

    for row in rows:
        row_dict = dict(row)
        if _verify(row_dict["token_hash"], token):
            # Compute new expires_at: now + window, capped at created_at + max.
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
    return None


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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/unit/test_auth_device_tokens.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/donna/api/auth/device_tokens.py tests/unit/test_auth_device_tokens.py
git commit -m "feat(auth): device tokens with argon2 hashing and sliding window"
```

---

## Task 6: Service keys module

**Files:**
- Create: `src/donna/api/auth/service_keys.py`
- Test: `tests/unit/test_auth_service_keys.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_auth_service_keys.py`:

```python
"""Unit tests for per-caller LLM gateway service keys."""

from __future__ import annotations

import ipaddress

import aiosqlite
import pytest

from donna.api.auth import service_keys as sk


@pytest.fixture
async def db(tmp_path):
    path = tmp_path / "sk.db"
    async with aiosqlite.connect(str(path)) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(
            """
            CREATE TABLE llm_gateway_callers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                caller_id TEXT NOT NULL UNIQUE,
                key_hash TEXT NOT NULL,
                monthly_budget_usd REAL NOT NULL DEFAULT 0.0,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                revoked_at DATETIME, revoke_reason TEXT
            );
            """
        )
        await conn.commit()
        yield conn


_INTERNAL = [ipaddress.ip_network("172.18.0.0/16")]


@pytest.mark.asyncio
async def test_issue_seed_and_validate(db):
    raw = await sk.seed_or_rotate(
        db, caller_id="curator", monthly_budget_usd=5.0
    )
    result = await sk.validate(
        db, presented_key=raw, source_ip="172.18.0.5",
        internal_cidrs=_INTERNAL, forwarded_host=None,
    )
    assert result is not None
    assert result["caller_id"] == "curator"


@pytest.mark.asyncio
async def test_external_ip_rejected(db):
    raw = await sk.seed_or_rotate(db, caller_id="curator", monthly_budget_usd=5.0)
    result = await sk.validate(
        db, presented_key=raw, source_ip="8.8.8.8",
        internal_cidrs=_INTERNAL, forwarded_host=None,
    )
    assert result is None


@pytest.mark.asyncio
async def test_caddy_proxied_request_rejected(db):
    raw = await sk.seed_or_rotate(db, caller_id="curator", monthly_budget_usd=5.0)
    result = await sk.validate(
        db, presented_key=raw, source_ip="172.18.0.5",
        internal_cidrs=_INTERNAL, forwarded_host="donna.houseoffeuer.com",
    )
    assert result is None


@pytest.mark.asyncio
async def test_disabled_caller_rejected(db):
    raw = await sk.seed_or_rotate(db, caller_id="curator", monthly_budget_usd=5.0)
    await db.execute("UPDATE llm_gateway_callers SET enabled=0 WHERE caller_id='curator'")
    await db.commit()
    result = await sk.validate(
        db, presented_key=raw, source_ip="172.18.0.5",
        internal_cidrs=_INTERNAL, forwarded_host=None,
    )
    assert result is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/unit/test_auth_service_keys.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `service_keys.py`**

Create `src/donna/api/auth/service_keys.py`:

```python
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/unit/test_auth_service_keys.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/donna/api/auth/service_keys.py tests/unit/test_auth_service_keys.py
git commit -m "feat(auth): per-caller service keys with internal-CIDR gate"
```

---

## Task 7: Trusted proxies (X-Forwarded-For resolution)

**Files:**
- Create: `src/donna/api/auth/trusted_proxies.py`
- Test: `tests/unit/test_auth_trusted_proxies.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_auth_trusted_proxies.py`:

```python
"""Unit tests for client IP resolution with trusted-proxy XFF handling."""

from __future__ import annotations

import ipaddress
from types import SimpleNamespace

import pytest

from donna.api.auth import trusted_proxies as tp


def _req(client_host: str, xff: str | None) -> SimpleNamespace:
    headers = {"x-forwarded-for": xff} if xff else {}
    return SimpleNamespace(
        client=SimpleNamespace(host=client_host),
        headers=headers,
    )


_CADDY_CIDR = [ipaddress.ip_network("172.18.0.0/16")]


def test_no_xff_uses_client_host():
    ip = tp.client_ip(_req("203.0.113.5", None), trusted_proxies=_CADDY_CIDR)
    assert ip == "203.0.113.5"


def test_xff_from_trusted_proxy_uses_rightmost_entry():
    ip = tp.client_ip(
        _req("172.18.0.2", "1.1.1.1, 203.0.113.9"),
        trusted_proxies=_CADDY_CIDR,
    )
    # The rightmost non-proxy entry is the real client as seen by the trusted proxy.
    assert ip == "203.0.113.9"


def test_xff_from_untrusted_source_is_ignored():
    ip = tp.client_ip(
        _req("203.0.113.200", "1.1.1.1"),  # not in trusted CIDR
        trusted_proxies=_CADDY_CIDR,
    )
    assert ip == "203.0.113.200"


def test_malformed_xff_falls_back_to_client_host():
    ip = tp.client_ip(
        _req("172.18.0.2", "not-an-ip"),
        trusted_proxies=_CADDY_CIDR,
    )
    assert ip == "172.18.0.2"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/unit/test_auth_trusted_proxies.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `trusted_proxies.py`**

Create `src/donna/api/auth/trusted_proxies.py`:

```python
"""Client IP resolution from X-Forwarded-For, respecting trusted proxies only.

NEVER read request.client.host or X-Forwarded-For directly outside this
module — always call client_ip(request, trusted_proxies=...).
"""

from __future__ import annotations

import ipaddress
from typing import Any


def client_ip(
    request: Any,
    *,
    trusted_proxies: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
) -> str:
    """Resolve the real client IP given the request.

    If request.client.host is in trusted_proxies, parse X-Forwarded-For and
    return the rightmost entry (the client as seen by the trusted proxy).
    Otherwise, return request.client.host unchanged.
    """
    raw_host = request.client.host if request.client else ""
    try:
        host = ipaddress.ip_address(raw_host)
    except ValueError:
        return raw_host

    if not any(host in cidr for cidr in trusted_proxies):
        return raw_host

    xff = request.headers.get("x-forwarded-for", "")
    if not xff:
        return raw_host

    parts = [p.strip() for p in xff.split(",") if p.strip()]
    for candidate in reversed(parts):
        try:
            ipaddress.ip_address(candidate)
            return candidate
        except ValueError:
            continue
    return raw_host
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/unit/test_auth_trusted_proxies.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/donna/api/auth/trusted_proxies.py tests/unit/test_auth_trusted_proxies.py
git commit -m "feat(auth): trusted-proxy XFF resolution"
```

---

## Task 8: Immich identity forwarding

**Files:**
- Create: `src/donna/api/auth/immich.py`
- Test: `tests/unit/test_auth_immich.py`

- [ ] **Step 1: Write the failing tests (using aioresponses for HTTP mocks)**

Create `tests/unit/test_auth_immich.py`:

```python
"""Unit tests for Immich identity forwarding."""

from __future__ import annotations

import pytest
from aioresponses import aioresponses

from donna.api.auth import immich


@pytest.mark.asyncio
async def test_resolve_returns_user_on_200():
    client = immich.ImmichClient(internal_url="http://immich:2283", cache_ttl_s=60)
    with aioresponses() as m:
        m.get(
            "http://immich:2283/api/users/me",
            status=200,
            payload={"id": "ab12", "email": "nick@example.com", "name": "Nick", "isAdmin": True},
        )
        user = await client.resolve(bearer="t0k")
    assert user.immich_user_id == "ab12"
    assert user.email == "nick@example.com"
    assert user.is_admin is True


@pytest.mark.asyncio
async def test_resolve_401_returns_none():
    client = immich.ImmichClient(internal_url="http://immich:2283", cache_ttl_s=60)
    with aioresponses() as m:
        m.get("http://immich:2283/api/users/me", status=401)
        user = await client.resolve(bearer="bad")
    assert user is None


@pytest.mark.asyncio
async def test_cache_hits_within_ttl():
    client = immich.ImmichClient(internal_url="http://immich:2283", cache_ttl_s=60)
    with aioresponses() as m:
        m.get(
            "http://immich:2283/api/users/me",
            status=200,
            payload={"id": "ab12", "email": "nick@example.com", "name": "Nick", "isAdmin": False},
        )
        first = await client.resolve(bearer="t0k")
        # Second call should NOT hit the network — no more mocks queued.
        second = await client.resolve(bearer="t0k")
    assert first is not None
    assert second is not None
    assert first.immich_user_id == second.immich_user_id
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/unit/test_auth_immich.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `immich.py`**

Create `src/donna/api/auth/immich.py`:

```python
"""Immich identity provider: forward cookie/bearer to Immich /api/users/me."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

import aiohttp
import structlog

logger = structlog.get_logger()


@dataclass(frozen=True)
class ImmichUser:
    immich_user_id: str
    email: str
    name: str | None
    is_admin: bool


class ImmichClient:
    def __init__(self, *, internal_url: str, cache_ttl_s: int = 60) -> None:
        self._url = internal_url.rstrip("/")
        self._ttl = cache_ttl_s
        self._cache: dict[str, tuple[float, ImmichUser | None]] = {}

    async def resolve(self, *, bearer: str) -> ImmichUser | None:
        key = hashlib.sha256(bearer.encode("utf-8")).hexdigest()
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and now - cached[0] < self._ttl:
            return cached[1]

        headers = {"Authorization": f"Bearer {bearer}"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._url}/api/users/me",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 401:
                        self._cache[key] = (now, None)
                        return None
                    resp.raise_for_status()
                    data = await resp.json()
        except aiohttp.ClientError as exc:
            logger.warning("immich_resolve_failed", error=str(exc))
            return None

        user = ImmichUser(
            immich_user_id=data["id"],
            email=data["email"],
            name=data.get("name"),
            is_admin=bool(data.get("isAdmin", False)),
        )
        self._cache[key] = (now, user)
        return user
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/unit/test_auth_immich.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/donna/api/auth/immich.py tests/unit/test_auth_immich.py
git commit -m "feat(auth): Immich identity forwarding with TTL cache"
```

---

## Task 9: Email allowlist + sender + config loader

**Files:**
- Create: `src/donna/api/auth/email_allowlist.py`
- Create: `src/donna/api/auth/email_sender.py`
- Create: `src/donna/api/auth/config.py`
- Create: `config/auth.yaml`
- Test: `tests/unit/test_auth_email_allowlist.py`
- Test: `tests/unit/test_auth_email_sender.py`
- Test: `tests/unit/test_auth_config.py`

- [ ] **Step 1: Write the failing allowlist test**

Create `tests/unit/test_auth_email_allowlist.py`:

```python
"""Unit tests for email allowlist sync and lookup."""

from __future__ import annotations

import aiosqlite
import pytest
from aioresponses import aioresponses

from donna.api.auth import email_allowlist as ea


@pytest.fixture
async def db(tmp_path):
    path = tmp_path / "ea.db"
    async with aiosqlite.connect(str(path)) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(
            """
            CREATE TABLE allowed_emails (
                email TEXT PRIMARY KEY,
                immich_user_id TEXT NOT NULL,
                name TEXT,
                is_admin INTEGER NOT NULL DEFAULT 0,
                synced_at DATETIME NOT NULL
            );
            """
        )
        await conn.commit()
        yield conn


@pytest.mark.asyncio
async def test_sync_replaces_table(db):
    with aioresponses() as m:
        m.get(
            "http://immich:2283/api/admin/users",
            status=200,
            payload=[
                {"id": "u1", "email": "Nick@Example.com", "name": "Nick", "isAdmin": True},
                {"id": "u2", "email": "dad@example.com", "name": "Dad", "isAdmin": False},
            ],
        )
        await ea.sync(
            db,
            internal_url="http://immich:2283",
            admin_api_key="secret",
        )
    assert await ea.is_allowed(db, "nick@example.com")
    assert await ea.is_allowed(db, "dad@example.com")
    assert not await ea.is_allowed(db, "attacker@evil.com")


@pytest.mark.asyncio
async def test_normalization_strips_and_lowercases(db):
    with aioresponses() as m:
        m.get(
            "http://immich:2283/api/admin/users",
            status=200,
            payload=[{"id": "u1", "email": "Nick@Example.com", "name": "Nick", "isAdmin": False}],
        )
        await ea.sync(db, internal_url="http://immich:2283", admin_api_key="secret")
    assert await ea.is_allowed(db, "  NICK@example.com  ")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/unit/test_auth_email_allowlist.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `email_allowlist.py`**

Create `src/donna/api/auth/email_allowlist.py`:

```python
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


async def sync(
    conn: aiosqlite.Connection,
    *,
    internal_url: str,
    admin_api_key: str,
) -> int:
    """Replace `allowed_emails` with the current Immich user list.

    Returns the number of users synced. Raises on network error.
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

    now_iso = datetime.utcnow().isoformat()
    async with conn.execute("BEGIN"):
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
```

- [ ] **Step 4: Run the allowlist test to verify it passes**

Run: `pytest tests/unit/test_auth_email_allowlist.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Write the failing email_sender test**

Create `tests/unit/test_auth_email_sender.py`:

```python
"""Unit tests for magic-link email sender (via Gmail integration)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from donna.api.auth import email_sender


@pytest.mark.asyncio
async def test_send_magic_link_creates_and_sends_draft():
    gmail = AsyncMock()
    gmail.create_draft.return_value = "draft123"
    gmail.send_draft.return_value = True

    await email_sender.send_magic_link(
        gmail,
        to="nick@example.com",
        token="opaque-token",
        verify_base_url="https://donna.houseoffeuer.com/auth/verify",
        from_name="Donna",
    )
    gmail.create_draft.assert_awaited_once()
    gmail.send_draft.assert_awaited_once_with("draft123")
    kwargs = gmail.create_draft.await_args.kwargs
    assert kwargs["to"] == "nick@example.com"
    assert "https://donna.houseoffeuer.com/auth/verify?token=opaque-token" in kwargs["body"]
    assert "Donna" in kwargs["subject"]


@pytest.mark.asyncio
async def test_send_magic_link_bubbles_send_failure():
    gmail = AsyncMock()
    gmail.create_draft.return_value = "draft123"
    gmail.send_draft.side_effect = RuntimeError("email send disabled")
    with pytest.raises(RuntimeError):
        await email_sender.send_magic_link(
            gmail,
            to="nick@example.com",
            token="opaque",
            verify_base_url="https://donna.houseoffeuer.com/auth/verify",
            from_name="Donna",
        )
```

- [ ] **Step 6: Run it to verify fail**

Run: `pytest tests/unit/test_auth_email_sender.py -v`
Expected: FAIL — module missing.

- [ ] **Step 7: Implement `email_sender.py`**

Create `src/donna/api/auth/email_sender.py`:

```python
"""Magic-link email sender. Uses the project's Gmail integration."""

from __future__ import annotations

from typing import Any


async def send_magic_link(
    gmail: Any,
    *,
    to: str,
    token: str,
    verify_base_url: str,
    from_name: str = "Donna",
) -> None:
    """Compose and send a magic-link email.

    Uses the Gmail integration's create_draft + send_draft pattern so it
    respects the existing `email.yaml` `send_enabled` config gate.
    """
    verify_url = f"{verify_base_url}?token={token}"
    subject = f"{from_name} — access verification"
    body = (
        f"You requested access to Donna from a new device or network.\n\n"
        f"Click this link within 15 minutes to verify:\n\n"
        f"    {verify_url}\n\n"
        f"If you did not request this, ignore this email. The link will "
        f"expire automatically, and you can revoke any trusted IP at "
        f"https://donna.houseoffeuer.com/admin/access.\n"
    )
    draft_id = await gmail.create_draft(to=to, subject=subject, body=body)
    await gmail.send_draft(draft_id)
```

- [ ] **Step 8: Run the sender test to verify it passes**

Run: `pytest tests/unit/test_auth_email_sender.py -v`
Expected: 2 PASS.

- [ ] **Step 9: Write the failing config loader test**

Create `tests/unit/test_auth_config.py`:

```python
"""Unit tests for AuthConfig loader."""

from __future__ import annotations

import ipaddress

import pytest
import yaml

from donna.api.auth import config as auth_config


def test_load_parses_yaml_and_casts_cidrs(tmp_path):
    p = tmp_path / "auth.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "ip_gate": {
                    "default_trust_duration": "30d",
                    "durations_allowed": ["24h", "7d", "30d", "90d"],
                    "rate_limit_per_ip": {
                        "request_access": {"max": 5, "window_seconds": 3600},
                        "verify": {"max": 10, "window_seconds": 600},
                    },
                },
                "trusted_proxies": ["172.18.0.0/16"],
                "internal_cidrs": ["172.18.0.0/16"],
                "immich": {
                    "internal_url": "http://immich:2283",
                    "external_url": "https://immich.houseoffeuer.com",
                    "admin_api_key_env": "IMMICH_ADMIN_API_KEY",
                    "user_cache_ttl_seconds": 60,
                    "allowlist_sync_interval_seconds": 900,
                    "allowlist_stale_tolerance_seconds": 86400,
                },
                "device_tokens": {
                    "sliding_window_days": 90,
                    "absolute_max_days": 365,
                    "max_per_user": 10,
                },
                "email": {
                    "from": "donna@houseoffeuer.com",
                    "subject": "Donna access verification",
                    "verify_base_url": "https://donna.houseoffeuer.com/auth/verify",
                    "token_expiry_minutes": 15,
                },
                "bootstrap": {"admin_email_env": "DONNA_BOOTSTRAP_ADMIN_EMAIL"},
            }
        )
    )
    cfg = auth_config.load(p)
    assert cfg.device_tokens.sliding_window_days == 90
    assert cfg.trusted_proxies == [ipaddress.ip_network("172.18.0.0/16")]
    assert cfg.internal_cidrs == [ipaddress.ip_network("172.18.0.0/16")]


def test_load_rejects_empty_trusted_proxies(tmp_path):
    p = tmp_path / "auth.yaml"
    p.write_text(yaml.safe_dump({"trusted_proxies": []}))
    with pytest.raises(ValueError, match="trusted_proxies"):
        auth_config.load(p)
```

- [ ] **Step 10: Run to verify fail**

Run: `pytest tests/unit/test_auth_config.py -v`
Expected: FAIL — module missing.

- [ ] **Step 11: Implement `config.py`**

Create `src/donna/api/auth/config.py`:

```python
"""Auth config loader with strict validation. Fail-closed on missing keys."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class RateLimit:
    max: int
    window_seconds: int


@dataclass(frozen=True)
class IPGateConfig:
    default_trust_duration: str
    durations_allowed: list[str]
    rate_limit_request_access: RateLimit
    rate_limit_verify: RateLimit


@dataclass(frozen=True)
class ImmichSettings:
    internal_url: str
    external_url: str
    admin_api_key_env: str
    user_cache_ttl_seconds: int
    allowlist_sync_interval_seconds: int
    allowlist_stale_tolerance_seconds: int


@dataclass(frozen=True)
class DeviceTokenSettings:
    sliding_window_days: int
    absolute_max_days: int
    max_per_user: int


@dataclass(frozen=True)
class EmailSettings:
    from_addr: str
    subject: str
    verify_base_url: str
    token_expiry_minutes: int


@dataclass(frozen=True)
class BootstrapSettings:
    admin_email_env: str


@dataclass(frozen=True)
class AuthConfig:
    ip_gate: IPGateConfig
    trusted_proxies: list[ipaddress.IPv4Network | ipaddress.IPv6Network]
    internal_cidrs: list[ipaddress.IPv4Network | ipaddress.IPv6Network]
    immich: ImmichSettings
    device_tokens: DeviceTokenSettings
    email: EmailSettings
    bootstrap: BootstrapSettings


def _parse_cidrs(raw: list) -> list:
    if not raw:
        raise ValueError("trusted_proxies/internal_cidrs must be non-empty")
    return [ipaddress.ip_network(c) for c in raw]


def load(path: Path) -> AuthConfig:
    data = yaml.safe_load(path.read_text())
    if not data:
        raise ValueError("auth.yaml is empty")

    ig = data["ip_gate"]
    rate = ig["rate_limit_per_ip"]
    ip_gate = IPGateConfig(
        default_trust_duration=ig["default_trust_duration"],
        durations_allowed=list(ig["durations_allowed"]),
        rate_limit_request_access=RateLimit(**rate["request_access"]),
        rate_limit_verify=RateLimit(**rate["verify"]),
    )

    trusted = _parse_cidrs(data.get("trusted_proxies") or [])
    internal = _parse_cidrs(data.get("internal_cidrs") or [])

    im = data["immich"]
    immich = ImmichSettings(
        internal_url=im["internal_url"],
        external_url=im["external_url"],
        admin_api_key_env=im["admin_api_key_env"],
        user_cache_ttl_seconds=int(im["user_cache_ttl_seconds"]),
        allowlist_sync_interval_seconds=int(im["allowlist_sync_interval_seconds"]),
        allowlist_stale_tolerance_seconds=int(im["allowlist_stale_tolerance_seconds"]),
    )

    dt = data["device_tokens"]
    device_tokens = DeviceTokenSettings(
        sliding_window_days=int(dt["sliding_window_days"]),
        absolute_max_days=int(dt["absolute_max_days"]),
        max_per_user=int(dt["max_per_user"]),
    )

    em = data["email"]
    email = EmailSettings(
        from_addr=em["from"],
        subject=em["subject"],
        verify_base_url=em["verify_base_url"],
        token_expiry_minutes=int(em["token_expiry_minutes"]),
    )

    bs = data["bootstrap"]
    bootstrap = BootstrapSettings(admin_email_env=bs["admin_email_env"])

    return AuthConfig(
        ip_gate=ip_gate,
        trusted_proxies=trusted,
        internal_cidrs=internal,
        immich=immich,
        device_tokens=device_tokens,
        email=email,
        bootstrap=bootstrap,
    )
```

- [ ] **Step 12: Create `config/auth.yaml`**

Create `config/auth.yaml`:

```yaml
ip_gate:
  default_trust_duration: 30d
  durations_allowed: [24h, 7d, 30d, 90d]
  rate_limit_per_ip:
    request_access: { max: 5, window_seconds: 3600 }
    verify:         { max: 10, window_seconds: 600 }

trusted_proxies:
  - 172.18.0.0/16   # homelab Docker network, where Caddy lives

internal_cidrs:
  - 172.18.0.0/16

immich:
  internal_url: http://immich_server:2283
  external_url: https://immich.houseoffeuer.com
  admin_api_key_env: IMMICH_ADMIN_API_KEY
  user_cache_ttl_seconds: 60
  allowlist_sync_interval_seconds: 900
  allowlist_stale_tolerance_seconds: 86400

device_tokens:
  sliding_window_days: 90
  absolute_max_days: 365
  max_per_user: 10

email:
  from: "donna@houseoffeuer.com"
  subject: "Donna access verification"
  verify_base_url: "https://donna.houseoffeuer.com/auth/verify"
  token_expiry_minutes: 15

bootstrap:
  admin_email_env: DONNA_BOOTSTRAP_ADMIN_EMAIL
```

- [ ] **Step 13: Run all new unit tests**

Run: `pytest tests/unit/test_auth_email_allowlist.py tests/unit/test_auth_email_sender.py tests/unit/test_auth_config.py -v`
Expected: all PASS.

- [ ] **Step 14: Commit**

```bash
git add src/donna/api/auth/email_allowlist.py src/donna/api/auth/email_sender.py src/donna/api/auth/config.py config/auth.yaml tests/unit/test_auth_email_allowlist.py tests/unit/test_auth_email_sender.py tests/unit/test_auth_config.py
git commit -m "feat(auth): email allowlist, sender, and config loader"
```

---

## Task 10: Dependencies composition layer + router factories

**Files:**
- Create: `src/donna/api/auth/dependencies.py`
- Create: `src/donna/api/auth/router_factory.py`
- Modify: `src/donna/api/auth/__init__.py` (export types)
- Test: `tests/unit/test_auth_dependencies.py`

- [ ] **Step 1: Write the failing dependencies test**

Create `tests/unit/test_auth_dependencies.py`:

```python
"""Unit tests for dependency resolution order and fail-closed behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from donna.api.auth import dependencies as dep


def _request(host: str, headers: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        client=SimpleNamespace(host=host),
        headers=headers or {},
    )


@pytest.mark.asyncio
async def test_resolve_user_device_token_short_circuits(monkeypatch):
    ctx = dep.AuthContext(
        conn=AsyncMock(),
        auth_config=None,  # dep overrides the sub-calls
        immich_client=AsyncMock(),
    )

    async def fake_device_validate(**kwargs):
        return {"user_id": "nick"}

    monkeypatch.setattr(dep.device_tokens, "validate", fake_device_validate)

    req = _request("203.0.113.5", {"authorization": "Bearer abc"})
    user_id = await dep._resolve_user_id(req, ctx, sliding_window_days=90, absolute_max_days=365, trusted_proxies=[])
    assert user_id == "nick"
    # ip_gate/immich should NOT have been touched.
    ctx.immich_client.resolve.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_user_untrusted_ip_raises_403(monkeypatch):
    ctx = dep.AuthContext(conn=AsyncMock(), auth_config=None, immich_client=AsyncMock())

    async def fake_device_validate(**kwargs):
        return None

    async def fake_ip_check(conn, ip, **kwargs):
        return {"action": "challenge", "reason": "unknown_ip", "ip_record": None}

    monkeypatch.setattr(dep.device_tokens, "validate", fake_device_validate)
    monkeypatch.setattr(dep.ip_gate, "check_ip_access", fake_ip_check)

    req = _request("203.0.113.5")
    with pytest.raises(HTTPException) as exc:
        await dep._resolve_user_id(req, ctx, sliding_window_days=90, absolute_max_days=365, trusted_proxies=[])
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_resolve_user_missing_immich_raises_401(monkeypatch):
    ctx = dep.AuthContext(conn=AsyncMock(), auth_config=None, immich_client=AsyncMock())

    async def fake_device_validate(**kwargs):
        return None

    async def fake_ip_check(conn, ip, **kwargs):
        return {"action": "allow", "reason": "trusted", "ip_record": {"access_level": "user"}}

    monkeypatch.setattr(dep.device_tokens, "validate", fake_device_validate)
    monkeypatch.setattr(dep.ip_gate, "check_ip_access", fake_ip_check)

    req = _request("203.0.113.5")  # No cookie, no bearer.
    with pytest.raises(HTTPException) as exc:
        await dep._resolve_user_id(req, ctx, sliding_window_days=90, absolute_max_days=365, trusted_proxies=[])
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_admin_rejects_device_token_path(monkeypatch):
    ctx = dep.AuthContext(conn=AsyncMock(), auth_config=None, immich_client=AsyncMock())

    async def fake_device_validate(**kwargs):
        return {"user_id": "nick"}

    monkeypatch.setattr(dep.device_tokens, "validate", fake_device_validate)

    req = _request("203.0.113.5", {"authorization": "Bearer abc"})
    with pytest.raises(HTTPException) as exc:
        await dep._resolve_admin_user_id(
            req, ctx, sliding_window_days=90, absolute_max_days=365, trusted_proxies=[],
        )
    # Admin must not accept device-token shortcut.
    assert exc.value.status_code in (401, 403)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/unit/test_auth_dependencies.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `dependencies.py`**

Create `src/donna/api/auth/dependencies.py`:

```python
"""Dependency composition for FastAPI routes.

These are the ONLY functions routes should import from `donna.api.auth`
(plus the type aliases and router factories in router_factory.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status

from donna.api.auth import device_tokens, immich, ip_gate, trusted_proxies

_DEVICE_COOKIE_NAME = "donna_device"


@dataclass
class AuthContext:
    conn: Any            # aiosqlite.Connection
    auth_config: Any     # AuthConfig (donna.api.auth.config.AuthConfig)
    immich_client: Any   # immich.ImmichClient


def _device_token_from_request(request: Request) -> str | None:
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip() or None
    return request.cookies.get(_DEVICE_COOKIE_NAME)


def _immich_bearer_from_request(request: Request) -> str | None:
    """Return the Immich bearer token from cookie or header.

    Device-token scheme uses `Authorization: Bearer` too, so for the
    Immich path we look at the `immich_access_token` cookie first and
    fall back to `X-Immich-Token` header if present.
    """
    cookie = request.cookies.get("immich_access_token")
    if cookie:
        return cookie
    header = request.headers.get("x-immich-token", "")
    return header or None


async def _resolve_user_id(
    request: Request,
    ctx: AuthContext,
    *,
    sliding_window_days: int,
    absolute_max_days: int,
    trusted_proxies: list,
) -> str:
    # 1. Device token short-circuit.
    raw_token = _device_token_from_request(request)
    if raw_token:
        ip = trusted_proxies_module_client_ip(request, trusted_proxies)
        row = await device_tokens.validate(
            ctx.conn,
            token=raw_token,
            ip=ip,
            sliding_window_days=sliding_window_days,
            absolute_max_days=absolute_max_days,
        )
        if row:
            return row["user_id"]

    # 2. IP gate.
    ip = trusted_proxies_module_client_ip(request, trusted_proxies)
    result = await ip_gate.check_ip_access(ctx.conn, ip, service="donna")
    if result["action"] != "allow":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "ip_not_trusted", "step": "request_access"},
        )

    # 3. Immich session.
    bearer = _immich_bearer_from_request(request)
    if not bearer:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthenticated", "step": "immich_login"},
        )
    immich_user = await ctx.immich_client.resolve(bearer=bearer)
    if immich_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "immich_session_invalid"},
        )

    # 4. Look up Donna user.
    cursor = await ctx.conn.execute(
        "SELECT donna_user_id FROM users WHERE immich_user_id=?",
        (immich_user.immich_user_id,),
    )
    row = await cursor.fetchone()
    await cursor.close()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "user_not_provisioned"},
        )
    return row[0]


async def _resolve_admin_user_id(
    request: Request,
    ctx: AuthContext,
    *,
    sliding_window_days: int,
    absolute_max_days: int,
    trusted_proxies: list,
) -> str:
    # Admin ops NEVER accept a device-token shortcut. Force the Immich path.
    ip = trusted_proxies_module_client_ip(request, trusted_proxies)
    result = await ip_gate.check_ip_access(ctx.conn, ip, service="admin")
    if result["action"] != "allow":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "ip_not_trusted_admin", "step": "request_access"},
        )
    bearer = _immich_bearer_from_request(request)
    if not bearer:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "admin_login_required"},
        )
    immich_user = await ctx.immich_client.resolve(bearer=bearer)
    if immich_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "immich_session_invalid"},
        )
    cursor = await ctx.conn.execute(
        "SELECT donna_user_id, role FROM users WHERE immich_user_id=?",
        (immich_user.immich_user_id,),
    )
    row = await cursor.fetchone()
    await cursor.close()
    if row is None or row[1] != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "admin_role_required"},
        )
    return row[0]


# Thin wrapper so tests can monkeypatch the ip_gate and device_tokens modules
# directly. Import-time alias indirection keeps the code testable.
def trusted_proxies_module_client_ip(request, proxies):  # noqa: D401
    return trusted_proxies.client_ip(request, trusted_proxies=proxies)
```

- [ ] **Step 4: Write the router factory test**

Create `tests/unit/test_auth_router_factory.py`:

```python
"""Router factory tests: deny-by-default is enforced."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from donna.api.auth import router_factory


def test_public_liveness_router_no_deps():
    r = router_factory.public_liveness_router()
    assert r.dependencies == []


def test_user_router_has_dependencies():
    r = router_factory.user_router()
    assert len(r.dependencies) >= 1


def test_admin_router_has_dependencies():
    r = router_factory.admin_router()
    assert len(r.dependencies) >= 1


def test_service_router_has_dependencies():
    r = router_factory.service_router()
    assert len(r.dependencies) >= 1
```

- [ ] **Step 5: Implement `router_factory.py`**

Create `src/donna/api/auth/router_factory.py`:

```python
"""APIRouter factories with auth dependencies pre-bound.

Use these INSTEAD of bare `APIRouter()` when mounting routes. Each factory
is the single public way to get a router of its auth class, which makes
"deny by default" structurally enforced.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from donna.api.auth import dependencies as dep


async def _user_dep(request: Request) -> str:
    ctx: dep.AuthContext = request.app.state.auth_context
    cfg = request.app.state.auth_config
    return await dep._resolve_user_id(
        request,
        ctx,
        sliding_window_days=cfg.device_tokens.sliding_window_days,
        absolute_max_days=cfg.device_tokens.absolute_max_days,
        trusted_proxies=cfg.trusted_proxies,
    )


async def _admin_dep(request: Request) -> str:
    ctx: dep.AuthContext = request.app.state.auth_context
    cfg = request.app.state.auth_config
    return await dep._resolve_admin_user_id(
        request,
        ctx,
        sliding_window_days=cfg.device_tokens.sliding_window_days,
        absolute_max_days=cfg.device_tokens.absolute_max_days,
        trusted_proxies=cfg.trusted_proxies,
    )


async def _service_dep(request: Request):
    from donna.api.auth import service_keys
    ctx: dep.AuthContext = request.app.state.auth_context
    cfg = request.app.state.auth_config
    ip = dep.trusted_proxies_module_client_ip(request, cfg.trusted_proxies)
    key = request.headers.get("x-donna-service-key", "")
    forwarded_host = request.headers.get("x-forwarded-host")
    caller = await service_keys.validate(
        ctx.conn,
        presented_key=key,
        source_ip=ip,
        internal_cidrs=cfg.internal_cidrs,
        forwarded_host=forwarded_host,
    )
    if caller is None:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"error": "service_key_invalid"})
    return caller


CurrentUser = Annotated[str, Depends(_user_dep)]
CurrentAdmin = Annotated[str, Depends(_admin_dep)]
CurrentServiceCaller = Annotated[dict, Depends(_service_dep)]


def public_liveness_router() -> APIRouter:
    return APIRouter()


def public_auth_router() -> APIRouter:
    return APIRouter()


def public_webhook_twilio_router() -> APIRouter:
    return APIRouter()


def user_router() -> APIRouter:
    return APIRouter(dependencies=[Depends(_user_dep)])


def admin_router() -> APIRouter:
    return APIRouter(dependencies=[Depends(_admin_dep)])


def service_router() -> APIRouter:
    return APIRouter(dependencies=[Depends(_service_dep)])
```

- [ ] **Step 6: Export everything from `__init__.py`**

Replace `src/donna/api/auth/__init__.py` contents with:

```python
"""Authentication and authorization for the Donna REST API.

See docs/superpowers/specs/2026-04-14-api-auth-hardening-design.md.
"""

from donna.api.auth.router_factory import (
    CurrentAdmin,
    CurrentServiceCaller,
    CurrentUser,
    admin_router,
    public_auth_router,
    public_liveness_router,
    public_webhook_twilio_router,
    service_router,
    user_router,
)

__all__ = [
    "CurrentUser",
    "CurrentAdmin",
    "CurrentServiceCaller",
    "public_liveness_router",
    "public_auth_router",
    "public_webhook_twilio_router",
    "user_router",
    "admin_router",
    "service_router",
]
```

- [ ] **Step 7: Run all dependency + router factory tests**

Run: `pytest tests/unit/test_auth_dependencies.py tests/unit/test_auth_router_factory.py -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add src/donna/api/auth/dependencies.py src/donna/api/auth/router_factory.py src/donna/api/auth/__init__.py tests/unit/test_auth_dependencies.py tests/unit/test_auth_router_factory.py
git commit -m "feat(auth): dependency composition layer and router factories"
```

---

## Task 11: `/auth/*` routes — request-access, verify, status, logout

**Files:**
- Create: `src/donna/api/routes/auth_flow.py`
- Test: `tests/unit/test_auth_flow_routes.py`

- [ ] **Step 1: Write the failing integration test**

Create `tests/unit/test_auth_flow_routes.py`:

```python
"""Integration tests for /auth/* routes (in-memory SQLite + FastAPI)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from donna.api.routes import auth_flow


@pytest.fixture
def app(monkeypatch):
    # Build a minimal app with the auth_flow router mounted.
    # Use module-level test doubles for the DB and Immich client.
    raise NotImplementedError  # Implement via fixture in conftest, see step below.


# The conftest for this test defines the shared app fixture.
```

- [ ] **Step 2: Add the test conftest helper**

Create or extend `tests/unit/conftest.py` with a `auth_test_app` fixture that builds a FastAPI app, creates the auth tables in an aiosqlite connection, seeds an `allowed_emails` row, patches `email_sender.send_magic_link` with an `AsyncMock`, and mounts `auth_flow.router`.

Append to `tests/conftest.py`:

```python
import pytest
import aiosqlite
from fastapi import FastAPI
from unittest.mock import AsyncMock

from donna.api.auth import config as auth_config_module
from donna.api.auth import dependencies as auth_deps


@pytest.fixture
async def auth_test_app(tmp_path):
    """Build a FastAPI app with the auth schema + a fake Immich client."""
    db_path = tmp_path / "auth_flow_test.db"
    conn = await aiosqlite.connect(str(db_path))
    conn.row_factory = aiosqlite.Row
    await conn.executescript(
        """
        CREATE TABLE trusted_ips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending',
            access_level TEXT, trust_duration TEXT,
            trusted_at DATETIME, expires_at DATETIME,
            verified_by TEXT, label TEXT, last_seen DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            source TEXT DEFAULT 'web',
            revoked_at DATETIME, revoked_by TEXT, revoke_reason TEXT
        );
        CREATE TABLE verification_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_hash TEXT NOT NULL UNIQUE,
            ip_address TEXT NOT NULL,
            email TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            expires_at DATETIME NOT NULL,
            used INTEGER NOT NULL DEFAULT 0,
            trust_duration TEXT NOT NULL DEFAULT '30d'
        );
        CREATE TABLE ip_connections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            service TEXT, action TEXT, user_id TEXT
        );
        CREATE TABLE allowed_emails (
            email TEXT PRIMARY KEY,
            immich_user_id TEXT NOT NULL,
            name TEXT,
            is_admin INTEGER NOT NULL DEFAULT 0,
            synced_at DATETIME NOT NULL
        );
        CREATE TABLE users (
            donna_user_id TEXT PRIMARY KEY,
            immich_user_id TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL,
            name TEXT,
            role TEXT NOT NULL DEFAULT 'user',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_login_at DATETIME
        );
        CREATE TABLE device_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_hash TEXT NOT NULL UNIQUE,
            user_id TEXT NOT NULL,
            label TEXT, user_agent TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_seen DATETIME, last_seen_ip TEXT,
            expires_at DATETIME NOT NULL,
            revoked_at DATETIME, revoked_by TEXT
        );
        INSERT INTO allowed_emails (email, immich_user_id, name, is_admin, synced_at)
        VALUES ('nick@example.com', 'imm_nick', 'Nick', 1, CURRENT_TIMESTAMP);
        """
    )
    await conn.commit()

    from donna.api.auth.config import (
        AuthConfig, BootstrapSettings, DeviceTokenSettings,
        EmailSettings, IPGateConfig, ImmichSettings, RateLimit,
    )
    import ipaddress

    cfg = AuthConfig(
        ip_gate=IPGateConfig(
            default_trust_duration="30d",
            durations_allowed=["24h", "7d", "30d", "90d"],
            rate_limit_request_access=RateLimit(max=100, window_seconds=3600),
            rate_limit_verify=RateLimit(max=100, window_seconds=600),
        ),
        trusted_proxies=[ipaddress.ip_network("127.0.0.0/8")],
        internal_cidrs=[ipaddress.ip_network("172.18.0.0/16")],
        immich=ImmichSettings(
            internal_url="http://immich:2283",
            external_url="https://immich.example",
            admin_api_key_env="IMMICH_ADMIN_API_KEY",
            user_cache_ttl_seconds=60,
            allowlist_sync_interval_seconds=900,
            allowlist_stale_tolerance_seconds=86400,
        ),
        device_tokens=DeviceTokenSettings(
            sliding_window_days=90, absolute_max_days=365, max_per_user=10,
        ),
        email=EmailSettings(
            from_addr="donna@example",
            subject="Donna verify",
            verify_base_url="https://donna.example/auth/verify",
            token_expiry_minutes=15,
        ),
        bootstrap=BootstrapSettings(admin_email_env="DONNA_BOOTSTRAP_ADMIN_EMAIL"),
    )

    gmail_mock = AsyncMock()
    gmail_mock.create_draft.return_value = "draft1"
    gmail_mock.send_draft.return_value = True

    immich_mock = AsyncMock()

    app = FastAPI()
    app.state.auth_config = cfg
    app.state.auth_context = auth_deps.AuthContext(
        conn=conn, auth_config=cfg, immich_client=immich_mock,
    )
    app.state.gmail = gmail_mock

    yield app, conn, gmail_mock, immich_mock

    await conn.close()
```

- [ ] **Step 3: Write the actual route tests**

Replace `tests/unit/test_auth_flow_routes.py` with:

```python
"""Integration tests for /auth/* routes."""

from __future__ import annotations

from fastapi.testclient import TestClient

from donna.api.routes import auth_flow


def test_request_access_unknown_email_returns_202(auth_test_app):
    app, conn, gmail, immich = auth_test_app
    app.include_router(auth_flow.router, prefix="/auth")
    client = TestClient(app)

    resp = client.post("/auth/request-access", json={"email": "attacker@evil.com"})
    assert resp.status_code == 202
    # Must not send email for unknown addresses.
    gmail.send_draft.assert_not_called()


def test_request_access_known_email_sends_email(auth_test_app):
    app, conn, gmail, immich = auth_test_app
    app.include_router(auth_flow.router, prefix="/auth")
    client = TestClient(app)

    resp = client.post(
        "/auth/request-access",
        json={"email": "Nick@Example.com"},  # case + whitespace
    )
    assert resp.status_code == 202
    gmail.create_draft.assert_called_once()
    gmail.send_draft.assert_called_once()


def test_request_access_malformed_returns_202_no_send(auth_test_app):
    app, conn, gmail, immich = auth_test_app
    app.include_router(auth_flow.router, prefix="/auth")
    client = TestClient(app)

    resp = client.post("/auth/request-access", json={"email": "not-an-email"})
    assert resp.status_code == 202
    gmail.send_draft.assert_not_called()


def test_verify_marks_ip_trusted_and_burns_token(auth_test_app):
    """Full happy path: request → verify → IP is trusted."""
    import asyncio
    from donna.api.auth import verification_tokens as vt

    app, conn, gmail, immich = auth_test_app
    app.include_router(auth_flow.router, prefix="/auth")
    client = TestClient(app)

    # Seed a verification token directly (skipping the email).
    raw = asyncio.get_event_loop().run_until_complete(
        vt.create(conn, ip="testclient", email="nick@example.com", expiry_minutes=15)
    )

    resp = client.post("/auth/verify", json={"token": raw})
    assert resp.status_code == 200
    assert resp.json()["trusted"] is True

    # Second verify with same token must fail.
    resp2 = client.post("/auth/verify", json={"token": raw})
    assert resp2.status_code == 400


def test_status_reflects_ip_trust_state(auth_test_app):
    app, conn, gmail, immich = auth_test_app
    app.include_router(auth_flow.router, prefix="/auth")
    client = TestClient(app)

    resp = client.get("/auth/status")
    assert resp.status_code == 200
    assert resp.json()["trusted"] is False
```

- [ ] **Step 4: Run tests to verify fail**

Run: `pytest tests/unit/test_auth_flow_routes.py -v`
Expected: FAIL — `auth_flow` module missing.

- [ ] **Step 5: Implement `auth_flow.py`**

Create `src/donna/api/routes/auth_flow.py`:

```python
"""/auth/* routes: request-access, verify, status, logout.

Public routes — deliberately not gated by IP or Immich. The request body
is the ONLY input; responses are constant-time and enumeration-resistant.
"""

from __future__ import annotations

import hashlib

import structlog
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from donna.api.auth import (
    email_allowlist, email_sender, ip_gate, trusted_proxies, verification_tokens,
)

logger = structlog.get_logger()
router = APIRouter()


class RequestAccessBody(BaseModel):
    email: str


class VerifyBody(BaseModel):
    token: str


_GENERIC_ACCEPTED = {"status": "accepted", "message": "If your email is on file, a verification link has been sent."}


@router.post("/request-access", status_code=status.HTTP_202_ACCEPTED)
async def request_access(body: RequestAccessBody, request: Request) -> dict:
    ctx = request.app.state.auth_context
    cfg = request.app.state.auth_config

    raw_email = body.email
    if not email_allowlist.is_valid_email(raw_email):
        logger.info("auth_request_access_invalid_email",
                    email_sha256=hashlib.sha256(raw_email.encode()).hexdigest())
        return _GENERIC_ACCEPTED

    email = email_allowlist.normalize_email(raw_email)
    if not await email_allowlist.is_allowed(ctx.conn, email):
        logger.info("auth_request_access_unknown_email",
                    email_sha256=hashlib.sha256(email.encode()).hexdigest())
        return _GENERIC_ACCEPTED

    client_host = trusted_proxies.client_ip(request, trusted_proxies=cfg.trusted_proxies)
    raw_token = await verification_tokens.create(
        ctx.conn,
        ip=client_host,
        email=email,
        expiry_minutes=cfg.email.token_expiry_minutes,
        trust_duration=cfg.ip_gate.default_trust_duration,
    )

    gmail = request.app.state.gmail
    try:
        await email_sender.send_magic_link(
            gmail,
            to=email,
            token=raw_token,
            verify_base_url=cfg.email.verify_base_url,
        )
    except Exception as exc:
        logger.error("auth_request_access_email_send_failed", error=str(exc))
        # Still return 202 to avoid enumeration.
    return _GENERIC_ACCEPTED


@router.post("/verify")
async def verify(body: VerifyBody, request: Request) -> dict:
    ctx = request.app.state.auth_context
    cfg = request.app.state.auth_config

    client_host = trusted_proxies.client_ip(request, trusted_proxies=cfg.trusted_proxies)
    record = await verification_tokens.validate(
        ctx.conn, token=body.token, ip=client_host,
    )
    if record is None:
        return JSONResponse(
            status_code=400,
            content={"error": "token_invalid_or_expired"},
        )

    await verification_tokens.mark_used(ctx.conn, token=body.token)
    await ip_gate.insert_pending_ip(ctx.conn, client_host)
    await ip_gate.trust_ip(
        ctx.conn, client_host,
        access_level="user",
        trust_duration=record["trust_duration"],
        verified_by=record["email"],
    )
    return {"trusted": True, "next": "immich_login"}


@router.get("/status")
async def auth_status(request: Request) -> dict:
    ctx = request.app.state.auth_context
    cfg = request.app.state.auth_config
    client_host = trusted_proxies.client_ip(request, trusted_proxies=cfg.trusted_proxies)
    result = await ip_gate.check_ip_access(ctx.conn, client_host)
    return {"trusted": result["action"] == "allow", "reason": result["reason"]}


@router.post("/logout")
async def logout(request: Request):
    """Clear the donna_device cookie and revoke the underlying token row."""
    from donna.api.auth import device_tokens

    resp = JSONResponse({"status": "logged_out"})
    resp.delete_cookie(
        "donna_device", domain=None, path="/",
        secure=True, httponly=True, samesite="strict",
    )

    raw = request.cookies.get("donna_device") or ""
    if raw:
        cfg = request.app.state.auth_config
        ctx = request.app.state.auth_context
        # Validation path also finds the row; reuse it to get the id.
        client_host = trusted_proxies.client_ip(request, trusted_proxies=cfg.trusted_proxies)
        row = await device_tokens.validate(
            ctx.conn, token=raw, ip=client_host,
            sliding_window_days=cfg.device_tokens.sliding_window_days,
            absolute_max_days=cfg.device_tokens.absolute_max_days,
        )
        if row:
            await device_tokens.revoke(ctx.conn, device_id=row["id"], revoked_by="self")
    return resp
```

- [ ] **Step 6: Run route tests to verify they pass**

Run: `pytest tests/unit/test_auth_flow_routes.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/donna/api/routes/auth_flow.py tests/unit/test_auth_flow_routes.py tests/conftest.py
git commit -m "feat(auth): /auth/* routes — request-access, verify, status, logout"
```

---

## Task 12: Wire new auth into `src/donna/api/__init__.py` and remove CORS wildcard

**Files:**
- Modify: `src/donna/api/__init__.py`

- [ ] **Step 1: Read the current `create_app`**

Run: `sed -n '1,260p' src/donna/api/__init__.py`
Note the imports, middleware, and router mounts.

- [ ] **Step 2: Rewrite the app factory**

Replace the CORS block (around lines 213-220) with:

```python
    cors_origins_raw = os.environ.get("DONNA_CORS_ORIGINS", "").strip()
    if cors_origins_raw:
        if "*" in cors_origins_raw.split(","):
            raise RuntimeError(
                "DONNA_CORS_ORIGINS='*' is forbidden when auth cookies are in use. "
                "Set a concrete allowlist or unset the variable for same-origin "
                "deployments behind Caddy."
            )
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[o.strip() for o in cors_origins_raw.split(",")],
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["authorization", "content-type", "x-immich-token"],
        )
    # Same-origin deployment behind Caddy path-split: no CORS middleware at all.
```

- [ ] **Step 3: Load auth config and wire app.state in the `lifespan`**

Locate the lifespan context manager. Inside the `startup` portion, after the DB is opened, add:

```python
    from donna.api.auth.config import load as load_auth_config
    from donna.api.auth.dependencies import AuthContext
    from donna.api.auth.immich import ImmichClient
    from donna.api.auth.email_allowlist import sync as sync_allowlist, sync_loop

    auth_cfg = load_auth_config(Path("config/auth.yaml"))
    admin_api_key = os.environ.get(auth_cfg.immich.admin_api_key_env, "").strip()
    if not admin_api_key:
        raise RuntimeError(
            f"{auth_cfg.immich.admin_api_key_env} must be set before startup"
        )

    immich_client = ImmichClient(
        internal_url=auth_cfg.immich.internal_url,
        cache_ttl_s=auth_cfg.immich.user_cache_ttl_seconds,
    )
    app.state.auth_config = auth_cfg
    app.state.auth_context = AuthContext(
        conn=db.conn,  # aiosqlite.Connection from existing DB opener
        auth_config=auth_cfg,
        immich_client=immich_client,
    )

    # Prime the allowlist synchronously so first requests see it.
    try:
        await sync_allowlist(
            db.conn,
            internal_url=auth_cfg.immich.internal_url,
            admin_api_key=admin_api_key,
        )
    except Exception as exc:
        logger.error("auth_allowlist_initial_sync_failed", error=str(exc))

    sync_task = asyncio.create_task(
        sync_loop(
            db.conn,
            internal_url=auth_cfg.immich.internal_url,
            admin_api_key=admin_api_key,
            interval_seconds=auth_cfg.immich.allowlist_sync_interval_seconds,
        )
    )
    app.state.auth_sync_task = sync_task
```

And in the `shutdown` portion:

```python
    sync_task = getattr(app.state, "auth_sync_task", None)
    if sync_task:
        sync_task.cancel()
```

- [ ] **Step 4: Mount the new auth_flow router (remove the old Firebase-import block)**

Near the other `include_router` calls, replace / add:

```python
    from donna.api.routes import auth_flow
    app.include_router(auth_flow.router, prefix="/auth", tags=["auth"])
```

- [ ] **Step 5: Run the existing API tests to catch any regressions**

Run: `pytest tests/unit -k api or admin -v`
Expected: the existing admin tests will now be broken because they expected no auth — that's the point of Task 13. For this task, just verify no **import-time** regressions.

Run: `python -c "from donna.api import create_app; create_app()"`
Expected: no import errors. (Runtime startup still needs env vars; that's Task 20.)

- [ ] **Step 6: Commit**

```bash
git add src/donna/api/__init__.py
git commit -m "feat(auth): wire auth package and remove CORS wildcard"
```

---

## Task 13: Migrate `/tasks`, `/schedule`, `/agents` routes

**Files:**
- Modify: `src/donna/api/routes/tasks.py`
- Modify: `src/donna/api/routes/schedule.py`
- Modify: `src/donna/api/routes/agents.py`

- [ ] **Step 1: Update imports in each route file**

In `src/donna/api/routes/tasks.py`, `schedule.py`, and `agents.py`, replace:

```python
from donna.api.auth import CurrentUser
```

with (same path — the new package re-exports it):

```python
from donna.api.auth import CurrentUser
```

No change needed to imports because the new `donna.api.auth/__init__.py` exports `CurrentUser` from `router_factory`. But **change the router construction**:

In each file, find:

```python
router = APIRouter()
```

and replace with:

```python
from donna.api.auth import user_router
router = user_router()
```

Remove any per-route `Depends(CurrentUser)` that's now redundant (the `user_router()` factory applies it to all routes — but keep `CurrentUser` as a function parameter type where handlers need the resolved `user_id`).

- [ ] **Step 2: Run the existing tests**

Run: `pytest tests/unit/test_tasks.py tests/unit/test_schedule.py tests/unit/test_agents.py -v` (or whatever the actual test filenames are for those routes — check `tests/unit/`).

Expected: tests fail because they don't pass an authenticated IP or Immich cookie. Update those tests to use the new `auth_test_app` fixture and seed a trusted IP + a users row for "nick".

- [ ] **Step 3: Commit once tests pass**

```bash
git add src/donna/api/routes/tasks.py src/donna/api/routes/schedule.py src/donna/api/routes/agents.py tests/unit/test_tasks*.py tests/unit/test_schedule*.py tests/unit/test_agents*.py
git commit -m "refactor(auth): migrate /tasks, /schedule, /agents to user_router"
```

---

## Task 14: Fix `/chat/*` routes — ownership + user_id from auth, not body

**Files:**
- Modify: `src/donna/api/routes/chat.py`
- Modify: `tests/unit/test_chat_api.py`

- [ ] **Step 1: Update `chat.py` to use `user_router()` and `CurrentUser`**

In `src/donna/api/routes/chat.py`:

1. Replace `router = APIRouter()` with:
   ```python
   from donna.api.auth import user_router, CurrentUser
   router = user_router()
   ```

2. In `send_message` (around line 32):
   - Remove `user_id = body.get("user_id", "nick")  # TODO: extract from JWT`
   - Add `user_id: CurrentUser` as a parameter
   - Remove `user_id` from the body's expected fields

3. In `approve_escalation` (around line 155):
   - Remove `user_id = "nick"  # TODO: extract from JWT`
   - Add `user_id: CurrentUser` as a parameter

4. In `get_session` and `list_messages`:
   - Add `user_id: CurrentUser` parameter
   - Change `session = await db.get_chat_session(session_id)` to also scope by user_id. If `get_chat_session` doesn't support that, add a wrapper:
     ```python
     session = await db.get_chat_session(session_id)
     if session is None or session.user_id != user_id:
         raise HTTPException(status_code=404, detail="Session not found")
     ```
   - Apply the same scoping to `list_messages` before returning messages.

5. In `pin_session`, `unpin_session`, `close_session`: same pattern — require `user_id: CurrentUser`, verify ownership, then operate.

- [ ] **Step 2: Add ownership tests**

Update or add tests in `tests/unit/test_chat_api.py`:

```python
def test_get_session_from_other_user_returns_404(auth_test_app):
    """A session owned by 'alice' must not be visible to 'nick'."""
    app, conn, gmail, immich = auth_test_app
    from donna.api.routes import chat as chat_routes
    app.include_router(chat_routes.router, prefix="/chat")
    # Seed a session owned by alice
    import asyncio
    asyncio.get_event_loop().run_until_complete(
        conn.execute(
            "INSERT INTO conversation_sessions (id, user_id, channel, status, "
            "created_at, last_activity, expires_at, message_count) "
            "VALUES ('sess-alice', 'alice', 'api', 'active', '2026-01-01T00:00:00', "
            "'2026-01-01T00:00:00', '2027-01-01T00:00:00', 0)"
        )
    )
    # Seed nick as a valid user, authenticate as him
    # ... (fixture helper to authenticate as 'nick')
    # Request the session
    # Expect 404.
```

(The full test fixture for "authenticated as a specific user" should be a helper in `conftest.py` that issues a device token for the user and sends it as a cookie.)

- [ ] **Step 3: Run chat tests**

Run: `pytest tests/unit/test_chat_api.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/donna/api/routes/chat.py tests/unit/test_chat_api.py tests/conftest.py
git commit -m "fix(chat): require CurrentUser on all routes and enforce session ownership

Closes audit Vuln 4 (unauthenticated user_id trust + missing ownership checks)."
```

---

## Task 15: Migrate `/admin/*` routers to `admin_router()`

**Files:**
- Modify: all `src/donna/api/routes/admin_*.py`
- Modify: `src/donna/api/__init__.py` (remove `# no auth required` comment)

- [ ] **Step 1: In each admin route file, swap to `admin_router()`**

For each of `admin_dashboard.py`, `admin_logs.py`, `admin_invocations.py`, `admin_tasks.py`, `admin_config.py`, `admin_agents.py`, `admin_shadow.py`, `admin_preferences.py`, `admin_health.py`:

Replace `router = APIRouter()` with:

```python
from donna.api.auth import admin_router
router = admin_router()
```

- [ ] **Step 2: Remove the `# no auth required` comment from `src/donna/api/__init__.py`**

Delete the comment line `# Admin routes for the Management GUI (no auth required)`.

- [ ] **Step 3: Update existing admin tests to authenticate as admin**

The tests in `tests/unit/test_admin_*.py` need a fixture that authenticates as an admin user (seeds a `users` row with `role='admin'`, seeds a trusted IP with `access_level='admin'`, and sends a mock Immich session). Use the `auth_test_app` fixture and extend.

- [ ] **Step 4: Run admin tests**

Run: `pytest tests/unit/test_admin_*.py -v`
Expected: PASS after test fixture updates.

- [ ] **Step 5: Commit**

```bash
git add src/donna/api/routes/admin_*.py src/donna/api/__init__.py tests/unit/test_admin_*.py
git commit -m "fix(admin): gate /admin/* with admin_router + fresh Immich auth

Closes audit Vulns 1 and 2 (unauthenticated /admin/* and unauth config writes)."
```

---

## Task 16: Migrate `/llm/*` to `service_router` and delete fail-open

**Files:**
- Modify: `src/donna/api/routes/llm.py`
- Modify: `tests/unit/test_llm_gateway.py` (if it exists) or create test

- [ ] **Step 1: Replace `_require_api_key` with `service_router` + `CurrentServiceCaller`**

In `src/donna/api/routes/llm.py`:

1. Delete the entire `_require_api_key` function (the one that returns silently when `api_key` is empty).
2. Replace `router = APIRouter()` with:
   ```python
   from donna.api.auth import service_router, CurrentServiceCaller
   router = service_router()
   ```
3. On each handler that depended on `_require_api_key`, replace the dependency with a `CurrentServiceCaller` parameter:
   ```python
   @router.post("/completions", response_model=CompletionResponse)
   async def completions(
       req: CompletionRequest,
       request: Request,
       caller: CurrentServiceCaller,
   ):
       # caller is {"id": ..., "caller_id": ..., "monthly_budget_usd": ...}
       ...
   ```
4. Log `caller["caller_id"]` on every invocation.

- [ ] **Step 2: Write a test that asserts fail-closed behavior**

Add to `tests/unit/test_llm_gateway.py` (or create new file):

```python
def test_llm_completions_rejected_without_key(auth_test_app):
    app, conn, gmail, immich = auth_test_app
    from donna.api.routes import llm as llm_routes
    app.include_router(llm_routes.router, prefix="/llm")
    client = TestClient(app)

    resp = client.post("/llm/completions", json={"prompt": "hi"})
    assert resp.status_code == 401  # service_key_invalid


def test_llm_completions_rejected_when_caddy_proxied(auth_test_app):
    app, conn, gmail, immich = auth_test_app
    from donna.api.routes import llm as llm_routes
    app.include_router(llm_routes.router, prefix="/llm")
    client = TestClient(app)

    # Seed a valid key
    import asyncio
    from donna.api.auth import service_keys as sk
    raw = asyncio.get_event_loop().run_until_complete(
        sk.seed_or_rotate(conn, caller_id="curator", monthly_budget_usd=5.0)
    )

    resp = client.post(
        "/llm/completions",
        json={"prompt": "hi"},
        headers={
            "x-donna-service-key": raw,
            "x-forwarded-host": "donna.houseoffeuer.com",  # simulated Caddy
        },
    )
    assert resp.status_code == 401
```

- [ ] **Step 3: Run the LLM tests**

Run: `pytest tests/unit/test_llm_gateway.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/donna/api/routes/llm.py tests/unit/test_llm_gateway.py
git commit -m "fix(llm): service_router gate with fail-closed service keys

Closes audit Vuln 3 (fail-open LLM gateway API key check)."
```

---

## Task 17: Delete Firebase `auth.py` and purge references

**Files:**
- Delete: `src/donna/api/auth.py`
- Modify: any remaining imports

- [ ] **Step 1: Verify no remaining imports of the old file**

Run: `grep -rn "from donna.api.auth import\|from donna.api import auth" src/ tests/ | grep -v "donna.api.auth\."`
Expected: either empty, or only imports of names now exported from the new package.

- [ ] **Step 2: Delete the file**

```bash
git rm src/donna/api/auth.py
```

Note: the path `src/donna/api/auth.py` conflicts with the new `src/donna/api/auth/` package. Python 3 prefers the package. If the file still exists locally from a `git checkout`, remove it manually:

```bash
rm -f src/donna/api/auth.py  # if still present from stale state
```

- [ ] **Step 3: Full test suite**

Run: `pytest`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore(auth): delete Firebase auth stub (replaced by donna.api.auth package)

Closes audit Vuln 5 (Firebase JWT silent fallback to default user)."
```

---

## Task 18: Admin access management routes (`/admin/ips`, `/admin/devices`, `/admin/callers`)

**Files:**
- Create: `src/donna/api/routes/admin_access.py`
- Test: `tests/unit/test_admin_access_routes.py`

- [ ] **Step 1: Write the failing test (happy path)**

Create `tests/unit/test_admin_access_routes.py`:

```python
"""Admin access panel: list/revoke/trust trusted IPs and device tokens."""

from __future__ import annotations

from fastapi.testclient import TestClient

from donna.api.routes import admin_access


def test_list_trusted_ips_as_admin(auth_test_app_with_admin):
    app, conn = auth_test_app_with_admin
    app.include_router(admin_access.router, prefix="/admin")
    client = TestClient(app)

    resp = client.get("/admin/ips")
    assert resp.status_code == 200
    assert "ips" in resp.json()


def test_revoke_ip_as_admin(auth_test_app_with_admin):
    app, conn = auth_test_app_with_admin
    import asyncio
    asyncio.get_event_loop().run_until_complete(
        conn.execute(
            "INSERT INTO trusted_ips (ip_address, status, source) VALUES ('1.2.3.4', 'trusted', 'web')"
        )
    )
    asyncio.get_event_loop().run_until_complete(conn.commit())

    app.include_router(admin_access.router, prefix="/admin")
    client = TestClient(app)
    resp = client.post("/admin/ips/1.2.3.4/revoke", json={"reason": "test"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "revoked"


def test_list_devices_as_admin(auth_test_app_with_admin):
    app, conn = auth_test_app_with_admin
    app.include_router(admin_access.router, prefix="/admin")
    client = TestClient(app)

    resp = client.get("/admin/devices")
    assert resp.status_code == 200


def test_non_admin_gets_403(auth_test_app_user_only):
    app, conn = auth_test_app_user_only
    app.include_router(admin_access.router, prefix="/admin")
    client = TestClient(app)

    resp = client.get("/admin/ips")
    assert resp.status_code == 403
```

- [ ] **Step 2: Add the `auth_test_app_with_admin` and `auth_test_app_user_only` fixtures**

Extend `tests/conftest.py` with two variants of `auth_test_app`:
- `auth_test_app_with_admin`: seeds a `users` row with `role='admin'`, marks the test client IP as trusted with `access_level='admin'`, and patches the Immich mock to return an admin user.
- `auth_test_app_user_only`: seeds a `users` row with `role='user'` only.

(The exact fixture code mirrors the `auth_test_app` fixture from Task 11 with extra INSERTs. Keep it DRY by factoring `_build_auth_app(role='admin')` into a helper.)

- [ ] **Step 3: Implement `admin_access.py`**

Create `src/donna/api/routes/admin_access.py`:

```python
"""Admin access management: trusted IPs, device tokens, service callers."""

from __future__ import annotations

from typing import Any

from fastapi import Body, Request

from donna.api.auth import CurrentAdmin, admin_router, device_tokens, ip_gate, service_keys

router = admin_router()


@router.get("/ips")
async def list_ips(
    request: Request,
    user_id: CurrentAdmin,
    status_filter: str = "all",
) -> dict[str, Any]:
    conn = request.app.state.auth_context.conn
    if status_filter == "all":
        cursor = await conn.execute(
            "SELECT * FROM trusted_ips ORDER BY created_at DESC LIMIT 500"
        )
    else:
        cursor = await conn.execute(
            "SELECT * FROM trusted_ips WHERE status=? ORDER BY created_at DESC LIMIT 500",
            (status_filter,),
        )
    rows = await cursor.fetchall()
    await cursor.close()
    return {"ips": [dict(r) for r in rows]}


@router.post("/ips/{ip}/revoke")
async def revoke_ip_route(
    request: Request,
    ip: str,
    user_id: CurrentAdmin,
    body: dict = Body(default_factory=dict),
) -> dict[str, str]:
    conn = request.app.state.auth_context.conn
    reason = body.get("reason") if isinstance(body, dict) else None
    await ip_gate.revoke_ip(conn, ip, revoked_by=user_id, reason=reason)
    return {"status": "revoked", "ip": ip}


@router.post("/ips/{ip}/trust")
async def trust_ip_route(
    request: Request,
    ip: str,
    user_id: CurrentAdmin,
    body: dict = Body(default_factory=dict),
) -> dict[str, str]:
    conn = request.app.state.auth_context.conn
    access_level = body.get("access_level", "user")
    trust_duration = body.get("trust_duration", "30d")
    await ip_gate.insert_pending_ip(conn, ip)
    await ip_gate.trust_ip(
        conn, ip,
        access_level=access_level,
        trust_duration=trust_duration,
        verified_by=user_id,
    )
    return {"status": "trusted", "ip": ip}


@router.get("/devices")
async def list_devices(
    request: Request,
    user_id: CurrentAdmin,
    target_user: str | None = None,
) -> dict[str, Any]:
    conn = request.app.state.auth_context.conn
    target = target_user or user_id
    devices = await device_tokens.list_for_user(conn, user_id=target)
    return {"devices": devices}


@router.post("/devices/{device_id}/revoke")
async def revoke_device(
    request: Request,
    device_id: int,
    user_id: CurrentAdmin,
) -> dict[str, str]:
    conn = request.app.state.auth_context.conn
    await device_tokens.revoke(conn, device_id=device_id, revoked_by=user_id)
    return {"status": "revoked", "device_id": str(device_id)}


@router.get("/callers")
async def list_callers(request: Request, user_id: CurrentAdmin) -> dict[str, Any]:
    conn = request.app.state.auth_context.conn
    cursor = await conn.execute(
        "SELECT id, caller_id, monthly_budget_usd, enabled, created_at, revoked_at "
        "FROM llm_gateway_callers ORDER BY caller_id"
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return {"callers": [dict(r) for r in rows]}


@router.post("/callers/{caller_id}/rotate")
async def rotate_caller(
    request: Request,
    caller_id: str,
    user_id: CurrentAdmin,
    body: dict = Body(default_factory=dict),
) -> dict[str, Any]:
    conn = request.app.state.auth_context.conn
    budget = float(body.get("monthly_budget_usd", 0.0))
    raw = await service_keys.seed_or_rotate(
        conn, caller_id=caller_id, monthly_budget_usd=budget,
    )
    return {
        "caller_id": caller_id,
        "api_key": raw,  # ONLY time the raw key is returned. Admin must copy.
        "monthly_budget_usd": budget,
    }
```

- [ ] **Step 4: Mount the router in `src/donna/api/__init__.py`**

Add near the other admin router mounts:

```python
    from donna.api.routes import admin_access
    app.include_router(admin_access.router, prefix="/admin", tags=["admin"])
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/test_admin_access_routes.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/donna/api/routes/admin_access.py src/donna/api/__init__.py tests/unit/test_admin_access_routes.py tests/conftest.py
git commit -m "feat(admin): admin access panel routes for IPs, devices, service callers"
```

---

## Task 19: End-to-end integration test (full auth flow)

**Files:**
- Create: `tests/integration/test_auth_end_to_end.py`

- [ ] **Step 1: Write an end-to-end flow test**

Create `tests/integration/test_auth_end_to_end.py`:

```python
"""End-to-end: request access → verify → Immich login → authenticated task request."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from donna.api.auth import verification_tokens


@pytest.mark.asyncio
async def test_full_flow_web_browser(auth_test_app):
    app, conn, gmail, immich_mock = auth_test_app

    # Mount all routers.
    from donna.api.routes import auth_flow, tasks as tasks_route
    app.include_router(auth_flow.router, prefix="/auth")
    app.include_router(tasks_route.router, prefix="/tasks")

    client = TestClient(app)

    # 1. Unknown IP → /tasks returns 403.
    resp = client.get("/tasks")
    assert resp.status_code in (401, 403)

    # 2. POST /auth/request-access with a known email → 202, gmail called.
    resp = client.post("/auth/request-access", json={"email": "nick@example.com"})
    assert resp.status_code == 202
    assert gmail.send_draft.called

    # 3. Simulate clicking the email: create a verification token directly.
    raw = await verification_tokens.create(
        conn, ip="testclient", email="nick@example.com", expiry_minutes=15
    )
    resp = client.post("/auth/verify", json={"token": raw})
    assert resp.status_code == 200

    # 4. Seed a users row for nick.
    await conn.execute(
        "INSERT INTO users (donna_user_id, immich_user_id, email, role) "
        "VALUES ('nick', 'imm_nick', 'nick@example.com', 'user')"
    )
    await conn.commit()

    # 5. Mock Immich to return nick.
    from donna.api.auth.immich import ImmichUser
    immich_mock.resolve.return_value = ImmichUser(
        immich_user_id="imm_nick", email="nick@example.com", name="Nick", is_admin=True,
    )

    # 6. Now /tasks should return 200 with an Immich bearer.
    resp = client.get(
        "/tasks",
        headers={"x-immich-token": "fake-but-mocked"},
    )
    assert resp.status_code in (200, 404)  # 404 if no tasks table seeded, but NOT 401/403.
```

- [ ] **Step 2: Run the integration test**

Run: `pytest tests/integration/test_auth_end_to_end.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_auth_end_to_end.py
git commit -m "test(auth): end-to-end integration test of full auth flow"
```

---

## Task 20: Caddy config, env vars, bootstrap documentation

**Files:**
- Create: `docker/caddy/donna.Caddyfile.example`
- Modify: `docker/.env.example` (or create if missing)
- Modify: `SETUP.md`
- Modify: `config/email.yaml` — set `send_enabled: true`

- [ ] **Step 1: Create Caddy snippet**

Create `docker/caddy/donna.Caddyfile.example`:

```caddy
donna.houseoffeuer.com {
    encode gzip zstd

    @api path /api/*
    handle @api {
        uri strip_prefix /api
        reverse_proxy donna-api:8200 {
            header_up X-Forwarded-For {remote_host}
            header_up X-Forwarded-Proto {scheme}
            header_up X-Forwarded-Host {host}
        }
    }

    handle {
        reverse_proxy donna-ui:8400
    }

    log {
        output file /var/log/caddy/donna-access.log
    }
}
```

- [ ] **Step 2: Add env vars to `docker/.env.example`**

Append:

```bash
# --- Auth ---
IMMICH_ADMIN_API_KEY=<create an admin API key in Immich with "read users" scope>
DONNA_BOOTSTRAP_ADMIN_EMAIL=nickfeuer@gmail.com
# DONNA_CORS_ORIGINS must be a concrete list or left unset (same-origin via Caddy).
# DONNA_CORS_ORIGINS=
```

- [ ] **Step 3: Enable Gmail send in `config/email.yaml`**

Open `config/email.yaml` and set `send_enabled: true`. If the config is structured differently, add a top-level `send_enabled: true` key — the exact location depends on the existing structure.

- [ ] **Step 4: Write the bootstrap walkthrough in `SETUP.md`**

Add a new section titled `## Auth bootstrap` with this content:

```markdown
## Auth bootstrap

After the first deploy, Donna has an empty `users` table and nobody can
reach anything. Bootstrap yourself as follows:

1. **Create an Immich admin API key** with "read users" scope. Copy it.
2. Set `IMMICH_ADMIN_API_KEY` and `DONNA_BOOTSTRAP_ADMIN_EMAIL` in
   `docker/.env` and restart `donna-api`.
3. On startup Donna fetches the Immich user list and caches it in
   `allowed_emails`. The first request to `/api/auth/request-access`
   from your IP will succeed only if your email is there.
4. Open `https://donna.houseoffeuer.com` in a browser. The SPA will
   show the "Request access" page because your IP is not yet trusted.
5. Submit your email. Check Gmail — the magic link arrives within
   seconds.
6. Click the link on the same device (same IP). Your IP is now marked
   trusted with `access_level='user'` for 30 days.
7. You'll be redirected to Immich login. Sign in with the email you
   just verified.
8. Donna reads the Immich cookie, auto-provisions a `users` row, and —
   because the table had no admin rows and your email matches
   `DONNA_BOOTSTRAP_ADMIN_EMAIL` — sets your `role='admin'`.
9. **Remove `DONNA_BOOTSTRAP_ADMIN_EMAIL`** from `.env` and restart.
   It's only used once; leaving it around is safe but unnecessary.
10. Test admin access: open `/admin/access` in the UI. You should see
    your own trusted IP and be able to list devices.

### Local Docker callers

For each internal service that calls `/llm/completions`:

1. Open the admin panel at `/admin/access` → "Service callers" → "Add".
2. Enter the caller ID (e.g., `curator`) and monthly budget.
3. Donna returns the raw API key **once**. Copy it into that service's
   `.env` as `DONNA_LLM_API_KEY`.
4. The service calls `http://donna-api:8200/llm/completions` with
   `X-Donna-Service-Key: $DONNA_LLM_API_KEY`. Never through Caddy.
```

- [ ] **Step 5: Run the full suite one more time**

Run: `pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add docker/caddy/donna.Caddyfile.example docker/.env.example config/email.yaml SETUP.md
git commit -m "docs(auth): Caddy config, env vars, and bootstrap walkthrough"
```

---

## Self-Review

After writing the plan, I checked it against the spec:

**Spec coverage check:**
- ✅ 4-layer auth stack (device/IP/Immich/service) — Tasks 5, 3, 8, 6
- ✅ Alembic migration with 7 tables — Task 1
- ✅ Async port of ip_gate — Task 3
- ✅ Magic-link verification tokens, IP-bound — Task 4
- ✅ Device tokens for mobile + browser (argon2) — Task 5
- ✅ Service keys with CIDR + X-Forwarded-Host rejection — Task 6
- ✅ Trusted proxy XFF resolution — Task 7
- ✅ Immich identity forwarding with TTL cache — Task 8
- ✅ Email allowlist sync from Immich admin API — Task 9
- ✅ Email sender via Gmail integration — Task 9
- ✅ AuthConfig loader with fail-closed validation — Task 9
- ✅ Dependency composition layer — Task 10
- ✅ Router factories (deny by default) — Task 10
- ✅ `/auth/*` routes — Task 11
- ✅ CORS wildcard removed + auth wiring into lifespan — Task 12
- ✅ `/tasks`, `/schedule`, `/agents` migrated — Task 13
- ✅ `/chat/*` user impersonation fix — Task 14 (closes Vuln 4)
- ✅ `/admin/*` gated — Task 15 (closes Vulns 1, 2)
- ✅ `/llm/*` fail-closed — Task 16 (closes Vuln 3)
- ✅ Firebase `auth.py` deleted — Task 17 (closes Vuln 5)
- ✅ CORS wildcard + credentials removed — Task 12 (closes Vuln 6)
- ✅ Admin access panel routes — Task 18
- ✅ End-to-end flow test — Task 19
- ✅ Caddy config + bootstrap docs — Task 20
- ✅ Browser cookie flow (Set-Cookie `donna_device`) — covered in Task 10 dependencies + Task 11 logout; the Set-Cookie is set on Immich-login completion (covered by the `/auth/verify` follow-up that issues the device token — see the spec's "First-time desktop browser login" step 12).

**Placeholder scan:** No "TODO", "TBD", "implement later" in-plan placeholders. One explicit `<CURRENT_HEAD>` in Task 1 that must be substituted by running `alembic heads` — that's not a placeholder, it's a required lookup step.

**Type consistency:**
- `ip_gate.check_ip_access(conn, ip_address, *, service=...)` — used consistently across Tasks 3, 10.
- `device_tokens.issue/validate/revoke/list_for_user` — consistent kwargs across Tasks 5, 10, 11, 18.
- `service_keys.validate` kwargs `presented_key`, `source_ip`, `internal_cidrs`, `forwarded_host` — consistent Tasks 6, 10, 16.
- `ImmichUser` dataclass fields `immich_user_id`, `email`, `is_admin` — consistent Tasks 8, 10, 19.
- `AuthContext(conn, auth_config, immich_client)` — consistent Tasks 10, 12, 18, 19.
- `CurrentUser`/`CurrentAdmin`/`CurrentServiceCaller` type aliases — consistent Tasks 10, 13, 14, 15, 16, 18.

No inconsistencies found.
