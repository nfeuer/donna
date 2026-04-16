"""Startup initialization for the skill system.

Called once at application boot. Idempotent:
1. Generate embeddings for capability rows with embedding=NULL.
2. Load seed skills from skills/ for capabilities without a skill row.
"""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import structlog

from donna.capabilities.embeddings import embed_text, embedding_to_bytes
from donna.capabilities.registry import _embedding_text
from donna.skills.loader import SkillLoadError, load_skill_from_directory

logger = structlog.get_logger()


async def initialize_skill_system(
    conn: aiosqlite.Connection,
    skills_dir: Path,
) -> None:
    await _fill_missing_embeddings(conn)
    await _load_seed_skills(conn, skills_dir)


async def _fill_missing_embeddings(conn: aiosqlite.Connection) -> None:
    cursor = await conn.execute(
        "SELECT id, name, description, input_schema FROM capability WHERE embedding IS NULL"
    )
    rows = await cursor.fetchall()
    if not rows:
        return

    for row in rows:
        cap_id, name, description, input_schema_json = row
        schema = json.loads(input_schema_json) if input_schema_json else {}
        text = _embedding_text(name, description, schema)
        vec = embed_text(text)
        blob = embedding_to_bytes(vec)

        await conn.execute(
            "UPDATE capability SET embedding = ? WHERE id = ?",
            (blob, cap_id),
        )
        logger.info("capability_embedding_generated", capability_id=cap_id, name=name)

    await conn.commit()


async def _load_seed_skills(
    conn: aiosqlite.Connection,
    skills_dir: Path,
) -> None:
    if not skills_dir.exists():
        logger.warning("seed_skills_dir_not_found", path=str(skills_dir))
        return

    cursor = await conn.execute("SELECT name FROM capability")
    capability_names = {row[0] for row in await cursor.fetchall()}

    cursor = await conn.execute("SELECT capability_name FROM skill")
    skill_names = {row[0] for row in await cursor.fetchall()}

    for skill_subdir in sorted(skills_dir.iterdir()):
        if not skill_subdir.is_dir():
            continue
        if not (skill_subdir / "skill.yaml").exists():
            continue
        name = skill_subdir.name
        if name not in capability_names:
            logger.info("skill_skipped_no_capability", skill=name)
            continue
        if name in skill_names:
            continue

        try:
            await load_skill_from_directory(skill_subdir, conn, initial_state="sandbox")
        except SkillLoadError as exc:
            logger.error("skill_load_failed", skill=name, error=str(exc))
