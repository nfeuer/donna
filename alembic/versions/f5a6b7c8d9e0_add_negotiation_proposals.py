"""add negotiation_proposals table

Persists scheduling-negotiation proposals (design §3) so propose-and-confirm
survives restarts. A proposal carries T's target slot plus an ordered set of
moves (displaced task → new slot); the accept path re-validates under the lock,
so stale rows are always safe to apply or discard.

See docs/superpowers/specs/2026-06-12-scheduling-negotiation-design.md §3.

Revision ID: f5a6b7c8d9e0
Revises: 933f74c55f24
Create Date: 2026-06-13 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f5a6b7c8d9e0"
down_revision: str | None = "933f74c55f24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "negotiation_proposals",
        sa.Column("proposal_id", sa.String(length=36), primary_key=True),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("slot_start", sa.String(length=64), nullable=False),
        sa.Column("slot_end", sa.String(length=64), nullable=False),
        # JSON array of moves: [{task_id, event_id, old_start, old_end,
        # new_start, new_end}, ...].
        sa.Column("moves_json", sa.Text(), nullable=False),
        # pending | accepted | declined | expired.
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("cost", sa.Float(), nullable=False),
        sa.Column("created_at", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.String(length=64), nullable=False),
    )
    op.create_index(
        "idx_negotiation_proposals_task",
        "negotiation_proposals",
        ["task_id"],
    )
    op.create_index(
        "idx_negotiation_proposals_status",
        "negotiation_proposals",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_negotiation_proposals_status", table_name="negotiation_proposals"
    )
    op.drop_index(
        "idx_negotiation_proposals_task", table_name="negotiation_proposals"
    )
    op.drop_table("negotiation_proposals")
