"""CapabilityRegistry — CRUD and retrieval for the capability table.

See docs/superpowers/specs/archive/2026-04-15-skill-system-and-challenger-refactor-design.md §6.1
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aiosqlite
import structlog
import uuid6

from donna.capabilities.embeddings import (
    bytes_to_embedding,
    cosine_similarity,
    embed_text,
    embedding_to_bytes,
)
from donna.capabilities.models import (
    SELECT_CAPABILITY,
    CapabilityRow,
    row_to_capability,
)
from donna.config import SkillSystemConfig

logger = structlog.get_logger()


def _embedding_text(name: str, description: str, input_schema: dict[str, Any]) -> str:
    field_names = list(input_schema.get("properties", {}).keys())
    field_part = " ".join(field_names) if field_names else ""
    return f"{name}. {description}. Inputs: {field_part}".strip()


@dataclass(slots=True)
class CapabilityInput:
    """Input payload for registering a new capability."""

    name: str
    description: str
    input_schema: dict[str, Any]
    trigger_type: str  # on_message | on_schedule | on_manual
    default_output_shape: dict[str, Any] | None = None
    notes: str | None = None


class CapabilityRegistry:
    """CRUD and retrieval for user-facing capabilities."""

    def __init__(
        self,
        connection: aiosqlite.Connection,
        config: SkillSystemConfig | None = None,
    ) -> None:
        self._conn = connection
        self._similarity_threshold = (
            config.similarity_audit_threshold if config else self.SIMILARITY_THRESHOLD
        )

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
        now = datetime.now(UTC)

        embedding_text = _embedding_text(payload.name, payload.description, payload.input_schema)
        embedding_blob = embedding_to_bytes(embed_text(embedding_text))
        audit_status = await self._audit_for_duplicates(embedding_blob, status)

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
                audit_status,
                embedding_blob,
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
            status=audit_status,
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
                f"SELECT {SELECT_CAPABILITY} FROM capability WHERE status = ? "
                "ORDER BY created_at DESC LIMIT ?",
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

    SIMILARITY_THRESHOLD = 0.80

    async def _audit_for_duplicates(
        self, new_embedding_blob: bytes, requested_status: str
    ) -> str:
        if requested_status != "active":
            return requested_status

        new_vec = bytes_to_embedding(new_embedding_blob)
        existing = await self.list_all(status="active", limit=1000)

        for cap in existing:
            if cap.embedding is None:
                continue
            cap_vec = bytes_to_embedding(cap.embedding)
            sim = cosine_similarity(new_vec, cap_vec)
            if sim >= self._similarity_threshold:
                logger.warning(
                    "capability_post_creation_audit_flagged",
                    similar_to=cap.name,
                    similarity=sim,
                    threshold=self._similarity_threshold,
                )
                return "pending_review"

        return "active"

    async def semantic_search(
        self, query: str, k: int = 5, status: str = "active"
    ) -> list[tuple[CapabilityRow, float]]:
        query_vec = embed_text(query)
        caps = await self.list_all(status=status, limit=1000)

        scored: list[tuple[CapabilityRow, float]] = []
        for cap in caps:
            if cap.embedding is None:
                continue
            cap_vec = bytes_to_embedding(cap.embedding)
            score = cosine_similarity(query_vec, cap_vec)
            scored.append((cap, score))

        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:k]
