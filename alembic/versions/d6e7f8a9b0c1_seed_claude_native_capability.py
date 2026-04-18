"""seed claude_native placeholder capability

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-04-17 00:00:06.000000
"""
from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d6e7f8a9b0c1"
down_revision: Union[str, None] = "c5d6e7f8a9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Seed a placeholder capability row used when a Discord-created
    automation has no specific capability match (novelty / polling path).

    Wave 3 bug-fix: ``automation.capability_name`` is NOT NULL and
    FK-bound to ``capability.name``. Without this row, polling automations
    produced by ClaudeNoveltyJudge (capability_name=None) cannot be
    persisted — AutomationCreationPath substitutes "claude_native" when
    the draft has no capability.
    """
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            INSERT OR IGNORE INTO capability
              (id, name, description, input_schema, trigger_type, status,
               created_at, created_by)
            VALUES
              (:id, :name, :description, :input_schema, 'on_schedule',
               'active', :created_at, 'seed')
            """
        ),
        {
            "id": "seed-claude_native",
            "name": "claude_native",
            "description": (
                "Placeholder capability for automations that route to Claude "
                "until a specific skill exists."
            ),
            "input_schema": json.dumps({"type": "object", "properties": {}}),
            "created_at": "2026-04-17T00:00:00+00:00",
        },
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM capability WHERE name = 'claude_native'"
        )
    )
