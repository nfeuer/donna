"""add nudge_events table and task stats columns

Revision ID: d5f9a7c3e281
Revises: c4d8e3b2f165
Create Date: 2026-04-03 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5f9a7c3e281"
down_revision: Union[str, None] = "d5f1a9c3e827"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add nudge_count and quality_score to tasks.
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("nudge_count", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("quality_score", sa.Float(), nullable=True))

    # Create nudge_events table.
    op.create_table(
        "nudge_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("nudge_type", sa.String(length=50), nullable=False),
        sa.Column("channel", sa.String(length=50), nullable=False),
        sa.Column("escalation_tier", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("message_text", sa.Text(), nullable=False),
        sa.Column("llm_generated", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
    )
    with op.batch_alter_table("nudge_events", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_nudge_events_user_id"), ["user_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_nudge_events_task_id"), ["task_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("nudge_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_nudge_events_task_id"))
        batch_op.drop_index(batch_op.f("ix_nudge_events_user_id"))
    op.drop_table("nudge_events")

    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_column("quality_score")
        batch_op.drop_column("nudge_count")
