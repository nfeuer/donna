"""promote seed skills to shadow_primary

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-04-16

Phase 3 §10: Now that shadow sampling exists, seed skills move from
sandbox to shadow_primary. Each promotion writes an audit row so the
history is auditable via SkillLifecycleManager semantics even though
the migration predates the manager's deployment.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None

SEED_CAPABILITIES = ("parse_task", "dedup_check", "classify_priority")
PROMOTION_NOTE = "Phase 3 §10 seed skill promotion (shadow sampling now available)"


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc).isoformat()

    for capability_name in SEED_CAPABILITIES:
        # Find the matching skill; skip if missing or not in sandbox.
        result = conn.execute(
            sa.text("SELECT id FROM skill WHERE capability_name = :name AND state = 'sandbox'"),
            {"name": capability_name},
        ).fetchone()
        if result is None:
            continue
        skill_id = result[0]

        # Promote.
        conn.execute(
            sa.text(
                "UPDATE skill SET state = 'shadow_primary', updated_at = :ts "
                "WHERE id = :id"
            ),
            {"ts": now, "id": skill_id},
        )

        # Audit row.
        conn.execute(
            sa.text(
                """
                INSERT INTO skill_state_transition
                    (id, skill_id, from_state, to_state, reason, actor, actor_id, at, notes)
                VALUES (:id, :skill_id, 'sandbox', 'shadow_primary',
                        'gate_passed', 'system', NULL, :at, :notes)
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "skill_id": skill_id,
                "at": now,
                "notes": PROMOTION_NOTE,
            },
        )


def downgrade() -> None:
    conn = op.get_bind()

    # Delete the audit rows we created in upgrade().
    conn.execute(
        sa.text("DELETE FROM skill_state_transition WHERE notes = :notes"),
        {"notes": PROMOTION_NOTE},
    )

    # Revert state if it's still shadow_primary.
    for capability_name in SEED_CAPABILITIES:
        conn.execute(
            sa.text(
                "UPDATE skill SET state = 'sandbox' "
                "WHERE capability_name = :name AND state = 'shadow_primary'"
            ),
            {"name": capability_name},
        )
