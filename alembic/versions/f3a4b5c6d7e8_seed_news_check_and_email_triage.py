"""seed news_check + email_triage capabilities + skills + fixtures

Revision ID: f3a4b5c6d7e8
Revises: e7f8a9b0c1d2
Create Date: 2026-04-20 00:00:00.000000
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
import yaml
from alembic import op

revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, None] = "e7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CAP_NAMES = ("news_check", "email_triage")


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _project_root() -> Path:
    # alembic/versions/<file>.py → project root is parents[2]
    return Path(__file__).resolve().parents[2]


def _seed_one(conn, capability_name: str, caps_config: list[dict], now: str) -> None:
    root = _project_root()
    skill_dir = root / "skills" / capability_name

    cap_entry = next((c for c in caps_config if c.get("name") == capability_name), None)
    if cap_entry is None:
        raise RuntimeError(f"{capability_name} missing from config/capabilities.yaml")

    capability_id = str(uuid.uuid4())
    conn.execute(sa.text(
        "INSERT INTO capability (id, name, description, input_schema, "
        "trigger_type, default_output_shape, status, created_at, created_by) "
        "VALUES (:id, :name, :desc, :schema, :trigger, :shape, 'active', :now, 'seed')"
    ), {
        "id": capability_id,
        "name": capability_name,
        "desc": cap_entry.get("description", ""),
        "schema": json.dumps(cap_entry.get("input_schema", {})),
        "trigger": cap_entry.get("trigger_type", "on_schedule"),
        "shape": json.dumps(cap_entry.get("default_output_shape", {})),
        "now": now,
    })

    # Skill + version (sandbox).
    skill_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    conn.execute(sa.text(
        "INSERT INTO skill (id, capability_name, current_version_id, state, "
        "requires_human_gate, created_at, updated_at) "
        "VALUES (:id, :cap, :vid, 'sandbox', 0, :now, :now)"
    ), {"id": skill_id, "cap": capability_name, "vid": version_id, "now": now})

    yaml_backbone = _read(skill_dir / "skill.yaml")

    step_content: dict[str, str] = {}
    output_schemas: dict[str, dict] = {}
    for step_md in sorted((skill_dir / "steps").glob("*.md")):
        step_name = step_md.stem
        step_content[step_name] = _read(step_md)
    for schema_json in sorted((skill_dir / "schemas").glob("*.json")):
        # schema filenames are "<step>_v1.json"
        step_name = schema_json.stem.rsplit("_v", 1)[0]
        output_schemas[step_name] = json.loads(_read(schema_json))

    conn.execute(sa.text(
        "INSERT INTO skill_version (id, skill_id, version_number, yaml_backbone, "
        "step_content, output_schemas, created_by, changelog, created_at) "
        "VALUES (:id, :sid, 1, :yaml, :steps, :schemas, 'seed', 'initial v1', :now)"
    ), {
        "id": version_id, "sid": skill_id,
        "yaml": yaml_backbone,
        "steps": json.dumps(step_content),
        "schemas": json.dumps(output_schemas),
        "now": now,
    })

    # Fixtures.
    fixtures_dir = skill_dir / "fixtures"
    for fixture_file in sorted(fixtures_dir.glob("*.json")):
        fixture = json.loads(_read(fixture_file))
        conn.execute(sa.text(
            "INSERT INTO skill_fixture "
            "(id, skill_id, case_name, input, expected_output_shape, "
            " source, captured_run_id, created_at, tool_mocks) "
            "VALUES (:id, :sid, :case, :input, :shape, 'human_written', "
            "         NULL, :now, :mocks)"
        ), {
            "id": str(uuid.uuid4()),
            "sid": skill_id,
            "case": fixture["case_name"],
            "input": json.dumps(fixture["input"]),
            "shape": (
                json.dumps(fixture["expected_output_shape"])
                if fixture.get("expected_output_shape") else None
            ),
            "now": now,
            "mocks": (
                json.dumps(fixture["tool_mocks"])
                if fixture.get("tool_mocks") else None
            ),
        })


def upgrade() -> None:
    root = _project_root()
    conn = op.get_bind()
    now = datetime.now(tz=timezone.utc).isoformat()

    capabilities_yaml = root / "config" / "capabilities.yaml"
    caps = yaml.safe_load(_read(capabilities_yaml)).get("capabilities", [])

    for name in CAP_NAMES:
        _seed_one(conn, name, caps, now)


def downgrade() -> None:
    conn = op.get_bind()
    for name in CAP_NAMES:
        conn.execute(sa.text(
            "DELETE FROM skill_fixture WHERE skill_id IN "
            "(SELECT id FROM skill WHERE capability_name = :cap)"
        ), {"cap": name})
        conn.execute(sa.text(
            "DELETE FROM skill_version WHERE skill_id IN "
            "(SELECT id FROM skill WHERE capability_name = :cap)"
        ), {"cap": name})
        conn.execute(sa.text(
            "DELETE FROM skill WHERE capability_name = :cap"
        ), {"cap": name})
        conn.execute(sa.text(
            "DELETE FROM capability WHERE name = :cap"
        ), {"cap": name})
