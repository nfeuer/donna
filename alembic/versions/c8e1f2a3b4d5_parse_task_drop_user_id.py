"""parse_task: drop bogus user_id input field

``user_id`` was seeded as a *required* input on the parse_task capability
(see seed_skill_system_phase_1). It is never extractable from a user's
message — the orchestrator already knows the author — so the local extractor
hallucinated values (null / "" / a name) and, being required, triggered a
spurious clarification thread ("- user_id: The user ID") whose reply then
contaminated the extracted raw_text. This migration removes ``user_id`` from
the parse_task input_schema so the capability parses cleanly from raw_text.

config/capabilities.yaml does not define parse_task, so the runtime
SeedCapabilityLoader will not re-introduce the field after this change.

Revision ID: c8e1f2a3b4d5
Revises: t1r2a3c4e5i6
Create Date: 2026-06-03
"""
from __future__ import annotations

import json

import sqlalchemy as sa

from alembic import op

revision = "c8e1f2a3b4d5"
down_revision = "t1r2a3c4e5i6"
branch_labels = None
depends_on = None

_CLEAN_SCHEMA = {
    "type": "object",
    "properties": {
        "raw_text": {"type": "string", "description": "The user's raw message"},
    },
    "required": ["raw_text"],
}

_LEGACY_SCHEMA = {
    "type": "object",
    "properties": {
        "raw_text": {"type": "string", "description": "The user's raw message"},
        "user_id": {"type": "string", "description": "The user ID"},
    },
    "required": ["raw_text", "user_id"],
}


def _set_schema(schema: dict) -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE capability SET input_schema = :schema WHERE name = 'parse_task'"
        ),
        {"schema": json.dumps(schema)},
    )


def upgrade() -> None:
    _set_schema(_CLEAN_SCHEMA)


def downgrade() -> None:
    _set_schema(_LEGACY_SCHEMA)
