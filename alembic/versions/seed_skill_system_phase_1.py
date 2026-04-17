"""seed skill system phase 1 - three capabilities

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-15
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None

SEED_CAPABILITIES = [
    {
        "name": "parse_task",
        "description": "Extract structured task fields from a natural language message",
        "input_schema": {
            "type": "object",
            "properties": {
                "raw_text": {"type": "string", "description": "The user's raw message"},
                "user_id": {"type": "string", "description": "The user ID"},
            },
            "required": ["raw_text", "user_id"],
        },
        "trigger_type": "on_message",
    },
    {
        "name": "dedup_check",
        "description": "Determine whether two task candidates represent the same work item",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_a": {"type": "object", "description": "First task candidate"},
                "task_b": {"type": "object", "description": "Second task candidate"},
            },
            "required": ["task_a", "task_b"],
        },
        "trigger_type": "on_message",
    },
    {
        "name": "classify_priority",
        "description": "Assign a priority level (1-5) to a task based on content and deadline",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Task title"},
                "description": {"type": "string", "description": "Task description"},
                "deadline": {"type": ["string", "null"], "description": "ISO 8601 deadline"},
            },
            "required": ["title"],
        },
        "trigger_type": "on_message",
    },
]


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc).isoformat()

    for cap in SEED_CAPABILITIES:
        conn.execute(
            sa.text("""
                INSERT OR IGNORE INTO capability
                  (id, name, description, input_schema, trigger_type, status, created_at, created_by)
                VALUES
                  (:id, :name, :description, :input_schema, :trigger_type, 'active', :created_at, 'seed')
            """),
            {
                "id": f"seed-{cap['name']}",
                "name": cap["name"],
                "description": cap["description"],
                "input_schema": json.dumps(cap["input_schema"]),
                "trigger_type": cap["trigger_type"],
                "created_at": now,
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    for cap in SEED_CAPABILITIES:
        conn.execute(
            sa.text("DELETE FROM skill_version WHERE skill_id IN (SELECT id FROM skill WHERE capability_name = :name)"),
            {"name": cap["name"]},
        )
        conn.execute(
            sa.text("DELETE FROM skill WHERE capability_name = :name"),
            {"name": cap["name"]},
        )
        conn.execute(
            sa.text("DELETE FROM capability WHERE name = :name"),
            {"name": cap["name"]},
        )
