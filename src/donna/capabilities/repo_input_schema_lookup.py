"""Resolve a capability name to its input_schema dict via the capability table."""
from __future__ import annotations

import json
from typing import Any

import aiosqlite


class CapabilityInputSchemaDBLookup:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._conn = connection

    async def lookup(self, capability_name: str) -> dict[str, Any]:
        cursor = await self._conn.execute(
            "SELECT input_schema FROM capability WHERE name = ?",
            (capability_name,),
        )
        row = await cursor.fetchone()
        if row is None or row[0] is None:
            return {}
        try:
            parsed = json.loads(row[0])
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
