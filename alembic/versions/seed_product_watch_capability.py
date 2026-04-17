"""seed product_watch capability + skill + fixtures

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-04-17 00:00:00.000000
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

revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _project_root() -> Path:
    # alembic/versions/<file>.py → project root is parents[2]
    return Path(__file__).resolve().parents[2]


def upgrade() -> None:
    root = _project_root()
    skill_dir = root / "skills" / "product_watch"
    conn = op.get_bind()
    now = datetime.now(tz=timezone.utc).isoformat()

    # 1. Capability.
    capabilities_yaml = root / "config" / "capabilities.yaml"
    caps = yaml.safe_load(_read(capabilities_yaml)).get("capabilities", [])
    cap_entry = next((c for c in caps if c.get("name") == "product_watch"), None)
    if cap_entry is None:
        raise RuntimeError("product_watch missing from config/capabilities.yaml")

    capability_id = str(uuid.uuid4())
    conn.execute(sa.text(
        "INSERT INTO capability (id, name, description, input_schema, "
        "trigger_type, default_output_shape, status, created_at, created_by) "
        "VALUES (:id, :name, :desc, :schema, :trigger, :shape, 'active', :now, 'seed')"
    ), {
        "id": capability_id,
        "name": "product_watch",
        "desc": cap_entry.get("description", ""),
        "schema": json.dumps(cap_entry.get("input_schema", {})),
        "trigger": cap_entry.get("trigger_type", "on_schedule"),
        "shape": json.dumps(cap_entry.get("default_output_shape", {})),
        "now": now,
    })

    # 2. Skill (sandbox state).
    skill_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    conn.execute(sa.text(
        "INSERT INTO skill (id, capability_name, current_version_id, state, "
        "requires_human_gate, created_at, updated_at) "
        "VALUES (:id, 'product_watch', :vid, 'sandbox', 0, :now, :now)"
    ), {"id": skill_id, "vid": version_id, "now": now})

    # 3. Skill version.
    yaml_backbone = _read(skill_dir / "skill.yaml")
    step_content = {
        "extract_product_info": _read(skill_dir / "steps" / "extract_product_info.md"),
        "format_output": _read(skill_dir / "steps" / "format_output.md"),
    }
    output_schemas = {
        "extract_product_info": json.loads(_read(skill_dir / "schemas" / "extract_product_info_v1.json")),
        "format_output": json.loads(_read(skill_dir / "schemas" / "format_output_v1.json")),
    }

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

    # 4. Fixtures.
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


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "DELETE FROM skill_fixture WHERE skill_id IN "
        "(SELECT id FROM skill WHERE capability_name = 'product_watch')"
    ))
    conn.execute(sa.text(
        "DELETE FROM skill_version WHERE skill_id IN "
        "(SELECT id FROM skill WHERE capability_name = 'product_watch')"
    ))
    conn.execute(sa.text(
        "DELETE FROM skill WHERE capability_name = 'product_watch'"
    ))
    conn.execute(sa.text(
        "DELETE FROM capability WHERE name = 'product_watch'"
    ))
