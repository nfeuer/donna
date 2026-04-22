"""Skill loader — parse filesystem YAML into skill + skill_version rows."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
import structlog
import uuid6
import yaml

from donna.skills.models import SELECT_SKILL, SELECT_SKILL_VERSION

logger = structlog.get_logger()


class SkillLoadError(Exception):
    pass


async def load_skill_from_directory(
    skill_dir: Path,
    conn: aiosqlite.Connection,
    initial_state: str = "sandbox",
) -> str:
    """Load a skill from a filesystem directory and insert into DB.

    Returns the newly-created skill.id.
    Raises SkillLoadError if capability is missing, files are missing, or YAML fails.
    """
    skill_yaml_path = skill_dir / "skill.yaml"
    if not skill_yaml_path.exists():
        raise SkillLoadError(f"skill.yaml not found in {skill_dir}")

    try:
        with open(skill_yaml_path) as f:
            skill_data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise SkillLoadError(f"Failed to parse {skill_yaml_path}: {exc}") from exc

    capability_name = skill_data.get("capability_name")
    if not capability_name:
        raise SkillLoadError(f"{skill_yaml_path} missing capability_name field")

    cursor = await conn.execute(
        "SELECT name FROM capability WHERE name = ?", (capability_name,)
    )
    if not await cursor.fetchone():
        raise SkillLoadError(
            f"capability '{capability_name}' not found in registry; "
            f"seed the capability before loading this skill"
        )

    step_content: dict[str, str] = {}
    output_schemas: dict[str, dict[str, Any]] = {}
    for step in skill_data.get("steps", []):
        if step.get("kind") != "llm":
            continue
        name = step["name"]
        prompt_path = skill_dir / step["prompt"]
        schema_path = skill_dir / step["output_schema"]

        if not prompt_path.exists():
            raise SkillLoadError(f"prompt file not found: {prompt_path}")
        if not schema_path.exists():
            raise SkillLoadError(f"schema file not found: {schema_path}")

        step_content[name] = prompt_path.read_text()
        with open(schema_path) as f:
            output_schemas[name] = json.load(f)

    skill_id = str(uuid6.uuid7())
    version_id = str(uuid6.uuid7())
    now = datetime.now(UTC).isoformat()

    await conn.execute(
        f"INSERT INTO skill ({SELECT_SKILL}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (skill_id, capability_name, version_id, initial_state, 0, None, now, now),
    )

    yaml_backbone = skill_yaml_path.read_text()
    await conn.execute(
        f"INSERT INTO skill_version ({SELECT_SKILL_VERSION}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (version_id, skill_id, skill_data.get("version", 1), yaml_backbone,
         json.dumps(step_content), json.dumps(output_schemas), "human",
         "Initial seed version", now),
    )

    await conn.commit()

    logger.info(
        "skill_loaded",
        skill_id=skill_id,
        capability_name=capability_name,
        version_number=skill_data.get("version", 1),
        initial_state=initial_state,
    )

    return skill_id
