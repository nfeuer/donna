"""seed claude-native capability rows for migration-ready task types (F-13)

Inserts capability rows for generate_digest, prep_research, task_decompose,
extract_preferences. These remain claude_native — no skill yet. A skill
can only use their tools once those tools (calendar_read, task_db_read,
cost_summary, web_search, email_read, notes_read, fs_read) are registered
on DEFAULT_TOOL_REGISTRY; a follow-up wave handles that.

Revision ID: a6b7c8d9e0f1
Revises: f5a1b2c3d4e5
Create Date: 2026-04-20
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Union

import sqlalchemy as sa
from alembic import op

revision = "a6b7c8d9e0f1"
down_revision: Union[str, None] = "f5a1b2c3d4e5"
branch_labels = None
depends_on = None


_CAPABILITIES = [
    {
        "name": "generate_digest",
        "description": "Generate morning digest in Donna persona",
        "input_schema": {
            "type": "object",
            "properties": {
                "calendar_events": {"type": ["array", "null"]},
                "tasks_due_today": {"type": ["array", "null"]},
            },
        },
        "default_output_shape": None,
    },
    {
        "name": "prep_research",
        "description": "Research and compile prep materials for a flagged task",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": ["string", "null"]},
                "description": {"type": ["string", "null"]},
                "domain": {"type": ["string", "null"]},
                "scheduled_start": {"type": ["string", "null"]},
            },
        },
        "default_output_shape": None,
    },
    {
        "name": "task_decompose",
        "description": "Break a complex task into subtasks with dependencies",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": ["string", "null"]},
                "description": {"type": ["string", "null"]},
            },
        },
        "default_output_shape": None,
    },
    {
        "name": "extract_preferences",
        "description": "Extract learned preference rules from correction history",
        "input_schema": {
            "type": "object",
            "properties": {
                "correction_batch": {"type": ["array", "null"]},
            },
        },
        "default_output_shape": None,
    },
]


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc).isoformat()
    for cap in _CAPABILITIES:
        conn.execute(
            sa.text(
                "INSERT OR IGNORE INTO capability "
                "(id, name, description, input_schema, trigger_type, "
                " default_output_shape, status, created_at, created_by) "
                "VALUES (:id, :name, :description, :input_schema, "
                "        'ad_hoc', :default_output_shape, 'active', "
                "        :created_at, 'seed')"
            ),
            {
                "id": f"seed-{cap['name']}",
                "name": cap["name"],
                "description": cap["description"],
                "input_schema": json.dumps(cap["input_schema"]),
                "default_output_shape": (
                    json.dumps(cap["default_output_shape"])
                    if cap["default_output_shape"] is not None else None
                ),
                "created_at": now,
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    for cap in _CAPABILITIES:
        conn.execute(
            sa.text("DELETE FROM capability WHERE name = :name AND created_by = 'seed'"),
            {"name": cap["name"]},
        )
