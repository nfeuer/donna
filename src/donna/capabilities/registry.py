"""CapabilityRegistry — CRUD and retrieval for the capability table.

See docs/superpowers/specs/2026-04-15-skill-system-and-challenger-refactor-design.md §6.1
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite
import structlog
import uuid6

from donna.capabilities.models import (
    SELECT_CAPABILITY,
    CapabilityRow,
    row_to_capability,
)

logger = structlog.get_logger()


@dataclass(slots=True)
class CapabilityInput:
    """Input payload for registering a new capability."""

    name: str
    description: str
    input_schema: dict
    trigger_type: str  # on_message | on_schedule | on_manual
    default_output_shape: dict | None = None
    notes: str | None = None


class CapabilityRegistry:
    """CRUD and retrieval for user-facing capabilities."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._conn = connection

    async def register(
        self,
        payload: CapabilityInput,
        created_by: str,
        status: str = "active",
    ) -> CapabilityRow:
        """Insert a new capability row.

        Raises ValueError if a capability with the same name already exists.
        """
        existing = await self.get_by_name(payload.name)
        if existing is not None:
            raise ValueError(f"Capability '{payload.name}' already exists")

        cap_id = str(uuid6.uuid7())
        now = datetime.now(timezone.utc)

        await self._conn.execute(
            f"""
            INSERT INTO capability ({SELECT_CAPABILITY})
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cap_id,
                payload.name,
                payload.description,
                json.dumps(payload.input_schema),
                payload.trigger_type,
                json.dumps(payload.default_output_shape) if payload.default_output_shape else None,
                status,
                None,  # embedding — filled by Task 7
                now.isoformat(),
                created_by,
                payload.notes,
            ),
        )
        await self._conn.commit()

        logger.info(
            "capability_registered",
            capability_id=cap_id,
            name=payload.name,
            status=status,
            created_by=created_by,
        )

        result = await self.get_by_name(payload.name)
        assert result is not None
        return result

    async def get_by_name(self, name: str) -> CapabilityRow | None:
        cursor = await self._conn.execute(
            f"SELECT {SELECT_CAPABILITY} FROM capability WHERE name = ?",
            (name,),
        )
        row = await cursor.fetchone()
        return row_to_capability(row) if row else None

    async def list_all(self, status: str | None = None, limit: int = 500) -> list[CapabilityRow]:
        if status is None:
            cursor = await self._conn.execute(
                f"SELECT {SELECT_CAPABILITY} FROM capability ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        else:
            cursor = await self._conn.execute(
                f"SELECT {SELECT_CAPABILITY} FROM capability WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        rows = await cursor.fetchall()
        return [row_to_capability(r) for r in rows]

    async def update_status(self, name: str, status: str) -> None:
        """Change a capability's status (e.g., pending_review → active)."""
        await self._conn.execute(
            "UPDATE capability SET status = ? WHERE name = ?",
            (status, name),
        )
        await self._conn.commit()
