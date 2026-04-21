"""Load seed capabilities from YAML and UPSERT into the capability table.

Idempotent: if a row exists, update its description/input_schema/trigger_type;
else insert. Called at orchestrator startup so editing capabilities.yaml +
restarting picks up changes without requiring a new Alembic migration.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
import structlog
import yaml

logger = structlog.get_logger()


class SeedCapabilityLoader:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._conn = connection

    async def load_and_upsert(self, yaml_path: Path) -> int:
        with open(yaml_path) as f:
            data = yaml.safe_load(f) or {}
        entries = data.get("capabilities", []) or []
        upserted = 0
        now = datetime.now(tz=timezone.utc).isoformat()
        for entry in entries:
            name = entry.get("name")
            if not name:
                continue
            description = entry.get("description", "")
            trigger_type = entry.get("trigger_type", "on_message")
            input_schema = json.dumps(entry.get("input_schema", {}))
            default_output_shape = (
                json.dumps(entry["default_output_shape"])
                if "default_output_shape" in entry
                else None
            )

            cursor = await self._conn.execute(
                "SELECT description, input_schema, trigger_type, default_output_shape "
                "FROM capability WHERE name = ?", (name,),
            )
            row = await cursor.fetchone()
            if row is None:
                await self._conn.execute(
                    "INSERT INTO capability "
                    "(id, name, description, input_schema, trigger_type, "
                    " default_output_shape, status, created_at, created_by) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, 'seed')",
                    (str(uuid.uuid4()), name, description, input_schema,
                     trigger_type, default_output_shape, now),
                )
            else:
                existing_desc, existing_schema, existing_trigger, existing_shape = row
                changed_fields: list[str] = []
                if existing_desc != description:
                    changed_fields.append("description")
                if existing_schema != input_schema:
                    changed_fields.append("input_schema")
                if existing_trigger != trigger_type:
                    changed_fields.append("trigger_type")
                if (existing_shape or None) != (default_output_shape or None):
                    changed_fields.append("default_output_shape")
                if changed_fields:
                    logger.info(
                        "seed_capability_drift",
                        capability_name=name,
                        fields=changed_fields,
                    )
                await self._conn.execute(
                    "UPDATE capability "
                    "SET description = ?, input_schema = ?, trigger_type = ?, "
                    "    default_output_shape = ? "
                    "WHERE name = ?",
                    (description, input_schema, trigger_type,
                     default_output_shape, name),
                )
            upserted += 1
        await self._conn.commit()
        logger.info("capabilities_seeded", count=upserted)
        return upserted
