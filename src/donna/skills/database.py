"""Lightweight read-only DB access for skills."""

from __future__ import annotations

import aiosqlite

from donna.skills.models import (
    SELECT_SKILL,
    SELECT_SKILL_VERSION,
    SkillRow,
    SkillVersionRow,
    row_to_skill,
    row_to_skill_version,
)


class SkillDatabase:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._conn = connection

    async def list_all(self, state: str | None = None, limit: int = 200) -> list[SkillRow]:
        if state:
            cursor = await self._conn.execute(
                f"SELECT {SELECT_SKILL} FROM skill WHERE state = ? "
                "ORDER BY updated_at DESC LIMIT ?",
                (state, limit),
            )
        else:
            cursor = await self._conn.execute(
                f"SELECT {SELECT_SKILL} FROM skill ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            )
        rows = await cursor.fetchall()
        return [row_to_skill(r) for r in rows]

    async def get_by_id(self, skill_id: str) -> SkillRow | None:
        cursor = await self._conn.execute(
            f"SELECT {SELECT_SKILL} FROM skill WHERE id = ?",
            (skill_id,),
        )
        row = await cursor.fetchone()
        return row_to_skill(row) if row else None

    async def get_by_capability(self, capability_name: str) -> SkillRow | None:
        cursor = await self._conn.execute(
            f"SELECT {SELECT_SKILL} FROM skill WHERE capability_name = ?",
            (capability_name,),
        )
        row = await cursor.fetchone()
        return row_to_skill(row) if row else None

    async def get_version(self, version_id: str) -> SkillVersionRow | None:
        cursor = await self._conn.execute(
            f"SELECT {SELECT_SKILL_VERSION} FROM skill_version WHERE id = ?",
            (version_id,),
        )
        row = await cursor.fetchone()
        return row_to_skill_version(row) if row else None
