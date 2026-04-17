"""seed fetch_and_summarize capability

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-04-15
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        sa.text("""
            INSERT OR IGNORE INTO capability
              (id, name, description, input_schema, trigger_type, status, created_at, created_by)
            VALUES
              (:id, :name, :description, :input_schema, 'on_manual', 'active', :created_at, 'seed')
        """),
        {
            "id": "seed-fetch_and_summarize",
            "name": "fetch_and_summarize",
            "description": "Fetch a URL and return a short summary (Phase 2 demo)",
            "input_schema": json.dumps({
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            }),
            "created_at": now,
        },
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM skill_version WHERE skill_id IN (SELECT id FROM skill WHERE capability_name = 'fetch_and_summarize')"),
    )
    conn.execute(sa.text("DELETE FROM skill WHERE capability_name = 'fetch_and_summarize'"))
    conn.execute(sa.text("DELETE FROM capability WHERE name = 'fetch_and_summarize'"))
